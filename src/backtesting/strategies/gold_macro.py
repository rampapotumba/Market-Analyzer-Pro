"""GoldMacroStrategy — macro-driven XAUUSD (GC=F) strategy on D1 (TASK-V7-24).

Entry logic:
  LONG (safe haven bid) — ONE of two conditions:
    Condition A (risk-off): VIX > 20 AND rising (2-day change > +2),
                            DXY RSI < 50, GC=F > SMA(50)
    Condition B (real rate decline): DXY < SMA(50), GC=F breaks 10-day high,
                                      ADX > 20

  SHORT (risk-on):
    VIX < 15 AND declining,
    DXY RSI > 55,
    GC=F < SMA(50)

Exit rules:
  SL:       2.5 * ATR(14)
  TP:       3.5 * ATR(14)
  Trailing: after 2.0 * ATR profit, trail at 1.5 * ATR
  Time exit: 10 D1 candles (handled by BacktestEngine)

Target instruments: GC=F only.

Graceful degradation: if VIX data or DXY RSI is unavailable, returns None
(no signal) — does not crash or guess.
"""

import bisect
import logging
from decimal import Decimal
from typing import Optional

import numpy as np
import pandas as pd

from src.backtesting.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

TARGET_SYMBOL = "GC=F"

# LONG risk-off thresholds
VIX_RISK_OFF_THRESHOLD = 20.0     # VIX must be above this for risk-off LONG
VIX_RISING_MIN_CHANGE = 2.0       # 2-day VIX change must exceed this

# SHORT risk-on thresholds
VIX_RISK_ON_THRESHOLD = 15.0      # VIX must be below this for risk-on SHORT

# DXY RSI thresholds
DXY_RSI_NEUTRAL_LOW = 50.0        # LONG requires DXY RSI < 50
DXY_RSI_SHORT_THRESHOLD = 55.0    # SHORT requires DXY RSI > 55

# TA periods
SMA_PERIOD = 50
HIGH_BREAK_PERIOD = 10            # Condition B: GC=F breaks 10-day high
ADX_MIN_THRESHOLD = 20.0          # Condition B: ADX > 20

# Exit multipliers (overrides engine defaults for this strategy)
SL_ATR_MULTIPLIER = Decimal("2.5")
TP_ATR_MULTIPLIER = Decimal("3.5")

# Trailing stop: activate after 2.0 * ATR profit, trail at 1.5 * ATR
TRAIL_ACTIVATE_ATR = Decimal("2.0")
TRAIL_DISTANCE_ATR = Decimal("1.5")

# VIX indicator name in macro_data
VIXCLS_INDICATOR = "VIXCLS"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_vix_values_before(
    macro_data: list,
    candle_ts,
) -> list[float]:
    """Return VIX values (sorted ascending by release_date) strictly before candle_ts.

    Returns an empty list if no VIXCLS data is available.
    """
    if not macro_data:
        return []

    vix_rows = [r for r in macro_data if getattr(r, "indicator_name", None) == VIXCLS_INDICATOR]
    if not vix_rows:
        return []

    # Sort ascending by release_date
    def _ts(row):
        rd = getattr(row, "release_date", None)
        if rd is None:
            return None
        if hasattr(rd, "tzinfo") and rd.tzinfo is not None:
            return rd
        import datetime
        return rd.replace(tzinfo=datetime.timezone.utc) if hasattr(rd, "replace") else rd

    vix_rows_with_ts = [(r, _ts(r)) for r in vix_rows if _ts(r) is not None]
    vix_rows_with_ts.sort(key=lambda x: x[1])

    # Normalise candle_ts to UTC-aware
    import datetime
    if hasattr(candle_ts, "tzinfo") and candle_ts.tzinfo is None:
        candle_ts_utc = candle_ts.replace(tzinfo=datetime.timezone.utc)
    else:
        candle_ts_utc = candle_ts

    # Keep only rows strictly before candle_ts
    visible = [r for r, ts in vix_rows_with_ts if ts < candle_ts_utc]
    if not visible:
        return []

    result = []
    for row in visible:
        val = getattr(row, "value", None)
        if val is not None:
            try:
                result.append(float(val))
            except (TypeError, ValueError):
                pass

    return result


def _compute_sma(series: pd.Series, period: int) -> Optional[float]:
    """Compute simple moving average of last `period` values. Returns None if insufficient data."""
    if len(series) < period:
        return None
    return float(series.iloc[-period:].mean())


def _compute_adx(df: pd.DataFrame) -> Optional[float]:
    """Return the most recent ADX value from pre-computed ta_indicators or compute from df."""
    # Try pre-computed arrays first
    if "adx" in df.columns:
        val = df["adx"].iloc[-1]
        if not (isinstance(val, float) and np.isnan(val)):
            return float(val)
    return None


# ── Strategy ──────────────────────────────────────────────────────────────────


class GoldMacroStrategy(BaseStrategy):
    """Macro-driven Gold (GC=F) strategy on D1 timeframe.

    Uses VIX (from macro_data VIXCLS series) and DXY RSI (from context)
    to identify safe-haven bids (LONG) or risk-on reversals (SHORT).

    If VIX or DXY RSI data is unavailable, returns None (graceful degradation).
    """

    def name(self) -> str:
        return "gold_macro"

    def check_entry(self, context: dict) -> Optional[dict]:
        """Check for GoldMacro entry signal.

        Returns entry dict or None.  Only signals for GC=F.
        Requires macro_data with VIXCLS entries in context.
        """
        symbol: str = context.get("symbol", "")
        if symbol != TARGET_SYMBOL:
            return None

        df: Optional[pd.DataFrame] = context.get("df")
        if df is None or len(df) < SMA_PERIOD + 1:
            logger.debug("[GoldMacro] Insufficient price history for %s", symbol)
            return None

        candle_ts = context.get("candle_ts")
        macro_data: list = context.get("macro_data") or []
        ta_indicators: dict = context.get("ta_indicators") or {}
        atr_value: Optional[float] = context.get("atr_value")
        regime: str = context.get("regime") or "DEFAULT"

        # ── VIX data ──────────────────────────────────────────────────────────
        vix_values = _get_vix_values_before(macro_data, candle_ts)
        if len(vix_values) < 2:
            logger.warning(
                "[GoldMacro] VIX data unavailable for %s at %s — skipping signal",
                symbol, candle_ts,
            )
            return None

        vix_latest = vix_values[-1]
        vix_prev = vix_values[-2]
        vix_2day_change = vix_latest - vix_prev

        # ── DXY RSI ───────────────────────────────────────────────────────────
        dxy_rsi: Optional[float] = context.get("dxy_rsi")
        if dxy_rsi is None:
            dxy_rsi = ta_indicators.get("dxy_rsi")
        if dxy_rsi is None:
            logger.warning(
                "[GoldMacro] DXY RSI unavailable for %s at %s — skipping signal",
                symbol, candle_ts,
            )
            return None

        # ── GC=F price indicators ─────────────────────────────────────────────
        close_series: pd.Series = df["close"] if "close" in df.columns else df.iloc[:, 3]

        sma50: Optional[float] = _compute_sma(close_series, SMA_PERIOD)
        if sma50 is None:
            logger.debug("[GoldMacro] Not enough data for SMA(%d)", SMA_PERIOD)
            return None

        current_close = float(close_series.iloc[-1])

        # ADX from ta_indicators array or context
        adx_val: Optional[float] = ta_indicators.get("adx")
        if adx_val is None or (isinstance(adx_val, float) and np.isnan(adx_val)):
            adx_val = None

        # ATR
        if atr_value is None or atr_value == 0.0:
            logger.debug("[GoldMacro] ATR unavailable for %s — cannot compute SL/TP", symbol)
            return None

        atr_decimal = Decimal(str(round(atr_value, 5)))

        # ── Entry condition evaluation ─────────────────────────────────────────
        direction: Optional[str] = self._evaluate_direction(
            vix_latest=vix_latest,
            vix_2day_change=vix_2day_change,
            dxy_rsi=float(dxy_rsi),
            current_close=current_close,
            sma50=sma50,
            adx_val=adx_val,
            close_series=close_series,
        )

        if direction is None:
            return None

        # ── Build signal ───────────────────────────────────────────────────────
        composite = Decimal("15.0") if direction == "LONG" else Decimal("-15.0")

        signal = {
            "direction": direction,
            "entry_price": Decimal(str(round(current_close, 5))),
            "composite_score": composite,
            "regime": regime,
            "atr": atr_decimal,
            "position_pct": 2.0,
            "ta_indicators": ta_indicators,
            "support_levels": [],
            "resistance_levels": [],
            # Strategy-specific exit overrides (consumed by BacktestEngine._recalc_sl_tp)
            "sl_atr_multiplier": SL_ATR_MULTIPLIER,
            "tp_atr_multiplier": TP_ATR_MULTIPLIER,
        }

        logger.debug(
            "[GoldMacro] %s signal at %s — VIX=%.2f dxy_rsi=%.1f close=%.2f sma50=%.2f",
            direction, candle_ts, vix_latest, float(dxy_rsi), current_close, sma50,
        )
        return signal

    # ── Private helpers ────────────────────────────────────────────────────────

    def _evaluate_direction(
        self,
        vix_latest: float,
        vix_2day_change: float,
        dxy_rsi: float,
        current_close: float,
        sma50: float,
        adx_val: Optional[float],
        close_series: pd.Series,
    ) -> Optional[str]:
        """Evaluate trade direction based on macro and TA conditions.

        Returns "LONG", "SHORT", or None.
        """
        above_sma50 = current_close > sma50
        below_sma50 = current_close < sma50

        # ── LONG Condition A: risk-off ────────────────────────────────────────
        cond_a = (
            vix_latest > VIX_RISK_OFF_THRESHOLD
            and vix_2day_change > VIX_RISING_MIN_CHANGE
            and dxy_rsi < DXY_RSI_NEUTRAL_LOW
            and above_sma50
        )

        # ── LONG Condition B: real rate decline ───────────────────────────────
        cond_b = self._check_condition_b(
            dxy_rsi=dxy_rsi,
            current_close=current_close,
            sma50=sma50,
            adx_val=adx_val,
            close_series=close_series,
        )

        if cond_a or cond_b:
            return "LONG"

        # ── SHORT: risk-on ────────────────────────────────────────────────────
        cond_short = (
            vix_latest < VIX_RISK_ON_THRESHOLD
            and vix_2day_change < 0.0
            and dxy_rsi > DXY_RSI_SHORT_THRESHOLD
            and below_sma50
        )

        if cond_short:
            return "SHORT"

        return None

    def _check_condition_b(
        self,
        dxy_rsi: float,
        current_close: float,
        sma50: float,
        adx_val: Optional[float],
        close_series: pd.Series,
    ) -> bool:
        """Check LONG Condition B: real rate decline.

        Requires:
          - DXY < SMA(50): proxied by DXY RSI < 50 (weak DXY momentum)
          - GC=F breaks 10-day high (close exceeds max of previous 10 closes)
          - ADX > 20
        """
        # Proxy for DXY < SMA(50): DXY RSI < 50 means DXY momentum is bearish
        dxy_below_sma = dxy_rsi < DXY_RSI_NEUTRAL_LOW

        # GC=F 10-day high breakout
        if len(close_series) < HIGH_BREAK_PERIOD + 1:
            return False
        prev_10_high = float(close_series.iloc[-(HIGH_BREAK_PERIOD + 1):-1].max())
        breaks_10day_high = current_close > prev_10_high

        # ADX > 20
        adx_strong = adx_val is not None and adx_val > ADX_MIN_THRESHOLD

        return dxy_below_sma and breaks_10day_high and adx_strong
