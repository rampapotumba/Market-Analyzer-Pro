"""Unified signal filter pipeline (SIM-42).

Consolidates all v5 filters (SIM-25..SIM-33, SIM-31, SIM-38) into a single
reusable class that works identically in live signal generation and backtest
simulation.
"""

import datetime
import logging
from decimal import Decimal
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# SIM-31: Allowed signal strengths (weak signals filtered out).
# Mirrors ALLOWED_SIGNAL_STRENGTHS in backtest_engine.py.
ALLOWED_SIGNAL_STRENGTHS: frozenset[str] = frozenset({"BUY", "STRONG_BUY", "SELL", "STRONG_SELL"})

# SIM-38: USD long-side pairs affected by DXY RSI filter.
# Mirrors BacktestEngine._USD_LONG_SIDE_PAIRS.
_USD_LONG_SIDE_PAIRS: frozenset[str] = frozenset({
    "EURUSD=X", "GBPUSD=X", "AUDUSD=X", "NZDUSD=X"
})

# EU/NA forex pairs blocked during Asian low-liquidity session (00:00–06:59 UTC).
# Mirrors BacktestEngine._FOREX_PAIRS_EU_NA and SignalEngine._FOREX_PAIRS_EU_NA.
_FOREX_PAIRS_EU_NA: frozenset[str] = frozenset({
    "EURUSD=X", "GBPUSD=X", "USDCHF=X", "EURGBP=X",
    "EURCAD=X", "GBPCAD=X", "EURCHF=X", "GBPCHF=X",
})
_ASIAN_SESSION_UTC_START = 0   # 00:00 UTC inclusive
_ASIAN_SESSION_UTC_END   = 7   # 07:00 UTC exclusive


def _is_asian_session(candle_ts: datetime.datetime) -> bool:
    """Return True if candle timestamp falls in low-liquidity Asian hours (UTC)."""
    hour = candle_ts.hour if candle_ts.tzinfo else candle_ts.replace(
        tzinfo=datetime.timezone.utc
    ).hour
    return _ASIAN_SESSION_UTC_START <= hour < _ASIAN_SESSION_UTC_END


def _get_signal_strength(composite: float) -> str:
    """Map composite score to signal strength bucket (SIM-14 score buckets).

    Mirrors _get_signal_strength() in backtest_engine.py.

    strong_buy:  >= +15
    buy:          +10..+15
    weak_buy:      +7..+10
    neutral:       -7..+7
    weak_sell:    -10..-7
    sell:         -15..-10
    strong_sell:  <= -15
    """
    return _get_signal_strength_scaled(composite, scale=1.0)


def _get_signal_strength_scaled(composite: float, scale: float = 1.0) -> str:
    """Map composite score to signal strength bucket with scaled thresholds.

    V6 (TASK-V6-02): when only TA is available (scale=0.45), thresholds
    are proportionally reduced so the same quality gates apply.

    Examples:
      scale=1.0 (live):    STRONG_BUY >= 15.0, BUY >= 10.0, WEAK_BUY >= 7.0
      scale=0.45 (backtest): STRONG_BUY >= 6.75, BUY >= 4.5, WEAK_BUY >= 3.15
    """
    abs_score = abs(composite)
    strong_threshold = 15.0 * scale
    buy_threshold = 10.0 * scale
    weak_threshold = 7.0 * scale

    if abs_score >= strong_threshold:
        return "STRONG_BUY" if composite > 0 else "STRONG_SELL"
    elif abs_score >= buy_threshold:
        return "BUY" if composite > 0 else "SELL"
    elif abs_score >= weak_threshold:
        return "WEAK_BUY" if composite > 0 else "WEAK_SELL"
    else:
        return "HOLD"


class SignalFilterPipeline:
    """
    Runs all configurable signal filters in sequence.

    All filters follow graceful degradation: missing data → filter passes.
    Each filter returns True (pass) or False (block).

    Usage:
        pipeline = SignalFilterPipeline(
            apply_score_filter=True,
            apply_regime_filter=True,
            apply_d1_trend_filter=True,
            apply_volume_filter=True,
            apply_momentum_filter=True,
            apply_weekday_filter=True,
            apply_calendar_filter=True,
            min_composite_score=None,  # None = use global config
        )
        passed, reason = pipeline.run_all(context)
    """

    def __init__(
        self,
        apply_score_filter: bool = True,
        apply_regime_filter: bool = True,
        apply_d1_trend_filter: bool = True,
        apply_volume_filter: bool = True,
        apply_momentum_filter: bool = True,
        apply_weekday_filter: bool = True,
        apply_calendar_filter: bool = True,
        apply_session_filter: bool = True,
        apply_dxy_filter: bool = True,
        min_composite_score: Optional[float] = None,
    ) -> None:
        self.apply_score_filter = apply_score_filter
        self.apply_regime_filter = apply_regime_filter
        self.apply_d1_trend_filter = apply_d1_trend_filter
        self.apply_volume_filter = apply_volume_filter
        self.apply_momentum_filter = apply_momentum_filter
        self.apply_weekday_filter = apply_weekday_filter
        self.apply_calendar_filter = apply_calendar_filter
        self.apply_session_filter = apply_session_filter
        self.apply_dxy_filter = apply_dxy_filter
        self.min_composite_score = min_composite_score

    def run_all(self, context: dict) -> tuple[bool, str]:
        """
        Run all enabled filters against the signal context.

        context keys:
          - composite_score: float
          - market_type: str ("forex", "crypto", "stocks")
          - symbol: str
          - regime: str
          - direction: str
          - timeframe: str
          - df: pd.DataFrame (OHLCV)
          - ta_indicators: dict (rsi_14, macd_line, macd_signal, ...)
          - candle_ts: datetime.datetime
          - d1_rows: list (D1 price rows, optional)
          - economic_events: list (optional)

        Returns: (passed: bool, reason: str)
          - passed=True  → all filters passed
          - passed=False → first failed filter + reason
        """
        composite = context.get("composite_score", 0.0)
        market_type = context.get("market_type", "forex")
        symbol = context.get("symbol", "")
        regime = context.get("regime", "DEFAULT")
        direction = context.get("direction", "LONG")
        timeframe = context.get("timeframe", "H1")
        df = context.get("df")
        ta_indicators = context.get("ta_indicators", {})
        candle_ts = context.get("candle_ts")
        d1_rows = context.get("d1_rows", [])
        economic_events = context.get("economic_events", [])
        dxy_rsi = context.get("dxy_rsi")
        # V6 TASK-V6-02: available_weight for proportional threshold scaling.
        # Default 1.0 = live mode (all components available).
        # Backtest passes 0.45 (only TA available).
        available_weight: float = context.get("available_weight", 1.0)

        # Session filter: must run first (cheapest check)
        if self.apply_session_filter and candle_ts is not None:
            passed, reason = self.check_session_liquidity(candle_ts, symbol, market_type)
            if not passed:
                return False, reason

        # SIM-25: Score threshold
        if self.apply_score_filter:
            passed, reason = self.check_score_threshold(
                composite, market_type, symbol, available_weight=available_weight
            )
            if not passed:
                return False, reason

        # SIM-31: Signal strength — always-on quality gate (not flag-controlled).
        # Blocks WEAK_BUY, WEAK_SELL, HOLD regardless of other filter flags.
        passed, reason = self.check_signal_strength(composite, direction, available_weight=available_weight)
        if not passed:
            return False, reason

        # SIM-26: RANGING regime block
        if self.apply_regime_filter:
            passed, reason = self.check_regime(regime, symbol)
            if not passed:
                return False, reason

        # SIM-27: D1 MA200 trend filter
        if self.apply_d1_trend_filter:
            passed, reason = self.check_d1_trend(symbol, direction, timeframe, d1_rows)
            if not passed:
                return False, reason

        # SIM-29: Volume confirmation
        if self.apply_volume_filter and df is not None:
            passed, reason = self.check_volume(df, market_type)
            if not passed:
                return False, reason

        # SIM-30: Momentum alignment
        if self.apply_momentum_filter:
            passed, reason = self.check_momentum(ta_indicators, direction)
            if not passed:
                return False, reason

        # SIM-38: DXY alignment filter
        if self.apply_dxy_filter:
            passed, reason = self.check_dxy_alignment(direction, symbol, dxy_rsi)
            if not passed:
                return False, reason

        # SIM-32: Weekday filter
        if self.apply_weekday_filter and candle_ts is not None:
            passed, reason = self.check_weekday(candle_ts, market_type)
            if not passed:
                return False, reason

        # SIM-33: Economic calendar
        if self.apply_calendar_filter:
            passed, reason = self.check_calendar(candle_ts, economic_events)
            if not passed:
                return False, reason

        return True, "all_passed"

    def check_score_threshold(
        self,
        composite: float,
        market_type: str,
        symbol: str,
        available_weight: float = 1.0,
    ) -> tuple[bool, str]:
        """SIM-25 + SIM-28 + V6-TASK-V6-02: composite score must exceed threshold.

        Priority (highest to lowest):
          1. instrument_override (INSTRUMENT_OVERRIDES[symbol])
          2. constructor min_composite_score (self.min_composite_score)
          3. market_default (MIN_COMPOSITE_SCORE_CRYPTO for crypto)
          4. global_default (MIN_COMPOSITE_SCORE)

        V6 (TASK-V6-02): effective_threshold = threshold * available_weight.
        In backtest (only TA, available_weight=0.45): threshold 15 → 6.75.
        In live (all components, available_weight=1.0): threshold 15 → 15.0.
        """
        from src.config import INSTRUMENT_OVERRIDES, MIN_COMPOSITE_SCORE, MIN_COMPOSITE_SCORE_CRYPTO

        threshold = MIN_COMPOSITE_SCORE_CRYPTO if market_type == "crypto" else MIN_COMPOSITE_SCORE
        # Constructor override takes precedence over market default
        if self.min_composite_score is not None:
            threshold = self.min_composite_score
        # SIM-28: per-symbol override has highest priority
        overrides = INSTRUMENT_OVERRIDES.get(symbol, {})
        if "min_composite_score" in overrides:
            threshold = overrides["min_composite_score"]

        # V6 TASK-V6-02: proportional scaling
        effective_threshold = threshold * available_weight

        if abs(composite) < effective_threshold:
            return False, f"score_below_threshold:{composite:.1f}<{effective_threshold:.2f}"
        return True, "ok"

    def check_regime(self, regime: str, symbol: str = "") -> tuple[bool, str]:
        """SIM-26 + SIM-28: regime must not be blocked."""
        from src.config import BLOCKED_REGIMES, INSTRUMENT_OVERRIDES

        if regime in BLOCKED_REGIMES:
            return False, f"regime_blocked:{regime}"
        # SIM-28: allowed_regimes override
        overrides = INSTRUMENT_OVERRIDES.get(symbol, {})
        allowed = overrides.get("allowed_regimes")
        if allowed and regime not in allowed:
            return False, f"regime_not_in_allowed:{regime}"
        return True, "ok"

    def check_d1_trend(
        self, symbol: str, direction: str, timeframe: str, d1_rows: list
    ) -> tuple[bool, str]:
        """SIM-27: D1 MA200 alignment filter."""
        if timeframe in ("M1", "M5", "M15"):
            return True, "ok"  # not applied
        if not d1_rows or len(d1_rows) < 200:
            logger.warning(
                "[SIM-27] Insufficient D1 data (%d/200) for %s",
                len(d1_rows) if d1_rows else 0,
                symbol,
            )
            return True, "ok"  # graceful degradation
        closes = [float(r.close) for r in d1_rows[-200:]]
        ma200 = sum(closes) / len(closes)
        current_close = closes[-1]
        if direction == "LONG" and current_close < ma200:
            return False, f"d1_bearish_trend:close={current_close:.5f}<ma200={ma200:.5f}"
        if direction == "SHORT" and current_close > ma200:
            return False, f"d1_bullish_trend:close={current_close:.5f}>ma200={ma200:.5f}"
        return True, "ok"

    def check_volume(self, df: pd.DataFrame, market_type: str = "forex") -> tuple[bool, str]:
        """SIM-29: Volume >= 120% of MA20.

        Known limitation: yfinance does not provide volume data for forex pairs
        (all values are 0). The filter is explicitly skipped for forex to avoid
        false positives. It remains active for stocks and crypto.
        """
        if market_type == "forex":
            logger.debug("[SIM-29] Volume filter skipped for forex (no volume data from provider)")
            return True, "ok"
        if df["volume"].sum() == 0:
            return True, "ok"
        if len(df) < 20:
            return True, "ok"
        vol_ma20 = df["volume"].rolling(20).mean().iloc[-1]
        if pd.isna(vol_ma20) or vol_ma20 == 0:
            return True, "ok"
        current_vol = df["volume"].iloc[-1]
        if current_vol < vol_ma20 * 1.2:
            return False, f"volume_low:{current_vol:.0f}<{vol_ma20 * 1.2:.0f}"
        return True, "ok"

    def check_momentum(self, ta_indicators: dict, direction: str) -> tuple[bool, str]:
        """SIM-30: RSI and MACD alignment."""
        rsi = ta_indicators.get("rsi_14") or ta_indicators.get("rsi")
        macd_line = ta_indicators.get("macd_line") or ta_indicators.get("macd")
        macd_signal = ta_indicators.get("macd_signal") or ta_indicators.get("macd_signal_line")
        if rsi is None or macd_line is None or macd_signal is None:
            return True, "ok"
        try:
            rsi_f, macd_f, sig_f = float(rsi), float(macd_line), float(macd_signal)
        except (TypeError, ValueError):
            return True, "ok"
        logger.debug(
            "[SIM-30] Momentum check: rsi=%.1f, macd=%.5f, signal=%.5f, direction=%s",
            rsi_f, macd_f, sig_f, direction,
        )
        if direction == "LONG" and not (rsi_f > 50 and macd_f > sig_f):
            return False, f"momentum_misaligned_long:rsi={rsi_f:.1f},macd_diff={macd_f - sig_f:.5f}"
        if direction == "SHORT" and not (rsi_f < 50 and macd_f < sig_f):
            return False, f"momentum_misaligned_short:rsi={rsi_f:.1f},macd_diff={macd_f - sig_f:.5f}"
        return True, "ok"

    def check_weekday(self, ts: datetime.datetime, market_type: str) -> tuple[bool, str]:
        """SIM-32: Weekday filter."""
        hour = ts.hour if ts.tzinfo else ts.replace(tzinfo=datetime.timezone.utc).hour
        weekday = ts.weekday()
        if weekday == 0 and hour < 10:
            if market_type == "crypto":
                return True, "ok"  # crypto exempt
            return False, f"monday_gap_filter:hour={hour}"
        if weekday == 4 and hour >= 18:
            return False, f"friday_close_filter:hour={hour}"
        return True, "ok"

    def check_calendar(
        self,
        candle_ts: Optional[datetime.datetime],
        economic_events: list,
    ) -> tuple[bool, str]:
        """SIM-33: Economic calendar ±2h filter."""
        if not economic_events or candle_ts is None:
            return True, "ok"
        window = datetime.timedelta(hours=2)
        for event in economic_events:
            event_dt = getattr(event, "event_date", None) or getattr(event, "scheduled_at", None)
            if event_dt is None:
                continue
            if abs((candle_ts - event_dt).total_seconds()) <= window.total_seconds():
                return False, "economic_calendar_block"
        return True, "ok"

    def check_signal_strength(
        self,
        composite: float,
        direction: str,
        available_weight: float = 1.0,
    ) -> tuple[bool, str]:
        """SIM-31 + V6-TASK-V6-02: Signal strength must be BUY/STRONG_BUY or SELL/STRONG_SELL.

        This is NOT flag-controlled — it is a fundamental quality gate that
        always runs regardless of configuration flags.

        V6: uses scaled thresholds when available_weight < 1.0 (backtest mode).
        """
        strength = _get_signal_strength_scaled(composite, scale=available_weight)
        if strength not in ALLOWED_SIGNAL_STRENGTHS:
            logger.debug(
                "[SIM-31] Filtered weak signal: %s (score=%.2f, direction=%s, scale=%.2f)",
                strength, composite, direction, available_weight,
            )
            return False, f"signal_strength_weak:{strength}"
        return True, "ok"

    def check_dxy_alignment(
        self, direction: str, symbol: str, dxy_rsi: Optional[float]
    ) -> tuple[bool, str]:
        """SIM-38: DXY RSI filter for forex USD long-side pairs.

        DXY RSI > 55 → USD strong → block LONG for EUR/GBP/AUD/NZD vs USD.
        DXY RSI < 45 → USD weak → block SHORT for the same pairs.
        45–55 → neutral, no filter.
        No DXY data → passthrough (graceful degradation).
        """
        if dxy_rsi is None:
            return True, "ok"
        if symbol not in _USD_LONG_SIDE_PAIRS:
            return True, "ok"
        if dxy_rsi > 55 and direction == "LONG":
            logger.debug("[SIM-38] DXY RSI=%.1f > 55: blocking LONG for %s", dxy_rsi, symbol)
            return False, f"dxy_strong_blocks_long:{symbol}"
        if dxy_rsi < 45 and direction == "SHORT":
            logger.debug("[SIM-38] DXY RSI=%.1f < 45: blocking SHORT for %s", dxy_rsi, symbol)
            return False, f"dxy_weak_blocks_short:{symbol}"
        return True, "ok"

    def check_session_liquidity(
        self,
        candle_ts: datetime.datetime,
        symbol: str,
        market_type: str,
    ) -> tuple[bool, str]:
        """Block EU/NA forex pairs during Asian session (00:00–06:59 UTC).

        Non-forex instruments and non-EU/NA pairs are always allowed.
        """
        if market_type != "forex":
            return True, "ok"
        if symbol not in _FOREX_PAIRS_EU_NA:
            return True, "ok"
        if _is_asian_session(candle_ts):
            return False, f"asian_session_block:{symbol}"
        return True, "ok"
