"""Trade Lifecycle Manager v2.

Handles post-entry trade management:
  - Breakeven: move SL to entry after RR 1:1 is reached
  - Partial close: suggest closing 50% at TP1
  - Trailing stop: 0.5×ATR (trend regimes), 0.3×ATR (ranging/other)

All methods are pure functions (no DB access) — they take the current
position snapshot and return an action dict. The caller (signal_tracker)
is responsible for persisting state to virtual_portfolio.
"""

import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

logger = logging.getLogger(__name__)

# Regimes considered as trending for trailing-stop purposes
_TREND_REGIMES = {
    "STRONG_TREND_BULL",
    "STRONG_TREND_BEAR",
    "WEAK_TREND_BULL",
    "WEAK_TREND_BEAR",
}

# ATR fractions for trailing stop (FIX-06: increased to avoid premature exits on normal pullbacks)
_TRAIL_ATR_TREND = Decimal("1.0")   # was 0.5 — 1.0×ATR gives room for normal trend pullbacks
_TRAIL_ATR_RANGE = Decimal("0.5")   # was 0.3 — tighter than trend but not too aggressive

_QUANT = Decimal("0.00000001")


class TradeLifecycleManager:
    """
    Stateless trade-management calculator.

    Usage:
        mgr = TradeLifecycleManager()
        action = mgr.check(
            direction="LONG",
            entry=Decimal("1.1000"),
            stop_loss=Decimal("1.0925"),
            take_profit_1=Decimal("1.1150"),
            current_price=Decimal("1.1160"),
            atr=Decimal("0.0050"),
            regime="STRONG_TREND_BULL",
            partial_closed=False,
            breakeven_moved=False,
            trailing_stop=None,
        )
    """

    def check(
        self,
        direction: str,
        entry: Decimal,
        stop_loss: Decimal,
        take_profit_1: Decimal,
        take_profit_2: Optional[Decimal],
        take_profit_3: Optional[Decimal],
        current_price: Decimal,
        atr: Decimal,
        regime: str,
        partial_closed: bool = False,
        breakeven_moved: bool = False,
        trailing_stop: Optional[Decimal] = None,
    ) -> dict:
        """
        Evaluate trade state and return recommended actions.

        Returns:
            {
                "action": "hold" | "breakeven" | "partial_close" | "trailing_update"
                          | "exit_sl" | "exit_tp1" | "exit_tp2" | "exit_tp3",
                "new_stop_loss": Decimal | None,
                "close_pct": float | None,  # 0.5 means close 50%
                "reason": str,
            }
        """
        if direction not in ("LONG", "SHORT"):
            return _action("hold", reason="Unknown direction")

        # ── SL check always fires first (risk management) ─────────────────────
        exit_action = self._check_exits(
            direction, entry, stop_loss, trailing_stop,
            take_profit_1, take_profit_2, take_profit_3,
            current_price,
        )
        if exit_action["action"] == "exit_sl":
            return exit_action

        # ── Partial close at TP1 (FIX-04: before TP exits) ────────────────────
        # When the position hasn't been partially closed yet, intercept TP1
        # to take 50% profit and let the remaining run.  Subsequent TP exits
        # only fire after partial_closed=True.
        if not partial_closed and take_profit_1 is not None:
            pc_action = self._check_partial_close(
                direction, entry, take_profit_1, current_price
            )
            if pc_action:
                return pc_action

        # ── TP2/TP3 exits (only after partial close is done) ──────────────────
        if exit_action["action"].startswith("exit_tp"):
            return exit_action

        actions = []

        # ── Breakeven ─────────────────────────────────────────────────────────
        if not breakeven_moved:
            be_action = self._check_breakeven(
                direction, entry, stop_loss, take_profit_1, current_price
            )
            if be_action:
                actions.append(be_action)

        # ── Trailing stop ─────────────────────────────────────────────────────
        if breakeven_moved:  # Only trail once breakeven is set
            trail_action = self._check_trailing(
                direction, entry, current_price, atr, regime, trailing_stop
            )
            if trail_action:
                actions.append(trail_action)

        # Return the highest-priority action
        for priority in ("breakeven", "trailing_update"):
            for a in actions:
                if a["action"] == priority:
                    return a

        return _action("hold", reason="No lifecycle action triggered")

    # ── Exit checks ───────────────────────────────────────────────────────────

    def _check_exits(
        self,
        direction: str,
        entry: Decimal,
        stop_loss: Decimal,
        trailing_stop: Optional[Decimal],
        take_profit_1: Optional[Decimal],
        take_profit_2: Optional[Decimal],
        take_profit_3: Optional[Decimal],
        current_price: Decimal,
    ) -> dict:
        active_sl = trailing_stop if trailing_stop is not None else stop_loss

        if direction == "LONG":
            if current_price <= active_sl:
                reason = "Trailing SL hit" if trailing_stop else "SL hit"
                return _action("exit_sl", reason=reason)
            if take_profit_3 and current_price >= take_profit_3:
                return _action("exit_tp3", reason="TP3 hit")
            if take_profit_2 and current_price >= take_profit_2:
                return _action("exit_tp2", reason="TP2 hit")
            if take_profit_1 and current_price >= take_profit_1:
                return _action("exit_tp1", reason="TP1 hit")
        else:  # SHORT
            if current_price >= active_sl:
                reason = "Trailing SL hit" if trailing_stop else "SL hit"
                return _action("exit_sl", reason=reason)
            if take_profit_3 and current_price <= take_profit_3:
                return _action("exit_tp3", reason="TP3 hit")
            if take_profit_2 and current_price <= take_profit_2:
                return _action("exit_tp2", reason="TP2 hit")
            if take_profit_1 and current_price <= take_profit_1:
                return _action("exit_tp1", reason="TP1 hit")

        return _action("hold", reason="No exit condition")

    # ── Breakeven ─────────────────────────────────────────────────────────────

    def _check_breakeven(
        self,
        direction: str,
        entry: Decimal,
        stop_loss: Decimal,
        take_profit_1: Optional[Decimal],
        current_price: Decimal,
    ) -> Optional[dict]:
        """Move SL to entry when RR 1:1 is reached."""
        if take_profit_1 is None:
            return None

        sl_dist = abs(entry - stop_loss)
        if sl_dist == Decimal("0"):
            return None

        # 1:1 RR trigger = entry ± sl_dist
        if direction == "LONG":
            trigger = entry + sl_dist
            reached = current_price >= trigger
        else:
            trigger = entry - sl_dist
            reached = current_price <= trigger

        if reached:
            return _action(
                "breakeven",
                new_stop_loss=entry.quantize(_QUANT),
                reason=f"Price at {float(current_price):.5f} reached 1:1 RR trigger {float(trigger):.5f}",
            )
        return None

    # ── Partial close ─────────────────────────────────────────────────────────

    def _check_partial_close(
        self,
        direction: str,
        entry: Decimal,
        take_profit_1: Decimal,
        current_price: Decimal,
    ) -> Optional[dict]:
        """Suggest closing 50% when price reaches TP1 (FIX-04: exactly at TP1, not 95%)."""
        # FIX-04: close at TP1 exactly, not 95% before it — traders take profit at the level
        if direction == "LONG":
            reached = current_price >= take_profit_1
        else:
            reached = current_price <= take_profit_1

        if reached:
            return _action(
                "partial_close",
                close_pct=0.5,
                reason=f"TP1 reached ({float(take_profit_1):.5f}) — partial close 50%",
            )
        return None

    # ── Trailing stop ─────────────────────────────────────────────────────────

    def _check_trailing(
        self,
        direction: str,
        entry: Decimal,
        current_price: Decimal,
        atr: Decimal,
        regime: str,
        current_trail: Optional[Decimal],
    ) -> Optional[dict]:
        """Update trailing stop: 0.5×ATR for trend, 0.3×ATR for ranging."""
        frac = _TRAIL_ATR_TREND if regime in _TREND_REGIMES else _TRAIL_ATR_RANGE

        if direction == "LONG":
            new_trail = (current_price - atr * frac).quantize(
                _QUANT, rounding=ROUND_HALF_UP
            )
            # Only tighten (trail up), never widen
            if current_trail is None or new_trail > current_trail:
                return _action(
                    "trailing_update",
                    new_stop_loss=new_trail,
                    reason=f"Trail LONG: {float(new_trail):.5f} ({frac}×ATR below price)",
                )
        else:  # SHORT
            new_trail = (current_price + atr * frac).quantize(
                _QUANT, rounding=ROUND_HALF_UP
            )
            # Only tighten (trail down), never widen
            if current_trail is None or new_trail < current_trail:
                return _action(
                    "trailing_update",
                    new_stop_loss=new_trail,
                    reason=f"Trail SHORT: {float(new_trail):.5f} ({frac}×ATR above price)",
                )

        return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _action(
    action: str,
    new_stop_loss: Optional[Decimal] = None,
    close_pct: Optional[float] = None,
    reason: str = "",
) -> dict:
    return {
        "action": action,
        "new_stop_loss": new_stop_loss,
        "close_pct": close_pct,
        "reason": reason,
    }
