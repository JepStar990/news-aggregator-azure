"""Unit tests for feed manager."""
import json
import tempfile
import os
from src.feed_manager import FeedManager


class TestFeedManager:
    def test_load_feeds_from_file(self):
        feeds = [
            {"name": "BBC", "url": "https://feeds.bbci.co.uk/news/rss.xml", "active": True},
            {"name": "CNN", "url": "https://rss.cnn.com/rss/cnn_topstories.rss", "active": True},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(feeds, f)
            path = f.name

        try:
            fm = FeedManager(path)
            loaded = fm.load_feeds()
            assert len(loaded) == 2
            assert loaded[0]["name"] == "BBC"
            assert loaded[0]["interval"] == 3600  # default
        finally:
            os.unlink(path)

    def test_get_active_feeds_excludes_inactive(self):
        feeds = [
            {"name": "Active", "url": "https://a.com", "active": True},
            {"name": "Inactive", "url": "https://b.com", "active": False},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(feeds, f)
            path = f.name

        try:
            fm = FeedManager(path)
            active = fm.get_active_feeds()
            assert len(active) == 1
            assert active[0]["name"] == "Active"
        finally:
            os.unlink(path)

    def test_deduplicates_feeds(self):
        feeds = [
            {"name": "BBC", "url": "https://bbc.com/rss"},
            {"name": "BBC", "url": "https://bbc.com/rss"},  # duplicate
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(feeds, f)
            path = f.name

        try:
            fm = FeedManager(path)
            loaded = fm.load_feeds()
            assert len(loaded) == 1
        finally:
            os.unlink(path)

    def test_file_not_found_falls_back_to_package(self):
        """When given path doesn't exist, FeedManager falls back to bundled feeds.json."""
        fm = FeedManager("/nonexistent/path.json")
        feeds = fm.load_feeds()
        # The fallback loads the bundled feeds.json (800+ feeds)
        assert len(feeds) > 0
