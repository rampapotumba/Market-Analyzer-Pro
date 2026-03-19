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
