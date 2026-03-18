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

    def test_tp1_hit_long(self):
        result = check("LONG", "1.11500")
        assert result["action"] == "exit_tp1"

    def test_tp1_hit_short(self):
        result = check("SHORT", "1.08500")
        assert result["action"] == "exit_tp1"

    def test_tp2_hit_long(self):
        result = check("LONG", "1.12700")  # above TP2
        assert result["action"] == "exit_tp2"

    def test_tp3_hit_long(self):
        result = check("LONG", "1.14100")  # above TP3
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
    def test_partial_close_at_95pct_tp1_long(self):
        """At 95% of the TP1 distance, suggest partial close."""
        # 95% of dist: 1.10000 + 0.95 * 0.01500 = 1.11425
        result = check("LONG", "1.11430")
        assert result["action"] == "partial_close"
        assert result["close_pct"] == pytest.approx(0.5)

    def test_partial_close_at_95pct_tp1_short(self):
        # TP1_SHORT = 1.08500, entry = 1.10000, dist = 0.01500
        # 95%: 1.10000 - 0.95 * 0.01500 = 1.08575
        result = check("SHORT", "1.08570")
        assert result["action"] == "partial_close"

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
