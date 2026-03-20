"""CryptoExtremeStrategy — counter-trend crypto strategy on D1 (TASK-V7-23).

Entry logic:
  LONG (buy fear): F&G <= 25, D1 RSI(14) <= 30, higher low vs prev day,
                   funding rate < 0 (optional), confirmation candle.
  SHORT (sell greed): F&G >= 75, D1 RSI(14) >= 70, lower high vs prev day,
                      funding rate > 0.05% (optional), confirmation candle.

Exit:
  SL: 3.5 × ATR(14)
  TP: 5.0 × ATR(14)
  Time exit: 14 D1 candles (handled by engine)

Target instruments: BTC/USDT, ETH/USDT (crypto market_type only).
"""

import logging
from decimal import Decimal
from typing import Optional

import pandas as pd

from src.backtesting.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

FEAR_GREED_EXTREME_FEAR_THRESHOLD = 25.0
FEAR_GREED_EXTREME_GREED_THRESHOLD = 75.0

RSI_OVERSOLD_THRESHOLD = 30.0
RSI_OVERBOUGHT_THRESHOLD = 70.0

FUNDING_RATE_POSITIVE_THRESHOLD = 0.001  # 0.1% expressed as fraction
FUNDING_RATE_NEGATIVE_THRESHOLD = 0.0    # any negative

ATR_SL_MULTIPLIER = Decimal("3.5")
ATR_TP_MULTIPLIER = Decimal("5.0")

TIME_EXIT_CANDLES = 14

RSI_PERIOD = 14
ATR_PERIOD = 14

ALLOWED_SYMBOLS = {"BTC/USDT", "ETH/USDT"}

# Minimum bars required to compute RSI and ATR reliably
MIN_BARS_REQUIRED = RSI_PERIOD + 2


# ── Indicator helpers ─────────────────────────────────────────────────────────


def _compute_rsi(series: pd.Series, period: int = RSI_PERIOD) -> Optional[float]:
    """Compute RSI(period) using Wilder's smoothing. Returns last value or None."""
    if len(series) < period + 1:
        return None
    delta = series.diff().dropna()
    gains = delta.clip(lower=0)
    losses = (-delta).clip(lower=0)

    avg_gain = gains.iloc[:period].mean()
    avg_loss = losses.iloc[:period].mean()

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains.iloc[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses.iloc[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _compute_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> Optional[float]:
    """Compute ATR(period) using Wilder's smoothing. Returns last value or None."""
    if len(df) < period + 1:
        return None

    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    tr = tr.dropna()

    if len(tr) < period:
        return None

    atr = tr.iloc[:period].mean()
    for i in range(period, len(tr)):
        atr = (atr * (period - 1) + tr.iloc[i]) / period

    return float(atr)


# ── Strategy ──────────────────────────────────────────────────────────────────


class CryptoExtremeStrategy(BaseStrategy):
    """Counter-trend crypto extreme strategy on D1 timeframe.

    Enters LONG at extreme fear (F&G <= 25, RSI <= 30) and SHORT at extreme
    greed (F&G >= 75, RSI >= 70) with confirmation candle requirement.
    Only trades BTC/USDT and ETH/USDT.
    """

    def name(self) -> str:
        return "crypto_extreme"

    def check_entry(self, context: dict) -> Optional[dict]:
        """Check for a counter-trend entry signal.

        Returns entry dict or None if no signal.
        """
        symbol: str = context.get("symbol", "")
        market_type: str = context.get("market_type", "")
        df: Optional[pd.DataFrame] = context.get("df")
        fear_greed: Optional[float] = context.get("fear_greed")

        # ── Crypto-only guard ─────────────────────────────────────────────────
        if market_type != "crypto" or symbol not in ALLOWED_SYMBOLS:
            return None

        # ── Fear & Greed required ─────────────────────────────────────────────
        if fear_greed is None or fear_greed == 0:
            logger.warning(
                "[CryptoExtremeStrategy] fear_greed is None or 0 for %s — skipping",
                symbol,
            )
            return None

        # ── DataFrame required ────────────────────────────────────────────────
        if df is None or len(df) < MIN_BARS_REQUIRED:
            logger.warning(
                "[CryptoExtremeStrategy] insufficient bars (%d) for %s — skipping",
                len(df) if df is not None else 0,
                symbol,
            )
            return None

        # ── Compute indicators ────────────────────────────────────────────────
        rsi = _compute_rsi(df["close"], RSI_PERIOD)
        if rsi is None:
            logger.warning(
                "[CryptoExtremeStrategy] could not compute RSI for %s — skipping",
                symbol,
            )
            return None

        atr = _compute_atr(df, ATR_PERIOD)
        if atr is None or atr <= 0:
            logger.warning(
                "[CryptoExtremeStrategy] could not compute ATR for %s — skipping",
                symbol,
            )
            return None

        # ── Need at least 3 candles for structural checks + confirmation ──────
        if len(df) < 3:
            return None

        prev_candle = df.iloc[-2]   # candle that just closed (signal candle)
        prev2_candle = df.iloc[-3]  # candle before that

        # Funding rate from context (optional)
        funding_rate: Optional[float] = context.get("funding_rate")

        direction = self._determine_direction(
            fear_greed=fear_greed,
            rsi=rsi,
            prev_candle=prev_candle,
            prev2_candle=prev2_candle,
            funding_rate=funding_rate,
        )

        if direction is None:
            return None

        # ── Confirmation: last closed candle closes above its open (LONG)
        #    or below its open (SHORT) ────────────────────────────────────────
        if not self._check_confirmation(prev_candle, direction):
            return None

        # ── Build signal ──────────────────────────────────────────────────────
        atr_dec = Decimal(str(round(atr, 10)))
        composite = Decimal("20") if direction == "LONG" else Decimal("-20")

        return {
            "direction": direction,
            "entry_price": None,  # engine sets next candle open
            "composite_score": composite,
            "regime": context.get("regime", "UNKNOWN"),
            "atr": atr_dec,
            "sl_atr_multiplier": float(ATR_SL_MULTIPLIER),
            "tp_atr_multiplier": float(ATR_TP_MULTIPLIER),
            "time_exit_candles": TIME_EXIT_CANDLES,
            "position_pct": 2.0,
            "ta_indicators": context.get("ta_indicators", {}),
            "support_levels": [],
            "resistance_levels": [],
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    def _determine_direction(
        self,
        fear_greed: float,
        rsi: float,
        prev_candle: pd.Series,
        prev2_candle: pd.Series,
        funding_rate: Optional[float],
    ) -> Optional[str]:
        """Return "LONG", "SHORT", or None based on entry conditions."""
        if fear_greed <= FEAR_GREED_EXTREME_FEAR_THRESHOLD and rsi <= RSI_OVERSOLD_THRESHOLD:
            # Higher low vs previous day (bullish reversal structure)
            if prev_candle["low"] <= prev2_candle["low"]:
                return None
            # Funding rate check (optional — skip filter if not available)
            if funding_rate is not None and funding_rate >= FUNDING_RATE_NEGATIVE_THRESHOLD:
                return None
            return "LONG"

        if fear_greed >= FEAR_GREED_EXTREME_GREED_THRESHOLD and rsi >= RSI_OVERBOUGHT_THRESHOLD:
            # Lower high vs previous day (bearish reversal structure)
            if prev_candle["high"] >= prev2_candle["high"]:
                return None
            # Funding rate check (optional — skip filter if not available)
            if funding_rate is not None and funding_rate <= FUNDING_RATE_POSITIVE_THRESHOLD:
                return None
            return "SHORT"

        return None

    def _check_confirmation(self, candle: pd.Series, direction: str) -> bool:
        """Check that the confirmation candle closes in the expected direction."""
        if direction == "LONG":
            return float(candle["close"]) > float(candle["open"])
        # SHORT
        return float(candle["close"]) < float(candle["open"])
