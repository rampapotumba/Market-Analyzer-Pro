"""Risk Manager v2 — Regime-adaptive SL/TP calculation.

Improvements over v1:
  - Regime-aware ATR multipliers (7 regimes)
  - SL alignment to nearest Support/Resistance level
  - TP1 / TP2 / TP3 targets per regime
  - R:R validation per regime
"""

import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from src.analysis.regime_detector import (
    REGIMES,
    _REGIME_SL_MULTIPLIER,
)
from src.config import settings

logger = logging.getLogger(__name__)

# ── v3: Regime-adaptive SL/TP tables (3.3.1) ─────────────────────────────────
#
# REGIME_SL_MULTIPLIERS   — SL = entry ± ATR × mult
# REGIME_TP1_RR / TP2_RR  — TP = entry ± SL_distance × R:R
#
# Key difference vs legacy: TP is now in R:R terms relative to actual SL distance,
# not in ATR multiples. This ensures consistent risk/reward regardless of SL placement.

REGIME_SL_MULTIPLIERS: dict[str, float] = {
    "STRONG_TREND_BULL": 1.5,
    "STRONG_TREND_BEAR": 1.5,
    "WEAK_TREND_BULL":   1.3,
    "WEAK_TREND_BEAR":   1.3,
    "RANGING":           1.2,
    "HIGH_VOLATILITY":   2.5,
    "LOW_VOLATILITY":    1.0,
}

REGIME_TP1_RR: dict[str, float] = {
    "STRONG_TREND_BULL": 2.0,
    "STRONG_TREND_BEAR": 2.0,
    "WEAK_TREND_BULL":   2.0,
    "WEAK_TREND_BEAR":   2.0,
    "RANGING":           1.5,
    "HIGH_VOLATILITY":   2.0,
    "LOW_VOLATILITY":    1.8,
}

REGIME_TP2_RR: dict[str, float] = {
    "STRONG_TREND_BULL": 3.5,
    "STRONG_TREND_BEAR": 3.5,
    "WEAK_TREND_BULL":   3.0,
    "WEAK_TREND_BEAR":   3.0,
    "RANGING":           2.5,
    "HIGH_VOLATILITY":   3.0,
    "LOW_VOLATILITY":    2.5,
}

_DEFAULT_SL_MULT = 1.5
_DEFAULT_TP1_RR = 2.0
_DEFAULT_TP2_RR = 3.5

# ── TP multipliers per regime (as multiples of ATR) — kept for legacy calculate_levels() ──
_REGIME_TP: dict[str, tuple[float, float, float]] = {
    "STRONG_TREND_BULL": (2.0, 3.5, 5.0),
    "STRONG_TREND_BEAR": (2.0, 3.5, 5.0),
    "WEAK_TREND_BULL":   (2.0, 3.5, 4.0),
    "WEAK_TREND_BEAR":   (2.0, 3.5, 4.0),
    "RANGING":           (1.5, 2.5, 3.5),
    "HIGH_VOLATILITY":   (2.5, 4.0, 6.0),
    "LOW_VOLATILITY":    (1.5, 2.5, 3.0),
}

# Minimum R:R per regime (TP1 / SL distance)
_REGIME_MIN_RR: dict[str, float] = {
    "STRONG_TREND_BULL": 1.3,
    "STRONG_TREND_BEAR": 1.3,
    "WEAK_TREND_BULL":   1.2,
    "WEAK_TREND_BEAR":   1.2,
    "RANGING":           1.0,
    "HIGH_VOLATILITY":   1.5,
    "LOW_VOLATILITY":    1.0,
}

# Buffer added/subtracted when aligning SL to S/R levels
_SR_BUFFER_ATR_FRAC = 0.05  # 5% of ATR


class RiskManagerV2:
    """
    Regime-adaptive risk manager.

    Usage:
        rm = RiskManagerV2()
        levels = rm.calculate_levels(
            entry=Decimal("1.1000"),
            atr=Decimal("0.0050"),
            direction="LONG",
            regime="STRONG_TREND_BULL",
            support_levels=[Decimal("1.0970"), Decimal("1.0940")],
        )
    """

    def calculate_levels(
        self,
        entry: Decimal,
        atr: Decimal,
        direction: str,
        regime: str = "RANGING",
        support_levels: Optional[list[Decimal]] = None,
        resistance_levels: Optional[list[Decimal]] = None,
    ) -> dict[str, Optional[Decimal]]:
        """
        Calculate SL, TP1, TP2, TP3 levels.

        SL is placed at (entry ± atr × sl_mult), then optionally snapped
        to the nearest S/R level that is at least as far as the raw SL.

        Returns:
            dict with keys: stop_loss, take_profit_1, take_profit_2,
                            take_profit_3, risk_reward_1
        """
        if direction not in ("LONG", "SHORT"):
            return {
                "stop_loss": None,
                "take_profit_1": None,
                "take_profit_2": None,
                "take_profit_3": None,
                "risk_reward_1": None,
            }

        if regime not in REGIMES:
            regime = "RANGING"

        sl_mult = Decimal(str(_REGIME_SL_MULTIPLIER[regime]))
        tp1_mult, tp2_mult, tp3_mult = [
            Decimal(str(m)) for m in _REGIME_TP[regime]
        ]
        buffer = atr * Decimal(str(_SR_BUFFER_ATR_FRAC))

        if direction == "LONG":
            raw_sl = entry - atr * sl_mult
            sl = self._align_sl_long(
                raw_sl=raw_sl,
                entry=entry,
                support_levels=support_levels,
                buffer=buffer,
            )
            tp1 = entry + atr * tp1_mult
            tp2 = entry + atr * tp2_mult
            tp3 = entry + atr * tp3_mult
        else:  # SHORT
            raw_sl = entry + atr * sl_mult
            sl = self._align_sl_short(
                raw_sl=raw_sl,
                entry=entry,
                resistance_levels=resistance_levels,
                buffer=buffer,
            )
            tp1 = entry - atr * tp1_mult
            tp2 = entry - atr * tp2_mult
            tp3 = entry - atr * tp3_mult

        quant = Decimal("0.00000001")
        sl = sl.quantize(quant, rounding=ROUND_HALF_UP)
        tp1 = tp1.quantize(quant, rounding=ROUND_HALF_UP)
        tp2 = tp2.quantize(quant, rounding=ROUND_HALF_UP)
        tp3 = tp3.quantize(quant, rounding=ROUND_HALF_UP)

        sl_dist = abs(entry - sl)
        tp1_dist = abs(tp1 - entry)
        rr1 = (tp1_dist / sl_dist).quantize(Decimal("0.01")) if sl_dist > 0 else None

        return {
            "stop_loss": sl,
            "take_profit_1": tp1,
            "take_profit_2": tp2,
            "take_profit_3": tp3,
            "risk_reward_1": rr1,
        }

    def calculate_levels_for_regime(
        self,
        entry: Decimal,
        atr: Decimal,
        direction: str,
        regime: str = "RANGING",
        support_levels: Optional[list[Decimal]] = None,
        resistance_levels: Optional[list[Decimal]] = None,
    ) -> dict[str, Optional[Decimal]]:
        """v3: Regime-adaptive SL/TP using R:R-based TP calculation.

        Replaces legacy calculate_levels() which used ATR multiples for TP.
        Here TP is expressed as a multiple of the actual SL distance,
        ensuring consistent risk/reward across regimes.

        SL = entry ± ATR × REGIME_SL_MULTIPLIERS[regime]
        TP1 = entry ± SL_distance × REGIME_TP1_RR[regime]
        TP2 = entry ± SL_distance × REGIME_TP2_RR[regime]
        """
        if direction not in ("LONG", "SHORT"):
            return {
                "stop_loss": None,
                "take_profit_1": None,
                "take_profit_2": None,
                "take_profit_3": None,
                "risk_reward_1": None,
            }

        if regime not in REGIMES:
            regime = "RANGING"

        sl_mult = Decimal(str(REGIME_SL_MULTIPLIERS.get(regime, _DEFAULT_SL_MULT)))
        tp1_rr = Decimal(str(REGIME_TP1_RR.get(regime, _DEFAULT_TP1_RR)))
        tp2_rr = Decimal(str(REGIME_TP2_RR.get(regime, _DEFAULT_TP2_RR)))
        # TP3 uses 1.5× TP2 R:R as a trailing target
        tp3_rr = tp2_rr * Decimal("1.5")

        buffer = atr * Decimal(str(_SR_BUFFER_ATR_FRAC))

        if direction == "LONG":
            raw_sl = entry - atr * sl_mult
            sl = self._align_sl_long(
                raw_sl=raw_sl,
                entry=entry,
                support_levels=support_levels,
                buffer=buffer,
            )
            sl_distance = abs(entry - sl)
            tp1 = entry + sl_distance * tp1_rr
            tp2 = entry + sl_distance * tp2_rr
            tp3 = entry + sl_distance * tp3_rr
        else:  # SHORT
            raw_sl = entry + atr * sl_mult
            sl = self._align_sl_short(
                raw_sl=raw_sl,
                entry=entry,
                resistance_levels=resistance_levels,
                buffer=buffer,
            )
            sl_distance = abs(entry - sl)
            tp1 = entry - sl_distance * tp1_rr
            tp2 = entry - sl_distance * tp2_rr
            tp3 = entry - sl_distance * tp3_rr

        quant = Decimal("0.00000001")
        sl = sl.quantize(quant, rounding=ROUND_HALF_UP)
        tp1 = tp1.quantize(quant, rounding=ROUND_HALF_UP)
        tp2 = tp2.quantize(quant, rounding=ROUND_HALF_UP)
        tp3 = tp3.quantize(quant, rounding=ROUND_HALF_UP)

        actual_sl_dist = abs(entry - sl)
        rr1 = (
            (abs(tp1 - entry) / actual_sl_dist).quantize(Decimal("0.01"))
            if actual_sl_dist > 0
            else None
        )

        return {
            "stop_loss": sl,
            "take_profit_1": tp1,
            "take_profit_2": tp2,
            "take_profit_3": tp3,
            "risk_reward_1": rr1,
        }

    # ── S/R alignment ─────────────────────────────────────────────────────────

    def _align_sl_long(
        self,
        raw_sl: Decimal,
        entry: Decimal,
        support_levels: Optional[list[Decimal]],
        buffer: Decimal,
    ) -> Decimal:
        """
        For LONG: find the support level just below entry that is at or
        below raw_sl and place SL just beneath it (level - buffer).
        If no suitable level found, use raw_sl.
        """
        if not support_levels:
            return raw_sl

        candidates = [
            lvl - buffer
            for lvl in support_levels
            if lvl < entry and (lvl - buffer) <= raw_sl
        ]
        if not candidates:
            return raw_sl

        # Closest to entry (highest value) that is still ≤ raw_sl
        best = max(candidates)
        return best

    def _align_sl_short(
        self,
        raw_sl: Decimal,
        entry: Decimal,
        resistance_levels: Optional[list[Decimal]],
        buffer: Decimal,
    ) -> Decimal:
        """
        For SHORT: find the resistance level just above entry that is at
        or above raw_sl and place SL just above it (level + buffer).
        """
        if not resistance_levels:
            return raw_sl

        candidates = [
            lvl + buffer
            for lvl in resistance_levels
            if lvl > entry and (lvl + buffer) >= raw_sl
        ]
        if not candidates:
            return raw_sl

        # Closest to entry (lowest value) that is still ≥ raw_sl
        best = min(candidates)
        return best

    # ── Validation ────────────────────────────────────────────────────────────

    def validate(
        self,
        entry: Decimal,
        stop_loss: Decimal,
        take_profit_1: Decimal,
        direction: str,
        regime: str = "RANGING",
    ) -> tuple[bool, str]:
        """Return (is_valid, reason)."""
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
        else:
            return False, f"Unknown direction: {direction}"

        sl_dist = abs(entry - stop_loss)
        tp1_dist = abs(take_profit_1 - entry)
        if sl_dist == 0:
            return False, "SL distance is zero"

        rr = float(tp1_dist / sl_dist)
        min_rr = _REGIME_MIN_RR.get(regime, 1.0)
        if rr < min_rr:
            return False, f"R:R {rr:.2f} below minimum {min_rr} for {regime}"

        return True, "OK"

    # ── Position sizing ───────────────────────────────────────────────────────

    def calculate_position_size(
        self,
        account: Decimal,
        risk_pct: float,
        sl_distance: Decimal,
        entry_price: Optional[Decimal] = None,
    ) -> Decimal:
        """
        Position size (% of account) given risk %.

        Formula:
            risk_amount = account × risk_pct / 100
            position_units = risk_amount / sl_distance
            position_pct = (position_units × entry) / account × 100
        """
        if sl_distance <= Decimal("0"):
            return Decimal("0")

        risk_amount = account * Decimal(str(risk_pct)) / Decimal("100")
        units = risk_amount / sl_distance

        if entry_price and entry_price > Decimal("0"):
            value = units * entry_price
            pct = (value / account) * Decimal("100")
        else:
            pct = (units / account) * Decimal("100")

        max_pct = Decimal(str(settings.MAX_RISK_PER_TRADE_PCT)) * Decimal("10")
        pct = min(pct, max_pct)

        return pct.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
