"""DivergenceHunterStrategy — H4 RSI divergence strategy (TASK-V7-25).

Detects classic bullish/bearish RSI divergence on H4 timeframe:
  - Bullish: price makes lower low, RSI makes higher low (long signal)
  - Bearish: price makes higher high, RSI makes lower high (short signal)

Entry conditions:
  - Divergence confirmed
  - D1 close > SMA(200) for LONG / D1 close < SMA(200) for SHORT
  - Volume > MA(20) for stocks/crypto (skipped for forex)

Exit rules (returned in signal dict; BacktestEngine handles execution):
  - SL: below/above swing low/high + 0.5 * ATR(14)
  - TP1: 1.5 * SL distance
  - TP2: 3.0 * SL distance
  - Time exit: 20 H4 candles (~3.3 days)
"""

import logging
from decimal import Decimal
from typing import Optional

import numpy as np
import pandas as pd

from src.backtesting.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Minimum candles between two consecutive swing points
_MIN_SWING_GAP = 3

# RSI period
_RSI_PERIOD = 14

# Volume moving average period
_VOL_MA_PERIOD = 20

# Minimum volume multiplier relative to MA
_VOL_MA_MULTIPLIER = 1.2

# SL buffer as fraction of ATR
_SL_ATR_BUFFER = Decimal("0.5")

# TP multipliers of SL distance
_TP1_MULTIPLIER = Decimal("1.5")
_TP2_MULTIPLIER = Decimal("3.0")

# Time exit in candles (H4 specific)
TIME_EXIT_CANDLES = 20

# D1 trend SMA period
_D1_SMA_PERIOD = 200

# Number of recent bars to search for swing points
_SWING_LOOKBACK = 60


# ── Swing detection helpers ────────────────────────────────────────────────────


def find_swing_lows(lows: np.ndarray, min_gap: int = _MIN_SWING_GAP) -> list[int]:
    """Return indices of swing lows in the lows array.

    A swing low at index i satisfies: lows[i] < lows[i-1] AND lows[i] < lows[i+1].
    Consecutive swings must be at least min_gap candles apart.

    Args:
        lows: 1-D array of low prices.
        min_gap: Minimum number of candles between two swing lows.

    Returns:
        List of bar indices where swing lows are detected (ascending order).
    """
    indices: list[int] = []
    last_idx = -min_gap - 1

    for i in range(1, len(lows) - 1):
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            if i - last_idx >= min_gap:
                indices.append(i)
                last_idx = i

    return indices


def find_swing_highs(highs: np.ndarray, min_gap: int = _MIN_SWING_GAP) -> list[int]:
    """Return indices of swing highs in the highs array.

    A swing high at index i satisfies: highs[i] > highs[i-1] AND highs[i] > highs[i+1].
    Consecutive swings must be at least min_gap candles apart.

    Args:
        highs: 1-D array of high prices.
        min_gap: Minimum number of candles between two swing highs.

    Returns:
        List of bar indices where swing highs are detected (ascending order).
    """
    indices: list[int] = []
    last_idx = -min_gap - 1

    for i in range(1, len(highs) - 1):
        if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
            if i - last_idx >= min_gap:
                indices.append(i)
                last_idx = i

    return indices


# ── RSI calculation ────────────────────────────────────────────────────────────


def compute_rsi(closes: np.ndarray, period: int = _RSI_PERIOD) -> np.ndarray:
    """Compute RSI using Wilder's smoothing method.

    Args:
        closes: Array of close prices (at least period + 1 elements).
        period: RSI period (default 14).

    Returns:
        Array of RSI values, same length as closes.  The first `period` values
        are NaN (insufficient history).
    """
    if len(closes) < period + 1:
        return np.full(len(closes), np.nan)

    rsi = np.full(len(closes), np.nan)
    deltas = np.diff(closes)

    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Initial average
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            rsi[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i + 1] = 100.0 - (100.0 / (1.0 + rs))

    return rsi


# ── ATR calculation ────────────────────────────────────────────────────────────


def compute_atr(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int = _RSI_PERIOD,
) -> float:
    """Compute ATR using Wilder's smoothing.

    Args:
        highs, lows, closes: Price arrays (same length, at least period + 1).
        period: ATR period (default 14).

    Returns:
        Most recent ATR value, or 0.0 if insufficient data.
    """
    if len(closes) < period + 1:
        return 0.0

    tr_values: list[float] = []
    for i in range(1, len(closes)):
        high_low = highs[i] - lows[i]
        high_prev_close = abs(highs[i] - closes[i - 1])
        low_prev_close = abs(lows[i] - closes[i - 1])
        tr_values.append(max(high_low, high_prev_close, low_prev_close))

    if not tr_values:
        return 0.0

    atr = float(np.mean(tr_values[:period]))
    for val in tr_values[period:]:
        atr = (atr * (period - 1) + val) / period

    return atr


# ── Strategy ───────────────────────────────────────────────────────────────────


class DivergenceHunterStrategy(BaseStrategy):
    """H4 RSI divergence strategy.

    Looks for classic bullish/bearish RSI divergence with D1 trend confirmation
    and optional volume filter.

    Signal is generated on the candle *after* the divergence is confirmed so
    there is no lookahead bias.  The strategy marks the next open as entry.
    """

    def name(self) -> str:
        return "divergence_hunter"

    def check_entry(self, context: dict) -> Optional[dict]:
        """Evaluate RSI divergence entry at the current candle.

        Reads ``df`` from context (OHLCV DataFrame up to and including the
        current bar).  Returns a signal dict or None.
        """
        df: Optional[pd.DataFrame] = context.get("df")
        if df is None or len(df) < _RSI_PERIOD + _MIN_SWING_GAP + 4:
            return None

        market_type: str = context.get("market_type", "forex")
        symbol: str = context.get("symbol", "")

        # Slice recent bars for swing search (avoid looking too far back)
        lookback = min(_SWING_LOOKBACK, len(df))
        recent = df.iloc[-lookback:]

        highs = recent["high"].values.astype(float)
        lows = recent["low"].values.astype(float)
        closes = recent["close"].values.astype(float)

        rsi_arr = compute_rsi(closes, _RSI_PERIOD)
        atr_val = compute_atr(highs, lows, closes, _RSI_PERIOD)

        # ── Attempt LONG (bullish divergence) ────────────────────────────────

        long_signal = self._check_bullish_divergence(
            lows=lows,
            rsi_arr=rsi_arr,
            df_recent=recent,
            atr_val=atr_val,
            market_type=market_type,
            context=context,
        )
        if long_signal is not None:
            return long_signal

        # ── Attempt SHORT (bearish divergence) ───────────────────────────────

        short_signal = self._check_bearish_divergence(
            highs=highs,
            rsi_arr=rsi_arr,
            df_recent=recent,
            atr_val=atr_val,
            market_type=market_type,
            context=context,
        )
        return short_signal

    # ── Private helpers ───────────────────────────────────────────────────────

    def _check_bullish_divergence(
        self,
        lows: np.ndarray,
        rsi_arr: np.ndarray,
        df_recent: pd.DataFrame,
        atr_val: float,
        market_type: str,
        context: dict,
    ) -> Optional[dict]:
        """Return LONG signal dict if bullish divergence is present, else None."""
        swing_low_indices = find_swing_lows(lows, _MIN_SWING_GAP)

        # Need at least two confirmed swing lows; the last confirmed swing
        # must not be the very last bar (we need the "after" candle)
        if len(swing_low_indices) < 2:
            return None

        # The most recent confirmed swing must not be the last bar in the slice
        # so that the divergence is confirmed (i.e. the next bar has already opened)
        latest_idx = swing_low_indices[-1]
        if latest_idx >= len(lows) - 1:
            return None

        prev_idx = swing_low_indices[-2]

        price_lower_low = lows[latest_idx] < lows[prev_idx]

        rsi_at_latest = rsi_arr[latest_idx]
        rsi_at_prev = rsi_arr[prev_idx]

        if np.isnan(rsi_at_latest) or np.isnan(rsi_at_prev):
            return None

        rsi_higher_low = rsi_at_latest > rsi_at_prev

        if not (price_lower_low and rsi_higher_low):
            return None

        # D1 trend filter
        if not self._check_d1_trend(direction="LONG", context=context, df_recent=df_recent):
            return None

        # Volume filter (skip for forex)
        if not self._check_volume(
            df_recent=df_recent, direction="LONG", market_type=market_type
        ):
            return None

        # RSI must have turned up after the latest swing low
        # (current RSI > RSI at swing low)
        current_rsi = rsi_arr[-2]  # second-to-last: swing candle's RSI already used
        if np.isnan(current_rsi):
            return None
        if current_rsi <= rsi_at_latest:
            return None

        # Build signal
        swing_low_price = float(lows[latest_idx])
        sl_distance = swing_low_price - (swing_low_price - atr_val * float(_SL_ATR_BUFFER))
        # sl_distance = atr * 0.5 used as buffer only
        sl_price = Decimal(str(swing_low_price)) - _SL_ATR_BUFFER * Decimal(str(atr_val))
        entry_price = Decimal(str(df_recent["close"].iloc[-1]))

        distance = entry_price - sl_price
        if distance <= 0:
            return None

        tp1 = entry_price + _TP1_MULTIPLIER * distance
        tp2 = entry_price + _TP2_MULTIPLIER * distance

        return self._build_signal(
            direction="LONG",
            entry_price=entry_price,
            sl_price=sl_price,
            tp1=tp1,
            tp2=tp2,
            atr_val=atr_val,
            context=context,
        )

    def _check_bearish_divergence(
        self,
        highs: np.ndarray,
        rsi_arr: np.ndarray,
        df_recent: pd.DataFrame,
        atr_val: float,
        market_type: str,
        context: dict,
    ) -> Optional[dict]:
        """Return SHORT signal dict if bearish divergence is present, else None."""
        swing_high_indices = find_swing_highs(highs, _MIN_SWING_GAP)

        if len(swing_high_indices) < 2:
            return None

        latest_idx = swing_high_indices[-1]
        if latest_idx >= len(highs) - 1:
            return None

        prev_idx = swing_high_indices[-2]

        price_higher_high = highs[latest_idx] > highs[prev_idx]

        rsi_at_latest = rsi_arr[latest_idx]
        rsi_at_prev = rsi_arr[prev_idx]

        if np.isnan(rsi_at_latest) or np.isnan(rsi_at_prev):
            return None

        rsi_lower_high = rsi_at_latest < rsi_at_prev

        if not (price_higher_high and rsi_lower_high):
            return None

        # D1 trend filter
        if not self._check_d1_trend(direction="SHORT", context=context, df_recent=df_recent):
            return None

        # Volume filter
        if not self._check_volume(
            df_recent=df_recent, direction="SHORT", market_type=market_type
        ):
            return None

        # RSI must have turned down after the latest swing high
        current_rsi = rsi_arr[-2]
        if np.isnan(current_rsi):
            return None
        if current_rsi >= rsi_at_latest:
            return None

        swing_high_price = float(highs[latest_idx])
        sl_price = Decimal(str(swing_high_price)) + _SL_ATR_BUFFER * Decimal(str(atr_val))
        entry_price = Decimal(str(df_recent["close"].iloc[-1]))

        distance = sl_price - entry_price
        if distance <= 0:
            return None

        tp1 = entry_price - _TP1_MULTIPLIER * distance
        tp2 = entry_price - _TP2_MULTIPLIER * distance

        return self._build_signal(
            direction="SHORT",
            entry_price=entry_price,
            sl_price=sl_price,
            tp1=tp1,
            tp2=tp2,
            atr_val=atr_val,
            context=context,
        )

    def _check_d1_trend(
        self,
        direction: str,
        context: dict,
        df_recent: pd.DataFrame,
    ) -> bool:
        """Check D1 SMA(200) trend alignment.

        Args:
            direction: "LONG" or "SHORT".
            context: Strategy context dict.
            df_recent: Recent OHLCV slice from H4 data (used only if no d1_df).

        Returns:
            True if trend aligns or data unavailable (graceful degradation).
        """
        d1_df: Optional[pd.DataFrame] = context.get("d1_df")

        if d1_df is not None and len(d1_df) >= _D1_SMA_PERIOD:
            d1_close = float(d1_df["close"].iloc[-1])
            sma200 = float(d1_df["close"].iloc[-_D1_SMA_PERIOD:].mean())

            if direction == "LONG" and d1_close < sma200:
                logger.debug("[DivergenceHunter] LONG blocked: D1 close below SMA(200)")
                return False
            if direction == "SHORT" and d1_close > sma200:
                logger.debug("[DivergenceHunter] SHORT blocked: D1 close above SMA(200)")
                return False
            return True

        # Graceful degradation: no D1 data available
        logger.warning(
            "[DivergenceHunter] D1 data not available in context — skipping trend filter"
        )
        return True

    def _check_volume(
        self,
        df_recent: pd.DataFrame,
        direction: str,
        market_type: str,
    ) -> bool:
        """Check volume confirmation (stocks/crypto only).

        Args:
            df_recent: Recent OHLCV slice.
            direction: "LONG" or "SHORT" (unused, kept for symmetry).
            market_type: "forex" | "crypto" | "stocks".

        Returns:
            True if volume passes or filter is not applicable.
        """
        if market_type == "forex":
            return True

        if "volume" not in df_recent.columns:
            logger.warning("[DivergenceHunter] No volume column — skipping volume filter")
            return True

        vol_series = df_recent["volume"].values.astype(float)

        # Skip if all volume is zero (some instruments report no volume)
        if np.all(vol_series == 0):
            return True

        if len(vol_series) < _VOL_MA_PERIOD + 1:
            return True

        vol_ma = np.mean(vol_series[-_VOL_MA_PERIOD - 1 : -1])
        current_vol = vol_series[-1]

        if vol_ma == 0:
            return True

        if current_vol < _VOL_MA_MULTIPLIER * vol_ma:
            logger.debug(
                "[DivergenceHunter] Volume filter failed: %.0f < %.1f × %.0f",
                current_vol,
                _VOL_MA_MULTIPLIER,
                vol_ma,
            )
            return False

        return True

    def _build_signal(
        self,
        direction: str,
        entry_price: Decimal,
        sl_price: Decimal,
        tp1: Decimal,
        tp2: Decimal,
        atr_val: float,
        context: dict,
    ) -> dict:
        """Assemble and return a signal dict compatible with BacktestEngine."""
        regime: str = context.get("regime", "UNKNOWN")
        symbol: str = context.get("symbol", "")
        ta_indicators: dict = context.get("ta_indicators") or {}

        return {
            "direction": direction,
            "entry_price": entry_price,
            "composite_score": Decimal("15"),
            "regime": regime,
            "atr": Decimal(str(atr_val)) if atr_val else Decimal("0"),
            "position_pct": 2.0,
            "ta_indicators": ta_indicators,
            "support_levels": [sl_price] if direction == "LONG" else [],
            "resistance_levels": [sl_price] if direction == "SHORT" else [],
            # Strategy-specific metadata consumed by BacktestEngine extensions
            "sl_price": sl_price,
            "tp1_price": tp1,
            "tp2_price": tp2,
            "time_exit_candles": TIME_EXIT_CANDLES,
            "strategy": self.name(),
        }
