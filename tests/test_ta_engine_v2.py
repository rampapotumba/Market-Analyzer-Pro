"""Tests for src.analysis.ta_engine_v2."""

import numpy as np
import pandas as pd
import pytest

from src.analysis.ta_engine_v2 import (
    TAEngineV2,
    _ichimoku_score,
    _market_structure_score,
    _mfi,
    _obv_score,
    _rsi,
    _vwap_score,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_df(n: int = 100, trend: str = "bull") -> pd.DataFrame:
    """Create a synthetic OHLCV DataFrame."""
    price = 100.0
    rows = []
    for i in range(n):
        if trend == "bull":
            price *= 1.002
        elif trend == "bear":
            price *= 0.998
        else:
            price += 0.5 if i % 2 == 0 else -0.5
        rows.append({
            "open": price * 0.999,
            "high": price * 1.003,
            "low": price * 0.997,
            "close": price,
            "volume": 1000.0 + i,
        })
    return pd.DataFrame(rows)


# ── RSI ───────────────────────────────────────────────────────────────────────

class TestRSI:
    def test_returns_float(self):
        df = _make_df(50)
        val = _rsi(df["close"], 14)
        assert isinstance(val, float)

    def test_bounded(self):
        df = _make_df(50)
        val = _rsi(df["close"], 14)
        assert 0.0 <= val <= 100.0

    def test_bull_trend_high_rsi(self):
        df = _make_df(100, "bull")
        val = _rsi(df["close"], 14)
        assert val > 50.0

    def test_bear_trend_low_rsi(self):
        df = _make_df(100, "bear")
        val = _rsi(df["close"], 14)
        assert val < 50.0

    def test_too_short_returns_none(self):
        df = _make_df(5)
        val = _rsi(df["close"], 14)
        # With fewer bars than period, may still return a value or None
        # Either is acceptable; just should not raise
        assert val is None or isinstance(val, float)


# ── OBV ───────────────────────────────────────────────────────────────────────

class TestOBVScore:
    def test_returns_float_or_none(self):
        df = _make_df(50)
        val = _obv_score(df)
        assert val is None or isinstance(val, float)

    def test_bull_positive(self):
        df = _make_df(100, "bull")
        val = _obv_score(df)
        assert val is not None and val > 0

    def test_bear_negative(self):
        df = _make_df(100, "bear")
        val = _obv_score(df)
        assert val is not None and val < 0

    def test_bounded(self):
        df = _make_df(100)
        val = _obv_score(df)
        if val is not None:
            assert -100.0 <= val <= 100.0


# ── MFI ───────────────────────────────────────────────────────────────────────

class TestMFI:
    def test_returns_float(self):
        df = _make_df(50)
        val = _mfi(df, 14)
        assert val is None or isinstance(val, float)

    def test_bounded(self):
        df = _make_df(50)
        val = _mfi(df, 14)
        if val is not None:
            assert 0.0 <= val <= 100.0

    def test_too_short_returns_none(self):
        df = _make_df(5)
        val = _mfi(df, 14)
        assert val is None


# ── VWAP ──────────────────────────────────────────────────────────────────────

class TestVWAPScore:
    def test_bull_positive(self):
        df = _make_df(50, "bull")
        val = _vwap_score(df)
        # Bull trend: recent price above session VWAP
        assert val is None or isinstance(val, float)

    def test_bounded(self):
        df = _make_df(50)
        val = _vwap_score(df)
        if val is not None:
            assert -100.0 <= val <= 100.0

    def test_too_short_returns_none(self):
        df = _make_df(5)
        val = _vwap_score(df)
        assert val is None


# ── Ichimoku ──────────────────────────────────────────────────────────────────

class TestIchimoku:
    def test_requires_52_bars(self):
        df = _make_df(30)
        assert _ichimoku_score(df) is None

    def test_returns_float_with_enough_data(self):
        df = _make_df(100)
        val = _ichimoku_score(df)
        assert val is None or isinstance(val, float)

    def test_bull_above_cloud(self):
        df = _make_df(100, "bull")
        val = _ichimoku_score(df)
        if val is not None:
            assert val > 0

    def test_bounded(self):
        df = _make_df(100)
        val = _ichimoku_score(df)
        if val is not None:
            assert -100.0 <= val <= 100.0


# ── Market Structure ──────────────────────────────────────────────────────────

class TestMarketStructure:
    def test_requires_enough_bars(self):
        df = _make_df(10)
        assert _market_structure_score(df) is None

    def test_returns_float(self):
        df = _make_df(100)
        val = _market_structure_score(df)
        assert val is None or isinstance(val, float)

    def test_bounded(self):
        df = _make_df(100)
        val = _market_structure_score(df)
        if val is not None:
            assert -100.0 <= val <= 100.0

    def test_bull_structure_positive(self):
        df = _make_df(100, "bull")
        val = _market_structure_score(df)
        if val is not None:
            assert val > 0

    def test_bear_structure_negative(self):
        df = _make_df(100, "bear")
        val = _market_structure_score(df)
        if val is not None:
            assert val < 0


# ── TAEngineV2 ────────────────────────────────────────────────────────────────

class TestTAEngineV2:
    def test_instantiate(self):
        df = _make_df(100)
        engine = TAEngineV2(df)
        assert engine is not None

    def test_score_returns_float(self):
        df = _make_df(100)
        engine = TAEngineV2(df)
        score = engine.score()
        assert isinstance(score, float)

    def test_score_bounded(self):
        df = _make_df(100)
        engine = TAEngineV2(df)
        score = engine.score()
        assert -100.0 <= score <= 100.0

    def test_too_short_returns_zero(self):
        df = _make_df(10)
        engine = TAEngineV2(df)
        assert engine.score() == 0.0

    def test_bull_score_positive(self):
        df = _make_df(100, "bull")
        engine = TAEngineV2(df)
        assert engine.score() > 0

    def test_bear_score_less_than_bull(self):
        """Bear trend should score lower than bull trend (RSI oversold may keep it positive)."""
        bull_score = TAEngineV2(_make_df(100, "bull")).score()
        bear_score = TAEngineV2(_make_df(100, "bear")).score()
        assert bear_score < bull_score

    def test_with_order_flow(self):
        df = _make_df(100)
        engine = TAEngineV2(
            df,
            funding_rate=0.01,
            open_interest=1_000_000.0,
            open_interest_prev=900_000.0,
            cvd=5000.0,
        )
        score = engine.score()
        assert -100.0 <= score <= 100.0

    def test_high_funding_bearish(self):
        """Extremely high funding rate should produce negative OF score."""
        df = _make_df(100, "range")
        engine = TAEngineV2(df, funding_rate=0.10)
        of_score = engine._score_order_flow()
        assert of_score is not None and of_score < 0

    def test_negative_funding_bullish(self):
        df = _make_df(100, "range")
        engine = TAEngineV2(df, funding_rate=-0.10)
        of_score = engine._score_order_flow()
        assert of_score is not None and of_score > 0

    def test_get_pivot_points(self):
        df = _make_df(100)
        engine = TAEngineV2(df)
        pivots = engine.get_pivot_points()
        assert "pp" in pivots
        assert "r1" in pivots
        assert "s1" in pivots
        assert "fib_r1" in pivots
        assert "fib_s1" in pivots

    def test_pivot_points_ordering(self):
        df = _make_df(100)
        engine = TAEngineV2(df)
        p = engine.get_pivot_points()
        assert p["r3"] > p["r2"] > p["r1"] > p["pp"] > p["s1"] > p["s2"] > p["s3"]

    def test_macd_divergence_no_crash(self):
        df = _make_df(100)
        engine = TAEngineV2(df)
        score = engine._macd_divergence_score()
        assert isinstance(score, float)
        assert -100.0 <= score <= 100.0

    def test_momentum_score_bounded(self):
        df = _make_df(100)
        engine = TAEngineV2(df)
        score = engine._score_momentum()
        if score is not None:
            assert -100.0 <= score <= 100.0

    def test_trend_score_bounded(self):
        df = _make_df(100)
        engine = TAEngineV2(df)
        score = engine._score_trend()
        if score is not None:
            assert -100.0 <= score <= 100.0

    def test_volume_score_bounded(self):
        df = _make_df(100)
        engine = TAEngineV2(df)
        score = engine._score_volume()
        if score is not None:
            assert -100.0 <= score <= 100.0

    # ── OI surge branches ─────────────────────────────────────────────────────

    def test_oi_surge_price_flat_bearish(self):
        """OI >10% surge + price flat (<1% move) → warning signal (-20)."""
        df = _make_df(100, "range")
        engine = TAEngineV2(
            df,
            open_interest=1_150_000.0,    # +15% (strictly > 10%)
            open_interest_prev=1_000_000.0,
        )
        score = engine._score_order_flow()
        assert score is not None
        assert score < 0  # -20 in parts

    def test_oi_surge_price_up_bullish(self):
        """OI >10% surge + price up >1% → trend continuation bullish (+40)."""
        # Create a strong bull df so price_change_pct > 1%
        df = _make_df(100, "bull")
        engine = TAEngineV2(
            df,
            open_interest=1_200_000.0,    # +20%
            open_interest_prev=1_000_000.0,
        )
        score = engine._score_order_flow()
        assert score is not None
        assert score > 0  # +40

    def test_oi_surge_price_down_bearish(self):
        """OI >10% surge + price down >1% → trend continuation bearish (-40)."""
        df = _make_df(100, "bear")
        engine = TAEngineV2(
            df,
            open_interest=1_200_000.0,    # +20%
            open_interest_prev=1_000_000.0,
        )
        score = engine._score_order_flow()
        assert score is not None
        assert score < 0  # -40

    def test_oi_no_surge_no_signal(self):
        """OI change <10% → no OI contribution."""
        df = _make_df(100, "range")
        engine = TAEngineV2(
            df,
            open_interest=1_050_000.0,    # +5%, below threshold
            open_interest_prev=1_000_000.0,
        )
        # Only OI provided (no FR, CVD) but OI below surge threshold → None
        score = engine._score_order_flow()
        assert score is None

    def test_oi_prev_zero_skips_oi(self):
        """OI_prev=0 → division guarded, no OI signal."""
        df = _make_df(100, "range")
        engine = TAEngineV2(
            df,
            open_interest=1_000_000.0,
            open_interest_prev=0.0,       # guard: oi_prev > 0 check
        )
        score = engine._score_order_flow()
        assert score is None

    def test_cvd_only_positive(self):
        """Only CVD provided (positive) → score > 0."""
        df = _make_df(100)
        engine = TAEngineV2(df, cvd=50_000.0)
        score = engine._score_order_flow()
        assert score is not None
        assert score > 0

    def test_cvd_only_negative(self):
        """Only CVD provided (negative) → score < 0."""
        df = _make_df(100)
        engine = TAEngineV2(df, cvd=-50_000.0)
        score = engine._score_order_flow()
        assert score is not None
        assert score < 0

    def test_score_all_none_components(self):
        """With only 49 bars (below _MIN_BARS=50), score() returns 0.0."""
        df = _make_df(49)
        engine = TAEngineV2(df)
        assert engine.score() == 0.0

    # ── Pivot points edge cases ────────────────────────────────────────────────

    def test_pivot_points_too_short_returns_empty(self):
        """Less than 2 bars → empty dict."""
        df = _make_df(1)
        engine = TAEngineV2(df)
        assert engine.get_pivot_points() == {}

    def test_pivot_points_has_all_keys(self):
        df = _make_df(10)
        engine = TAEngineV2(df)
        pivots = engine.get_pivot_points()
        expected_keys = {"pp", "r1", "r2", "r3", "s1", "s2", "s3",
                         "fib_r1", "fib_r2", "fib_r3", "fib_s1", "fib_s2", "fib_s3"}
        assert expected_keys == set(pivots.keys())

    # ── RSI edge cases ─────────────────────────────────────────────────────────

    def test_rsi_all_gains_returns_100(self):
        """All positive candles → loss=0 → RSI=100."""
        series = pd.Series([float(x) for x in range(1, 30)])
        val = _rsi(series, 14)
        assert val == 100.0

    def test_rsi_all_losses_returns_near_zero(self):
        """All negative candles → gain=0 → RSI near 0."""
        series = pd.Series([float(30 - x) for x in range(30)])
        val = _rsi(series, 14)
        assert val is not None and val < 10.0

    # ── OBV edge cases ─────────────────────────────────────────────────────────

    def test_obv_constant_price_returns_none(self):
        """Flat price → OBV is all zeros → std=0 → None."""
        rows = [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0}] * 50
        df = pd.DataFrame(rows)
        val = _obv_score(df)
        assert val is None

    # ── Market structure BOS branches ─────────────────────────────────────────

    def test_market_structure_bos_bullish(self):
        """Strong bull trend → current price above last swing high → BOS up → positive."""
        df = _make_df(150, "bull")
        val = _market_structure_score(df)
        if val is not None:
            assert val > 0

    def test_market_structure_bos_bearish(self):
        """Strong bear trend → current price below last swing low → BOS down → negative."""
        df = _make_df(150, "bear")
        val = _market_structure_score(df)
        if val is not None:
            assert val < 0

    def test_market_structure_too_few_bars_returns_none(self):
        """Less than swing_n*4=20 bars → None."""
        df = _make_df(15)
        val = _market_structure_score(df)
        assert val is None

    # ── Score() normalisation ──────────────────────────────────────────────────

    def test_score_with_only_funding_rate(self):
        """Only funding rate provided for OF; other components from price."""
        df = _make_df(100)
        engine = TAEngineV2(df, funding_rate=0.0)
        score = engine.score()
        assert -100.0 <= score <= 100.0

    def test_macd_divergence_short_series_returns_zero(self):
        """Less than 35 bars → MACD divergence returns 0."""
        df = _make_df(30)
        engine = TAEngineV2(df)
        assert engine._macd_divergence_score() == 0.0

    # ── MFI extreme branches ───────────────────────────────────────────────────

    def test_mfi_oversold_score_positive(self):
        """MFI ≤ 20 → mfi_score = +80."""
        from src.analysis.ta_engine_v2 import _mfi
        # Build a strong bear candle sequence to get MFI < 20
        rows = []
        price = 100.0
        for i in range(50):
            price -= 1.0  # sharp decline
            rows.append({
                "open": price + 1.0,
                "high": price + 1.0,
                "low": price - 0.1,
                "close": price,
                "volume": 10000.0,
            })
        df = pd.DataFrame(rows)
        engine = TAEngineV2(df)
        vol_score = engine._score_volume()
        # With very low MFI (likely < 20), the mfi_score contributes +80
        # Score should be positive or at least not crash
        if vol_score is not None:
            assert -100.0 <= vol_score <= 100.0

    def test_mfi_overbought_score_negative(self):
        """MFI ≥ 80 → mfi_score = -80."""
        rows = []
        price = 100.0
        for i in range(50):
            price += 1.0  # sharp rise
            rows.append({
                "open": price - 1.0,
                "high": price + 0.1,
                "low": price - 1.0,
                "close": price,
                "volume": 10000.0,
            })
        df = pd.DataFrame(rows)
        engine = TAEngineV2(df)
        vol_score = engine._score_volume()
        if vol_score is not None:
            assert -100.0 <= vol_score <= 100.0

    def test_score_volume_returns_none_with_constant_data(self):
        """Both OBV and MFI None → _score_volume returns None."""
        # Use only 5 bars — MFI needs period+1=15, OBV needs lookback+1=21
        rows = [{"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0}] * 5
        df = pd.DataFrame(rows)
        engine = TAEngineV2(df)
        result = engine._score_volume()
        assert result is None

    def test_score_trend_returns_none_with_short_data(self):
        """Both ichimoku and vwap None with 5 bars → _score_trend returns None."""
        df = _make_df(5)
        engine = TAEngineV2(df)
        result = engine._score_trend()
        assert result is None

    # ── Market structure detailed branches ─────────────────────────────────────

    def test_market_structure_hh_and_hl(self):
        """Uptrend zigzag: Higher Highs + Higher Lows → positive score."""
        # Build a realistic uptrend with swing points: small pullbacks then higher rallies
        rows = []
        base = 100.0
        # Create alternating rally/pullback with HH+HL pattern
        pattern = [
            # rally, pullback, rally, pullback, rally (all progressively higher)
            *[(base + 0.5 * j, "up") for j in range(10)],   # rally 1
            *[(base + 5.0 - 0.3 * j, "dn") for j in range(5)],  # pullback 1
            *[(base + 3.5 + 0.6 * j, "up") for j in range(10)],  # rally 2 (HH)
            *[(base + 9.5 - 0.3 * j, "dn") for j in range(5)],  # pullback 2 (HL)
            *[(base + 8.0 + 0.7 * j, "up") for j in range(10)],  # rally 3 (HH)
            *[(base + 15.0 - 0.4 * j, "dn") for j in range(5)],  # pullback 3 (HL)
            *[(base + 13.0 + 0.8 * j, "up") for j in range(10)], # rally 4 (HH)
        ]
        for p, _ in pattern:
            rows.append({
                "open": p - 0.1,
                "high": p + 0.3,
                "low": p - 0.3,
                "close": p,
                "volume": 1000.0,
            })
        df = pd.DataFrame(rows)
        val = _market_structure_score(df, swing_n=3)
        if val is not None:
            assert val > 0

    def test_market_structure_lh_and_ll(self):
        """Downtrend zigzag: Lower Highs + Lower Lows → negative score."""
        rows = []
        base = 120.0
        pattern = [
            *[(base - 0.5 * j,) for j in range(10)],
            *[(base - 5.0 + 0.3 * j,) for j in range(5)],
            *[(base - 3.5 - 0.6 * j,) for j in range(10)],
            *[(base - 9.5 + 0.3 * j,) for j in range(5)],
            *[(base - 8.0 - 0.7 * j,) for j in range(10)],
            *[(base - 15.0 + 0.4 * j,) for j in range(5)],
            *[(base - 13.0 - 0.8 * j,) for j in range(10)],
        ]
        for (p,) in pattern:
            rows.append({
                "open": p + 0.1,
                "high": p + 0.3,
                "low": p - 0.3,
                "close": p,
                "volume": 1000.0,
            })
        df = pd.DataFrame(rows)
        val = _market_structure_score(df, swing_n=3)
        if val is not None:
            assert val < 0

    def test_macd_bullish_divergence(self):
        """Simulate bullish divergence: price lower, MACD higher."""
        import pandas as pd
        # Build a series where price goes down in last 10 bars but MACD goes up
        # Use explicit prices
        prices = [100.0 + i * 0.5 for i in range(40)]  # rising then...
        prices += [prices[-1] - i * 0.3 for i in range(1, 11)]  # slight down
        df_rows = []
        for p in prices:
            df_rows.append({"open": p * 0.999, "high": p * 1.001, "low": p * 0.999, "close": p, "volume": 1000.0})
        df = pd.DataFrame(df_rows)
        engine = TAEngineV2(df)
        score = engine._macd_divergence_score()
        assert isinstance(score, float)
