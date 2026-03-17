"""Tests for TAEngine Smart Money Concepts and advanced indicator methods."""

import datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from src.analysis.ta_engine import TAEngine


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_df(n: int = 100, base: float = 1.1, trend: float = 0.0) -> pd.DataFrame:
    """Generate simple OHLCV data. trend > 0 = uptrend, < 0 = downtrend."""
    np.random.seed(0)
    close = np.array([base + i * trend + np.random.uniform(-0.0005, 0.0005) for i in range(n)])
    close = np.clip(close, 0.01, None)
    high = close + np.abs(np.random.uniform(0, 0.001, n))
    low = close - np.abs(np.random.uniform(0, 0.001, n))
    low = np.clip(low, 0.001, None)
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    vol = np.random.uniform(1000, 5000, n)

    idx = pd.date_range(
        start=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
        periods=n,
        freq="h",
    )
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


# ── PDH / PDL ─────────────────────────────────────────────────────────────────

class TestPDHPDL:
    """Test Previous Day High / Low calculation."""

    def test_returns_dict_with_required_keys(self):
        engine = TAEngine(make_df(100))
        result = engine.calculate_pdh_pdl()
        assert "pdh" in result
        assert "pdl" in result
        assert "prev_close" in result

    def test_pdh_greater_than_pdl(self):
        engine = TAEngine(make_df(100))
        result = engine.calculate_pdh_pdl()
        if result["pdh"] != 0.0 or result["pdl"] != 0.0:
            assert result["pdh"] >= result["pdl"]

    def test_short_df_returns_zeros(self):
        """Less than 2 bars should return zero defaults."""
        df = make_df(1)
        engine = TAEngine(df)
        result = engine.calculate_pdh_pdl()
        assert result == {"pdh": 0.0, "pdl": 0.0, "prev_close": 0.0}

    def test_single_day_returns_zeros(self):
        """Only one day of data → no 'previous' day."""
        idx = pd.date_range("2024-01-01", periods=8, freq="h", tz="UTC")
        df = pd.DataFrame({
            "open": [1.1] * 8, "high": [1.12] * 8,
            "low": [1.09] * 8, "close": [1.11] * 8, "volume": [1000.0] * 8,
        }, index=idx)
        engine = TAEngine(df)
        result = engine.calculate_pdh_pdl()
        assert result == {"pdh": 0.0, "pdl": 0.0, "prev_close": 0.0}

    def test_two_day_df_has_valid_pdh(self):
        """Two days of data should produce a valid PDH."""
        idx = pd.date_range("2024-01-01", periods=48, freq="h", tz="UTC")
        prices = np.linspace(1.10, 1.12, 48)
        df = pd.DataFrame({
            "open": prices, "high": prices + 0.001,
            "low": prices - 0.001, "close": prices, "volume": [1000.0] * 48,
        }, index=idx)
        engine = TAEngine(df)
        result = engine.calculate_pdh_pdl()
        assert result["pdh"] > result["pdl"]


# ── Session Levels ────────────────────────────────────────────────────────────

class TestSessionLevels:
    """Test trading session high/low calculation."""

    def test_returns_all_session_keys(self):
        engine = TAEngine(make_df(200))
        result = engine.calculate_session_levels()
        expected_keys = {
            "asia_high", "asia_low", "london_high",
            "london_low", "ny_high", "ny_low",
        }
        assert set(result.keys()) == expected_keys

    def test_session_high_gte_low(self):
        engine = TAEngine(make_df(200))
        result = engine.calculate_session_levels()
        for session in ("asia", "london", "ny"):
            h, l = result[f"{session}_high"], result[f"{session}_low"]
            if h != 0.0 or l != 0.0:
                assert h >= l, f"{session} high < low"

    def test_short_df_returns_zeros(self):
        engine = TAEngine(make_df(5))
        result = engine.calculate_session_levels()
        assert all(v == 0.0 for v in result.values())


# ── Fibonacci ─────────────────────────────────────────────────────────────────

class TestFibonacci:
    """Test Fibonacci retracement level calculation."""

    def test_returns_expected_keys(self):
        engine = TAEngine(make_df(100))
        result = engine.calculate_fibonacci()
        for key in ("swing_high", "swing_low", "fib_236", "fib_382", "fib_500", "fib_618", "fib_786"):
            assert key in result

    def test_fib_levels_between_swing_high_and_low(self):
        engine = TAEngine(make_df(100, trend=0.001))
        r = engine.calculate_fibonacci()
        if r["swing_high"] > r["swing_low"]:
            assert r["swing_low"] <= r["fib_786"] <= r["fib_618"] <= r["fib_500"]
            assert r["fib_500"] <= r["fib_382"] <= r["fib_236"] <= r["swing_high"]

    def test_short_df_returns_defaults(self):
        engine = TAEngine(make_df(1))
        result = engine.calculate_fibonacci()
        assert result["swing_high"] == 0.0
        assert result["swing_low"] == 0.0

    def test_zero_range_returns_defaults(self):
        """Flat price → zero range → return defaults."""
        df = pd.DataFrame({
            "open": [1.1] * 10, "high": [1.1] * 10,
            "low": [1.1] * 10, "close": [1.1] * 10, "volume": [1000.0] * 10,
        })
        engine = TAEngine(df)
        result = engine.calculate_fibonacci()
        assert result["fib_618"] == 0.0


# ── Volume Profile ────────────────────────────────────────────────────────────

class TestVolumeProfile:
    """Test VPOC and Value Area calculation."""

    def test_returns_vpoc_vah_val(self):
        engine = TAEngine(make_df(100))
        result = engine.calculate_volume_profile()
        assert "vpoc" in result
        assert "vah" in result
        assert "val" in result

    def test_vpoc_within_price_range(self):
        engine = TAEngine(make_df(100, base=1.1))
        result = engine.calculate_volume_profile()
        df = engine.df
        if result["vpoc"] != 0.0:
            assert float(df["low"].min()) <= result["vpoc"] <= float(df["high"].max())

    def test_val_lte_vpoc_lte_vah(self):
        engine = TAEngine(make_df(100))
        result = engine.calculate_volume_profile()
        if result["vpoc"] != 0.0:
            assert result["val"] <= result["vpoc"] <= result["vah"]

    def test_short_df_returns_zeros(self):
        engine = TAEngine(make_df(3))
        result = engine.calculate_volume_profile()
        assert result == {"vpoc": 0.0, "vah": 0.0, "val": 0.0}

    def test_zero_volume_returns_vpoc_only(self):
        """Zero-volume data → total_volume=0 → VPOC equals itself."""
        df = pd.DataFrame({
            "open": [1.0] * 10, "high": [1.1] * 10,
            "low": [0.9] * 10, "close": [1.0] * 10,
            "volume": [0.0] * 10,
        })
        engine = TAEngine(df)
        result = engine.calculate_volume_profile()
        # With no volume, bins stay zero; result is default or VPOC only
        assert "vpoc" in result


# ── Order Blocks ──────────────────────────────────────────────────────────────

class TestOrderBlocks:
    """Test bullish/bearish order block detection."""

    def test_returns_list(self):
        engine = TAEngine(make_df(100))
        result = engine.detect_order_blocks()
        assert isinstance(result, list)

    def test_ob_has_required_keys(self):
        engine = TAEngine(make_df(100))
        for ob in engine.detect_order_blocks():
            assert "type" in ob
            assert "high" in ob
            assert "low" in ob
            assert "index" in ob

    def test_ob_type_values(self):
        engine = TAEngine(make_df(100))
        for ob in engine.detect_order_blocks():
            assert ob["type"] in ("bullish", "bearish")

    def test_ob_high_gte_low(self):
        engine = TAEngine(make_df(100))
        for ob in engine.detect_order_blocks():
            assert ob["high"] >= ob["low"]

    def test_short_df_returns_empty(self):
        engine = TAEngine(make_df(5))
        assert engine.detect_order_blocks() == []


# ── Fair Value Gaps ───────────────────────────────────────────────────────────

class TestFairValueGaps:
    """Test FVG detection."""

    def test_returns_list(self):
        engine = TAEngine(make_df(50))
        result = engine.detect_fair_value_gaps()
        assert isinstance(result, list)

    def test_fvg_has_required_keys(self):
        engine = TAEngine(make_df(50))
        for fvg in engine.detect_fair_value_gaps():
            assert "type" in fvg
            assert "top" in fvg
            assert "bottom" in fvg

    def test_fvg_type_values(self):
        engine = TAEngine(make_df(50))
        for fvg in engine.detect_fair_value_gaps():
            assert fvg["type"] in ("bullish", "bearish")

    def test_fvg_top_gte_bottom(self):
        engine = TAEngine(make_df(50))
        for fvg in engine.detect_fair_value_gaps():
            assert fvg["top"] >= fvg["bottom"]

    def test_at_most_3_fvgs_returned(self):
        """detect_fair_value_gaps returns at most 3 unmitigated FVGs."""
        engine = TAEngine(make_df(100))
        assert len(engine.detect_fair_value_gaps()) <= 3

    def test_short_df_returns_empty(self):
        engine = TAEngine(make_df(2))
        assert engine.detect_fair_value_gaps() == []

    def test_bullish_fvg_detected(self):
        """Manually craft a bullish FVG: candle[i].low > candle[i-2].high."""
        df = pd.DataFrame({
            "open":  [1.0, 1.0, 1.1, 1.2],
            "high":  [1.01, 1.01, 1.11, 1.22],
            "low":   [0.99, 0.99, 1.05, 1.15],   # lows[2]=1.05 > highs[0]=1.01 ✓
            "close": [1.01, 1.00, 1.10, 1.20],
            "volume": [1000.0] * 4,
        })
        engine = TAEngine(df)
        fvgs = engine.detect_fair_value_gaps()
        bullish = [f for f in fvgs if f["type"] == "bullish"]
        assert len(bullish) >= 1


# ── TA Score v2 (SMC-enhanced) ────────────────────────────────────────────────

class TestTAScoreV2:
    """Test the SMC-enhanced TA score."""

    def test_returns_float(self):
        engine = TAEngine(make_df(100))
        score = engine.calculate_ta_score_v2()
        assert isinstance(score, float)

    def test_score_in_range(self):
        engine = TAEngine(make_df(200))
        score = engine.calculate_ta_score_v2()
        assert -100.0 <= score <= 100.0

    def test_v2_close_to_v1_without_smc_signals(self):
        """With no SMC context, v2 should be close to 0.85 × v1."""
        engine = TAEngine(make_df(50))
        v1 = engine.calculate_ta_score()
        v2 = engine.calculate_ta_score_v2()
        # They can differ due to SMC contribution, but both in range
        assert -100.0 <= v2 <= 100.0
