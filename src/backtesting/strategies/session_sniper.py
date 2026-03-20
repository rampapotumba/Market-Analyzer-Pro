"""SessionSniperStrategy — H1 session-based entry strategy (TASK-V7-22).

Trades London open (07:00-09:00 UTC) and NY open (13:00-15:00 UTC) on
major forex pairs only.  Entry is confirmed by momentum (price direction),
RSI range, above-average ATR volatility, and SMA(20) position.

Exit:
  - SL: 1.5 * ATR(14)
  - TP1: 2.0 * ATR (50%), TP2: 3.0 * ATR (50%)
  - Time exit: 6 H1 candles
  - Hard close at 16:00 UTC

Weekday filter: Monday (0) and Friday (4) are excluded.
"""

import logging
from decimal import Decimal
from typing import Optional

import pandas as pd

from src.backtesting.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

LONDON_OPEN_START_HOUR = 7
LONDON_OPEN_END_HOUR = 9   # exclusive: [07, 09)

NY_OPEN_START_HOUR = 13
NY_OPEN_END_HOUR = 15      # exclusive: [13, 15)

HARD_CLOSE_HOUR = 16

# Weekdays (Python weekday(): 0=Monday, 6=Sunday)
BLOCKED_WEEKDAYS = {0, 4}  # Monday, Friday

TARGET_SYMBOLS = {"EURUSD=X", "GBPUSD=X", "AUDUSD=X", "USDCAD=X"}

# RSI ranges (inclusive)
RSI_LONG_MIN = 45.0
RSI_LONG_MAX = 65.0
RSI_SHORT_MIN = 35.0
RSI_SHORT_MAX = 55.0

# ATR volatility threshold
ATR_VOL_MULTIPLIER = 1.2

# Lookback periods
ATR_PERIOD = 14
ATR_MA_PERIOD = 20
SMA_PERIOD = 20

# Position sizing and SL/TP multipliers
SL_ATR_MULTIPLIER = Decimal("1.5")
TP1_ATR_MULTIPLIER = Decimal("2.0")
TP2_ATR_MULTIPLIER = Decimal("3.0")
DEFAULT_POSITION_PCT = 2.0

TIME_EXIT_CANDLES = 6


def _compute_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> Optional[float]:
    """Compute ATR(period) using Wilder smoothing.  Returns None if data is insufficient."""
    required = period + 1
    if len(df) < required:
        return None

    high = df["high"].values
    low = df["low"].values
    close = df["close"].values

    tr_values = []
    for i in range(1, len(high)):
        tr = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
        tr_values.append(tr)

    if len(tr_values) < period:
        return None

    # Seed with simple average of first `period` TRs
    atr = sum(tr_values[:period]) / period
    for tr in tr_values[period:]:
        atr = (atr * (period - 1) + tr) / period

    return atr


def _compute_atr_series(df: pd.DataFrame, period: int = ATR_PERIOD) -> Optional[pd.Series]:
    """Return per-row ATR series (Wilder) aligned to df index.  Returns None if insufficient data."""
    if len(df) < period + 1:
        return None

    high = df["high"].values
    low = df["low"].values
    close = df["close"].values

    tr_list = [float("nan")]  # no TR for first row
    for i in range(1, len(close)):
        tr = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
        tr_list.append(tr)

    tr_series = pd.Series(tr_list, index=df.index)

    atr_vals = [float("nan")] * len(tr_series)
    # Seed at index `period` with simple mean of tr[1..period]
    first_seed_idx = period
    if len(tr_series) <= first_seed_idx:
        return None

    seed = float(tr_series.iloc[1 : period + 1].mean())
    atr_vals[first_seed_idx] = seed

    for i in range(first_seed_idx + 1, len(tr_series)):
        prev = atr_vals[i - 1]
        if pd.isna(prev):
            continue
        atr_vals[i] = (prev * (period - 1) + tr_series.iloc[i]) / period

    return pd.Series(atr_vals, index=df.index)


def _compute_sma(df: pd.DataFrame, period: int = SMA_PERIOD) -> Optional[float]:
    """Return SMA(period) of the last row.  None if insufficient data."""
    if len(df) < period:
        return None
    return float(df["close"].iloc[-period:].mean())


def _compute_rsi(df: pd.DataFrame, period: int = ATR_PERIOD) -> Optional[float]:
    """Return RSI(period) at the last bar.  None if insufficient data."""
    if len(df) < period + 1:
        return None

    delta = df["close"].diff().dropna()
    if len(delta) < period:
        return None

    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    avg_gain = float(gain.iloc[:period].mean())
    avg_loss = float(loss.iloc[:period].mean())

    for i in range(period, len(delta)):
        avg_gain = (avg_gain * (period - 1) + float(gain.iloc[i])) / period
        avg_loss = (avg_loss * (period - 1) + float(loss.iloc[i])) / period

    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _is_session_window(hour: int) -> Optional[str]:
    """Return session name if the hour falls inside a session window, else None."""
    if LONDON_OPEN_START_HOUR <= hour < LONDON_OPEN_END_HOUR:
        return "london"
    if NY_OPEN_START_HOUR <= hour < NY_OPEN_END_HOUR:
        return "ny"
    return None


class SessionSniperStrategy(BaseStrategy):
    """H1 session-open sniper: trades London and NY open breakouts on major forex pairs."""

    def name(self) -> str:
        return "session_sniper"

    def check_entry(self, context: dict) -> Optional[dict]:
        symbol: str = context.get("symbol", "")
        timeframe: str = context.get("timeframe", "")
        candle_ts = context.get("candle_ts")
        df: Optional[pd.DataFrame] = context.get("df")
        market_type: str = context.get("market_type", "")

        # ── Pre-condition guards ───────────────────────────────────────────────

        if market_type != "forex":
            logger.debug("[SessionSniper] %s skipped — market_type=%s (forex only)", symbol, market_type)
            return None

        if symbol not in TARGET_SYMBOLS:
            logger.debug("[SessionSniper] %s skipped — not in TARGET_SYMBOLS", symbol)
            return None

        if candle_ts is None:
            logger.warning("[SessionSniper] candle_ts is None — skipping")
            return None

        if df is None or len(df) < max(ATR_PERIOD + ATR_MA_PERIOD, SMA_PERIOD) + 1:
            logger.warning("[SessionSniper] %s skipped — insufficient OHLCV data", symbol)
            return None

        # ── Weekday filter ─────────────────────────────────────────────────────
        weekday = candle_ts.weekday()
        if weekday in BLOCKED_WEEKDAYS:
            logger.debug("[SessionSniper] %s skipped — blocked weekday %d", symbol, weekday)
            return None

        # ── Session window check ───────────────────────────────────────────────
        session = _is_session_window(candle_ts.hour)
        if session is None:
            return None

        # ── Indicator computation ──────────────────────────────────────────────
        rsi = _compute_rsi(df)
        if rsi is None:
            logger.warning("[SessionSniper] %s — RSI computation failed, skipping", symbol)
            return None

        atr = _compute_atr(df)
        if atr is None or atr == 0.0:
            logger.warning("[SessionSniper] %s — ATR computation failed, skipping", symbol)
            return None

        # ATR MA20: mean of the last ATR_MA_PERIOD ATR values
        atr_series = _compute_atr_series(df)
        if atr_series is None:
            logger.warning("[SessionSniper] %s — ATR series computation failed, skipping", symbol)
            return None

        atr_ma_values = atr_series.dropna().iloc[-ATR_MA_PERIOD:]
        if len(atr_ma_values) < ATR_MA_PERIOD:
            logger.warning(
                "[SessionSniper] %s — not enough ATR history for MA%d, skipping",
                symbol,
                ATR_MA_PERIOD,
            )
            return None

        atr_ma20 = float(atr_ma_values.mean())
        if atr_ma20 == 0.0:
            logger.warning("[SessionSniper] %s — ATR MA20 is zero, skipping", symbol)
            return None

        sma20 = _compute_sma(df)
        if sma20 is None:
            logger.warning("[SessionSniper] %s — SMA20 computation failed, skipping", symbol)
            return None

        current_close = float(df["close"].iloc[-1])
        prev_close = float(df["close"].iloc[-2]) if len(df) >= 2 else current_close

        # ── Volatility filter (same for both directions) ───────────────────────
        if atr < ATR_VOL_MULTIPLIER * atr_ma20:
            logger.debug(
                "[SessionSniper] %s — below-average ATR %.5f < %.5f * %.5f",
                symbol,
                atr,
                ATR_VOL_MULTIPLIER,
                atr_ma20,
            )
            return None

        # ── Direction determination ────────────────────────────────────────────
        direction: Optional[str] = None

        if (
            current_close > prev_close
            and RSI_LONG_MIN <= rsi <= RSI_LONG_MAX
            and current_close > sma20
        ):
            direction = "LONG"
        elif (
            current_close < prev_close
            and RSI_SHORT_MIN <= rsi <= RSI_SHORT_MAX
            and current_close < sma20
        ):
            direction = "SHORT"

        if direction is None:
            logger.debug(
                "[SessionSniper] %s session=%s — no direction signal (rsi=%.1f close=%.5f prev=%.5f sma=%.5f)",
                symbol,
                session,
                rsi,
                current_close,
                prev_close,
                sma20,
            )
            return None

        atr_dec = Decimal(str(round(atr, 8)))
        current_close_dec = Decimal(str(current_close))

        if direction == "LONG":
            sl_price = current_close_dec - SL_ATR_MULTIPLIER * atr_dec
            tp1_price = current_close_dec + TP1_ATR_MULTIPLIER * atr_dec
            tp2_price = current_close_dec + TP2_ATR_MULTIPLIER * atr_dec
        else:
            sl_price = current_close_dec + SL_ATR_MULTIPLIER * atr_dec
            tp1_price = current_close_dec - TP1_ATR_MULTIPLIER * atr_dec
            tp2_price = current_close_dec - TP2_ATR_MULTIPLIER * atr_dec

        logger.info(
            "[SessionSniper] %s %s signal at %s session=%s rsi=%.1f atr=%.5f",
            symbol,
            direction,
            candle_ts,
            session,
            rsi,
            atr,
        )

        return {
            "direction": direction,
            "entry_price": current_close_dec,
            "composite_score": Decimal("0"),
            "regime": context.get("regime", "UNKNOWN"),
            "atr": atr_dec,
            "position_pct": DEFAULT_POSITION_PCT,
            "ta_indicators": {
                "rsi": rsi,
                "atr": atr,
                "sma20": sma20,
                "atr_ma20": atr_ma20,
                "session": session,
            },
            "support_levels": [],
            "resistance_levels": [],
            # Exit metadata for the engine
            "sl_price": sl_price,
            "tp1_price": tp1_price,
            "tp2_price": tp2_price,
            "time_exit_candles": TIME_EXIT_CANDLES,
            "hard_close_hour": HARD_CLOSE_HOUR,
        }
