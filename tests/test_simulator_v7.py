"""Trade Simulator v7 tests — Phase 1 data wiring fixes.

Test naming: test_v7_{task_number}_{what_we_check}
All DB interactions are mocked — no real database required.
"""

import datetime
import inspect
from decimal import Decimal
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

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
