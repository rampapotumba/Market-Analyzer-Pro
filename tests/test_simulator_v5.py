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
