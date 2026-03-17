"""Tests for Correlation Engine."""

from unittest.mock import MagicMock

import pytest

from src.analysis.correlation_engine import (
    CorrelationEngine,
    _extract_macro_value,
    USD_QUOTE_PAIRS,
    USD_BASE_PAIRS,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_macro(indicator: str, value) -> MagicMock:
    item = MagicMock()
    item.indicator_name = indicator
    item.value = value
    return item


def make_instrument(symbol: str, market: str) -> MagicMock:
    inst = MagicMock()
    inst.symbol = symbol
    inst.market = market
    return inst


# ── _extract_macro_value ─────────────────────────────────────────────────────

class TestExtractMacroValue:

    def test_returns_float_for_existing_indicator(self):
        records = [make_macro("DXY", 103.5)]
        assert _extract_macro_value(records, "DXY") == pytest.approx(103.5)

    def test_returns_none_for_missing_indicator(self):
        records = [make_macro("VIX", 20.0)]
        assert _extract_macro_value(records, "DXY") is None

    def test_returns_none_for_empty_records(self):
        assert _extract_macro_value([], "DXY") is None

    def test_skips_none_value(self):
        records = [make_macro("DXY", None)]
        assert _extract_macro_value(records, "DXY") is None

    def test_skips_bad_value_returns_none(self):
        records = [make_macro("DXY", "not_a_number")]
        assert _extract_macro_value(records, "DXY") is None

    def test_returns_first_match(self):
        records = [make_macro("DXY", 103.0), make_macro("DXY", 105.0)]
        assert _extract_macro_value(records, "DXY") == pytest.approx(103.0)


# ── CorrelationEngine init ────────────────────────────────────────────────────

class TestCorrelationEngineInit:

    def test_extracts_dxy_vix_tnx(self):
        records = [
            make_macro("DXY", 103.5),
            make_macro("VIX", 18.0),
            make_macro("TNX", 4.3),
        ]
        inst = make_instrument("EURUSD=X", "forex")
        engine = CorrelationEngine(inst, records)
        assert engine.dxy == pytest.approx(103.5)
        assert engine.vix == pytest.approx(18.0)
        assert engine.tnx == pytest.approx(4.3)

    def test_extracts_funding_rates(self):
        records = [
            make_macro("FUNDING_RATE_BTC", 0.0005),
            make_macro("FUNDING_RATE_ETH", -0.0002),
            make_macro("FUNDING_RATE_SOL", 0.0001),
        ]
        inst = make_instrument("BTC/USDT", "crypto")
        engine = CorrelationEngine(inst, records)
        assert engine.funding_btc == pytest.approx(0.0005)
        assert engine.funding_eth == pytest.approx(-0.0002)
        assert engine.funding_sol == pytest.approx(0.0001)

    def test_none_values_when_no_records(self):
        inst = make_instrument("EURUSD=X", "forex")
        engine = CorrelationEngine(inst, [])
        assert engine.dxy is None
        assert engine.vix is None
        assert engine.tnx is None


# ── Market type helpers ───────────────────────────────────────────────────────

class TestMarketTypeHelpers:

    def test_is_forex(self):
        engine = CorrelationEngine(make_instrument("EURUSD=X", "forex"), [])
        assert engine._is_forex() is True
        assert engine._is_stock() is False
        assert engine._is_crypto() is False

    def test_is_stock(self):
        engine = CorrelationEngine(make_instrument("AAPL", "stocks"), [])
        assert engine._is_stock() is True
        assert engine._is_forex() is False

    def test_is_crypto_by_market(self):
        engine = CorrelationEngine(make_instrument("BTC/USDT", "crypto"), [])
        assert engine._is_crypto() is True

    def test_is_crypto_by_symbol_keyword(self):
        engine = CorrelationEngine(make_instrument("BTCUSDT", "unknown"), [])
        assert engine._is_crypto() is True

    def test_get_symbol_uppercase(self):
        engine = CorrelationEngine(make_instrument("eurusd=x", "forex"), [])
        assert engine._get_symbol() == "EURUSD=X"


# ── VIX modifier ─────────────────────────────────────────────────────────────

class TestVixModifier:

    def test_no_vix_returns_base_score(self):
        engine = CorrelationEngine(make_instrument("EURUSD=X", "forex"), [])
        engine.vix = None
        assert engine._vix_modifier(50.0) == pytest.approx(50.0)

    def test_extreme_vix_reduces_magnitude_by_40pct(self):
        engine = CorrelationEngine(make_instrument("EURUSD=X", "forex"), [])
        engine.vix = 36.0
        assert engine._vix_modifier(50.0) == pytest.approx(30.0)

    def test_elevated_vix_reduces_magnitude_by_20pct(self):
        engine = CorrelationEngine(make_instrument("EURUSD=X", "forex"), [])
        engine.vix = 27.0
        assert engine._vix_modifier(50.0) == pytest.approx(40.0)

    def test_normal_vix_unchanged(self):
        engine = CorrelationEngine(make_instrument("EURUSD=X", "forex"), [])
        engine.vix = 18.0
        assert engine._vix_modifier(50.0) == pytest.approx(50.0)

    def test_vix_modifier_preserves_sign(self):
        engine = CorrelationEngine(make_instrument("EURUSD=X", "forex"), [])
        engine.vix = 36.0
        assert engine._vix_modifier(-60.0) == pytest.approx(-36.0)


# ── Forex scoring ─────────────────────────────────────────────────────────────

class TestScoreForex:

    def test_strong_dxy_bearish_for_eurusd(self):
        records = [make_macro("DXY", 105.0)]
        inst = make_instrument("EURUSD=X", "forex")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score < 0

    def test_strong_dxy_bullish_for_usdjpy(self):
        records = [make_macro("DXY", 105.0)]
        inst = make_instrument("USDJPY=X", "forex")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score > 0

    def test_moderate_dxy_bearish_for_eurusd(self):
        records = [make_macro("DXY", 101.0)]
        inst = make_instrument("EURUSD=X", "forex")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score < 0

    def test_neutral_dxy_zero_for_eurusd(self):
        """DXY between 96-100: dxy_strength=0 → score=0."""
        records = [make_macro("DXY", 98.0)]
        inst = make_instrument("EURUSD=X", "forex")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score == pytest.approx(0.0)

    def test_weak_dxy_bullish_for_eurusd(self):
        records = [make_macro("DXY", 93.0)]
        inst = make_instrument("EURUSD=X", "forex")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score > 0

    def test_weak_dxy_bearish_for_usdjpy(self):
        records = [make_macro("DXY", 93.0)]
        inst = make_instrument("USDJPY=X", "forex")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score < 0

    def test_cross_pair_not_in_quote_or_base(self):
        """Cross pair (EURGBP) has no direct DXY impact → score stays at 0."""
        records = [make_macro("DXY", 105.0)]
        inst = make_instrument("EURGBP", "forex")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score == pytest.approx(0.0)

    def test_no_dxy_returns_zero_base(self):
        inst = make_instrument("EURUSD=X", "forex")
        engine = CorrelationEngine(inst, [])
        score = engine.calculate_correlation_score()
        assert score == pytest.approx(0.0)

    def test_forex_score_in_range(self):
        records = [make_macro("DXY", 110.0), make_macro("VIX", 40.0)]
        inst = make_instrument("EURUSD=X", "forex")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert -100.0 <= score <= 100.0

    def test_vix_modifier_applied_to_forex(self):
        """With extreme VIX, magnitude reduced by 40%."""
        records = [make_macro("DXY", 105.0), make_macro("VIX", 36.0)]
        inst = make_instrument("EURUSD=X", "forex")
        engine = CorrelationEngine(inst, records)
        score_with_vix = engine.calculate_correlation_score()

        records_no_vix = [make_macro("DXY", 105.0)]
        engine2 = CorrelationEngine(inst, records_no_vix)
        score_no_vix = engine2.calculate_correlation_score()

        assert abs(score_with_vix) < abs(score_no_vix)


# ── Stock scoring ─────────────────────────────────────────────────────────────

class TestScoreStock:

    def test_high_vix_bearish_stocks(self):
        records = [make_macro("VIX", 31.0)]
        inst = make_instrument("SPY", "stocks")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score < 0

    def test_moderate_vix_bearish_stocks(self):
        records = [make_macro("VIX", 27.0)]
        inst = make_instrument("AAPL", "stocks")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score < 0

    def test_mildly_elevated_vix_bearish_stocks(self):
        records = [make_macro("VIX", 22.0)]
        inst = make_instrument("SPY", "stocks")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score < 0

    def test_low_vix_mildly_bullish_stocks(self):
        records = [make_macro("VIX", 12.0)]
        inst = make_instrument("SPY", "stocks")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score > 0

    def test_very_high_tnx_bearish_stocks(self):
        records = [make_macro("TNX", 5.5)]
        inst = make_instrument("SPY", "stocks")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score < 0

    def test_high_tnx_bearish_stocks(self):
        records = [make_macro("TNX", 4.7)]
        inst = make_instrument("SPY", "stocks")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score < 0

    def test_moderate_tnx_bearish_stocks(self):
        records = [make_macro("TNX", 4.2)]
        inst = make_instrument("SPY", "stocks")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score < 0

    def test_low_tnx_bullish_stocks(self):
        records = [make_macro("TNX", 2.5)]
        inst = make_instrument("SPY", "stocks")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score > 0

    def test_stock_score_in_range(self):
        records = [make_macro("VIX", 35.0), make_macro("TNX", 6.0)]
        inst = make_instrument("SPY", "stocks")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert -100.0 <= score <= 100.0

    def test_no_macro_returns_zero_for_stocks(self):
        inst = make_instrument("SPY", "stocks")
        engine = CorrelationEngine(inst, [])
        score = engine.calculate_correlation_score()
        assert score == pytest.approx(0.0)

    def test_equities_market_recognized(self):
        inst = make_instrument("SPY", "equities")
        engine = CorrelationEngine(inst, [])
        assert engine._is_stock() is True


# ── Crypto scoring ────────────────────────────────────────────────────────────

class TestScoreCrypto:

    def test_high_vix_bearish_crypto(self):
        records = [make_macro("VIX", 36.0)]
        inst = make_instrument("BTC/USDT", "crypto")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score < 0

    def test_moderate_vix_bearish_crypto(self):
        records = [make_macro("VIX", 27.0)]
        inst = make_instrument("BTC/USDT", "crypto")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score < 0

    def test_low_vix_bullish_crypto(self):
        records = [make_macro("VIX", 12.0)]
        inst = make_instrument("BTC/USDT", "crypto")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score > 0

    def test_very_high_funding_bearish_btc(self):
        """Funding > 0.1% → very overbought → score -40."""
        records = [make_macro("FUNDING_RATE_BTC", 0.0015)]  # 0.15%
        inst = make_instrument("BTC/USDT", "crypto")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score < 0

    def test_high_funding_bearish_btc(self):
        records = [make_macro("FUNDING_RATE_BTC", 0.0008)]  # 0.08%
        inst = make_instrument("BTC/USDT", "crypto")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score < 0

    def test_moderate_funding_slight_bearish_btc(self):
        records = [make_macro("FUNDING_RATE_BTC", 0.00015)]  # 0.015%
        inst = make_instrument("BTC/USDT", "crypto")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score < 0

    def test_negative_funding_bullish_btc(self):
        records = [make_macro("FUNDING_RATE_BTC", -0.0008)]  # -0.08%
        inst = make_instrument("BTC/USDT", "crypto")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score > 0

    def test_mildly_negative_funding_slight_bullish(self):
        records = [make_macro("FUNDING_RATE_BTC", -0.00015)]  # -0.015%
        inst = make_instrument("BTC/USDT", "crypto")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score > 0

    def test_eth_uses_eth_funding(self):
        records = [
            make_macro("FUNDING_RATE_ETH", 0.0015),  # high = bearish
            make_macro("FUNDING_RATE_BTC", 0.0),
        ]
        inst = make_instrument("ETH/USDT", "crypto")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score < 0

    def test_sol_uses_sol_funding(self):
        records = [
            make_macro("FUNDING_RATE_SOL", -0.0008),  # negative = bullish
            make_macro("FUNDING_RATE_BTC", 0.0),
        ]
        inst = make_instrument("SOL/USDT", "crypto")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score > 0

    def test_unknown_crypto_uses_btc_funding_as_proxy(self):
        """For unknown crypto symbol, BTC funding used as proxy."""
        records = [make_macro("FUNDING_RATE_BTC", 0.0015)]
        inst = make_instrument("DOGE/USDT", "crypto")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert score < 0

    def test_crypto_score_in_range(self):
        records = [make_macro("VIX", 40.0), make_macro("FUNDING_RATE_BTC", 0.002)]
        inst = make_instrument("BTC/USDT", "crypto")
        engine = CorrelationEngine(inst, records)
        score = engine.calculate_correlation_score()
        assert -100.0 <= score <= 100.0


# ── Unknown market ────────────────────────────────────────────────────────────

class TestUnknownMarket:

    def test_unknown_market_returns_zero(self):
        inst = make_instrument("GOLD", "commodities")
        engine = CorrelationEngine(inst, [])
        score = engine.calculate_correlation_score()
        assert score == 0.0
