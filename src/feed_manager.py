"""Feed configuration loader — loads feeds.json from the package."""
import json
import logging
import os
from pathlib import Path
from typing import List, Dict

from src import config

logger = logging.getLogger(__name__)

# Default polling interval (seconds) if not specified in feed config
DEFAULT_INTERVAL = 3600


class FeedManager:
    """Load and validate feed definitions from JSON."""

    def __init__(self, feeds_file: str = ""):
        self.feeds_file = feeds_file or config.FEEDS_FILE
        # Fall back to package-bundled feeds.json
        if not os.path.exists(self.feeds_file):
            pkg = Path(__file__).parent.parent / "feeds.json"
            if pkg.exists():
                self.feeds_file = str(pkg)

    def load_feeds(self) -> List[Dict]:
        """Load all feeds with deduplication and defaults."""
        if not os.path.exists(self.feeds_file):
            logger.error("Feeds file not found: %s", self.feeds_file)
            return []

        try:
            with open(self.feeds_file, "r", encoding="utf-8") as f:
                feeds = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to load feeds.json: %s", e)
            return []

        validated = []
        seen: set = set()

        for feed in feeds:
            if not self._validate(feed):
                continue
            key = (feed["name"], feed["url"])
            if key in seen:
                continue
            seen.add(key)
            validated.append(self._apply_defaults(feed))

        logger.info("Loaded %d feeds (%d duplicates skipped)", len(validated), len(feeds) - len(validated))
        return validated

    def get_active_feeds(self) -> List[Dict]:
        """Return only feeds marked active (or unmarked, which default to active)."""
        return [f for f in self.load_feeds() if f.get("active", True)]

    @staticmethod
    def _validate(feed: Dict) -> bool:
        if not isinstance(feed, dict):
            return False
        return bool(feed.get("name") and feed.get("url"))

    @staticmethod
    def _apply_defaults(feed: Dict) -> Dict:
        return {
            "name": feed["name"],
            "url": feed["url"],
            "interval": feed.get("interval", DEFAULT_INTERVAL),
            "priority": feed.get("priority", "medium"),
            "active": feed.get("active", True),
        }
