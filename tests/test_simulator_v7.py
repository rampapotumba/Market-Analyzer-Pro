"""Tests for v7 Phase 1 — broken data wiring fixes.

TASK-V7-03: Central bank rates wired into FAEngine for rate differentials.
"""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.analysis.fa_engine import FAEngine, _PAIR_BANK_MAP, _RATE_DIFF_SCORE_MULTIPLIER
from src.database.models import Base


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_instrument(symbol: str, market: str) -> MagicMock:
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


# ── TASK-V7-03: rate differential calculation ─────────────────────────────────


class TestV7RateDifferential:
    """Tests for _analyze_rate_differential() and its integration into calculate_fa_score()."""

    def test_v7_03_rate_differential_calculation_eurusd(self):
        """FED > ECB → USD stronger → EURUSD is bearish → negative contribution."""
        inst = make_instrument("EURUSD=X", "forex")
        rates = {"FED": 5.25, "ECB": 4.50}
        engine = FAEngine(inst, [], [], central_bank_rates=rates)

        score = engine._analyze_rate_differential()

        # differential = FED - ECB = 0.75; USD is quote → negate → -0.75 * 10 = -7.5
        assert score == pytest.approx(-7.5, abs=1e-9)

    def test_v7_03_rate_differential_calculation_usdjpy(self):
        """FED > BOJ → USD stronger → USDJPY is bullish → positive contribution."""
        inst = make_instrument("USDJPY=X", "forex")
        rates = {"FED": 5.25, "BOJ": 0.10}
        engine = FAEngine(inst, [], [], central_bank_rates=rates)

        score = engine._analyze_rate_differential()

        # differential = FED - BOJ = 5.15; USD is base → positive → 5.15 * 10 = 51.5
        assert score == pytest.approx(51.5, abs=1e-9)

    def test_v7_03_rate_differential_ecb_higher_than_fed(self):
        """ECB > FED → EUR stronger → EURUSD is bullish → positive contribution."""
        inst = make_instrument("EURUSD=X", "forex")
        rates = {"FED": 3.00, "ECB": 4.50}
        engine = FAEngine(inst, [], [], central_bank_rates=rates)

        score = engine._analyze_rate_differential()

        # differential = FED - ECB = -1.5; USD is quote → negate → 1.5 * 10 = +15.0
        assert score == pytest.approx(15.0, abs=1e-9)

    def test_v7_03_fa_score_reflects_rate_differential(self):
        """FA score for EURUSD with FED>ECB should be negative (rate diff component dominates)."""
        inst = make_instrument("EURUSD=X", "forex")
        rates = {"FED": 5.25, "ECB": 4.50}
        # No macro data and no news — only rate differential drives the score
        engine = FAEngine(inst, [], [], central_bank_rates=rates)

        fa_score = engine.calculate_fa_score()

        # base_score=0, rate_diff=-7.5, news_adj=0 → 0*0.6 + (-7.5)*0.3 + 0*0.1 = -2.25
        assert fa_score == pytest.approx(-2.25, abs=1e-6)

    def test_v7_03_fa_score_with_macro_and_rate_diff(self):
        """FA score combines macro base_score (60%) and rate_diff (30%)."""
        inst = make_instrument("EURUSD=X", "forex")
        rates = {"FED": 5.25, "ECB": 4.50}
        # FED rate hike → base_score < 0 for EURUSD
        macro = [make_macro("FEDFUNDS", 5.5, 5.25)]
        engine = FAEngine(inst, macro, [], central_bank_rates=rates)

        fa_score = engine.calculate_fa_score()

        # Both macro and rate diff are bearish for EURUSD → combined score should be negative
        assert fa_score < 0

    def test_v7_03_no_rate_data_graceful(self):
        """No rates passed → rate differential component = 0, FA score still computed."""
        inst = make_instrument("EURUSD=X", "forex")
        macro = [make_macro("FEDFUNDS", 5.5, 5.25)]
        engine = FAEngine(inst, macro, [], central_bank_rates=None)

        # Should not raise; rate_diff_score silently falls back to 0
        fa_score = engine.calculate_fa_score()

        assert isinstance(fa_score, float)
        assert -100.0 <= fa_score <= 100.0

    def test_v7_03_empty_rates_dict_graceful(self):
        """Empty rates dict → rate differential = 0.0 (graceful degradation)."""
        inst = make_instrument("EURUSD=X", "forex")
        engine = FAEngine(inst, [], [], central_bank_rates={})

        score = engine._analyze_rate_differential()

        assert score == 0.0

    def test_v7_03_partial_rates_missing_one_bank(self):
        """If one bank rate is missing, differential returns 0.0 with a warning."""
        inst = make_instrument("EURUSD=X", "forex")
        rates = {"FED": 5.25}  # ECB missing
        engine = FAEngine(inst, [], [], central_bank_rates=rates)

        score = engine._analyze_rate_differential()

        assert score == 0.0

    def test_v7_03_crypto_no_rate_differential(self):
        """Crypto instruments must not use rate differential (not in _PAIR_BANK_MAP)."""
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
        # Extreme differential: 20 pp → 20*10 = 200 (would exceed 100)
        rates = {"FED": 20.0, "BOJ": 0.0}
        engine = FAEngine(inst, [], [], central_bank_rates=rates)

        fa_score = engine.calculate_fa_score()

        assert -100.0 <= fa_score <= 100.0


# ── Local DB fixture (isolated per-test engine) ───────────────────────────────


@pytest_asyncio.fixture
async def v7_db() -> AsyncSession:
    """Isolated SQLite session for v7 CRUD tests.

    Creates a temporary file-based SQLite database per test so that
    each test has a clean, independent schema.
    """
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

        # Two rows for FED: older and newer
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
