"""Tests for src.signals.risk_manager_v2."""

from decimal import Decimal

import pytest

from src.signals.risk_manager_v2 import RiskManagerV2, _REGIME_TP, _REGIME_MIN_RR


ENTRY = Decimal("1.10000")
ATR = Decimal("0.00500")


class TestRiskManagerV2Levels:
    def test_long_sl_below_entry(self):
        rm = RiskManagerV2()
        levels = rm.calculate_levels(ENTRY, ATR, "LONG", "RANGING")
        assert levels["stop_loss"] < ENTRY

    def test_long_tp1_above_entry(self):
        rm = RiskManagerV2()
        levels = rm.calculate_levels(ENTRY, ATR, "LONG", "RANGING")
        assert levels["take_profit_1"] > ENTRY

    def test_short_sl_above_entry(self):
        rm = RiskManagerV2()
        levels = rm.calculate_levels(ENTRY, ATR, "SHORT", "STRONG_TREND_BEAR")
        assert levels["stop_loss"] > ENTRY

    def test_short_tp1_below_entry(self):
        rm = RiskManagerV2()
        levels = rm.calculate_levels(ENTRY, ATR, "SHORT", "STRONG_TREND_BEAR")
        assert levels["take_profit_1"] < ENTRY

    def test_hold_returns_nones(self):
        rm = RiskManagerV2()
        levels = rm.calculate_levels(ENTRY, ATR, "HOLD")
        assert levels["stop_loss"] is None
        assert levels["take_profit_1"] is None

    def test_tp1_tp2_tp3_ordering_long(self):
        rm = RiskManagerV2()
        levels = rm.calculate_levels(ENTRY, ATR, "LONG", "STRONG_TREND_BULL")
        assert ENTRY < levels["take_profit_1"] < levels["take_profit_2"] < levels["take_profit_3"]

    def test_tp1_tp2_tp3_ordering_short(self):
        rm = RiskManagerV2()
        levels = rm.calculate_levels(ENTRY, ATR, "SHORT", "STRONG_TREND_BEAR")
        assert ENTRY > levels["take_profit_1"] > levels["take_profit_2"] > levels["take_profit_3"]

    def test_rr_returned(self):
        rm = RiskManagerV2()
        levels = rm.calculate_levels(ENTRY, ATR, "LONG", "RANGING")
        assert levels["risk_reward_1"] is not None
        assert levels["risk_reward_1"] > 0

    def test_unknown_regime_defaults_to_ranging(self):
        rm = RiskManagerV2()
        levels_ranging = rm.calculate_levels(ENTRY, ATR, "LONG", "RANGING")
        levels_unknown = rm.calculate_levels(ENTRY, ATR, "LONG", "UNKNOWN_REGIME")
        assert levels_ranging["stop_loss"] == levels_unknown["stop_loss"]

    def test_all_regimes_produce_levels(self):
        rm = RiskManagerV2()
        for regime in _REGIME_TP:
            levels = rm.calculate_levels(ENTRY, ATR, "LONG", regime)
            assert levels["stop_loss"] is not None
            assert levels["take_profit_1"] is not None

    def test_high_volatility_wider_sl(self):
        """HIGH_VOLATILITY should have a wider SL than RANGING."""
        rm = RiskManagerV2()
        vol_sl = rm.calculate_levels(ENTRY, ATR, "LONG", "HIGH_VOLATILITY")["stop_loss"]
        rng_sl = rm.calculate_levels(ENTRY, ATR, "LONG", "RANGING")["stop_loss"]
        # Higher vol → lower SL for long (further from entry)
        assert vol_sl < rng_sl


class TestRiskManagerV2SRAlignment:
    def test_long_sl_aligned_to_support(self):
        rm = RiskManagerV2()
        # Support at 1.0960 — below raw SL (1.0925)
        support = [Decimal("1.0960"), Decimal("1.0930")]
        levels = rm.calculate_levels(
            ENTRY, ATR, "LONG", "RANGING", support_levels=support
        )
        sl = levels["stop_loss"]
        # SL should be at or below the nearest support minus buffer
        assert sl < Decimal("1.0960")

    def test_short_sl_aligned_to_resistance(self):
        rm = RiskManagerV2()
        resistance = [Decimal("1.1040"), Decimal("1.1060")]
        levels = rm.calculate_levels(
            ENTRY, ATR, "SHORT", "RANGING", resistance_levels=resistance
        )
        sl = levels["stop_loss"]
        # SL should be above nearest resistance + buffer
        assert sl > Decimal("1.1040")

    def test_no_sr_levels_uses_raw_sl(self):
        rm = RiskManagerV2()
        raw = rm.calculate_levels(ENTRY, ATR, "LONG", "RANGING")
        with_empty = rm.calculate_levels(
            ENTRY, ATR, "LONG", "RANGING", support_levels=[]
        )
        assert raw["stop_loss"] == with_empty["stop_loss"]

    def test_unsuitable_sr_uses_raw_sl(self):
        """Support levels above SL distance should be ignored."""
        rm = RiskManagerV2()
        # Support at 1.0990 — between entry and raw SL, shouldn't tighten SL
        support = [Decimal("1.0990")]
        raw = rm.calculate_levels(ENTRY, ATR, "LONG", "RANGING")
        aligned = rm.calculate_levels(
            ENTRY, ATR, "LONG", "RANGING", support_levels=support
        )
        # When no support is suitable (below raw_sl), use raw_sl
        assert aligned["stop_loss"] == raw["stop_loss"]


class TestRiskManagerV2Validation:
    def test_valid_long(self):
        rm = RiskManagerV2()
        ok, msg = rm.validate(ENTRY, Decimal("1.0950"), Decimal("1.1100"), "LONG")
        assert ok is True
        assert msg == "OK"

    def test_invalid_long_sl_above_entry(self):
        rm = RiskManagerV2()
        ok, msg = rm.validate(ENTRY, Decimal("1.1050"), Decimal("1.1200"), "LONG")
        assert ok is False
        assert "SL must be below" in msg

    def test_invalid_short_sl_below_entry(self):
        rm = RiskManagerV2()
        ok, msg = rm.validate(ENTRY, Decimal("1.0950"), Decimal("1.0800"), "SHORT")
        assert ok is False
        assert "SL must be above" in msg

    def test_low_rr_rejected(self):
        rm = RiskManagerV2()
        # SL very far, TP very close → R:R < 1.0
        ok, msg = rm.validate(
            ENTRY,
            Decimal("1.0500"),  # far SL
            Decimal("1.1010"),  # tiny TP
            "LONG",
            "RANGING",
        )
        assert ok is False
        assert "R:R" in msg

    def test_unknown_direction_rejected(self):
        rm = RiskManagerV2()
        ok, _ = rm.validate(ENTRY, Decimal("1.0950"), Decimal("1.1100"), "FOO")
        assert ok is False


class TestRiskManagerV2PositionSizing:
    def test_basic_sizing(self):
        rm = RiskManagerV2()
        pct = rm.calculate_position_size(
            account=Decimal("10000"),
            risk_pct=1.0,
            sl_distance=Decimal("100"),
            entry_price=Decimal("50000"),
        )
        assert pct > 0

    def test_zero_sl_returns_zero(self):
        rm = RiskManagerV2()
        pct = rm.calculate_position_size(
            account=Decimal("10000"),
            risk_pct=1.0,
            sl_distance=Decimal("0"),
        )
        assert pct == Decimal("0")

    def test_larger_sl_smaller_size(self):
        rm = RiskManagerV2()
        small_sl = rm.calculate_position_size(
            account=Decimal("10000"),
            risk_pct=1.0,
            sl_distance=Decimal("50"),
            entry_price=Decimal("1000"),
        )
        large_sl = rm.calculate_position_size(
            account=Decimal("10000"),
            risk_pct=1.0,
            sl_distance=Decimal("200"),
            entry_price=Decimal("1000"),
        )
        assert small_sl > large_sl
