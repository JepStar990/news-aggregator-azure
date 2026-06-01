"""Configuration — all values come from environment variables (set by ARM template)."""
import os
import json

# ── Storage ──────────────────────────────────────────────────────────────────
STORAGE_CONNECTION_STRING = os.getenv("STORAGE_CONNECTION_STRING", "")
QUEUE_NAME = os.getenv("QUEUE_NAME", "article-ingest")
DEAD_LETTER_QUEUE_NAME = os.getenv("DEAD_LETTER_QUEUE_NAME", "article-dead-letter")
CONTAINER_NAME = os.getenv("CONTAINER_NAME", "news-data")
TABLE_NAME = os.getenv("TABLE_NAME", "ArticleIndex")

# ── Fetching ─────────────────────────────────────────────────────────────────
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "10"))
RATE_LIMIT_DELAY = float(os.getenv("RATE_LIMIT_DELAY", "0.5"))
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "50"))
FEED_POLL_BATCH_SIZE = int(os.getenv("FEED_POLL_BATCH_SIZE", "50"))

# ── Validation ───────────────────────────────────────────────────────────────
SEND_TO_DEAD_LETTER = os.getenv("SEND_TO_DEAD_LETTER", "true").lower() == "true"

# ── Feed config ──────────────────────────────────────────────────────────────
# feeds.json is bundled in the package alongside this module
import pathlib
_FEEDS_PATH = pathlib.Path(__file__).parent.parent / "feeds.json"
FEEDS_FILE = str(_FEEDS_PATH) if _FEEDS_PATH.exists() else "feeds.json"

# ── HTTP ─────────────────────────────────────────────────────────────────────
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
}

USER_AGENTS = [
    USER_AGENT,
    "Mozilla/5.0 (macOS; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15",
    "Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.77 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Gecko/20100101 Firefox/89.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:89.0) Gecko/20100101 Firefox/89.0",
]
