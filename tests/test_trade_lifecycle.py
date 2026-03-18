"""Tests for src.signals.trade_lifecycle."""

from decimal import Decimal

import pytest

from src.signals.trade_lifecycle import TradeLifecycleManager

# Common test parameters
ENTRY_LONG = Decimal("1.10000")
SL_LONG = Decimal("1.09250")     # 75 pips below entry
TP1_LONG = Decimal("1.11500")    # 150 pips above entry (RR 2:1)
TP2_LONG = Decimal("1.12625")    # 262.5 pips
TP3_LONG = Decimal("1.14000")    # 400 pips
ATR = Decimal("0.00500")

ENTRY_SHORT = Decimal("1.10000")
SL_SHORT = Decimal("1.10750")
TP1_SHORT = Decimal("1.08500")
TP2_SHORT = Decimal("1.07375")
TP3_SHORT = Decimal("1.06000")


def check(
    direction,
    price,
    partial_closed=False,
    breakeven_moved=False,
    trailing_stop=None,
    regime="STRONG_TREND_BULL",
    entry=None,
    sl=None,
    tp1=None,
):
    mgr = TradeLifecycleManager()
    entry = entry or (ENTRY_LONG if direction == "LONG" else ENTRY_SHORT)
    sl = sl or (SL_LONG if direction == "LONG" else SL_SHORT)
    tp1 = tp1 or (TP1_LONG if direction == "LONG" else TP1_SHORT)
    return mgr.check(
        direction=direction,
        entry=entry,
        stop_loss=sl,
        take_profit_1=tp1,
        take_profit_2=TP2_LONG if direction == "LONG" else TP2_SHORT,
        take_profit_3=TP3_LONG if direction == "LONG" else TP3_SHORT,
        current_price=Decimal(str(price)),
        atr=ATR,
        regime=regime,
        partial_closed=partial_closed,
        breakeven_moved=breakeven_moved,
        trailing_stop=trailing_stop,
    )


class TestExitConditions:
    def test_sl_hit_long(self):
        result = check("LONG", "1.09200")  # below SL 1.09250
        assert result["action"] == "exit_sl"

    def test_sl_hit_short(self):
        result = check("SHORT", "1.10800")  # above SL 1.10750
        assert result["action"] == "exit_sl"

    def test_tp1_hit_long_partial_not_done(self):
        """FIX-04: price at TP1 with partial_closed=False → partial_close, not exit_tp1."""
        result = check("LONG", "1.11500")
        assert result["action"] == "partial_close"
        assert result["close_pct"] == pytest.approx(0.5)

    def test_tp1_hit_long_after_partial(self):
        """After partial close done, TP1 exit fires normally."""
        result = check("LONG", "1.11500", partial_closed=True)
        assert result["action"] == "exit_tp1"

    def test_tp1_hit_short_partial_not_done(self):
        """FIX-04: price at TP1 SHORT with partial_closed=False → partial_close."""
        result = check("SHORT", "1.08500")
        assert result["action"] == "partial_close"

    def test_tp1_hit_short_after_partial(self):
        result = check("SHORT", "1.08500", partial_closed=True)
        assert result["action"] == "exit_tp1"

    def test_tp2_hit_long_partial_not_done(self):
        """FIX-04: must partial close at TP1 before TP2 can fire."""
        result = check("LONG", "1.12700", partial_closed=False)
        assert result["action"] == "partial_close"

    def test_tp2_hit_long_after_partial(self):
        result = check("LONG", "1.12700", partial_closed=True)
        assert result["action"] == "exit_tp2"

    def test_tp3_hit_long_partial_not_done(self):
        """FIX-04: partial close must happen first before TP3 exit."""
        result = check("LONG", "1.14100", partial_closed=False)
        assert result["action"] == "partial_close"

    def test_tp3_hit_long_after_partial(self):
        result = check("LONG", "1.14100", partial_closed=True)
        assert result["action"] == "exit_tp3"

    def test_trailing_sl_hit_long(self):
        result = check("LONG", "1.09600", trailing_stop=Decimal("1.09700"))
        assert result["action"] == "exit_sl"
        assert result["reason"] == "Trailing SL hit"

    def test_hold_when_price_mid_range(self):
        result = check("LONG", "1.10500")  # between entry and TP1
        assert result["action"] in ("hold", "breakeven", "partial_close", "trailing_update")


class TestBreakeven:
    def test_breakeven_triggered_at_1to1_long(self):
        """Price at entry + SL_dist = 1.10750 should trigger breakeven."""
        result = check("LONG", "1.10750", breakeven_moved=False)
        assert result["action"] == "breakeven"
        assert result["new_stop_loss"] == ENTRY_LONG

    def test_breakeven_triggered_at_1to1_short(self):
        """Price at entry - SL_dist = 1.09250 should trigger breakeven SHORT."""
        result = check("SHORT", "1.09250", breakeven_moved=False)
        assert result["action"] == "breakeven"
        assert result["new_stop_loss"] == ENTRY_SHORT

    def test_no_breakeven_if_already_moved(self):
        result = check("LONG", "1.10800", breakeven_moved=True, trailing_stop=None)
        # Should not produce breakeven action
        assert result["action"] != "breakeven"

    def test_no_breakeven_below_trigger(self):
        result = check("LONG", "1.10300")  # not yet at 1:1 RR level
        assert result["action"] != "breakeven"


class TestPartialClose:
    def test_partial_close_at_tp1_long(self):
        """FIX-04: partial close triggers exactly at TP1, not 95% before it."""
        result = check("LONG", "1.11500")  # exactly at TP1
        assert result["action"] == "partial_close"
        assert result["close_pct"] == pytest.approx(0.5)

    def test_partial_close_above_tp1_long(self):
        """Price beyond TP1 also triggers partial_close when not yet done."""
        result = check("LONG", "1.11600")  # above TP1
        assert result["action"] == "partial_close"

    def test_partial_close_at_tp1_short(self):
        """FIX-04: partial close triggers exactly at TP1 SHORT."""
        result = check("SHORT", "1.08500")  # exactly at TP1_SHORT
        assert result["action"] == "partial_close"

    def test_no_partial_close_just_below_tp1(self):
        """Price just below TP1 should NOT trigger partial close."""
        result = check("LONG", "1.11499")
        assert result["action"] != "partial_close"

    def test_no_partial_close_if_already_done(self):
        result = check("LONG", "1.11430", partial_closed=True)
        assert result["action"] != "partial_close"

    def test_no_partial_close_too_early(self):
        result = check("LONG", "1.11000")  # only ~67% of TP1 distance
        assert result["action"] != "partial_close"


class TestTrailingStop:
    def test_trail_set_after_breakeven_long(self):
        """Once breakeven is moved, trailing should engage."""
        result = check("LONG", "1.11000", breakeven_moved=True)
        assert result["action"] == "trailing_update"
        assert result["new_stop_loss"] is not None
        # Trail = 1.11000 - 0.5*ATR = 1.10750
        assert result["new_stop_loss"] < Decimal("1.11000")

    def test_trail_tightens_only_long(self):
        """Trail should only move up for LONG, never down."""
        mgr = TradeLifecycleManager()
        current_trail = Decimal("1.10900")  # already set high
        result = mgr.check(
            direction="LONG",
            entry=ENTRY_LONG,
            stop_loss=SL_LONG,
            take_profit_1=TP1_LONG,
            take_profit_2=TP2_LONG,
            take_profit_3=TP3_LONG,
            current_price=Decimal("1.10800"),  # price dropped → new trail would be lower
            atr=ATR,
            regime="STRONG_TREND_BULL",
            breakeven_moved=True,
            trailing_stop=current_trail,
        )
        # Should NOT update since new trail would be below current
        assert result["action"] != "trailing_update"

    def test_trail_uses_smaller_frac_in_range_regime(self):
        """Ranging regime uses 0.3×ATR trail instead of 0.5×ATR."""
        mgr = TradeLifecycleManager()
        result_trend = mgr.check(
            direction="LONG",
            entry=ENTRY_LONG,
            stop_loss=SL_LONG,
            take_profit_1=TP1_LONG,
            take_profit_2=TP2_LONG,
            take_profit_3=TP3_LONG,
            current_price=Decimal("1.11200"),
            atr=ATR,
            regime="STRONG_TREND_BULL",  # 0.5×ATR
            breakeven_moved=True,
        )
        result_range = mgr.check(
            direction="LONG",
            entry=ENTRY_LONG,
            stop_loss=SL_LONG,
            take_profit_1=TP1_LONG,
            take_profit_2=TP2_LONG,
            take_profit_3=TP3_LONG,
            current_price=Decimal("1.11200"),
            atr=ATR,
            regime="RANGING",  # 0.3×ATR
            breakeven_moved=True,
        )
        # Ranging trail is tighter (closer to price)
        assert result_range["new_stop_loss"] > result_trend["new_stop_loss"]

    def test_no_trail_before_breakeven(self):
        """Trailing stop should not engage before breakeven is moved."""
        result = check("LONG", "1.11200", breakeven_moved=False)
        assert result["action"] != "trailing_update"


class TestUnknownDirection:
    def test_hold_for_unknown_direction(self):
        mgr = TradeLifecycleManager()
        result = mgr.check(
            direction="FOO",
            entry=ENTRY_LONG,
            stop_loss=SL_LONG,
            take_profit_1=TP1_LONG,
            take_profit_2=TP2_LONG,
            take_profit_3=TP3_LONG,
            current_price=Decimal("1.10500"),
            atr=ATR,
            regime="RANGING",
        )
        assert result["action"] == "hold"
