"""Blob Storage wrapper — replaces local-disk StorageManager from the original pipeline."""
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any

from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import ResourceNotFoundError, AzureError

from src import config

logger = logging.getLogger(__name__)


class BlobManager:
    """Azure Blob Storage as the canonical sink for raw XML and parsed JSON.

    Path structure:
        raw-feeds/{feed_name}.xml
        parsed-articles/{feed_name}/{date}/{timestamp}.json
        dead-letter/{feed_name}/{date}/{timestamp}.json
    """

    def __init__(self, conn_str: str = "", container: str = ""):
        self.conn_str = conn_str or config.STORAGE_CONNECTION_STRING
        self.container_name = container or config.CONTAINER_NAME
        if not self.conn_str:
            raise RuntimeError("STORAGE_CONNECTION_STRING not set")
        self.client = BlobServiceClient.from_connection_string(self.conn_str)
        self._ensure_container()

    def _ensure_container(self) -> None:
        try:
            self.client.create_container(self.container_name)
        except AzureError:
            pass  # already exists

    def save_raw_feed(self, content: str, feed_name: str) -> str:
        """Save raw XML feed content, overwriting the previous fetch."""
        safe_name = feed_name.replace("/", "-").replace(" ", "_")
        blob_name = f"raw-feeds/{safe_name}.xml"
        try:
            blob = self.client.get_blob_client(container=self.container_name, blob=blob_name)
            blob.upload_blob(content.encode("utf-8"), overwrite=True)
            return blob_name
        except AzureError as e:
            logger.error("Failed to save raw feed %s: %s", feed_name, e)
            raise

    def save_parsed_articles(self, articles: List[Dict[str, Any]], feed_name: str) -> str:
        """Save parsed articles as a timestamped JSON blob."""
        now = datetime.now(timezone.utc)
        safe_name = feed_name.replace("/", "-").replace(" ", "_")
        blob_name = (
            f"parsed-articles/{safe_name}/"
            f"{now.strftime('%Y-%m-%d')}/"
            f"{now.strftime('%Y-%m-%dT%H-%M-%S')}.json"
        )
        payload = json.dumps(articles, ensure_ascii=False, indent=2)
        try:
            blob = self.client.get_blob_client(container=self.container_name, blob=blob_name)
            blob.upload_blob(payload.encode("utf-8"), overwrite=True)
            return blob_name
        except AzureError as e:
            logger.error("Failed to save articles for %s: %s", feed_name, e)
            raise

    def save_dead_letter(self, articles: List[Dict[str, Any]], feed_name: str) -> str:
        """Save failed articles for later triage."""
        now = datetime.now(timezone.utc)
        safe_name = feed_name.replace("/", "-").replace(" ", "_")
        blob_name = (
            f"dead-letter/{safe_name}/"
            f"{now.strftime('%Y-%m-%d')}/"
            f"{now.strftime('%Y-%m-%dT%H-%M-%S')}.json"
        )
        payload = json.dumps(articles, ensure_ascii=False, indent=2)
        try:
            blob = self.client.get_blob_client(container=self.container_name, blob=blob_name)
            blob.upload_blob(payload.encode("utf-8"), overwrite=True)
            return blob_name
        except AzureError as e:
            logger.error("Failed to save dead-letter for %s: %s", feed_name, e)
            raise

    def read_raw_feed(self, feed_name: str) -> str | None:
        """Read the last-saved raw XML for a feed (useful for debugging)."""
        safe_name = feed_name.replace("/", "-").replace(" ", "_")
        blob_name = f"raw-feeds/{safe_name}.xml"
        try:
            blob = self.client.get_blob_client(container=self.container_name, blob=blob_name)
            return blob.download_blob().readall().decode("utf-8")
        except ResourceNotFoundError:
            return None
