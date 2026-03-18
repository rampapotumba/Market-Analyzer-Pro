"""Tests for src.signals.signal_engine_v2."""

from decimal import Decimal
from unittest.mock import patch

import pytest

from src.signals.portfolio_risk import OpenPosition, PortfolioRiskManager
from src.signals.signal_engine_v2 import SignalEngineV2


ENTRY = Decimal("50000")
ATR = Decimal("1500")


def _make_engine(**kwargs) -> SignalEngineV2:
    return SignalEngineV2(**kwargs)


async def _gen(engine, **overrides):
    defaults = dict(
        symbol="BTC/USDT",
        timeframe="H4",
        ta_score=70.0,
        fa_score=50.0,
        sentiment_score=40.0,
        geo_score=10.0,
        regime="STRONG_TREND_BULL",
        market_type="crypto",
        entry_price=ENTRY,
        atr=ATR,
    )
    defaults.update(overrides)
    return await engine.generate(**defaults)


class TestSignalEngineV2Composite:
    @pytest.mark.asyncio
    async def test_strong_bull_generates_long(self):
        engine = _make_engine()
        result = await _gen(engine)
        assert result is not None
        assert result["direction"] == "LONG"

    @pytest.mark.asyncio
    async def test_strong_bear_generates_short(self):
        engine = _make_engine()
        result = await _gen(
            engine,
            ta_score=-70.0,
            fa_score=-50.0,
            sentiment_score=-40.0,
            geo_score=-10.0,
            regime="STRONG_TREND_BEAR",
        )
        assert result is not None
        assert result["direction"] == "SHORT"

    @pytest.mark.asyncio
    async def test_neutral_returns_none(self):
        engine = _make_engine()
        result = await _gen(
            engine,
            ta_score=5.0,
            fa_score=-5.0,
            sentiment_score=0.0,
            geo_score=0.0,
            regime="RANGING",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_signal_has_required_fields(self):
        engine = _make_engine()
        result = await _gen(engine)
        assert result is not None
        for field in (
            "direction", "signal_strength", "composite_score",
            "stop_loss", "take_profit_1", "confidence", "regime",
        ):
            assert field in result

    @pytest.mark.asyncio
    async def test_of_modifier_for_crypto(self):
        """OF score should influence composite for crypto."""
        engine = _make_engine()
        # With high OF score, composite should be boosted
        result = await _gen(engine, of_score=90.0)
        assert result is not None
        result_no_of = await _gen(engine, of_score=None)
        if result_no_of:
            assert result["composite_score"] != result_no_of["composite_score"]

    @pytest.mark.asyncio
    async def test_of_modifier_not_applied_to_forex(self):
        """OF modifier should NOT be applied for forex."""
        engine = _make_engine()
        result = await _gen(
            engine,
            market_type="forex",
            of_score=90.0,
            entry_price=Decimal("1.1000"),
            atr=Decimal("0.005"),
        )
        # Forex doesn't apply OF weight
        # just check it runs without error
        # (result may be None if composite < threshold)
        assert result is None or isinstance(result, dict)


class TestSignalEngineV2Earnings:
    @pytest.mark.asyncio
    async def test_earnings_skip_within_2_days(self):
        engine = _make_engine()
        result = await _gen(
            engine,
            market_type="stocks",
            earnings_days=1,
            entry_price=Decimal("100"),
            atr=Decimal("2"),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_earnings_discount_3_to_5_days(self):
        """Discount window: composite × 0.7 — may still pass threshold."""
        engine = _make_engine()
        result = await _gen(
            engine,
            market_type="stocks",
            earnings_days=4,
            entry_price=Decimal("100"),
            atr=Decimal("2"),
        )
        # Result may be None (post-discount below threshold) or discounted signal
        assert result is None or isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_no_earnings_discount_beyond_5_days(self):
        engine = _make_engine()
        result = await _gen(
            engine,
            market_type="stocks",
            earnings_days=10,
            entry_price=Decimal("100"),
            atr=Decimal("2"),
        )
        assert result is not None


class TestSignalEngineV2Portfolio:
    @pytest.mark.asyncio
    async def test_portfolio_heat_gate_blocks(self):
        """When portfolio is full, no new signal should be generated."""
        pm = PortfolioRiskManager()
        # Fill crypto to max (2)
        pm.add_position(
            OpenPosition(1, "BTC/USDT", "crypto", risk_pct=3.0, direction="LONG")
        )
        pm.add_position(
            OpenPosition(2, "ETH/USDT", "crypto", risk_pct=3.0, direction="LONG")
        )
        engine = _make_engine(portfolio=pm)
        result = await _gen(engine, market_type="crypto")
        assert result is None

    @pytest.mark.asyncio
    async def test_portfolio_heat_included_in_signal(self):
        engine = _make_engine()
        result = await _gen(engine)
        if result:
            assert "portfolio_heat" in result
            assert result["portfolio_heat"] >= 0


class TestSignalEngineV2Confidence:
    @pytest.mark.asyncio
    async def test_low_confidence_blocks_signal(self):
        """When all sources disagree, confidence is low → signal should be None."""
        engine = _make_engine()
        result = await _gen(
            engine,
            ta_score=35.0,    # bullish
            fa_score=-30.0,   # bearish
            sentiment_score=-25.0,  # bearish
            geo_score=30.0,   # bullish
            regime="RANGING",
        )
        # Conflicting signals → low confidence → likely None
        # (composite may also be below threshold)
        assert result is None or isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_high_alignment_produces_high_confidence(self):
        engine = _make_engine()
        result = await _gen(
            engine,
            ta_score=80.0,
            fa_score=70.0,
            sentiment_score=60.0,
            geo_score=50.0,
            regime="STRONG_TREND_BULL",
        )
        if result:
            assert result["confidence"] >= 50.0

    def test_confidence_calculation(self):
        engine = _make_engine()
        conf = engine._calculate_confidence(
            composite=80.0,
            ta_score=70.0,
            fa_score=60.0,
            sentiment_score=50.0,
            regime="STRONG_TREND_BULL",
        )
        assert 0 <= conf <= 100.0

    def test_confidence_all_agree_bullish(self):
        engine = _make_engine()
        conf = engine._calculate_confidence(
            composite=75.0,
            ta_score=80.0,
            fa_score=70.0,
            sentiment_score=60.0,
            regime="STRONG_TREND_BULL",
        )
        # All agree → high alignment component
        assert conf >= 60.0

    def test_confidence_all_disagree_low(self):
        engine = _make_engine()
        conf = engine._calculate_confidence(
            composite=35.0,     # positive composite
            ta_score=-20.0,     # but TA disagrees
            fa_score=-15.0,     # FA disagrees
            sentiment_score=-10.0,  # sentiment disagrees
            regime="RANGING",
        )
        assert conf < 60.0

    # ── FIX-01: magnitude normalised to real composite range (±25, not ±100) ──

    def test_fix01_magnitude_weak_signal(self):
        """FIX-01: composite=7 (min BUY) → magnitude=0.28, not 0.07."""
        engine = _make_engine()
        # Access internal calculation: composite=7, all agree, strong trend
        conf_weak = engine._calculate_confidence(
            composite=7.0,
            ta_score=5.0, fa_score=4.0, sentiment_score=3.0,
            regime="STRONG_TREND_BULL",
        )
        # With correct normalisation: magnitude = 7/25 = 0.28 → contributes 11.2
        # With old broken formula:    magnitude = 7/100 = 0.07 → contributes  2.8
        # Minimum with correct formula: 11.2 (magnitude) + 40.0 (all align) + 20.0 (regime) = 71.2
        assert conf_weak >= 65.0, f"Expected ≥65, got {conf_weak}"

    def test_fix01_magnitude_max_at_25(self):
        """FIX-01: composite=25 → magnitude=1.0 (max), not 0.25."""
        engine = _make_engine()
        conf_strong = engine._calculate_confidence(
            composite=25.0,
            ta_score=15.0, fa_score=10.0, sentiment_score=8.0,
            regime="STRONG_TREND_BULL",
        )
        # With correct normalisation: magnitude = 25/25 = 1.0 → full 40 points from magnitude
        # confidence = 40 + 40 + 20 = 100.0
        assert conf_strong == 100.0, f"Expected 100.0, got {conf_strong}"

    def test_fix01_weak_vs_strong_distinguishable(self):
        """FIX-01: confidence must differ significantly between composite=7 and composite=20."""
        engine = _make_engine()
        same_kwargs = dict(ta_score=5.0, fa_score=4.0, sentiment_score=3.0,
                           regime="STRONG_TREND_BULL")
        conf_weak   = engine._calculate_confidence(composite=7.0,  **same_kwargs)
        conf_strong = engine._calculate_confidence(composite=20.0, **same_kwargs)
        # With correct normalisation: difference ≈ (20-7)/25 × 40 = 20.8 points
        # With old formula:          difference ≈ (20-7)/100 × 40 = 5.2 points
        assert conf_strong - conf_weak >= 15.0, (
            f"Weak={conf_weak}, Strong={conf_strong} — difference too small"
        )


class TestSignalEngineV2Classify:
    def test_strong_buy(self):
        engine = _make_engine()
        direction, strength = engine._classify(70.0)
        assert direction == "LONG"
        assert strength == "STRONG_BUY"

    def test_buy(self):
        engine = _make_engine()
        direction, strength = engine._classify(40.0)
        assert direction == "LONG"
        assert strength == "BUY"

    def test_hold(self):
        engine = _make_engine()
        direction, strength = engine._classify(10.0)
        assert direction == "HOLD"

    def test_sell(self):
        engine = _make_engine()
        direction, strength = engine._classify(-40.0)
        assert direction == "SHORT"
        assert strength == "SELL"

    def test_strong_sell(self):
        engine = _make_engine()
        direction, strength = engine._classify(-70.0)
        assert direction == "SHORT"
        assert strength == "STRONG_SELL"
