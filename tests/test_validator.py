"""Unit tests for article validator — fast, no Azure dependency."""
import pytest
from src.validator import Validator


class TestValidateArticle:
    def test_valid_article(self):
        article = {"title": "Breaking News", "link": "https://example.com/1", "published": "2025-06-01"}
        assert Validator.validate_article(article) is True

    def test_missing_title(self):
        article = {"link": "https://example.com/1", "published": "2025-06-01"}
        assert Validator.validate_article(article) is False

    def test_missing_link(self):
        article = {"title": "Breaking News", "published": "2025-06-01"}
        assert Validator.validate_article(article) is False

    def test_missing_published(self):
        article = {"title": "Breaking News", "link": "https://example.com/1"}
        assert Validator.validate_article(article) is False

    def test_empty_fields(self):
        article = {"title": "", "link": "", "published": ""}
        assert Validator.validate_article(article) is False

    def test_extra_fields_ok(self):
        article = {"title": "X", "link": "Y", "published": "Z", "extra": "field"}
        assert Validator.validate_article(article) is True


class TestFilterValid:
    def test_splits_valid_and_invalid(self):
        articles = [
            {"title": "A", "link": "https://a.com", "published": "2025-01-01"},
            {"title": "", "link": "https://b.com", "published": "2025-01-01"},  # invalid
            {"title": "C", "link": "https://c.com", "published": "2025-01-01"},
        ]
        valid, invalid = Validator.filter_valid(articles)
        assert len(valid) == 2
        assert len(invalid) == 1

    def test_empty_input(self):
        valid, invalid = Validator.filter_valid([])
        assert valid == []
        assert invalid == []
