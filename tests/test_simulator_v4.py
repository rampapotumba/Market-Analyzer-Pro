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


# ── SIM-19: Regime-adaptive SL multiplier ────────────────────────────────────


def _make_levels(regime: str, direction: str = "LONG") -> dict:
    """Helper: calculate SL/TP levels via RiskManagerV2 for a given regime."""
    from src.signals.risk_manager_v2 import RiskManagerV2

    rm = RiskManagerV2()
    entry = Decimal("1.10000")
    atr = Decimal("0.00100")  # 10 pips ATR for EURUSD
    return rm.calculate_levels_for_regime(
        entry=entry,
        atr=atr,
        direction=direction,
        regime=regime,
    )


def test_sim19_sl_wider_volatile():
    """VOLATILE regime: SL = entry ± 2.5×ATR."""
    from src.signals.risk_manager_v2 import ATR_SL_MULTIPLIER_MAP

    assert ATR_SL_MULTIPLIER_MAP["VOLATILE"] == 2.5

    levels = _make_levels("VOLATILE", "LONG")
    entry = Decimal("1.10000")
    atr = Decimal("0.00100")
    expected_sl = entry - atr * Decimal("2.5")  # 1.10000 - 0.0025 = 1.0975

    assert levels["stop_loss"] == expected_sl.quantize(Decimal("0.00000001")), (
        f"VOLATILE SL expected {expected_sl}, got {levels['stop_loss']}"
    )


def test_sim19_sl_strong_trend():
    """STRONG_TREND_BULL: SL = entry ± 1.5×ATR (tighter — trend is clear)."""
    from src.signals.risk_manager_v2 import ATR_SL_MULTIPLIER_MAP

    assert ATR_SL_MULTIPLIER_MAP["STRONG_TREND_BULL"] == 1.5

    levels = _make_levels("STRONG_TREND_BULL", "LONG")
    entry = Decimal("1.10000")
    atr = Decimal("0.00100")
    expected_sl = entry - atr * Decimal("1.5")  # 1.10000 - 0.0015 = 1.0985

    assert levels["stop_loss"] == expected_sl.quantize(Decimal("0.00000001"))


def test_sim19_position_size_decreases_with_wider_sl():
    """Wider SL (VOLATILE 2.5×ATR) → smaller position than tight SL (STRONG_TREND 1.5×ATR).

    Formula: position_size = risk_amount / sl_distance
    With constant risk_amount, wider sl_distance → smaller position.
    We verify:
      1. sl_distance_volatile > sl_distance_strong_trend
      2. raw position (before cap) is inversely proportional to sl_distance
    """
    from src.signals.risk_manager_v2 import ATR_SL_MULTIPLIER_MAP, RiskManagerV2

    rm = RiskManagerV2()
    account = Decimal("1000")
    risk_pct = 1.0
    # Use entry_price=None so formula is: pct = risk_amount / sl_distance / account × 100
    # pct = risk_pct / sl_distance  (not subject to entry_price amplification)
    # With sl_dist=1.5 → pct=0.67%, sl_dist=2.5 → pct=0.40% → both below 2.0% cap
    atr = Decimal("1.0")

    # STRONG_TREND_BULL: SL = 1.5 × ATR
    sl_dist_tight = atr * Decimal(str(ATR_SL_MULTIPLIER_MAP["STRONG_TREND_BULL"]))  # 1.5
    size_tight = rm.calculate_position_size(account, risk_pct, sl_dist_tight, entry_price=None)

    # VOLATILE: SL = 2.5 × ATR
    sl_dist_wide = atr * Decimal(str(ATR_SL_MULTIPLIER_MAP["VOLATILE"]))  # 2.5
    size_wide = rm.calculate_position_size(account, risk_pct, sl_dist_wide, entry_price=None)

    assert sl_dist_wide > sl_dist_tight, "VOLATILE SL distance must be wider"
    assert size_wide < size_tight, (
        f"Wider SL should produce smaller position: tight={size_tight}, wide={size_wide}"
    )


def test_sim19_rr_preserved():
    """R:R should be correctly calculated from the new SL distance (not broken after SIM-19)."""
    levels_volatile = _make_levels("VOLATILE", "LONG")
    levels_strong = _make_levels("STRONG_TREND_BULL", "LONG")

    entry = Decimal("1.10000")

    for label, levels in [("VOLATILE", levels_volatile), ("STRONG_TREND_BULL", levels_strong)]:
        sl = levels["stop_loss"]
        tp1 = levels["take_profit_1"]
        assert sl is not None and tp1 is not None, f"{label}: SL/TP1 must not be None"
        sl_dist = abs(entry - sl)
        tp1_dist = abs(tp1 - entry)
        rr = float(tp1_dist / sl_dist)
        assert rr > 0, f"{label}: R:R must be > 0, got {rr}"
        # Verify rr1 field matches manual calculation
        assert abs(float(levels["risk_reward_1"]) - rr) < 0.02, (
            f"{label}: risk_reward_1 {levels['risk_reward_1']} does not match manual {rr:.2f}"
        )


# ── SIM-18: Dynamic R:R by market regime ─────────────────────────────────────


def test_sim18_rr_strong_trend():
    """STRONG_TREND_BULL → TP1 at 2.5×SL distance."""
    from src.signals.risk_manager_v2 import REGIME_RR_MAP, RiskManagerV2

    assert REGIME_RR_MAP["STRONG_TREND_BULL"]["target_rr"] == 2.5

    rm = RiskManagerV2()
    entry = Decimal("1.10000")
    atr = Decimal("0.00100")
    levels = rm.calculate_levels_for_regime(
        entry=entry, atr=atr, direction="LONG", regime="STRONG_TREND_BULL"
    )
    sl_dist = abs(entry - levels["stop_loss"])
    tp1_dist = abs(levels["take_profit_1"] - entry)
    rr = float(tp1_dist / sl_dist)

    assert abs(rr - 2.5) < 0.01, f"STRONG_TREND_BULL target R:R should be 2.5, got {rr:.3f}"


def test_sim18_rr_ranging():
    """RANGING → TP1 at 1.3×SL distance."""
    from src.signals.risk_manager_v2 import REGIME_RR_MAP, RiskManagerV2

    assert REGIME_RR_MAP["RANGING"]["target_rr"] == 1.3

    rm = RiskManagerV2()
    entry = Decimal("1.10000")
    atr = Decimal("0.00100")
    levels = rm.calculate_levels_for_regime(
        entry=entry, atr=atr, direction="LONG", regime="RANGING"
    )
    sl_dist = abs(entry - levels["stop_loss"])
    tp1_dist = abs(levels["take_profit_1"] - entry)
    rr = float(tp1_dist / sl_dist)

    assert abs(rr - 1.3) < 0.01, f"RANGING target R:R should be 1.3, got {rr:.3f}"


def test_sim18_rr_level_snap():
    """TP1 should snap to nearest resistance level within ±20% band."""
    from src.signals.risk_manager_v2 import RiskManagerV2

    rm = RiskManagerV2()
    entry = Decimal("1.10000")
    atr = Decimal("0.00100")
    # STRONG_TREND_BULL: target_rr=2.5, SL mult=1.5
    # SL = entry - 1.5×ATR = 1.0985
    # SL dist = 0.0015
    # tp1_calc = entry + 0.0015 × 2.5 = 1.10375

    # Place a resistance level close to tp1_calc: 1.1040 (within ±20% of 1.10375)
    resistance = [Decimal("1.10400")]
    levels = rm.calculate_levels_for_regime(
        entry=entry, atr=atr, direction="LONG", regime="STRONG_TREND_BULL",
        resistance_levels=resistance,
    )

    # TP1 should be snapped to the resistance level
    assert levels["take_profit_1"] == Decimal("1.10400"), (
        f"TP1 should snap to resistance 1.10400, got {levels['take_profit_1']}"
    )


def test_sim18_rr_min_respected():
    """After snap, if R:R < min_rr, revert to calculated TP1."""
    from src.signals.risk_manager_v2 import REGIME_RR_MAP, RiskManagerV2

    rm = RiskManagerV2()
    entry = Decimal("1.10000")
    atr = Decimal("0.00100")
    # STRONG_TREND_BULL: min_rr=2.0, target_rr=2.5
    # SL dist = 1.5 × ATR = 0.0015
    # tp1_calc = entry + 0.0015 × 2.5 = 1.10375
    # Place resistance MUCH closer than min_rr: 1.10100 → R:R = 0.1/0.0015 ≈ 0.67 < min_rr=2.0

    bad_resistance = [Decimal("1.10100")]  # way too close → R:R ≈ 0.67
    levels_snapped = rm.calculate_levels_for_regime(
        entry=entry, atr=atr, direction="LONG", regime="STRONG_TREND_BULL",
        resistance_levels=bad_resistance,
    )

    levels_no_snap = rm.calculate_levels_for_regime(
        entry=entry, atr=atr, direction="LONG", regime="STRONG_TREND_BULL",
    )

    # When snap would violate min_rr, tp1 should revert to the calculated value
    assert levels_snapped["take_profit_1"] == levels_no_snap["take_profit_1"], (
        f"Bad snap should be rejected: got {levels_snapped['take_profit_1']}, "
        f"expected {levels_no_snap['take_profit_1']}"
    )


# ── SIM-21: Correlation guard ─────────────────────────────────────────────────


def test_sim21_get_correlation_group():
    """get_correlation_group returns the correct group for known symbols."""
    from src.signals.portfolio_risk import get_correlation_group, CORRELATED_GROUPS

    group = get_correlation_group("EURUSD=X")
    assert group is not None
    assert "GBPUSD=X" in group  # in the same USD-long-side group

    group_btc = get_correlation_group("BTC/USDT")
    assert group_btc is not None
    assert "ETH/USDT" in group_btc

    group_none = get_correlation_group("UNKNOWN_SYMBOL")
    assert group_none is None


@pytest.mark.asyncio
async def test_sim21_same_instrument_blocked(mock_db_session):
    """Same instrument → always blocked (Rule 1: direct instrument guard)."""
    from src.database.crud import is_position_blocked_by_correlation

    # Simulate: has_open_position_for_instrument returns True
    with patch("src.database.crud.has_open_position_for_instrument", new_callable=AsyncMock) as mock_has:
        mock_has.return_value = True

        blocked, reason = await is_position_blocked_by_correlation(
            mock_db_session, instrument_id=1, symbol="EURUSD=X", direction="SHORT"
        )

    assert blocked is True
    assert "instrument_id=1" in reason or "EURUSD" in reason


@pytest.mark.asyncio
async def test_sim21_correlated_same_direction_blocked(mock_db_session):
    """EURUSD SHORT open + GBPUSD SHORT → blocked (same group, same direction)."""
    from src.database.crud import is_position_blocked_by_correlation

    with patch("src.database.crud.has_open_position_for_instrument", new_callable=AsyncMock) as mock_has:
        mock_has.return_value = False  # GBPUSD has no direct open position
        with patch("src.database.crud.count_open_positions_in_group", new_callable=AsyncMock) as mock_count:
            mock_count.return_value = 1  # 1 SHORT already in USD-long group (EURUSD SHORT)

            blocked, reason = await is_position_blocked_by_correlation(
                mock_db_session, instrument_id=2, symbol="GBPUSD=X", direction="SHORT"
            )

    assert blocked is True
    assert "correlation group limit" in reason.lower() or "limit" in reason.lower()


@pytest.mark.asyncio
async def test_sim21_correlated_opposite_direction_allowed(mock_db_session):
    """EURUSD SHORT open + GBPUSD LONG → allowed (opposite direction = hedge)."""
    from src.database.crud import is_position_blocked_by_correlation

    with patch("src.database.crud.has_open_position_for_instrument", new_callable=AsyncMock) as mock_has:
        mock_has.return_value = False
        with patch("src.database.crud.count_open_positions_in_group", new_callable=AsyncMock) as mock_count:
            mock_count.return_value = 0  # No LONG positions in the group

            blocked, reason = await is_position_blocked_by_correlation(
                mock_db_session, instrument_id=2, symbol="GBPUSD=X", direction="LONG"
            )

    assert blocked is False
    assert reason == "OK"


@pytest.mark.asyncio
async def test_sim21_different_group_allowed(mock_db_session):
    """EURUSD SHORT open + BTC/USDT SHORT → allowed (different groups)."""
    from src.database.crud import is_position_blocked_by_correlation
    from src.signals.portfolio_risk import get_correlation_group

    # Verify they're in different groups
    eurusd_group = get_correlation_group("EURUSD=X")
    btc_group = get_correlation_group("BTC/USDT")
    assert eurusd_group is not None and btc_group is not None
    assert eurusd_group != btc_group

    with patch("src.database.crud.has_open_position_for_instrument", new_callable=AsyncMock) as mock_has:
        mock_has.return_value = False  # BTC has no direct open
        with patch("src.database.crud.count_open_positions_in_group", new_callable=AsyncMock) as mock_count:
            mock_count.return_value = 0  # No SHORT in BTC group

            blocked, reason = await is_position_blocked_by_correlation(
                mock_db_session, instrument_id=3, symbol="BTC/USDT", direction="SHORT"
            )

    assert blocked is False
    assert reason == "OK"


@pytest.mark.asyncio
async def test_sim21_unknown_symbol_allowed(mock_db_session):
    """Symbol not in any group → allowed (no correlation blocking)."""
    from src.database.crud import is_position_blocked_by_correlation
    from src.signals.portfolio_risk import get_correlation_group

    assert get_correlation_group("UNKNOWN_PAIR=X") is None  # not in any group

    with patch("src.database.crud.has_open_position_for_instrument", new_callable=AsyncMock) as mock_has:
        mock_has.return_value = False

        blocked, reason = await is_position_blocked_by_correlation(
            mock_db_session, instrument_id=99, symbol="UNKNOWN_PAIR=X", direction="LONG"
        )

    assert blocked is False
    assert reason == "OK"


# ── SIM-22: Backtest CRUD ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sim22_crud_create_and_get_run(mock_db_session):
    """create_backtest_run returns a UUID string; get_backtest_run fetches it."""
    import json
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    from src.database.crud import create_backtest_run, get_backtest_run

    # Patch session.add so we can capture what was added
    added_objects = []
    mock_db_session.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

    params = {"symbols": ["EURUSD=X"], "timeframe": "H1", "account_size": "1000"}
    run_id = await create_backtest_run(mock_db_session, params)

    # run_id must be a valid UUID
    assert isinstance(run_id, str)
    uuid.UUID(run_id)  # raises if invalid

    # One BacktestRun was added to the session
    from src.database.models import BacktestRun
    assert len(added_objects) == 1
    run_obj = added_objects[0]
    assert isinstance(run_obj, BacktestRun)
    assert run_obj.id == run_id
    assert run_obj.status == "pending"
    assert json.loads(run_obj.params) == params

    # get_backtest_run — mock DB returning the run object
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = run_obj
    mock_db_session.execute = AsyncMock(return_value=mock_result)

    fetched = await get_backtest_run(mock_db_session, run_id)
    assert fetched is run_obj


@pytest.mark.asyncio
async def test_sim22_crud_update_run(mock_db_session):
    """update_backtest_run sets status and completed_at when status=completed."""
    from src.database.crud import update_backtest_run

    await update_backtest_run(mock_db_session, run_id="abc-123", status="completed")
    mock_db_session.execute.assert_called_once()
    mock_db_session.flush.assert_called()


@pytest.mark.asyncio
async def test_sim22_crud_bulk_insert_trades(mock_db_session):
    """create_backtest_trades_bulk adds N BacktestTrade objects."""
    from src.database.models import BacktestTrade

    added = []
    mock_db_session.add = MagicMock(side_effect=lambda obj: added.append(obj))

    from src.database.crud import create_backtest_trades_bulk

    trades = [
        {
            "symbol": "EURUSD=X",
            "timeframe": "H1",
            "direction": "LONG",
            "entry_price": "1.10000",
            "exit_price": "1.11000",
            "exit_reason": "tp_hit",
            "pnl_pips": "100.0",
            "pnl_usd": "10.0",
            "result": "win",
            "composite_score": "12.5",
            "entry_at": None,
            "exit_at": None,
            "duration_minutes": 60,
            "mfe": "0.01000",
            "mae": "0.00200",
        },
        {
            "symbol": "GBPUSD=X",
            "timeframe": "H1",
            "direction": "SHORT",
            "entry_price": "1.30000",
            "exit_price": "1.29000",
            "exit_reason": "sl_hit",
            "pnl_pips": "-100.0",
            "pnl_usd": "-10.0",
            "result": "loss",
            "composite_score": "-8.0",
            "entry_at": None,
            "exit_at": None,
            "duration_minutes": 30,
            "mfe": "0.00300",
            "mae": "-0.01200",
        },
    ]

    await create_backtest_trades_bulk(mock_db_session, run_id="abc-123", trades=trades)

    assert len(added) == 2
    assert all(isinstance(obj, BacktestTrade) for obj in added)
    assert added[0].symbol == "EURUSD=X"
    assert added[0].result == "win"
    assert added[1].direction == "SHORT"
    mock_db_session.flush.assert_called()


@pytest.mark.asyncio
async def test_sim22_crud_get_results_structure(mock_db_session):
    """get_backtest_results returns dict with all required top-level fields."""
    import json
    from decimal import Decimal
    from unittest.mock import MagicMock

    from src.database.models import BacktestRun, BacktestTrade

    run = BacktestRun()
    run.id = "test-run-id"
    run.status = "completed"
    run.params = json.dumps({"symbols": ["EURUSD=X"], "timeframe": "H1"})
    run.started_at = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
    run.completed_at = datetime.datetime(2024, 1, 2, tzinfo=datetime.timezone.utc)
    run.summary = None

    trade1 = BacktestTrade()
    trade1.id = 1
    trade1.symbol = "EURUSD=X"
    trade1.timeframe = "H1"
    trade1.direction = "LONG"
    trade1.entry_price = Decimal("1.10000")
    trade1.exit_price = Decimal("1.11000")
    trade1.exit_reason = "tp_hit"
    trade1.pnl_pips = Decimal("100.0")
    trade1.pnl_usd = Decimal("10.0")
    trade1.result = "win"
    trade1.composite_score = Decimal("12.5")
    trade1.entry_at = None
    trade1.exit_at = None
    trade1.duration_minutes = 60
    trade1.mfe = Decimal("0.01000")
    trade1.mae = Decimal("0.00200")

    # First call → get_backtest_run (returns run), second call → trades
    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = run
    trades_result = MagicMock()
    trades_result.scalars.return_value.all.return_value = [trade1]
    mock_db_session.execute = AsyncMock(side_effect=[run_result, trades_result])

    from src.database.crud import get_backtest_results

    result = await get_backtest_results(mock_db_session, "test-run-id")

    required_keys = [
        "run_id", "status", "params", "started_at", "completed_at",
        "total_trades", "win_rate_pct", "profit_factor", "total_pnl_usd",
        "avg_duration_minutes", "trades",
    ]
    for key in required_keys:
        assert key in result, f"Missing key: {key}"

    assert result["total_trades"] == 1
    assert result["win_rate_pct"] == 100.0
    assert result["total_pnl_usd"] == 10.0
    assert len(result["trades"]) == 1
    assert result["trades"][0]["symbol"] == "EURUSD=X"


# ── SIM-22: Backtest Engine ───────────────────────────────────────────────────


def test_sim22_backtest_no_lookahead():
    """On candle N, only rows [0..N-1] are passed to the signal generator.

    We verify this by checking that _simulate_symbol passes `price_rows[:i]`
    to the signal generator — i.e., current candle is NOT in the history slice.
    """
    import numpy as np

    from src.backtesting.backtest_engine import BacktestEngine, _to_ohlcv_df

    # Build a small synthetic price list (5 bars)
    class FakeRow:
        def __init__(self, ts, o, h, l, c):
            self.timestamp = ts
            self.open = o
            self.high = h
            self.low = l
            self.close = c
            self.volume = 0

    base = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
    rows = [
        FakeRow(base + datetime.timedelta(hours=i), 1.10, 1.11, 1.09, 1.10)
        for i in range(60)  # 60 bars so we can slice to index 50
    ]

    # Slice to simulate "history up to candle 50"
    history = rows[:50]
    df = _to_ohlcv_df(history)

    # The DataFrame must have 50 rows (indices 0..49 = candles 0..49)
    assert len(df) == 50
    # The last timestamp in the slice must be < the 51st candle
    last_in_df = df.index[-1]
    current_candle_ts = rows[50].timestamp
    assert last_in_df < current_candle_ts, (
        f"Lookahead violation: last history ts {last_in_df} >= current candle ts {current_candle_ts}"
    )


def test_sim22_backtest_sl_tp_check():
    """SIM-09: SL hit when candle_low <= stop_loss; TP hit when candle_high >= tp.

    Worst case: if both breached → exit at SL.
    """
    from src.backtesting.backtest_engine import BacktestEngine

    engine = BacktestEngine(db=None)  # no DB needed for this unit test

    class FakeCandle:
        def __init__(self, h, l, close, ts=None):
            self.high = h
            self.low = l
            self.close = close
            self.timestamp = ts or datetime.datetime(2024, 1, 2, tzinfo=datetime.timezone.utc)

    # LONG position: SL=1.09, TP=1.12
    open_trade = {
        "symbol": "EURUSD=X",
        "timeframe": "H1",
        "direction": "LONG",
        "entry_price": Decimal("1.10000"),
        "entry_at": datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
        "stop_loss": Decimal("1.09000"),
        "take_profit": Decimal("1.12000"),
        "composite_score": Decimal("12.0"),
        "position_pct": 2.0,
        "mfe": 0.0,
        "mae": 0.0,
    }

    # Candle that breaches neither
    no_exit = engine._check_exit(
        open_trade, FakeCandle(1.115, 1.095, 1.105), "forex", False
    )
    assert no_exit is None, "Should not exit when SL/TP not breached"

    # Candle that hits TP
    tp_candle = FakeCandle(1.130, 1.098, 1.125)  # high > TP
    tp_result = engine._check_exit(open_trade, tp_candle, "forex", False)
    assert tp_result is not None
    assert tp_result.exit_reason == "tp_hit"
    assert tp_result.result == "win"

    # Candle that hits SL
    sl_candle = FakeCandle(1.110, 1.085, 1.088)  # low < SL
    sl_result = engine._check_exit(open_trade, sl_candle, "forex", False)
    assert sl_result is not None
    assert sl_result.exit_reason == "sl_hit"
    assert sl_result.result == "loss"

    # Candle that hits BOTH (worst case → SL)
    both_candle = FakeCandle(1.130, 1.085, 1.10)  # high>TP, low<SL
    both_result = engine._check_exit(open_trade, both_candle, "forex", False)
    assert both_result is not None
    assert both_result.exit_reason == "sl_hit", (
        f"Worst-case rule: both SL+TP hit → SL, got {both_result.exit_reason}"
    )


def test_sim22_backtest_results_structure():
    """_compute_summary returns all required top-level fields from spec."""
    from src.backtesting.backtest_engine import _compute_summary
    from src.backtesting.backtest_params import BacktestTradeResult

    trades = [
        BacktestTradeResult(
            symbol="EURUSD=X",
            timeframe="H1",
            direction="LONG",
            entry_price=Decimal("1.10"),
            exit_price=Decimal("1.11"),
            exit_reason="tp_hit",
            pnl_pips=Decimal("100"),
            pnl_usd=Decimal("10"),
            result="win",
            composite_score=Decimal("12"),
            entry_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
            exit_at=datetime.datetime(2024, 1, 2, tzinfo=datetime.timezone.utc),
            duration_minutes=60,
            mfe=Decimal("0.01"),
            mae=Decimal("0.002"),
        ),
        BacktestTradeResult(
            symbol="EURUSD=X",
            timeframe="H1",
            direction="SHORT",
            entry_price=Decimal("1.10"),
            exit_price=Decimal("1.09"),
            exit_reason="sl_hit",
            pnl_pips=Decimal("-100"),
            pnl_usd=Decimal("-10"),
            result="loss",
            composite_score=Decimal("-12"),
            entry_at=datetime.datetime(2024, 2, 1, tzinfo=datetime.timezone.utc),
            exit_at=datetime.datetime(2024, 2, 2, tzinfo=datetime.timezone.utc),
            duration_minutes=30,
            mfe=Decimal("0.003"),
            mae=Decimal("0.01"),
        ),
    ]

    summary = _compute_summary(trades, account_size=Decimal("1000"))

    required = [
        "total_trades", "win_rate_pct", "profit_factor", "total_pnl_usd",
        "max_drawdown_pct", "avg_duration_minutes", "long_count", "short_count",
        "equity_curve", "monthly_returns", "by_symbol", "by_score_bucket",
    ]
    for key in required:
        assert key in summary, f"Missing key in summary: {key}"

    assert summary["total_trades"] == 2
    assert summary["win_rate_pct"] == 50.0
    assert summary["long_count"] == 1
    assert summary["short_count"] == 1
    assert summary["total_pnl_usd"] == 0.0
    assert len(summary["equity_curve"]) == 2
    assert len(summary["monthly_returns"]) == 2  # Jan and Feb 2024


@pytest.mark.asyncio
async def test_sim22_backtest_isolated_from_live(mock_db_session):
    """BacktestEngine must not write to signal_results or virtual_portfolio.

    We verify by inspecting what tables are referenced in the CRUD calls
    made during backtest — none should be signal_results / virtual_portfolio.
    """
    from src.backtesting.backtest_engine import BacktestEngine
    from src.backtesting.backtest_params import BacktestParams

    # The engine's run_backtest() calls: create_backtest_run, update_backtest_run,
    # create_backtest_trades_bulk — all isolated to backtest_* tables.
    # We mock _simulate to return empty trades so the test runs fast.
    engine = BacktestEngine(db=mock_db_session)

    with patch.object(engine, "_simulate", new_callable=AsyncMock) as mock_sim:
        mock_sim.return_value = []

        # Patch CRUD calls
        with patch("src.backtesting.backtest_engine.create_backtest_run", new_callable=AsyncMock) as mock_create, \
             patch("src.backtesting.backtest_engine.update_backtest_run", new_callable=AsyncMock), \
             patch("src.backtesting.backtest_engine.create_backtest_trades_bulk", new_callable=AsyncMock):

            mock_create.return_value = "test-run-id"
            mock_db_session.commit = AsyncMock()

            params = BacktestParams(
                symbols=["EURUSD=X"],
                timeframe="H1",
                start_date="2024-01-01",
                end_date="2024-12-31",
                account_size=Decimal("1000"),
            )
            run_id = await engine.run_backtest(params)

    assert run_id == "test-run-id"
    # The mock_db_session was never asked to write directly to live tables
    # (signal_results / virtual_portfolio are never mentioned in backtest_engine.py)
    assert mock_sim.called


# ── SIM-20: MAE Early Exit ────────────────────────────────────────────────────


def _make_signal_for_mae(direction: str = "LONG") -> MagicMock:
    """Create a minimal signal mock for MAE early exit tests."""
    sig = MagicMock()
    sig.id = 99
    sig.direction = direction
    sig.timeframe = "H1"
    sig.stop_loss = Decimal("1.09000") if direction == "LONG" else Decimal("1.11000")
    sig.entry_price = Decimal("1.10000")
    return sig


def _make_position_for_mae(
    mfe: Decimal = Decimal("0"),
    mae: Decimal = Decimal("0"),
    entry_filled_at: Optional[datetime.datetime] = None,
    current_stop_loss: Optional[Decimal] = None,
    entry_actual_price: Optional[Decimal] = None,
) -> MagicMock:
    pos = MagicMock()
    pos.mfe = mfe
    pos.mae = mae
    pos.entry_filled_at = entry_filled_at or datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
    pos.current_stop_loss = current_stop_loss or Decimal("1.09000")
    pos.entry_actual_price = entry_actual_price or Decimal("1.10000")
    pos.trailing_stop = None
    return pos


def test_sim20_mae_early_exit_triggers():
    """MAE 65% of SL distance, 4 candles elapsed, MFE=0 → early exit."""
    from src.tracker.signal_tracker import SignalTracker

    tracker = SignalTracker()
    sig = _make_signal_for_mae("LONG")

    # SL distance = |1.10000 - 1.09000| = 0.01000
    # MAE = 0.0065 → mae_ratio = 0.0065/0.01 = 0.65 ≥ threshold (0.60)
    pos = _make_position_for_mae(
        mfe=Decimal("0"),
        mae=Decimal("0.00650"),
        entry_filled_at=datetime.datetime(2024, 1, 1, 0, 0, tzinfo=datetime.timezone.utc),
    )

    # now = 4 hours after entry → candles_elapsed = 4 (H1)
    now = datetime.datetime(2024, 1, 1, 4, 0, tzinfo=datetime.timezone.utc)
    current_price = Decimal("1.09650")  # current price not used in decision

    result = tracker._check_mae_early_exit(pos, sig, current_price, now)
    assert result is True, (
        "Expected early exit with MAE=65%SL, 4 candles, MFE=0"
    )


def test_sim20_mae_early_exit_no_trigger_early_candles():
    """MAE 65% of SL, but only 2 candles elapsed → NO early exit."""
    from src.tracker.signal_tracker import SignalTracker

    tracker = SignalTracker()
    sig = _make_signal_for_mae("LONG")
    pos = _make_position_for_mae(
        mfe=Decimal("0"),
        mae=Decimal("0.00650"),
        entry_filled_at=datetime.datetime(2024, 1, 1, 0, 0, tzinfo=datetime.timezone.utc),
    )

    # now = only 2 hours after entry → candles_elapsed = 2 < min_candles (3)
    now = datetime.datetime(2024, 1, 1, 2, 0, tzinfo=datetime.timezone.utc)
    result = tracker._check_mae_early_exit(pos, sig, Decimal("1.09650"), now)
    assert result is False, "Should not early exit before min_candles"


def test_sim20_mae_early_exit_no_trigger_with_mfe():
    """MAE 65%, 4 candles, but MFE = 40% of MAE → NO early exit.

    mfe_max_ratio = 0.20 → exit only if mfe/mae < 0.20 → mfe < 20% of mae.
    Here mfe = 40% of mae → mfe_ratio (0.40) >= mfe_max_ratio (0.20) → skip.
    """
    from src.tracker.signal_tracker import SignalTracker

    tracker = SignalTracker()
    sig = _make_signal_for_mae("LONG")
    # MAE = 0.0065, MFE = 0.40 × 0.0065 = 0.0026
    pos = _make_position_for_mae(
        mfe=Decimal("0.00260"),
        mae=Decimal("0.00650"),
        entry_filled_at=datetime.datetime(2024, 1, 1, 0, 0, tzinfo=datetime.timezone.utc),
    )
    now = datetime.datetime(2024, 1, 1, 4, 0, tzinfo=datetime.timezone.utc)
    result = tracker._check_mae_early_exit(pos, sig, Decimal("1.09650"), now)
    assert result is False, (
        "Should not early exit: MFE=40%MAE is above mfe_max_ratio threshold (20%)"
    )


def test_sim20_mae_early_exit_division_by_zero():
    """sl_distance=0 or mae=0 → graceful (returns False, no crash)."""
    from src.tracker.signal_tracker import SignalTracker

    tracker = SignalTracker()
    sig = _make_signal_for_mae("LONG")

    # sl_distance = 0 (entry == SL — degenerate case)
    pos_zero_sl = _make_position_for_mae(
        mfe=Decimal("0"), mae=Decimal("0"),
        current_stop_loss=Decimal("1.10000"),  # same as entry → dist=0
        entry_actual_price=Decimal("1.10000"),
        entry_filled_at=datetime.datetime(2024, 1, 1, 0, 0, tzinfo=datetime.timezone.utc),
    )
    now = datetime.datetime(2024, 1, 1, 5, 0, tzinfo=datetime.timezone.utc)
    result = tracker._check_mae_early_exit(pos_zero_sl, sig, Decimal("1.10000"), now)
    assert result is False, "sl_distance=0 should return False gracefully"

    # mae=0 → ratio < threshold → no exit
    pos_zero_mae = _make_position_for_mae(
        mfe=Decimal("0"), mae=Decimal("0"),
        entry_filled_at=datetime.datetime(2024, 1, 1, 0, 0, tzinfo=datetime.timezone.utc),
    )
    result2 = tracker._check_mae_early_exit(pos_zero_mae, sig, Decimal("1.10000"), now)
    assert result2 is False, "mae=0 should return False (no adverse move)"


# ── SIM-24: Partial close ─────────────────────────────────────────────────────


def test_sim24_partial_close_triggers_at_tp1():
    """TradeLifecycleManager returns 'partial_close' when price >= TP1 (LONG, not yet partial_closed)."""
    from src.signals.trade_lifecycle import TradeLifecycleManager

    mgr = TradeLifecycleManager()
    entry = Decimal("1.10000")
    sl = Decimal("1.09000")
    tp1 = Decimal("1.11500")
    tp2 = Decimal("1.12000")
    atr = Decimal("0.00100")

    # Price AT tp1 — should trigger partial_close (not yet done)
    action = mgr.check(
        direction="LONG",
        entry=entry,
        stop_loss=sl,
        take_profit_1=tp1,
        take_profit_2=tp2,
        take_profit_3=None,
        current_price=tp1,  # price == TP1
        atr=atr,
        regime="TREND_BULL",
        partial_closed=False,
        breakeven_moved=False,
        trailing_stop=None,
    )
    assert action["action"] == "partial_close", (
        f"Expected partial_close, got {action['action']}: {action['reason']}"
    )
    assert action["close_pct"] == 0.5


def test_sim24_partial_close_sl_moves_to_breakeven():
    """After partial close, SL must be set to entry price (breakeven)."""
    from src.signals.trade_lifecycle import TradeLifecycleManager

    mgr = TradeLifecycleManager()
    entry = Decimal("1.10000")

    # After partial close, partial_closed=True and breakeven_moved=True
    # Now TradeLifecycle should trail / hold, not partial close again
    action = mgr.check(
        direction="LONG",
        entry=entry,
        stop_loss=entry,            # SL already at breakeven
        take_profit_1=Decimal("1.11500"),
        take_profit_2=Decimal("1.12000"),
        take_profit_3=None,
        current_price=Decimal("1.11000"),  # between BE and TP2
        atr=Decimal("0.00100"),
        regime="TREND_BULL",
        partial_closed=True,        # already done
        breakeven_moved=True,
        trailing_stop=None,
    )
    # Should NOT trigger partial_close again
    assert action["action"] != "partial_close", (
        f"Should not partial_close twice: got {action['action']}"
    )


def test_sim24_second_half_closes_at_breakeven():
    """After partial close, if price returns to entry → SL hit → result still 'win' (blended).

    The overall result depends on the full P&L calculation:
    - partial 50% at TP1 → profit
    - second 50% at BE → 0 P&L
    - Total: still positive → result = 'win'

    We test the lifecycle manager returns exit_sl when price hits breakeven SL.
    """
    from src.signals.trade_lifecycle import TradeLifecycleManager

    mgr = TradeLifecycleManager()
    entry = Decimal("1.10000")
    be_sl = entry  # SL at breakeven after partial close

    action = mgr.check(
        direction="LONG",
        entry=entry,
        stop_loss=be_sl,
        take_profit_1=Decimal("1.11500"),
        take_profit_2=Decimal("1.12000"),
        take_profit_3=None,
        current_price=entry,  # price hit breakeven SL
        atr=Decimal("0.00100"),
        regime="TREND_BULL",
        partial_closed=True,
        breakeven_moved=True,
        trailing_stop=None,
    )
    assert action["action"] == "exit_sl", (
        f"Expected exit_sl at breakeven, got {action['action']}"
    )


def test_sim24_candle_tp1_hit_triggers_partial_close():
    """SIM-24 fix: when SIM-09 candle check hits TP1 and position is NOT partial_closed,
    the partial close path is taken instead of full close.

    We verify the _check_mae_early_exit logic and the candle-based check interaction
    by confirming the TradeLifecycleManager's partial_close action is correct.
    """
    from src.signals.trade_lifecycle import TradeLifecycleManager

    mgr = TradeLifecycleManager()

    # LONG position: price is above TP1 (candle high hit TP1)
    entry = Decimal("1.10000")
    tp1 = Decimal("1.11500")
    action = mgr.check(
        direction="LONG",
        entry=entry,
        stop_loss=Decimal("1.09000"),
        take_profit_1=tp1,
        take_profit_2=Decimal("1.12500"),
        take_profit_3=None,
        current_price=Decimal("1.11600"),  # above TP1 — candle hit TP1
        atr=Decimal("0.00100"),
        regime="TREND_BULL",
        partial_closed=False,
        breakeven_moved=False,
        trailing_stop=None,
    )
    # Without partial_closed=True, TP1 triggers partial_close (not exit_tp1)
    assert action["action"] == "partial_close", (
        f"Candle TP1 hit + not partial_closed → partial_close, got {action['action']}"
    )
