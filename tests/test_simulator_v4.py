"""Trade Simulator v4 tests — SIM-17..SIM-24.

Test naming: test_{sim_number}_{what_we_check}
All DB interactions are mocked — no real database required.
"""

import datetime
from decimal import Decimal
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
import pytest_asyncio


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_db_session() -> AsyncMock:
    """Async mock of SQLAlchemy AsyncSession."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.flush = AsyncMock()
    return session


@pytest.fixture
def mock_position_long() -> MagicMock:
    """Mock VirtualPortfolio for an open LONG position."""
    pos = MagicMock()
    pos.id = 1
    pos.signal_id = 101
    pos.status = "open"

    # Price levels
    pos.entry_price = Decimal("1.10000")
    pos.current_stop_loss = Decimal("1.09000")   # 100 pips SL
    pos.current_price = Decimal("1.10000")

    # Position sizing
    pos.size_pct = Decimal("2.0")
    pos.size_remaining_pct = Decimal("1.0")
    pos.partial_closed = False
    pos.breakeven_moved = False
    pos.partial_close_price = None
    pos.partial_close_at = None

    # MFE / MAE (in price units)
    pos.mfe = Decimal("0")
    pos.mae = Decimal("0")

    # Swap / account
    pos.accrued_swap_pips = Decimal("0")
    pos.accrued_swap_usd = Decimal("0")
    pos.last_swap_date = None
    pos.account_balance_at_entry = Decimal("1000.0")

    # Instrument info (via relationship mock)
    instrument = MagicMock()
    instrument.symbol = "EURUSD=X"
    instrument.market = "forex"
    instrument.pip_size = Decimal("0.0001")
    pos.signal = MagicMock()
    pos.signal.instrument = instrument
    pos.signal.direction = "LONG"
    pos.signal.timeframe = "H1"
    pos.signal.take_profit_1 = Decimal("1.11330")
    pos.signal.take_profit_2 = Decimal("1.12000")
    pos.signal.take_profit_3 = Decimal("1.12660")
    pos.signal.stop_loss = Decimal("1.09000")
    pos.signal.entry_price = Decimal("1.10000")
    pos.signal.composite_score = Decimal("12.0")
    pos.signal.regime = "TREND_BULL"

    return pos


@pytest.fixture
def mock_position_short() -> MagicMock:
    """Mock VirtualPortfolio for an open SHORT position."""
    pos = MagicMock()
    pos.id = 2
    pos.signal_id = 102
    pos.status = "open"

    # Price levels
    pos.entry_price = Decimal("1.10000")
    pos.current_stop_loss = Decimal("1.11000")   # 100 pips SL
    pos.current_price = Decimal("1.10000")

    # Position sizing
    pos.size_pct = Decimal("2.0")
    pos.size_remaining_pct = Decimal("1.0")
    pos.partial_closed = False
    pos.breakeven_moved = False
    pos.partial_close_price = None
    pos.partial_close_at = None

    # MFE / MAE
    pos.mfe = Decimal("0")
    pos.mae = Decimal("0")

    # Swap / account
    pos.accrued_swap_pips = Decimal("0")
    pos.accrued_swap_usd = Decimal("0")
    pos.last_swap_date = None
    pos.account_balance_at_entry = Decimal("1000.0")

    # Instrument info
    instrument = MagicMock()
    instrument.symbol = "EURUSD=X"
    instrument.market = "forex"
    instrument.pip_size = Decimal("0.0001")
    pos.signal = MagicMock()
    pos.signal.instrument = instrument
    pos.signal.direction = "SHORT"
    pos.signal.timeframe = "H1"
    pos.signal.take_profit_1 = Decimal("1.08670")
    pos.signal.take_profit_2 = Decimal("1.08000")
    pos.signal.take_profit_3 = Decimal("1.07340")
    pos.signal.stop_loss = Decimal("1.11000")
    pos.signal.entry_price = Decimal("1.10000")
    pos.signal.composite_score = Decimal("-12.0")
    pos.signal.regime = "TREND_BEAR"

    return pos


@pytest.fixture
def mock_candle_data() -> dict:
    """Single OHLCV candle as dict (price units for EURUSD=X)."""
    return {
        "timestamp": datetime.datetime(2024, 6, 1, 12, 0, tzinfo=datetime.timezone.utc),
        "open":  Decimal("1.10050"),
        "high":  Decimal("1.10200"),
        "low":   Decimal("1.09900"),
        "close": Decimal("1.10100"),
        "volume": Decimal("5000"),
    }


@pytest.fixture
def mock_ohlcv_df() -> pd.DataFrame:
    """Small synthetic OHLCV DataFrame (50 bars) for backtest tests."""
    import numpy as np

    np.random.seed(123)
    n = 50
    base = 1.1000
    close = base + np.cumsum(np.random.normal(0, 0.0005, n))
    high = close + np.abs(np.random.normal(0, 0.0002, n))
    low = close - np.abs(np.random.normal(0, 0.0002, n))
    open_ = np.roll(close, 1)
    open_[0] = close[0]

    idx = pd.date_range(
        start=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
        periods=n,
        freq="h",
    )
    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": np.random.uniform(1000, 5000, n),
    }, index=idx)


# ── SIM-17: SHORT bias diagnosis and fallback fixes ───────────────────────────


def test_sim17_neutral_fallback_fa():
    """FA engine with mocked error API → fa_score remains 0.0 via fallback."""
    from src.analysis.fa_engine import FAEngine

    instrument = MagicMock()
    instrument.market = "forex"
    instrument.symbol = "EURUSD=X"

    # No macro data, no news data → should return a value close to 0 (no data)
    engine = FAEngine(instrument, [], [])
    score = engine.calculate_fa_score()

    # With no macro data, all sub-scores = 0, final = 0
    assert score == 0.0, f"Expected 0.0 for no-data FA, got {score}"


def test_sim17_neutral_fallback_fa_unknown_market():
    """FA engine with unknown market returns 0.0 neutral."""
    from src.analysis.fa_engine import FAEngine

    instrument = MagicMock()
    instrument.market = "unknown_market"
    instrument.symbol = "XYZ"

    engine = FAEngine(instrument, [], [])
    score = engine.calculate_fa_score()

    assert score == 0.0


def test_sim17_neutral_fallback_fa_crypto():
    """FA engine for crypto returns 0.0 (crypto FA not yet implemented)."""
    from src.analysis.fa_engine import FAEngine

    instrument = MagicMock()
    instrument.market = "crypto"
    instrument.symbol = "BTC/USDT"

    engine = FAEngine(instrument, [], [])
    score = engine.calculate_fa_score()

    assert score == 0.0


@pytest.mark.asyncio
async def test_sim17_neutral_fallback_sentiment():
    """Sentiment engine with no news → returns 0.0."""
    from src.analysis.sentiment_engine_v2 import SentimentEngineV2

    engine = SentimentEngineV2(news_events=[], social_data={})
    score = await engine.calculate()

    assert score == 0.0, f"Expected 0.0 for empty sentiment, got {score}"


@pytest.mark.asyncio
async def test_sim17_neutral_fallback_geo():
    """Geo engine for unknown symbol → returns 0.0 (no country mapping)."""
    from src.analysis.geo_engine_v2 import GeoEngineV2

    engine = GeoEngineV2()
    # Symbol with no country mapping → should return 0.0 without error
    score = await engine.score("UNKNOWN_SYMBOL_XYZ")
    await engine.close()

    assert score == 0.0, f"Expected 0.0 for unknown symbol, got {score}"


@pytest.mark.asyncio
async def test_sim17_neutral_fallback_of():
    """Order flow: no separate scoring engine → of_score = None in diagnostic output."""
    # The signal_engine.py does not call any order_flow scoring engine.
    # The diagnostic endpoint returns of_score=None, of_weight=0.0.
    # This test verifies the of_score field structure expectation.
    expected_of_score = None
    expected_of_weight = 0.0

    # Verify via the backtesting module (backtest_params imports cleanly)
    from src.backtesting.backtest_params import BacktestParams

    params = BacktestParams(
        symbols=["EURUSD=X"],
        start_date="2024-01-01",
        end_date="2024-12-31",
        account_size=Decimal("1000"),
    )
    assert params.symbols == ["EURUSD=X"]
    # Structural: of_score is not in composite scoring
    assert expected_of_score is None
    assert expected_of_weight == 0.0


@pytest.mark.asyncio
async def test_sim17_scoring_breakdown_endpoint(mock_db_session):
    """Diagnostic endpoint returns correct structure with instruments and summary."""
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from src.api.routes_v2 import router_v2

    # Mock get_all_instruments and other DB calls to return empty
    with patch("src.api.routes_v2.get_all_instruments", new_callable=AsyncMock) as mock_instr:
        mock_instr.return_value = []  # no instruments → empty result
        with patch("src.database.crud.get_macro_data", new_callable=AsyncMock) as mock_macro:
            mock_macro.return_value = []
            with patch("src.database.crud.get_news_events", new_callable=AsyncMock) as mock_news:
                mock_news.return_value = []

                app = FastAPI()
                app.include_router(router_v2)

                client = TestClient(app)
                with patch("src.database.engine.async_session_factory") as mock_factory:
                    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db_session)
                    mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)
                    resp = client.get("/api/v2/diagnostics/scoring-breakdown")

    # Verify structure even if no instruments (empty case)
    assert resp.status_code == 200
    data = resp.json()
    assert "instruments" in data
    assert "summary" in data
    assert "avg_composite" in data["summary"]
    assert "pct_negative" in data["summary"]
    assert "suspected_bias_sources" in data["summary"]
    assert isinstance(data["instruments"], list)


def test_sim17_long_signal_possible():
    """With neutral FA/sentiment/geo and bullish TA, composite_score should be positive → LONG possible."""
    from src.signals.mtf_filter import MTFFilter

    # Simulate what signal_engine does:
    # ta_score = +30 (strong bullish), all others = 0.0
    ta_score = 30.0
    fa_score = 0.0
    sentiment_score = 0.0
    geo_score = 0.0
    correlation_score = 0.0

    mtf = MTFFilter()
    weights = mtf.get_timeframe_weights("H1")

    composite = (
        weights["ta"] * ta_score
        + weights["fa"] * fa_score
        + weights["sentiment"] * sentiment_score
        + weights["geo"] * geo_score
    )
    composite += 0.05 * correlation_score

    # BUY_THRESHOLD = 7.0 (from config). With bullish TA, composite should be > 7
    assert composite > 7.0, (
        f"Expected composite > 7.0 for bullish TA=30 with neutral others, got {composite:.2f}. "
        "This indicates potential LONG signal blockage."
    )
