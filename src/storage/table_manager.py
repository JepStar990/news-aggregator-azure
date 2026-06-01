"""Table Storage wrapper — key-value article index for querying and dedup."""
import hashlib
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from azure.data.tables import TableServiceClient, UpdateMode
from azure.core.exceptions import ResourceExistsError, AzureError

from src import config

logger = logging.getLogger(__name__)


class TableManager:
    """Azure Table Storage as an article metadata index.

    Schema:
        PartitionKey = published_date (YYYY-MM-DD)  → date-range queries
        RowKey       = SHA-256(link)[:32]           → unique per article
    """

    def __init__(self, conn_str: str = "", table_name: str = ""):
        self.conn_str = conn_str or config.STORAGE_CONNECTION_STRING
        self.table_name = table_name or config.TABLE_NAME
        if not self.conn_str:
            raise RuntimeError("STORAGE_CONNECTION_STRING not set")
        self.client = TableServiceClient.from_connection_string(self.conn_str)
        self.table = self._ensure_table()

    def _ensure_table(self):
        try:
            return self.client.create_table_if_not_exists(self.table_name)
        except AzureError as e:
            logger.error("Failed to create table %s: %s", self.table_name, e)
            raise

    @staticmethod
    def _partition_key(published: str) -> str:
        return published[:10] if published else datetime.now(timezone.utc).strftime("%Y-%m-%d")

    @staticmethod
    def _row_key(link: str) -> str:
        return hashlib.sha256(link.encode("utf-8")).hexdigest()[:32]

    def upsert_article(self, article: Dict[str, Any]) -> bool:
        """Insert or update article metadata. Returns True on success."""
        entity = {
            "PartitionKey": self._partition_key(article.get("published", "")),
            "RowKey": self._row_key(article.get("link", "")),
            "Title": (article.get("title") or "")[:255],
            "Source": article.get("source", ""),
            "Link": article.get("link", ""),
            "Published": article.get("published", ""),
            "Summary": (article.get("summary") or "")[:1024],
            "ProcessedAt": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self.table.upsert_entity(entity, mode=UpdateMode.MERGE)
            return True
        except AzureError as e:
            logger.error("Failed to upsert article %s: %s", article.get("link", ""), e)
            return False

    def upsert_batch(self, articles: List[Dict[str, Any]]) -> int:
        """Batch upsert articles. Returns count of successfully inserted rows."""
        success = 0
        for article in articles:
            if self.upsert_article(article):
                success += 1
        return success

    def article_exists(self, link: str, published: str = "") -> bool:
        """Check if an article has already been indexed."""
        try:
            pk = self._partition_key(published)
            rk = self._row_key(link)
            self.table.get_entity(partition_key=pk, row_key=rk)
            return True
        except AzureError:
            return False

    def query_by_date(self, date_str: str) -> List[Dict[str, Any]]:
        """Return all articles for a given date."""
        try:
            entities = self.table.query_entities(f"PartitionKey eq '{date_str}'")
            return list(entities)
        except AzureError as e:
            logger.error("Failed to query table by date %s: %s", date_str, e)
            return []
