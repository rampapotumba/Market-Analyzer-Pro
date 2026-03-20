"""Risk Manager: SL/TP calculation and position sizing."""

import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from src.config import settings

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Manages risk parameters: Stop Loss, Take Profit, Position Size, R:R ratio.

    Uses ATR-based levels as defined in SPEC:
        SL = Entry ± ATR(14) × 1.5
        TP1 = Entry ± ATR(14) × 2.0
        TP2 = Entry ± ATR(14) × 3.5
    """

    def __init__(
        self,
        sl_atr_mult: float = None,
        tp1_atr_mult: float = None,
        tp2_atr_mult: float = None,
        max_risk_pct: float = None,
    ) -> None:
        self.sl_atr_mult = Decimal(str(sl_atr_mult or settings.SL_ATR_MULTIPLIER))
        self.tp1_atr_mult = Decimal(str(tp1_atr_mult or settings.TP1_ATR_MULTIPLIER))
        self.tp2_atr_mult = Decimal(str(tp2_atr_mult or settings.TP2_ATR_MULTIPLIER))
        self.max_risk_pct = max_risk_pct or settings.MAX_RISK_PER_TRADE_PCT

    def calculate_levels(
        self,
        entry: Decimal,
        atr: Decimal,
        direction: str,
    ) -> dict[str, Decimal]:
        """
        Calculate Stop Loss and Take Profit levels.

        Args:
            entry: Entry price
            atr: ATR(14) value
            direction: 'LONG' or 'SHORT'

        Returns:
            dict with keys: stop_loss, take_profit_1, take_profit_2
        """
        if direction == "LONG":
            stop_loss = entry - atr * self.sl_atr_mult
            take_profit_1 = entry + atr * self.tp1_atr_mult
            take_profit_2 = entry + atr * self.tp2_atr_mult
        elif direction == "SHORT":
            stop_loss = entry + atr * self.sl_atr_mult
            take_profit_1 = entry - atr * self.tp1_atr_mult
            take_profit_2 = entry - atr * self.tp2_atr_mult
        else:
            # HOLD: no levels
            return {
                "stop_loss": None,
                "take_profit_1": None,
                "take_profit_2": None,
            }

        return {
            "stop_loss": stop_loss.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP),
            "take_profit_1": take_profit_1.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP),
            "take_profit_2": take_profit_2.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP),
        }

    def calculate_risk_reward(
        self,
        entry: Decimal,
        stop_loss: Decimal,
        take_profit_1: Decimal,
    ) -> Optional[Decimal]:
        """
        Calculate Risk:Reward ratio.

        Args:
            entry: Entry price
            stop_loss: Stop loss level
            take_profit_1: First take profit level

        Returns:
            R:R ratio as Decimal, or None if SL distance is 0.
        """
        sl_distance = abs(entry - stop_loss)
        tp_distance = abs(entry - take_profit_1)

        if sl_distance == Decimal("0"):
            return None

        rr = tp_distance / sl_distance
        return rr.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def calculate_position_size(
        self,
        account: Decimal,
        risk_pct: float,
        sl_distance: Decimal,
        entry_price: Optional[Decimal] = None,
    ) -> Decimal:
        """
        Calculate position size as percentage of account.

        Formula: Position Size = (Risk% × Account) / SL_Distance

        Args:
            account: Account balance
            risk_pct: Risk percentage (e.g., 2.0 for 2%)
            sl_distance: Distance from entry to stop loss (absolute)
            entry_price: Entry price (for normalization, optional)

        Returns:
            Position size percentage as Decimal.
        """
        if sl_distance <= Decimal("0"):
            return Decimal("0")

        risk_amount = account * Decimal(str(risk_pct)) / Decimal("100")
        # Position size in account currency units
        position_size_units = risk_amount / sl_distance

        # Convert to percentage of account
        if entry_price and entry_price > Decimal("0"):
            position_value = position_size_units * entry_price
            position_pct = (position_value / account) * Decimal("100")
        else:
            position_pct = (position_size_units / account) * Decimal("100")

        # Cap at max risk per trade
        max_pct = Decimal(str(self.max_risk_pct))
        position_pct = min(position_pct, max_pct)

        return position_pct.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    def validate_signal(
        self,
        entry: Decimal,
        stop_loss: Decimal,
        take_profit_1: Decimal,
        direction: str,
        min_rr: float = 1.0,
    ) -> tuple[bool, str]:
        """
        Validate signal parameters.

        Returns:
            (is_valid, reason)
        """
        if direction == "LONG":
            if stop_loss >= entry:
                return False, "SL must be below entry for LONG"
            if take_profit_1 <= entry:
                return False, "TP1 must be above entry for LONG"
        elif direction == "SHORT":
            if stop_loss <= entry:
                return False, "SL must be above entry for SHORT"
            if take_profit_1 >= entry:
                return False, "TP1 must be below entry for SHORT"

        rr = self.calculate_risk_reward(entry, stop_loss, take_profit_1)
        if rr is None or float(rr) < min_rr:
            return False, f"R:R ratio {rr} is below minimum {min_rr}"

        return True, "OK"
