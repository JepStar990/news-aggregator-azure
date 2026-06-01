"""Integration tests for Azure Storage classes — requires Azurite.

Run with:
    STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=http;..." pytest tests/test_storage.py -v
"""
import os
import pytest


# Skip all tests if Azurite is not configured
AZURITE_AVAILABLE = bool(os.getenv("STORAGE_CONNECTION_STRING", ""))
pytestmark = pytest.mark.skipif(
    not AZURITE_AVAILABLE,
    reason="STORAGE_CONNECTION_STRING not set — Azurite required for storage tests",
)


# ── BlobManager ────────────────────────────────────────────────────────────────

class TestBlobManager:
    @pytest.fixture(autouse=True)
    def setup(self):
        from src.storage.blob_manager import BlobManager
        self.bm = BlobManager()

    def test_save_and_read_raw_feed(self):
        self.bm.save_raw_feed("<rss><channel><item>Test</item></channel></rss>", "IntegrationTest")
        result = self.bm.read_raw_feed("IntegrationTest")
        assert result is not None
        assert "<rss>" in result
        assert "<item>Test</item>" in result

    def test_save_parsed_articles(self):
        articles = [
            {"title": "Test 1", "link": "https://example.com/1", "published": "2026-06-01T00:00:00Z"},
            {"title": "Test 2", "link": "https://example.com/2", "published": "2026-06-01T01:00:00Z"},
        ]
        blob_name = self.bm.save_parsed_articles(articles, "IntegrationTest")
        assert "parsed-articles/IntegrationTest/" in blob_name
        assert blob_name.endswith(".json")

    def test_save_dead_letter(self):
        articles = [{"title": "Bad", "link": ""}]
        blob_name = self.bm.save_dead_letter(articles, "DeadFeed")
        assert "dead-letter/DeadFeed/" in blob_name
        assert blob_name.endswith(".json")

    def test_read_nonexistent_feed(self):
        result = self.bm.read_raw_feed("NonExistentFeed12345")
        assert result is None


# ── QueuePublisher ─────────────────────────────────────────────────────────────

class TestQueuePublisher:
    @pytest.fixture(autouse=True)
    def setup(self):
        from src.publishers.queue_publisher import QueuePublisher
        self.qp = QueuePublisher()

    def test_publish_batch(self):
        articles = [
            {"title": f"Article {i}", "link": f"https://example.com/q/{i}",
             "published": "2026-06-01T00:00:00Z"}
            for i in range(10)
        ]
        count = self.qp.publish_batch(articles)
        assert count == 10

    def test_publish_empty_batch(self):
        count = self.qp.publish_batch([])
        assert count == 0

    def test_publish_single_article(self):
        articles = [{"title": "Solo", "link": "https://example.com/solo", "published": "2026-06-01T00:00:00Z"}]
        count = self.qp.publish_batch(articles)
        assert count == 1

    def test_publish_dead_letter(self):
        articles = [{"title": "Bad Article", "link": "", "reason": "missing link"}]
        # Dead-letter is controlled by config; it may be disabled
        count = self.qp.publish_dead_letter(articles)
        assert count >= 0  # 0 if SEND_TO_DEAD_LETTER is false


# ── TableManager ───────────────────────────────────────────────────────────────

class TestTableManager:
    @pytest.fixture(autouse=True)
    def setup(self):
        from src.storage.table_manager import TableManager
        self.tm = TableManager()

    def test_upsert_and_check_exists(self):
        article = {
            "title": "Table Test Article",
            "link": "https://example.com/table-test-1",
            "published": "2026-06-01T12:00:00Z",
            "source": "TestSource",
            "summary": "An article for testing Table Storage",
        }
        result = self.tm.upsert_article(article)
        assert result is True

        exists = self.tm.article_exists("https://example.com/table-test-1", "2026-06-01T12:00:00Z")
        assert exists is True

    def test_article_not_exists(self):
        exists = self.tm.article_exists("https://example.com/definitely-not-real", "2026-06-01")
        assert exists is False

    def test_upsert_batch(self):
        articles = [
            {
                "title": f"Batch {i}",
                "link": f"https://example.com/batch/{i}",
                "published": "2026-06-01T00:00:00Z",
                "source": "BatchTest",
                "summary": f"Batch article {i}",
            }
            for i in range(20)
        ]
        count = self.tm.upsert_batch(articles)
        assert count == 20

        # Verify first and last exist
        assert self.tm.article_exists("https://example.com/batch/0", "2026-06-01T00:00:00Z")
        assert self.tm.article_exists("https://example.com/batch/19", "2026-06-01T00:00:00Z")

    def test_query_by_date(self):
        articles = self.tm.query_by_date("2026-06-01")
        assert isinstance(articles, list)
        assert len(articles) > 0  # we've inserted articles above
