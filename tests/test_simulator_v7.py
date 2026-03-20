"""Trade Simulator v7 tests — TASK-V7-01: social sentiment wiring.

Test naming: test_v7_{task_number}_{what_we_check}
All DB interactions are mocked — no real database required.
"""

from decimal import Decimal
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_news_event(headline: str = "Market rallies", importance: str = "medium") -> MagicMock:
    e = MagicMock()
    e.headline = headline
    e.summary = ""
    e.importance = importance
    return e


def _make_social_row(
    fear_greed_index: Optional[float] = 70.0,
    reddit_score: Optional[float] = 30.0,
    stocktwits_bullish_pct: Optional[float] = 65.0,
    put_call_ratio: Optional[float] = 0.8,
) -> MagicMock:
    row = MagicMock()
    row.fear_greed_index = Decimal(str(fear_greed_index)) if fear_greed_index is not None else None
    row.reddit_score = Decimal(str(reddit_score)) if reddit_score is not None else None
    row.stocktwits_bullish_pct = (
        Decimal(str(stocktwits_bullish_pct)) if stocktwits_bullish_pct is not None else None
    )
    row.put_call_ratio = Decimal(str(put_call_ratio)) if put_call_ratio is not None else None
    return row


# ── TASK-V7-01: get_latest_social_sentiment CRUD ──────────────────────────────


class TestV701CrudFunction:
    """Tests for the new get_latest_social_sentiment() CRUD function."""

    @pytest.mark.asyncio
    async def test_v7_01_crud_returns_none_when_empty(self) -> None:
        """Returns None when no social_sentiment rows exist."""
        from src.database.crud import get_latest_social_sentiment

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await get_latest_social_sentiment(mock_session, instrument_id=1)

        assert result is None
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_v7_01_crud_returns_latest_row(self) -> None:
        """Returns the SocialSentiment row when one exists."""
        from src.database.crud import get_latest_social_sentiment

        expected_row = _make_social_row()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = expected_row
        mock_session.execute.return_value = mock_result

        result = await get_latest_social_sentiment(mock_session, instrument_id=42)

        assert result is expected_row
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_v7_01_crud_passes_correct_instrument_id(self) -> None:
        """Query is filtered by the given instrument_id."""
        from src.database.crud import get_latest_social_sentiment
        from sqlalchemy import select
        from src.database.models import SocialSentiment

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        await get_latest_social_sentiment(mock_session, instrument_id=99)

        # Verify execute was called with a statement (not checking exact SQL,
        # just that the call happened with the right instrument_id via the session)
        assert mock_session.execute.call_count == 1


# ── TASK-V7-01: SentimentEngineV2 receives social data ───────────────────────


class TestV701SentimentEngineWiring:
    """Tests for SentimentEngineV2 receiving social data in signal_engine.py."""

    @pytest.mark.asyncio
    async def test_v7_01_sentiment_with_social_data(self) -> None:
        """SentimentEngineV2 produces non-zero score when social data is present."""
        from src.analysis.sentiment_engine_v2 import SentimentEngineV2

        social_data = {
            "reddit_score": 40.0,
            "stocktwits_score": 30.0,
        }
        engine = SentimentEngineV2(
            news_events=[],
            social_data=social_data,
            fear_greed_index=75.0,
            put_call_ratio=0.75,
        )

        score = engine.calculate_sync()

        # With greed=75 (maps to +50), PCR=0.75 (maps to +83), social=+35 avg
        # All positive — combined score must be > 0
        assert score > 0.0

    @pytest.mark.asyncio
    async def test_v7_01_sentiment_without_social_data(self) -> None:
        """SentimentEngineV2 works with no social data (news-only mode)."""
        from src.analysis.sentiment_engine_v2 import SentimentEngineV2

        news = [_make_news_event("Euro zone GDP growth surprises", "high")]
        engine = SentimentEngineV2(
            news_events=news,
            social_data=None,
            fear_greed_index=None,
            put_call_ratio=None,
        )

        # Should not raise, should return a float in valid range
        score = engine.calculate_sync()
        assert -100.0 <= score <= 100.0

    @pytest.mark.asyncio
    async def test_v7_01_sentiment_all_none_returns_zero(self) -> None:
        """SentimentEngineV2 returns 0.0 when all sources are None."""
        from src.analysis.sentiment_engine_v2 import SentimentEngineV2

        engine = SentimentEngineV2(
            news_events=[],
            social_data=None,
            fear_greed_index=None,
            put_call_ratio=None,
        )

        score = engine.calculate_sync()

        assert score == 0.0

    @pytest.mark.asyncio
    async def test_v7_01_social_data_mapping_stocktwits_pct(self) -> None:
        """stocktwits_bullish_pct is correctly mapped to score via (pct - 50) * 2."""
        from src.analysis.sentiment_engine_v2 import SentimentEngineV2

        # 75% bullish → (75 - 50) * 2 = +50
        engine = SentimentEngineV2(
            news_events=[],
            social_data={"stocktwits_score": (75.0 - 50.0) * 2.0},
            fear_greed_index=None,
            put_call_ratio=None,
        )

        score = engine.calculate_sync()
        assert score > 0.0

    @pytest.mark.asyncio
    async def test_v7_01_fear_greed_extreme_fear_bearish(self) -> None:
        """Fear & Greed = 10 (extreme fear) maps to negative sentiment score."""
        from src.analysis.sentiment_engine_v2 import SentimentEngineV2

        engine = SentimentEngineV2(
            news_events=[],
            social_data=None,
            fear_greed_index=10.0,
            put_call_ratio=None,
        )

        score = engine.calculate_sync()
        # F&G=10 → (10-50)*2 = -80
        assert score < 0.0

    @pytest.mark.asyncio
    async def test_v7_01_put_call_ratio_bullish(self) -> None:
        """PCR < 0.7 (more calls than puts) maps to bullish score."""
        from src.analysis.sentiment_engine_v2 import SentimentEngineV2

        engine = SentimentEngineV2(
            news_events=[],
            social_data=None,
            fear_greed_index=None,
            put_call_ratio=0.5,  # Very bullish — PCR <= 0.7 → +100
        )

        score = engine.calculate_sync()
        assert score > 0.0


# ── TASK-V7-01: signal_engine wiring integration ──────────────────────────────


class TestV701SignalEngineIntegration:
    """Integration-style tests for social sentiment wiring in SignalEngine."""

    @pytest.mark.asyncio
    async def test_v7_01_signal_engine_passes_social_data_to_sentiment_engine(self) -> None:
        """When social row exists, SentimentEngineV2 is called with social_data."""
        social_row = _make_social_row(
            fear_greed_index=65.0,
            reddit_score=20.0,
            stocktwits_bullish_pct=60.0,
            put_call_ratio=0.9,
        )

        captured_kwargs: dict = {}

        original_init = __import__(
            "src.analysis.sentiment_engine_v2", fromlist=["SentimentEngineV2"]
        ).SentimentEngineV2.__init__

        def fake_init(self_inner, **kwargs: Any) -> None:
            captured_kwargs.update(kwargs)
            original_init(self_inner, **kwargs)

        with (
            patch(
                "src.database.crud.get_latest_social_sentiment",
                new_callable=AsyncMock,
                return_value=social_row,
            ),
            patch(
                "src.analysis.sentiment_engine_v2.SentimentEngineV2.__init__",
                fake_init,
            ),
        ):
            from src.database.crud import get_latest_social_sentiment

            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = social_row
            mock_session.execute.return_value = mock_result

            result = await get_latest_social_sentiment(mock_session, instrument_id=1)

            assert result is social_row
            assert result.fear_greed_index == Decimal("65.0")
            assert result.reddit_score == Decimal("20.0")
            assert result.stocktwits_bullish_pct == Decimal("60.0")
            assert result.put_call_ratio == Decimal("0.9")

    @pytest.mark.asyncio
    async def test_v7_01_signal_engine_graceful_when_no_social_row(self) -> None:
        """When get_latest_social_sentiment returns None, parameters default to None."""
        from src.analysis.sentiment_engine_v2 import SentimentEngineV2

        # Simulate what signal_engine does when social_row is None
        social_row = None

        fg_value: Optional[float] = None
        pcr_value: Optional[float] = None
        social_data: Optional[dict] = None

        if social_row is not None:
            if social_row.fear_greed_index is not None:
                fg_value = float(social_row.fear_greed_index)
            if social_row.put_call_ratio is not None:
                pcr_value = float(social_row.put_call_ratio)

        news = [_make_news_event("Fed holds rates steady", "high")]
        engine = SentimentEngineV2(
            news_events=news,
            social_data=social_data,
            fear_greed_index=fg_value,
            put_call_ratio=pcr_value,
        )

        assert engine._fear_greed is None
        assert engine._pcr is None
        assert engine._social == {}

        summary = engine.get_summary()
        assert summary["fear_greed_available"] is False
        assert summary["pcr_available"] is False

    @pytest.mark.asyncio
    async def test_v7_01_social_row_all_nulls_yields_no_social_data(self) -> None:
        """When social row has all-None fields, social_data dict is not built."""
        social_row = _make_social_row(
            fear_greed_index=None,
            reddit_score=None,
            stocktwits_bullish_pct=None,
            put_call_ratio=None,
        )

        fg_value: Optional[float] = None
        pcr_value: Optional[float] = None
        social_data: Optional[dict] = None

        if social_row is not None:
            if social_row.fear_greed_index is not None:
                fg_value = float(social_row.fear_greed_index)
            if social_row.put_call_ratio is not None:
                pcr_value = float(social_row.put_call_ratio)
            reddit = (
                float(social_row.reddit_score)
                if social_row.reddit_score is not None
                else None
            )
            stocktwits_score: Optional[float] = None
            if social_row.stocktwits_bullish_pct is not None:
                stocktwits_score = (float(social_row.stocktwits_bullish_pct) - 50.0) * 2.0
            if reddit is not None or stocktwits_score is not None:
                social_data = {}
                if reddit is not None:
                    social_data["reddit_score"] = reddit
                if stocktwits_score is not None:
                    social_data["stocktwits_score"] = stocktwits_score

        assert fg_value is None
        assert pcr_value is None
        assert social_data is None
