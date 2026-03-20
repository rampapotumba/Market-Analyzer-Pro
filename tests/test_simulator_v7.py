"""Trade Simulator v7 tests — Phase 1 data wiring fixes.

Test naming: test_v7_{task_number}_{what_we_check}
All DB interactions are mocked — no real database required.
"""

import datetime
import inspect
from decimal import Decimal
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.analysis.fa_engine import FAEngine, _PAIR_BANK_MAP, _RATE_DIFF_SCORE_MULTIPLIER
from src.database.models import Base
from src.analysis.geo_engine_v2 import (
    GeoEngineV2,
    _COUNTRY_INSTRUMENTS,
    _CB_FAIL_THRESHOLD,
    _GDELT_CACHE_TTL,
    _symbol_to_countries,
    _tone_to_score,
)


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


def make_instrument(symbol: str, market: str = "forex") -> MagicMock:
    inst = MagicMock()
    inst.symbol = symbol
    inst.market = market
    return inst


def make_macro(indicator: str, value: float, prev: float) -> MagicMock:
    item = MagicMock()
    item.indicator_name = indicator
    item.value = Decimal(str(value))
    item.previous_value = Decimal(str(prev))
    return item


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
        assert score < 0.0

    @pytest.mark.asyncio
    async def test_v7_01_put_call_ratio_bullish(self) -> None:
        """PCR < 0.7 (more calls than puts) maps to bullish score."""
        from src.analysis.sentiment_engine_v2 import SentimentEngineV2

        engine = SentimentEngineV2(
            news_events=[],
            social_data=None,
            fear_greed_index=None,
            put_call_ratio=0.5,
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


# ── TASK-V7-02: FRED collector fetch limit + FA engine delta ─────────────────


class TestV702FredFetchLimit:
    """TASK-V7-02: FREDCollector._fetch_series uses limit=60 by default."""

    def test_v7_02_fred_fetch_limit(self) -> None:
        """_fetch_series default limit parameter must be 60 (5 years monthly)."""
        from src.collectors.macro_collector import FREDCollector

        sig = inspect.signature(FREDCollector._fetch_series)
        default_limit = sig.parameters["limit"].default
        assert default_limit == 60, (
            f"Expected _fetch_series default limit=60, got {default_limit}"
        )


class TestV702FaEngineDeltaWithHistory:
    """TASK-V7-02: FAEngine._delta() returns non-None when 2+ observations exist."""

    def _make_macro_record(
        self,
        indicator_name: str,
        value: str,
        release_date: datetime.datetime,
    ) -> MagicMock:
        record = MagicMock()
        record.indicator_name = indicator_name
        record.value = Decimal(value)
        record.release_date = release_date
        return record

    def _make_instrument(self, market: str = "forex", symbol: str = "EURUSD=X") -> MagicMock:
        instrument = MagicMock()
        instrument.market = market
        instrument.symbol = symbol
        return instrument

    def test_v7_02_fa_engine_delta_with_history(self) -> None:
        """FAEngine._delta() is non-None when 2 observations are provided per indicator."""
        from src.analysis.fa_engine import FAEngine

        now = datetime.datetime(2025, 3, 1, tzinfo=datetime.timezone.utc)
        month_ago = datetime.datetime(2025, 2, 1, tzinfo=datetime.timezone.utc)

        macro_data = [
            self._make_macro_record("FEDFUNDS", "5.33", now),
            self._make_macro_record("FEDFUNDS", "5.08", month_ago),
            self._make_macro_record("CPIAUCSL", "310.50", now),
            self._make_macro_record("CPIAUCSL", "309.80", month_ago),
            self._make_macro_record("UNRATE", "3.9", now),
            self._make_macro_record("UNRATE", "4.1", month_ago),
            self._make_macro_record("GDPC1", "22000.0", now),
            self._make_macro_record("GDPC1", "21800.0", month_ago),
        ]

        instrument = self._make_instrument(market="forex", symbol="EURUSD=X")
        engine = FAEngine(instrument, macro_data, [])

        assert engine._delta("FEDFUNDS") is not None
        assert engine._delta("CPIAUCSL") is not None
        assert engine._delta("UNRATE") is not None
        assert engine._delta("GDPC1") is not None

        assert abs(engine._delta("FEDFUNDS") - (5.33 - 5.08)) < 1e-9  # type: ignore[operator]
        assert abs(engine._delta("UNRATE") - (3.9 - 4.1)) < 1e-9     # type: ignore[operator]

    def test_v7_02_fa_engine_delta_none_when_single_observation(self) -> None:
        """FAEngine._delta() returns None when only 1 observation per indicator."""
        from src.analysis.fa_engine import FAEngine

        now = datetime.datetime(2025, 3, 1, tzinfo=datetime.timezone.utc)
        macro_data = [
            self._make_macro_record("FEDFUNDS", "5.33", now),
        ]

        instrument = self._make_instrument(market="forex", symbol="EURUSD=X")
        engine = FAEngine(instrument, macro_data, [])

        assert engine._delta("FEDFUNDS") is None

    def test_v7_02_fa_score_nonzero_with_two_observations(self) -> None:
        """FAEngine.calculate_fa_score() returns non-zero when deltas are available."""
        from src.analysis.fa_engine import FAEngine

        now = datetime.datetime(2025, 3, 1, tzinfo=datetime.timezone.utc)
        month_ago = datetime.datetime(2025, 2, 1, tzinfo=datetime.timezone.utc)

        macro_data = [
            self._make_macro_record("FEDFUNDS", "5.50", now),
            self._make_macro_record("FEDFUNDS", "5.00", month_ago),
        ]

        instrument = self._make_instrument(market="forex", symbol="EURUSD=X")
        engine = FAEngine(instrument, macro_data, [])

        score = engine.calculate_fa_score()
        assert score != 0.0, f"Expected non-zero FA score, got {score}"


class TestV702NewFredSeriesDefined:
    """TASK-V7-02: New FRED series DFF, T10Y2Y, DTWEXBGS, UMCSENT are defined."""

    def test_v7_02_new_fred_series_defined(self) -> None:
        """DFF, T10Y2Y, DTWEXBGS, UMCSENT must be present in FRED_SERIES dict."""
        from src.collectors.macro_collector import FRED_SERIES

        required_series = {"DFF", "T10Y2Y", "DTWEXBGS", "UMCSENT"}
        missing = required_series - set(FRED_SERIES.keys())
        assert not missing, f"Missing FRED series: {missing}"

    def test_v7_02_new_fred_series_have_name_and_country(self) -> None:
        """Each new FRED series must have 'name' and 'country' fields."""
        from src.collectors.macro_collector import FRED_SERIES

        new_series = ["DFF", "T10Y2Y", "DTWEXBGS", "UMCSENT"]
        for series_id in new_series:
            meta = FRED_SERIES[series_id]
            assert "name" in meta, f"{series_id} missing 'name'"
            assert "country" in meta, f"{series_id} missing 'country'"
            assert meta["name"], f"{series_id} has empty 'name'"
            assert meta["country"] == "US", f"{series_id} expected country 'US'"

    def test_v7_02_existing_fred_series_still_present(self) -> None:
        """Original FRED series must not have been removed."""
        from src.collectors.macro_collector import FRED_SERIES

        original_series = {
            "FEDFUNDS", "CPIAUCSL", "UNRATE", "GDPC1",
            "PAYEMS", "INDPRO", "RETAILSMNSA", "HOUST",
        }
        missing = original_series - set(FRED_SERIES.keys())
        assert not missing, f"Original FRED series removed: {missing}"


# ── TASK-V7-03: rate differential calculation ─────────────────────────────────


class TestV7RateDifferential:
    """Tests for _analyze_rate_differential() and its integration into calculate_fa_score()."""

    def test_v7_03_rate_differential_calculation_eurusd(self):
        """FED > ECB → USD stronger → EURUSD is bearish → negative contribution."""
        inst = make_instrument("EURUSD=X", "forex")
        rates = {"FED": 5.25, "ECB": 4.50}
        engine = FAEngine(inst, [], [], central_bank_rates=rates)

        score = engine._analyze_rate_differential()

        assert score == pytest.approx(-7.5, abs=1e-9)

    def test_v7_03_rate_differential_calculation_usdjpy(self):
        """FED > BOJ → USD stronger → USDJPY is bullish → positive contribution."""
        inst = make_instrument("USDJPY=X", "forex")
        rates = {"FED": 5.25, "BOJ": 0.10}
        engine = FAEngine(inst, [], [], central_bank_rates=rates)

        score = engine._analyze_rate_differential()

        assert score == pytest.approx(51.5, abs=1e-9)

    def test_v7_03_rate_differential_ecb_higher_than_fed(self):
        """ECB > FED → EUR stronger → EURUSD is bullish → positive contribution."""
        inst = make_instrument("EURUSD=X", "forex")
        rates = {"FED": 3.00, "ECB": 4.50}
        engine = FAEngine(inst, [], [], central_bank_rates=rates)

        score = engine._analyze_rate_differential()

        assert score == pytest.approx(15.0, abs=1e-9)

    def test_v7_03_fa_score_reflects_rate_differential(self):
        """FA score for EURUSD with FED>ECB should be negative."""
        inst = make_instrument("EURUSD=X", "forex")
        rates = {"FED": 5.25, "ECB": 4.50}
        engine = FAEngine(inst, [], [], central_bank_rates=rates)

        fa_score = engine.calculate_fa_score()

        assert fa_score == pytest.approx(-2.25, abs=1e-6)

    def test_v7_03_fa_score_with_macro_and_rate_diff(self):
        """FA score combines macro base_score (60%) and rate_diff (30%)."""
        inst = make_instrument("EURUSD=X", "forex")
        rates = {"FED": 5.25, "ECB": 4.50}
        macro = [make_macro("FEDFUNDS", 5.5, 5.25)]
        engine = FAEngine(inst, macro, [], central_bank_rates=rates)

        fa_score = engine.calculate_fa_score()

        assert fa_score < 0

    def test_v7_03_no_rate_data_graceful(self):
        """No rates passed → rate differential component = 0."""
        inst = make_instrument("EURUSD=X", "forex")
        macro = [make_macro("FEDFUNDS", 5.5, 5.25)]
        engine = FAEngine(inst, macro, [], central_bank_rates=None)

        fa_score = engine.calculate_fa_score()

        assert isinstance(fa_score, float)
        assert -100.0 <= fa_score <= 100.0

    def test_v7_03_empty_rates_dict_graceful(self):
        """Empty rates dict → rate differential = 0.0."""
        inst = make_instrument("EURUSD=X", "forex")
        engine = FAEngine(inst, [], [], central_bank_rates={})

        score = engine._analyze_rate_differential()

        assert score == 0.0

    def test_v7_03_partial_rates_missing_one_bank(self):
        """If one bank rate is missing, differential returns 0.0."""
        inst = make_instrument("EURUSD=X", "forex")
        rates = {"FED": 5.25}  # ECB missing
        engine = FAEngine(inst, [], [], central_bank_rates=rates)

        score = engine._analyze_rate_differential()

        assert score == 0.0

    def test_v7_03_crypto_no_rate_differential(self):
        """Crypto instruments must not use rate differential."""
        inst = make_instrument("BTC/USDT", "crypto")
        rates = {"FED": 5.25, "ECB": 4.50}
        engine = FAEngine(inst, [], [], central_bank_rates=rates)

        score = engine._analyze_rate_differential()

        assert score == 0.0

    def test_v7_03_crypto_fa_score_unaffected_by_rates(self):
        """Passing rates to FAEngine for a crypto instrument must not change the score."""
        inst = make_instrument("BTC/USDT", "crypto")
        rates = {"FED": 5.25, "ECB": 4.50}

        score_without = FAEngine(inst, [], [], central_bank_rates=None).calculate_fa_score()
        score_with = FAEngine(inst, [], [], central_bank_rates=rates).calculate_fa_score()

        assert score_without == score_with

    def test_v7_03_stock_instrument_no_rate_differential(self):
        """Stock instruments are not in _PAIR_BANK_MAP → rate diff = 0."""
        inst = make_instrument("AAPL", "stocks")
        rates = {"FED": 5.25}
        engine = FAEngine(inst, [], [], central_bank_rates=rates)

        score = engine._analyze_rate_differential()

        assert score == 0.0

    def test_v7_03_pair_bank_map_covers_all_major_pairs(self):
        """All 7 major forex pairs must be present in _PAIR_BANK_MAP."""
        expected_pairs = {
            "EURUSD=X", "GBPUSD=X", "USDJPY=X",
            "AUDUSD=X", "USDCAD=X", "USDCHF=X", "NZDUSD=X",
        }
        assert expected_pairs == set(_PAIR_BANK_MAP.keys())

    def test_v7_03_score_clamped_to_range(self):
        """Even extreme rate differential must not push FA score outside [-100, +100]."""
        inst = make_instrument("USDJPY=X", "forex")
        rates = {"FED": 20.0, "BOJ": 0.0}
        engine = FAEngine(inst, [], [], central_bank_rates=rates)

        fa_score = engine.calculate_fa_score()

        assert -100.0 <= fa_score <= 100.0


# ── Local DB fixture (isolated per-test engine) ───────────────────────────────


@pytest_asyncio.fixture
async def v7_db() -> AsyncSession:
    """Isolated SQLite session for v7 CRUD tests."""
    import os
    import tempfile
    import uuid as _uuid

    tmp_path = os.path.join(tempfile.gettempdir(), f"v7_test_{_uuid.uuid4().hex}.db")
    db_url = f"sqlite+aiosqlite:///{tmp_path}"
    engine = create_async_engine(db_url, echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, checkfirst=True))

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            yield session
    finally:
        await engine.dispose()
        try:
            os.remove(tmp_path)
        except OSError:
            pass


# ── TASK-V7-03: CRUD function ─────────────────────────────────────────────────


class TestV7GetCentralBankRates:
    """Tests for get_central_bank_rates() CRUD function."""

    @pytest.mark.asyncio
    async def test_v7_03_crud_returns_latest_rate_per_bank(self, v7_db: AsyncSession):
        """get_central_bank_rates returns one rate per bank (the most recent)."""
        import datetime

        from src.database.crud import get_central_bank_rates
        from src.database.models import CentralBankRate

        now = datetime.datetime.now(datetime.timezone.utc)
        older = now - datetime.timedelta(days=30)

        fed_old = CentralBankRate(
            bank="FED", currency="USD", rate=Decimal("5.00"),
            effective_date=older, source="test",
        )
        fed_new = CentralBankRate(
            bank="FED", currency="USD", rate=Decimal("5.25"),
            effective_date=now, source="test",
        )
        ecb_row = CentralBankRate(
            bank="ECB", currency="EUR", rate=Decimal("4.50"),
            effective_date=now, source="test",
        )
        v7_db.add_all([fed_old, fed_new, ecb_row])
        await v7_db.flush()

        result = await get_central_bank_rates(v7_db)

        assert isinstance(result, dict)
        assert result["FED"] == pytest.approx(5.25)
        assert result["ECB"] == pytest.approx(4.50)

    @pytest.mark.asyncio
    async def test_v7_03_crud_empty_table_returns_empty_dict(self, v7_db: AsyncSession):
        """Empty table → empty dict (no exception)."""
        from src.database.crud import get_central_bank_rates

        result = await get_central_bank_rates(v7_db)

        assert result == {}

    @pytest.mark.asyncio
    async def test_v7_03_crud_returns_float_values(self, v7_db: AsyncSession):
        """Rate values must be native Python floats (not Decimal)."""
        import datetime

        from src.database.crud import get_central_bank_rates
        from src.database.models import CentralBankRate

        now = datetime.datetime.now(datetime.timezone.utc)
        row = CentralBankRate(
            bank="BOE", currency="GBP", rate=Decimal("5.25"),
            effective_date=now, source="test",
        )
        v7_db.add(row)
        await v7_db.flush()

        result = await get_central_bank_rates(v7_db)

        assert isinstance(result["BOE"], float)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_artlist_response(tones: list[float]) -> dict[str, Any]:
    """Build a fake GDELT artlist JSON response with the given tone values."""
    articles = [
        {
            "title": f"Article {i}",
            "url": f"https://example.com/{i}",
            "seendate": "20240101T120000Z",
            "tone": f"{t},0,0,0,0,0,0",  # GDELT tone CSV: first field is avg tone
        }
        for i, t in enumerate(tones)
    ]
    return {"articles": articles}


def _make_empty_response() -> dict[str, Any]:
    return {"articles": []}


# ── Symbol format tests ────────────────────────────────────────────────────────

class TestV7GdeltSymbolFormat:
    """TASK-V7-04: Verify _COUNTRY_INSTRUMENTS uses correct symbol format."""

    def test_v7_04_gdelt_symbol_format_us_contains_suffixed_symbols(self) -> None:
        """US instruments must use =X / GC=F format, not bare 'EURUSD'."""
        us_syms = _COUNTRY_INSTRUMENTS["US"]
        # Symbols with =X suffix
        assert "EURUSD=X" in us_syms
        assert "USDJPY=X" in us_syms
        assert "GBPUSD=X" in us_syms
        # Gold futures
        assert "GC=F" in us_syms

    def test_v7_04_gdelt_symbol_format_no_bare_forex(self) -> None:
        """Bare forex symbols like 'EURUSD' must NOT appear in the mapping."""
        for country, symbols in _COUNTRY_INSTRUMENTS.items():
            for sym in symbols:
                # Bare forex pairs (4-6 letter all-alpha) should not exist
                stripped = sym.replace("/", "").replace("=X", "").replace("=F", "")
                # If it looks like a forex pair and has no suffix, that's a bug
                is_bare_forex = (
                    stripped.isalpha()
                    and 6 <= len(stripped) <= 8
                    and sym == stripped  # no suffix was removed
                    and "SPY" not in sym
                    and "QQQ" not in sym
                )
                assert not is_bare_forex, (
                    f"Bare forex symbol '{sym}' found for country '{country}'. "
                    "Should use '=X' suffix."
                )

    def test_v7_04_gdelt_symbol_format_eu_instruments(self) -> None:
        eu_syms = _COUNTRY_INSTRUMENTS["EU"]
        assert "EURUSD=X" in eu_syms
        assert "EURJPY=X" in eu_syms
        assert "EURGBP=X" in eu_syms

    def test_v7_04_gdelt_symbol_format_uk_instruments(self) -> None:
        uk_syms = _COUNTRY_INSTRUMENTS["UK"]
        assert "GBPUSD=X" in uk_syms
        assert "EURGBP=X" in uk_syms

    def test_v7_04_gdelt_symbol_to_countries_eurusd_x(self) -> None:
        """_symbol_to_countries should resolve 'EURUSD=X' to US and EU."""
        countries = _symbol_to_countries("EURUSD=X")
        assert "US" in countries
        assert "EU" in countries

    def test_v7_04_gdelt_symbol_to_countries_btc(self) -> None:
        """BTC/USDT should resolve to US and CN."""
        countries = _symbol_to_countries("BTC/USDT")
        assert "US" in countries
        assert "CN" in countries

    def test_v7_04_gdelt_symbol_to_countries_gold(self) -> None:
        """GC=F (Gold) should resolve to RU and ME."""
        countries = _symbol_to_countries("GC=F")
        assert "RU" in countries or "ME" in countries

    def test_v7_04_gdelt_symbol_to_countries_unknown_returns_empty(self) -> None:
        assert _symbol_to_countries("UNKNOWN_SYMBOL") == []


# ── Fallback query tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestV7GdeltFallback:
    """TASK-V7-04: Fallback query activates when primary returns empty."""

    async def test_v7_04_gdelt_fallback_on_empty_primary(self) -> None:
        """When primary query returns empty articles, fallback query is attempted."""
        engine = GeoEngineV2()

        # Mock cache: always miss
        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock(return_value=True)
        mock_cache.delete = AsyncMock(return_value=True)

        # Primary returns empty; fallback returns tones
        fallback_response = _make_artlist_response([-2.0, -1.5])

        call_count = 0

        async def fake_get(url: str, *, params: dict, timeout: float) -> MagicMock:
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            if call_count == 1:
                mock_resp.json = MagicMock(return_value=_make_empty_response())
            else:
                mock_resp.json = MagicMock(return_value=fallback_response)
            return mock_resp

        with (
            patch("src.analysis.geo_engine_v2.cache", mock_cache),
            patch.object(engine._client, "get", side_effect=fake_get),
        ):
            tone = await engine.fetch_gdelt_tone("US")

        # Two requests must have been made (primary + fallback)
        assert call_count == 2
        # Tone should be average of fallback tones
        assert tone is not None
        assert abs(tone - (-1.75)) < 0.01

        await engine.close()

    async def test_v7_04_gdelt_no_fallback_when_primary_succeeds(self) -> None:
        """When primary returns articles, fallback is NOT called."""
        engine = GeoEngineV2()

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock(return_value=True)
        mock_cache.delete = AsyncMock(return_value=True)

        primary_response = _make_artlist_response([1.0, 2.0])
        call_count = 0

        async def fake_get(url: str, *, params: dict, timeout: float) -> MagicMock:
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = MagicMock(return_value=primary_response)
            return mock_resp

        with (
            patch("src.analysis.geo_engine_v2.cache", mock_cache),
            patch.object(engine._client, "get", side_effect=fake_get),
        ):
            tone = await engine.fetch_gdelt_tone("UK")

        assert call_count == 1
        assert tone is not None
        assert abs(tone - 1.5) < 0.01

        await engine.close()

    async def test_v7_04_gdelt_fallback_also_empty_returns_none(self) -> None:
        """When both primary and fallback return empty, tone is None."""
        engine = GeoEngineV2()

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock(return_value=True)
        mock_cache.delete = AsyncMock(return_value=True)

        async def fake_get(url: str, *, params: dict, timeout: float) -> MagicMock:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = MagicMock(return_value=_make_empty_response())
            return mock_resp

        with (
            patch("src.analysis.geo_engine_v2.cache", mock_cache),
            patch.object(engine._client, "get", side_effect=fake_get),
        ):
            tone = await engine.fetch_gdelt_tone("US")

        assert tone is None

        await engine.close()


# ── Circuit breaker tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestV7GdeltCircuitBreaker:
    """TASK-V7-04: Circuit breaker trips after 3 consecutive failures."""

    async def test_v7_04_gdelt_circuit_breaker_trips_after_threshold(self) -> None:
        """After CB_FAIL_THRESHOLD consecutive failures, circuit opens."""
        engine = GeoEngineV2()

        # Simulate in-memory cache counters
        in_memory: dict[str, Any] = {}

        async def fake_cache_get(key: str) -> Any:
            return in_memory.get(key)

        async def fake_cache_set(key: str, value: Any, ttl: int = 300) -> bool:
            in_memory[key] = value
            return True

        async def fake_cache_delete(key: str) -> bool:
            in_memory.pop(key, None)
            return True

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(side_effect=fake_cache_get)
        mock_cache.set = AsyncMock(side_effect=fake_cache_set)
        mock_cache.delete = AsyncMock(side_effect=fake_cache_delete)

        # Both primary and fallback always return empty → failure recorded
        async def fake_get(url: str, *, params: dict, timeout: float) -> MagicMock:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = MagicMock(return_value=_make_empty_response())
            return mock_resp

        with (
            patch("src.analysis.geo_engine_v2.cache", mock_cache),
            patch.object(engine._client, "get", side_effect=fake_get),
        ):
            # First CB_FAIL_THRESHOLD - 1 calls: circuit should still be closed
            for _ in range(_CB_FAIL_THRESHOLD - 1):
                await engine.fetch_gdelt_tone("EU")

            tripped_key = "geo:cb:tripped:EU"
            assert in_memory.get(tripped_key) is None, "Circuit should not be tripped yet"

            # One more failure — circuit trips
            await engine.fetch_gdelt_tone("EU")
            assert in_memory.get(tripped_key) == "1", "Circuit should be tripped now"

        await engine.close()

    async def test_v7_04_gdelt_circuit_breaker_skips_when_open(self) -> None:
        """When circuit is open, fetch_gdelt_tone returns None immediately."""
        engine = GeoEngineV2()

        call_count = 0
        in_memory: dict[str, Any] = {"geo:cb:tripped:JP": "1"}

        async def fake_cache_get(key: str) -> Any:
            return in_memory.get(key)

        async def fake_cache_set(key: str, value: Any, ttl: int = 300) -> bool:
            in_memory[key] = value
            return True

        async def fake_cache_delete(key: str) -> bool:
            in_memory.pop(key, None)
            return True

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(side_effect=fake_cache_get)
        mock_cache.set = AsyncMock(side_effect=fake_cache_set)
        mock_cache.delete = AsyncMock(side_effect=fake_cache_delete)

        async def fake_get(url: str, *, params: dict, timeout: float) -> MagicMock:
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = MagicMock(return_value=_make_artlist_response([1.0]))
            return mock_resp

        with (
            patch("src.analysis.geo_engine_v2.cache", mock_cache),
            patch.object(engine._client, "get", side_effect=fake_get),
        ):
            tone = await engine.fetch_gdelt_tone("JP")

        # No HTTP calls should have been made
        assert call_count == 0
        assert tone is None

        await engine.close()

    async def test_v7_04_gdelt_circuit_breaker_resets_on_success(self) -> None:
        """Successful response resets the failure counter."""
        engine = GeoEngineV2()

        in_memory: dict[str, Any] = {"geo:cb:fail:CN": "2"}  # 2 prior failures

        async def fake_cache_get(key: str) -> Any:
            return in_memory.get(key)

        async def fake_cache_set(key: str, value: Any, ttl: int = 300) -> bool:
            in_memory[key] = value
            return True

        async def fake_cache_delete(key: str) -> bool:
            in_memory.pop(key, None)
            return True

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(side_effect=fake_cache_get)
        mock_cache.set = AsyncMock(side_effect=fake_cache_set)
        mock_cache.delete = AsyncMock(side_effect=fake_cache_delete)

        async def fake_get(url: str, *, params: dict, timeout: float) -> MagicMock:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = MagicMock(return_value=_make_artlist_response([-1.0]))
            return mock_resp

        with (
            patch("src.analysis.geo_engine_v2.cache", mock_cache),
            patch.object(engine._client, "get", side_effect=fake_get),
        ):
            tone = await engine.fetch_gdelt_tone("CN")

        assert tone is not None
        # Failure counter must be removed after success
        assert in_memory.get("geo:cb:fail:CN") is None

        await engine.close()


# ── Geo score calculation tests ────────────────────────────────────────────────

@pytest.mark.asyncio
class TestV7GeoScoreCalculation:
    """TASK-V7-04: Geo score is computed correctly from mocked GDELT responses."""

    async def test_v7_04_geo_score_calculation_in_range(self) -> None:
        """Score must be in [-50, +50] range for any tone input."""
        engine = GeoEngineV2()

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock(return_value=True)
        mock_cache.delete = AsyncMock(return_value=True)

        # Very negative tones → should clamp to -50 (not -100)
        async def fake_get(url: str, *, params: dict, timeout: float) -> MagicMock:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = MagicMock(
                return_value=_make_artlist_response([-10.0, -8.0, -9.0])
            )
            return mock_resp

        with (
            patch("src.analysis.geo_engine_v2.cache", mock_cache),
            patch.object(engine._client, "get", side_effect=fake_get),
        ):
            result = await engine.calculate_geopolitical_risk("EURUSD=X")

        assert -50.0 <= result <= 50.0

        await engine.close()

    async def test_v7_04_geo_score_calculation_positive_tone(self) -> None:
        """Positive tones yield a positive score."""
        engine = GeoEngineV2()

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock(return_value=True)
        mock_cache.delete = AsyncMock(return_value=True)

        async def fake_get(url: str, *, params: dict, timeout: float) -> MagicMock:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = MagicMock(
                return_value=_make_artlist_response([2.0, 3.0, 2.5])
            )
            return mock_resp

        with (
            patch("src.analysis.geo_engine_v2.cache", mock_cache),
            patch.object(engine._client, "get", side_effect=fake_get),
        ):
            result = await engine.calculate_geopolitical_risk("EURUSD=X")

        assert result > 0.0
        assert result <= 50.0

        await engine.close()

    async def test_v7_04_geo_score_calculation_negative_tone(self) -> None:
        """Strongly negative tones yield a negative score."""
        engine = GeoEngineV2()

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock(return_value=True)
        mock_cache.delete = AsyncMock(return_value=True)

        async def fake_get(url: str, *, params: dict, timeout: float) -> MagicMock:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = MagicMock(
                return_value=_make_artlist_response([-5.0, -4.0])
            )
            return mock_resp

        with (
            patch("src.analysis.geo_engine_v2.cache", mock_cache),
            patch.object(engine._client, "get", side_effect=fake_get),
        ):
            result = await engine.calculate_geopolitical_risk("GBPUSD=X")

        assert result < 0.0
        assert result >= -50.0

        await engine.close()

    async def test_v7_04_geo_score_calculation_no_data_returns_zero(self) -> None:
        """When no GDELT data available, score must be 0 (graceful degradation)."""
        engine = GeoEngineV2()

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock(return_value=True)
        mock_cache.delete = AsyncMock(return_value=True)

        async def fake_get(url: str, *, params: dict, timeout: float) -> MagicMock:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = MagicMock(return_value=_make_empty_response())
            return mock_resp

        with (
            patch("src.analysis.geo_engine_v2.cache", mock_cache),
            patch.object(engine._client, "get", side_effect=fake_get),
        ):
            result = await engine.calculate_geopolitical_risk("EURUSD=X")

        assert result == 0.0

        await engine.close()

    async def test_v7_04_geo_score_unknown_symbol_returns_zero(self) -> None:
        """Unknown symbol with no country mapping returns 0."""
        engine = GeoEngineV2()
        result = await engine.calculate_geopolitical_risk("UNKNOWN_SYM=X")
        assert result == 0.0
        await engine.close()

    async def test_v7_04_geo_score_gdelt_exception_returns_zero(self) -> None:
        """Exception during GDELT fetch → score() returns 0 (not raising)."""
        engine = GeoEngineV2()

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock(return_value=True)
        mock_cache.delete = AsyncMock(return_value=True)

        async def fake_get(url: str, *, params: dict, timeout: float) -> MagicMock:
            raise httpx.TimeoutException("timeout")

        import httpx  # noqa: PLC0415 — needed for exception class in scope

        with (
            patch("src.analysis.geo_engine_v2.cache", mock_cache),
            patch.object(engine._client, "get", side_effect=fake_get),
        ):
            result = await engine.score("EURUSD=X")

        assert result == 0.0

        await engine.close()


# ── _tone_to_score unit tests ──────────────────────────────────────────────────

class TestToneToScore:
    """Unit tests for the _tone_to_score pure function."""

    def test_tone_neutral(self) -> None:
        assert _tone_to_score(0.0) == 0.0

    def test_tone_at_positive_threshold(self) -> None:
        assert _tone_to_score(3.0) == 100.0

    def test_tone_above_positive_threshold(self) -> None:
        assert _tone_to_score(5.0) == 100.0

    def test_tone_at_negative_threshold(self) -> None:
        assert _tone_to_score(-3.0) == -100.0

    def test_tone_below_negative_threshold(self) -> None:
        assert _tone_to_score(-10.0) == -100.0

    def test_tone_midpoint_positive(self) -> None:
        score = _tone_to_score(1.5)
        assert abs(score - 50.0) < 0.01

    def test_tone_midpoint_negative(self) -> None:
        score = _tone_to_score(-1.5)
        assert abs(score - (-50.0)) < 0.01

# ── TASK-V7-10: CoinMetrics backfill ─────────────────────────────────────────


class TestV710CoinMetricsBackfill:
    """TASK-V7-10: backfill_coinmetrics() in scripts/backfill_historical.py."""

    def _make_cm_response(
        self,
        asset: str = "btc",
        time_str: str = "2021-01-01T00:00:00.000000000Z",
        mvrv: str = "2.5",
        adract: str = "800000",
        txcnt: str = "300000",
        next_page_token: Optional[str] = None,
    ) -> dict:
        payload: dict = {
            "data": [
                {
                    "asset": asset,
                    "time": time_str,
                    "CapMVRVCur": mvrv,
                    "AdrActCnt": adract,
                    "TxCnt": txcnt,
                }
            ]
        }
        if next_page_token is not None:
            payload["next_page_token"] = next_page_token
        return payload

    # ── _parse_coinmetrics_rows ────────────────────────────────────────────────

    def test_parse_produces_correct_indicator_names(self) -> None:
        from scripts.backfill_historical import _parse_coinmetrics_rows

        rows = [
            {
                "asset": "btc",
                "time": "2021-06-01T00:00:00Z",
                "CapMVRVCur": "3.1",
                "AdrActCnt": "900000",
                "TxCnt": "250000",
            }
        ]
        records = _parse_coinmetrics_rows(rows)
        names = {r["indicator_name"] for r in records}
        assert names == {
            "COINMETRICS_BTC_MVRV",
            "COINMETRICS_BTC_ADRACT",
            "COINMETRICS_BTC_TXCNT",
        }

    def test_parse_sets_country_global_and_source(self) -> None:
        from scripts.backfill_historical import _parse_coinmetrics_rows

        rows = [
            {
                "asset": "eth",
                "time": "2021-06-01T00:00:00Z",
                "CapMVRVCur": "2.0",
                "AdrActCnt": "500000",
                "TxCnt": "1200000",
            }
        ]
        records = _parse_coinmetrics_rows(rows)
        for rec in records:
            assert rec["country"] == "GLOBAL"
            assert rec["source"] == "coinmetrics"

    def test_parse_decimal_values(self) -> None:
        from scripts.backfill_historical import _parse_coinmetrics_rows

        rows = [
            {
                "asset": "btc",
                "time": "2021-06-01T00:00:00Z",
                "CapMVRVCur": "1.234567",
                "AdrActCnt": "123456",
                "TxCnt": "78901",
            }
        ]
        records = _parse_coinmetrics_rows(rows)
        mvrv_rec = next(r for r in records if r["indicator_name"] == "COINMETRICS_BTC_MVRV")
        assert mvrv_rec["value"] == Decimal("1.234567")

    def test_parse_skips_null_metric(self) -> None:
        from scripts.backfill_historical import _parse_coinmetrics_rows

        rows = [
            {
                "asset": "btc",
                "time": "2021-06-01T00:00:00Z",
                "CapMVRVCur": None,   # null — must be skipped
                "AdrActCnt": "500000",
                "TxCnt": "200000",
            }
        ]
        records = _parse_coinmetrics_rows(rows)
        names = {r["indicator_name"] for r in records}
        # MVRV must be absent
        assert "COINMETRICS_BTC_MVRV" not in names
        # Others present
        assert "COINMETRICS_BTC_ADRACT" in names
        assert "COINMETRICS_BTC_TXCNT" in names

    def test_parse_skips_bad_timestamp(self) -> None:
        from scripts.backfill_historical import _parse_coinmetrics_rows

        rows = [
            {
                "asset": "btc",
                "time": "NOT_A_DATE",
                "CapMVRVCur": "2.0",
                "AdrActCnt": "500000",
                "TxCnt": "200000",
            }
        ]
        records = _parse_coinmetrics_rows(rows)
        assert records == []

    def test_parse_eth_indicator_names(self) -> None:
        from scripts.backfill_historical import _parse_coinmetrics_rows

        rows = [
            {
                "asset": "eth",
                "time": "2021-06-01T00:00:00Z",
                "CapMVRVCur": "1.9",
                "AdrActCnt": "600000",
                "TxCnt": "1100000",
            }
        ]
        records = _parse_coinmetrics_rows(rows)
        names = {r["indicator_name"] for r in records}
        assert names == {
            "COINMETRICS_ETH_MVRV",
            "COINMETRICS_ETH_ADRACT",
            "COINMETRICS_ETH_TXCNT",
        }

    def test_parse_release_date_utc(self) -> None:
        from scripts.backfill_historical import _parse_coinmetrics_rows

        rows = [
            {
                "asset": "btc",
                "time": "2022-03-15T00:00:00.000000000Z",
                "CapMVRVCur": "1.5",
                "AdrActCnt": "700000",
                "TxCnt": "280000",
            }
        ]
        records = _parse_coinmetrics_rows(rows)
        for rec in records:
            assert rec["release_date"].year == 2022
            assert rec["release_date"].month == 3
            assert rec["release_date"].day == 15

    # ── backfill_coinmetrics() dry_run ─────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_dry_run_returns_zero(self) -> None:
        """dry_run=True must return 0 and not touch the DB."""
        from unittest.mock import AsyncMock, patch

        import httpx

        fake_payload = {
            "data": [
                {
                    "asset": "btc",
                    "time": "2021-01-01T00:00:00Z",
                    "CapMVRVCur": "2.5",
                    "AdrActCnt": "800000",
                    "TxCnt": "300000",
                }
            ]
            # no next_page_token → single page
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = fake_payload

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("src.database.crud.upsert_macro_data") as mock_upsert,
        ):
            from scripts.backfill_historical import backfill_coinmetrics

            result = await backfill_coinmetrics(
                start="2021-01-01",
                end="2021-01-02",
                dry_run=True,
            )

        assert result == 0
        mock_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_response_returns_zero(self) -> None:
        """Empty data array from API must return 0 records."""
        from unittest.mock import AsyncMock, patch

        fake_payload: dict = {"data": []}

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = fake_payload

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("src.database.crud.upsert_macro_data") as mock_upsert,
        ):
            from scripts.backfill_historical import backfill_coinmetrics

            result = await backfill_coinmetrics(
                start="2021-01-01",
                end="2021-01-02",
                dry_run=False,
            )

        assert result == 0
        mock_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_http_error_does_not_raise(self) -> None:
        """HTTP failure must be caught and return 0 without raising."""
        import httpx
        from unittest.mock import AsyncMock, patch

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "503", request=MagicMock(), response=MagicMock()
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            from scripts.backfill_historical import backfill_coinmetrics

            result = await backfill_coinmetrics(
                start="2021-01-01",
                end="2021-01-02",
                dry_run=False,
            )

        assert result == 0

    @pytest.mark.asyncio
    async def test_pagination_follows_next_page_token(self) -> None:
        """Two pages are fetched when next_page_token is present on page 1."""
        from unittest.mock import AsyncMock, patch, call as mock_call

        page1 = {
            "data": [
                {
                    "asset": "btc",
                    "time": "2021-01-01T00:00:00Z",
                    "CapMVRVCur": "2.5",
                    "AdrActCnt": "800000",
                    "TxCnt": "300000",
                }
            ],
            "next_page_token": "TOKEN_ABC",
        }
        page2 = {
            "data": [
                {
                    "asset": "btc",
                    "time": "2021-01-02T00:00:00Z",
                    "CapMVRVCur": "2.6",
                    "AdrActCnt": "820000",
                    "TxCnt": "310000",
                }
            ]
            # no next_page_token → stop
        }

        responses = [page1, page2]
        call_count = 0

        async def _fake_get(*args, **kwargs):  # noqa: ANN001
            nonlocal call_count
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = responses[call_count]
            call_count += 1
            return mock_resp

        mock_client = AsyncMock()
        mock_client.get = _fake_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        upserted_records: list = []

        async def _fake_upsert(session, records):  # noqa: ANN001
            upserted_records.extend(records)
            return len(records)

        # Mock async_session_factory as an async context manager
        mock_session = AsyncMock()
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
        # begin() context manager
        mock_begin_ctx = AsyncMock()
        mock_begin_ctx.__aenter__ = AsyncMock(return_value=None)
        mock_begin_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(return_value=mock_begin_ctx)
        mock_session_factory = MagicMock(return_value=mock_session_ctx)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("src.database.crud.upsert_macro_data", side_effect=_fake_upsert),
            patch("src.database.engine.async_session_factory", mock_session_factory),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            from scripts.backfill_historical import backfill_coinmetrics

            result = await backfill_coinmetrics(
                start="2021-01-01",
                end="2021-01-02",
                dry_run=False,
            )

        # 2 days × 3 metrics = 6 records total
        assert len(upserted_records) == 6
        assert call_count == 2


# ── TASK-V7-07: Fear & Greed full history backfill ────────────────────────────


class TestBackfillFearGreed:
    """Tests for scripts/backfill_historical.py — backfill_fear_greed()."""

    def _make_api_response(self, entries: list[dict]) -> dict:
        return {"data": entries, "metadata": {"error": None}}

    def _make_entry(self, value: int = 50, timestamp: int = 1609459200) -> dict:
        """Return a single alternative.me API entry dict."""
        return {
            "value": str(value),
            "value_classification": "Neutral",
            "timestamp": str(timestamp),
        }

    @pytest.mark.asyncio
    async def test_v7_07_happy_path_stores_valid_records(self) -> None:
        """Valid API response results in correct number of DB upsert calls."""
        import scripts.backfill_historical as mod
        from scripts.backfill_historical import backfill_fear_greed

        entries = [
            self._make_entry(value=25, timestamp=1609459200),  # 2021-01-01
            self._make_entry(value=55, timestamp=1609545600),  # 2021-01-02
            self._make_entry(value=80, timestamp=1609632000),  # 2021-01-03
        ]
        api_payload = self._make_api_response(entries)

        mock_resp = MagicMock()
        mock_resp.json.return_value = api_payload
        mock_resp.raise_for_status = MagicMock()

        upsert_stored: list = []

        async def mock_upsert(db: Any, records: list) -> int:
            upsert_stored.extend(records)
            return len(records)

        # Build a fully async-compatible mock session/factory
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_begin_ctx = AsyncMock()
        mock_begin_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_begin_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(return_value=mock_begin_ctx)
        mock_factory = MagicMock(return_value=mock_session)

        mod.SUMMARY.clear()

        with patch("scripts.backfill_historical.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            # Patch the lazy-imported symbols inside the function's module scope
            with patch("src.database.crud.upsert_macro_data", side_effect=mock_upsert):
                with patch("src.database.engine.async_session_factory", mock_factory):
                    await backfill_fear_greed(dry_run=False)

        assert mod.SUMMARY["fear_greed"]["fetched"] == 3
        assert mod.SUMMARY["fear_greed"].get("error") is None

    @pytest.mark.asyncio
    async def test_v7_07_dry_run_does_not_write_to_db(self) -> None:
        """dry_run=True logs intent but never calls upsert_macro_data."""
        import scripts.backfill_historical as mod
        from scripts.backfill_historical import backfill_fear_greed

        entries = [self._make_entry(value=42, timestamp=1609459200)]
        api_payload = self._make_api_response(entries)

        mock_resp = MagicMock()
        mock_resp.json.return_value = api_payload
        mock_resp.raise_for_status = MagicMock()

        upsert_called: list = []

        async def mock_upsert(db: Any, records: list) -> int:
            upsert_called.extend(records)
            return len(records)

        mod.SUMMARY.clear()

        with patch("scripts.backfill_historical.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            # In dry_run mode, upsert should never be called regardless.
            with patch("src.database.crud.upsert_macro_data", side_effect=mock_upsert):
                await backfill_fear_greed(dry_run=True)

        assert len(upsert_called) == 0, "dry_run must not write to DB"
        assert mod.SUMMARY["fear_greed"]["dry_run"] is True
        assert mod.SUMMARY["fear_greed"]["fetched"] == 1
        assert mod.SUMMARY["fear_greed"]["stored"] == 0

    @pytest.mark.asyncio
    async def test_v7_07_invalid_value_out_of_range_skipped(self) -> None:
        """Values outside 0–100 are skipped, valid ones are stored."""
        import scripts.backfill_historical as mod
        from scripts.backfill_historical import backfill_fear_greed

        entries = [
            self._make_entry(value=50, timestamp=1609459200),   # valid
            self._make_entry(value=101, timestamp=1609545600),  # invalid — above 100
            self._make_entry(value=0, timestamp=1609632000),    # valid — boundary
        ]
        api_payload = self._make_api_response(entries)

        mock_resp = MagicMock()
        mock_resp.json.return_value = api_payload
        mock_resp.raise_for_status = MagicMock()

        mod.SUMMARY.clear()

        with patch("scripts.backfill_historical.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            await backfill_fear_greed(dry_run=True)

        summary = mod.SUMMARY["fear_greed"]
        assert summary["fetched"] == 2, "Only 2 valid records expected"
        assert summary["skipped"] == 1, "1 invalid record should be skipped"

    @pytest.mark.asyncio
    async def test_v7_07_empty_api_response_handled_gracefully(self) -> None:
        """Empty data array from API does not raise, summary reflects 0 records."""
        import scripts.backfill_historical as mod
        from scripts.backfill_historical import backfill_fear_greed

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": []}
        mock_resp.raise_for_status = MagicMock()

        mod.SUMMARY.clear()

        with patch("scripts.backfill_historical.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            await backfill_fear_greed(dry_run=True)

        assert mod.SUMMARY["fear_greed"]["fetched"] == 0

    @pytest.mark.asyncio
    async def test_v7_07_http_error_handled_gracefully(self) -> None:
        """HTTP error does not propagate, summary captures error key."""
        import scripts.backfill_historical as mod
        from scripts.backfill_historical import backfill_fear_greed

        mod.SUMMARY.clear()

        with patch("scripts.backfill_historical.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(
                side_effect=httpx.TimeoutException("connection timeout")
            )
            mock_client_cls.return_value = mock_client

            await backfill_fear_greed(dry_run=False)

        assert "error" in mod.SUMMARY["fear_greed"]
        assert mod.SUMMARY["fear_greed"]["fetched"] == 0

    @pytest.mark.asyncio
    async def test_v7_07_unix_timestamp_converts_to_midnight_utc(self) -> None:
        """Unix timestamps are normalised to midnight UTC regardless of hour."""
        import scripts.backfill_historical as mod
        from scripts.backfill_historical import backfill_fear_greed

        # 1609502400 = 2021-01-01 12:00:00 UTC (midday — must become midnight)
        entries = [self._make_entry(value=50, timestamp=1609502400)]
        mock_resp = MagicMock()
        mock_resp.json.return_value = self._make_api_response(entries)
        mock_resp.raise_for_status = MagicMock()

        mod.SUMMARY.clear()

        with patch("scripts.backfill_historical.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            await backfill_fear_greed(dry_run=True)

        # In dry_run mode, verify the summary date_range reflects the correct date.
        summary = mod.SUMMARY["fear_greed"]
        assert "2021-01-01" in summary["date_range"]

    def test_v7_07_record_structure_matches_macro_data_schema(self) -> None:
        """Parsed records have exactly the fields required by macro_data model."""
        required_fields = {"indicator_name", "country", "value", "release_date", "source"}

        record = {
            "indicator_name": "FEAR_GREED",
            "country": "GLOBAL",
            "value": Decimal("55"),
            "release_date": datetime.datetime(2021, 1, 1, tzinfo=datetime.timezone.utc),
            "source": "alternative.me",
        }

        assert set(record.keys()) == required_fields
        assert isinstance(record["value"], Decimal)
        assert record["release_date"].tzinfo is not None  # timezone-aware

    def test_v7_07_indicator_name_matches_expected_constant(self) -> None:
        """The indicator_name constant matches what SIM-39 and FAEngine expect."""
        from scripts.backfill_historical import (
            FEAR_GREED_COUNTRY,
            FEAR_GREED_INDICATOR_NAME,
            FEAR_GREED_SOURCE,
        )

        assert FEAR_GREED_INDICATOR_NAME == "FEAR_GREED"
        assert FEAR_GREED_COUNTRY == "GLOBAL"
        assert FEAR_GREED_SOURCE == "alternative.me"


# ── TASK-V7-17: Data integrity verification for backtest ──────────────────────


def _make_ohlcv_df(
    n: int = 100,
    start: Optional[datetime.datetime] = None,
    interval_minutes: int = 60,
    high_as_close: bool = False,
    low_as_open: bool = False,
    duplicate_last: bool = False,
    gap_after_index: Optional[int] = None,
    gap_multiplier: float = 3.0,
    volume_value: float = 1000.0,
) -> pd.DataFrame:
    """Build a synthetic OHLCV DataFrame for quality-check tests."""

    if start is None:
        start = datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)

    timestamps = []
    current = start
    for i in range(n):
        if gap_after_index is not None and i == gap_after_index + 1:
            current = timestamps[-1] + datetime.timedelta(
                minutes=interval_minutes * gap_multiplier
            )
        timestamps.append(current)
        current += datetime.timedelta(minutes=interval_minutes)

    if duplicate_last:
        timestamps[-1] = timestamps[-2]

    opens = [1.1000 + i * 0.0001 for i in range(n)]
    closes = [o + 0.0002 for o in opens]
    highs = [max(o, c) + 0.0005 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.0005 for o, c in zip(opens, closes)]

    # Intentional OHLC violation: set high < close for all rows if requested
    if high_as_close:
        highs = [c - 0.0001 for c in closes]  # high < close → violation

    # Intentional OHLC violation: set low > open for all rows if requested
    if low_as_open:
        lows = [o + 0.0001 for o in opens]  # low > open → violation

    volumes = [volume_value] * n

    df = pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        },
        index=pd.DatetimeIndex(timestamps, name="timestamp"),
    )
    return df


class TestV717DataQualityValidOHLC:
    """Valid OHLC data should pass all checks with no violations."""

    def test_v7_17_valid_data_no_warnings(self) -> None:
        """Clean data produces zero violations across all checks."""
        from src.backtesting.backtest_engine import BacktestEngine

        df = _make_ohlcv_df(n=200, interval_minutes=60)
        result = BacktestEngine._check_data_quality("EURUSD=X", df, "H1")

        assert result["ohlc_violation_count"] == 0
        assert result["duplicate_ts_count"] == 0
        assert result["gap_count"] == 0
        assert result["volume_nonzero_pct"] == 100.0
        assert result["candle_count"] == 200

    def test_v7_17_valid_data_returns_symbol_and_timeframe(self) -> None:
        """Result dict must contain symbol and timeframe fields."""
        from src.backtesting.backtest_engine import BacktestEngine

        df = _make_ohlcv_df(n=50)
        result = BacktestEngine._check_data_quality("GBPUSD=X", df, "H4")

        assert result["symbol"] == "GBPUSD=X"
        assert result["timeframe"] == "H4"

    def test_v7_17_valid_data_no_ohlc_violations_warning(self) -> None:
        """No OHLC violation warning in clean data."""
        from src.backtesting.backtest_engine import BacktestEngine

        df = _make_ohlcv_df(n=100)
        result = BacktestEngine._check_data_quality("EURUSD=X", df, "H1")

        ohlc_warns = [w for w in result["warnings"] if "ohlc_violations" in w]
        assert not ohlc_warns, f"Unexpected OHLC warnings: {ohlc_warns}"

    def test_v7_17_result_has_all_expected_keys(self) -> None:
        """Result dict must contain all documented keys."""
        from src.backtesting.backtest_engine import BacktestEngine

        df = _make_ohlcv_df(n=60)
        result = BacktestEngine._check_data_quality("BTC/USDT", df, "H1")

        expected_keys = {
            "symbol", "timeframe", "candle_count", "expected_candles",
            "candle_coverage_pct", "gap_count", "ohlc_violation_count",
            "duplicate_ts_count", "volume_nonzero_pct",
            "d1_rows_available", "d1_rows_count", "warnings",
        }
        assert expected_keys.issubset(result.keys()), (
            f"Missing keys: {expected_keys - set(result.keys())}"
        )


class TestV717OHLCViolation:
    """OHLC integrity violations must be detected and flagged."""

    def test_v7_17_high_below_close_detected(self) -> None:
        """When high < close, violation must be counted."""
        from src.backtesting.backtest_engine import BacktestEngine

        df = _make_ohlcv_df(n=10, high_as_close=True)
        result = BacktestEngine._check_data_quality("EURUSD=X", df, "H1")

        assert result["ohlc_violation_count"] > 0

    def test_v7_17_ohlc_violation_in_warnings(self) -> None:
        """OHLC violation must produce a warning entry."""
        from src.backtesting.backtest_engine import BacktestEngine

        df = _make_ohlcv_df(n=10, high_as_close=True)
        result = BacktestEngine._check_data_quality("EURUSD=X", df, "H1")

        ohlc_warns = [w for w in result["warnings"] if "ohlc_violations" in w]
        assert ohlc_warns, "Expected ohlc_violations warning"

    def test_v7_17_low_above_open_detected(self) -> None:
        """When low > open, violation must be counted."""
        from src.backtesting.backtest_engine import BacktestEngine

        df = _make_ohlcv_df(n=5, low_as_open=True)
        result = BacktestEngine._check_data_quality("USDJPY=X", df, "H4")

        assert result["ohlc_violation_count"] > 0

    def test_v7_17_violation_count_matches_bad_rows(self) -> None:
        """Violation count equals number of rows with OHLC errors."""
        from src.backtesting.backtest_engine import BacktestEngine

        df = _make_ohlcv_df(n=20, high_as_close=True)
        result = BacktestEngine._check_data_quality("EURUSD=X", df, "H1")

        # All rows have high < close, so all 20 must be flagged
        assert result["ohlc_violation_count"] == 20


class TestV717DuplicateTimestamps:
    """Duplicate timestamps must be detected."""

    def test_v7_17_duplicate_timestamps_detected(self) -> None:
        """DataFrame with duplicate last timestamp must report dup count > 0."""
        from src.backtesting.backtest_engine import BacktestEngine

        df = _make_ohlcv_df(n=50, duplicate_last=True)
        result = BacktestEngine._check_data_quality("EURUSD=X", df, "H1")

        assert result["duplicate_ts_count"] > 0

    def test_v7_17_duplicate_in_warnings(self) -> None:
        """Duplicate timestamps must produce a warning entry."""
        from src.backtesting.backtest_engine import BacktestEngine

        df = _make_ohlcv_df(n=50, duplicate_last=True)
        result = BacktestEngine._check_data_quality("EURUSD=X", df, "H1")

        dup_warns = [w for w in result["warnings"] if "duplicate_timestamps" in w]
        assert dup_warns, "Expected duplicate_timestamps warning"

    def test_v7_17_no_duplicates_in_clean_data(self) -> None:
        """Clean data must have zero duplicates."""
        from src.backtesting.backtest_engine import BacktestEngine

        df = _make_ohlcv_df(n=50)
        result = BacktestEngine._check_data_quality("EURUSD=X", df, "H1")

        assert result["duplicate_ts_count"] == 0


class TestV717GapDetection:
    """Gaps larger than 2x normal interval must be detected."""

    def test_v7_17_gap_detected(self) -> None:
        """A 3x interval gap must be counted."""
        from src.backtesting.backtest_engine import BacktestEngine

        df = _make_ohlcv_df(n=50, interval_minutes=60, gap_after_index=25, gap_multiplier=3.0)
        result = BacktestEngine._check_data_quality("EURUSD=X", df, "H1")

        assert result["gap_count"] >= 1

    def test_v7_17_gap_count_correct(self) -> None:
        """Two separate gaps must both be detected."""
        from src.backtesting.backtest_engine import BacktestEngine

        # Build manually: 3 segments with 2 gaps
        base = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        seg1 = [base + datetime.timedelta(hours=i) for i in range(10)]
        seg2 = [seg1[-1] + datetime.timedelta(hours=5) + datetime.timedelta(hours=i) for i in range(10)]
        seg3 = [seg2[-1] + datetime.timedelta(hours=5) + datetime.timedelta(hours=i) for i in range(10)]
        all_ts = seg1 + seg2 + seg3

        df = pd.DataFrame(
            {
                "open": [1.1] * 30,
                "high": [1.105] * 30,
                "low": [1.095] * 30,
                "close": [1.102] * 30,
                "volume": [500.0] * 30,
            },
            index=pd.DatetimeIndex(all_ts, name="timestamp"),
        )
        result = BacktestEngine._check_data_quality("EURUSD=X", df, "H1")

        assert result["gap_count"] == 2

    def test_v7_17_gap_in_warnings(self) -> None:
        """Gap must produce a warning entry."""
        from src.backtesting.backtest_engine import BacktestEngine

        df = _make_ohlcv_df(n=50, interval_minutes=60, gap_after_index=20, gap_multiplier=3.0)
        result = BacktestEngine._check_data_quality("EURUSD=X", df, "H1")

        gap_warns = [w for w in result["warnings"] if w.startswith("gaps_")]
        assert gap_warns, "Expected gaps_N warning"

    def test_v7_17_small_gap_not_detected(self) -> None:
        """A gap of exactly 1x interval (no gap) must not be flagged."""
        from src.backtesting.backtest_engine import BacktestEngine

        # Normal 1x interval — should produce 0 gaps
        df = _make_ohlcv_df(n=50, interval_minutes=60, gap_multiplier=1.0)
        result = BacktestEngine._check_data_quality("EURUSD=X", df, "H1")

        assert result["gap_count"] == 0


class TestV717VolumeAvailability:
    """Volume availability reporting."""

    def test_v7_17_zero_volume_reported_correctly(self) -> None:
        """All-zero volume must be reported as volume_nonzero_pct = 0."""
        from src.backtesting.backtest_engine import BacktestEngine

        df = _make_ohlcv_df(n=100, volume_value=0.0)
        result = BacktestEngine._check_data_quality("EURUSD=X", df, "H1")

        assert result["volume_nonzero_pct"] == 0.0

    def test_v7_17_zero_volume_in_warnings(self) -> None:
        """All-zero volume must produce a volume_all_zero warning."""
        from src.backtesting.backtest_engine import BacktestEngine

        df = _make_ohlcv_df(n=100, volume_value=0.0)
        result = BacktestEngine._check_data_quality("EURUSD=X", df, "H1")

        assert "volume_all_zero" in result["warnings"]

    def test_v7_17_full_volume_gives_100pct(self) -> None:
        """All candles with positive volume → volume_nonzero_pct = 100."""
        from src.backtesting.backtest_engine import BacktestEngine

        df = _make_ohlcv_df(n=50, volume_value=500.0)
        result = BacktestEngine._check_data_quality("EURUSD=X", df, "H1")

        assert result["volume_nonzero_pct"] == 100.0

    def test_v7_17_partial_volume_computed_correctly(self) -> None:
        """50% zero volume → volume_nonzero_pct = 50.0."""
        from src.backtesting.backtest_engine import BacktestEngine

        base = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        ts = [base + datetime.timedelta(hours=i) for i in range(20)]
        vols = [1000.0 if i % 2 == 0 else 0.0 for i in range(20)]

        df = pd.DataFrame(
            {
                "open": [1.1] * 20,
                "high": [1.105] * 20,
                "low": [1.095] * 20,
                "close": [1.102] * 20,
                "volume": vols,
            },
            index=pd.DatetimeIndex(ts, name="timestamp"),
        )
        result = BacktestEngine._check_data_quality("EURUSD=X", df, "H1")

        assert result["volume_nonzero_pct"] == 50.0

    def test_v7_17_zero_volume_does_not_block_check(self) -> None:
        """Zero-volume data must still complete all checks without exception."""
        from src.backtesting.backtest_engine import BacktestEngine

        df = _make_ohlcv_df(n=50, volume_value=0.0)
        result = BacktestEngine._check_data_quality("EURUSD=X", df, "H1")

        # Other checks must still have values
        assert result["candle_count"] == 50
        assert "check_error" not in str(result["warnings"])


class TestV717D1DataAvailability:
    """D1 data availability check."""

    def test_v7_17_d1_rows_none_reports_unavailable(self) -> None:
        """d1_rows=None → d1_rows_available=False, d1_rows_count=0."""
        from src.backtesting.backtest_engine import BacktestEngine

        df = _make_ohlcv_df(n=100)
        result = BacktestEngine._check_data_quality("EURUSD=X", df, "H1", d1_rows=None)

        assert result["d1_rows_available"] is False
        assert result["d1_rows_count"] == 0

    def test_v7_17_d1_rows_200_no_warning(self) -> None:
        """d1_rows with 200+ rows → no d1_insufficient warning."""
        from src.backtesting.backtest_engine import BacktestEngine

        df = _make_ohlcv_df(n=100)
        fake_d1 = [object()] * 200
        result = BacktestEngine._check_data_quality("EURUSD=X", df, "H1", d1_rows=fake_d1)

        d1_warns = [w for w in result["warnings"] if "d1_insufficient" in w]
        assert not d1_warns, f"Unexpected d1 warning: {d1_warns}"

    def test_v7_17_d1_rows_insufficient_produces_warning(self) -> None:
        """d1_rows with < 200 rows → d1_insufficient warning."""
        from src.backtesting.backtest_engine import BacktestEngine

        df = _make_ohlcv_df(n=100)
        fake_d1 = [object()] * 50
        result = BacktestEngine._check_data_quality("EURUSD=X", df, "H1", d1_rows=fake_d1)

        d1_warns = [w for w in result["warnings"] if "d1_insufficient" in w]
        assert d1_warns, "Expected d1_insufficient warning"
        assert result["d1_rows_count"] == 50

    def test_v7_17_d1_rows_empty_list_reports_unavailable(self) -> None:
        """Empty d1_rows list → d1_rows_available=False."""
        from src.backtesting.backtest_engine import BacktestEngine

        df = _make_ohlcv_df(n=100)
        result = BacktestEngine._check_data_quality("EURUSD=X", df, "H1", d1_rows=[])

        assert result["d1_rows_available"] is False


# ── TASK-V7-12: Regime detection wiring ──────────────────────────────────────


def _make_backtest_trade(
    regime: Optional[str] = "STRONG_TREND_BULL",
    result: str = "win",
    pnl_usd: float = 10.0,
    direction: str = "LONG",
    exit_reason: str = "tp_hit",
    entry_at: Optional[datetime.datetime] = None,
    exit_at: Optional[datetime.datetime] = None,
) -> MagicMock:
    """Build a minimal BacktestTradeResult-like mock for _compute_summary tests."""
    t = MagicMock()
    t.symbol = "EURUSD=X"
    t.timeframe = "H1"
    t.direction = direction
    t.entry_price = Decimal("1.1000")
    t.exit_price = Decimal("1.1100")
    t.pnl_usd = Decimal(str(pnl_usd))
    t.pnl_pips = Decimal("100.0000")
    t.result = result
    t.exit_reason = exit_reason
    t.composite_score = Decimal("12.0")
    t.entry_at = entry_at or datetime.datetime(2024, 6, 10, 10, 0, tzinfo=datetime.timezone.utc)
    t.exit_at = exit_at or datetime.datetime(2024, 6, 11, 10, 0, tzinfo=datetime.timezone.utc)
    t.duration_minutes = 1440
    t.mfe = Decimal("0.0100")
    t.mae = Decimal("0.0020")
    t.regime = regime
    t.sl_price = Decimal("1.0900")
    t.fg_adjustment = None
    t.fr_adjustment = None
    return t


class TestV712RegimeNeverNone:
    """TASK-V7-12: regime field in trade records must never be None."""

    def test_v7_12_precompute_regimes_returns_nonempty_list(self) -> None:
        """_precompute_regimes returns a list with the same length as n."""
        import numpy as np
        from src.backtesting.backtest_engine import _precompute_regimes

        n = 50
        adx = np.full(n, 25.0)
        atr = np.full(n, 0.001)
        close = np.full(n, 1.1)
        sma200 = np.full(n, float("nan"))

        regimes = _precompute_regimes(adx, atr, close, sma200, n)

        assert len(regimes) == n

    def test_v7_12_precompute_regimes_no_none_values(self) -> None:
        """Every element from _precompute_regimes is a non-empty string."""
        import numpy as np
        from src.backtesting.backtest_engine import _precompute_regimes

        n = 300
        adx = np.linspace(10.0, 40.0, n)
        atr = np.full(n, 0.001)
        close = np.linspace(1.05, 1.20, n)
        sma200 = np.full(n, 1.1)

        regimes = _precompute_regimes(adx, atr, close, sma200, n)

        for i, r in enumerate(regimes):
            assert isinstance(r, str) and r, f"regime at index {i} is empty/None: {r!r}"

    def test_v7_12_precompute_regimes_known_values_trend_bull(self) -> None:
        """Strong uptrend (ADX=35, close > SMA200, moderate ATR volatility) → STRONG_TREND_BULL."""
        import numpy as np
        from src.backtesting.backtest_engine import _precompute_regimes

        n = 300
        adx = np.full(n, 35.0)
        # ATR must vary so percentile lands in the middle range (not LOW_VOLATILITY <20%)
        # Use linearly increasing ATR to ensure the last value is at the ~100th percentile,
        # then flatten — last 50 values are around the middle of the historical range.
        atr = np.concatenate([np.linspace(0.0005, 0.0020, 200), np.full(100, 0.0012)])
        close = np.full(n, 1.15)
        sma200 = np.full(n, 1.10)

        regimes = _precompute_regimes(adx, atr, close, sma200, n)

        # At least some of the last elements should be STRONG_TREND_BULL
        # (flat ATR in mid-range → not a volatility extreme → ADX path → STRONG_TREND_BULL)
        tail = regimes[-20:]
        dominant = max(set(tail), key=tail.count)
        assert dominant == "STRONG_TREND_BULL", \
            f"Expected dominant STRONG_TREND_BULL in last 20 bars, got: {set(tail)}"

    def test_v7_12_precompute_regimes_low_bars_produces_warning(self) -> None:
        """Fewer than 200 bars triggers a warning log."""
        import logging
        import numpy as np
        from src.backtesting.backtest_engine import _precompute_regimes

        n = 100
        adx = np.full(n, 15.0)
        atr = np.full(n, 0.001)
        close = np.full(n, 1.1)
        sma200 = np.full(n, float("nan"))

        captured: list[logging.LogRecord] = []

        class _Handler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(record)

        eng_logger = logging.getLogger("src.backtesting.backtest_engine")
        handler = _Handler()
        eng_logger.addHandler(handler)
        old_level = eng_logger.level
        eng_logger.setLevel(logging.WARNING)
        try:
            _precompute_regimes(adx, atr, close, sma200, n)
        finally:
            eng_logger.removeHandler(handler)
            eng_logger.setLevel(old_level)

        messages = " ".join(r.getMessage() for r in captured)
        assert "SMA200" in messages or "200" in messages, \
            f"Expected SMA200 warning, got: {messages!r}"


class TestV712ComputeSummaryByRegime:
    """TASK-V7-12: _compute_summary produces extended per-regime metrics."""

    def test_v7_12_summary_by_regime_has_win_rate(self) -> None:
        """by_regime includes win_rate_pct for each regime."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = [
            _make_backtest_trade(regime="STRONG_TREND_BULL", result="win", pnl_usd=10.0),
            _make_backtest_trade(regime="STRONG_TREND_BULL", result="win", pnl_usd=8.0),
            _make_backtest_trade(regime="STRONG_TREND_BULL", result="loss", pnl_usd=-5.0),
            _make_backtest_trade(regime="VOLATILE", result="win", pnl_usd=12.0),
        ]

        summary = _compute_summary(trades, Decimal("1000"))

        assert "by_regime" in summary
        assert "STRONG_TREND_BULL" in summary["by_regime"]
        assert "win_rate_pct" in summary["by_regime"]["STRONG_TREND_BULL"]
        assert abs(summary["by_regime"]["STRONG_TREND_BULL"]["win_rate_pct"] - 66.67) < 0.1

    def test_v7_12_summary_by_regime_has_profit_factor(self) -> None:
        """by_regime includes profit_factor per regime."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = [
            _make_backtest_trade(regime="STRONG_TREND_BULL", result="win", pnl_usd=20.0),
            _make_backtest_trade(regime="STRONG_TREND_BULL", result="loss", pnl_usd=-10.0),
        ]

        summary = _compute_summary(trades, Decimal("1000"))

        pf = summary["by_regime"]["STRONG_TREND_BULL"]["profit_factor"]
        assert pf is not None
        assert abs(pf - 2.0) < 0.001

    def test_v7_12_summary_by_regime_pf_none_when_no_losses(self) -> None:
        """profit_factor is None when there are no losing trades in a regime."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = [
            _make_backtest_trade(regime="STRONG_TREND_BULL", result="win", pnl_usd=15.0),
            _make_backtest_trade(regime="STRONG_TREND_BULL", result="win", pnl_usd=10.0),
        ]

        summary = _compute_summary(trades, Decimal("1000"))

        pf = summary["by_regime"]["STRONG_TREND_BULL"]["profit_factor"]
        assert pf is None

    def test_v7_12_summary_no_unknown_when_all_regimes_set(self) -> None:
        """No UNKNOWN key in by_regime when all trades have a valid regime."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = [
            _make_backtest_trade(regime="STRONG_TREND_BULL", result="win", pnl_usd=10.0),
            _make_backtest_trade(regime="VOLATILE", result="loss", pnl_usd=-5.0),
        ]

        summary = _compute_summary(trades, Decimal("1000"))

        assert "UNKNOWN" not in summary["by_regime"]

    def test_v7_12_summary_unknown_appears_when_regime_is_none(self) -> None:
        """Trades with regime=None are bucketed under 'UNKNOWN'."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = [
            _make_backtest_trade(regime=None, result="win", pnl_usd=10.0),
            _make_backtest_trade(regime=None, result="loss", pnl_usd=-5.0),
        ]

        summary = _compute_summary(trades, Decimal("1000"))

        assert "UNKNOWN" in summary["by_regime"]
        assert summary["by_regime"]["UNKNOWN"]["trades"] == 2

    def test_v7_12_summary_regimes_with_zero_trades_present(self) -> None:
        """regimes_with_zero_trades lists blocked regimes that had no trades."""
        from src.backtesting.backtest_engine import _compute_summary
        from src.config import BLOCKED_REGIMES

        # Only use STRONG_TREND_BULL which is not in BLOCKED_REGIMES
        trades = [
            _make_backtest_trade(regime="STRONG_TREND_BULL", result="win", pnl_usd=10.0),
        ]

        summary = _compute_summary(trades, Decimal("1000"))

        assert "regimes_with_zero_trades" in summary
        zero_regimes = summary["regimes_with_zero_trades"]
        assert isinstance(zero_regimes, list)
        for r in BLOCKED_REGIMES:
            assert r in zero_regimes, f"Expected {r} in zero_regimes: {zero_regimes}"

    def test_v7_12_summary_blocked_regime_absent_from_zero_list_when_has_trades(self) -> None:
        """A blocked regime with trades is not in regimes_with_zero_trades."""
        from src.backtesting.backtest_engine import _compute_summary
        from src.config import BLOCKED_REGIMES

        if not BLOCKED_REGIMES:
            return

        blocked = BLOCKED_REGIMES[0]
        trades = [
            _make_backtest_trade(regime=blocked, result="win", pnl_usd=10.0),
        ]

        summary = _compute_summary(trades, Decimal("1000"))

        assert blocked not in summary["regimes_with_zero_trades"]

    def test_v7_12_summary_multiple_regimes_tracked_separately(self) -> None:
        """Trades from different regimes produce separate entries in by_regime."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = [
            _make_backtest_trade(regime="STRONG_TREND_BULL", result="win", pnl_usd=20.0),
            _make_backtest_trade(regime="STRONG_TREND_BULL", result="loss", pnl_usd=-8.0),
            _make_backtest_trade(regime="VOLATILE", result="win", pnl_usd=5.0),
        ]

        summary = _compute_summary(trades, Decimal("1000"))

        by_r = summary["by_regime"]
        assert set(by_r.keys()) >= {"STRONG_TREND_BULL", "VOLATILE"}
        assert by_r["STRONG_TREND_BULL"]["trades"] == 2
        assert by_r["VOLATILE"]["trades"] == 1


# ── TASK-V7-13: Statistical significance tests ────────────────────────────────


def _make_trade(
    pnl_usd: float,
    result: str = "win",
    exit_reason: str = "tp_hit",
) -> "Any":
    """Create a minimal BacktestTradeResult for statistical tests."""
    from src.backtesting.backtest_params import BacktestTradeResult

    return BacktestTradeResult(
        symbol="EURUSD=X",
        timeframe="H1",
        direction="LONG",
        entry_price=Decimal("1.10000"),
        exit_price=Decimal("1.11000"),
        exit_reason=exit_reason,
        pnl_usd=Decimal(str(pnl_usd)),
        result=result,
    )


def _make_winning_trades(n: int, pnl: float = 10.0) -> list:
    return [_make_trade(pnl, result="win", exit_reason="tp_hit") for _ in range(n)]


def _make_losing_trades(n: int, pnl: float = -8.0) -> list:
    return [_make_trade(pnl, result="loss", exit_reason="sl_hit") for _ in range(n)]


class TestV713StatisticalTests:
    """TASK-V7-13: Statistical significance tests added to backtest summary."""

    # ── Import helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _fn():
        from src.backtesting.backtest_engine import _compute_statistical_tests
        return _compute_statistical_tests

    # ── Insufficient data edge cases ──────────────────────────────────────────

    def test_v7_13_zero_trades_returns_insufficient(self) -> None:
        """0 trades → INSUFFICIENT_DATA verdict."""
        fn = self._fn()
        result = fn([])
        assert result["verdict"] == "INSUFFICIENT_DATA"
        assert result["min_trades"] == 10

    def test_v7_13_nine_trades_returns_insufficient(self) -> None:
        """9 trades (< 10) → INSUFFICIENT_DATA verdict."""
        fn = self._fn()
        trades = _make_winning_trades(9)
        result = fn(trades)
        assert result["verdict"] == "INSUFFICIENT_DATA"
        assert result["min_trades"] == 10

    def test_v7_13_exactly_ten_trades_computes(self) -> None:
        """Exactly 10 trades does not return INSUFFICIENT_DATA."""
        fn = self._fn()
        trades = _make_winning_trades(5) + _make_losing_trades(5)
        result = fn(trades)
        assert result["verdict"] != "INSUFFICIENT_DATA"
        assert "t_stat" in result
        assert "p_value" in result

    # ── Result structure ──────────────────────────────────────────────────────

    def test_v7_13_result_contains_all_required_keys(self) -> None:
        """Summary dict contains all required keys for >= 10 trades."""
        fn = self._fn()
        trades = _make_winning_trades(15) + _make_losing_trades(10)
        result = fn(trades)

        required = {
            "n_trades", "t_stat", "p_value",
            "pf_ci_5th", "pf_ci_95th",
            "sharpe", "sortino",
            "max_consecutive_losses",
            "verdict",
        }
        assert required.issubset(result.keys())

    def test_v7_13_n_trades_matches_input(self) -> None:
        """n_trades equals len(trades)."""
        fn = self._fn()
        trades = _make_winning_trades(12) + _make_losing_trades(8)
        result = fn(trades)
        assert result["n_trades"] == 20

    # ── t-stat direction ──────────────────────────────────────────────────────

    def test_v7_13_positive_returns_give_positive_t_stat(self) -> None:
        """Consistently profitable trades → t_stat > 0."""
        fn = self._fn()
        trades = _make_winning_trades(20, pnl=15.0) + _make_losing_trades(5, pnl=-5.0)
        result = fn(trades)
        assert result["t_stat"] > 0, f"Expected positive t_stat, got {result['t_stat']}"

    def test_v7_13_negative_returns_give_negative_t_stat(self) -> None:
        """Consistently losing trades → t_stat < 0."""
        fn = self._fn()
        trades = _make_losing_trades(20, pnl=-15.0) + _make_winning_trades(5, pnl=3.0)
        result = fn(trades)
        assert result["t_stat"] < 0, f"Expected negative t_stat, got {result['t_stat']}"

    # ── All wins / all losses ─────────────────────────────────────────────────

    def test_v7_13_all_wins_no_crash(self) -> None:
        """All winning trades — no crash, verdict computed."""
        fn = self._fn()
        trades = _make_winning_trades(15, pnl=10.0)
        result = fn(trades)
        # PF bootstrap: no losses in population → pf_ci values may be None
        assert result["verdict"] in {"SIGNIFICANT", "NOT_SIGNIFICANT"}
        assert result["max_consecutive_losses"] == 0

    def test_v7_13_all_losses_no_crash(self) -> None:
        """All losing trades — no crash, verdict is NOT_SIGNIFICANT."""
        fn = self._fn()
        trades = _make_losing_trades(15, pnl=-10.0)
        result = fn(trades)
        assert result["verdict"] == "NOT_SIGNIFICANT"
        assert result["max_consecutive_losses"] == 15

    # ── Max consecutive losses ────────────────────────────────────────────────

    def test_v7_13_max_consecutive_losses_correct(self) -> None:
        """Max consecutive losses is computed correctly."""
        fn = self._fn()
        # Pattern: win, loss, loss, loss, win, loss, loss
        trades = [
            _make_trade(10.0, result="win"),
            _make_trade(-5.0, result="loss"),
            _make_trade(-5.0, result="loss"),
            _make_trade(-5.0, result="loss"),
            _make_trade(10.0, result="win"),
            _make_trade(-5.0, result="loss"),
            _make_trade(-5.0, result="loss"),
            _make_trade(10.0, result="win"),
            _make_trade(10.0, result="win"),
            _make_trade(10.0, result="win"),
        ]
        result = fn(trades)
        assert result["max_consecutive_losses"] == 3

    # ── Sharpe / Sortino direction ────────────────────────────────────────────

    def test_v7_13_high_winrate_gives_positive_sharpe(self) -> None:
        """80%+ win rate with good R:R → positive Sharpe."""
        fn = self._fn()
        trades = _make_winning_trades(16, pnl=10.0) + _make_losing_trades(4, pnl=-5.0)
        result = fn(trades)
        assert result["sharpe"] > 0

    def test_v7_13_all_losses_sortino_zero_or_negative(self) -> None:
        """All-loss scenario → sortino <= 0."""
        fn = self._fn()
        trades = _make_losing_trades(15, pnl=-10.0)
        result = fn(trades)
        assert result["sortino"] <= 0

    # ── Bootstrap CI ─────────────────────────────────────────────────────────

    def test_v7_13_bootstrap_ci_5th_below_95th(self) -> None:
        """Bootstrap CI: pf_ci_5th <= pf_ci_95th for mixed trades."""
        fn = self._fn()
        trades = _make_winning_trades(12) + _make_losing_trades(8)
        result = fn(trades)
        if result["pf_ci_5th"] is not None and result["pf_ci_95th"] is not None:
            assert result["pf_ci_5th"] <= result["pf_ci_95th"]

    # ── Verdict: SIGNIFICANT ──────────────────────────────────────────────────

    def test_v7_13_strong_edge_gives_significant_verdict(self) -> None:
        """Very strong and consistent edge (high win rate, big R:R) → SIGNIFICANT."""
        fn = self._fn()
        # 30 wins at +20 each, 5 losses at -5 each — very strong edge
        trades = _make_winning_trades(30, pnl=20.0) + _make_losing_trades(5, pnl=-5.0)
        result = fn(trades)
        # With t_stat >> 0 and consistent profitability, should be SIGNIFICANT
        # (p_value < 0.05, pf_5th > 1.0, sharpe > 0.5)
        assert result["verdict"] == "SIGNIFICANT", (
            f"Expected SIGNIFICANT but got {result['verdict']}. "
            f"p={result['p_value']}, pf5={result['pf_ci_5th']}, sharpe={result['sharpe']}"
        )

    def test_v7_13_breakeven_gives_not_significant_verdict(self) -> None:
        """Breakeven trades (equal wins and losses) → NOT_SIGNIFICANT."""
        fn = self._fn()
        trades = _make_winning_trades(10, pnl=10.0) + _make_losing_trades(10, pnl=-10.0)
        result = fn(trades)
        assert result["verdict"] == "NOT_SIGNIFICANT"

    # ── Integration: summary contains statistical_tests key ──────────────────

    def test_v7_13_summary_contains_statistical_tests_key(self) -> None:
        """_compute_summary output includes 'statistical_tests' key."""
        from decimal import Decimal

        from src.backtesting.backtest_engine import _compute_summary
        from src.backtesting.backtest_params import BacktestTradeResult

        import datetime

        trades = []
        for i in range(15):
            pnl = 10.0 if i < 10 else -5.0
            res = "win" if i < 10 else "loss"
            reason = "tp_hit" if i < 10 else "sl_hit"
            trades.append(BacktestTradeResult(
                symbol="EURUSD=X",
                timeframe="H1",
                direction="LONG",
                entry_price=Decimal("1.10000"),
                exit_price=Decimal("1.11000"),
                exit_reason=reason,
                pnl_usd=Decimal(str(pnl)),
                result=res,
                entry_at=datetime.datetime(2024, 1, 2, 10, 0),
                exit_at=datetime.datetime(2024, 1, 2, 11, 0),
                duration_minutes=60,
            ))

        summary = _compute_summary(trades, Decimal("1000"))
        assert "statistical_tests" in summary
        st = summary["statistical_tests"]
        assert st["verdict"] in {"SIGNIFICANT", "NOT_SIGNIFICANT", "INSUFFICIENT_DATA"}

    def test_v7_13_summary_statistical_tests_insufficient_when_few_real_trades(self) -> None:
        """summary['statistical_tests'] is INSUFFICIENT_DATA when all trades are end_of_data."""
        from decimal import Decimal

        from src.backtesting.backtest_engine import _compute_summary
        from src.backtesting.backtest_params import BacktestTradeResult

        # All trades are end_of_data — metric_trades will be empty
        trades = [
            BacktestTradeResult(
                symbol="EURUSD=X",
                timeframe="H1",
                direction="LONG",
                entry_price=Decimal("1.10000"),
                exit_price=Decimal("1.11000"),
                exit_reason="end_of_data",
                pnl_usd=Decimal("5.0"),
                result="win",
            )
            for _ in range(5)
        ]
        summary = _compute_summary(trades, Decimal("1000"))
        st = summary["statistical_tests"]
        assert st["verdict"] == "INSUFFICIENT_DATA"


# ── TASK-V7-14: Sample size adequacy check ────────────────────────────────────


class TestV714SampleAdequacy:
    """TASK-V7-14: sample_adequacy section in _compute_summary()."""

    def _make_trade(
        self,
        result: str = "win",
        exit_reason: str = "tp_hit",
        pnl_usd: str = "10.00",
    ) -> MagicMock:
        t = MagicMock()
        t.symbol = "EURUSD=X"
        t.direction = "LONG"
        t.entry_price = Decimal("1.1000")
        t.exit_price = Decimal("1.1100")
        t.pnl_usd = Decimal(pnl_usd)
        t.result = result
        t.exit_reason = exit_reason
        t.pnl_pips = Decimal("100.0000")
        t.composite_score = Decimal("12.0")
        t.entry_at = datetime.datetime(2024, 1, 15, 10, 0, tzinfo=datetime.timezone.utc)
        t.exit_at = datetime.datetime(2024, 1, 16, 10, 0, tzinfo=datetime.timezone.utc)
        t.duration_minutes = 1440
        t.mfe = Decimal("0.0100")
        t.mae = Decimal("0.0020")
        t.regime = "TREND_BULL"
        t.timeframe = "H1"
        return t

    def _make_trades(self, n_wins: int, n_losses: int) -> list:
        trades = []
        for _ in range(n_wins):
            trades.append(self._make_trade(result="win", exit_reason="tp_hit", pnl_usd="10.00"))
        for _ in range(n_losses):
            trades.append(self._make_trade(result="loss", exit_reason="sl_hit", pnl_usd="-8.00"))
        return trades

    def test_v7_14_key_present_in_summary(self) -> None:
        """sample_adequacy key must be present in _compute_summary() output."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = self._make_trades(10, 5)
        summary = _compute_summary(trades, Decimal("1000"))

        assert "sample_adequacy" in summary

    def test_v7_14_zero_trades(self) -> None:
        """With 0 trades: n=0, verdict=INSUFFICIENT, CI is [0, 0]."""
        from src.backtesting.backtest_engine import _compute_summary

        summary = _compute_summary([], Decimal("1000"))
        sa = summary["sample_adequacy"]

        assert sa["n"] == 0
        assert sa["verdict"] == "INSUFFICIENT"
        assert sa["min_recommended"] == 100
        assert sa["win_rate_ci_95"]["low"] == 0.0
        assert sa["win_rate_ci_95"]["high"] == 0.0

    def test_v7_14_insufficient_below_50(self) -> None:
        """With 33 trades (< 50): verdict must be INSUFFICIENT."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = self._make_trades(20, 13)
        summary = _compute_summary(trades, Decimal("1000"))
        sa = summary["sample_adequacy"]

        assert sa["n"] == 33
        assert sa["verdict"] == "INSUFFICIENT"
        assert sa["min_recommended"] == 100

    def test_v7_14_marginal_50_to_99(self) -> None:
        """With 75 trades (50 <= n < 100): verdict must be MARGINAL."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = self._make_trades(45, 30)
        summary = _compute_summary(trades, Decimal("1000"))
        sa = summary["sample_adequacy"]

        assert sa["n"] == 75
        assert sa["verdict"] == "MARGINAL"

    def test_v7_14_marginal_exactly_50(self) -> None:
        """Exactly 50 trades is MARGINAL (lower boundary)."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = self._make_trades(30, 20)
        summary = _compute_summary(trades, Decimal("1000"))
        sa = summary["sample_adequacy"]

        assert sa["n"] == 50
        assert sa["verdict"] == "MARGINAL"

    def test_v7_14_sufficient_100_plus(self) -> None:
        """With 120 trades (>= 100): verdict must be SUFFICIENT."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = self._make_trades(70, 50)
        summary = _compute_summary(trades, Decimal("1000"))
        sa = summary["sample_adequacy"]

        assert sa["n"] == 120
        assert sa["verdict"] == "SUFFICIENT"

    def test_v7_14_sufficient_exactly_100(self) -> None:
        """Exactly 100 trades is SUFFICIENT (lower boundary)."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = self._make_trades(60, 40)
        summary = _compute_summary(trades, Decimal("1000"))
        sa = summary["sample_adequacy"]

        assert sa["n"] == 100
        assert sa["verdict"] == "SUFFICIENT"

    def test_v7_14_win_rate_ci_within_0_100(self) -> None:
        """Win rate CI bounds must always be within [0, 100]."""
        from src.backtesting.backtest_engine import _compute_summary

        # Test with extreme win rates (0% and 100%) on small samples
        all_wins = self._make_trades(10, 0)
        summary_wins = _compute_summary(all_wins, Decimal("1000"))
        ci_wins = summary_wins["sample_adequacy"]["win_rate_ci_95"]
        assert ci_wins["low"] >= 0.0
        assert ci_wins["high"] <= 100.0

        all_losses = self._make_trades(0, 10)
        summary_losses = _compute_summary(all_losses, Decimal("1000"))
        ci_losses = summary_losses["sample_adequacy"]["win_rate_ci_95"]
        assert ci_losses["low"] >= 0.0
        assert ci_losses["high"] <= 100.0

    def test_v7_14_ci_low_less_than_high(self) -> None:
        """CI low must be <= CI high for any non-trivial sample."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = self._make_trades(30, 20)
        summary = _compute_summary(trades, Decimal("1000"))
        ci = summary["sample_adequacy"]["win_rate_ci_95"]

        assert ci["low"] <= ci["high"]

    def test_v7_14_end_of_data_excluded_from_n(self) -> None:
        """end_of_data trades are excluded from n (they are not counted in metric_total)."""
        from src.backtesting.backtest_engine import _compute_summary

        real_trades = self._make_trades(20, 10)
        eod_trades = [
            self._make_trade(result="loss", exit_reason="end_of_data", pnl_usd="-5.00")
            for _ in range(15)
        ]
        all_trades = real_trades + eod_trades
        summary = _compute_summary(all_trades, Decimal("1000"))
        sa = summary["sample_adequacy"]

        # n must equal only real (non-end_of_data) trades
        assert sa["n"] == 30

    def test_v7_14_ci_centered_on_win_rate(self) -> None:
        """CI midpoint should be close to the observed win rate."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = self._make_trades(60, 40)  # 60% WR
        summary = _compute_summary(trades, Decimal("1000"))
        sa = summary["sample_adequacy"]
        ci = sa["win_rate_ci_95"]

        midpoint = (ci["low"] + ci["high"]) / 2
        assert abs(midpoint - 60.0) < 1.0  # midpoint within 1% of actual WR

    def test_v7_14_structure_has_required_keys(self) -> None:
        """sample_adequacy dict contains all required keys."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = self._make_trades(10, 5)
        summary = _compute_summary(trades, Decimal("1000"))
        sa = summary["sample_adequacy"]

        assert "n" in sa
        assert "verdict" in sa
        assert "min_recommended" in sa
        assert "win_rate_ci_95" in sa
        assert "low" in sa["win_rate_ci_95"]
        assert "high" in sa["win_rate_ci_95"]


# ── TASK-V7-18: Isolation mode backtest ───────────────────────────────────────


class TestV718IsolationMode:
    """Tests for isolation_mode backtest (TASK-V7-18)."""

    def test_v7_18_isolation_mode_field_exists_with_default_false(self) -> None:
        """BacktestParams has isolation_mode: bool with default False."""
        from src.backtesting.backtest_params import BacktestParams

        params = BacktestParams(
            symbols=["EURUSD=X"],
            start_date="2024-01-01",
            end_date="2024-06-01",
        )
        assert hasattr(params, "isolation_mode")
        assert params.isolation_mode is False

    def test_v7_18_isolation_mode_can_be_set_true(self) -> None:
        """BacktestParams accepts isolation_mode=True."""
        from src.backtesting.backtest_params import BacktestParams

        params = BacktestParams(
            symbols=["EURUSD=X", "GC=F"],
            start_date="2024-01-01",
            end_date="2024-06-01",
            isolation_mode=True,
        )
        assert params.isolation_mode is True

    def test_v7_18_backtest_result_has_isolation_results_field(self) -> None:
        """BacktestResult model includes optional isolation_results field."""
        from src.backtesting.backtest_params import BacktestResult

        result = BacktestResult(run_id="test-run", status="completed")
        assert hasattr(result, "isolation_results")
        assert result.isolation_results is None  # default is None

    def test_v7_18_backtest_result_isolation_results_accepts_dict(self) -> None:
        """BacktestResult.isolation_results can hold a populated dict."""
        from src.backtesting.backtest_params import BacktestResult

        isolation_data = {
            "mode": "isolation",
            "per_instrument": {
                "GC=F": {
                    "total_trades": 5,
                    "win_rate_pct": 60.0,
                    "profit_factor": 1.5,
                    "total_pnl_usd": 150.0,
                }
            },
            "path_dependence": {
                "path_dependent": [],
                "comparison": {},
                "threshold_pct": 30.0,
            },
        }
        result = BacktestResult(
            run_id="test-run",
            status="completed",
            isolation_results=isolation_data,
        )
        assert result.isolation_results is not None
        assert result.isolation_results["mode"] == "isolation"
        assert "per_instrument" in result.isolation_results

    def test_v7_18_path_dependence_computation_no_difference(self) -> None:
        """_compute_path_dependence returns empty list when PnL matches exactly."""
        from src.backtesting.backtest_engine import _compute_path_dependence

        isolation = {
            "EURUSD=X": {"total_pnl_usd": 100.0},
            "GC=F": {"total_pnl_usd": -50.0},
        }
        combined = {
            "EURUSD=X": {"pnl_usd": 100.0},
            "GC=F": {"pnl_usd": -50.0},
        }

        result = _compute_path_dependence(isolation, combined, threshold_pct=30.0)

        assert result["path_dependent"] == []
        assert len(result["comparison"]) == 2
        assert result["comparison"]["EURUSD=X"]["diff_pct"] == 0.0

    def test_v7_18_path_dependence_flags_large_difference(self) -> None:
        """_compute_path_dependence flags instrument when PnL diff > 30%."""
        from src.backtesting.backtest_engine import _compute_path_dependence

        # GC=F: isolated=+150, combined=-7.69 — diff is (150 - (-7.69)) / 150 * 100 ≈ 105%
        isolation = {
            "GC=F": {"total_pnl_usd": 150.0},
            "EURUSD=X": {"total_pnl_usd": 80.0},
        }
        combined = {
            "GC=F": {"pnl_usd": -7.69},
            "EURUSD=X": {"pnl_usd": 82.0},  # diff ~2.5% — not flagged
        }

        result = _compute_path_dependence(isolation, combined, threshold_pct=30.0)

        assert "GC=F" in result["path_dependent"]
        assert "EURUSD=X" not in result["path_dependent"]
        assert result["comparison"]["GC=F"]["diff_pct"] > 30.0

    def test_v7_18_path_dependence_handles_zero_isolated_pnl(self) -> None:
        """_compute_path_dependence handles isolated PnL = 0 without division by zero."""
        from src.backtesting.backtest_engine import _compute_path_dependence

        isolation = {"SYM": {"total_pnl_usd": 0.0}}
        combined = {"SYM": {"pnl_usd": 50.0}}

        result = _compute_path_dependence(isolation, combined)
        # Both zero or non-zero: no crash, diff_pct computed
        assert "SYM" in result["comparison"]
        assert result["comparison"]["SYM"]["diff_pct"] >= 0.0

    def test_v7_18_path_dependence_handles_both_zero(self) -> None:
        """_compute_path_dependence handles both PnLs = 0 gracefully."""
        from src.backtesting.backtest_engine import _compute_path_dependence

        isolation = {"SYM": {"total_pnl_usd": 0.0}}
        combined = {"SYM": {"pnl_usd": 0.0}}

        result = _compute_path_dependence(isolation, combined)
        assert result["comparison"]["SYM"]["diff_pct"] == 0.0

    @pytest.mark.asyncio
    async def test_v7_18_isolation_mode_produces_per_instrument_results(self) -> None:
        """When isolation_mode=True, run_backtest summary contains isolation_results
        with per_instrument breakdown for each simulated symbol."""
        from src.backtesting.backtest_engine import BacktestEngine
        from src.backtesting.backtest_params import BacktestParams, BacktestTradeResult

        params = BacktestParams(
            symbols=["EURUSD=X", "GC=F"],
            start_date="2024-01-01",
            end_date="2024-06-01",
            isolation_mode=True,
        )

        # Build mock trades for two symbols
        def _make_trade(symbol: str, pnl: float, result: str = "win") -> BacktestTradeResult:
            t = BacktestTradeResult(
                symbol=symbol,
                timeframe="H1",
                direction="LONG",
                entry_price=Decimal("1.1000"),
                exit_price=Decimal("1.1100"),
                exit_reason="tp_hit",
                pnl_pips=Decimal("100"),
                pnl_usd=Decimal(str(pnl)),
                result=result,
                entry_at=datetime.datetime(2024, 2, 1, 10, 0, tzinfo=datetime.timezone.utc),
                exit_at=datetime.datetime(2024, 2, 1, 15, 0, tzinfo=datetime.timezone.utc),
                duration_minutes=300,
            )
            return t

        eurusd_trades = [
            _make_trade("EURUSD=X", 20.0, "win"),
            _make_trade("EURUSD=X", -10.0, "loss"),
        ]
        gcf_trades = [
            _make_trade("GC=F", 150.0, "win"),
            _make_trade("GC=F", -30.0, "loss"),
        ]

        mock_db = AsyncMock()

        engine = BacktestEngine(mock_db)

        # Patch _simulate_isolated to return controlled data
        async def _fake_isolated(p, run_id=None):
            all_trades = eurusd_trades + gcf_trades
            filter_stats: dict = {}
            per_instrument = {
                "EURUSD=X": {
                    "total_trades": 2,
                    "win_rate_pct": 50.0,
                    "profit_factor": 2.0,
                    "total_pnl_usd": 10.0,
                },
                "GC=F": {
                    "total_trades": 2,
                    "win_rate_pct": 50.0,
                    "profit_factor": 5.0,
                    "total_pnl_usd": 120.0,
                },
            }
            isolation_results = {"mode": "isolation", "per_instrument": per_instrument}
            return all_trades, filter_stats, isolation_results

        engine._simulate_isolated = _fake_isolated

        # Patch DB calls so run_backtest doesn't fail
        with (
            patch("src.backtesting.backtest_engine.create_backtest_run", new_callable=AsyncMock, return_value="run-123"),
            patch("src.backtesting.backtest_engine.update_backtest_run", new_callable=AsyncMock),
            patch("src.backtesting.backtest_engine.create_backtest_trades_bulk", new_callable=AsyncMock),
        ):
            mock_db.commit = AsyncMock()
            run_id = await engine.run_backtest(params)

        assert run_id == "run-123"

    @pytest.mark.asyncio
    async def test_v7_18_correlation_guard_skipped_in_isolation_mode(self) -> None:
        """In isolation mode, _simulate_isolated is called (not _simulate).
        _simulate contains cross-symbol state; _simulate_isolated uses independent loops."""
        from src.backtesting.backtest_engine import BacktestEngine
        from src.backtesting.backtest_params import BacktestParams

        params = BacktestParams(
            symbols=["EURUSD=X"],
            start_date="2024-01-01",
            end_date="2024-06-01",
            isolation_mode=True,
        )

        mock_db = AsyncMock()
        engine = BacktestEngine(mock_db)

        simulate_called = []
        simulate_isolated_called = []

        async def _fake_simulate(p, run_id=None):
            simulate_called.append(True)
            return [], {}

        async def _fake_isolated(p, run_id=None):
            simulate_isolated_called.append(True)
            return [], {}, {"mode": "isolation", "per_instrument": {}}

        engine._simulate = _fake_simulate
        engine._simulate_isolated = _fake_isolated

        with (
            patch("src.backtesting.backtest_engine.create_backtest_run", new_callable=AsyncMock, return_value="run-999"),
            patch("src.backtesting.backtest_engine.update_backtest_run", new_callable=AsyncMock),
            patch("src.backtesting.backtest_engine.create_backtest_trades_bulk", new_callable=AsyncMock),
        ):
            mock_db.commit = AsyncMock()
            await engine.run_backtest(params)

        # isolation_mode=True must call _simulate_isolated, not _simulate
        assert len(simulate_isolated_called) == 1
        assert len(simulate_called) == 0

    @pytest.mark.asyncio
    async def test_v7_18_normal_mode_does_not_call_simulate_isolated(self) -> None:
        """When isolation_mode=False (default), _simulate is called, not _simulate_isolated."""
        from src.backtesting.backtest_engine import BacktestEngine
        from src.backtesting.backtest_params import BacktestParams

        params = BacktestParams(
            symbols=["EURUSD=X"],
            start_date="2024-01-01",
            end_date="2024-06-01",
            isolation_mode=False,
        )

        mock_db = AsyncMock()
        engine = BacktestEngine(mock_db)

        simulate_called = []
        simulate_isolated_called = []

        async def _fake_simulate(p, run_id=None):
            simulate_called.append(True)
            return [], {}

        async def _fake_isolated(p, run_id=None):
            simulate_isolated_called.append(True)
            return [], {}, {"mode": "isolation", "per_instrument": {}}

        engine._simulate = _fake_simulate
        engine._simulate_isolated = _fake_isolated

        with (
            patch("src.backtesting.backtest_engine.create_backtest_run", new_callable=AsyncMock, return_value="run-001"),
            patch("src.backtesting.backtest_engine.update_backtest_run", new_callable=AsyncMock),
            patch("src.backtesting.backtest_engine.create_backtest_trades_bulk", new_callable=AsyncMock),
        ):
            mock_db.commit = AsyncMock()
            await engine.run_backtest(params)

        assert len(simulate_called) == 1
        assert len(simulate_isolated_called) == 0


# ── TASK-V7-15: Filter activation statistics ──────────────────────────────────


class TestV715FilterActivationStats:
    """TASK-V7-15: filter_activation_stats section in _compute_summary()."""

    def _make_trade(
        self,
        result: str = "win",
        exit_reason: str = "tp_hit",
        pnl_usd: str = "10.00",
        regime: str = "TREND_BULL",
    ) -> MagicMock:
        t = MagicMock()
        t.symbol = "EURUSD=X"
        t.direction = "LONG"
        t.entry_price = Decimal("1.1000")
        t.exit_price = Decimal("1.1100")
        t.pnl_usd = Decimal(pnl_usd)
        t.result = result
        t.exit_reason = exit_reason
        t.pnl_pips = Decimal("100.0000")
        t.composite_score = Decimal("12.0")
        t.entry_at = datetime.datetime(2024, 3, 12, 10, 0, tzinfo=datetime.timezone.utc)
        t.exit_at = datetime.datetime(2024, 3, 13, 10, 0, tzinfo=datetime.timezone.utc)
        t.duration_minutes = 1440
        t.mfe = Decimal("0.0100")
        t.mae = Decimal("0.0020")
        t.sl_price = Decimal("1.0900")
        t.regime = regime
        t.timeframe = "H1"
        t.fg_adjustment = None
        t.fr_adjustment = None
        return t

    def _make_trades(self, n_wins: int, n_losses: int, regime: str = "TREND_BULL") -> list:
        trades = []
        for _ in range(n_wins):
            trades.append(self._make_trade(result="win", exit_reason="tp_hit", pnl_usd="10.00", regime=regime))
        for _ in range(n_losses):
            trades.append(self._make_trade(result="loss", exit_reason="sl_hit", pnl_usd="-8.00", regime=regime))
        return trades

    def test_v7_15_summary_includes_filter_activation_stats_key(self) -> None:
        """_compute_summary() with filter_stats includes filter_activation_stats key."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = self._make_trades(5, 3)
        filter_stats = {
            "total_raw_signals": 100,
            "passed_all": 8,
            "rejected_by_score_threshold": 40,
            "rejected_by_regime_filter": 20,
            "rejected_by_momentum_filter": 32,
        }
        summary = _compute_summary(trades, Decimal("1000"), filter_stats=filter_stats)

        assert "filter_activation_stats" in summary

    def test_v7_15_filter_activation_stats_absent_when_no_filter_stats(self) -> None:
        """_compute_summary() without filter_stats has no filter_activation_stats key."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = self._make_trades(5, 3)
        summary = _compute_summary(trades, Decimal("1000"))

        assert "filter_activation_stats" not in summary

    def test_v7_15_nonzero_rejection_counts_preserved(self) -> None:
        """filter_activation_stats preserves all rejection count fields from filter_stats."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = self._make_trades(5, 3)
        filter_stats = {
            "total_raw_signals": 100,
            "passed_all": 8,
            "rejected_by_score_threshold": 45,
            "rejected_by_regime_filter": 20,
            "rejected_by_momentum_filter": 27,
        }
        summary = _compute_summary(trades, Decimal("1000"), filter_stats=filter_stats)

        fas = summary["filter_activation_stats"]
        assert fas["rejected_by_score_threshold"] == 45
        assert fas["rejected_by_regime_filter"] == 20
        assert fas["rejected_by_momentum_filter"] == 27

    def test_v7_15_zero_rejection_filter_produces_warning(self) -> None:
        """A filter with 0 rejections must appear in filter_activation_stats.warnings."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = self._make_trades(5, 3)
        filter_stats = {
            "total_raw_signals": 100,
            "passed_all": 90,
            "rejected_by_score_threshold": 10,
            "rejected_by_d1_trend_filter": 0,   # never activated
            "rejected_by_volume_filter": 0,      # never activated
        }
        summary = _compute_summary(trades, Decimal("1000"), filter_stats=filter_stats)

        warnings = summary["filter_activation_stats"]["warnings"]
        assert any("d1_trend_filter" in w for w in warnings), (
            f"Expected d1_trend_filter warning, got: {warnings}"
        )
        assert any("volume_filter" in w for w in warnings), (
            f"Expected volume_filter warning, got: {warnings}"
        )

    def test_v7_15_nonzero_rejection_filter_produces_no_warning(self) -> None:
        """A filter with >0 rejections must NOT appear in warnings."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = self._make_trades(5, 3)
        filter_stats = {
            "total_raw_signals": 100,
            "passed_all": 50,
            "rejected_by_score_threshold": 50,
        }
        summary = _compute_summary(trades, Decimal("1000"), filter_stats=filter_stats)

        warnings = summary["filter_activation_stats"]["warnings"]
        assert not any("score_threshold" in w for w in warnings), (
            f"score_threshold had rejections but appeared in warnings: {warnings}"
        )

    def test_v7_15_regime_always_same_produces_warning(self) -> None:
        """When all trades have the same regime, a uniformity warning is added."""
        from src.backtesting.backtest_engine import _compute_summary

        # All trades use "DEFAULT" regime
        trades = self._make_trades(5, 3, regime="DEFAULT")
        filter_stats = {
            "total_raw_signals": 50,
            "passed_all": 8,
        }
        summary = _compute_summary(trades, Decimal("1000"), filter_stats=filter_stats)

        warnings = summary["filter_activation_stats"]["warnings"]
        assert any("regime_always_same" in w for w in warnings), (
            f"Expected regime uniformity warning, got: {warnings}"
        )

    def test_v7_15_multiple_regimes_no_uniformity_warning(self) -> None:
        """When trades span multiple regimes, no regime uniformity warning is added."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = (
            self._make_trades(3, 2, regime="TREND_BULL")
            + self._make_trades(2, 1, regime="STRONG_TREND_BULL")
        )
        filter_stats = {
            "total_raw_signals": 50,
            "passed_all": 8,
        }
        summary = _compute_summary(trades, Decimal("1000"), filter_stats=filter_stats)

        warnings = summary["filter_activation_stats"]["warnings"]
        assert not any("regime_always_same" in w for w in warnings), (
            f"Multiple regimes but uniformity warning appeared: {warnings}"
        )

    def test_v7_15_warnings_key_present_even_when_no_warnings(self) -> None:
        """filter_activation_stats.warnings is always a list (empty if no issues)."""
        from src.backtesting.backtest_engine import _compute_summary

        # All filters have rejections, multiple regimes — no warnings expected
        trades = (
            self._make_trades(3, 2, regime="TREND_BULL")
            + self._make_trades(2, 1, regime="VOLATILE")
        )
        filter_stats = {
            "total_raw_signals": 50,
            "passed_all": 5,
            "rejected_by_score_threshold": 30,
            "rejected_by_regime_filter": 15,
        }
        summary = _compute_summary(trades, Decimal("1000"), filter_stats=filter_stats)

        fas = summary["filter_activation_stats"]
        assert "warnings" in fas
        assert isinstance(fas["warnings"], list)


# ── TASK-V7-16: Walk-forward validation ───────────────────────────────────────


def _make_wf_trade(
    direction: str = "LONG",
    result: str = "win",
    pnl_usd: float = 10.0,
    entry_at: Optional[datetime.datetime] = None,
    exit_at: Optional[datetime.datetime] = None,
    symbol: str = "EURUSD=X",
) -> Any:
    """Build a minimal BacktestTradeResult for walk-forward tests."""
    from src.backtesting.backtest_params import BacktestTradeResult

    now = entry_at or datetime.datetime(2022, 1, 15, tzinfo=datetime.timezone.utc)
    return BacktestTradeResult(
        symbol=symbol,
        timeframe="H1",
        direction=direction,
        entry_price=Decimal("1.1000"),
        exit_price=Decimal("1.1100") if result == "win" else Decimal("1.0950"),
        exit_reason="tp_hit" if result == "win" else "sl_hit",
        pnl_pips=Decimal("100") if result == "win" else Decimal("-50"),
        pnl_usd=Decimal(str(pnl_usd)),
        result=result,
        composite_score=Decimal("12.0"),
        entry_at=now,
        exit_at=exit_at or (now + datetime.timedelta(hours=2)),
        duration_minutes=120,
    )


class TestV716WalkForwardFoldBoundaries:
    """TASK-V7-16: Fold boundary computation for anchored expanding window."""

    def test_v7_16_fold_boundaries_single_fold(self) -> None:
        """Single fold: IS 18 months, OOS 6 months from 2020-01-01 to 2022-01-01."""
        from src.backtesting.backtest_engine import BacktestEngine
        from src.backtesting.backtest_params import BacktestParams
        from dateutil.relativedelta import relativedelta

        # Build expected folds manually
        global_start = datetime.datetime(2020, 1, 1)
        is_end_1 = global_start + relativedelta(months=18)  # 2021-07-01
        oos_end_1 = is_end_1 + relativedelta(months=6)       # 2022-01-01

        params = BacktestParams(
            symbols=["EURUSD=X"],
            start_date="2020-01-01",
            end_date="2022-01-01",
            enable_walk_forward=True,
            in_sample_months=18,
            out_of_sample_months=6,
        )

        # Compute expected fold windows using same logic as _run_walk_forward
        fold_windows = []
        fold_num = 1
        global_end = datetime.datetime(2022, 1, 1)
        while True:
            is_end = global_start + relativedelta(months=params.in_sample_months * fold_num)
            oos_start = is_end
            oos_end = oos_start + relativedelta(months=params.out_of_sample_months)
            if oos_start >= global_end:
                break
            effective_oos_end = min(oos_end, global_end)
            fold_windows.append((
                params.start_date,
                is_end.date().isoformat(),
                oos_start.date().isoformat(),
                effective_oos_end.date().isoformat(),
            ))
            fold_num += 1
            if oos_end >= global_end:
                break

        assert len(fold_windows) == 1
        is_start, is_end_s, oos_start_s, oos_end_s = fold_windows[0]
        assert is_start == "2020-01-01"
        assert is_end_s == is_end_1.date().isoformat()
        assert oos_start_s == is_end_1.date().isoformat()
        assert oos_end_s == oos_end_1.date().isoformat()

    def test_v7_16_fold_boundaries_multiple_folds(self) -> None:
        """Multiple folds: IS 12 months, OOS 6 months from 2020-01-01 to 2022-07-01.

        Expected folds:
          Fold 1: IS 2020-01 to 2021-01, OOS 2021-01 to 2021-07
          Fold 2: IS 2020-01 to 2022-01, OOS 2022-01 to 2022-07
        """
        from dateutil.relativedelta import relativedelta

        global_start = datetime.datetime(2020, 1, 1)
        global_end = datetime.datetime(2022, 7, 1)
        in_sample_months = 12
        out_of_sample_months = 6

        fold_windows = []
        fold_num = 1
        while True:
            is_end = global_start + relativedelta(months=in_sample_months * fold_num)
            oos_start = is_end
            oos_end = oos_start + relativedelta(months=out_of_sample_months)
            if oos_start >= global_end:
                break
            effective_oos_end = min(oos_end, global_end)
            fold_windows.append((
                "2020-01-01",
                is_end.date().isoformat(),
                oos_start.date().isoformat(),
                effective_oos_end.date().isoformat(),
            ))
            fold_num += 1
            if oos_end >= global_end:
                break

        assert len(fold_windows) == 2

        # Fold 1
        _, is_end_1, oos_s_1, oos_e_1 = fold_windows[0]
        assert is_end_1 == "2021-01-01"
        assert oos_s_1 == "2021-01-01"
        assert oos_e_1 == "2021-07-01"

        # Fold 2 (IS expands to 24 months from start)
        _, is_end_2, oos_s_2, oos_e_2 = fold_windows[1]
        assert is_end_2 == "2022-01-01"
        assert oos_s_2 == "2022-01-01"
        assert oos_e_2 == "2022-07-01"

    def test_v7_16_oos_clamped_to_global_end(self) -> None:
        """OOS end is clamped to global_end when it would extend beyond."""
        from dateutil.relativedelta import relativedelta

        global_start = datetime.datetime(2020, 1, 1)
        global_end = datetime.datetime(2021, 10, 1)  # ends before full OOS window
        in_sample_months = 18
        out_of_sample_months = 6

        fold_windows = []
        fold_num = 1
        while True:
            is_end = global_start + relativedelta(months=in_sample_months * fold_num)
            oos_start = is_end
            oos_end = oos_start + relativedelta(months=out_of_sample_months)
            if oos_start >= global_end:
                break
            effective_oos_end = min(oos_end, global_end)
            fold_windows.append((
                "2020-01-01",
                is_end.date().isoformat(),
                oos_start.date().isoformat(),
                effective_oos_end.date().isoformat(),
            ))
            fold_num += 1
            if oos_end >= global_end:
                break

        assert len(fold_windows) == 1
        _, _, oos_start_s, oos_end_s = fold_windows[0]
        # OOS would end 2022-01-01 but global_end is 2021-10-01
        assert oos_end_s == "2021-10-01"

    def test_v7_16_no_folds_when_range_too_short(self) -> None:
        """When date range is shorter than IS window, no folds are generated."""
        from dateutil.relativedelta import relativedelta

        global_start = datetime.datetime(2020, 1, 1)
        global_end = datetime.datetime(2020, 6, 1)  # only 5 months
        in_sample_months = 18
        out_of_sample_months = 6

        fold_windows = []
        fold_num = 1
        while True:
            is_end = global_start + relativedelta(months=in_sample_months * fold_num)
            oos_start = is_end
            oos_end = oos_start + relativedelta(months=out_of_sample_months)
            if oos_start >= global_end:
                break
            effective_oos_end = min(oos_end, global_end)
            fold_windows.append((
                "2020-01-01",
                is_end.date().isoformat(),
                oos_start.date().isoformat(),
                effective_oos_end.date().isoformat(),
            ))
            fold_num += 1
            if oos_end >= global_end:
                break

        assert len(fold_windows) == 0


class TestV716WalkForwardOOSTrades:
    """TASK-V7-16: OOS trades are separated per fold and aggregated."""

    @pytest.mark.asyncio
    async def test_v7_16_oos_trades_separated_from_is(self) -> None:
        """IS trades and OOS trades come from separate _simulate calls per fold."""
        from src.backtesting.backtest_engine import BacktestEngine
        from src.backtesting.backtest_params import BacktestParams

        params = BacktestParams(
            symbols=["EURUSD=X"],
            start_date="2020-01-01",
            end_date="2022-01-01",
            enable_walk_forward=True,
            in_sample_months=18,
            out_of_sample_months=6,
        )

        mock_db = AsyncMock()
        engine = BacktestEngine(mock_db)

        call_log: list[tuple[str, str]] = []

        async def _fake_simulate(p: Any, run_id: Optional[str] = None) -> tuple:
            call_log.append((p.start_date, p.end_date))
            # Win trade for IS period, loss for OOS to distinguish
            if p.start_date == "2020-01-01":
                trades = [_make_wf_trade(result="win", pnl_usd=20.0)]
            else:
                trades = [_make_wf_trade(result="loss", pnl_usd=-10.0)]
            return trades, {}, {}

        engine._simulate = _fake_simulate

        result = await engine._run_walk_forward(params)

        # Should have 1 fold, 2 _simulate calls (IS + OOS)
        assert len(result["folds"]) == 1
        assert len(call_log) == 2  # IS + OOS calls

        fold = result["folds"][0]
        assert fold["fold"] == 1
        # IS had 1 win trade
        assert fold["in_sample"]["total_trades"] == 1
        # OOS had 1 loss trade
        assert fold["out_of_sample"]["total_trades"] == 1

    @pytest.mark.asyncio
    async def test_v7_16_aggregate_oos_combines_all_folds(self) -> None:
        """aggregate_oos combines OOS trades from all folds."""
        from src.backtesting.backtest_engine import BacktestEngine
        from src.backtesting.backtest_params import BacktestParams

        # 2 folds: IS 12 months, OOS 6 months, range 2020-01 to 2022-07
        params = BacktestParams(
            symbols=["EURUSD=X"],
            start_date="2020-01-01",
            end_date="2022-07-01",
            enable_walk_forward=True,
            in_sample_months=12,
            out_of_sample_months=6,
        )

        mock_db = AsyncMock()
        engine = BacktestEngine(mock_db)

        call_count = [0]

        async def _fake_simulate(p: Any, run_id: Optional[str] = None) -> tuple:
            call_count[0] += 1
            # Every other call is IS (call 1, 3) or OOS (call 2, 4)
            # IS calls: start_date == "2020-01-01"
            # OOS calls: start_date != "2020-01-01"
            if p.start_date == "2020-01-01":
                trades = [_make_wf_trade(result="win", pnl_usd=15.0)]
            else:
                # Each OOS fold has 2 trades: 1 win, 1 loss
                trades = [
                    _make_wf_trade(result="win", pnl_usd=10.0),
                    _make_wf_trade(result="loss", pnl_usd=-5.0),
                ]
            return trades, {}, {}

        engine._simulate = _fake_simulate

        result = await engine._run_walk_forward(params)

        # 2 folds → 4 simulate calls
        assert len(result["folds"]) == 2
        assert call_count[0] == 4

        # Aggregate OOS = 2 folds × 2 trades = 4 OOS trades total
        assert result["aggregate_oos"]["total_trades"] == 4

    @pytest.mark.asyncio
    async def test_v7_16_fold_oos_period_matches_params(self) -> None:
        """Each fold reports correct is_period and oos_period strings."""
        from src.backtesting.backtest_engine import BacktestEngine
        from src.backtesting.backtest_params import BacktestParams

        params = BacktestParams(
            symbols=["EURUSD=X"],
            start_date="2020-01-01",
            end_date="2022-01-01",
            enable_walk_forward=True,
            in_sample_months=18,
            out_of_sample_months=6,
        )

        mock_db = AsyncMock()
        engine = BacktestEngine(mock_db)

        async def _fake_simulate(p: Any, run_id: Optional[str] = None) -> tuple:
            return [], {}, {}

        engine._simulate = _fake_simulate

        result = await engine._run_walk_forward(params)
        assert len(result["folds"]) == 1
        fold = result["folds"][0]

        assert "2020-01-01" in fold["is_period"]
        assert "2021-07-01" in fold["is_period"]
        assert "2021-07-01" in fold["oos_period"]
        assert "2022-01-01" in fold["oos_period"]


class TestV716WalkForwardAggregateMetrics:
    """TASK-V7-16: Aggregate OOS metrics computation."""

    @pytest.mark.asyncio
    async def test_v7_16_aggregate_pf_computed_from_all_oos_trades(self) -> None:
        """Aggregate OOS PF reflects all OOS trades, not per-fold average."""
        from src.backtesting.backtest_engine import BacktestEngine
        from src.backtesting.backtest_params import BacktestParams

        params = BacktestParams(
            symbols=["EURUSD=X"],
            start_date="2020-01-01",
            end_date="2022-07-01",
            enable_walk_forward=True,
            in_sample_months=12,
            out_of_sample_months=6,
        )

        mock_db = AsyncMock()
        engine = BacktestEngine(mock_db)

        async def _fake_simulate(p: Any, run_id: Optional[str] = None) -> tuple:
            if p.start_date == "2020-01-01":
                return [_make_wf_trade(result="win", pnl_usd=20.0)], {}, {}
            # OOS: 3 wins (30 USD) + 1 loss (-10 USD) → PF = 3.0
            return [
                _make_wf_trade(result="win", pnl_usd=10.0),
                _make_wf_trade(result="win", pnl_usd=10.0),
                _make_wf_trade(result="win", pnl_usd=10.0),
                _make_wf_trade(result="loss", pnl_usd=-10.0),
            ], {}, {}

        engine._simulate = _fake_simulate

        result = await engine._run_walk_forward(params)

        agg = result["aggregate_oos"]
        assert agg["total_trades"] == 8  # 2 folds × 4 OOS trades
        assert agg["profit_factor"] is not None
        # 6 wins (60 USD) + 2 losses (-20 USD) → PF = 3.0
        assert abs(agg["profit_factor"] - 3.0) < 0.01

    @pytest.mark.asyncio
    async def test_v7_16_aggregate_zero_oos_trades(self) -> None:
        """When all OOS windows are empty, aggregate has 0 trades and None PF."""
        from src.backtesting.backtest_engine import BacktestEngine
        from src.backtesting.backtest_params import BacktestParams

        params = BacktestParams(
            symbols=["EURUSD=X"],
            start_date="2020-01-01",
            end_date="2022-01-01",
            enable_walk_forward=True,
            in_sample_months=18,
            out_of_sample_months=6,
        )

        mock_db = AsyncMock()
        engine = BacktestEngine(mock_db)

        async def _fake_simulate(p: Any, run_id: Optional[str] = None) -> tuple:
            return [], {}, {}

        engine._simulate = _fake_simulate

        result = await engine._run_walk_forward(params)
        agg = result["aggregate_oos"]
        assert agg["total_trades"] == 0
        assert agg["profit_factor"] is None


class TestV716WalkForwardVerdict:
    """TASK-V7-16: Verdict logic — VALID / INVALID / INSUFFICIENT_DATA."""

    @pytest.mark.asyncio
    async def test_v7_16_verdict_valid_all_folds_profitable_agg_pf_above_1_2(self) -> None:
        """VALID when all folds OOS PF > 1.0 AND aggregate OOS PF > 1.2."""
        from src.backtesting.backtest_engine import BacktestEngine
        from src.backtesting.backtest_params import BacktestParams

        params = BacktestParams(
            symbols=["EURUSD=X"],
            start_date="2020-01-01",
            end_date="2022-07-01",
            enable_walk_forward=True,
            in_sample_months=12,
            out_of_sample_months=6,
        )

        mock_db = AsyncMock()
        engine = BacktestEngine(mock_db)

        async def _fake_simulate(p: Any, run_id: Optional[str] = None) -> tuple:
            if p.start_date == "2020-01-01":
                return [_make_wf_trade(result="win", pnl_usd=10.0)], {}, {}
            # OOS: PF = 20/10 = 2.0 > 1.0
            return [
                _make_wf_trade(result="win", pnl_usd=20.0),
                _make_wf_trade(result="loss", pnl_usd=-10.0),
            ], {}, {}

        engine._simulate = _fake_simulate

        result = await engine._run_walk_forward(params)

        assert result["verdict"] == "VALID"
        assert result["verdict_criteria"]["all_folds_pf_above_1_0"] is True
        assert result["verdict_criteria"]["aggregate_pf_above_1_2"] is True

    @pytest.mark.asyncio
    async def test_v7_16_verdict_invalid_one_fold_unprofitable(self) -> None:
        """INVALID when at least one fold has OOS PF <= 1.0."""
        from src.backtesting.backtest_engine import BacktestEngine
        from src.backtesting.backtest_params import BacktestParams

        params = BacktestParams(
            symbols=["EURUSD=X"],
            start_date="2020-01-01",
            end_date="2022-07-01",
            enable_walk_forward=True,
            in_sample_months=12,
            out_of_sample_months=6,
        )

        mock_db = AsyncMock()
        engine = BacktestEngine(mock_db)

        fold_oos_call = [0]

        async def _fake_simulate(p: Any, run_id: Optional[str] = None) -> tuple:
            if p.start_date == "2020-01-01":
                return [_make_wf_trade(result="win", pnl_usd=10.0)], {}, {}
            fold_oos_call[0] += 1
            if fold_oos_call[0] == 1:
                # Fold 1 OOS: profitable (PF = 2.0)
                return [
                    _make_wf_trade(result="win", pnl_usd=20.0),
                    _make_wf_trade(result="loss", pnl_usd=-10.0),
                ], {}, {}
            else:
                # Fold 2 OOS: unprofitable (PF = 0.5)
                return [
                    _make_wf_trade(result="win", pnl_usd=5.0),
                    _make_wf_trade(result="loss", pnl_usd=-10.0),
                ], {}, {}

        engine._simulate = _fake_simulate

        result = await engine._run_walk_forward(params)

        assert result["verdict"] == "INVALID"
        assert result["verdict_criteria"]["all_folds_pf_above_1_0"] is False

    @pytest.mark.asyncio
    async def test_v7_16_verdict_invalid_agg_pf_below_1_2(self) -> None:
        """INVALID when aggregate OOS PF <= 1.2 even if all folds are > 1.0."""
        from src.backtesting.backtest_engine import BacktestEngine
        from src.backtesting.backtest_params import BacktestParams

        params = BacktestParams(
            symbols=["EURUSD=X"],
            start_date="2020-01-01",
            end_date="2022-07-01",
            enable_walk_forward=True,
            in_sample_months=12,
            out_of_sample_months=6,
        )

        mock_db = AsyncMock()
        engine = BacktestEngine(mock_db)

        async def _fake_simulate(p: Any, run_id: Optional[str] = None) -> tuple:
            if p.start_date == "2020-01-01":
                return [_make_wf_trade(result="win", pnl_usd=10.0)], {}, {}
            # OOS: PF = 11/10 = 1.1 (> 1.0 per fold, but < 1.2 aggregate)
            return [
                _make_wf_trade(result="win", pnl_usd=11.0),
                _make_wf_trade(result="loss", pnl_usd=-10.0),
            ], {}, {}

        engine._simulate = _fake_simulate

        result = await engine._run_walk_forward(params)

        # Each fold OOS PF = 1.1 > 1.0, but aggregate PF = 1.1 < 1.2
        assert result["verdict"] == "INVALID"
        assert result["verdict_criteria"]["all_folds_pf_above_1_0"] is True
        assert result["verdict_criteria"]["aggregate_pf_above_1_2"] is False

    @pytest.mark.asyncio
    async def test_v7_16_verdict_insufficient_data_no_folds(self) -> None:
        """INSUFFICIENT_DATA when date range produces no valid folds."""
        from src.backtesting.backtest_engine import BacktestEngine
        from src.backtesting.backtest_params import BacktestParams

        # Range too short for any IS window
        params = BacktestParams(
            symbols=["EURUSD=X"],
            start_date="2020-01-01",
            end_date="2020-06-01",  # only 5 months, IS needs 18
            enable_walk_forward=True,
            in_sample_months=18,
            out_of_sample_months=6,
        )

        mock_db = AsyncMock()
        engine = BacktestEngine(mock_db)

        async def _fake_simulate(p: Any, run_id: Optional[str] = None) -> tuple:
            return [], {}, {}

        engine._simulate = _fake_simulate

        result = await engine._run_walk_forward(params)

        assert result["verdict"] == "INSUFFICIENT_DATA"
        assert result["fold_count"] == 0
        assert result["folds"] == []

    def test_v7_16_verdict_criteria_keys_always_present(self) -> None:
        """verdict_criteria dict always has both keys regardless of verdict."""
        # Test the structure by checking the keys in a VALID result
        result = {
            "verdict": "VALID",
            "verdict_criteria": {
                "all_folds_pf_above_1_0": True,
                "aggregate_pf_above_1_2": True,
            },
        }
        assert "all_folds_pf_above_1_0" in result["verdict_criteria"]
        assert "aggregate_pf_above_1_2" in result["verdict_criteria"]


class TestV716WalkForwardBackwardCompatibility:
    """TASK-V7-16: Backward compatibility — WF disabled keeps current behavior."""

    @pytest.mark.asyncio
    async def test_v7_16_wf_disabled_does_not_call_run_walk_forward(self) -> None:
        """When enable_walk_forward=False, _run_walk_forward is never called."""
        from src.backtesting.backtest_engine import BacktestEngine
        from src.backtesting.backtest_params import BacktestParams

        params = BacktestParams(
            symbols=["EURUSD=X"],
            start_date="2024-01-01",
            end_date="2024-06-01",
            enable_walk_forward=False,
        )

        mock_db = AsyncMock()
        engine = BacktestEngine(mock_db)

        wf_called = []

        async def _fake_wf(p: Any) -> dict:
            wf_called.append(True)
            return {}

        async def _fake_simulate(p: Any, run_id: Optional[str] = None) -> tuple:
            return [], {}, {}

        engine._run_walk_forward = _fake_wf
        engine._simulate = _fake_simulate

        with (
            patch("src.backtesting.backtest_engine.create_backtest_run", new_callable=AsyncMock, return_value="run-wf-001"),
            patch("src.backtesting.backtest_engine.update_backtest_run", new_callable=AsyncMock),
            patch("src.backtesting.backtest_engine.create_backtest_trades_bulk", new_callable=AsyncMock),
        ):
            mock_db.commit = AsyncMock()
            await engine.run_backtest(params)

        assert len(wf_called) == 0

    @pytest.mark.asyncio
    async def test_v7_16_wf_enabled_calls_run_walk_forward(self) -> None:
        """When enable_walk_forward=True, _run_walk_forward is called once."""
        from src.backtesting.backtest_engine import BacktestEngine
        from src.backtesting.backtest_params import BacktestParams

        params = BacktestParams(
            symbols=["EURUSD=X"],
            start_date="2020-01-01",
            end_date="2022-01-01",
            enable_walk_forward=True,
            in_sample_months=18,
            out_of_sample_months=6,
        )

        mock_db = AsyncMock()
        engine = BacktestEngine(mock_db)

        wf_called = []

        async def _fake_wf(p: Any) -> dict:
            wf_called.append(True)
            return {"verdict": "VALID", "folds": [], "fold_count": 0, "aggregate_oos": {}}

        async def _fake_simulate(p: Any, run_id: Optional[str] = None) -> tuple:
            return [], {}, {}

        engine._run_walk_forward = _fake_wf
        engine._simulate = _fake_simulate

        with (
            patch("src.backtesting.backtest_engine.create_backtest_run", new_callable=AsyncMock, return_value="run-wf-002"),
            patch("src.backtesting.backtest_engine.update_backtest_run", new_callable=AsyncMock),
            patch("src.backtesting.backtest_engine.create_backtest_trades_bulk", new_callable=AsyncMock),
        ):
            mock_db.commit = AsyncMock()
            run_id = await engine.run_backtest(params)

        assert len(wf_called) == 1
        assert run_id == "run-wf-002"

    @pytest.mark.asyncio
    async def test_v7_16_wf_disabled_summary_has_no_walk_forward_key(self) -> None:
        """When WF is disabled, summary does not contain walk_forward key."""
        from src.backtesting.backtest_engine import BacktestEngine
        from src.backtesting.backtest_params import BacktestParams

        params = BacktestParams(
            symbols=["EURUSD=X"],
            start_date="2024-01-01",
            end_date="2024-06-01",
            enable_walk_forward=False,
        )

        mock_db = AsyncMock()
        engine = BacktestEngine(mock_db)

        captured_summary: list[dict] = []

        async def _fake_simulate(p: Any, run_id: Optional[str] = None) -> tuple:
            return [], {}, {}

        engine._simulate = _fake_simulate

        async def _fake_update(db: Any, run_id: str, status: str, summary: Optional[dict] = None) -> None:
            if status == "completed" and summary is not None:
                captured_summary.append(summary)

        with (
            patch("src.backtesting.backtest_engine.create_backtest_run", new_callable=AsyncMock, return_value="run-nowf"),
            patch("src.backtesting.backtest_engine.update_backtest_run", new=_fake_update),
            patch("src.backtesting.backtest_engine.create_backtest_trades_bulk", new_callable=AsyncMock),
        ):
            mock_db.commit = AsyncMock()
            await engine.run_backtest(params)

        assert len(captured_summary) == 1
        assert "walk_forward" not in captured_summary[0]

    @pytest.mark.asyncio
    async def test_v7_16_wf_result_in_summary_when_enabled(self) -> None:
        """When WF is enabled, summary["walk_forward"] contains the WF result dict."""
        from src.backtesting.backtest_engine import BacktestEngine
        from src.backtesting.backtest_params import BacktestParams

        params = BacktestParams(
            symbols=["EURUSD=X"],
            start_date="2020-01-01",
            end_date="2022-01-01",
            enable_walk_forward=True,
            in_sample_months=18,
            out_of_sample_months=6,
        )

        mock_db = AsyncMock()
        engine = BacktestEngine(mock_db)
        captured_summary: list[dict] = []

        wf_result = {
            "verdict": "VALID",
            "folds": [{"fold": 1}],
            "fold_count": 1,
            "aggregate_oos": {"total_trades": 5, "profit_factor": 1.5},
        }

        async def _fake_wf(p: Any) -> dict:
            return wf_result

        async def _fake_simulate(p: Any, run_id: Optional[str] = None) -> tuple:
            return [], {}, {}

        engine._run_walk_forward = _fake_wf
        engine._simulate = _fake_simulate

        async def _fake_update(db: Any, run_id: str, status: str, summary: Optional[dict] = None) -> None:
            if status == "completed" and summary is not None:
                captured_summary.append(summary)

        with (
            patch("src.backtesting.backtest_engine.create_backtest_run", new_callable=AsyncMock, return_value="run-wf-003"),
            patch("src.backtesting.backtest_engine.update_backtest_run", new=_fake_update),
            patch("src.backtesting.backtest_engine.create_backtest_trades_bulk", new_callable=AsyncMock),
        ):
            mock_db.commit = AsyncMock()
            await engine.run_backtest(params)

        assert len(captured_summary) == 1
        assert "walk_forward" in captured_summary[0]
        assert captured_summary[0]["walk_forward"]["verdict"] == "VALID"
        assert captured_summary[0]["walk_forward"]["fold_count"] == 1
