"""Tests for Technical Analysis Engine."""

import datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from src.analysis.ta_engine import TAEngine


class TestTAEngineBasic:
    """Test TAEngine initialization and indicator calculation."""

    def test_init_with_ohlcv_df(self, sample_ohlcv_df):
        """TAEngine should initialize correctly with OHLCV DataFrame."""
        engine = TAEngine(sample_ohlcv_df)
        assert engine.df is not None
        assert len(engine.df) == 200

    def test_normalize_columns_uppercase(self):
        """TAEngine should normalize column names to lowercase."""
        df = pd.DataFrame({
            "Open": [1.0, 1.1],
            "High": [1.2, 1.3],
            "Low": [0.9, 1.0],
            "Close": [1.1, 1.2],
            "Volume": [1000.0, 2000.0],
        })
        engine = TAEngine(df)
        assert "close" in engine.df.columns
        assert "Open" not in engine.df.columns

    def test_missing_required_column_raises(self):
        """TAEngine should raise ValueError if required column is missing."""
        df = pd.DataFrame({
            "open": [1.0, 1.1],
            "high": [1.2, 1.3],
            "close": [1.1, 1.2],
            # Missing 'low'
        })
        with pytest.raises(ValueError, match="missing column"):
            TAEngine(df)


class TestIndicatorCalculation:
    """Test individual indicator calculations."""

    def test_rsi_range(self, sample_ohlcv_df):
        """RSI should be between 0 and 100."""
        engine = TAEngine(sample_ohlcv_df)
        indicators = engine.calculate_all_indicators()
        rsi = indicators.get("rsi")
        assert rsi is not None
        assert 0 <= rsi <= 100

    def test_macd_calculated(self, sample_ohlcv_df):
        """MACD components should be calculated."""
        engine = TAEngine(sample_ohlcv_df)
        indicators = engine.calculate_all_indicators()
        assert "macd" in indicators
        assert "macd_signal" in indicators
        assert "macd_hist" in indicators
        # MACD values should be floats (can be negative)
        assert isinstance(indicators["macd"], (int, float))

    def test_bollinger_bands(self, sample_ohlcv_df):
        """Bollinger Bands should be ordered: lower < middle < upper."""
        engine = TAEngine(sample_ohlcv_df)
        indicators = engine.calculate_all_indicators()
        assert indicators["bb_lower"] < indicators["bb_middle"] < indicators["bb_upper"]

    def test_atr_positive(self, sample_ohlcv_df):
        """ATR should always be positive."""
        engine = TAEngine(sample_ohlcv_df)
        indicators = engine.calculate_all_indicators()
        atr = indicators.get("atr")
        assert atr is not None
        assert atr > 0

    def test_moving_averages(self, sample_ohlcv_df):
        """Moving averages should be calculated."""
        engine = TAEngine(sample_ohlcv_df)
        indicators = engine.calculate_all_indicators()
        assert indicators.get("sma20") is not None
        assert indicators.get("sma50") is not None
        assert indicators.get("ema12") is not None
        assert indicators.get("ema26") is not None

    def test_stochastic_range(self, sample_ohlcv_df):
        """Stochastic %K and %D should be between 0 and 100."""
        engine = TAEngine(sample_ohlcv_df)
        indicators = engine.calculate_all_indicators()
        k = indicators.get("stoch_k")
        d = indicators.get("stoch_d")
        if k is not None:
            assert 0 <= k <= 100
        if d is not None:
            assert 0 <= d <= 100

    def test_indicators_cached(self, sample_ohlcv_df):
        """Indicators should be cached after first calculation."""
        engine = TAEngine(sample_ohlcv_df)
        ind1 = engine.calculate_all_indicators()
        ind2 = engine.calculate_all_indicators()
        assert ind1 is ind2  # Same object (cached)


class TestTASignals:
    """Test TA signal generation."""

    def test_signals_have_required_keys(self, sample_ohlcv_df):
        """Each signal should have 'signal' and 'strength' keys."""
        engine = TAEngine(sample_ohlcv_df)
        signals = engine.generate_ta_signals()

        expected_indicators = [
            "rsi", "macd", "bollinger", "ma_cross", "adx",
            "stochastic", "volume", "support_resistance", "candle_patterns"
        ]
        for ind in expected_indicators:
            assert ind in signals, f"Missing signal for {ind}"
            assert "signal" in signals[ind], f"Missing 'signal' key for {ind}"
            assert "strength" in signals[ind], f"Missing 'strength' key for {ind}"

    def test_signal_direction_values(self, sample_ohlcv_df):
        """Signal direction should be -1, 0, or 1."""
        engine = TAEngine(sample_ohlcv_df)
        signals = engine.generate_ta_signals()
        for name, sig in signals.items():
            assert sig["signal"] in (-1, 0, 1), f"{name}: invalid signal {sig['signal']}"

    def test_signal_strength_range(self, sample_ohlcv_df):
        """Signal strength should be in [0, 1]."""
        engine = TAEngine(sample_ohlcv_df)
        signals = engine.generate_ta_signals()
        for name, sig in signals.items():
            assert 0 <= sig["strength"] <= 1.0, (
                f"{name}: strength {sig['strength']} out of range"
            )

    def test_rsi_oversold_bullish(self):
        """RSI < 30 should generate bullish signal."""
        n = 100
        # Create data where price drops significantly → RSI will be low
        prices = np.array([1.0 - i * 0.005 for i in range(n)])  # Strong downtrend
        prices = np.clip(prices, 0.1, None)
        df = pd.DataFrame({
            "open": prices * 1.001,
            "high": prices * 1.002,
            "low": prices * 0.998,
            "close": prices,
            "volume": np.full(n, 1000.0),
        })
        engine = TAEngine(df)
        indicators = engine.calculate_all_indicators()
        rsi = indicators.get("rsi")
        if rsi is not None and rsi < 30:
            signals = engine.generate_ta_signals()
            assert signals["rsi"]["signal"] == 1  # Oversold → bullish


class TestTAScore:
    """Test TA score calculation."""

    def test_score_range(self, sample_ohlcv_df):
        """TA score should be in [-100, +100]."""
        engine = TAEngine(sample_ohlcv_df)
        score = engine.calculate_ta_score()
        assert -100 <= score <= 100

    def test_bullish_trend_ma_signal_positive(self, trending_bullish_df):
        """Strongly bullish trend should produce positive MA crossover signal."""
        engine = TAEngine(trending_bullish_df)
        signals = engine.generate_ta_signals()
        # In a clear uptrend, MA cross and ADX should be bullish
        ma_signal = signals["ma_cross"]["signal"]
        adx_signal = signals["adx"]["signal"]
        assert ma_signal == 1, f"MA cross should be 1 in uptrend, got {ma_signal}"
        assert adx_signal == 1, f"ADX should be 1 in uptrend, got {adx_signal}"

    def test_bearish_trend_ma_signal_negative(self, trending_bearish_df):
        """Strongly bearish trend should produce negative MA crossover signal."""
        engine = TAEngine(trending_bearish_df)
        signals = engine.generate_ta_signals()
        # In a clear downtrend, MA cross and ADX should be bearish
        ma_signal = signals["ma_cross"]["signal"]
        adx_signal = signals["adx"]["signal"]
        assert ma_signal == -1, f"MA cross should be -1 in downtrend, got {ma_signal}"
        assert adx_signal == -1, f"ADX should be -1 in downtrend, got {adx_signal}"

    def test_get_atr_returns_decimal(self, sample_ohlcv_df):
        """get_atr() should return a Decimal."""
        engine = TAEngine(sample_ohlcv_df)
        atr = engine.get_atr(14)
        assert atr is not None
        assert isinstance(atr, Decimal)
        assert atr > Decimal("0")
