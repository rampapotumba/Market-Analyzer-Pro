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

# ── SIM-19: Regime-adaptive SL multiplier map (v4) ───────────────────────────
# Wider SL → fewer premature stop-outs → position size shrinks to keep $ risk constant.
# Formula: position_size = risk_amount / sl_distance
# Use ATR_SL_MULTIPLIER_MAP in all v4 code; REGIME_SL_MULTIPLIERS kept for legacy.
ATR_SL_MULTIPLIER_MAP: dict[str, float] = {
    "STRONG_TREND_BULL": 1.5,  # trend is clear — SL can be tighter
    "STRONG_TREND_BEAR": 1.5,
    "TREND_BULL":        2.0,  # standard
    "TREND_BEAR":        2.0,
    "RANGING":           1.5,  # SL behind range boundary, not too wide
    "VOLATILE":          2.5,  # high volatility — wide buffer needed
    "DEFAULT":           2.0,  # fallback for any unknown regime
    # Legacy aliases (old detector regime names → v4 semantics)
    "WEAK_TREND_BULL":   2.0,
    "WEAK_TREND_BEAR":   2.0,
    "HIGH_VOLATILITY":   2.5,
    "LOW_VOLATILITY":    1.5,
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

# ── SIM-18: Dynamic R:R table per market regime ───────────────────────────────
# target_rr: TP1 = entry ± SL_distance × target_rr
# min_rr:    reject level-snap if it would reduce R:R below this threshold
# V6-CAL2-05: R:R ratios reduced by ~30% across all regimes.
# Motivation: 75% trades exit by time_exit, only 10% reach TP.
# TP targets were too far — price doesn't reach TP within 24 candles.
REGIME_RR_MAP: dict[str, dict[str, float]] = {
    "STRONG_TREND_BULL": {"min_rr": 1.4, "target_rr": 1.75},   # was 2.0/2.5
    "STRONG_TREND_BEAR": {"min_rr": 1.4, "target_rr": 1.75},   # was 2.0/2.5
    "TREND_BULL":        {"min_rr": 1.0, "target_rr": 1.4},    # was 1.5/2.0
    "TREND_BEAR":        {"min_rr": 1.0, "target_rr": 1.4},    # was 1.5/2.0
    "RANGING":           {"min_rr": 0.7, "target_rr": 0.9},    # was 1.0/1.3
    "VOLATILE":          {"min_rr": 1.0, "target_rr": 1.4},    # was 1.5/2.0
    "DEFAULT":           {"min_rr": 0.9, "target_rr": 1.05},   # was 1.3/1.5
    # Legacy aliases — same reduction
    "WEAK_TREND_BULL":   {"min_rr": 1.0, "target_rr": 1.4},
    "WEAK_TREND_BEAR":   {"min_rr": 1.0, "target_rr": 1.4},
    "HIGH_VOLATILITY":   {"min_rr": 1.0, "target_rr": 1.4},
    "LOW_VOLATILITY":    {"min_rr": 0.9, "target_rr": 1.05},
}

_TP_SNAP_BAND = Decimal("0.20")  # ±20% of tp1 — look for S/R to snap to

# Regimes where TP3 is unrealistic — lateral/volatile markets rarely sustain 3.75R moves
_NO_TP3_REGIMES: frozenset[str] = frozenset({"RANGING", "HIGH_VOLATILITY"})

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
        sl_atr_multiplier_override: Optional[float] = None,
    ) -> dict[str, Optional[Decimal]]:
        """v3: Regime-adaptive SL/TP using R:R-based TP calculation.

        Replaces legacy calculate_levels() which used ATR multiples for TP.
        Here TP is expressed as a multiple of the actual SL distance,
        ensuring consistent risk/reward across regimes.

        SL = entry ± ATR × REGIME_SL_MULTIPLIERS[regime]
        TP1 = entry ± SL_distance × REGIME_TP1_RR[regime]
        TP2 = entry ± SL_distance × REGIME_TP2_RR[regime]

        sl_atr_multiplier_override: SIM-28 — if provided, use instead of ATR_SL_MULTIPLIER_MAP lookup.
        """
        if direction not in ("LONG", "SHORT"):
            return {
                "stop_loss": None,
                "take_profit_1": None,
                "take_profit_2": None,
                "take_profit_3": None,
                "risk_reward_1": None,
            }

        # SIM-19: use ATR_SL_MULTIPLIER_MAP with DEFAULT fallback (any unknown regime → 2.0)
        # SIM-28: if override provided, use it instead of map lookup
        if sl_atr_multiplier_override is not None:
            sl_mult = Decimal(str(sl_atr_multiplier_override))
        else:
            sl_mult = Decimal(str(ATR_SL_MULTIPLIER_MAP.get(regime, ATR_SL_MULTIPLIER_MAP["DEFAULT"])))
        # SIM-18: use REGIME_RR_MAP for target_rr (TP1) and min_rr (snap validation)
        rr_cfg = REGIME_RR_MAP.get(regime, REGIME_RR_MAP["DEFAULT"])
        target_rr = Decimal(str(rr_cfg["target_rr"]))
        min_rr = Decimal(str(rr_cfg["min_rr"]))
        tp2_rr = Decimal(str(REGIME_TP2_RR.get(regime, _DEFAULT_TP2_RR)))
        # TP3 uses 1.5× TP2 R:R as a trailing target, but not for lateral/volatile regimes
        tp3_rr = tp2_rr * Decimal("1.5") if regime not in _NO_TP3_REGIMES else None

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
            tp1_calc = entry + sl_distance * target_rr
            # SIM-18: level snap — if a resistance level is near TP1, snap to it
            tp1 = self._snap_tp_to_level(
                tp1_calc=tp1_calc,
                entry=entry,
                sl_distance=sl_distance,
                min_rr=min_rr,
                levels=resistance_levels,
                direction="LONG",
            )
            tp2 = entry + sl_distance * tp2_rr
            tp3 = (entry + sl_distance * tp3_rr) if tp3_rr is not None else None
        else:  # SHORT
            raw_sl = entry + atr * sl_mult
            sl = self._align_sl_short(
                raw_sl=raw_sl,
                entry=entry,
                resistance_levels=resistance_levels,
                buffer=buffer,
            )
            sl_distance = abs(entry - sl)
            tp1_calc = entry - sl_distance * target_rr
            # SIM-18: level snap — if a support level is near TP1, snap to it
            tp1 = self._snap_tp_to_level(
                tp1_calc=tp1_calc,
                entry=entry,
                sl_distance=sl_distance,
                min_rr=min_rr,
                levels=support_levels,
                direction="SHORT",
            )
            tp2 = entry - sl_distance * tp2_rr
            tp3 = (entry - sl_distance * tp3_rr) if tp3_rr is not None else None

        quant = Decimal("0.00000001")
        sl = sl.quantize(quant, rounding=ROUND_HALF_UP)
        tp1 = tp1.quantize(quant, rounding=ROUND_HALF_UP)
        tp2 = tp2.quantize(quant, rounding=ROUND_HALF_UP)
        tp3 = tp3.quantize(quant, rounding=ROUND_HALF_UP) if tp3 is not None else None

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

    # ── SIM-18: TP level snap ─────────────────────────────────────────────────

    def _snap_tp_to_level(
        self,
        tp1_calc: Decimal,
        entry: Decimal,
        sl_distance: Decimal,
        min_rr: Decimal,
        levels: Optional[list[Decimal]],
        direction: str,
    ) -> Decimal:
        """Snap TP1 to nearest S/R level within ±20% band.

        If a level exists in [tp1_calc × 0.8, tp1_calc × 1.2]:
          - Snap tp1 to that level
          - If resulting R:R < min_rr → revert to tp1_calc (calculated value)
        """
        if not levels or sl_distance <= Decimal("0"):
            return tp1_calc

        lo = tp1_calc * (Decimal("1") - _TP_SNAP_BAND)
        hi = tp1_calc * (Decimal("1") + _TP_SNAP_BAND)

        candidates = [lvl for lvl in levels if lo <= lvl <= hi]
        if not candidates:
            return tp1_calc

        # Pick the closest level to tp1_calc
        snapped = min(candidates, key=lambda lvl: abs(lvl - tp1_calc))
        snapped_rr = abs(snapped - entry) / sl_distance

        if snapped_rr < min_rr:
            logger.debug(
                "[SIM-18] TP snap rejected: snapped R:R %.2f < min_rr %.2f — reverting to calc",
                float(snapped_rr), float(min_rr),
            )
            return tp1_calc

        logger.debug(
            "[SIM-18] TP snapped from %.5f to %.5f (R:R %.2f)",
            float(tp1_calc), float(snapped), float(snapped_rr),
        )
        return snapped

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

        max_pct = Decimal(str(settings.MAX_RISK_PER_TRADE_PCT))
        pct = min(pct, max_pct)

        return pct.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
