"""Queue-triggered article processor — the downstream consumer of article-ingest.

Receives batch messages from the RSSFetcher, enriches articles with
category tags and keyword extraction, deduplicates against Table Storage,
and writes enriched results to Blob Storage.

This is the "Phase 2" component that completes the event-driven pipeline:
    TimerTrigger → RSSFetcher → Queue Storage → ArticleProcessor → Blob + Table
"""
import json
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

import azure.functions as func

from src.logging_config import setup_logging
from src.storage.blob_manager import BlobManager
from src.storage.table_manager import TableManager

setup_logging()
logger = logging.getLogger("ArticleProcessor")


# ── Category rules ────────────────────────────────────────────────────────────
# Lightweight keyword-based categorisation. Extensible to Azure Cognitive
# Services Text Analytics for production-grade classification.

CATEGORY_RULES: Dict[str, List[str]] = {
    "technology": [
        r"\b(?:ai|artificial.intelligence|ml|machine.learning)\b",
        r"\b(?:software|app|startup|silicon.valley|big.tech)\b",
        r"\b(?:cyber|hack|breach|ransomware|malware|phish)\b",
        r"\b(?:cloud|aws|azure|gcp|saas|api|devops)\b",
        r"\b(?:crypto|bitcoin|ethereum|blockchain|nft|web3)\b",
    ],
    "business": [
        r"\b(?:market|stock|nasdaq|dow|s&p|share.price)\b",
        r"\b(?:acquisition|merger|ipo|funding|venture.capital)\b",
        r"\b(?:revenue|earnings|profit|loss|quarterly)\b",
        r"\b(?:economy|inflation|gdp|interest.rate|recession)\b",
        r"\b(?:layoff|hiring|workforce|remote.work|rto)\b",
    ],
    "politics": [
        r"\b(?:election|congress|senate|president|parliament)\b",
        r"\b(?:democrat|republican|gop|liberal|conservative)\b",
        r"\b(?:sanction|tariff|trade.war|diplomat|treaty)\b",
        r"\b(?:protest|rally|campaign|ballot|vote)\b",
    ],
    "science": [
        r"\b(?:nasa|spacex|space|mars|moon|orbit|satellite)\b",
        r"\b(?:climate|carbon|emission|renewable|solar|wind)\b",
        r"\b(?:gene|dna|crispr|vaccine|clinical.trial)\b",
        r"\b(?:physics|quantum|particle|lhc|cern)\b",
    ],
    "health": [
        r"\b(?:covid|pandemic|virus|outbreak|vaccine|booster)\b",
        r"\b(?:hospital|patient|treatment|diagnosis|therapy)\b",
        r"\b(?:fda|drug|pharma|clinical|trial)\b",
        r"\b(?:mental.health|wellness|fitness|nutrition)\b",
    ],
    "sports": [
        r"\b(?:nfl|nba|mlb|nhl|premier.league|champions.league)\b",
        r"\b(?:olympic|world.cup|tournament|championship)\b",
        r"\b(?:transfer|contract|draft|playoff|final)\b",
    ],
    "entertainment": [
        r"\b(?:movie|film|netflix|hbo|disney|streaming)\b",
        r"\b(?:music|album|concert|tour|grammy|spotify)\b",
        r"\b(?:celebrity|actor|actress|director|oscar|emmy)\b",
        r"\b(?:gaming|esport|playstation|xbox|nintendo)\b",
    ],
}


class ArticleProcessor:
    """Processes batches of articles from the ingest queue.

    Responsibilities:
        1. Decode batch messages from Queue Storage
        2. Categorise articles via keyword matching
        3. Extract top keywords
        4. Deduplicate against Table Storage
        5. Store enriched results in Blob + Table
    """

    def __init__(self):
        self.blob = BlobManager()
        self.table = TableManager()

    # ── Categorisation ─────────────────────────────────────────────────────

    @staticmethod
    def categorise(title: str, summary: str) -> List[str]:
        """Return a list of category tags matching the article text."""
        text = f"{title} {summary}".lower()
        matched = []
        for category, patterns in CATEGORY_RULES.items():
            if any(re.search(pattern, text) for pattern in patterns):
                matched.append(category)
        return matched or ["general"]

    @staticmethod
    def extract_keywords(title: str, summary: str, max_keywords: int = 5) -> List[str]:
        """Extract simple keywords by frequency (placeholder for NLP)."""
        text = f"{title} {summary}".lower()
        # Remove common stop words and punctuation
        words = re.findall(r"\b[a-z]{4,}\b", text)
        stopwords = {
            "this", "that", "with", "from", "have", "been", "were", "they",
            "their", "what", "when", "which", "about", "into", "more", "some",
            "would", "could", "should", "other", "after", "over", "than",
            "also", "very", "just", "like", "will", "year", "said",
        }
        filtered = [w for w in words if w not in stopwords]
        # Count and return top N
        from collections import Counter
        return [word for word, _ in Counter(filtered).most_common(max_keywords)]

    # ── Article enrichment ──────────────────────────────────────────────────

    def enrich(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """Add category, keywords, and processing metadata to an article."""
        title = article.get("title", "")
        summary = article.get("summary", "")

        return {
            **article,
            "categories": self.categorise(title, summary),
            "keywords": self.extract_keywords(title, summary),
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }

    # ── Batch processing ────────────────────────────────────────────────────

    def process_batch(self, articles: List[Dict[str, Any]]) -> Dict[str, int]:
        """Enrich a batch of articles and persist results.

        Returns counts of {new, duplicate, error}.
        """
        counts = {"new": 0, "duplicate": 0, "error": 0}

        enriched = []
        for article in articles:
            link = article.get("link", "")
            published = article.get("published", "")

            if not link:
                counts["error"] += 1
                continue

            # Dedup check against Table Storage
            if self.table.article_exists(link, published):
                counts["duplicate"] += 1
                continue

            try:
                processed = self.enrich(article)
                enriched.append(processed)
                self.table.upsert_article(processed)
                counts["new"] += 1
            except Exception as e:
                logger.error("Failed to process article %s: %s", link, e)
                counts["error"] += 1

        # Persist enriched batch to Blob
        if enriched:
            try:
                source = enriched[0].get("source", "unknown")
                self.blob.save_parsed_articles(enriched, f"enriched/{source}")
            except Exception as e:
                logger.error("Failed to save enriched batch: %s", e)

        return counts


# ── Module-level singleton ─────────────────────────────────────────────────────

_processor: Optional[ArticleProcessor] = None


def _get_processor() -> ArticleProcessor:
    global _processor
    if _processor is None:
        _processor = ArticleProcessor()
        logger.info("ArticleProcessor singleton initialised (cold start)")
    return _processor


# ── Function entry point ─────────────────────────────────────────────────────

def main(msg: func.QueueMessage) -> None:
    """Azure Functions QueueTrigger entry point.

    Called for each message on the 'article-ingest' queue.  A single message
    contains a batch of articles from one RSS feed poll (up to ~50 articles,
    packed into one 64 KB queue message by QueuePublisher.publish_batch).
    """
    processor = _get_processor()

    try:
        body = msg.get_body()
        articles: List[Dict[str, Any]] = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.error("Failed to decode queue message: %s", e)
        # This message will be moved to the poison queue after
        # maxDequeueCount (5) retries — no explicit dead-letter needed.
        raise

    if not isinstance(articles, list):
        logger.warning("Queue message is not a list — skipping")
        return

    counts = processor.process_batch(articles)

    logger.info(
        "Batch processed: %d new, %d duplicate, %d error (from %d articles)",
        counts["new"],
        counts["duplicate"],
        counts["error"],
        len(articles),
    )
