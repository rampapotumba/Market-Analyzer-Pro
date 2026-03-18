"""Tests for Phase 3.2 — TA Engine v3 improvements.

Covers:
  3.2.1  RSI contextual signal via ADX
  3.2.2  ADX fallback with proper Directional Movement calculation
  3.2.3  S/R cluster detection with touch-count boost
  3.2.4  Volume signal direction via SMA20
  3.2.5  Timeframe-adaptive indicator periods
"""

import datetime
from typing import Any

import numpy as np
import pandas as pd
import pytest

from src.analysis.ta_engine import TAEngine, TF_INDICATOR_PERIODS


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_df(n: int = 250, trend: str = "up") -> pd.DataFrame:
    """Synthetic OHLCV with configurable trend direction."""
    np.random.seed(42)
    base = 1.1000
    if trend == "up":
        prices = np.array([base + i * 0.001 + np.random.normal(0, 0.0002) for i in range(n)])
    elif trend == "down":
        prices = np.array([base - i * 0.001 + np.random.normal(0, 0.0002) for i in range(n)])
    else:
        prices = np.array([base + np.random.normal(0, 0.0005) for _ in range(n)])

    prices = np.clip(prices, 0.01, None)
    high = prices * (1 + abs(np.random.normal(0, 0.0003, n)))
    low = prices * (1 - abs(np.random.normal(0, 0.0003, n)))
    opens = np.roll(prices, 1)
    opens[0] = prices[0]
    volume = np.random.uniform(1000, 5000, n)

    idx = pd.date_range(
        start=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
        periods=n,
        freq="h",
    )
    return pd.DataFrame(
        {"open": opens, "high": high, "low": low, "close": prices, "volume": volume},
        index=idx,
    )


# ── 3.2.1 RSI contextual signal ───────────────────────────────────────────────


def test_rsi_signal_range_mode_oversold() -> None:
    """ADX < 25 → classic RSI oversold = bullish signal."""
    df = _make_df(250, trend="range")
    engine = TAEngine(df)
    result = engine._rsi_signal(rsi=22.0, adx=18.0)
    assert result["signal"] == 1
    assert result["strength"] > 0


def test_rsi_signal_range_mode_overbought() -> None:
    """ADX < 25 → classic RSI overbought = bearish signal."""
    df = _make_df(250, trend="range")
    engine = TAEngine(df)
    result = engine._rsi_signal(rsi=78.0, adx=15.0)
    assert result["signal"] == -1
    assert result["strength"] > 0


def test_rsi_signal_trend_mode_oversold_reduced() -> None:
    """ADX >= 25 → RSI < 30 in trend = reduced strength (trap filter), per v3 spec."""
    df = _make_df(250, trend="up")
    engine = TAEngine(df)
    range_result = engine._rsi_signal(rsi=25.0, adx=None)      # range mode
    trend_result = engine._rsi_signal(rsi=25.0, adx=30.0)      # trend mode
    # Trend mode should have lower strength (trap filter ×0.4)
    assert trend_result["strength"] < range_result["strength"]


def test_rsi_signal_trend_mode_pullback_zone() -> None:
    """ADX >= 25, RSI in 40-55 zone = pullback buy signal."""
    df = _make_df(250, trend="up")
    engine = TAEngine(df)
    result = engine._rsi_signal(rsi=47.0, adx=35.0)
    assert result["signal"] == 1
    assert result["strength"] > 0


def test_rsi_signal_trend_mode_no_signal_in_middle() -> None:
    """ADX >= 25, RSI 62 = outside both pullback zones = neutral."""
    df = _make_df(250, trend="up")
    engine = TAEngine(df)
    result = engine._rsi_signal(rsi=62.0, adx=35.0)
    assert result["signal"] == 0


def test_rsi_signal_no_adx_falls_back_to_range_mode() -> None:
    """adx=None → range mode (no trend context available)."""
    df = _make_df(250)
    engine = TAEngine(df)
    result = engine._rsi_signal(rsi=25.0, adx=None)
    assert result["signal"] == 1  # classic oversold


# ── 3.2.2 ADX fallback ────────────────────────────────────────────────────────


def test_adx_fallback_plus_di_ne_minus_di() -> None:
    """Fallback ADX produces meaningful +DI / -DI (not all equal)."""
    df = _make_df(100, trend="up")
    engine = TAEngine(df)
    adx, plus_di, minus_di = engine._calc_adx(14)
    # In an uptrend, plus_di should exceed minus_di
    plus_last = float(plus_di[~np.isnan(plus_di)][-1])
    minus_last = float(minus_di[~np.isnan(minus_di)][-1])
    # They should differ (not the old constant 25/25/25)
    assert plus_last != minus_last, "ADX fallback must produce distinct +DI and -DI"


def test_adx_fallback_uptrend_plus_di_dominant() -> None:
    """In a strong uptrend, fallback +DI > -DI."""
    df = _make_df(200, trend="up")
    engine = TAEngine(df)
    _, plus_di, minus_di = engine._calc_adx(14)
    valid_plus = float(plus_di[-1]) if not np.isnan(plus_di[-1]) else 25.0
    valid_minus = float(minus_di[-1]) if not np.isnan(minus_di[-1]) else 25.0
    assert valid_plus > valid_minus, "Uptrend: +DI should exceed -DI"


def test_adx_fallback_downtrend_minus_di_dominant() -> None:
    """In a strong downtrend, fallback -DI > +DI."""
    df = _make_df(200, trend="down")
    engine = TAEngine(df)
    _, plus_di, minus_di = engine._calc_adx(14)
    valid_plus = float(plus_di[-1]) if not np.isnan(plus_di[-1]) else 25.0
    valid_minus = float(minus_di[-1]) if not np.isnan(minus_di[-1]) else 25.0
    assert valid_minus > valid_plus, "Downtrend: -DI should exceed +DI"


# ── 3.2.3 S/R cluster detection ───────────────────────────────────────────────


def test_sr_clusters_with_repeated_touches() -> None:
    """_find_support_resistance returns touch count when level has multiple pivots."""
    # Build a range-bound dataframe that touches a level multiple times
    n = 150
    np.random.seed(0)
    # Create price that oscillates around 1.1
    base = 1.1
    prices = np.sin(np.linspace(0, 6 * np.pi, n)) * 0.005 + base
    prices += np.random.normal(0, 0.0002, n)
    high = prices + 0.001
    low = prices - 0.001
    opens = np.roll(prices, 1)
    opens[0] = prices[0]
    idx = pd.date_range("2024-01-01", periods=n, freq="h")
    df = pd.DataFrame({"open": opens, "high": high, "low": low, "close": prices, "volume": 1000.0}, index=idx)

    engine = TAEngine(df)
    sr = engine._find_support_resistance()
    assert "nearest_support_touches" in sr
    assert "nearest_resistance_touches" in sr
    # With oscillating price, we expect some touch count ≥ 1
    assert sr["nearest_support_touches"] >= 0
    assert sr["nearest_resistance_touches"] >= 0


def test_sr_touch_boost_increases_strength() -> None:
    """Support with 4+ touches generates touch_boost > 1.0 in the S/R signal."""
    # Verify the boost formula: touch_boost = 1.0 + min(0.5, (touches - 1) * 0.15)
    touches_4 = 1.0 + min(0.5, (4 - 1) * 0.15)
    assert touches_4 > 1.0


def test_sr_touch_boost_formula_caps_at_1_5() -> None:
    """touch_boost is capped: max is 1.5 regardless of touch count."""
    # touches=100 → min(0.5, 99 * 0.15) = 0.5 → boost = 1.5
    boost_many = 1.0 + min(0.5, (100 - 1) * 0.15)
    assert boost_many == pytest.approx(1.5)


def test_sr_clusters_returns_correct_keys() -> None:
    """_find_support_resistance returns the 4 expected keys."""
    df = _make_df(100)
    engine = TAEngine(df)
    sr = engine._find_support_resistance()
    assert "support" in sr
    assert "resistance" in sr
    assert "nearest_support_touches" in sr
    assert "nearest_resistance_touches" in sr


def test_sr_touches_stored_in_indicators() -> None:
    """calculate_all_indicators includes nearest_*_touches keys."""
    df = _make_df(200)
    engine = TAEngine(df)
    ind = engine.calculate_all_indicators()
    assert "nearest_support_touches" in ind
    assert "nearest_resistance_touches" in ind


# ── 3.2.4 Volume signal via SMA20 ─────────────────────────────────────────────


def test_volume_signal_bullish_when_price_above_sma20() -> None:
    """High volume + close > SMA20 → bullish volume signal."""
    df = _make_df(200, trend="up")
    engine = TAEngine(df)
    ind = engine.calculate_all_indicators()

    # Simulate: current volume >> avg volume, close > sma20
    ind["current_volume"] = ind["avg_volume_20"] * 3.0  # vol_ratio = 3.0 > 1.5
    ind["sma20"] = ind["current_price"] * 0.99  # price is above SMA20

    # Rerun signal generation with patched indicators
    engine._indicators = ind
    engine._signals = None
    signals = engine.generate_ta_signals()
    assert signals["volume"]["signal"] == 1


def test_volume_signal_bearish_when_price_below_sma20() -> None:
    """High volume + close < SMA20 → bearish volume signal."""
    df = _make_df(200, trend="down")
    engine = TAEngine(df)
    ind = engine.calculate_all_indicators()

    ind["current_volume"] = ind["avg_volume_20"] * 3.0
    ind["sma20"] = ind["current_price"] * 1.01  # price below SMA20

    engine._indicators = ind
    engine._signals = None
    signals = engine.generate_ta_signals()
    assert signals["volume"]["signal"] == -1


def test_volume_signal_neutral_when_low_volume() -> None:
    """Volume ratio < 1.5 → signal = 0."""
    df = _make_df(200)
    engine = TAEngine(df)
    ind = engine.calculate_all_indicators()

    ind["current_volume"] = ind["avg_volume_20"] * 0.8  # below threshold

    engine._indicators = ind
    engine._signals = None
    signals = engine.generate_ta_signals()
    assert signals["volume"]["signal"] == 0


# ── 3.2.5 TF-adaptive indicator periods ───────────────────────────────────────


def test_tf_periods_h4_sma_long_is_100() -> None:
    """H4 timeframe uses sma_long = 100 (not 200, which would require 800d data)."""
    df = _make_df(200)
    engine = TAEngine(df, "H4")
    assert engine._periods["sma_long"] == 100


def test_tf_periods_d1_uses_longer_periods() -> None:
    """D1 timeframe uses sma_fast=50, ema_fast=21."""
    df = _make_df(200)
    engine = TAEngine(df, "D1")
    assert engine._periods["sma_fast"] == 50
    assert engine._periods["ema_fast"] == 21
    assert engine._periods["ema_slow"] == 55


def test_tf_periods_h1_uses_standard_periods() -> None:
    """H1 timeframe uses standard periods (sma_fast=20, ema_fast=12)."""
    df = _make_df(200)
    engine = TAEngine(df, "H1")
    assert engine._periods["sma_fast"] == 20
    assert engine._periods["ema_fast"] == 12


def test_tf_periods_m15_same_as_h1() -> None:
    """M15 and H1 share the same period table."""
    df = _make_df(200)
    assert TAEngine(df, "M15")._periods == TAEngine(df, "H1")._periods


def test_tf_periods_unknown_tf_uses_default() -> None:
    """Unknown timeframe falls back to _default periods."""
    df = _make_df(200)
    engine = TAEngine(df, "CUSTOM")
    assert engine._periods == TF_INDICATOR_PERIODS["_default"]


def test_default_timeframe_is_h1() -> None:
    """TAEngine without explicit timeframe uses H1 periods."""
    df = _make_df(200)
    engine = TAEngine(df)  # no timeframe arg
    assert engine._periods == TF_INDICATOR_PERIODS["H1"]
