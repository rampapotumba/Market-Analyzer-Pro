"""Technical Analysis Engine using TA-Lib."""

import logging
from decimal import Decimal
from typing import Any, Optional

import numpy as np
import pandas as pd

try:
    import talib
    TALIB_AVAILABLE = True
except ImportError:
    TALIB_AVAILABLE = False
    logging.warning("TA-Lib not available, using fallback calculations")

logger = logging.getLogger(__name__)

# TA indicator weights (must sum to 1.0)
TA_WEIGHTS = {
    "macd": 0.20,
    "rsi": 0.15,
    "bollinger": 0.12,
    "ma_cross": 0.18,
    "adx": 0.10,
    "stochastic": 0.08,
    "volume": 0.07,
    "support_resistance": 0.05,
    "candle_patterns": 0.05,
}


def _safe_last(series) -> Optional[float]:
    """Safely get last non-NaN value from a series."""
    if series is None:
        return None
    arr = np.asarray(series, dtype=float)
    valid = arr[~np.isnan(arr)]
    if len(valid) == 0:
        return None
    return float(valid[-1])


class TAEngine:
    """Technical Analysis Engine. Accepts an OHLCV DataFrame."""

    def __init__(self, df: pd.DataFrame) -> None:
        """
        Args:
            df: DataFrame with columns [Open/open, High/high, Low/low, Close/close, Volume/volume]
                indexed by datetime.
        """
        self.df = df.copy()
        self._normalize_columns()
        self._indicators: Optional[dict[str, Any]] = None
        self._signals: Optional[dict[str, Any]] = None

    def _normalize_columns(self) -> None:
        """Normalize column names to lowercase."""
        self.df.columns = [c.lower() for c in self.df.columns]
        # Ensure required columns exist
        for col in ("open", "high", "low", "close"):
            if col not in self.df.columns:
                raise ValueError(f"DataFrame missing column: {col}")
        if "volume" not in self.df.columns:
            self.df["volume"] = 0.0

        # Convert to float64 for talib
        for col in ("open", "high", "low", "close", "volume"):
            self.df[col] = self.df[col].astype(float)

    @property
    def _close(self) -> np.ndarray:
        return self.df["close"].values

    @property
    def _high(self) -> np.ndarray:
        return self.df["high"].values

    @property
    def _low(self) -> np.ndarray:
        return self.df["low"].values

    @property
    def _open(self) -> np.ndarray:
        return self.df["open"].values

    @property
    def _volume(self) -> np.ndarray:
        return self.df["volume"].values

    def _calc_rsi(self, period: int = 14) -> np.ndarray:
        if TALIB_AVAILABLE:
            return talib.RSI(self._close, timeperiod=period)
        # Fallback: manual RSI
        close = pd.Series(self._close)
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi.values

    def _calc_macd(self):
        if TALIB_AVAILABLE:
            macd, signal, hist = talib.MACD(self._close, 12, 26, 9)
            return macd, signal, hist
        # Fallback
        close = pd.Series(self._close)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        hist = macd - signal
        return macd.values, signal.values, hist.values

    def _calc_bb(self, period: int = 20, std: float = 2.0):
        if TALIB_AVAILABLE:
            upper, middle, lower = talib.BBANDS(self._close, period, std, std)
            return upper, middle, lower
        # Fallback
        close = pd.Series(self._close)
        middle = close.rolling(period).mean()
        dev = close.rolling(period).std()
        upper = middle + std * dev
        lower = middle - std * dev
        return upper.values, middle.values, lower.values

    def _calc_sma(self, period: int) -> np.ndarray:
        if TALIB_AVAILABLE:
            return talib.SMA(self._close, timeperiod=period)
        return pd.Series(self._close).rolling(period).mean().values

    def _calc_ema(self, period: int) -> np.ndarray:
        if TALIB_AVAILABLE:
            return talib.EMA(self._close, timeperiod=period)
        return pd.Series(self._close).ewm(span=period, adjust=False).mean().values

    def _calc_adx(self, period: int = 14):
        if TALIB_AVAILABLE:
            adx = talib.ADX(self._high, self._low, self._close, timeperiod=period)
            plus_di = talib.PLUS_DI(self._high, self._low, self._close, timeperiod=period)
            minus_di = talib.MINUS_DI(self._high, self._low, self._close, timeperiod=period)
            return adx, plus_di, minus_di
        # Fallback: simplified
        close = pd.Series(self._close)
        adx = pd.Series(np.full(len(close), 25.0))  # neutral
        return adx.values, adx.values, adx.values

    def _calc_stochastic(self, k_period: int = 14, d_period: int = 3):
        if TALIB_AVAILABLE:
            slowk, slowd = talib.STOCH(
                self._high, self._low, self._close,
                fastk_period=k_period, slowk_period=d_period, slowk_matype=0,
                slowd_period=d_period, slowd_matype=0
            )
            return slowk, slowd
        # Fallback
        high = pd.Series(self._high)
        low = pd.Series(self._low)
        close = pd.Series(self._close)
        lowest_low = low.rolling(k_period).min()
        highest_high = high.rolling(k_period).max()
        k = 100 * (close - lowest_low) / (highest_high - lowest_low + 1e-10)
        d = k.rolling(d_period).mean()
        return k.values, d.values

    def _calc_atr(self, period: int = 14) -> np.ndarray:
        if TALIB_AVAILABLE:
            return talib.ATR(self._high, self._low, self._close, timeperiod=period)
        # Fallback
        high = pd.Series(self._high)
        low = pd.Series(self._low)
        close = pd.Series(self._close)
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean().values

    def _detect_candle_patterns(self) -> int:
        """Detect basic candle patterns. Returns signal: -1, 0, or 1."""
        if len(self.df) < 3:
            return 0

        if not TALIB_AVAILABLE:
            return 0

        patterns = {
            "hammer": talib.CDLHAMMER(self._open, self._high, self._low, self._close),
            "engulfing": talib.CDLENGULFING(self._open, self._high, self._low, self._close),
            "doji": talib.CDLDOJI(self._open, self._high, self._low, self._close),
            "morning_star": talib.CDLMORNINGSTAR(self._open, self._high, self._low, self._close),
            "evening_star": talib.CDLEVENINGSTAR(self._open, self._high, self._low, self._close),
            "shooting_star": talib.CDLSHOOTINGSTAR(self._open, self._high, self._low, self._close),
        }

        last_signals = []
        for name, arr in patterns.items():
            val = _safe_last(arr)
            if val and val != 0:
                last_signals.append(1 if val > 0 else -1)

        if not last_signals:
            return 0
        avg = sum(last_signals) / len(last_signals)
        if avg > 0.3:
            return 1
        if avg < -0.3:
            return -1
        return 0

    def _find_support_resistance(self) -> dict[str, float]:
        """Find approximate support and resistance using recent highs/lows."""
        if len(self.df) < 20:
            return {"support": 0.0, "resistance": 0.0}

        recent = self.df.tail(50)
        support = float(recent["low"].min())
        resistance = float(recent["high"].max())
        return {"support": support, "resistance": resistance}

    def calculate_all_indicators(self) -> dict[str, Any]:
        """Calculate all technical indicators. Returns dict with values."""
        if self._indicators is not None:
            return self._indicators

        indicators: dict[str, Any] = {}

        # RSI
        rsi = self._calc_rsi(14)
        indicators["rsi"] = _safe_last(rsi)

        # MACD
        macd, macd_signal, macd_hist = self._calc_macd()
        indicators["macd"] = _safe_last(macd)
        indicators["macd_signal"] = _safe_last(macd_signal)
        indicators["macd_hist"] = _safe_last(macd_hist)

        # Bollinger Bands
        bb_upper, bb_middle, bb_lower = self._calc_bb(20, 2.0)
        indicators["bb_upper"] = _safe_last(bb_upper)
        indicators["bb_middle"] = _safe_last(bb_middle)
        indicators["bb_lower"] = _safe_last(bb_lower)
        indicators["bb_width"] = (
            (indicators["bb_upper"] - indicators["bb_lower"]) / indicators["bb_middle"]
            if indicators["bb_middle"] else None
        )

        # Moving Averages
        indicators["sma20"] = _safe_last(self._calc_sma(20))
        indicators["sma50"] = _safe_last(self._calc_sma(50))
        indicators["sma200"] = _safe_last(self._calc_sma(200))
        indicators["ema12"] = _safe_last(self._calc_ema(12))
        indicators["ema26"] = _safe_last(self._calc_ema(26))

        # ADX
        adx, plus_di, minus_di = self._calc_adx(14)
        indicators["adx"] = _safe_last(adx)
        indicators["plus_di"] = _safe_last(plus_di)
        indicators["minus_di"] = _safe_last(minus_di)

        # Stochastic
        stoch_k, stoch_d = self._calc_stochastic(14, 3)
        indicators["stoch_k"] = _safe_last(stoch_k)
        indicators["stoch_d"] = _safe_last(stoch_d)

        # ATR
        atr = self._calc_atr(14)
        indicators["atr"] = _safe_last(atr)
        indicators["atr14"] = indicators["atr"]

        # Current price
        indicators["current_price"] = float(self.df["close"].iloc[-1])
        indicators["current_volume"] = float(self.df["volume"].iloc[-1])
        indicators["avg_volume_20"] = float(self.df["volume"].tail(20).mean())

        # Support/Resistance
        sr = self._find_support_resistance()
        indicators["support"] = sr["support"]
        indicators["resistance"] = sr["resistance"]

        # Candle patterns
        indicators["candle_pattern"] = self._detect_candle_patterns()

        # PDH/PDL
        pdh_pdl = self.calculate_pdh_pdl()
        indicators.update(pdh_pdl)

        # Session levels
        session = self.calculate_session_levels()
        indicators.update(session)

        # Fibonacci
        fib = self.calculate_fibonacci()
        indicators.update(fib)

        # Volume Profile
        vp = self.calculate_volume_profile()
        indicators.update(vp)

        # Order Blocks (store count and last OB levels)
        obs = self.detect_order_blocks()
        bull_obs = [o for o in obs if o["type"] == "bullish"]
        bear_obs = [o for o in obs if o["type"] == "bearish"]
        indicators["bull_ob_high"] = bull_obs[-1]["high"] if bull_obs else None
        indicators["bull_ob_low"] = bull_obs[-1]["low"] if bull_obs else None
        indicators["bear_ob_high"] = bear_obs[-1]["high"] if bear_obs else None
        indicators["bear_ob_low"] = bear_obs[-1]["low"] if bear_obs else None

        # FVGs
        fvgs = self.detect_fair_value_gaps()
        bull_fvgs = [f for f in fvgs if f["type"] == "bullish"]
        bear_fvgs = [f for f in fvgs if f["type"] == "bearish"]
        indicators["bull_fvg_top"] = bull_fvgs[-1]["top"] if bull_fvgs else None
        indicators["bull_fvg_bottom"] = bull_fvgs[-1]["bottom"] if bull_fvgs else None
        indicators["bear_fvg_top"] = bear_fvgs[-1]["top"] if bear_fvgs else None
        indicators["bear_fvg_bottom"] = bear_fvgs[-1]["bottom"] if bear_fvgs else None

        self._indicators = indicators
        return indicators

    def generate_ta_signals(self) -> dict[str, Any]:
        """
        Generate signals for each indicator.
        Returns dict with signal (-1=sell, 0=neutral, +1=buy) and strength [0,1].
        """
        if self._signals is not None:
            return self._signals

        ind = self.calculate_all_indicators()
        signals: dict[str, Any] = {}

        # RSI signal
        rsi = ind.get("rsi")
        if rsi is not None:
            if rsi < 30:
                signals["rsi"] = {"signal": 1, "strength": (30 - rsi) / 30}
            elif rsi > 70:
                signals["rsi"] = {"signal": -1, "strength": (rsi - 70) / 30}
            else:
                signals["rsi"] = {"signal": 0, "strength": abs(rsi - 50) / 50}
        else:
            signals["rsi"] = {"signal": 0, "strength": 0.0}

        # MACD signal
        macd_val = ind.get("macd")
        macd_sig = ind.get("macd_signal")
        macd_hist = ind.get("macd_hist")
        if macd_val is not None and macd_sig is not None and macd_hist is not None:
            if macd_val > macd_sig and macd_hist > 0:
                signals["macd"] = {"signal": 1, "strength": min(abs(macd_hist) / max(abs(macd_val), 1e-10), 1.0)}
            elif macd_val < macd_sig and macd_hist < 0:
                signals["macd"] = {"signal": -1, "strength": min(abs(macd_hist) / max(abs(macd_val), 1e-10), 1.0)}
            else:
                signals["macd"] = {"signal": 0, "strength": 0.0}
        else:
            signals["macd"] = {"signal": 0, "strength": 0.0}

        # Bollinger Bands signal
        close = ind["current_price"]
        bb_upper = ind.get("bb_upper")
        bb_lower = ind.get("bb_lower")
        bb_middle = ind.get("bb_middle")
        if bb_upper and bb_lower and bb_middle:
            bb_range = bb_upper - bb_lower
            if bb_range > 0:
                position = (close - bb_lower) / bb_range  # 0=at lower, 1=at upper
                if close < bb_lower:
                    signals["bollinger"] = {"signal": 1, "strength": min((bb_lower - close) / bb_range, 1.0)}
                elif close > bb_upper:
                    signals["bollinger"] = {"signal": -1, "strength": min((close - bb_upper) / bb_range, 1.0)}
                else:
                    # Closer to lower = bullish, closer to upper = bearish
                    if position < 0.3:
                        signals["bollinger"] = {"signal": 1, "strength": (0.3 - position) / 0.3}
                    elif position > 0.7:
                        signals["bollinger"] = {"signal": -1, "strength": (position - 0.7) / 0.3}
                    else:
                        signals["bollinger"] = {"signal": 0, "strength": 0.0}
            else:
                signals["bollinger"] = {"signal": 0, "strength": 0.0}
        else:
            signals["bollinger"] = {"signal": 0, "strength": 0.0}

        # MA Crossover signal
        sma20 = ind.get("sma20")
        sma50 = ind.get("sma50")
        sma200 = ind.get("sma200")
        ema12 = ind.get("ema12")
        ema26 = ind.get("ema26")
        ma_score = 0
        ma_count = 0
        if sma20 is not None and sma50 is not None:
            if close > sma20 > sma50:
                ma_score += 1
            elif close < sma20 < sma50:
                ma_score -= 1
            ma_count += 1
        if sma200 is not None:
            if close > sma200:
                ma_score += 0.5
            else:
                ma_score -= 0.5
            ma_count += 0.5
        if ema12 is not None and ema26 is not None:
            if ema12 > ema26:
                ma_score += 1
            else:
                ma_score -= 1
            ma_count += 1
        if ma_count > 0:
            norm = ma_score / ma_count
            signals["ma_cross"] = {
                "signal": 1 if norm > 0.3 else (-1 if norm < -0.3 else 0),
                "strength": min(abs(norm), 1.0),
            }
        else:
            signals["ma_cross"] = {"signal": 0, "strength": 0.0}

        # ADX signal (trend strength, not direction)
        adx = ind.get("adx")
        plus_di = ind.get("plus_di")
        minus_di = ind.get("minus_di")
        if adx is not None and plus_di is not None and minus_di is not None:
            if adx > 25:  # Strong trend
                if plus_di > minus_di:
                    signals["adx"] = {"signal": 1, "strength": min(adx / 100, 1.0)}
                else:
                    signals["adx"] = {"signal": -1, "strength": min(adx / 100, 1.0)}
            else:
                signals["adx"] = {"signal": 0, "strength": adx / 100}
        else:
            signals["adx"] = {"signal": 0, "strength": 0.0}

        # Stochastic signal
        stoch_k = ind.get("stoch_k")
        stoch_d = ind.get("stoch_d")
        if stoch_k is not None and stoch_d is not None:
            if stoch_k < 20 and stoch_k > stoch_d:
                signals["stochastic"] = {"signal": 1, "strength": (20 - stoch_k) / 20}
            elif stoch_k > 80 and stoch_k < stoch_d:
                signals["stochastic"] = {"signal": -1, "strength": (stoch_k - 80) / 20}
            else:
                signals["stochastic"] = {"signal": 0, "strength": 0.0}
        else:
            signals["stochastic"] = {"signal": 0, "strength": 0.0}

        # Volume signal
        avg_vol = ind.get("avg_volume_20", 0)
        curr_vol = ind.get("current_volume", 0)
        if avg_vol > 0:
            vol_ratio = curr_vol / avg_vol
            if vol_ratio > 1.5:
                # High volume: confirms current direction
                macd_dir = signals.get("macd", {}).get("signal", 0)
                signals["volume"] = {"signal": macd_dir, "strength": min((vol_ratio - 1) / 2, 1.0)}
            else:
                signals["volume"] = {"signal": 0, "strength": vol_ratio / 1.5}
        else:
            signals["volume"] = {"signal": 0, "strength": 0.0}

        # Support/Resistance signal
        support = ind.get("support", 0)
        resistance = ind.get("resistance", 0)
        if resistance > support > 0:
            sr_range = resistance - support
            position_in_range = (close - support) / sr_range
            if position_in_range < 0.15:
                signals["support_resistance"] = {"signal": 1, "strength": (0.15 - position_in_range) / 0.15}
            elif position_in_range > 0.85:
                signals["support_resistance"] = {"signal": -1, "strength": (position_in_range - 0.85) / 0.15}
            else:
                signals["support_resistance"] = {"signal": 0, "strength": 0.0}
        else:
            signals["support_resistance"] = {"signal": 0, "strength": 0.0}

        # Candle patterns signal
        candle_sig = ind.get("candle_pattern", 0)
        signals["candle_patterns"] = {"signal": candle_sig, "strength": abs(candle_sig) * 0.7}

        self._signals = signals
        return signals

    def calculate_ta_score(self) -> float:
        """
        Calculate weighted TA score.
        Returns float in [-100, +100].
        """
        signals = self.generate_ta_signals()
        total_score = 0.0

        for indicator, weight in TA_WEIGHTS.items():
            sig = signals.get(indicator, {"signal": 0, "strength": 0.0})
            direction = sig.get("signal", 0)
            strength = sig.get("strength", 0.0)
            contribution = direction * strength * weight * 100
            total_score += contribution

        return max(-100.0, min(100.0, total_score))

    def get_atr(self, period: int = 14) -> Optional[Decimal]:
        """Get the current ATR value as Decimal."""
        indicators = self.calculate_all_indicators()
        atr = indicators.get("atr")
        if atr is None:
            return None
        return Decimal(str(round(atr, 8)))

    # ── Smart Money Concepts ──────────────────────────────────────────────────

    def calculate_pdh_pdl(self) -> dict[str, float]:
        """
        Calculate Previous Day High, Low, and Close.
        Returns {"pdh": float, "pdl": float, "prev_close": float}.
        """
        if len(self.df) < 2:
            return {"pdh": 0.0, "pdl": 0.0, "prev_close": 0.0}

        try:
            # Ensure we have a DatetimeIndex
            df_copy = self.df.copy()
            if not isinstance(df_copy.index, pd.DatetimeIndex):
                return {"pdh": 0.0, "pdl": 0.0, "prev_close": 0.0}

            # Group by date
            df_copy["_date"] = df_copy.index.normalize()
            daily_groups = df_copy.groupby("_date")
            dates = sorted(daily_groups.groups.keys())

            if len(dates) < 2:
                return {"pdh": 0.0, "pdl": 0.0, "prev_close": 0.0}

            prev_date = dates[-2]
            prev_day = daily_groups.get_group(prev_date)

            pdh = float(prev_day["high"].max())
            pdl = float(prev_day["low"].min())
            prev_close = float(prev_day["close"].iloc[-1])

            return {"pdh": pdh, "pdl": pdl, "prev_close": prev_close}
        except Exception:
            return {"pdh": 0.0, "pdl": 0.0, "prev_close": 0.0}

    def calculate_session_levels(self) -> dict[str, float]:
        """
        Calculate high/low for Asia (00-08 UTC), London (08-16 UTC), NY (13-21 UTC) sessions.
        Uses the last complete session of each type.
        Returns dict with asia_high, asia_low, london_high, london_low, ny_high, ny_low.
        """
        default = {
            "asia_high": 0.0, "asia_low": 0.0,
            "london_high": 0.0, "london_low": 0.0,
            "ny_high": 0.0, "ny_low": 0.0,
        }

        if len(self.df) < 10:
            return default

        try:
            df_copy = self.df.copy()
            if not isinstance(df_copy.index, pd.DatetimeIndex):
                return default

            # Make UTC-aware if not already
            if df_copy.index.tz is None:
                df_copy.index = df_copy.index.tz_localize("UTC")
            else:
                df_copy.index = df_copy.index.tz_convert("UTC")

            hours = df_copy.index.hour

            # Session masks
            asia_mask = (hours >= 0) & (hours < 8)
            london_mask = (hours >= 8) & (hours < 16)
            ny_mask = (hours >= 13) & (hours < 21)

            result = {}
            for name, mask in [("asia", asia_mask), ("london", london_mask), ("ny", ny_mask)]:
                session_df = df_copy[mask]
                if len(session_df) >= 2:
                    result[f"{name}_high"] = float(session_df["high"].max())
                    result[f"{name}_low"] = float(session_df["low"].min())
                else:
                    result[f"{name}_high"] = 0.0
                    result[f"{name}_low"] = 0.0

            return result
        except Exception:
            return default

    def calculate_fibonacci(self, lookback: int = 50) -> dict[str, float]:
        """
        Calculate Fibonacci retracement levels over the last `lookback` candles.
        Returns swing_high, swing_low and fib levels: 0.236, 0.382, 0.500, 0.618, 0.786.
        """
        default = {
            "swing_high": 0.0, "swing_low": 0.0,
            "fib_236": 0.0, "fib_382": 0.0, "fib_500": 0.0,
            "fib_618": 0.0, "fib_786": 0.0,
        }

        if len(self.df) < 2:
            return default

        try:
            window = self.df.tail(lookback)
            swing_high = float(window["high"].max())
            swing_low = float(window["low"].min())
            rng = swing_high - swing_low

            if rng <= 0:
                return default

            # Fibonacci retracement from high to low
            return {
                "swing_high": swing_high,
                "swing_low": swing_low,
                "fib_236": swing_high - 0.236 * rng,
                "fib_382": swing_high - 0.382 * rng,
                "fib_500": swing_high - 0.500 * rng,
                "fib_618": swing_high - 0.618 * rng,
                "fib_786": swing_high - 0.786 * rng,
            }
        except Exception:
            return default

    def calculate_volume_profile(self, bins: int = 20) -> dict[str, float]:
        """
        Divide price range into `bins` bins and calculate volume at each level.
        Returns vpoc (Volume Point of Control), vah (Value Area High), val (Value Area Low).
        Value Area = 70% of total volume.
        """
        default = {"vpoc": 0.0, "vah": 0.0, "val": 0.0}

        if len(self.df) < 5:
            return default

        try:
            highs = self.df["high"].values
            lows = self.df["low"].values
            volumes = self.df["volume"].values

            price_min = float(lows.min())
            price_max = float(highs.max())
            price_range = price_max - price_min

            if price_range <= 0:
                return default

            bin_size = price_range / bins
            bin_volumes = np.zeros(bins)
            bin_centers = np.array([price_min + (i + 0.5) * bin_size for i in range(bins)])

            for i in range(len(self.df)):
                candle_low = float(lows[i])
                candle_high = float(highs[i])
                vol = float(volumes[i])

                if vol <= 0:
                    continue

                # Distribute volume across the candle's price range
                low_bin = max(0, int((candle_low - price_min) / bin_size))
                high_bin = min(bins - 1, int((candle_high - price_min) / bin_size))

                n_bins_spanned = high_bin - low_bin + 1
                vol_per_bin = vol / n_bins_spanned
                for b in range(low_bin, high_bin + 1):
                    bin_volumes[b] += vol_per_bin

            # VPOC = price level with highest volume
            vpoc_idx = int(np.argmax(bin_volumes))
            vpoc = float(bin_centers[vpoc_idx])

            # Value Area = 70% of total volume
            total_volume = float(bin_volumes.sum())
            if total_volume <= 0:
                return {"vpoc": vpoc, "vah": vpoc, "val": vpoc}

            target_volume = total_volume * 0.70
            accumulated = float(bin_volumes[vpoc_idx])
            lo_idx = vpoc_idx
            hi_idx = vpoc_idx

            while accumulated < target_volume:
                can_go_up = hi_idx < bins - 1
                can_go_down = lo_idx > 0

                if not can_go_up and not can_go_down:
                    break

                vol_up = float(bin_volumes[hi_idx + 1]) if can_go_up else -1.0
                vol_down = float(bin_volumes[lo_idx - 1]) if can_go_down else -1.0

                if vol_up >= vol_down:
                    hi_idx += 1
                    accumulated += float(bin_volumes[hi_idx])
                else:
                    lo_idx -= 1
                    accumulated += float(bin_volumes[lo_idx])

            vah = float(bin_centers[hi_idx])
            val = float(bin_centers[lo_idx])

            return {"vpoc": vpoc, "vah": vah, "val": val}
        except Exception:
            return default

    def detect_order_blocks(self, lookback: int = 50) -> list[dict]:
        """
        Detect bullish and bearish order blocks.
        Bullish OB: last bullish candle before a bearish impulse (3×ATR move down).
        Bearish OB: last bearish candle before a bullish impulse (3×ATR move up).
        Returns list of {"type": "bullish"|"bearish", "high": float, "low": float, "index": int}.
        """
        if len(self.df) < 10:
            return []

        try:
            atr_arr = self._calc_atr(14)
            window = self.df.tail(lookback).copy()
            window_atr = atr_arr[-lookback:] if len(atr_arr) >= lookback else atr_arr
            n = len(window)
            result = []

            opens = window["open"].values
            closes = window["close"].values
            highs = window["high"].values
            lows = window["low"].values

            for i in range(1, n - 1):
                atr_val = float(window_atr[i]) if i < len(window_atr) and not np.isnan(window_atr[i]) else 0.0
                if atr_val <= 0:
                    continue

                impulse_threshold = 3.0 * atr_val

                # Check for bullish impulse (big up move) starting at candle i
                # → candle i-1 is a bearish OB (last bearish candle before the impulse)
                up_move = float(closes[i]) - float(opens[i])
                if up_move >= impulse_threshold and float(opens[i - 1]) > float(closes[i - 1]):
                    result.append({
                        "type": "bearish",
                        "high": float(highs[i - 1]),
                        "low": float(lows[i - 1]),
                        "index": i - 1,
                    })

                # Check for bearish impulse (big down move) starting at candle i
                # → candle i-1 is a bullish OB (last bullish candle before the impulse)
                down_move = float(opens[i]) - float(closes[i])
                if down_move >= impulse_threshold and float(closes[i - 1]) > float(opens[i - 1]):
                    result.append({
                        "type": "bullish",
                        "high": float(highs[i - 1]),
                        "low": float(lows[i - 1]),
                        "index": i - 1,
                    })

            return result
        except Exception:
            return []

    def detect_fair_value_gaps(self, lookback: int = 30) -> list[dict]:
        """
        Detect Fair Value Gaps (FVGs).
        Bullish FVG: candle[i].low > candle[i-2].high (gap up).
        Bearish FVG: candle[i].high < candle[i-2].low (gap down).
        A FVG is mitigated when price returns to fill it.
        Returns last 3 unmitigated FVGs.
        """
        if len(self.df) < 3:
            return []

        try:
            window = self.df.tail(lookback + 3).copy()
            n = len(window)
            closes = window["close"].values
            highs = window["high"].values
            lows = window["low"].values

            fvgs = []
            for i in range(2, n):
                # Bullish FVG: gap between candle[i-2].high and candle[i].low
                if float(lows[i]) > float(highs[i - 2]):
                    top = float(lows[i])
                    bottom = float(highs[i - 2])

                    # Check mitigation: did price close back into the gap after i?
                    mitigated = False
                    for j in range(i + 1, n):
                        if float(lows[j]) <= top and float(highs[j]) >= bottom:
                            mitigated = True
                            break

                    if not mitigated:
                        fvgs.append({
                            "type": "bullish",
                            "top": top,
                            "bottom": bottom,
                            "index": i,
                        })

                # Bearish FVG: gap between candle[i].high and candle[i-2].low
                if float(highs[i]) < float(lows[i - 2]):
                    top = float(lows[i - 2])
                    bottom = float(highs[i])

                    # Check mitigation
                    mitigated = False
                    for j in range(i + 1, n):
                        if float(lows[j]) <= top and float(highs[j]) >= bottom:
                            mitigated = True
                            break

                    if not mitigated:
                        fvgs.append({
                            "type": "bearish",
                            "top": top,
                            "bottom": bottom,
                            "index": i,
                        })

            # Return last 3 unmitigated FVGs
            return fvgs[-3:] if len(fvgs) > 3 else fvgs
        except Exception:
            return []

    def calculate_ta_score_v2(self) -> float:
        """
        Enhanced TA score incorporating Smart Money Concepts (order blocks + FVGs).
        SMC gets 15% weight; other weights scaled down proportionally to 85%.
        Returns float in [-100, +100].
        """
        base_score = self.calculate_ta_score()
        # base_score already in [-100, +100] using TA_WEIGHTS

        # SMC signal: combine order blocks and FVGs relative to current price
        smc_signal = 0.0
        smc_count = 0

        indicators = self.calculate_all_indicators()
        current_price = indicators.get("current_price", 0.0)
        if not current_price:
            return base_score

        # Order blocks: is price near a bullish OB (support) or bearish OB (resistance)?
        bull_ob_low = indicators.get("bull_ob_low")
        bull_ob_high = indicators.get("bull_ob_high")
        bear_ob_low = indicators.get("bear_ob_low")
        bear_ob_high = indicators.get("bear_ob_high")
        atr_val = indicators.get("atr", 0.0) or 0.0

        if bull_ob_high and bull_ob_low and atr_val > 0:
            # Price at/above bullish OB → support → bullish
            if bull_ob_low <= current_price <= bull_ob_high + atr_val:
                smc_signal += 1.0
                smc_count += 1

        if bear_ob_high and bear_ob_low and atr_val > 0:
            # Price at/below bearish OB → resistance → bearish
            if bear_ob_low - atr_val <= current_price <= bear_ob_high:
                smc_signal -= 1.0
                smc_count += 1

        # FVGs: is there an unfilled FVG above or below price?
        bull_fvg_bottom = indicators.get("bull_fvg_bottom")
        bull_fvg_top = indicators.get("bull_fvg_top")
        bear_fvg_bottom = indicators.get("bear_fvg_bottom")
        bear_fvg_top = indicators.get("bear_fvg_top")

        if bull_fvg_bottom and bull_fvg_top:
            # Bullish FVG below price: price may retrace into it (support zone)
            if bull_fvg_bottom < current_price:
                smc_signal += 0.5
                smc_count += 1

        if bear_fvg_bottom and bear_fvg_top:
            # Bearish FVG above price: price may retrace into it (resistance zone)
            if bear_fvg_top > current_price:
                smc_signal -= 0.5
                smc_count += 1

        # Normalize SMC signal to [-100, +100]
        if smc_count > 0:
            smc_score = (smc_signal / smc_count) * 100.0
        else:
            smc_score = 0.0

        # Blend: 85% base TA + 15% SMC
        combined = 0.85 * base_score + 0.15 * smc_score
        return max(-100.0, min(100.0, combined))
