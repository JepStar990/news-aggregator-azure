"""Unit tests for ArticleProcessor — categorisation, keyword extraction, enrichment.

These tests mock Azure Storage dependencies so they run without Azurite.
"""
from unittest.mock import patch, MagicMock
import pytest

# Mock the storage classes before importing ArticleProcessor
with patch("src.storage.blob_manager.BlobManager", autospec=True), \
     patch("src.storage.table_manager.TableManager", autospec=True):
    from src.functions.ArticleProcessor.__init__ import ArticleProcessor, CATEGORY_RULES


@pytest.fixture(autouse=True)
def mock_storage():
    """Ensure storage classes are always mocked for unit tests."""
    with patch("src.storage.blob_manager.BlobManager", autospec=True), \
         patch("src.storage.table_manager.TableManager", autospec=True):
        yield


class TestCategorise:
    def setup_method(self):
        self.processor = ArticleProcessor()

    def test_technology_match(self):
        result = self.processor.categorise(
            "Apple announces new AI features for iPhone",
            "The company revealed machine learning capabilities at WWDC",
        )
        assert "technology" in result

    def test_business_match(self):
        result = self.processor.categorise(
            "Stock market hits record high",
            "NASDAQ and S&P 500 rally on strong earnings reports",
        )
        assert "business" in result

    def test_politics_match(self):
        result = self.processor.categorise(
            "Congress passes new sanctions bill",
            "The legislation targets foreign trade practices",
        )
        assert "politics" in result

    def test_science_match(self):
        result = self.processor.categorise(
            "NASA reveals new Mars mission details",
            "The space agency plans to launch a satellite next year",
        )
        assert "science" in result

    def test_health_match(self):
        result = self.processor.categorise(
            "FDA approves new vaccine for clinical trials",
            "The treatment shows promise in early patient studies",
        )
        assert "health" in result

    def test_sports_match(self):
        result = self.processor.categorise(
            "NBA playoffs reach championship finals",
            "The tournament continues with draft picks announced",
        )
        assert "sports" in result

    def test_entertainment_match(self):
        result = self.processor.categorise(
            "Netflix releases new original movie",
            "The streaming service announces Grammy-nominated soundtrack",
        )
        assert "entertainment" in result

    def test_multiple_categories(self):
        result = self.processor.categorise(
            "Tech startup IPO raises $500M amid market rally",
            "The AI software company's venture capital funding and stock debut",
        )
        assert "technology" in result
        assert "business" in result

    def test_general_fallback(self):
        result = self.processor.categorise(
            "Local community garden opens",
            "Residents gather for the ribbon-cutting ceremony",
        )
        assert result == ["general"]

    def test_empty_input(self):
        result = self.processor.categorise("", "")
        assert result == ["general"]


class TestExtractKeywords:
    def setup_method(self):
        self.processor = ArticleProcessor()

    def test_extracts_common_words(self):
        keywords = self.processor.extract_keywords(
            "Climate change summit begins in Paris",
            "World leaders gather to discuss carbon emissions and renewable energy targets",
        )
        assert isinstance(keywords, list)
        assert len(keywords) <= 5
        for kw in keywords:
            assert len(kw) >= 4
            assert kw not in {"this", "that", "with", "from", "have", "been"}

    def test_empty_input(self):
        keywords = self.processor.extract_keywords("", "")
        assert keywords == []

    def test_stop_words_filtered(self):
        keywords = self.processor.extract_keywords(
            "This is the news about that thing with some more words",
            "They have been working with their team",
        )
        assert "this" not in keywords
        assert "that" not in keywords
        assert "with" not in keywords
        assert "have" not in keywords


class TestEnrich:
    def setup_method(self):
        self.processor = ArticleProcessor()

    def test_enrich_adds_fields(self):
        article = {
            "title": "Test Article",
            "link": "https://example.com/test",
            "published": "2026-06-01T00:00:00Z",
            "source": "TestFeed",
            "summary": "A test article about technology and business",
        }
        enriched = self.processor.enrich(article)
        assert "categories" in enriched
        assert "keywords" in enriched
        assert "processed_at" in enriched
        assert enriched["title"] == article["title"]
        assert enriched["link"] == article["link"]
        assert isinstance(enriched["categories"], list)
        assert isinstance(enriched["keywords"], list)

    def test_enrich_preserves_original(self):
        article = {"title": "X", "link": "Y", "published": "Z"}
        enriched = self.processor.enrich(article)
        assert enriched["title"] == "X"
        assert enriched["link"] == "Y"
        assert enriched["published"] == "Z"


class TestCategoryRules:
    def test_all_categories_have_patterns(self):
        assert len(CATEGORY_RULES) >= 7
        for category, patterns in CATEGORY_RULES.items():
            assert isinstance(category, str)
            assert isinstance(patterns, list)
            assert len(patterns) > 0, f"Category {category} has no patterns"
            for pattern in patterns:
                assert isinstance(pattern, str)
