"""Tests for Fundamental Analysis Engine."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.analysis.fa_engine import FAEngine


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_macro(indicator: str, value: float, prev=None) -> MagicMock:
    item = MagicMock()
    item.indicator_name = indicator
    item.value = Decimal(str(value))
    item.previous_value = Decimal(str(prev)) if prev is not None else None
    return item


def make_news(sentiment: float = 0.0, importance: str = "medium") -> MagicMock:
    item = MagicMock()
    item.sentiment_score = Decimal(str(sentiment))
    item.importance = importance
    return item


def make_instrument(symbol: str, market: str) -> MagicMock:
    inst = MagicMock()
    inst.symbol = symbol
    inst.market = market
    return inst


# ── Forex Fundamentals ────────────────────────────────────────────────────────

class TestForexFundamentals:
    """Test forex-specific fundamental analysis."""

    def test_fed_rate_hike_bearish_eurusd(self):
        """Rate hike strengthens USD, so EUR/USD (non-USD base) should score negative."""
        inst = make_instrument("EURUSD=X", "forex")
        macro = [make_macro("FEDFUNDS", 5.5, 5.25)]  # +0.25% hike
        score = FAEngine(inst, macro, []).calculate_fa_score()
        assert score < 0

    def test_fed_rate_cut_bullish_eurusd(self):
        """Rate cut weakens USD → positive for EUR/USD."""
        inst = make_instrument("EURUSD=X", "forex")
        macro = [make_macro("FEDFUNDS", 4.75, 5.00)]  # -0.25% cut
        score = FAEngine(inst, macro, []).calculate_fa_score()
        assert score > 0

    def test_unemployment_decrease_bearish_eurusd(self):
        """Lower unemployment = stronger USD = negative for EUR/USD."""
        inst = make_instrument("EURUSD=X", "forex")
        macro = [make_macro("UNRATE", 3.5, 4.0)]
        score = FAEngine(inst, macro, []).calculate_fa_score()
        assert score < 0

    def test_gdp_growth_bearish_eurusd(self):
        """Stronger US GDP = stronger USD = negative for EUR/USD."""
        inst = make_instrument("EURUSD=X", "forex")
        macro = [make_macro("GDPC1", 25000, 24000)]
        score = FAEngine(inst, macro, []).calculate_fa_score()
        assert score < 0

    def test_jpy_pair_reverses_direction(self):
        """Rate hike is bearish EUR/USD but bullish USD/JPY."""
        eur_inst = make_instrument("EURUSD=X", "forex")
        jpy_inst = make_instrument("USDJPY=X", "forex")
        macro = [make_macro("FEDFUNDS", 5.5, 5.25)]

        eur_score = FAEngine(eur_inst, macro, []).calculate_fa_score()
        jpy_score = FAEngine(jpy_inst, macro, []).calculate_fa_score()

        # JPY pair reverses sign
        assert eur_score < 0
        assert jpy_score > 0

    def test_gbp_pair_applies_scalar(self):
        """GBP pairs should produce a non-zero score when there is macro data."""
        inst = make_instrument("GBPUSD=X", "forex")
        macro = [make_macro("FEDFUNDS", 5.5, 5.25)]
        score = FAEngine(inst, macro, []).calculate_fa_score()
        assert score != 0.0

    def test_score_always_in_range(self):
        """Forex FA score must be in [-100, +100] for extreme values."""
        inst = make_instrument("EURUSD=X", "forex")
        macro = [
            make_macro("FEDFUNDS", 30.0, 0.0),
            make_macro("UNRATE", 0.01, 20.0),
        ]
        score = FAEngine(inst, macro, []).calculate_fa_score()
        assert -100.0 <= score <= 100.0

    def test_no_previous_value_skipped(self):
        """Indicators without previous value should be skipped (no delta)."""
        inst = make_instrument("EURUSD=X", "forex")
        macro = [make_macro("FEDFUNDS", 5.0, prev=None)]
        # No previous → no delta → score = 0
        engine = FAEngine(inst, macro, [])
        base = engine._analyze_forex_fundamentals()
        assert base == 0.0

    def test_empty_macro_returns_zero(self):
        """Empty macro list → zero base score."""
        inst = make_instrument("EURUSD=X", "forex")
        score = FAEngine(inst, [], []).calculate_fa_score()
        assert score == 0.0  # only news adjustment (also 0)


# ── Stock Fundamentals ────────────────────────────────────────────────────────

class TestStockFundamentals:
    """Test stock-specific fundamental analysis."""

    def test_gdp_growth_bullish_stocks(self):
        """GDP growth is positive for equities."""
        inst = make_instrument("SPY", "stocks")
        macro = [make_macro("GDPC1", 25000, 24000)]
        score = FAEngine(inst, macro, []).calculate_fa_score()
        assert score > 0

    def test_rate_hike_bearish_stocks(self):
        """Rate hike is bearish for stocks (higher borrowing cost)."""
        inst = make_instrument("SPY", "stocks")
        macro = [make_macro("FEDFUNDS", 5.5, 5.25)]
        score = FAEngine(inst, macro, []).calculate_fa_score()
        assert score < 0

    def test_unemployment_drop_bullish_stocks(self):
        """Lower unemployment is bullish for stocks."""
        inst = make_instrument("SPY", "stocks")
        macro = [make_macro("UNRATE", 3.5, 4.5)]
        score = FAEngine(inst, macro, []).calculate_fa_score()
        assert score > 0

    def test_stock_score_in_range(self):
        inst = make_instrument("SPY", "stocks")
        macro = [make_macro("GDPC1", 50000, 10000)]
        score = FAEngine(inst, macro, []).calculate_fa_score()
        assert -100.0 <= score <= 100.0

    def test_high_inflation_bearish_stocks(self):
        """CPI increase > 0.5% month-over-month is bearish for stocks."""
        inst = make_instrument("SPY", "stocks")
        macro = [make_macro("CPIAUCSL", 310.0, 300.0)]  # ~3.3% jump
        score = FAEngine(inst, macro, []).calculate_fa_score()
        assert score < 0


# ── Crypto Fundamentals ───────────────────────────────────────────────────────

class TestCryptoFundamentals:
    """Test crypto fundamental analysis (Phase 1 stub)."""

    def test_crypto_returns_zero_base(self):
        inst = make_instrument("BTC/USDT", "crypto")
        engine = FAEngine(inst, [], [])
        assert engine._analyze_crypto_fundamentals() == 0.0

    def test_crypto_score_zero_without_news(self):
        inst = make_instrument("BTC/USDT", "crypto")
        score = FAEngine(inst, [], []).calculate_fa_score()
        assert score == 0.0


# ── Unknown Market ────────────────────────────────────────────────────────────

class TestUnknownMarket:
    def test_unknown_market_returns_zero(self):
        inst = make_instrument("XYZ", "commodities")
        score = FAEngine(inst, [], []).calculate_fa_score()
        assert score == 0.0


# ── News Sentiment Adjustment ─────────────────────────────────────────────────

class TestNewsSentimentAdjustment:
    """Test the news sentiment contribution to FA score."""

    def test_no_news_adjustment_zero(self):
        inst = make_instrument("EURUSD=X", "forex")
        engine = FAEngine(inst, [], [])
        assert engine._news_sentiment_adjustment() == 0.0

    def test_positive_news_positive_adjustment(self):
        inst = make_instrument("EURUSD=X", "forex")
        news = [make_news(0.8, "high"), make_news(0.6, "medium")]
        engine = FAEngine(inst, [], news)
        adj = engine._news_sentiment_adjustment()
        assert adj > 0

    def test_negative_news_negative_adjustment(self):
        inst = make_instrument("EURUSD=X", "forex")
        news = [make_news(-0.9, "critical"), make_news(-0.5, "high")]
        engine = FAEngine(inst, [], news)
        adj = engine._news_sentiment_adjustment()
        assert adj < 0

    def test_news_contributes_to_final_score(self):
        """With neutral macro, only news adjustment drives the score."""
        inst = make_instrument("BTC/USDT", "crypto")
        news = [make_news(1.0, "critical")]
        score = FAEngine(inst, [], news).calculate_fa_score()
        assert score > 0

    def test_dict_news_items_supported(self):
        """News events provided as dicts should also work."""
        inst = make_instrument("EURUSD=X", "forex")
        news = [{"sentiment_score": 0.5, "importance": "medium"}]
        engine = FAEngine(inst, [], news)
        adj = engine._news_sentiment_adjustment()
        assert adj > 0
