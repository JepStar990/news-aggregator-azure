"""Timer-triggered RSS fetch function — runs every 5 minutes.

Replaces the original APScheduler + BackgroundScheduler + ThreadPoolExecutor.
The Azure Functions runtime handles scheduling, concurrency, and retry.
"""
import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Dict, List, Optional

import azure.functions as func
import feedparser
import httpx

from src import config
from src.logging_config import setup_logging
from src.feed_manager import FeedManager
from src.validator import Validator
from src.publishers.queue_publisher import QueuePublisher
from src.storage.blob_manager import BlobManager
from src.storage.table_manager import TableManager

setup_logging()
logger = logging.getLogger("RSSFetcher")


class RSSFetcher:
    """Async RSS feed fetcher with ETag support and URL dedup.

    Improvements over the original:
        - No BackgroundScheduler — Functions runtime IS the scheduler.
        - No ThreadPoolExecutor — pure async with bounded semaphore.
        - Batch queue writes per feed (saves ~90% operations).
        - Structured JSON logging for App Insights.
    """

    MAX_SEEN_URLS = 100_000
    MAX_CONCURRENCY = 50

    def __init__(self):
        self.blob = BlobManager()
        self.table = TableManager()
        self.queue = QueuePublisher()
        self.feed_manager = FeedManager()
        self._seen_urls: OrderedDict[str, None] = OrderedDict()
        self._feed_state: Dict[str, Dict] = {}
        self._stats = {"feeds_processed": 0, "articles_published": 0, "errors": 0}

    # ── HTTP client ──────────────────────────────────────────────────────────

    def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            limits=httpx.Limits(max_keepalive_connections=50, max_connections=100, keepalive_expiry=30.0),
            timeout=httpx.Timeout(config.REQUEST_TIMEOUT),
            follow_redirects=True,
        )

    # ── Fetch a single feed ──────────────────────────────────────────────────

    async def fetch_feed(self, url: str, name: str) -> Optional[Dict]:
        """HTTP GET with ETag / If-Modified-Since for bandwidth efficiency."""
        state = self._feed_state.get(url, {})
        headers = {
            **config.DEFAULT_HEADERS,
            "User-Agent": self._rotate_ua(),
            "If-None-Match": state.get("etag", ""),
            "If-Modified-Since": state.get("last_modified", ""),
        }

        try:
            async with self._make_client() as client:
                await asyncio.sleep(config.RATE_LIMIT_DELAY)
                resp = await client.get(url, headers=headers)

                if resp.status_code == 304:
                    return None  # unchanged

                if resp.status_code != 200:
                    logger.warning("HTTP %d from %s (%s)", resp.status_code, name, url)
                    self._stats["errors"] += 1
                    return None

                # Cache validation headers for next poll
                self._feed_state[url] = {
                    "etag": resp.headers.get("ETag", ""),
                    "last_modified": resp.headers.get("Last-Modified", ""),
                }

                return feedparser.parse(resp.content)

        except (httpx.RequestError, httpx.TimeoutException) as e:
            logger.warning("Fetch failed for %s: %s", name, e)
            self._stats["errors"] += 1
            return None

    # ── Process feed entries ─────────────────────────────────────────────────

    def _process_entries(self, entries: List, feed_name: str) -> List[Dict]:
        """Parse, deduplicate, and validate feed entries."""
        articles = []
        for entry in entries:
            link = entry.get("link", "")
            if not link or self._is_duplicate(link):
                continue

            article = {
                "title": entry.get("title", ""),
                "link": link,
                "published": entry.get("published", ""),
                "summary": entry.get("summary", entry.get("description", "")),
                "source": feed_name,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            articles.append(article)

        valid, invalid = Validator.filter_valid(articles, feed_name)
        return valid

    def _is_duplicate(self, url: str) -> bool:
        """LRU-based URL dedup to avoid re-processing within the session."""
        h = hashlib.sha256(url.encode()).hexdigest()
        if h in self._seen_urls:
            self._seen_urls.move_to_end(h)
            return True
        self._seen_urls[h] = None
        if len(self._seen_urls) > self.MAX_SEEN_URLS:
            self._seen_urls.popitem(last=False)
        return False

    def _rotate_ua(self) -> str:
        return config.USER_AGENTS[len(self._seen_urls) % len(config.USER_AGENTS)]

    # ── Process all feeds ────────────────────────────────────────────────────

    async def process_all_feeds(self) -> Dict:
        """Fetch and process all active feeds concurrently."""
        feeds = self.feed_manager.get_active_feeds()
        if not feeds:
            logger.warning("No active feeds found")
            return self._stats

        semaphore = asyncio.Semaphore(self.MAX_CONCURRENCY)

        async def process_one(feed: Dict) -> Optional[int]:
            async with semaphore:
                url = feed.get("url", "")
                name = feed.get("name", "")
                if not url or not name:
                    return None

                parsed = await self.fetch_feed(url, name)
                if parsed is None:
                    return None  # 304 or error — already logged
                if parsed.bozo:
                    logger.debug("Bozo feed %s: %s", name, parsed.bozo_exception)
                    return None

                articles = self._process_entries(parsed.entries, name)
                if not articles:
                    return 0

                # Publish batch to queue, persist to blob + table
                self.queue.publish_batch(articles)
                self.table.upsert_batch(articles)
                self.blob.save_raw_feed(
                    parsed.get("raw_xml", "") or str(parsed),
                    name,
                )
                self.blob.save_parsed_articles(articles, name)
                self._stats["articles_published"] += len(articles)
                self._stats["feeds_processed"] += 1
                return len(articles)

        tasks = [process_one(f) for f in feeds]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.error("Error processing %s: %s", feeds[i].get("name", "?"), r)
                self._stats["errors"] += 1

        return self._stats

    def reset_stats(self) -> None:
        """Reset per-invocation counters. ETag and dedup state are preserved."""
        self._stats = {"feeds_processed": 0, "articles_published": 0, "errors": 0}

    @property
    def stats(self) -> Dict:
        return dict(self._stats)

    @property
    def seen_urls_count(self) -> int:
        return len(self._seen_urls)

    @property
    def feed_state_count(self) -> int:
        return len(self._feed_state)


# ── Module-level singleton — persists state across warm invocations ────────────
# On cold start, a fresh instance is created. On warm invocations (subsequent
# timer triggers within the same instance), ETag cache and LRU URL dedup are
# preserved, dramatically reducing redundant HTTP traffic and reprocessing.

_fetcher: Optional["RSSFetcher"] = None


def _get_fetcher() -> RSSFetcher:
    """Return the module-level singleton, creating it on first invocation."""
    global _fetcher
    if _fetcher is None:
        _fetcher = RSSFetcher()
        logger.info("RSSFetcher singleton initialised (cold start)")
    return _fetcher


# ── Function entry point ─────────────────────────────────────────────────────

async def main(timer: func.TimerRequest) -> None:
    """Azure Functions TimerTrigger entry point.

    Uses async def so the Functions host can manage the event loop directly.
    The module-level RSSFetcher singleton preserves ETag and dedup caches
    between warm invocations, avoiding redundant HTTP traffic.
    """
    start = time.monotonic()
    fetcher = _get_fetcher()
    fetcher.reset_stats()

    logger.info(
        "RSS fetch cycle starting (seen_urls=%d, etag_state=%d)",
        fetcher.seen_urls_count,
        fetcher.feed_state_count,
    )

    stats = await fetcher.process_all_feeds()

    elapsed = time.monotonic() - start
    logger.info(
        "Cycle complete in %.1fs — %d feeds processed, %d articles published, %d errors",
        elapsed,
        stats["feeds_processed"],
        stats["articles_published"],
        stats["errors"],
    )
