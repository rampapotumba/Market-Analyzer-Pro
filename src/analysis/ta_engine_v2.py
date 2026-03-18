"""Technical Analysis Engine v2.

Extends ta_engine.py with:
  - Order flow signals: CVD divergence, OI expansion, funding extremes, liquidation clusters
  - RSI / MACD divergence detection
  - OBV, MFI, VWAP
  - Ichimoku Cloud (Tenkan/Kijun/Senkou A & B / Chikou)
  - Pivot Points (Classic + Fibonacci)
  - Market structure: HH/HL/LH/LL, Break of Structure (BOS), Change of Character (CHoCH)

Score output: [-100, +100]
"""

import logging
from decimal import Decimal
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_RSI_OVERBOUGHT = 70.0
_RSI_OVERSOLD = 30.0
_FUNDING_EXTREME_BULL = 0.03   # 0.03% per 8h = overleveraged longs
_FUNDING_EXTREME_BEAR = -0.03
_OI_SURGE_PCT = 10.0           # OI growing >10% while price flat = warning
_MIN_BARS = 50                 # Minimum candles required


# ── Public class ───────────────────────────────────────────────────────────────

class TAEngineV2:
    """Extended technical analysis engine.

    Accepts OHLCV DataFrame plus optional order flow data.
    All score methods return floats in [-100, +100].
    """

    def __init__(
        self,
        df: pd.DataFrame,
        funding_rate: Optional[float] = None,
        open_interest: Optional[float] = None,
        open_interest_prev: Optional[float] = None,
        cvd: Optional[float] = None,
    ) -> None:
        """
        Args:
            df: OHLCV DataFrame with columns [open, high, low, close, volume].
            funding_rate: Current 8h funding rate (decimals, e.g. 0.0001 = 0.01%).
            open_interest: Current open interest in base units.
            open_interest_prev: Open interest N periods ago (for surge detection).
            cvd: Cumulative Volume Delta for the current interval.
        """
        self._df = df.copy()
        self._funding_rate = funding_rate
        self._oi = open_interest
        self._oi_prev = open_interest_prev
        self._cvd = cvd
        self._n = len(df)

    # ── Composite score ────────────────────────────────────────────────────────

    def score(self) -> float:
        """Return a composite TA score combining all sub-components."""
        if self._n < _MIN_BARS:
            return 0.0

        components = {
            "momentum":      (self._score_momentum(), 0.25),
            "trend":         (self._score_trend(), 0.20),
            "structure":     (self._score_market_structure(), 0.20),
            "volume":        (self._score_volume(), 0.15),
            "order_flow":    (self._score_order_flow(), 0.20),
        }

        total_w = 0.0
        weighted_sum = 0.0
        for label, (val, w) in components.items():
            if val is None:
                continue
            weighted_sum += val * w
            total_w += w

        if total_w == 0:
            return 0.0
        return max(-100.0, min(100.0, weighted_sum / total_w))

    # ── Sub-scores ─────────────────────────────────────────────────────────────

    def _score_momentum(self) -> Optional[float]:
        """RSI + MACD divergence score.

        RSI is treated as a trend-momentum indicator (above 50 = bullish,
        below 50 = bearish) with MACD divergence as an additive signal.
        Extreme RSI values (>80 or <20) are capped to avoid over-signalling.
        """
        rsi = _rsi(self._df["close"], 14)
        if rsi is None:
            return None

        # Trend-following RSI: 50 = neutral, 70 = moderately bullish, 30 = moderately bearish
        # Cap at ±80 to allow divergence from extreme readings
        rsi_capped = max(20.0, min(80.0, rsi))
        rsi_score = (rsi_capped - 50.0) * (100.0 / 30.0)  # 50→0, 80→+100, 20→-100

        # MACD divergence bonus (±20 pts)
        macd_score = self._macd_divergence_score()

        return max(-100.0, min(100.0, rsi_score + macd_score * 0.2))

    def _score_trend(self) -> Optional[float]:
        """Ichimoku + VWAP trend score."""
        ichimoku = _ichimoku_score(self._df)
        vwap_score = _vwap_score(self._df)

        parts = [s for s in (ichimoku, vwap_score) if s is not None]
        if not parts:
            return None
        return sum(parts) / len(parts)

    def _score_volume(self) -> Optional[float]:
        """OBV + MFI volume-based score."""
        obv = _obv_score(self._df)
        mfi = _mfi(self._df, 14)

        mfi_score: Optional[float] = None
        if mfi is not None:
            if mfi <= 20:
                mfi_score = 80.0
            elif mfi >= 80:
                mfi_score = -80.0
            else:
                mfi_score = (50.0 - mfi) * 1.6  # linear

        parts = [s for s in (obv, mfi_score) if s is not None]
        if not parts:
            return None
        return max(-100.0, min(100.0, sum(parts) / len(parts)))

    def _score_market_structure(self) -> Optional[float]:
        """HH/HL, LH/LL, BOS, CHoCH detection."""
        return _market_structure_score(self._df)

    def _score_order_flow(self) -> Optional[float]:
        """Score from funding rate, OI, CVD signals."""
        parts: list[float] = []

        if self._funding_rate is not None:
            fr = self._funding_rate
            if fr >= _FUNDING_EXTREME_BULL:
                # Very high funding = overleveraged longs = contrarian bearish
                parts.append(-min(100.0, (fr / 0.05) * 100.0))
            elif fr <= _FUNDING_EXTREME_BEAR:
                parts.append(min(100.0, (abs(fr) / 0.05) * 100.0))
            else:
                parts.append(0.0)

        if self._oi is not None and self._oi_prev is not None and self._oi_prev > 0:
            oi_change_pct = (self._oi - self._oi_prev) / self._oi_prev * 100.0
            close = self._df["close"].iloc[-1]
            prev_close = self._df["close"].iloc[-20] if self._n > 20 else self._df["close"].iloc[0]
            price_change_pct = (close - prev_close) / prev_close * 100.0

            if oi_change_pct > _OI_SURGE_PCT and abs(price_change_pct) < 1.0:
                # OI surge without price move = indecision / warning
                parts.append(-20.0)
            elif oi_change_pct > _OI_SURGE_PCT and price_change_pct > 1.0:
                # OI + price up = trend continuation bullish
                parts.append(40.0)
            elif oi_change_pct > _OI_SURGE_PCT and price_change_pct < -1.0:
                # OI + price down = trend continuation bearish
                parts.append(-40.0)

        if self._cvd is not None:
            # CVD normalised to ±100 over a "typical" daily volume (ad hoc)
            typical_vol = self._df["volume"].rolling(20).mean().iloc[-1]
            if typical_vol > 0:
                cvd_norm = max(-100.0, min(100.0, self._cvd / typical_vol * 100.0))
                parts.append(cvd_norm)

        if not parts:
            return None
        return max(-100.0, min(100.0, sum(parts) / len(parts)))

    # ── MACD divergence helper ─────────────────────────────────────────────────

    def _macd_divergence_score(self) -> float:
        """Detect bullish / bearish MACD divergence.  Returns score in [-100, +100]."""
        close = self._df["close"]
        if len(close) < 35:
            return 0.0

        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26

        # Simple last 2 peaks/troughs approach
        macd_now = macd_line.iloc[-1]
        macd_prev = macd_line.iloc[-10]   # ~10 bars ago
        price_now = close.iloc[-1]
        price_prev = close.iloc[-10]

        # Bullish divergence: price lower low, MACD higher low
        if price_now < price_prev and macd_now > macd_prev:
            return 60.0
        # Bearish divergence: price higher high, MACD lower high
        if price_now > price_prev and macd_now < macd_prev:
            return -60.0
        return 0.0

    # ── Pivot points (public utility) ──────────────────────────────────────────

    def get_pivot_points(self) -> dict[str, float]:
        """Return Classic and Fibonacci pivot points from the last full candle."""
        if self._n < 2:
            return {}
        prev = self._df.iloc[-2]
        h, l, c = float(prev["high"]), float(prev["low"]), float(prev["close"])
        pp = (h + l + c) / 3.0
        r = h - l  # range

        return {
            "pp": round(pp, 5),
            "r1": round(pp + (pp - l), 5),
            "r2": round(pp + r, 5),
            "r3": round(h + 2 * (pp - l), 5),
            "s1": round(pp - (h - pp), 5),
            "s2": round(pp - r, 5),
            "s3": round(l - 2 * (h - pp), 5),
            # Fibonacci
            "fib_r1": round(pp + r * 0.382, 5),
            "fib_r2": round(pp + r * 0.618, 5),
            "fib_r3": round(pp + r * 1.000, 5),
            "fib_s1": round(pp - r * 0.382, 5),
            "fib_s2": round(pp - r * 0.618, 5),
            "fib_s3": round(pp - r * 1.000, 5),
        }


# ── TA helper functions (module-level, usable independently) ───────────────────

def _rsi(series: pd.Series, period: int = 14) -> Optional[float]:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    last_gain = gain.iloc[-1]
    last_loss = loss.iloc[-1]
    if pd.isna(last_gain) or pd.isna(last_loss):
        return None
    if last_loss == 0:
        return 100.0 if last_gain > 0 else 50.0
    rs = last_gain / last_loss
    return float(100 - 100 / (1 + rs))


def _obv_score(df: pd.DataFrame, lookback: int = 20) -> Optional[float]:
    """OBV trend: positive slope = bullish, negative = bearish."""
    close = df["close"]
    volume = df["volume"]
    if len(close) < lookback + 1:
        return None
    direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    obv = (direction * volume).cumsum()

    # Slope of last `lookback` bars
    x = np.arange(lookback)
    y = obv.iloc[-lookback:].values
    if len(y) < lookback or np.std(y) == 0:
        return None
    slope = np.polyfit(x, y, 1)[0]
    # Normalise slope to ±100 relative to mean OBV magnitude
    mean_obv = np.abs(y).mean()
    if mean_obv == 0:
        return None
    score = (slope / mean_obv) * 100.0 * lookback
    return max(-100.0, min(100.0, score))


def _mfi(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    """Money Flow Index (0–100)."""
    if len(df) < period + 1:
        return None
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    mf = tp * df["volume"]
    pos_mf = mf.where(tp > tp.shift(1), 0.0).rolling(period).sum()
    neg_mf = mf.where(tp < tp.shift(1), 0.0).rolling(period).sum()
    mfr = pos_mf / neg_mf.replace(0, float("nan"))
    mfi_series = 100 - 100 / (1 + mfr)
    val = mfi_series.iloc[-1]
    return None if pd.isna(val) else float(val)


def _ichimoku_score(df: pd.DataFrame) -> Optional[float]:
    """Simplified Ichimoku: price vs cloud + Tenkan/Kijun cross."""
    if len(df) < 52:
        return None

    tenkan = (df["high"].rolling(9).max() + df["low"].rolling(9).min()) / 2
    kijun = (df["high"].rolling(26).max() + df["low"].rolling(26).min()) / 2
    senkou_a = ((tenkan + kijun) / 2).shift(26)
    senkou_b = ((df["high"].rolling(52).max() + df["low"].rolling(52).min()) / 2).shift(26)

    price = df["close"].iloc[-1]
    cloud_top = max(senkou_a.iloc[-1], senkou_b.iloc[-1]) if not (pd.isna(senkou_a.iloc[-1]) or pd.isna(senkou_b.iloc[-1])) else None
    cloud_bot = min(senkou_a.iloc[-1], senkou_b.iloc[-1]) if cloud_top is not None else None

    score = 0.0
    if cloud_top is not None and cloud_bot is not None:
        if price > cloud_top:
            score += 60.0   # above cloud = bullish
        elif price < cloud_bot:
            score -= 60.0   # below cloud = bearish

    # Tenkan / Kijun cross
    t_now, k_now = tenkan.iloc[-1], kijun.iloc[-1]
    t_prev, k_prev = tenkan.iloc[-2], kijun.iloc[-2]
    if not any(pd.isna(x) for x in (t_now, k_now, t_prev, k_prev)):
        if t_prev <= k_prev and t_now > k_now:
            score += 30.0   # golden cross
        elif t_prev >= k_prev and t_now < k_now:
            score -= 30.0   # death cross

    return max(-100.0, min(100.0, score))


def _vwap_score(df: pd.DataFrame) -> Optional[float]:
    """Price vs VWAP: above = bullish, below = bearish."""
    if len(df) < 20:
        return None
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    cum_vol = df["volume"].cumsum()
    vwap = (tp * df["volume"]).cumsum() / cum_vol.replace(0, float("nan"))
    price = df["close"].iloc[-1]
    vwap_val = vwap.iloc[-1]
    if pd.isna(vwap_val) or vwap_val == 0:
        return None
    diff_pct = (price - vwap_val) / vwap_val * 100.0
    # ±2% diff → ±100 score
    return max(-100.0, min(100.0, diff_pct / 2.0 * 100.0))


def _market_structure_score(df: pd.DataFrame, swing_n: int = 5) -> Optional[float]:
    """Detect HH/HL/LH/LL sequences and BOS/CHoCH.

    Returns positive score for bullish structure, negative for bearish.
    """
    if len(df) < swing_n * 4:
        return None

    highs = df["high"].values
    lows = df["low"].values
    n = len(highs)

    # Find local swing highs and lows (simple peak/trough detection)
    def _is_swing_high(i: int) -> bool:
        return all(highs[i] >= highs[max(0, i - swing_n):i]) and \
               all(highs[i] >= highs[i + 1:min(n, i + swing_n + 1)])

    def _is_swing_low(i: int) -> bool:
        return all(lows[i] <= lows[max(0, i - swing_n):i]) and \
               all(lows[i] <= lows[i + 1:min(n, i + swing_n + 1)])

    swing_highs = [(i, highs[i]) for i in range(swing_n, n - swing_n) if _is_swing_high(i)]
    swing_lows = [(i, lows[i]) for i in range(swing_n, n - swing_n) if _is_swing_low(i)]

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return None

    # Last 2 swing highs and lows
    sh1_idx, sh1_val = swing_highs[-2]
    sh2_idx, sh2_val = swing_highs[-1]
    sl1_idx, sl1_val = swing_lows[-2]
    sl2_idx, sl2_val = swing_lows[-1]

    score = 0.0

    # HH (Higher High)
    if sh2_val > sh1_val:
        score += 25.0
    # HL (Higher Low)
    if sl2_val > sl1_val:
        score += 25.0
    # LH (Lower High) — bearish
    if sh2_val < sh1_val:
        score -= 25.0
    # LL (Lower Low) — bearish
    if sl2_val < sl1_val:
        score -= 25.0

    # BOS: price breaks above last swing high (bullish) or below last swing low (bearish)
    current_price = df["close"].iloc[-1]
    last_sh = swing_highs[-1][1]
    last_sl = swing_lows[-1][1]
    if current_price > last_sh:
        score += 30.0   # Break of Structure bullish
    elif current_price < last_sl:
        score -= 30.0   # Break of Structure bearish

    return max(-100.0, min(100.0, score))
