"""Queue Storage publisher — replaces KafkaPublisher from the original pipeline."""
import json
import logging
from typing import List, Dict, Any

from azure.storage.queue import QueueClient, BinaryBase64EncodePolicy
from azure.core.exceptions import AzureError, ResourceExistsError

from src import config

logger = logging.getLogger(__name__)


class QueuePublisher:
    """Azure Queue Storage as the message bus for validated articles.

    Improvements over the original Kafka publisher:
        - Batch sending: groups all articles from one feed poll into a single
          queue message (up to 64 KB). This reduces queue operations by ~90%.
        - Dead-letter: automatically sends invalid articles to a separate queue.
        - Base64 encoding: handled by QueueClient's BinaryBase64EncodePolicy.
    """

    MAX_MESSAGE_BYTES = 64 * 1024  # Queue Storage limit

    def __init__(self, conn_str: str = "", queue_name: str = "", dead_letter: str = ""):
        self.conn_str = conn_str or config.STORAGE_CONNECTION_STRING
        self.queue_name = queue_name or config.QUEUE_NAME
        self.dead_letter_queue = dead_letter or config.DEAD_LETTER_QUEUE_NAME
        if not self.conn_str:
            raise RuntimeError("STORAGE_CONNECTION_STRING not set")

        self.encode_policy = BinaryBase64EncodePolicy()
        self.client = QueueClient.from_connection_string(
            self.conn_str, self.queue_name,
            message_encode_policy=self.encode_policy,
        )
        self._ensure_queue(self.client, self.queue_name)

        self.dl_client = QueueClient.from_connection_string(
            self.conn_str, self.dead_letter_queue,
            message_encode_policy=self.encode_policy,
        )
        self._ensure_queue(self.dl_client, self.dead_letter_queue)

    @staticmethod
    def _ensure_queue(client: QueueClient, name: str) -> None:
        """Create the queue if it doesn't exist; ignore if it already does."""
        try:
            client.create_queue()
        except ResourceExistsError:
            logger.debug("Queue %s already exists", name)
        except AzureError as e:
            logger.warning("Could not create queue %s: %s", name, e)

    def publish_batch(self, articles: List[Dict[str, Any]], topic: str = "") -> int:
        """Publish a batch of articles as a single queue message.

        Groups articles from one feed into one message for efficiency.
        Returns the number of articles published.
        """
        if not articles:
            return 0

        try:
            payload = json.dumps(articles, ensure_ascii=False).encode("utf-8")

            if len(payload) > self.MAX_MESSAGE_BYTES:
                # Split into smaller batches
                total = 0
                mid = len(articles) // 2
                total += self.publish_batch(articles[:mid])
                total += self.publish_batch(articles[mid:])
                return total

            # send_message applies encode_policy (Base64) automatically
            self.client.send_message(payload)
            logger.info("Published %d articles to queue %s", len(articles), self.queue_name)
            return len(articles)

        except AzureError as e:
            logger.error("Failed to publish batch to queue: %s", e)
            # Fall back to individual publishes
            return self._publish_individually(articles)

    def _publish_individually(self, articles: List[Dict[str, Any]]) -> int:
        """Fallback: publish articles one at a time."""
        count = 0
        for article in articles:
            try:
                payload = json.dumps(article, ensure_ascii=False).encode("utf-8")
                self.client.send_message(payload)
                count += 1
            except AzureError as e:
                logger.error("Failed to publish article: %s", e)
        return count

    def publish_dead_letter(self, articles: List[Dict[str, Any]]) -> int:
        """Send invalid articles to the dead-letter queue for triage."""
        if not articles or not config.SEND_TO_DEAD_LETTER:
            return 0
        try:
            now = datetime.now(timezone.utc).isoformat()
            payload = json.dumps({
                "articles": articles,
                "timestamp": now,
                "reason": "validation_failed",
            }, ensure_ascii=False).encode("utf-8")
            self.dl_client.send_message(payload)
            logger.info("Sent %d articles to dead-letter queue", len(articles))
            return len(articles)
        except AzureError as e:
            logger.error("Failed to send to dead-letter: %s", e)
            return 0


# Needed for dead-letter timestamp
from datetime import datetime, timezone
