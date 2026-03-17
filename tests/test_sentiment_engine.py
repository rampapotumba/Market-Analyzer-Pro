"""Tests for Sentiment Analysis Engine."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.analysis.sentiment_engine import SentimentEngine


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_news(
    headline: str = "",
    sentiment_score=None,
    importance: str = "medium",
) -> MagicMock:
    item = MagicMock()
    item.headline = headline
    item.summary = ""
    item.sentiment_score = Decimal(str(sentiment_score)) if sentiment_score is not None else None
    item.importance = importance
    return item


# ── Score Calculation ─────────────────────────────────────────────────────────

class TestCalculateSentimentScore:
    """Test weighted sentiment score computation."""

    def test_empty_events_returns_zero(self):
        assert SentimentEngine([]).calculate_sentiment_score() == 0.0

    def test_positive_headline_positive_score(self):
        news = [make_news("Exceptional profits and outstanding growth")]
        score = SentimentEngine(news).calculate_sentiment_score()
        assert score > 0

    def test_negative_headline_negative_score(self):
        news = [make_news("Devastating crash and catastrophic losses")]
        score = SentimentEngine(news).calculate_sentiment_score()
        assert score < 0

    def test_score_in_range(self):
        """Score must always be in [-100, +100]."""
        news = [make_news("Amazing wonderful perfect", sentiment_score=1.0, importance="critical")]
        score = SentimentEngine(news).calculate_sentiment_score()
        assert -100.0 <= score <= 100.0

    def test_pre_stored_sentiment_used_over_textblob(self):
        """Pre-computed sentiment_score should be used directly without TextBlob."""
        news = [make_news("neutral text", sentiment_score=0.5)]
        score = SentimentEngine(news).calculate_sentiment_score()
        # 0.5 polarity × 100 = 50.0
        assert abs(score - 50.0) < 1.0

    def test_pre_stored_negative_sentiment(self):
        news = [make_news("neutral text", sentiment_score=-0.5)]
        score = SentimentEngine(news).calculate_sentiment_score()
        assert abs(score - (-50.0)) < 1.0

    def test_mixed_sentiment_near_zero(self):
        """Equally positive and negative (same weight) → ~0."""
        news = [
            make_news(sentiment_score=1.0, importance="medium"),
            make_news(sentiment_score=-1.0, importance="medium"),
        ]
        score = SentimentEngine(news).calculate_sentiment_score()
        assert abs(score) < 5.0

    def test_critical_importance_outweighs_low(self):
        """Critical importance (weight=3) should dominate over low (weight=1)."""
        news = [
            make_news(sentiment_score=1.0, importance="critical"),   # weight 3
            make_news(sentiment_score=-1.0, importance="low"),        # weight 1
        ]
        # Net = (1.0×3 + (-1.0)×1) / (3+1) = 2/4 = 0.5 polarity → positive
        score = SentimentEngine(news).calculate_sentiment_score()
        assert score > 0

    def test_empty_headline_no_crash(self):
        """Empty headline should not raise, should contribute 0 polarity."""
        item = MagicMock()
        item.headline = ""
        item.summary = ""
        item.sentiment_score = None
        item.importance = "low"
        score = SentimentEngine([item]).calculate_sentiment_score()
        assert score == 0.0

    def test_dict_news_supported(self):
        """News provided as dicts should also work."""
        news = [{"headline": "Great rally", "summary": "", "sentiment_score": 0.7, "importance": "high"}]
        score = SentimentEngine(news).calculate_sentiment_score()
        assert score > 0


# ── Text Scoring ──────────────────────────────────────────────────────────────

class TestScoreText:
    """Test raw TextBlob text scoring."""

    def test_empty_string_returns_zero(self):
        engine = SentimentEngine([])
        assert engine._score_text("") == 0.0

    def test_whitespace_returns_zero(self):
        engine = SentimentEngine([])
        assert engine._score_text("   ") == 0.0

    def test_positive_text_positive_polarity(self):
        engine = SentimentEngine([])
        score = engine._score_text("Excellent performance and fantastic growth")
        assert score >= 0

    def test_result_in_range(self):
        engine = SentimentEngine([])
        score = engine._score_text("some random text here")
        assert -1.0 <= score <= 1.0


# ── Event Helpers ─────────────────────────────────────────────────────────────

class TestEventHelpers:
    """Test helper methods for event attribute extraction."""

    def test_get_event_text_from_orm(self):
        engine = SentimentEngine([])
        item = MagicMock()
        item.headline = "Fed raises rates"
        item.summary = "by 25 basis points"
        text = engine._get_event_text(item)
        assert "Fed raises rates" in text
        assert "25 basis points" in text

    def test_get_event_text_from_dict(self):
        engine = SentimentEngine([])
        item = {"headline": "Market surges", "summary": "on strong data"}
        text = engine._get_event_text(item)
        assert "Market surges" in text

    def test_get_event_weight_critical(self):
        engine = SentimentEngine([])
        item = MagicMock()
        item.importance = "critical"
        assert engine._get_event_weight(item) == 3.0

    def test_get_event_weight_high(self):
        engine = SentimentEngine([])
        item = MagicMock()
        item.importance = "high"
        assert engine._get_event_weight(item) == 2.0

    def test_get_event_weight_unknown_defaults_to_low(self):
        engine = SentimentEngine([])
        item = MagicMock()
        item.importance = "nonexistent"
        assert engine._get_event_weight(item) == 1.0

    def test_get_stored_sentiment_orm(self):
        engine = SentimentEngine([])
        item = MagicMock()
        item.sentiment_score = Decimal("0.3")
        assert engine._get_stored_sentiment(item) == pytest.approx(0.3, abs=1e-9)

    def test_get_stored_sentiment_none(self):
        engine = SentimentEngine([])
        item = MagicMock()
        item.sentiment_score = None
        assert engine._get_stored_sentiment(item) is None


# ── Summary ───────────────────────────────────────────────────────────────────

class TestGetSummary:
    """Test sentiment summary statistics."""

    def test_empty_returns_zero_counts(self):
        summary = SentimentEngine([]).get_summary()
        assert summary["total_events"] == 0
        assert summary["avg_score"] == 0.0
        assert summary["bullish_count"] == 0
        assert summary["bearish_count"] == 0
        assert summary["neutral_count"] == 0

    def test_counts_bullish_bearish_neutral(self):
        news = [
            make_news(sentiment_score=0.5),   # bullish
            make_news(sentiment_score=-0.5),  # bearish
            make_news(sentiment_score=0.0),   # neutral
        ]
        summary = SentimentEngine(news).get_summary()
        assert summary["total_events"] == 3
        assert summary["bullish_count"] == 1
        assert summary["bearish_count"] == 1
        assert summary["neutral_count"] == 1

    def test_avg_score_calculated(self):
        news = [
            make_news(sentiment_score=0.4),
            make_news(sentiment_score=0.6),
        ]
        summary = SentimentEngine(news).get_summary()
        assert abs(summary["avg_score"] - 0.5) < 0.01

    def test_all_bullish(self):
        news = [make_news(sentiment_score=0.8) for _ in range(5)]
        summary = SentimentEngine(news).get_summary()
        assert summary["bullish_count"] == 5
        assert summary["bearish_count"] == 0
