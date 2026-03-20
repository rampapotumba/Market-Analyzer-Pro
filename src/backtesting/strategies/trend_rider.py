"""TrendRiderStrategy — D1 trend-following strategy (TASK-V7-21).

Entry logic:
  LONG:  ADX(14) > 25, Close > SMA200, Close > SMA50,
         MACD histogram > 0 AND increasing, price within 1×ATR of SMA50
  SHORT: mirror conditions

Exit parameters (returned for BacktestEngine to apply):
  SL:       2.0 × ATR from entry
  TP:       3.0 × ATR from entry  (R:R ≈ 1.5:1)
  Trailing: move SL to breakeven at 1.5 × ATR profit
  Time:     15 D1 candles without profit → time_exit

Regime filter: STRONG_TREND_BULL (LONG) or STRONG_TREND_BEAR (SHORT)
Target instruments: EURUSD=X, AUDUSD=X, USDCAD=X, GC=F, BTC/USDT
"""

import logging
from decimal import Decimal
from typing import Optional

import numpy as np
import pandas as pd

from src.backtesting.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

ADX_THRESHOLD = 25.0
SMA_FAST_PERIOD = 50
SMA_SLOW_PERIOD = 200
ADX_PERIOD = 14
ATR_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

SL_ATR_MULTIPLIER = Decimal("2.0")
TP_ATR_MULTIPLIER = Decimal("3.0")
BREAKEVEN_ATR_MULTIPLIER = Decimal("1.5")
TIME_EXIT_CANDLES = 15

PULLBACK_ATR_MULTIPLIER = 1.0  # price must be within 1×ATR of SMA50

TARGET_INSTRUMENTS = frozenset(["EURUSD=X", "AUDUSD=X", "USDCAD=X", "GC=F", "BTC/USDT"])

ALLOWED_REGIMES_LONG = frozenset(["STRONG_TREND_BULL"])
ALLOWED_REGIMES_SHORT = frozenset(["STRONG_TREND_BEAR"])
ALLOWED_REGIMES = ALLOWED_REGIMES_LONG | ALLOWED_REGIMES_SHORT

# Minimum bars required to compute all indicators reliably
_MIN_BARS = SMA_SLOW_PERIOD + MACD_SLOW + MACD_SIGNAL + 5


# ── Indicator helpers ──────────────────────────────────────────────────────────


def _sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def _compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder ATR using True Range."""
    high = df["High"]
    low = df["Low"]
    prev_close = df["Close"].shift(1)

    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    # Wilder smoothing: EWM with alpha = 1/period
    atr = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return atr


def _compute_adx(df: pd.DataFrame, period: int) -> pd.Series:
    """ADX via Wilder smoothing of DM+/DM-."""
    high = df["High"]
    low = df["Low"]
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    up_move = high - prev_high
    down_move = prev_low - low

    dm_plus = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    dm_minus = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    atr = _compute_atr(df, period)

    smoothed_dm_plus = dm_plus.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    smoothed_dm_minus = dm_minus.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    di_plus = 100.0 * smoothed_dm_plus / atr.replace(0, np.nan)
    di_minus = 100.0 * smoothed_dm_minus / atr.replace(0, np.nan)

    dx = (100.0 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)).fillna(0)
    adx = dx.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return adx


def _compute_macd(
    series: pd.Series,
    fast: int = MACD_FAST,
    slow: int = MACD_SLOW,
    signal: int = MACD_SIGNAL,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return (macd_line, signal_line, histogram)."""
    ema_fast = _ema(series, fast)
    ema_slow = _ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


# ── Strategy ──────────────────────────────────────────────────────────────────


class TrendRiderStrategy(BaseStrategy):
    """D1 trend-following strategy: enter on pullbacks in confirmed trend direction.

    All indicators are computed internally from `context["df"]`.
    The strategy is self-contained and does not depend on pre-computed
    ta_indicators from BacktestEngine.
    """

    def name(self) -> str:
        return "trend_rider"

    def check_entry(self, context: dict) -> Optional[dict]:
        """Evaluate LONG/SHORT entry at the current candle.

        Returns an entry dict compatible with BacktestEngine or None.
        """
        symbol: str = context.get("symbol", "")
        regime: str = context.get("regime", "")
        df: Optional[pd.DataFrame] = context.get("df")

        # ── Regime guard ──────────────────────────────────────────────────────
        if regime not in ALLOWED_REGIMES:
            return None

        # ── DataFrame guard ───────────────────────────────────────────────────
        if df is None or len(df) < _MIN_BARS:
            logger.debug(
                "[TrendRider] %s: not enough bars (%d < %d)",
                symbol,
                0 if df is None else len(df),
                _MIN_BARS,
            )
            return None

        # ── Compute indicators at the current (last) bar ───────────────────────
        close = df["Close"]

        sma50_series = _sma(close, SMA_FAST_PERIOD)
        sma200_series = _sma(close, SMA_SLOW_PERIOD)
        atr_series = _compute_atr(df, ATR_PERIOD)
        adx_series = _compute_adx(df, ADX_PERIOD)
        _, _, hist_series = _compute_macd(close)

        # Last valid values
        sma50 = sma50_series.iloc[-1]
        sma200 = sma200_series.iloc[-1]
        atr = atr_series.iloc[-1]
        adx = adx_series.iloc[-1]
        hist_cur = hist_series.iloc[-1]
        hist_prev = hist_series.iloc[-2] if len(hist_series) >= 2 else hist_cur

        price = close.iloc[-1]

        # Guard against NaN indicators
        if any(
            np.isnan(v)
            for v in [sma50, sma200, atr, adx, hist_cur, hist_prev, price]
        ):
            logger.debug("[TrendRider] %s: NaN in indicators — skipping", symbol)
            return None

        if atr <= 0:
            logger.debug("[TrendRider] %s: ATR=0 — skipping", symbol)
            return None

        # ── ADX filter ────────────────────────────────────────────────────────
        if adx <= ADX_THRESHOLD:
            return None

        # ── Direction selection ───────────────────────────────────────────────
        if regime in ALLOWED_REGIMES_LONG:
            direction = "LONG"
        else:
            direction = "SHORT"

        # ── Trend alignment (SMA) ─────────────────────────────────────────────
        if direction == "LONG":
            if not (price > sma200 and price > sma50):
                return None
        else:
            if not (price < sma200 and price < sma50):
                return None

        # ── MACD histogram: > 0 AND increasing ────────────────────────────────
        if direction == "LONG":
            if not (hist_cur > 0 and hist_cur > hist_prev):
                return None
        else:
            if not (hist_cur < 0 and hist_cur < hist_prev):
                return None

        # ── Pullback proximity to SMA50 ───────────────────────────────────────
        distance = abs(price - sma50)
        if distance > PULLBACK_ATR_MULTIPLIER * atr:
            return None

        # ── Compute SL / TP ───────────────────────────────────────────────────
        atr_dec = Decimal(str(round(atr, 10)))
        entry_price = Decimal(str(round(price, 10)))

        if direction == "LONG":
            sl_price = entry_price - SL_ATR_MULTIPLIER * atr_dec
            tp_price = entry_price + TP_ATR_MULTIPLIER * atr_dec
        else:
            sl_price = entry_price + SL_ATR_MULTIPLIER * atr_dec
            tp_price = entry_price - TP_ATR_MULTIPLIER * atr_dec

        # ── Build signal dict ─────────────────────────────────────────────────
        ta_indicators = {
            "adx": float(adx),
            "sma50": float(sma50),
            "sma200": float(sma200),
            "macd_hist": float(hist_cur),
            "macd_hist_prev": float(hist_prev),
            "atr": float(atr),
        }

        return {
            "direction": direction,
            "entry_price": entry_price,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "composite_score": Decimal("20"),
            "regime": regime,
            "atr": atr_dec,
            "position_pct": 2.0,
            "ta_indicators": ta_indicators,
            "support_levels": [],
            "resistance_levels": [],
            # Extra meta for engine: breakeven and time-exit parameters
            "breakeven_atr_multiplier": float(BREAKEVEN_ATR_MULTIPLIER),
            "time_exit_candles": TIME_EXIT_CANDLES,
        }
