"""Tests for Risk Manager."""

from decimal import Decimal

import pytest

from src.signals.risk_manager import RiskManager


class TestRiskManagerLevels:
    """Test SL/TP level calculation."""

    @pytest.fixture
    def rm(self):
        return RiskManager(
            sl_atr_mult=1.5,
            tp1_atr_mult=2.0,
            tp2_atr_mult=3.5,
        )

    def test_long_sl_below_entry(self, rm):
        """For LONG: SL should be below entry price."""
        entry = Decimal("1.1000")
        atr = Decimal("0.0010")
        levels = rm.calculate_levels(entry, atr, "LONG")
        assert levels["stop_loss"] < entry

    def test_long_tp1_above_entry(self, rm):
        """For LONG: TP1 should be above entry price."""
        entry = Decimal("1.1000")
        atr = Decimal("0.0010")
        levels = rm.calculate_levels(entry, atr, "LONG")
        assert levels["take_profit_1"] > entry

    def test_long_tp2_above_tp1(self, rm):
        """For LONG: TP2 should be above TP1."""
        entry = Decimal("1.1000")
        atr = Decimal("0.0010")
        levels = rm.calculate_levels(entry, atr, "LONG")
        assert levels["take_profit_2"] > levels["take_profit_1"]

    def test_short_sl_above_entry(self, rm):
        """For SHORT: SL should be above entry price."""
        entry = Decimal("1.1000")
        atr = Decimal("0.0010")
        levels = rm.calculate_levels(entry, atr, "SHORT")
        assert levels["stop_loss"] > entry

    def test_short_tp1_below_entry(self, rm):
        """For SHORT: TP1 should be below entry price."""
        entry = Decimal("1.1000")
        atr = Decimal("0.0010")
        levels = rm.calculate_levels(entry, atr, "SHORT")
        assert levels["take_profit_1"] < entry

    def test_short_tp2_below_tp1(self, rm):
        """For SHORT: TP2 should be below TP1."""
        entry = Decimal("1.1000")
        atr = Decimal("0.0010")
        levels = rm.calculate_levels(entry, atr, "SHORT")
        assert levels["take_profit_2"] < levels["take_profit_1"]

    def test_long_sl_distance(self, rm):
        """SL distance should be ATR × SL multiplier."""
        entry = Decimal("1.1000")
        atr = Decimal("0.0010")
        levels = rm.calculate_levels(entry, atr, "LONG")
        expected_sl = entry - atr * Decimal("1.5")
        assert abs(levels["stop_loss"] - expected_sl) < Decimal("0.00000001")

    def test_long_tp1_distance(self, rm):
        """TP1 distance should be ATR × TP1 multiplier."""
        entry = Decimal("1.1000")
        atr = Decimal("0.0010")
        levels = rm.calculate_levels(entry, atr, "LONG")
        expected_tp1 = entry + atr * Decimal("2.0")
        assert abs(levels["take_profit_1"] - expected_tp1) < Decimal("0.00000001")

    def test_hold_returns_none_levels(self, rm):
        """HOLD direction should return None for all levels."""
        entry = Decimal("1.1000")
        atr = Decimal("0.0010")
        levels = rm.calculate_levels(entry, atr, "HOLD")
        assert levels["stop_loss"] is None
        assert levels["take_profit_1"] is None
        assert levels["take_profit_2"] is None


class TestRiskReward:
    """Test R:R ratio calculation."""

    @pytest.fixture
    def rm(self):
        return RiskManager()

    def test_rr_calculation(self, rm):
        """R:R should equal TP distance / SL distance."""
        entry = Decimal("1.1000")
        sl = Decimal("1.0985")  # 15 pips SL
        tp1 = Decimal("1.1020")  # 20 pips TP
        rr = rm.calculate_risk_reward(entry, sl, tp1)
        expected = Decimal("20") / Decimal("15")
        assert abs(rr - expected) < Decimal("0.01")

    def test_rr_returns_decimal(self, rm):
        """R:R should return Decimal type."""
        entry = Decimal("1.1000")
        sl = Decimal("1.0985")
        tp1 = Decimal("1.1020")
        rr = rm.calculate_risk_reward(entry, sl, tp1)
        assert isinstance(rr, Decimal)

    def test_rr_zero_sl_returns_none(self, rm):
        """R:R should return None when SL equals entry."""
        entry = Decimal("1.1000")
        rr = rm.calculate_risk_reward(entry, entry, Decimal("1.1020"))
        assert rr is None

    def test_rr_standard_trade(self, rm):
        """Standard ATR-based trade should have ~1.33 R:R."""
        entry = Decimal("1.1000")
        atr = Decimal("0.0010")
        levels = rm.calculate_levels(entry, atr, "LONG")
        rr = rm.calculate_risk_reward(entry, levels["stop_loss"], levels["take_profit_1"])
        # TP1 = 2.0 * ATR, SL = 1.5 * ATR → R:R = 2.0/1.5 = 1.33
        expected = Decimal("2.0") / Decimal("1.5")
        assert abs(rr - expected) < Decimal("0.01")


class TestPositionSizing:
    """Test position size calculation."""

    @pytest.fixture
    def rm(self):
        return RiskManager()

    def test_position_size_positive(self, rm):
        """Position size should always be positive."""
        size = rm.calculate_position_size(
            account=Decimal("10000"),
            risk_pct=2.0,
            sl_distance=Decimal("0.0015"),
            entry_price=Decimal("1.1000"),
        )
        assert size > Decimal("0")

    def test_zero_sl_returns_zero(self, rm):
        """Zero SL distance should return zero position size."""
        size = rm.calculate_position_size(
            account=Decimal("10000"),
            risk_pct=2.0,
            sl_distance=Decimal("0"),
        )
        assert size == Decimal("0")

    def test_position_size_type(self, rm):
        """Position size should be Decimal."""
        size = rm.calculate_position_size(
            account=Decimal("10000"),
            risk_pct=2.0,
            sl_distance=Decimal("0.0015"),
        )
        assert isinstance(size, Decimal)


class TestValidation:
    """Test signal validation."""

    @pytest.fixture
    def rm(self):
        return RiskManager()

    def test_valid_long_signal(self, rm):
        """Valid LONG signal should pass validation."""
        valid, reason = rm.validate_signal(
            entry=Decimal("1.1000"),
            stop_loss=Decimal("1.0985"),
            take_profit_1=Decimal("1.1020"),
            direction="LONG",
        )
        assert valid, reason

    def test_invalid_long_sl_above_entry(self, rm):
        """LONG signal with SL above entry should fail."""
        valid, reason = rm.validate_signal(
            entry=Decimal("1.1000"),
            stop_loss=Decimal("1.1010"),  # SL above entry
            take_profit_1=Decimal("1.1020"),
            direction="LONG",
        )
        assert not valid

    def test_valid_short_signal(self, rm):
        """Valid SHORT signal should pass validation."""
        valid, reason = rm.validate_signal(
            entry=Decimal("1.1000"),
            stop_loss=Decimal("1.1015"),
            take_profit_1=Decimal("1.0980"),
            direction="SHORT",
        )
        assert valid, reason
