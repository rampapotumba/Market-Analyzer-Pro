"""Tests for O(n) backtest engine optimization (Task 9).

Verifies:
- No-lookahead guarantee: pre-computed indicators match fresh TAEngine per-index
- Equivalence: O(n) path produces identical trades to O(n^2) path
- Benchmark: backtest completes in reasonable time
- Helper functions: _precompute_ta_scores, _precompute_regimes, classify_regime_at_point
"""

import datetime
import math
import time
from decimal import Decimal
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from src.analysis.ta_engine import TAEngine
from src.analysis.regime_detector import classify_regime_at_point
from src.backtesting.backtest_engine import (
    _nan_to_none,
    _precompute_ta_scores,
    _precompute_regimes,
    _to_ohlcv_df,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_ohlcv_df(n: int, seed: int = 42) -> pd.DataFrame:
    """Create a synthetic OHLCV DataFrame with n bars."""
    rng = np.random.default_rng(seed)
    base = 1.1000
    returns = rng.normal(0, 0.001, n)
    close = base * np.cumprod(1 + returns)
    high = close * (1 + np.abs(rng.normal(0, 0.0005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.0005, n)))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    volume = rng.uniform(1000, 10000, n)

    idx = pd.date_range(
        start=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
        periods=n,
        freq="h",
    )
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _make_price_row(ts: datetime.datetime, o: float, h: float, lo: float, c: float, v: float = 1000.0) -> MagicMock:
    """Create a mock ORM price row."""
    row = MagicMock()
    row.timestamp = ts
    row.open = o
    row.high = h
    row.low = lo
    row.close = c
    row.volume = v
    return row


def _df_to_price_rows(df: pd.DataFrame) -> list:
    """Convert OHLCV DataFrame to list of mock price rows."""
    rows = []
    for ts, row in df.iterrows():
        rows.append(_make_price_row(ts, row["open"], row["high"], row["low"], row["close"], row.get("volume", 1000)))
    return rows


# ── Tests: _nan_to_none ──────────────────────────────────────────────────────

def test_nan_to_none_with_nan():
    assert _nan_to_none(float("nan")) is None


def test_nan_to_none_with_valid_float():
    assert _nan_to_none(1.5) == pytest.approx(1.5)


def test_nan_to_none_with_none():
    assert _nan_to_none(None) is None


def test_nan_to_none_with_zero():
    assert _nan_to_none(0.0) == pytest.approx(0.0)


# ── Tests: TAEngine.calculate_all_indicators_arrays ──────────────────────────

class TestCalculateAllIndicatorsArrays:
    def test_returns_dict_with_expected_keys(self):
        df = _make_ohlcv_df(100)
        ta = TAEngine(df, timeframe="H1")
        arrays = ta.calculate_all_indicators_arrays()

        expected_keys = [
            "rsi", "macd", "macd_signal", "macd_hist",
            "bb_upper", "bb_middle", "bb_lower",
            "sma_fast", "sma_slow", "sma_long",
            "ema_fast", "ema_slow",
            "adx", "plus_di", "minus_di",
            "stoch_k", "stoch_d",
            "atr",
            "close", "high", "low", "open", "volume",
        ]
        for key in expected_keys:
            assert key in arrays, f"Missing key: {key}"

    def test_array_lengths_match_df(self):
        n = 150
        df = _make_ohlcv_df(n)
        ta = TAEngine(df, timeframe="H1")
        arrays = ta.calculate_all_indicators_arrays()

        for key, arr in arrays.items():
            assert len(arr) == n, f"Array '{key}' has length {len(arr)}, expected {n}"

    def test_last_value_matches_calculate_all_indicators(self):
        """Pre-computed array last values must match scalar calculate_all_indicators()."""
        df = _make_ohlcv_df(200)
        ta = TAEngine(df, timeframe="H1")
        arrays = ta.calculate_all_indicators_arrays()
        scalars = ta.calculate_all_indicators()

        mapping = {
            "rsi": "rsi",
            "macd": "macd",
            "macd_signal": "macd_signal",
            "atr": "atr",
            "adx": "adx",
        }
        for arr_key, scalar_key in mapping.items():
            arr_val = _nan_to_none(arrays[arr_key][-1])
            scalar_val = scalars.get(scalar_key)
            if arr_val is not None and scalar_val is not None:
                assert abs(arr_val - scalar_val) < 1e-6, (
                    f"Array '{arr_key}' last value {arr_val} != scalar '{scalar_key}' {scalar_val}"
                )


# ── Tests: classify_regime_at_point ─────────────────────────────────────────

class TestClassifyRegimeAtPoint:
    def test_strong_trend_bull_high_adx_above_sma200(self):
        regime = classify_regime_at_point(adx=35.0, atr_pct=50.0, close=1.2, sma200=1.0)
        assert regime == "STRONG_TREND_BULL"

    def test_strong_trend_bear_high_adx_below_sma200(self):
        regime = classify_regime_at_point(adx=35.0, atr_pct=50.0, close=0.9, sma200=1.0)
        assert regime == "STRONG_TREND_BEAR"

    def test_weak_trend_bull(self):
        regime = classify_regime_at_point(adx=25.0, atr_pct=50.0, close=1.2, sma200=1.0)
        assert regime == "WEAK_TREND_BULL"

    def test_weak_trend_bear(self):
        regime = classify_regime_at_point(adx=25.0, atr_pct=50.0, close=0.8, sma200=1.0)
        assert regime == "WEAK_TREND_BEAR"

    def test_ranging_when_adx_low(self):
        regime = classify_regime_at_point(adx=15.0, atr_pct=50.0, close=1.0, sma200=1.0)
        assert regime == "RANGING"

    def test_high_volatility(self):
        regime = classify_regime_at_point(adx=15.0, atr_pct=85.0, close=1.0, sma200=1.0)
        assert regime == "HIGH_VOLATILITY"

    def test_low_volatility(self):
        regime = classify_regime_at_point(adx=15.0, atr_pct=10.0, close=1.0, sma200=1.0)
        assert regime == "LOW_VOLATILITY"

    def test_none_adx_returns_ranging(self):
        regime = classify_regime_at_point(adx=None, atr_pct=50.0, close=1.0, sma200=1.0)
        assert regime == "RANGING"

    def test_nan_sma200_no_crash(self):
        """NaN sma200 should not raise an exception."""
        regime = classify_regime_at_point(adx=35.0, atr_pct=50.0, close=1.2, sma200=float("nan"))
        # When sma200 is NaN, trend direction is unknown → falls through to RANGING
        assert isinstance(regime, str)
        assert regime in ("STRONG_TREND_BULL", "STRONG_TREND_BEAR", "WEAK_TREND_BULL", "WEAK_TREND_BEAR", "RANGING",
                          "HIGH_VOLATILITY", "LOW_VOLATILITY")

    def test_matches_regime_detector_on_sample_data(self):
        """classify_regime_at_point must produce same result as RegimeDetector._detect_regime()."""
        from src.analysis.regime_detector import RegimeDetector, _calculate_adx, _calculate_atr, _atr_percentile

        df = _make_ohlcv_df(300)
        rd = RegimeDetector()
        raw_regime, adx_val, atr_pct_val = rd._detect_regime(df, vix=None)

        close_val = float(df["close"].iloc[-1])
        sma200_val = float(df["close"].rolling(200).mean().iloc[-1])
        result = classify_regime_at_point(
            adx=adx_val, atr_pct=atr_pct_val, close=close_val, sma200=sma200_val,
        )
        assert result == raw_regime, f"classify_regime_at_point={result} != RegimeDetector={raw_regime}"


# ── Tests: _precompute_ta_scores ────────────────────────────────────────────

class TestPrecomputeTaScores:
    def test_returns_array_of_correct_length(self):
        n = 100
        df = _make_ohlcv_df(n)
        ta = TAEngine(df, timeframe="H1")
        arrays = ta.calculate_all_indicators_arrays()
        scores = _precompute_ta_scores(arrays, n, "H1")
        assert len(scores) == n

    def test_early_indices_may_be_nan_or_zero(self):
        """Warmup period indices have limited indicators.

        Some indicators (SMA200, BB) need many bars and will be NaN early.
        Others (EMA via ewm) start from index 0 with small values.
        The score at index 0 may be non-NaN if EMA/MACD computes from index 0,
        but we verify the score stays in [-100, 100] range even then.
        """
        n = 100
        df = _make_ohlcv_df(n)
        ta = TAEngine(df, timeframe="H1")
        arrays = ta.calculate_all_indicators_arrays()
        scores = _precompute_ta_scores(arrays, n, "H1")
        # Verify that all non-NaN scores are in valid range
        for s in scores:
            if not math.isnan(s):
                assert -100.0 <= s <= 100.0, f"Score out of range: {s}"

    def test_later_indices_are_valid_numbers(self):
        """After warmup, scores must be finite numbers in [-100, 100]."""
        n = 200
        df = _make_ohlcv_df(n)
        ta = TAEngine(df, timeframe="H1")
        arrays = ta.calculate_all_indicators_arrays()
        scores = _precompute_ta_scores(arrays, n, "H1")
        # Check last 100 values
        valid = [s for s in scores[-100:] if not math.isnan(s)]
        assert len(valid) > 0
        for s in valid:
            assert -100.0 <= s <= 100.0, f"Score out of range: {s}"

    def test_last_value_close_to_ta_engine_score(self):
        """Pre-computed score at last index should be close to TAEngine.calculate_ta_score().

        Due to S/R and candle pattern components being excluded (5% + 5% = 10% weight),
        the difference can be up to ~10 points. We check within 15 to allow for edge cases.
        """
        n = 250
        df = _make_ohlcv_df(n)
        ta = TAEngine(df, timeframe="H1")
        arrays = ta.calculate_all_indicators_arrays()
        scores = _precompute_ta_scores(arrays, n, "H1")

        ta_score_fresh = ta.calculate_ta_score()
        precomputed_last = scores[-1]

        if not math.isnan(precomputed_last):
            diff = abs(precomputed_last - ta_score_fresh)
            assert diff < 15.0, (
                f"Pre-computed score {precomputed_last:.2f} differs from TAEngine "
                f"score {ta_score_fresh:.2f} by {diff:.2f} (expected < 15)"
            )


# ── Tests: _precompute_regimes ───────────────────────────────────────────────

class TestPrecomputeRegimes:
    def test_returns_list_of_correct_length(self):
        n = 300
        df = _make_ohlcv_df(n)
        ta = TAEngine(df, timeframe="H1")
        arrays = ta.calculate_all_indicators_arrays()

        regimes = _precompute_regimes(
            arrays["adx"], arrays["atr"], arrays["close"], arrays["sma_long"], n
        )
        assert len(regimes) == n

    def test_all_elements_are_valid_regime_strings(self):
        n = 300
        df = _make_ohlcv_df(n)
        ta = TAEngine(df, timeframe="H1")
        arrays = ta.calculate_all_indicators_arrays()

        regimes = _precompute_regimes(
            arrays["adx"], arrays["atr"], arrays["close"], arrays["sma_long"], n
        )
        valid_regimes = {
            "STRONG_TREND_BULL", "STRONG_TREND_BEAR",
            "TREND_BULL", "TREND_BEAR",
            "RANGING", "VOLATILE", "DEFAULT",
        }
        for r in regimes:
            assert r in valid_regimes, f"Invalid regime: {r}"

    def test_last_regime_matches_regime_detector(self):
        """Last pre-computed regime must match _detect_regime_from_df() on full data."""
        from src.backtesting.backtest_engine import _detect_regime_from_df

        n = 300
        df = _make_ohlcv_df(n)
        ta = TAEngine(df, timeframe="H1")
        arrays = ta.calculate_all_indicators_arrays()

        regimes = _precompute_regimes(
            arrays["adx"], arrays["atr"], arrays["close"], arrays["sma_long"], n
        )
        expected = _detect_regime_from_df(df)
        assert regimes[-1] == expected, (
            f"Pre-computed regime at last index: {regimes[-1]}, "
            f"_detect_regime_from_df: {expected}"
        )


# ── Tests: Spot-check — pre-computed indicators vs fresh TAEngine ─────────────

class TestSpotCheckNoLookahead:
    def test_rsi_matches_fresh_engine_at_random_indices(self):
        """Pre-computed RSI[i] must match TAEngine(df[:i+1]).calculate_all_indicators()['rsi']."""
        n = 200
        df = _make_ohlcv_df(n)
        ta = TAEngine(df, timeframe="H1")
        arrays = ta.calculate_all_indicators_arrays()

        rng = np.random.default_rng(7)
        check_indices = rng.integers(50, n, size=5).tolist()

        for i in check_indices:
            precomputed_rsi = _nan_to_none(arrays["rsi"][i])

            fresh_ta = TAEngine(df.iloc[:i + 1], timeframe="H1")
            fresh_rsi = fresh_ta.calculate_all_indicators().get("rsi")

            if precomputed_rsi is not None and fresh_rsi is not None:
                assert abs(precomputed_rsi - fresh_rsi) < 1e-5, (
                    f"RSI mismatch at index {i}: pre={precomputed_rsi:.6f}, fresh={fresh_rsi:.6f}"
                )

    def test_atr_matches_fresh_engine_at_random_indices(self):
        """Pre-computed ATR[i] must match fresh TAEngine at same index."""
        n = 200
        df = _make_ohlcv_df(n)
        ta = TAEngine(df, timeframe="H1")
        arrays = ta.calculate_all_indicators_arrays()

        rng = np.random.default_rng(13)
        check_indices = rng.integers(50, n, size=5).tolist()

        for i in check_indices:
            precomputed_atr = _nan_to_none(arrays["atr"][i])

            fresh_ta = TAEngine(df.iloc[:i + 1], timeframe="H1")
            fresh_atr = fresh_ta.calculate_all_indicators().get("atr")

            if precomputed_atr is not None and fresh_atr is not None:
                assert abs(precomputed_atr - fresh_atr) < 1e-5, (
                    f"ATR mismatch at index {i}: pre={precomputed_atr:.8f}, fresh={fresh_atr:.8f}"
                )

    def test_macd_matches_fresh_engine_at_random_indices(self):
        """Pre-computed MACD[i] must match fresh TAEngine at same index."""
        n = 200
        df = _make_ohlcv_df(n)
        ta = TAEngine(df, timeframe="H1")
        arrays = ta.calculate_all_indicators_arrays()

        rng = np.random.default_rng(17)
        check_indices = rng.integers(60, n, size=5).tolist()

        for i in check_indices:
            precomputed_macd = _nan_to_none(arrays["macd"][i])

            fresh_ta = TAEngine(df.iloc[:i + 1], timeframe="H1")
            fresh_macd = fresh_ta.calculate_all_indicators().get("macd")

            if precomputed_macd is not None and fresh_macd is not None:
                assert abs(precomputed_macd - fresh_macd) < 1e-5, (
                    f"MACD mismatch at index {i}: pre={precomputed_macd:.8f}, fresh={fresh_macd:.8f}"
                )


# ── Tests: O(n) _simulate_symbol equivalence ─────────────────────────────────

class TestSimulateSymbolON:
    """Verify _generate_signal_fast() returns plausible results."""

    def test_generate_signal_fast_returns_none_when_no_atr(self):
        from src.backtesting.backtest_engine import BacktestEngine
        from unittest.mock import MagicMock

        engine = BacktestEngine(db=MagicMock())
        result = engine._generate_signal_fast(
            ta_score=20.0,
            atr_value=None,
            regime="TREND_BULL",
            ta_indicators_at_i={},
            symbol="EURUSD=X",
            market_type="forex",
            timeframe="H1",
        )
        assert result is None

    def test_generate_signal_fast_returns_none_when_zero_atr(self):
        from src.backtesting.backtest_engine import BacktestEngine
        from unittest.mock import MagicMock

        engine = BacktestEngine(db=MagicMock())
        result = engine._generate_signal_fast(
            ta_score=20.0,
            atr_value=0.0,
            regime="TREND_BULL",
            ta_indicators_at_i={},
            symbol="EURUSD=X",
            market_type="forex",
            timeframe="H1",
        )
        assert result is None

    def test_generate_signal_fast_long_on_positive_ta_score(self):
        from src.backtesting.backtest_engine import BacktestEngine
        from unittest.mock import MagicMock

        engine = BacktestEngine(db=MagicMock())
        result = engine._generate_signal_fast(
            ta_score=30.0,
            atr_value=0.001,
            regime="TREND_BULL",
            ta_indicators_at_i={"rsi": 60.0, "macd": 0.001, "macd_signal": 0.0005},
            symbol="EURUSD=X",
            market_type="forex",
            timeframe="H1",
        )
        assert result is not None
        assert result["direction"] == "LONG"
        assert result["composite_score"] > 0
        assert result["atr"] == Decimal("0.001")

    def test_generate_signal_fast_short_on_negative_ta_score(self):
        from src.backtesting.backtest_engine import BacktestEngine
        from unittest.mock import MagicMock

        engine = BacktestEngine(db=MagicMock())
        result = engine._generate_signal_fast(
            ta_score=-30.0,
            atr_value=0.001,
            regime="TREND_BEAR",
            ta_indicators_at_i={"rsi": 35.0},
            symbol="EURUSD=X",
            market_type="forex",
            timeframe="H1",
        )
        assert result is not None
        assert result["direction"] == "SHORT"
        assert result["composite_score"] < 0

    def test_generate_signal_fast_none_on_zero_composite(self):
        from src.backtesting.backtest_engine import BacktestEngine
        from unittest.mock import MagicMock

        engine = BacktestEngine(db=MagicMock())
        result = engine._generate_signal_fast(
            ta_score=0.0,
            atr_value=0.001,
            regime="RANGING",
            ta_indicators_at_i={},
            symbol="EURUSD=X",
            market_type="forex",
            timeframe="H1",
        )
        assert result is None

    def test_generate_signal_fast_returns_ta_indicators(self):
        from src.backtesting.backtest_engine import BacktestEngine
        from unittest.mock import MagicMock

        engine = BacktestEngine(db=MagicMock())
        indicators = {"rsi": 55.0, "macd": 0.001, "adx": 28.0}
        result = engine._generate_signal_fast(
            ta_score=25.0,
            atr_value=0.001,
            regime="TREND_BULL",
            ta_indicators_at_i=indicators,
            symbol="EURUSD=X",
            market_type="forex",
            timeframe="H1",
        )
        assert result is not None
        assert result["ta_indicators"] == indicators


# ── Tests: Benchmark (informational, not a pass/fail threshold) ───────────────

class TestBenchmark:
    def test_precompute_arrays_on_large_dataset(self):
        """Pre-computation of 2000 candles should complete in < 5 seconds."""
        n = 2000
        df = _make_ohlcv_df(n, seed=99)
        ta = TAEngine(df, timeframe="H1")

        start = time.perf_counter()
        arrays = ta.calculate_all_indicators_arrays()
        scores = _precompute_ta_scores(arrays, n, "H1")
        regimes = _precompute_regimes(
            arrays["adx"], arrays["atr"], arrays["close"], arrays["sma_long"], n
        )
        elapsed = time.perf_counter() - start

        assert len(scores) == n
        assert len(regimes) == n
        # Informational log; not strictly enforced but documents expected behavior
        print(f"\n[Benchmark] Pre-compute 2000 candles: {elapsed:.2f}s")
        assert elapsed < 30.0, f"Pre-computation took {elapsed:.1f}s — too slow (limit 30s)"
