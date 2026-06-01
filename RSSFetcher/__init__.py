"""Timer-triggered RSS fetch function — runs every 5 minutes.

Replaces the original APScheduler + BackgroundScheduler + ThreadPoolExecutor.
The Azure Functions runtime handles scheduling, concurrency, and retry.

Production features:
    - Graceful degradation: functions even if individual storage backends fail
    - ETag persistence: saves HTTP cache state to Blob so cold starts benefit too
    - Feed health tracking: tracks consecutive failures, skips dead feeds
    - Custom metrics: feeds_processed, articles_published, errors, latency
    - Circuit breaker: back-off feeds that fail 3+ consecutive times
"""
import asyncio
import hashlib
import json
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

# ── State persistence blob name ────────────────────────────────────────────────
STATE_BLOB = "system/feed-state.json"


class RSSFetcher:
    """Async RSS feed fetcher with ETag support, URL dedup, and circuit breaker.

    Improvements over the original:
        - No BackgroundScheduler — Functions runtime IS the scheduler.
        - No ThreadPoolExecutor — pure async with bounded semaphore.
        - Batch queue writes per feed (saves ~90% operations).
        - ETag state persists to Blob for cold-start recovery.
        - Dead-feed detection: skips feeds failing 3+ consecutive times.
        - Graceful degradation: operates even if storage backends are unavailable.
    """

    MAX_SEEN_URLS = 100_000
    MAX_CONCURRENCY = 50
    MAX_CONSECUTIVE_ERRORS = 3       # consecutive failures before skipping a feed
    HEALTH_RESET_AFTER = 20          # consecutive successes reset error count

    def __init__(self):
        # Storage backends initialised lazily — won't block function startup
        self._blob: Optional[BlobManager] = None
        self._table: Optional[TableManager] = None
        self._queue: Optional[QueuePublisher] = None
        self._feed_manager = FeedManager()

        # In-memory state (persists across warm invocations)
        self._seen_urls: OrderedDict[str, None] = OrderedDict()
        self._feed_state: Dict[str, Dict] = {}       # url → {etag, last_modified}
        self._feed_health: Dict[str, Dict] = {}      # url → {consecutive_errors, consecutive_successes, last_error}
        self._stats = {"feeds_processed": 0, "articles_published": 0, "errors": 0, "skipped": 0}
        self._storage_available = False

    # ── Lazy storage initialisation ──────────────────────────────────────────

    @property
    def blob(self) -> Optional[BlobManager]:
        if self._blob is None:
            try:
                self._blob = BlobManager()
                self._storage_available = True
            except RuntimeError as e:
                logger.warning("BlobManager unavailable: %s — running without blob storage", e)
        return self._blob

    @property
    def table(self) -> Optional[TableManager]:
        if self._table is None:
            try:
                self._table = TableManager()
                self._storage_available = True
            except RuntimeError as e:
                logger.warning("TableManager unavailable: %s — running without table storage", e)
        return self._table

    @property
    def queue(self) -> Optional[QueuePublisher]:
        if self._queue is None:
            try:
                self._queue = QueuePublisher()
                self._storage_available = True
            except RuntimeError as e:
                logger.warning("QueuePublisher unavailable: %s — running without queue", e)
        return self._queue

    # ── State persistence ────────────────────────────────────────────────────

    def _load_state_from_blob(self) -> None:
        """Recover ETag cache and feed health from Blob after a cold start."""
        if self.blob is None:
            return
        try:
            data = self.blob.read_json(STATE_BLOB)
            if data:
                self._feed_state = data.get("feed_state", {})
                self._feed_health = data.get("feed_health", {})
                logger.info(
                    "Restored state from blob: %d etags, %d health entries",
                    len(self._feed_state), len(self._feed_health),
                )
        except Exception as e:
            logger.debug("Could not load state from blob (first run?): %s", e)

    def _save_state_to_blob(self) -> None:
        """Persist ETag cache and feed health to Blob for cold-start recovery."""
        if self.blob is None:
            return
        try:
            state = {
                "feed_state": self._feed_state,
                "feed_health": self._feed_health,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
            self.blob.save_json(STATE_BLOB, state)
        except Exception as e:
            logger.debug("Could not persist state to blob: %s", e)

    # ── HTTP client ──────────────────────────────────────────────────────────

    def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            limits=httpx.Limits(
                max_keepalive_connections=self.MAX_CONCURRENCY,
                max_connections=100,
                keepalive_expiry=30.0,
            ),
            timeout=httpx.Timeout(config.REQUEST_TIMEOUT),
            follow_redirects=True,
        )

    # ── Feed health / circuit breaker ────────────────────────────────────────

    def _is_feed_dead(self, url: str) -> bool:
        """Return True if feed has failed too many times consecutively."""
        health = self._feed_health.get(url, {})
        errors = health.get("consecutive_errors", 0)
        return errors >= self.MAX_CONSECUTIVE_ERRORS

    def _record_feed_success(self, url: str) -> None:
        entry = self._feed_health.get(url, {})
        successes = entry.get("consecutive_successes", 0) + 1
        entry["consecutive_successes"] = successes
        entry["consecutive_errors"] = 0
        # Reset tracking if feed has been stable for a while
        if successes >= self.HEALTH_RESET_AFTER:
            self._feed_health.pop(url, None)
        else:
            self._feed_health[url] = entry

    def _record_feed_error(self, url: str, error: str) -> None:
        entry = self._feed_health.get(url, {})
        entry["consecutive_errors"] = entry.get("consecutive_errors", 0) + 1
        entry["consecutive_successes"] = 0
        entry["last_error"] = error
        entry["last_failed_at"] = datetime.now(timezone.utc).isoformat()
        self._feed_health[url] = entry

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
                    self._record_feed_success(url)
                    return None  # unchanged

                if resp.status_code != 200:
                    msg = f"HTTP {resp.status_code}"
                    logger.warning("%s from %s (%s)", msg, name, url)
                    self._stats["errors"] += 1
                    self._record_feed_error(url, msg)
                    return None

                # Cache validation headers for next poll
                self._feed_state[url] = {
                    "etag": resp.headers.get("ETag", ""),
                    "last_modified": resp.headers.get("Last-Modified", ""),
                }
                self._record_feed_success(url)
                return feedparser.parse(resp.content)

        except (httpx.RequestError, httpx.TimeoutException) as e:
            msg = f"{type(e).__name__}: {e}"
            logger.warning("Fetch failed for %s: %s", name, e)
            self._stats["errors"] += 1
            self._record_feed_error(url, msg)
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

                # Circuit breaker: skip feeds that fail repeatedly
                if self._is_feed_dead(url):
                    self._stats["skipped"] += 1
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

                # Publish to queue, persist to blob + table (best-effort)
                if self.queue:
                    try:
                        self.queue.publish_batch(articles)
                    except Exception as e:
                        logger.error("Queue publish failed for %s: %s", name, e)

                if self.table:
                    try:
                        self.table.upsert_batch(articles)
                    except Exception as e:
                        logger.error("Table upsert failed for %s: %s", name, e)

                if self.blob:
                    try:
                        self.blob.save_raw_feed(
                            parsed.get("raw_xml", "") or str(parsed), name)
                        self.blob.save_parsed_articles(articles, name)
                    except Exception as e:
                        logger.error("Blob save failed for %s: %s", name, e)

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

    # ── Stats management ─────────────────────────────────────────────────────

    def reset_stats(self) -> None:
        """Reset per-invocation counters. ETag and dedup state are preserved."""
        self._stats = {"feeds_processed": 0, "articles_published": 0, "errors": 0, "skipped": 0}

    @property
    def stats(self) -> Dict:
        return dict(self._stats)

    @property
    def seen_urls_count(self) -> int:
        return len(self._seen_urls)

    @property
    def feed_state_count(self) -> int:
        return len(self._feed_state)

    @property
    def dead_feed_count(self) -> int:
        return sum(
            1 for h in self._feed_health.values()
            if h.get("consecutive_errors", 0) >= self.MAX_CONSECUTIVE_ERRORS
        )


# ── Module-level singleton — persists state across warm invocations ────────────
# On cold start, a fresh instance is created. On warm invocations (subsequent
# timer triggers within the same instance), ETag cache and LRU URL dedup are
# preserved, dramatically reducing redundant HTTP traffic and reprocessing.
# State is also persisted to Blob for cold-start recovery.

_fetcher: Optional["RSSFetcher"] = None


def _get_fetcher() -> RSSFetcher:
    """Return the module-level singleton, creating and hydrating it on first call."""
    global _fetcher
    if _fetcher is None:
        _fetcher = RSSFetcher()
        logger.info("RSSFetcher singleton initialised (cold start)")
        # Load persisted state from Blob for cold-start recovery
        _fetcher._load_state_from_blob()
    return _fetcher


# ── Custom metrics helper ──────────────────────────────────────────────────────

def _track_metrics(stats: Dict, elapsed: float, fetcher: RSSFetcher) -> None:
    """Emit structured log events for App Insights custom metric extraction.

    App Insights can extract custom metrics from trace logs via KQL.
    These structured events enable dashboards for:
        - feeds_processed, articles_published, errors per cycle
        - cycle_duration_seconds
        - dead_feeds count (circuit-breaker hits)
    """
    logger.info(
        "METRICS feeds_processed=%d articles_published=%d errors=%d "
        "skipped=%d duration=%.2f dead_feeds=%d seen_urls=%d etag_state=%d",
        stats["feeds_processed"],
        stats["articles_published"],
        stats["errors"],
        stats.get("skipped", 0),
        elapsed,
        fetcher.dead_feed_count,
        fetcher.seen_urls_count,
        fetcher.feed_state_count,
    )


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
        "RSS fetch cycle starting (seen_urls=%d, etag_state=%d, dead_feeds=%d)",
        fetcher.seen_urls_count,
        fetcher.feed_state_count,
        fetcher.dead_feed_count,
    )

    stats = await fetcher.process_all_feeds()

    elapsed = time.monotonic() - start
    logger.info(
        "Cycle complete in %.1fs — %d feeds processed, %d articles published, "
        "%d errors, %d skipped (dead)",
        elapsed,
        stats["feeds_processed"],
        stats["articles_published"],
        stats["errors"],
        stats.get("skipped", 0),
    )

    # Emit structured metrics for App Insights dashboard consumption
    _track_metrics(stats, elapsed, fetcher)

    # Persist ETag/health state to Blob for cold start recovery
    fetcher._save_state_to_blob()
