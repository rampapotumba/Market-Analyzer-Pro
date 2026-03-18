"""Tests for src.analysis.sentiment_engine_v2."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.analysis.sentiment_engine_v2 import (
    SentimentEngineV2,
    _extract_text,
    _importance_weight,
    _weighted_average,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_event(headline: str = "Test news", importance: str = "medium") -> MagicMock:
    e = MagicMock()
    e.headline = headline
    e.summary = ""
    e.importance = importance
    return e


# ── _extract_text ─────────────────────────────────────────────────────────────

class TestExtractText:
    def test_from_mock_obj(self):
        e = _make_event("Fed raises rates")
        assert "Fed raises rates" in _extract_text(e)

    def test_from_dict(self):
        e = {"headline": "Market up 2%", "summary": "Stocks rallied."}
        assert "Market up 2%" in _extract_text(e)

    def test_empty_event(self):
        e = {"headline": "", "summary": ""}
        assert _extract_text(e) == ""


# ── _importance_weight ────────────────────────────────────────────────────────

class TestImportanceWeight:
    def test_critical(self):
        assert _importance_weight({"importance": "critical"}) == 3.0

    def test_high(self):
        assert _importance_weight({"importance": "high"}) == 2.0

    def test_medium(self):
        assert _importance_weight({"importance": "medium"}) == 1.5

    def test_low(self):
        assert _importance_weight({"importance": "low"}) == 1.0

    def test_unknown_defaults_low(self):
        assert _importance_weight({"importance": "unknown"}) == 1.0


# ── _weighted_average ─────────────────────────────────────────────────────────

class TestWeightedAverage:
    def test_all_none_returns_zero(self):
        parts = {"news": None, "social": None}
        weights = {"news": 0.6, "social": 0.4}
        assert _weighted_average(parts, weights) == 0.0

    def test_single_source(self):
        parts = {"news": 50.0, "social": None}
        weights = {"news": 0.6, "social": 0.4}
        result = _weighted_average(parts, weights)
        assert result == pytest.approx(50.0)

    def test_two_sources(self):
        parts = {"news": 100.0, "social": 0.0}
        weights = {"news": 0.5, "social": 0.5}
        result = _weighted_average(parts, weights)
        assert result == pytest.approx(50.0)

    def test_bounded_positive(self):
        parts = {"news": 200.0}
        weights = {"news": 1.0}
        result = _weighted_average(parts, weights)
        assert result == 100.0

    def test_bounded_negative(self):
        parts = {"news": -200.0}
        weights = {"news": 1.0}
        result = _weighted_average(parts, weights)
        assert result == -100.0


# ── SentimentEngineV2 ─────────────────────────────────────────────────────────

class TestSentimentEngineV2:
    def test_instantiate(self):
        engine = SentimentEngineV2()
        assert engine is not None

    def test_empty_returns_zero_sync(self):
        engine = SentimentEngineV2()
        score = engine.calculate_sync()
        assert score == 0.0

    def test_fear_greed_50_neutral(self):
        engine = SentimentEngineV2(fear_greed_index=50.0)
        score = engine.calculate_sync()
        assert score == pytest.approx(0.0)

    def test_fear_greed_100_bullish(self):
        engine = SentimentEngineV2(fear_greed_index=100.0)
        assert engine._score_fear_greed() == pytest.approx(100.0)

    def test_fear_greed_0_bearish(self):
        engine = SentimentEngineV2(fear_greed_index=0.0)
        assert engine._score_fear_greed() == pytest.approx(-100.0)

    def test_pcr_low_bullish(self):
        engine = SentimentEngineV2(put_call_ratio=0.5)
        assert engine._score_options() == 100.0

    def test_pcr_high_bearish(self):
        engine = SentimentEngineV2(put_call_ratio=1.5)
        assert engine._score_options() == -100.0

    def test_pcr_neutral(self):
        engine = SentimentEngineV2(put_call_ratio=1.0)
        score = engine._score_options()
        assert abs(score) < 35.0

    def test_social_bullish(self):
        engine = SentimentEngineV2(
            social_data={"bullish_pct": 80.0}
        )
        score = engine._score_social()
        assert score is not None and score > 0

    def test_social_bearish(self):
        engine = SentimentEngineV2(
            social_data={"bullish_pct": 20.0}
        )
        score = engine._score_social()
        assert score is not None and score < 0

    def test_social_empty_returns_none(self):
        engine = SentimentEngineV2(social_data={})
        assert engine._score_social() is None

    def test_get_summary(self):
        engine = SentimentEngineV2(
            news_events=[_make_event()],
            fear_greed_index=50.0,
        )
        summary = engine.get_summary()
        assert "news_count" in summary
        assert summary["news_count"] == 1
        assert summary["fear_greed_available"] is True

    @pytest.mark.asyncio
    async def test_calculate_no_finbert_falls_back(self):
        """When FinBERT returns None, calculation still completes."""
        mock_finbert = AsyncMock()
        mock_finbert.score_batch = AsyncMock(return_value=None)

        events = [_make_event("Market crashes")]
        engine = SentimentEngineV2(
            news_events=events,
            fear_greed_index=20.0,
            finbert_client=mock_finbert,
        )
        score = await engine.calculate()
        assert isinstance(score, float)
        assert -100.0 <= score <= 100.0

    @pytest.mark.asyncio
    async def test_calculate_with_finbert(self):
        """FinBERT results are incorporated into the score."""
        from src.analysis.finbert_client import ScoreResult
        mock_finbert = AsyncMock()
        mock_finbert.score_batch = AsyncMock(return_value=[
            ScoreResult(score=0.8, label="positive", confidence=0.9),
        ])

        events = [_make_event("Strong earnings beat")]
        engine = SentimentEngineV2(
            news_events=events,
            finbert_client=mock_finbert,
        )
        score = await engine.calculate()
        # 0.8 * 100 = 80 (positive) → weighted by 0.4
        assert score > 0

    @pytest.mark.asyncio
    async def test_score_bounded(self):
        from src.analysis.finbert_client import ScoreResult
        mock_finbert = AsyncMock()
        mock_finbert.score_batch = AsyncMock(return_value=[
            ScoreResult(score=1.0, label="positive", confidence=1.0),
        ] * 5)
        events = [_make_event() for _ in range(5)]
        engine = SentimentEngineV2(
            news_events=events,
            fear_greed_index=100.0,
            put_call_ratio=0.5,
            finbert_client=mock_finbert,
        )
        score = await engine.calculate()
        assert -100.0 <= score <= 100.0


class TestSentimentV2EdgeCases:
    """Cover remaining uncovered paths in sentiment_engine_v2."""

    @pytest.mark.asyncio
    async def test_score_news_finbert_empty_news_returns_none(self):
        from src.analysis.sentiment_engine_v2 import SentimentEngineV2
        engine = SentimentEngineV2(news_events=[])
        result = await engine._score_news_finbert()
        assert result is None

    @pytest.mark.asyncio
    async def test_score_news_finbert_all_none_results(self):
        """FinBERT returns None for all items → fall back."""
        from unittest.mock import AsyncMock
        from src.analysis.sentiment_engine_v2 import SentimentEngineV2

        mock_finbert = AsyncMock()
        mock_finbert.score_batch = AsyncMock(return_value=None)  # FinBERT unavailable
        events = [{"headline": "Market rises sharply", "importance": "high"}]
        engine = SentimentEngineV2(news_events=events, finbert_client=mock_finbert)
        # Falls back to TextBlob
        result = await engine._score_news_finbert()
        # TextBlob fallback should return a float or None
        assert result is None or isinstance(result, float)

    @pytest.mark.asyncio
    async def test_score_news_finbert_skips_none_score_results(self):
        """When FinBERT returns some None scores, they should be skipped."""
        from unittest.mock import AsyncMock
        from src.analysis.finbert_client import ScoreResult
        from src.analysis.sentiment_engine_v2 import SentimentEngineV2

        mock_finbert = AsyncMock()
        mock_finbert.score_batch = AsyncMock(return_value=[
            None,  # skipped
            ScoreResult(score=0.8, label="positive", confidence=0.9),
        ])
        events = [
            {"headline": "Bad headline", "importance": "low"},
            {"headline": "Good headline", "importance": "high"},
        ]
        engine = SentimentEngineV2(news_events=events, finbert_client=mock_finbert)
        result = await engine._score_news_finbert()
        assert result is not None
        assert result > 0  # only positive score contributed

    def test_score_news_textblob_empty_text_skipped(self):
        from src.analysis.sentiment_engine_v2 import SentimentEngineV2
        events = [{"headline": "", "summary": ""}]  # empty text
        engine = SentimentEngineV2(news_events=events)
        result = engine._score_news_textblob()
        assert result is None

    def test_social_with_only_bullish_pct(self):
        from src.analysis.sentiment_engine_v2 import SentimentEngineV2
        engine = SentimentEngineV2(
            social_data={"bullish_pct": 75.0}  # only bullish_pct
        )
        result = engine._score_social()
        assert result is not None
        assert result > 0  # 75% bullish → positive

    def test_social_with_all_three_sources(self):
        from src.analysis.sentiment_engine_v2 import SentimentEngineV2
        engine = SentimentEngineV2(
            social_data={
                "reddit_score": 30.0,
                "stocktwits_score": 50.0,
                "bullish_pct": 70.0,
            }
        )
        result = engine._score_social()
        assert result is not None
        assert result > 0

    def test_social_empty_parts_returns_none(self):
        from src.analysis.sentiment_engine_v2 import SentimentEngineV2
        # social_data with no recognized keys
        engine = SentimentEngineV2(social_data={"unknown_key": 50.0})
        result = engine._score_social()
        assert result is None
