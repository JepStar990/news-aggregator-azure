"""Article validator — ported from original, with improved validation rules."""
import logging
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


class Validator:
    """Validates RSS article structure and content quality."""

    REQUIRED_FIELDS = ("title", "link", "published")

    @classmethod
    def validate_article(cls, article: Dict[str, Any]) -> bool:
        """Single-article validation — checks required fields are present and non-empty."""
        return all(
            field in article and article[field]
            for field in cls.REQUIRED_FIELDS
        )

    @classmethod
    def filter_valid(
        cls, articles: List[Dict[str, Any]], feed_name: str = "UnknownFeed"
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Split articles into valid and invalid lists.

        Returns (valid_articles, invalid_articles).
        """
        valid: List[Dict[str, Any]] = []
        invalid: List[Dict[str, Any]] = []

        for article in articles:
            if cls.validate_article(article):
                valid.append(article)
            else:
                logger.warning(
                    "Invalid article from %s: missing %s",
                    feed_name,
                    [f for f in cls.REQUIRED_FIELDS if not article.get(f)],
                )
                invalid.append(article)

        return valid, invalid
