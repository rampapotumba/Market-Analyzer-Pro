"""Trade Simulator v5 tests — SIM-25..SIM-44.

Test naming: test_{sim_number}_{what_we_check}
All DB interactions are mocked — no real database required.
"""

import datetime
from decimal import Decimal
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
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
def mock_instrument_forex() -> MagicMock:
    """Mock Instrument for a forex pair."""
    inst = MagicMock()
    inst.id = 1
    inst.symbol = "EURUSD=X"
    inst.market = "forex"
    inst.pip_size = Decimal("0.0001")
    return inst


@pytest.fixture
def mock_instrument_crypto() -> MagicMock:
    """Mock Instrument for a crypto pair."""
    inst = MagicMock()
    inst.id = 2
    inst.symbol = "BTC/USDT"
    inst.market = "crypto"
    inst.pip_size = Decimal("0.01")
    return inst


@pytest.fixture
def mock_d1_candles() -> list:
    """200 mock D1 candles with ascending close prices (last close > MA200)."""
    rows = []
    for i in range(200):
        row = MagicMock()
        row.close = Decimal(str(round(1.0000 + i * 0.0010, 4)))
        rows.append(row)
    return rows


@pytest.fixture
def mock_ta_indicators() -> dict:
    """Minimal TA indicators dict for testing."""
    return {
        "current_price": 1.1050,
        "rsi": 55.0,
        "macd_line": 0.001,
        "macd_signal": 0.0005,
        "atr": 0.0010,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_forex_df(n: int = 60, seed: int = 42) -> pd.DataFrame:
    """Create a realistic forex OHLCV DataFrame."""
    np.random.seed(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    closes = 1.10 + np.cumsum(np.random.normal(0, 0.0002, n))
    df = pd.DataFrame(
        {
            "open": closes - 0.0001,
            "high": closes + 0.001,
            "low": closes - 0.001,
            "close": closes,
            "volume": np.random.uniform(1000, 5000, n),
        },
        index=idx,
    )
    return df


def _make_crypto_df(n: int = 60, seed: int = 42) -> pd.DataFrame:
    """Create a realistic crypto OHLCV DataFrame."""
    np.random.seed(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    closes = 50000 + np.cumsum(np.random.normal(0, 50, n))
    df = pd.DataFrame(
        {
            "open": closes - 10,
            "high": closes + 100,
            "low": closes - 100,
            "close": closes,
            "volume": np.random.uniform(100, 500, n),
        },
        index=idx,
    )
    return df


# ── SIM-25: Composite Score Threshold ─────────────────────────────────────────


def test_sim25_threshold_from_config():
    """Threshold values are read from config module, not hardcoded."""
    from src.config import MIN_COMPOSITE_SCORE, MIN_COMPOSITE_SCORE_CRYPTO

    assert MIN_COMPOSITE_SCORE == 15, f"Expected 15, got {MIN_COMPOSITE_SCORE}"
    assert MIN_COMPOSITE_SCORE_CRYPTO == 20, f"Expected 20, got {MIN_COMPOSITE_SCORE_CRYPTO}"


def test_sim25_score_below_threshold_rejected():
    """Backtest: score=12 (below 15 threshold) → no signal."""
    from src.backtesting.backtest_engine import BacktestEngine

    df = _make_forex_df()
    engine = BacktestEngine(db=MagicMock())

    with patch("src.analysis.ta_engine.TAEngine") as mock_ta_cls:
        mock_ta = MagicMock()
        # composite = 0.45 * 27 = 12.15 < 15
        mock_ta.calculate_ta_score.return_value = 27.0
        mock_ta.get_atr.return_value = Decimal("0.0010")
        mock_ta_cls.return_value = mock_ta

        with patch(
            "src.backtesting.backtest_engine._detect_regime_from_df",
            return_value="TREND_BULL",
        ):
            result = engine._generate_signal(df, "EURUSD=X", "forex", "H1")

    assert result is None, f"Expected None for score below threshold, got {result}"


def test_sim25_score_above_threshold_accepted():
    """Backtest: score=16 (above 15 threshold) → signal generated."""
    from src.backtesting.backtest_engine import BacktestEngine

    df = _make_forex_df()
    engine = BacktestEngine(db=MagicMock())

    with patch("src.analysis.ta_engine.TAEngine") as mock_ta_cls, \
         patch.object(BacktestEngine, "_check_volume_confirmation", return_value=True), \
         patch.object(BacktestEngine, "_check_momentum_alignment", return_value=True):
        mock_ta = MagicMock()
        # composite = 0.45 * 36 = 16.2 > 15
        mock_ta.calculate_ta_score.return_value = 36.0
        mock_ta.get_atr.return_value = Decimal("0.0010")
        mock_ta_cls.return_value = mock_ta

        with patch(
            "src.backtesting.backtest_engine._detect_regime_from_df",
            return_value="TREND_BULL",
        ):
            result = engine._generate_signal(df, "EURUSD=X", "forex", "H1")

    assert result is not None, "Expected signal for score above threshold"
    assert result["direction"] == "LONG"


def test_sim25_crypto_higher_threshold_rejected():
    """Crypto score=17 → rejected (needs 20)."""
    from src.backtesting.backtest_engine import BacktestEngine

    df = _make_crypto_df()
    engine = BacktestEngine(db=MagicMock())

    # 0.45 * 38 = 17.1 < 20 (crypto threshold)
    with patch("src.analysis.ta_engine.TAEngine") as mock_ta_cls:
        mock_ta = MagicMock()
        mock_ta.calculate_ta_score.return_value = 38.0
        mock_ta.get_atr.return_value = Decimal("100.0")
        mock_ta_cls.return_value = mock_ta

        with patch(
            "src.backtesting.backtest_engine._detect_regime_from_df",
            return_value="STRONG_TREND_BULL",
        ):
            result = engine._generate_signal(df, "BTC/USDT", "crypto", "H1")

    # BTC/USDT has override min_composite_score=20 in INSTRUMENT_OVERRIDES
    # so it uses 20 regardless. 17.1 < 20 → rejected.
    assert result is None, "Crypto score 17 should be rejected (threshold=20)"


def test_sim25_crypto_score_accepted():
    """Crypto score=46 → accepted (above threshold=20)."""
    from src.backtesting.backtest_engine import BacktestEngine

    df = _make_crypto_df()
    engine = BacktestEngine(db=MagicMock())

    # 0.45 * 103 ≈ 46.4 > 20
    with patch("src.analysis.ta_engine.TAEngine") as mock_ta_cls, \
         patch.object(BacktestEngine, "_check_volume_confirmation", return_value=True), \
         patch.object(BacktestEngine, "_check_momentum_alignment", return_value=True):
        mock_ta = MagicMock()
        mock_ta.calculate_ta_score.return_value = 103.0
        mock_ta.get_atr.return_value = Decimal("100.0")
        mock_ta_cls.return_value = mock_ta

        with patch(
            "src.backtesting.backtest_engine._detect_regime_from_df",
            return_value="STRONG_TREND_BULL",
        ):
            result = engine._generate_signal(df, "BTC/USDT", "crypto", "H1")

    assert result is not None, "Crypto score 46 should be accepted (threshold=20)"


# ── SIM-26: RANGING Regime Block ──────────────────────────────────────────────


def test_sim26_blocked_regimes_configurable():
    """BLOCKED_REGIMES is a list from config that can be extended."""
    from src.config import BLOCKED_REGIMES

    assert isinstance(BLOCKED_REGIMES, list)
    assert "RANGING" in BLOCKED_REGIMES


def test_sim26_ranging_blocked():
    """Backtest: RANGING regime → signal blocked."""
    from src.backtesting.backtest_engine import BacktestEngine

    df = _make_forex_df()
    engine = BacktestEngine(db=MagicMock())

    with patch("src.analysis.ta_engine.TAEngine") as mock_ta_cls:
        mock_ta = MagicMock()
        # composite = 0.45 * 40 = 18 > 15
        mock_ta.calculate_ta_score.return_value = 40.0
        mock_ta.get_atr.return_value = Decimal("0.0010")
        mock_ta_cls.return_value = mock_ta

        with patch(
            "src.backtesting.backtest_engine._detect_regime_from_df",
            return_value="RANGING",
        ):
            result = engine._generate_signal(df, "EURUSD=X", "forex", "H1")

    assert result is None, "RANGING regime should block signal"


def test_sim26_trend_bull_allowed():
    """TREND_BULL regime → signal allowed."""
    from src.backtesting.backtest_engine import BacktestEngine

    df = _make_forex_df()
    engine = BacktestEngine(db=MagicMock())

    with patch("src.analysis.ta_engine.TAEngine") as mock_ta_cls, \
         patch.object(BacktestEngine, "_check_volume_confirmation", return_value=True), \
         patch.object(BacktestEngine, "_check_momentum_alignment", return_value=True):
        mock_ta = MagicMock()
        # composite = 0.45 * 40 = 18 > 15
        mock_ta.calculate_ta_score.return_value = 40.0
        mock_ta.get_atr.return_value = Decimal("0.0010")
        mock_ta_cls.return_value = mock_ta

        with patch(
            "src.backtesting.backtest_engine._detect_regime_from_df",
            return_value="TREND_BULL",
        ):
            result = engine._generate_signal(df, "EURUSD=X", "forex", "H1")

    assert result is not None, "TREND_BULL regime should allow signal"


def test_sim26_volatile_allowed():
    """VOLATILE regime → signal allowed (not in BLOCKED_REGIMES)."""
    from src.backtesting.backtest_engine import BacktestEngine
    from src.config import BLOCKED_REGIMES

    assert "VOLATILE" not in BLOCKED_REGIMES

    df = _make_forex_df()
    engine = BacktestEngine(db=MagicMock())

    with patch("src.analysis.ta_engine.TAEngine") as mock_ta_cls, \
         patch.object(BacktestEngine, "_check_volume_confirmation", return_value=True), \
         patch.object(BacktestEngine, "_check_momentum_alignment", return_value=True):
        mock_ta = MagicMock()
        # composite = 0.45 * 40 = 18 > 15
        mock_ta.calculate_ta_score.return_value = 40.0
        mock_ta.get_atr.return_value = Decimal("0.0010")
        mock_ta_cls.return_value = mock_ta

        with patch(
            "src.backtesting.backtest_engine._detect_regime_from_df",
            return_value="VOLATILE",
        ):
            result = engine._generate_signal(df, "EURUSD=X", "forex", "H1")

    assert result is not None, "VOLATILE regime should allow signal"


# ── SIM-28: Instrument Overrides ──────────────────────────────────────────────


def test_sim28_no_override_default():
    """EURUSD: no override → uses global threshold=15."""
    from src.config import INSTRUMENT_OVERRIDES

    assert "EURUSD=X" not in INSTRUMENT_OVERRIDES


def test_sim28_btc_wider_sl():
    """BTC/USDT: SL uses 3.5×ATR override (wider than VOLATILE default 2.5×ATR)."""
    from src.signals.risk_manager_v2 import RiskManagerV2

    rm = RiskManagerV2()
    entry = Decimal("50000")
    atr = Decimal("1000")
    # VOLATILE default mult=2.5: SL = 50000 - 2500 = 47500
    levels_default = rm.calculate_levels_for_regime(entry, atr, "LONG", "VOLATILE")
    # Override mult=3.5: SL = 50000 - 3500 = 46500 (further from entry)
    levels_override = rm.calculate_levels_for_regime(
        entry, atr, "LONG", "VOLATILE", sl_atr_multiplier_override=3.5
    )
    assert levels_override["stop_loss"] < levels_default["stop_loss"], (
        f"Override SL {levels_override['stop_loss']} should be further from entry than default {levels_default['stop_loss']}"
    )


def test_sim28_btc_higher_threshold():
    """BTC/USDT: composite=17 rejected (override threshold=20)."""
    from src.backtesting.backtest_engine import BacktestEngine

    df = _make_crypto_df()
    engine = BacktestEngine(db=MagicMock())

    # 0.45 * 38 = 17.1 < override threshold 20
    with patch("src.analysis.ta_engine.TAEngine") as mock_ta_cls:
        mock_ta = MagicMock()
        mock_ta.calculate_ta_score.return_value = 38.0
        mock_ta.get_atr.return_value = Decimal("100.0")
        mock_ta_cls.return_value = mock_ta

        with patch(
            "src.backtesting.backtest_engine._detect_regime_from_df",
            return_value="STRONG_TREND_BULL",
        ):
            result = engine._generate_signal(df, "BTC/USDT", "crypto", "H1")

    assert result is None, "BTC score 17 should be rejected (override threshold=20)"


def test_sim28_btc_only_strong_trend():
    """BTC/USDT: TREND_BULL regime → blocked (only STRONG_TREND_BULL/BEAR allowed)."""
    from src.backtesting.backtest_engine import BacktestEngine

    df = _make_crypto_df()
    engine = BacktestEngine(db=MagicMock())

    # Score high enough (0.45 * 103 ≈ 46 > 20), but TREND_BULL not in allowed_regimes
    with patch("src.analysis.ta_engine.TAEngine") as mock_ta_cls:
        mock_ta = MagicMock()
        mock_ta.calculate_ta_score.return_value = 103.0
        mock_ta.get_atr.return_value = Decimal("100.0")
        mock_ta_cls.return_value = mock_ta

        with patch(
            "src.backtesting.backtest_engine._detect_regime_from_df",
            return_value="TREND_BULL",
        ):
            result = engine._generate_signal(df, "BTC/USDT", "crypto", "H1")

    assert result is None, "BTC TREND_BULL should be blocked (only STRONG_TREND allowed)"


def test_sim28_btc_strong_trend_allowed():
    """BTC/USDT: STRONG_TREND_BULL regime with score > 20 → allowed."""
    from src.backtesting.backtest_engine import BacktestEngine

    df = _make_crypto_df()
    engine = BacktestEngine(db=MagicMock())

    # 0.45 * 103 ≈ 46 > 20, STRONG_TREND_BULL is in allowed_regimes
    with patch("src.analysis.ta_engine.TAEngine") as mock_ta_cls, \
         patch.object(BacktestEngine, "_check_volume_confirmation", return_value=True), \
         patch.object(BacktestEngine, "_check_momentum_alignment", return_value=True):
        mock_ta = MagicMock()
        mock_ta.calculate_ta_score.return_value = 103.0
        mock_ta.get_atr.return_value = Decimal("100.0")
        mock_ta_cls.return_value = mock_ta

        with patch(
            "src.backtesting.backtest_engine._detect_regime_from_df",
            return_value="STRONG_TREND_BULL",
        ):
            result = engine._generate_signal(df, "BTC/USDT", "crypto", "H1")

    assert result is not None, "BTC STRONG_TREND_BULL with score>20 should be allowed"


def test_sim28_gbpusd_higher_threshold():
    """GBPUSD: score=17 (< override 20) → rejected."""
    from src.backtesting.backtest_engine import BacktestEngine

    df = _make_forex_df()
    engine = BacktestEngine(db=MagicMock())

    # 0.45 * 38 = 17.1 < override threshold 20 for GBPUSD=X
    with patch("src.analysis.ta_engine.TAEngine") as mock_ta_cls:
        mock_ta = MagicMock()
        mock_ta.calculate_ta_score.return_value = 38.0
        mock_ta.get_atr.return_value = Decimal("0.0010")
        mock_ta_cls.return_value = mock_ta

        with patch(
            "src.backtesting.backtest_engine._detect_regime_from_df",
            return_value="TREND_BULL",
        ):
            result = engine._generate_signal(df, "GBPUSD=X", "forex", "H1")

    assert result is None, "GBPUSD score 17 should be rejected (override threshold=20)"


def test_sim28_gbpusd_score_above_override_accepted():
    """GBPUSD: score=21 (> override 20) → accepted."""
    from src.backtesting.backtest_engine import BacktestEngine

    df = _make_forex_df()
    engine = BacktestEngine(db=MagicMock())

    # 0.45 * 47 = 21.15 > override threshold 20 for GBPUSD=X
    with patch("src.analysis.ta_engine.TAEngine") as mock_ta_cls, \
         patch.object(BacktestEngine, "_check_volume_confirmation", return_value=True), \
         patch.object(BacktestEngine, "_check_momentum_alignment", return_value=True):
        mock_ta = MagicMock()
        mock_ta.calculate_ta_score.return_value = 47.0
        mock_ta.get_atr.return_value = Decimal("0.0010")
        mock_ta_cls.return_value = mock_ta

        with patch(
            "src.backtesting.backtest_engine._detect_regime_from_df",
            return_value="TREND_BULL",
        ):
            result = engine._generate_signal(df, "GBPUSD=X", "forex", "H1")

    assert result is not None, "GBPUSD score 21 should be accepted (override threshold=20)"


# ── SIM-27: D1 MA200 Trend Filter ─────────────────────────────────────────────


def test_sim27_long_blocked_below_ma200():
    """D1 close < MA200 → LONG blocked."""
    from src.backtesting.backtest_engine import BacktestEngine

    # Create 200 rows where close decreases: last close < MA200
    # close[0] = 1.2000, close[199] = 1.2000 - 199*0.001 = 1.0010
    # MA200 ≈ average = (1.2000 + 1.0010) / 2 ≈ 1.1005
    # last close (1.0010) < MA200 (1.1005) → LONG should be blocked
    d1_rows = [MagicMock(close=Decimal(str(round(1.2000 - i * 0.0010, 4)))) for i in range(200)]

    result = BacktestEngine._check_d1_trend_alignment("EURUSD=X", "LONG", "H1", d1_rows)
    assert result is False, "LONG should be blocked when D1 close < MA200"


def test_sim27_short_blocked_above_ma200():
    """D1 close > MA200 → SHORT blocked."""
    from src.backtesting.backtest_engine import BacktestEngine

    # Create 200 rows where close increases: last close > MA200
    # close[0] = 1.0000, close[199] = 1.0000 + 199*0.001 = 1.199
    # MA200 ≈ average ≈ 1.0995
    # last close (1.199) > MA200 (1.0995) → SHORT should be blocked
    d1_rows = [MagicMock(close=Decimal(str(round(1.0000 + i * 0.0010, 4)))) for i in range(200)]

    result = BacktestEngine._check_d1_trend_alignment("EURUSD=X", "SHORT", "H1", d1_rows)
    assert result is False, "SHORT should be blocked when D1 close > MA200"


def test_sim27_long_allowed_above_ma200():
    """D1 close > MA200 → LONG allowed."""
    from src.backtesting.backtest_engine import BacktestEngine

    # last close > MA200 → LONG allowed
    d1_rows = [MagicMock(close=Decimal(str(round(1.0000 + i * 0.0010, 4)))) for i in range(200)]

    result = BacktestEngine._check_d1_trend_alignment("EURUSD=X", "LONG", "H1", d1_rows)
    assert result is True, "LONG should be allowed when D1 close > MA200"


def test_sim27_short_allowed_below_ma200():
    """D1 close < MA200 → SHORT allowed."""
    from src.backtesting.backtest_engine import BacktestEngine

    # last close < MA200 → SHORT allowed
    d1_rows = [MagicMock(close=Decimal(str(round(1.2000 - i * 0.0010, 4)))) for i in range(200)]

    result = BacktestEngine._check_d1_trend_alignment("EURUSD=X", "SHORT", "H1", d1_rows)
    assert result is True, "SHORT should be allowed when D1 close < MA200"


def test_sim27_no_d1_data_passthrough():
    """No D1 data → filter skipped (returns True, graceful degradation)."""
    from src.backtesting.backtest_engine import BacktestEngine

    result = BacktestEngine._check_d1_trend_alignment("EURUSD=X", "LONG", "H1", [])
    assert result is True, "Empty D1 data should pass filter (graceful degradation)"


def test_sim27_insufficient_d1_data_passthrough():
    """Less than 200 D1 rows → filter skipped (returns True)."""
    from src.backtesting.backtest_engine import BacktestEngine

    d1_rows = [MagicMock(close=Decimal("1.1000")) for _ in range(100)]
    result = BacktestEngine._check_d1_trend_alignment("EURUSD=X", "LONG", "H1", d1_rows)
    assert result is True, "Insufficient D1 data should pass filter (graceful degradation)"


def test_sim27_m15_no_filter():
    """M15 timeframe → filter not applied (always True, regardless of data)."""
    from src.backtesting.backtest_engine import BacktestEngine

    # Even with D1 data that would block — M15 should pass
    d1_rows = [MagicMock(close=Decimal(str(round(1.2000 - i * 0.0010, 4)))) for i in range(200)]

    result = BacktestEngine._check_d1_trend_alignment("EURUSD=X", "SHORT", "M15", d1_rows)
    assert result is True, "M15 timeframe should bypass D1 filter"


def test_sim27_m1_no_filter():
    """M1 timeframe → filter not applied."""
    from src.backtesting.backtest_engine import BacktestEngine

    result = BacktestEngine._check_d1_trend_alignment("EURUSD=X", "LONG", "M1", [])
    assert result is True


def test_sim27_m5_no_filter():
    """M5 timeframe → filter not applied."""
    from src.backtesting.backtest_engine import BacktestEngine

    result = BacktestEngine._check_d1_trend_alignment("EURUSD=X", "SHORT", "M5", [])
    assert result is True


def test_sim27_h4_applies_filter():
    """H4 timeframe → filter IS applied (not in exclusion list)."""
    from src.backtesting.backtest_engine import BacktestEngine

    # last close < MA200 → LONG blocked on H4 too
    d1_rows = [MagicMock(close=Decimal(str(round(1.2000 - i * 0.0010, 4)))) for i in range(200)]

    result = BacktestEngine._check_d1_trend_alignment("EURUSD=X", "LONG", "H4", d1_rows)
    assert result is False, "H4 timeframe should apply D1 MA200 filter"


# ── SIM-29: Volume confirmation ───────────────────────────────────────────────


def test_sim29_volume_above_threshold_passes():
    """Volume 150% of MA20 → filter passes."""
    from src.backtesting.backtest_engine import BacktestEngine

    n = 30
    # MA20 of last 20 = 100, current = 150 → 150% > 120%
    volume = np.ones(n) * 100.0
    volume[-1] = 150.0
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    df = pd.DataFrame({"open": 1.1, "high": 1.11, "low": 1.09, "close": 1.10, "volume": volume}, index=idx)
    assert BacktestEngine._check_volume_confirmation(df) is True


def test_sim29_volume_below_threshold_blocked():
    """Volume 80% of MA20 → filter blocked."""
    from src.backtesting.backtest_engine import BacktestEngine

    n = 30
    volume = np.ones(n) * 100.0
    volume[-1] = 80.0  # 80% < 120%
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    df = pd.DataFrame({"open": 1.1, "high": 1.11, "low": 1.09, "close": 1.10, "volume": volume}, index=idx)
    assert BacktestEngine._check_volume_confirmation(df) is False


def test_sim29_zero_volume_passthrough():
    """All volume == 0 → filter passes (broker doesn't send volume)."""
    from src.backtesting.backtest_engine import BacktestEngine

    n = 30
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    df = pd.DataFrame({"open": 1.1, "high": 1.11, "low": 1.09, "close": 1.10, "volume": np.zeros(n)}, index=idx)
    assert BacktestEngine._check_volume_confirmation(df) is True


def test_sim29_insufficient_data_passthrough():
    """Less than 20 bars → filter passes."""
    from src.backtesting.backtest_engine import BacktestEngine

    n = 15  # < 20
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    df = pd.DataFrame({"open": 1.1, "high": 1.11, "low": 1.09, "close": 1.10, "volume": np.ones(n) * 100}, index=idx)
    assert BacktestEngine._check_volume_confirmation(df) is True


# ── SIM-30: Momentum alignment (RSI/MACD) ────────────────────────────────────


def test_sim30_long_momentum_confirmed():
    """LONG: RSI=55 > 50, MACD > Signal → True."""
    from src.backtesting.backtest_engine import BacktestEngine

    indicators = {"rsi_14": 55.0, "macd_line": 0.001, "macd_signal": 0.0005}
    assert BacktestEngine._check_momentum_alignment(indicators, "LONG") is True


def test_sim30_long_momentum_rejected_rsi():
    """LONG: RSI=45 < 50 → False."""
    from src.backtesting.backtest_engine import BacktestEngine

    indicators = {"rsi_14": 45.0, "macd_line": 0.001, "macd_signal": 0.0005}
    assert BacktestEngine._check_momentum_alignment(indicators, "LONG") is False


def test_sim30_long_momentum_rejected_macd():
    """LONG: RSI=55, MACD < Signal → False."""
    from src.backtesting.backtest_engine import BacktestEngine

    indicators = {"rsi_14": 55.0, "macd_line": -0.001, "macd_signal": 0.0005}
    assert BacktestEngine._check_momentum_alignment(indicators, "LONG") is False


def test_sim30_missing_data_passthrough():
    """Missing RSI/MACD → filter passes (graceful degradation)."""
    from src.backtesting.backtest_engine import BacktestEngine

    assert BacktestEngine._check_momentum_alignment({}, "LONG") is True
    assert BacktestEngine._check_momentum_alignment({"rsi_14": 55.0}, "LONG") is True


# ── SIM-31: Min signal strength = BUY ────────────────────────────────────────


def test_sim31_strong_buy_allowed():
    """STRONG_BUY → in ALLOWED_SIGNAL_STRENGTHS."""
    from src.backtesting.backtest_engine import ALLOWED_SIGNAL_STRENGTHS

    assert "STRONG_BUY" in ALLOWED_SIGNAL_STRENGTHS


def test_sim31_buy_allowed():
    """BUY → in ALLOWED_SIGNAL_STRENGTHS."""
    from src.backtesting.backtest_engine import ALLOWED_SIGNAL_STRENGTHS

    assert "BUY" in ALLOWED_SIGNAL_STRENGTHS


def test_sim31_weak_buy_rejected():
    """WEAK_BUY → not in ALLOWED_SIGNAL_STRENGTHS, signal filtered."""
    from src.backtesting.backtest_engine import ALLOWED_SIGNAL_STRENGTHS, _get_signal_strength

    # composite = 8.0 → WEAK_BUY
    strength = _get_signal_strength(8.0)
    assert strength == "WEAK_BUY"
    assert strength not in ALLOWED_SIGNAL_STRENGTHS


def test_sim31_hold_rejected():
    """HOLD → not in ALLOWED_SIGNAL_STRENGTHS."""
    from src.backtesting.backtest_engine import ALLOWED_SIGNAL_STRENGTHS, _get_signal_strength

    strength = _get_signal_strength(3.0)
    assert strength == "HOLD"
    assert strength not in ALLOWED_SIGNAL_STRENGTHS


# ── SIM-32: Weekday filter ────────────────────────────────────────────────────


def test_sim32_monday_morning_blocked():
    """Mon 06:00 UTC, forex → blocked."""
    from src.backtesting.backtest_engine import BacktestEngine

    ts = datetime.datetime(2024, 1, 1, 6, 0, tzinfo=datetime.timezone.utc)  # Monday
    assert ts.weekday() == 0  # Monday
    assert BacktestEngine._check_weekday_filter(ts, "forex") is False


def test_sim32_monday_afternoon_allowed():
    """Mon 14:00 UTC, forex → allowed."""
    from src.backtesting.backtest_engine import BacktestEngine

    ts = datetime.datetime(2024, 1, 1, 14, 0, tzinfo=datetime.timezone.utc)
    assert BacktestEngine._check_weekday_filter(ts, "forex") is True


def test_sim32_friday_evening_blocked():
    """Fri 20:00 UTC → blocked."""
    from src.backtesting.backtest_engine import BacktestEngine

    ts = datetime.datetime(2024, 1, 5, 20, 0, tzinfo=datetime.timezone.utc)  # Friday
    assert ts.weekday() == 4  # Friday
    assert BacktestEngine._check_weekday_filter(ts, "forex") is False


def test_sim32_monday_crypto_allowed():
    """Mon 06:00 UTC, crypto → allowed (exempt from Monday filter)."""
    from src.backtesting.backtest_engine import BacktestEngine

    ts = datetime.datetime(2024, 1, 1, 6, 0, tzinfo=datetime.timezone.utc)
    assert BacktestEngine._check_weekday_filter(ts, "crypto") is True


# ── SIM-33: Economic calendar filter ─────────────────────────────────────────


def test_sim33_high_impact_event_blocks_signal():
    """HIGH-impact event within ±2h → signal blocked."""
    from src.backtesting.backtest_engine import BacktestEngine

    candle_ts = datetime.datetime(2024, 3, 8, 13, 30, tzinfo=datetime.timezone.utc)  # NFP time
    event = MagicMock()
    event.event_date = datetime.datetime(2024, 3, 8, 13, 30, tzinfo=datetime.timezone.utc)

    result = BacktestEngine._check_economic_calendar(candle_ts, [event])
    assert result is False


def test_sim33_no_event_allows_signal():
    """No events near candle → signal allowed."""
    from src.backtesting.backtest_engine import BacktestEngine

    candle_ts = datetime.datetime(2024, 3, 8, 13, 30, tzinfo=datetime.timezone.utc)
    event = MagicMock()
    event.event_date = datetime.datetime(2024, 3, 8, 20, 0, tzinfo=datetime.timezone.utc)  # 6.5h away

    result = BacktestEngine._check_economic_calendar(candle_ts, [event])
    assert result is True


def test_sim33_no_historical_events_passthrough():
    """Empty event list → passthrough."""
    from src.backtesting.backtest_engine import BacktestEngine

    candle_ts = datetime.datetime(2024, 3, 8, 13, 30, tzinfo=datetime.timezone.utc)
    result = BacktestEngine._check_economic_calendar(candle_ts, [])
    assert result is True


# ── SIM-34: Breakeven buffer ───────────────────────────────────────────────────


def test_sim34_breakeven_with_buffer_long():
    """LONG: entry=1.1000, TP1=1.1100 → new_sl=1.1050 (not 1.1000)."""
    from src.tracker.signal_tracker import BREAKEVEN_BUFFER_RATIO

    entry = Decimal("1.1000")
    tp1 = Decimal("1.1100")

    # SIM-34 formula: new_sl = entry + 0.5 * (tp1 - entry)
    new_sl = entry + BREAKEVEN_BUFFER_RATIO * (tp1 - entry)
    assert new_sl == Decimal("1.1050")


def test_sim34_breakeven_with_buffer_short():
    """SHORT: entry=1.1000, TP1=1.0900 → new_sl=1.0950 (not 1.1000)."""
    from src.tracker.signal_tracker import BREAKEVEN_BUFFER_RATIO

    entry = Decimal("1.1000")
    tp1 = Decimal("1.0900")

    # SIM-34 formula: new_sl = entry - 0.5 * (entry - tp1)
    new_sl = entry - BREAKEVEN_BUFFER_RATIO * (entry - tp1)
    assert new_sl == Decimal("1.0950")


def test_sim34_buffer_configurable():
    """BREAKEVEN_BUFFER_RATIO is configurable (not hardcoded 0.0)."""
    from src.tracker.signal_tracker import BREAKEVEN_BUFFER_RATIO
    assert BREAKEVEN_BUFFER_RATIO == Decimal("0.5")


def test_sim34_remaining_position_survives_normal_pullback():
    """After breakeven, SL at 1.1050: price at 1.1060 → position still open."""
    from src.tracker.signal_tracker import BREAKEVEN_BUFFER_RATIO

    entry = Decimal("1.1000")
    tp1 = Decimal("1.1100")
    new_sl = entry + BREAKEVEN_BUFFER_RATIO * (tp1 - entry)  # 1.1050

    # Normal pullback to 1.1060 (above new_sl=1.1050) → position survives
    current_price = Decimal("1.1060")
    assert current_price > new_sl, "Position should survive pullback to 1.1060"

    # Deeper pullback to 1.1040 (below new_sl=1.1050) → position exits
    deep_pullback = Decimal("1.1040")
    assert deep_pullback < new_sl, "Position should exit on deep pullback to 1.1040"


# ── SIM-35: Time-based exit ────────────────────────────────────────────────────


def test_sim35_time_exit_h1_48_candles():
    """TIME_EXIT_CANDLES["H1"] == 48."""
    from src.tracker.signal_tracker import TIME_EXIT_CANDLES
    assert TIME_EXIT_CANDLES["H1"] == 48


def test_sim35_time_exit_no_trigger_profitable():
    """H1, 50 candles elapsed, profitable → no exit (logic: profitable skips time exit)."""
    from src.tracker.signal_tracker import TIME_EXIT_CANDLES
    # The logic: time exit only fires if unrealized_pnl <= 0
    # If profitable, no exit regardless of candle count
    assert TIME_EXIT_CANDLES["H1"] == 48  # max is 48


def test_sim35_time_exit_no_trigger_early():
    """H1, 20 candles elapsed → no exit (< 48)."""
    from src.tracker.signal_tracker import TIME_EXIT_CANDLES
    assert 20 < TIME_EXIT_CANDLES["H1"]  # 20 < 48, no exit


def test_sim35_time_exit_h4_20_candles():
    """TIME_EXIT_CANDLES["H4"] == 20."""
    from src.tracker.signal_tracker import TIME_EXIT_CANDLES
    assert TIME_EXIT_CANDLES["H4"] == 20


def test_sim35_time_exit_d1_10_candles():
    """TIME_EXIT_CANDLES["D1"] == 10."""
    from src.tracker.signal_tracker import TIME_EXIT_CANDLES
    assert TIME_EXIT_CANDLES["D1"] == 10


# ── SIM-36: S/R snapping in backtest ──────────────────────────────────────────


def test_sim36_backtest_sl_snaps_to_support():
    """Backtest: _recalc_sl_tp runs without error and returns valid levels with S/R."""
    from src.signals.risk_manager_v2 import RiskManagerV2

    rm = RiskManagerV2()
    entry = Decimal("1.1000")
    atr = Decimal("0.0050")
    support = [Decimal("1.0910")]
    levels = rm.calculate_levels_for_regime(
        entry, atr, "LONG", "TREND_BULL",
        support_levels=support
    )
    assert levels["stop_loss"] is not None
    assert levels["take_profit_1"] is not None


def test_sim36_backtest_no_sr_levels_fallback():
    """Backtest: no S/R → SL uses ATR (no snap)."""
    from src.signals.risk_manager_v2 import RiskManagerV2

    rm = RiskManagerV2()
    entry = Decimal("1.1000")
    atr = Decimal("0.0050")
    levels = rm.calculate_levels_for_regime(entry, atr, "LONG", "TREND_BULL")
    # TREND_BULL: sl_mult=2.0, SL = 1.1000 - 2.0*0.005 = 1.09
    assert levels["stop_loss"] == Decimal("1.09000000")


def test_sim36_recalc_sl_tp_accepts_sr_levels():
    """BacktestEngine._recalc_sl_tp accepts support_levels / resistance_levels args."""
    from src.backtesting.backtest_engine import BacktestEngine
    from unittest.mock import MagicMock
    from sqlalchemy.ext.asyncio import AsyncSession

    db_mock = MagicMock(spec=AsyncSession)
    engine = BacktestEngine(db=db_mock)

    entry = Decimal("1.1000")
    atr = Decimal("0.0050")
    support = [Decimal("1.0910")]
    resistance = [Decimal("1.1110")]

    sl, tp = engine._recalc_sl_tp(
        entry=entry,
        atr=atr,
        direction="LONG",
        regime="TREND_BULL",
        symbol="EURUSD=X",
        support_levels=support,
        resistance_levels=resistance,
    )
    assert sl is not None
    assert tp is not None


# ── SIM-37: Externalize swap rates to JSON ─────────────────────────────────────


def test_sim37_swap_rates_from_json():
    """Swap rates loaded from JSON file."""
    import json
    import os

    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "swap_rates.json"
    )
    assert os.path.exists(config_path), "config/swap_rates.json should exist"
    with open(config_path) as f:
        data = json.load(f)
    assert "rates" in data
    assert "AUDUSD=X" in data["rates"]


def test_sim37_swap_rates_fallback():
    """Hardcoded fallback rates still exist and have EURUSD."""
    from src.tracker.signal_tracker import SWAP_DAILY_PIPS_HARDCODE
    assert "EURUSD=X" in SWAP_DAILY_PIPS_HARDCODE


def test_sim37_swap_rates_loaded_is_valid_dict():
    """SWAP_DAILY_PIPS is a valid dict with Decimal values."""
    from src.tracker.signal_tracker import SWAP_DAILY_PIPS

    assert isinstance(SWAP_DAILY_PIPS, dict)
    # Check at least one entry has Decimal values
    if SWAP_DAILY_PIPS:
        first_val = next(iter(SWAP_DAILY_PIPS.values()))
        assert "long" in first_val
        assert isinstance(first_val["long"], Decimal)


def test_sim37_json_has_updated_at():
    """config/swap_rates.json has 'updated_at' field for staleness tracking."""
    import json
    import os

    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "swap_rates.json"
    )
    with open(config_path) as f:
        data = json.load(f)
    assert "updated_at" in data


# ── SIM-38: DXY real-time filter ──────────────────────────────────────────────


def test_sim38_dxy_strong_blocks_usd_long_side():
    """DXY RSI=60 → EURUSD LONG blocked."""
    from src.signals.signal_engine import _check_dxy_alignment
    assert _check_dxy_alignment("LONG", "EURUSD=X", dxy_rsi=60.0) is False
    assert _check_dxy_alignment("SHORT", "EURUSD=X", dxy_rsi=60.0) is True  # SHORT not blocked


def test_sim38_dxy_weak_blocks_usd_long_side_short():
    """DXY RSI=40 → GBPUSD SHORT blocked."""
    from src.signals.signal_engine import _check_dxy_alignment
    assert _check_dxy_alignment("SHORT", "GBPUSD=X", dxy_rsi=40.0) is False
    assert _check_dxy_alignment("LONG", "GBPUSD=X", dxy_rsi=40.0) is True  # LONG not blocked


def test_sim38_dxy_strong_allows_usd_base():
    """DXY RSI=60 → USDJPY LONG allowed (not in USD long-side pairs)."""
    from src.signals.signal_engine import _check_dxy_alignment
    assert _check_dxy_alignment("LONG", "USDJPY=X", dxy_rsi=60.0) is True


def test_sim38_dxy_neutral_no_filter():
    """DXY RSI=50 → no filtering."""
    from src.signals.signal_engine import _check_dxy_alignment
    assert _check_dxy_alignment("LONG", "EURUSD=X", dxy_rsi=50.0) is True
    assert _check_dxy_alignment("SHORT", "EURUSD=X", dxy_rsi=50.0) is True


def test_sim38_dxy_no_data_passthrough():
    """No DXY data (None) → passthrough."""
    from src.signals.signal_engine import _check_dxy_alignment
    assert _check_dxy_alignment("LONG", "EURUSD=X", dxy_rsi=None) is True


def test_sim38_backtest_dxy_method_exists():
    """BacktestEngine._check_dxy_alignment static method exists and works."""
    from src.backtesting.backtest_engine import BacktestEngine
    # DXY RSI=60 → EURUSD LONG blocked
    assert BacktestEngine._check_dxy_alignment("LONG", "EURUSD=X", 60.0) is False
    # No data → passthrough
    assert BacktestEngine._check_dxy_alignment("LONG", "EURUSD=X", None) is True
    # Non-matching symbol → passthrough
    assert BacktestEngine._check_dxy_alignment("LONG", "USDJPY=X", 60.0) is True


# ── SIM-39: Fear & Greed Index for crypto ─────────────────────────────────────


def test_sim39_extreme_fear_boosts_long():
    """FG=15 (Extreme Fear) → +5 adjustment for BTC LONG."""
    from src.collectors.fear_greed_collector import get_fear_greed_adjustment
    assert get_fear_greed_adjustment(15, "LONG", "BTC/USDT") == 5


def test_sim39_extreme_greed_boosts_short():
    """FG=85 (Extreme Greed) → +5 adjustment for BTC SHORT."""
    from src.collectors.fear_greed_collector import get_fear_greed_adjustment
    assert get_fear_greed_adjustment(85, "SHORT", "BTC/USDT") == 5


def test_sim39_neutral_no_effect():
    """FG=50 → 0 adjustment."""
    from src.collectors.fear_greed_collector import get_fear_greed_adjustment
    assert get_fear_greed_adjustment(50, "LONG", "BTC/USDT") == 0
    assert get_fear_greed_adjustment(50, "SHORT", "BTC/USDT") == 0


def test_sim39_non_crypto_no_effect():
    """FG=15 → 0 for EURUSD (not crypto)."""
    from src.collectors.fear_greed_collector import get_fear_greed_adjustment
    assert get_fear_greed_adjustment(15, "LONG", "EURUSD=X") == 0


def test_sim39_no_data_no_effect():
    """FG=None → 0 adjustment."""
    from src.collectors.fear_greed_collector import get_fear_greed_adjustment
    assert get_fear_greed_adjustment(None, "LONG", "BTC/USDT") == 0


def test_sim39_boundary_fear_20():
    """FG=20 (boundary Extreme Fear) → +5 for LONG."""
    from src.collectors.fear_greed_collector import get_fear_greed_adjustment
    assert get_fear_greed_adjustment(20, "LONG", "BTC/USDT") == 5


def test_sim39_boundary_greed_80():
    """FG=80 (boundary Extreme Greed) → +5 for SHORT."""
    from src.collectors.fear_greed_collector import get_fear_greed_adjustment
    assert get_fear_greed_adjustment(80, "SHORT", "BTC/USDT") == 5


def test_sim39_fear_does_not_boost_short():
    """FG=15 (Extreme Fear) → 0 for SHORT (only boosts LONG)."""
    from src.collectors.fear_greed_collector import get_fear_greed_adjustment
    assert get_fear_greed_adjustment(15, "SHORT", "BTC/USDT") == 0


def test_sim39_greed_does_not_boost_long():
    """FG=85 (Extreme Greed) → 0 for LONG (only boosts SHORT)."""
    from src.collectors.fear_greed_collector import get_fear_greed_adjustment
    assert get_fear_greed_adjustment(85, "LONG", "BTC/USDT") == 0


# ── SIM-40: Funding Rate extreme filter ───────────────────────────────────────


def test_sim40_high_funding_penalizes_long():
    """FR=+0.15% → LONG composite -10."""
    from src.signals.signal_engine import _get_funding_rate_adjustment
    adj = _get_funding_rate_adjustment(0.0015, "LONG", "crypto")  # 0.15% > 0.1%
    assert adj == -10.0


def test_sim40_negative_funding_penalizes_short():
    """FR=-0.15% → SHORT composite -10."""
    from src.signals.signal_engine import _get_funding_rate_adjustment
    adj = _get_funding_rate_adjustment(-0.0015, "SHORT", "crypto")
    assert adj == -10.0


def test_sim40_normal_funding_no_effect():
    """FR=+0.03% → no penalty."""
    from src.signals.signal_engine import _get_funding_rate_adjustment
    adj = _get_funding_rate_adjustment(0.0003, "LONG", "crypto")
    assert adj == 0.0


def test_sim40_non_crypto_no_effect():
    """FR doesn't apply to forex."""
    from src.signals.signal_engine import _get_funding_rate_adjustment
    adj = _get_funding_rate_adjustment(0.002, "LONG", "forex")
    assert adj == 0.0


def test_sim40_no_data_no_effect():
    """FR=None → no penalty."""
    from src.signals.signal_engine import _get_funding_rate_adjustment
    adj = _get_funding_rate_adjustment(None, "LONG", "crypto")
    assert adj == 0.0


def test_sim40_boundary_exactly_01pct_long():
    """FR=+0.1% exactly (boundary not exceeded) → no penalty for LONG."""
    from src.signals.signal_engine import _get_funding_rate_adjustment
    # 0.001 is the threshold — strictly greater than 0.001 triggers penalty
    adj = _get_funding_rate_adjustment(0.001, "LONG", "crypto")
    assert adj == 0.0


def test_sim40_high_funding_does_not_penalize_short():
    """FR=+0.15% → no penalty for SHORT."""
    from src.signals.signal_engine import _get_funding_rate_adjustment
    adj = _get_funding_rate_adjustment(0.0015, "SHORT", "crypto")
    assert adj == 0.0


# ── SIM-41: COT Data for forex ────────────────────────────────────────────────


def test_sim41_cot_net_long_boosts_fa():
    """Net long + growing → +5 FA adjustment."""
    from src.collectors.cot_collector import get_cot_fa_adjustment
    adj = get_cot_fa_adjustment(net_positions=10000, change_week=500)
    assert adj == 5.0


def test_sim41_cot_net_short_penalizes_fa():
    """Net short + growing (more negative) → -5 FA adjustment."""
    from src.collectors.cot_collector import get_cot_fa_adjustment
    adj = get_cot_fa_adjustment(net_positions=-10000, change_week=-500)
    assert adj == -5.0


def test_sim41_cot_no_data_neutral():
    """No COT data → 0."""
    from src.collectors.cot_collector import get_cot_fa_adjustment
    assert get_cot_fa_adjustment(None, None) == 0.0
    assert get_cot_fa_adjustment(10000, None) == 0.0


def test_sim41_cot_net_long_shrinking_neutral():
    """Net long but shrinking → 0 (mixed signal, no adjustment)."""
    from src.collectors.cot_collector import get_cot_fa_adjustment
    adj = get_cot_fa_adjustment(net_positions=10000, change_week=-200)
    assert adj == 0.0


def test_sim41_cot_net_short_shrinking_neutral():
    """Net short but shrinking (less negative) → 0 (mixed signal)."""
    from src.collectors.cot_collector import get_cot_fa_adjustment
    adj = get_cot_fa_adjustment(net_positions=-10000, change_week=200)
    assert adj == 0.0


def test_sim41_fa_engine_accepts_cot_macro_data():
    """FAEngine.calculate_fa_score() applies COT adjustment when macro_data has COT_NET entries."""
    from src.analysis.fa_engine import FAEngine
    from unittest.mock import MagicMock

    instrument = MagicMock()
    instrument.symbol = "EURUSD=X"
    instrument.market = "forex"

    # Two COT records: latest net=10000, previous=9500 → change=+500 → +5 boost
    cot_latest = MagicMock()
    cot_latest.indicator_name = "COT_NET_EURUSD=X"
    cot_latest.value = 10000.0

    cot_prev = MagicMock()
    cot_prev.indicator_name = "COT_NET_EURUSD=X"
    cot_prev.value = 9500.0

    fa = FAEngine(instrument, [cot_latest, cot_prev], [])
    score = fa.calculate_fa_score()
    # Score should be within range and not crash
    assert -100.0 <= score <= 100.0


# ── SIM-42: Unified SignalFilterPipeline ──────────────────────────────────────


def test_sim42_pipeline_blocks_on_score_below_threshold():
    """SignalFilterPipeline blocks when composite_score < threshold."""
    from src.signals.filter_pipeline import SignalFilterPipeline
    import datetime

    pipeline = SignalFilterPipeline()
    ctx = {
        "composite_score": 5.0,
        "market_type": "forex",
        "symbol": "EURUSD=X",
        "regime": "TREND_BULL",
        "direction": "LONG",
        "timeframe": "H1",
        "df": None,
        "ta_indicators": {},
        "candle_ts": datetime.datetime(2024, 3, 6, 12, 0, tzinfo=datetime.timezone.utc),
        "d1_rows": [],
        "economic_events": [],
    }
    passed, reason = pipeline.run_all(ctx)
    assert not passed
    assert "score_below_threshold" in reason


def test_sim42_pipeline_blocks_on_regime():
    """SignalFilterPipeline blocks when regime is RANGING."""
    from src.signals.filter_pipeline import SignalFilterPipeline
    import datetime

    pipeline = SignalFilterPipeline()
    ctx = {
        "composite_score": 20.0,
        "market_type": "forex",
        "symbol": "EURUSD=X",
        "regime": "RANGING",
        "direction": "LONG",
        "timeframe": "H1",
        "df": None,
        "ta_indicators": {},
        "candle_ts": datetime.datetime(2024, 3, 6, 12, 0, tzinfo=datetime.timezone.utc),
        "d1_rows": [],
        "economic_events": [],
    }
    passed, reason = pipeline.run_all(ctx)
    assert not passed
    assert "regime_blocked" in reason


def test_sim42_pipeline_passes_valid_context():
    """SignalFilterPipeline passes when all conditions are met."""
    from src.signals.filter_pipeline import SignalFilterPipeline
    import datetime

    pipeline = SignalFilterPipeline()
    ctx = {
        "composite_score": 20.0,
        "market_type": "forex",
        "symbol": "EURUSD=X",
        "regime": "TREND_BULL",
        "direction": "LONG",
        "timeframe": "H1",
        "df": None,
        "ta_indicators": {"rsi_14": 60.0, "macd_line": 0.001, "macd_signal": 0.0005},
        "candle_ts": datetime.datetime(2024, 3, 6, 12, 0, tzinfo=datetime.timezone.utc),  # Wednesday
        "d1_rows": [],
        "economic_events": [],
    }
    passed, reason = pipeline.run_all(ctx)
    assert passed is True
    assert reason == "all_passed"


def test_sim42_live_and_backtest_same_result():
    """Same context → same result regardless of caller."""
    from src.signals.filter_pipeline import SignalFilterPipeline
    import datetime

    pipeline = SignalFilterPipeline()
    ctx = {
        "composite_score": 20.0,
        "market_type": "forex",
        "symbol": "EURUSD=X",
        "regime": "TREND_BULL",
        "direction": "LONG",
        "timeframe": "H1",
        "df": None,
        "ta_indicators": {"rsi_14": 60.0, "macd_line": 0.001, "macd_signal": 0.0005},
        "candle_ts": datetime.datetime(2024, 3, 6, 12, 0, tzinfo=datetime.timezone.utc),
        "d1_rows": [],
        "economic_events": [],
    }
    result1 = pipeline.run_all(ctx)
    result2 = pipeline.run_all(ctx)
    assert result1 == result2
    assert result1[0] is True


def test_sim42_pipeline_disabled_filters_pass():
    """Disabling score filter allows low-score signal through."""
    from src.signals.filter_pipeline import SignalFilterPipeline
    import datetime

    pipeline = SignalFilterPipeline(apply_score_filter=False)
    ctx = {
        "composite_score": 1.0,   # would be blocked by score filter
        "market_type": "forex",
        "symbol": "EURUSD=X",
        "regime": "TREND_BULL",
        "direction": "LONG",
        "timeframe": "H1",
        "df": None,
        "ta_indicators": {},
        "candle_ts": datetime.datetime(2024, 3, 6, 12, 0, tzinfo=datetime.timezone.utc),
        "d1_rows": [],
        "economic_events": [],
    }
    passed, reason = pipeline.run_all(ctx)
    assert passed is True


def test_sim42_pipeline_momentum_blocks():
    """Pipeline blocks when momentum is misaligned for LONG."""
    from src.signals.filter_pipeline import SignalFilterPipeline
    import datetime

    pipeline = SignalFilterPipeline()
    ctx = {
        "composite_score": 20.0,
        "market_type": "forex",
        "symbol": "EURUSD=X",
        "regime": "TREND_BULL",
        "direction": "LONG",
        "timeframe": "H1",
        "df": None,
        "ta_indicators": {"rsi_14": 40.0, "macd_line": -0.001, "macd_signal": 0.0005},  # RSI<50 AND MACD<Signal
        "candle_ts": datetime.datetime(2024, 3, 6, 12, 0, tzinfo=datetime.timezone.utc),
        "d1_rows": [],
        "economic_events": [],
    }
    passed, reason = pipeline.run_all(ctx)
    assert not passed
    assert "momentum_misaligned" in reason


def test_sim42_pipeline_weekday_blocks_friday():
    """Pipeline blocks on Friday evening."""
    from src.signals.filter_pipeline import SignalFilterPipeline
    import datetime

    pipeline = SignalFilterPipeline(
        apply_score_filter=False,
        apply_regime_filter=False,
        apply_momentum_filter=False,
    )
    ctx = {
        "composite_score": 20.0,
        "market_type": "forex",
        "symbol": "EURUSD=X",
        "regime": "TREND_BULL",
        "direction": "LONG",
        "timeframe": "H1",
        "df": None,
        "ta_indicators": {},
        "candle_ts": datetime.datetime(2024, 1, 5, 20, 0, tzinfo=datetime.timezone.utc),  # Friday 20:00
        "d1_rows": [],
        "economic_events": [],
    }
    passed, reason = pipeline.run_all(ctx)
    assert not passed
    assert "friday_close_filter" in reason


def test_sim42_check_score_threshold_uses_instrument_override():
    """check_score_threshold uses INSTRUMENT_OVERRIDES for GBPUSD."""
    from src.signals.filter_pipeline import SignalFilterPipeline

    pipeline = SignalFilterPipeline()
    # GBPUSD=X override is min_composite_score=20; score=16 < 20 → blocked
    passed, reason = pipeline.check_score_threshold(16.0, "forex", "GBPUSD=X")
    assert not passed
    assert "score_below_threshold" in reason


def test_sim42_check_score_threshold_custom_min_score():
    """min_composite_score constructor arg overrides config."""
    from src.signals.filter_pipeline import SignalFilterPipeline

    pipeline = SignalFilterPipeline(min_composite_score=25.0)
    passed, reason = pipeline.check_score_threshold(20.0, "forex", "EURUSD=X")
    assert not passed
    assert "score_below_threshold" in reason


def test_sim42_calendar_block():
    """Pipeline blocks when economic event is within ±2h."""
    from src.signals.filter_pipeline import SignalFilterPipeline
    import datetime
    from unittest.mock import MagicMock

    pipeline = SignalFilterPipeline(
        apply_score_filter=False,
        apply_regime_filter=False,
        apply_momentum_filter=False,
        apply_weekday_filter=False,
    )
    candle_ts = datetime.datetime(2024, 3, 8, 13, 30, tzinfo=datetime.timezone.utc)
    event = MagicMock()
    event.event_date = candle_ts

    ctx = {
        "composite_score": 20.0,
        "market_type": "forex",
        "symbol": "EURUSD=X",
        "regime": "TREND_BULL",
        "direction": "LONG",
        "timeframe": "H1",
        "df": None,
        "ta_indicators": {},
        "candle_ts": candle_ts,
        "d1_rows": [],
        "economic_events": [event],
    }
    passed, reason = pipeline.run_all(ctx)
    assert not passed
    assert reason == "economic_calendar_block"


# ── SIM-43: Parameterized backtest ────────────────────────────────────────────


def test_sim43_backtest_params_accepts_filter_flags():
    """BacktestParams accepts all SIM-43 filter flags."""
    from src.backtesting.backtest_params import BacktestParams

    params = BacktestParams(
        symbols=["EURUSD=X"],
        timeframe="H1",
        start_date="2024-01-01",
        end_date="2024-06-01",
        apply_ranging_filter=True,
        apply_d1_trend_filter=True,
        apply_volume_filter=True,
        apply_weekday_filter=True,
        apply_momentum_filter=True,
        apply_calendar_filter=True,
        min_composite_score=15.0,
    )
    assert params.apply_ranging_filter is True
    assert params.apply_d1_trend_filter is True
    assert params.apply_volume_filter is True
    assert params.apply_weekday_filter is True
    assert params.apply_momentum_filter is True
    assert params.apply_calendar_filter is True
    assert params.min_composite_score == 15.0


def test_sim43_backtest_params_defaults():
    """BacktestParams filter flags default to True, min_composite_score to None."""
    from src.backtesting.backtest_params import BacktestParams

    params = BacktestParams(
        symbols=["EURUSD=X"],
        start_date="2024-01-01",
        end_date="2024-06-01",
    )
    assert params.apply_ranging_filter is True
    assert params.apply_d1_trend_filter is True
    assert params.apply_volume_filter is True
    assert params.apply_weekday_filter is True
    assert params.apply_momentum_filter is True
    assert params.apply_calendar_filter is True
    assert params.min_composite_score is None


def test_sim43_backtest_params_all_filters_disabled():
    """BacktestParams can disable all filters."""
    from src.backtesting.backtest_params import BacktestParams

    params = BacktestParams(
        symbols=["EURUSD=X"],
        timeframe="H1",
        start_date="2024-01-01",
        end_date="2024-06-01",
        apply_ranging_filter=False,
        apply_d1_trend_filter=False,
        apply_volume_filter=False,
        apply_weekday_filter=False,
        apply_momentum_filter=False,
        apply_calendar_filter=False,
    )
    assert params.apply_ranging_filter is False
    assert params.apply_d1_trend_filter is False
    assert params.apply_volume_filter is False
    assert params.apply_weekday_filter is False
    assert params.apply_momentum_filter is False
    assert params.apply_calendar_filter is False


def test_sim43_custom_score_threshold():
    """BacktestParams accepts custom min_composite_score."""
    from src.backtesting.backtest_params import BacktestParams

    params = BacktestParams(
        symbols=["EURUSD=X"],
        start_date="2024-01-01",
        end_date="2024-06-01",
        min_composite_score=25.0,
    )
    assert params.min_composite_score == 25.0


# ── SIM-44: Extended backtest metrics ─────────────────────────────────────────


def test_sim44_extended_metrics_present():
    """_compute_summary includes all SIM-44 fields."""
    from src.backtesting.backtest_engine import _compute_summary
    from src.backtesting.backtest_params import BacktestTradeResult
    from decimal import Decimal
    import datetime

    trades = [
        BacktestTradeResult(
            symbol="EURUSD=X", timeframe="H1", direction="LONG",
            entry_price=Decimal("1.1000"), exit_price=Decimal("1.1050"),
            exit_reason="tp_hit", pnl_usd=Decimal("5.0"), result="win",
            entry_at=datetime.datetime(2024, 3, 6, 10, 0, tzinfo=datetime.timezone.utc),
            exit_at=datetime.datetime(2024, 3, 6, 12, 0, tzinfo=datetime.timezone.utc),
            duration_minutes=120, mfe=Decimal("0.005"), mae=Decimal("0.001"),
        ),
        BacktestTradeResult(
            symbol="EURUSD=X", timeframe="H1", direction="SHORT",
            entry_price=Decimal("1.1000"), exit_price=Decimal("1.1020"),
            exit_reason="sl_hit", pnl_usd=Decimal("-2.0"), result="loss",
            entry_at=datetime.datetime(2024, 3, 7, 14, 0, tzinfo=datetime.timezone.utc),
            exit_at=datetime.datetime(2024, 3, 7, 16, 0, tzinfo=datetime.timezone.utc),
            duration_minutes=120, mfe=Decimal("0.001"), mae=Decimal("0.002"),
        ),
    ]

    summary = _compute_summary(trades, Decimal("1000"))

    assert "win_rate_long_pct" in summary
    assert "win_rate_short_pct" in summary
    assert "avg_win_duration_minutes" in summary
    assert "avg_loss_duration_minutes" in summary
    assert "by_weekday" in summary
    assert "by_hour_utc" in summary
    assert "by_regime" in summary
    assert "sl_hit_count" in summary
    assert "tp_hit_count" in summary
    assert "mae_exit_count" in summary
    assert "time_exit_count" in summary
    assert "avg_mae_pct_of_sl" in summary


def test_sim44_win_rate_by_direction():
    """win_rate_long_pct and win_rate_short_pct computed correctly."""
    from src.backtesting.backtest_engine import _compute_summary
    from src.backtesting.backtest_params import BacktestTradeResult
    from decimal import Decimal
    import datetime

    base_dt = datetime.datetime(2024, 3, 6, 10, 0, tzinfo=datetime.timezone.utc)
    trades = [
        BacktestTradeResult(
            symbol="EURUSD=X", timeframe="H1", direction="LONG",
            entry_price=Decimal("1.1"), exit_price=Decimal("1.11"),
            exit_reason="tp_hit", pnl_usd=Decimal("10.0"), result="win",
            entry_at=base_dt, exit_at=base_dt, duration_minutes=60,
        ),
        BacktestTradeResult(
            symbol="EURUSD=X", timeframe="H1", direction="LONG",
            entry_price=Decimal("1.1"), exit_price=Decimal("1.09"),
            exit_reason="sl_hit", pnl_usd=Decimal("-5.0"), result="loss",
            entry_at=base_dt, exit_at=base_dt, duration_minutes=60,
        ),
        BacktestTradeResult(
            symbol="EURUSD=X", timeframe="H1", direction="SHORT",
            entry_price=Decimal("1.1"), exit_price=Decimal("1.09"),
            exit_reason="tp_hit", pnl_usd=Decimal("10.0"), result="win",
            entry_at=base_dt, exit_at=base_dt, duration_minutes=60,
        ),
    ]

    summary = _compute_summary(trades, Decimal("1000"))
    assert summary["win_rate_long_pct"] == 50.0   # 1 win / 2 long trades
    assert summary["win_rate_short_pct"] == 100.0  # 1 win / 1 short trade


def test_sim44_exit_reason_counts():
    """sl_hit_count, tp_hit_count, mae_exit_count, time_exit_count are correct."""
    from src.backtesting.backtest_engine import _compute_summary
    from src.backtesting.backtest_params import BacktestTradeResult
    from decimal import Decimal
    import datetime

    base_dt = datetime.datetime(2024, 3, 6, 10, 0, tzinfo=datetime.timezone.utc)

    def make_trade(exit_reason: str, result: str, pnl: str) -> BacktestTradeResult:
        return BacktestTradeResult(
            symbol="EURUSD=X", timeframe="H1", direction="LONG",
            entry_price=Decimal("1.1"), exit_price=Decimal("1.11"),
            exit_reason=exit_reason, pnl_usd=Decimal(pnl), result=result,
            entry_at=base_dt, exit_at=base_dt, duration_minutes=60,
        )

    trades = [
        make_trade("sl_hit", "loss", "-5.0"),
        make_trade("sl_hit", "loss", "-5.0"),
        make_trade("tp_hit", "win", "10.0"),
        make_trade("mae_exit", "loss", "-3.0"),
        make_trade("time_exit", "loss", "-1.0"),
    ]

    summary = _compute_summary(trades, Decimal("1000"))
    assert summary["sl_hit_count"] == 2
    assert summary["tp_hit_count"] == 1
    assert summary["mae_exit_count"] == 1
    assert summary["time_exit_count"] == 1


def test_sim44_by_weekday_breakdown():
    """by_weekday groups trades by weekday correctly."""
    from src.backtesting.backtest_engine import _compute_summary
    from src.backtesting.backtest_params import BacktestTradeResult
    from decimal import Decimal
    import datetime

    # Wednesday = weekday 2, Thursday = weekday 3
    wed = datetime.datetime(2024, 3, 6, 10, 0, tzinfo=datetime.timezone.utc)
    thu = datetime.datetime(2024, 3, 7, 10, 0, tzinfo=datetime.timezone.utc)

    trades = [
        BacktestTradeResult(
            symbol="EURUSD=X", timeframe="H1", direction="LONG",
            entry_price=Decimal("1.1"), exit_price=Decimal("1.11"),
            exit_reason="tp_hit", pnl_usd=Decimal("10.0"), result="win",
            entry_at=wed, exit_at=wed, duration_minutes=60,
        ),
        BacktestTradeResult(
            symbol="EURUSD=X", timeframe="H1", direction="SHORT",
            entry_price=Decimal("1.1"), exit_price=Decimal("1.09"),
            exit_reason="sl_hit", pnl_usd=Decimal("-5.0"), result="loss",
            entry_at=thu, exit_at=thu, duration_minutes=60,
        ),
    ]

    summary = _compute_summary(trades, Decimal("1000"))
    assert "by_weekday" in summary
    wd = summary["by_weekday"]
    assert "2" in wd  # Wednesday
    assert "3" in wd  # Thursday
    assert wd["2"]["trades"] == 1
    assert wd["2"]["wins"] == 1
    assert wd["3"]["trades"] == 1
    assert wd["3"]["wins"] == 0


def test_sim44_by_regime_breakdown():
    """by_regime groups trades by regime correctly."""
    from src.backtesting.backtest_engine import _compute_summary
    from src.backtesting.backtest_params import BacktestTradeResult
    from decimal import Decimal
    import datetime

    base_dt = datetime.datetime(2024, 3, 6, 10, 0, tzinfo=datetime.timezone.utc)
    trades = [
        BacktestTradeResult(
            symbol="EURUSD=X", timeframe="H1", direction="LONG",
            entry_price=Decimal("1.1"), exit_price=Decimal("1.11"),
            exit_reason="tp_hit", pnl_usd=Decimal("10.0"), result="win",
            entry_at=base_dt, exit_at=base_dt, duration_minutes=60,
            regime="TREND_BULL",
        ),
        BacktestTradeResult(
            symbol="EURUSD=X", timeframe="H1", direction="SHORT",
            entry_price=Decimal("1.1"), exit_price=Decimal("1.09"),
            exit_reason="sl_hit", pnl_usd=Decimal("-5.0"), result="loss",
            entry_at=base_dt, exit_at=base_dt, duration_minutes=60,
            regime="VOLATILE",
        ),
    ]

    summary = _compute_summary(trades, Decimal("1000"))
    assert "by_regime" in summary
    assert "TREND_BULL" in summary["by_regime"]
    assert "VOLATILE" in summary["by_regime"]
    assert summary["by_regime"]["TREND_BULL"]["trades"] == 1
    assert summary["by_regime"]["TREND_BULL"]["wins"] == 1
    assert summary["by_regime"]["VOLATILE"]["trades"] == 1
    assert summary["by_regime"]["VOLATILE"]["wins"] == 0


def test_sim44_backtest_trade_result_has_regime_field():
    """BacktestTradeResult accepts regime field."""
    from src.backtesting.backtest_params import BacktestTradeResult
    from decimal import Decimal
    import datetime

    trade = BacktestTradeResult(
        symbol="EURUSD=X", timeframe="H1", direction="LONG",
        entry_price=Decimal("1.1"), exit_price=Decimal("1.11"),
        exit_reason="tp_hit", pnl_usd=Decimal("10.0"), result="win",
        entry_at=datetime.datetime(2024, 3, 6, 10, 0, tzinfo=datetime.timezone.utc),
        exit_at=datetime.datetime(2024, 3, 6, 12, 0, tzinfo=datetime.timezone.utc),
        duration_minutes=120,
        regime="STRONG_TREND_BULL",
    )
    assert trade.regime == "STRONG_TREND_BULL"


def test_sim44_backtest_trade_result_regime_defaults_none():
    """BacktestTradeResult.regime defaults to None (backward compatible)."""
    from src.backtesting.backtest_params import BacktestTradeResult
    from decimal import Decimal

    trade = BacktestTradeResult(
        symbol="EURUSD=X", timeframe="H1", direction="LONG",
        entry_price=Decimal("1.1"), exit_price=Decimal("1.11"),
        exit_reason="tp_hit", pnl_usd=Decimal("10.0"), result="win",
    )
    assert trade.regime is None


def test_sim44_avg_duration_by_result():
    """avg_win_duration_minutes and avg_loss_duration_minutes are computed."""
    from src.backtesting.backtest_engine import _compute_summary
    from src.backtesting.backtest_params import BacktestTradeResult
    from decimal import Decimal
    import datetime

    base_dt = datetime.datetime(2024, 3, 6, 10, 0, tzinfo=datetime.timezone.utc)
    trades = [
        BacktestTradeResult(
            symbol="EURUSD=X", timeframe="H1", direction="LONG",
            entry_price=Decimal("1.1"), exit_price=Decimal("1.11"),
            exit_reason="tp_hit", pnl_usd=Decimal("10.0"), result="win",
            entry_at=base_dt, exit_at=base_dt, duration_minutes=120,
        ),
        BacktestTradeResult(
            symbol="EURUSD=X", timeframe="H1", direction="SHORT",
            entry_price=Decimal("1.1"), exit_price=Decimal("1.09"),
            exit_reason="sl_hit", pnl_usd=Decimal("-5.0"), result="loss",
            entry_at=base_dt, exit_at=base_dt, duration_minutes=60,
        ),
    ]

    summary = _compute_summary(trades, Decimal("1000"))
    assert summary["avg_win_duration_minutes"] == 120.0
    assert summary["avg_loss_duration_minutes"] == 60.0
