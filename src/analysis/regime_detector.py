"""Market Regime Detector v2.

Detects one of 7 market regimes from price data:
  1. STRONG_TREND_BULL  — ADX > 30, price above 200-MA, low volatility
  2. STRONG_TREND_BEAR  — ADX > 30, price below 200-MA, low volatility
  3. WEAK_TREND_BULL    — ADX 20-30, price above 200-MA
  4. WEAK_TREND_BEAR    — ADX 20-30, price below 200-MA
  5. RANGING            — ADX < 20
  6. HIGH_VOLATILITY    — ATR percentile > 80
  7. LOW_VOLATILITY     — ATR percentile < 20 (squeeze)

Each regime maps to a distinct weight set for the signal engine and an
ATR multiplier for stop-loss sizing.

Detection inputs:
  - ADX(14) from TA engine
  - 200-period SMA
  - ATR percentile (rolling 252-bar window)
  - VIX level from macro_data (for confirming volatility regime)
"""

import datetime
import logging
from decimal import Decimal
from typing import Optional

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.engine import async_session_factory
from src.database.models import Instrument, MacroData, PriceData, RegimeState

logger = logging.getLogger(__name__)

# ── Regime definitions ────────────────────────────────────────────────────────

REGIMES = [
    "STRONG_TREND_BULL",
    "STRONG_TREND_BEAR",
    "WEAK_TREND_BULL",
    "WEAK_TREND_BEAR",
    "RANGING",
    "HIGH_VOLATILITY",
    "LOW_VOLATILITY",
]

# Weight sets per regime: (ta, fa, sentiment, geo)
_REGIME_WEIGHTS: dict[str, dict[str, float]] = {
    "STRONG_TREND_BULL": {"ta": 0.55, "fa": 0.20, "sentiment": 0.15, "geo": 0.10},
    "STRONG_TREND_BEAR": {"ta": 0.55, "fa": 0.20, "sentiment": 0.15, "geo": 0.10},
    "WEAK_TREND_BULL":   {"ta": 0.45, "fa": 0.25, "sentiment": 0.20, "geo": 0.10},
    "WEAK_TREND_BEAR":   {"ta": 0.45, "fa": 0.25, "sentiment": 0.20, "geo": 0.10},
    "RANGING":           {"ta": 0.35, "fa": 0.30, "sentiment": 0.25, "geo": 0.10},
    "HIGH_VOLATILITY":   {"ta": 0.40, "fa": 0.25, "sentiment": 0.20, "geo": 0.15},
    "LOW_VOLATILITY":    {"ta": 0.50, "fa": 0.25, "sentiment": 0.15, "geo": 0.10},
}

# ATR multipliers for stop-loss per regime
_REGIME_SL_MULTIPLIER: dict[str, float] = {
    "STRONG_TREND_BULL": 1.5,
    "STRONG_TREND_BEAR": 1.5,
    "WEAK_TREND_BULL":   1.8,
    "WEAK_TREND_BEAR":   1.8,
    "RANGING":           1.2,
    "HIGH_VOLATILITY":   2.5,
    "LOW_VOLATILITY":    1.0,
}

# ADX thresholds
_ADX_STRONG = 30.0
_ADX_WEAK = 20.0

# ATR percentile thresholds
_ATR_HIGH_VOL_PCT = 80.0
_ATR_LOW_VOL_PCT = 20.0

# VIX thresholds
_VIX_HIGH = 30.0
_VIX_LOW = 15.0

# Minimum candles for detection (ADX needs ~28, ATR percentile benefits from more)
_MIN_BARS = 50


class RegimeDetector:
    """Detects market regime for instruments and persists to DB."""

    # ── Public ────────────────────────────────────────────────────────────────

    async def detect_all(self) -> None:
        """Run regime detection for all active instruments (daily timeframe)."""
        async with async_session_factory() as session:
            result = await session.execute(
                select(Instrument).where(Instrument.is_active.is_(True))
            )
            instruments = result.scalars().all()

        vix = await self._get_vix()

        for instrument in instruments:
            try:
                await self._detect_and_persist(instrument, "D1", vix)
            except Exception as exc:
                logger.error(
                    "RegimeDetector: failed for %s: %s", instrument.symbol, exc
                )

    async def detect(
        self,
        instrument_id: int,
        timeframe: str = "D1",
    ) -> Optional[str]:
        """Detect regime for a single instrument. Returns regime string."""
        async with async_session_factory() as session:
            price_records = await self._fetch_price_data(session, instrument_id, timeframe)
            vix = await self._get_vix(session)

        if not price_records or len(price_records) < _MIN_BARS:
            return None

        df = _to_df(price_records)
        regime, adx, atr_pct = self._detect_regime(df, vix)
        return regime

    # ── Detection logic ───────────────────────────────────────────────────────

    def _detect_regime(
        self,
        df: pd.DataFrame,
        vix: Optional[float],
    ) -> tuple[str, Optional[float], Optional[float]]:
        """Core regime classification.

        Returns (regime_name, adx, atr_percentile).
        """
        adx = _calculate_adx(df, period=14)
        atr = _calculate_atr(df, period=14)
        atr_pct = _atr_percentile(atr, window=252)
        close = df["close"].iloc[-1]
        sma200 = df["close"].rolling(200).mean().iloc[-1]

        # 1. Volatility regimes take priority if extreme
        if atr_pct is not None and atr_pct > _ATR_HIGH_VOL_PCT:
            # Confirm with VIX if available
            if vix is None or vix > _VIX_HIGH:
                return "HIGH_VOLATILITY", adx, atr_pct

        if atr_pct is not None and atr_pct < _ATR_LOW_VOL_PCT:
            if vix is None or vix < _VIX_LOW:
                return "LOW_VOLATILITY", adx, atr_pct

        # 2. Trend regimes
        if adx is not None:
            bull: Optional[bool] = bool(close > sma200) if pd.notna(sma200) else None
            if adx >= _ADX_STRONG:
                if bull is True:
                    return "STRONG_TREND_BULL", adx, atr_pct
                if bull is False:
                    return "STRONG_TREND_BEAR", adx, atr_pct
            if adx >= _ADX_WEAK:
                if bull is True:
                    return "WEAK_TREND_BULL", adx, atr_pct
                if bull is False:
                    return "WEAK_TREND_BEAR", adx, atr_pct

        # 3. Default: ranging
        return "RANGING", adx, atr_pct

    def _detect_trend(self, adx: Optional[float]) -> str:
        """Classify ADX value into trend strength category."""
        if adx is None:
            return "unknown"
        if adx >= _ADX_STRONG:
            return "strong"
        if adx >= _ADX_WEAK:
            return "weak"
        return "none"

    def _detect_volatility_regime(self, atr_pct: Optional[float]) -> str:
        """Classify ATR percentile into volatility category."""
        if atr_pct is None:
            return "normal"
        if atr_pct >= _ATR_HIGH_VOL_PCT:
            return "high"
        if atr_pct <= _ATR_LOW_VOL_PCT:
            return "low"
        return "normal"

    def get_regime_weights(self, regime: str) -> dict[str, float]:
        """Return signal engine weights for the given regime."""
        return _REGIME_WEIGHTS.get(regime, _REGIME_WEIGHTS["RANGING"])

    def get_atr_multiplier(self, regime: str) -> float:
        """Return SL ATR multiplier for the given regime."""
        return _REGIME_SL_MULTIPLIER.get(regime, 1.5)

    # ── DB helpers ────────────────────────────────────────────────────────────

    async def _detect_and_persist(
        self,
        instrument: Instrument,
        timeframe: str,
        vix: Optional[float],
    ) -> None:
        async with async_session_factory() as session:
            price_records = await self._fetch_price_data(session, instrument.id, timeframe)
            if not price_records or len(price_records) < _MIN_BARS:
                logger.debug(
                    "RegimeDetector: insufficient data for %s (%d bars)",
                    instrument.symbol,
                    len(price_records) if price_records else 0,
                )
                return

            df = _to_df(price_records)
            regime, adx, atr_pct = self._detect_regime(df, vix)
            weights = self.get_regime_weights(regime)
            sl_mult = self.get_atr_multiplier(regime)

            from sqlalchemy.dialects.postgresql import insert  # noqa: PLC0415

            stmt = (
                insert(RegimeState)
                .values(
                    instrument_id=instrument.id,
                    detected_at=datetime.datetime.now(datetime.timezone.utc),
                    regime=regime,
                    adx=Decimal(str(round(adx, 4))) if adx is not None else None,
                    atr_percentile=Decimal(str(round(atr_pct, 4))) if atr_pct is not None else None,
                    vix=Decimal(str(round(vix, 4))) if vix is not None else None,
                    ta_weight=Decimal(str(weights["ta"])),
                    fa_weight=Decimal(str(weights["fa"])),
                    sentiment_weight=Decimal(str(weights["sentiment"])),
                    geo_weight=Decimal(str(weights["geo"])),
                    sl_atr_multiplier=Decimal(str(sl_mult)),
                )
            )
            await session.execute(stmt)
            await session.commit()

            logger.info(
                "RegimeDetector: %s → %s (ADX=%.1f, ATR_pct=%.0f%%)",
                instrument.symbol,
                regime,
                adx or 0,
                atr_pct or 0,
            )

    async def _fetch_price_data(
        self,
        session: AsyncSession,
        instrument_id: int,
        timeframe: str,
        limit: int = 300,
    ) -> list[PriceData]:
        stmt = (
            select(PriceData)
            .where(
                PriceData.instrument_id == instrument_id,
                PriceData.timeframe == timeframe,
            )
            .order_by(PriceData.timestamp.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        records = list(reversed(result.scalars().all()))
        return records

    async def _get_vix(self, session: Optional[AsyncSession] = None) -> Optional[float]:
        """Fetch the most recent VIX value from macro_data."""
        async def _fetch(s: AsyncSession) -> Optional[float]:
            stmt = (
                select(MacroData.value)
                .where(
                    MacroData.country == "US",
                    MacroData.indicator_name == "VIXCLS",
                )
                .order_by(MacroData.release_date.desc())
                .limit(1)
            )
            result = await s.execute(stmt)
            row = result.scalar_one_or_none()
            return float(row) if row is not None else None

        if session is not None:
            return await _fetch(session)
        async with async_session_factory() as s:
            return await _fetch(s)


# ── TA calculations ────────────────────────────────────────────────────────────

def classify_regime_at_point(
    adx: Optional[float],
    atr_pct: Optional[float],
    close: float,
    sma200: float,
    vix: Optional[float] = None,
) -> str:
    """Classify market regime from scalar indicator values. No DataFrame needed.

    Pure function extracted from RegimeDetector._detect_regime() for use in
    backtest pre-computation (O(n) optimization).

    Returns one of: STRONG_TREND_BULL, STRONG_TREND_BEAR, WEAK_TREND_BULL,
    WEAK_TREND_BEAR, RANGING, HIGH_VOLATILITY, LOW_VOLATILITY.
    """
    # 1. Volatility regimes take priority if extreme
    if atr_pct is not None and atr_pct > _ATR_HIGH_VOL_PCT:
        if vix is None or vix > _VIX_HIGH:
            return "HIGH_VOLATILITY"

    if atr_pct is not None and atr_pct < _ATR_LOW_VOL_PCT:
        if vix is None or vix < _VIX_LOW:
            return "LOW_VOLATILITY"

    # 2. Trend regimes
    if adx is not None:
        import math
        if not math.isnan(sma200) and sma200 > 0:
            bull: Optional[bool] = bool(close > sma200)
        else:
            bull = None

        if adx >= _ADX_STRONG:
            if bull is True:
                return "STRONG_TREND_BULL"
            if bull is False:
                return "STRONG_TREND_BEAR"
        if adx >= _ADX_WEAK:
            if bull is True:
                return "WEAK_TREND_BULL"
            if bull is False:
                return "WEAK_TREND_BEAR"

    # 3. Default: ranging
    return "RANGING"


def _to_df(records: list[PriceData]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [float(r.open) for r in records],
            "high": [float(r.high) for r in records],
            "low": [float(r.low) for r in records],
            "close": [float(r.close) for r in records],
            "volume": [float(r.volume) for r in records],
        }
    )


def _calculate_adx(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    """Compute ADX using Wilder's method."""
    try:
        high = df["high"]
        low = df["low"]
        close = df["close"]

        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0

        tr = pd.concat(
            [
                high - low,
                (high - close.shift()).abs(),
                (low - close.shift()).abs(),
            ],
            axis=1,
        ).max(axis=1)

        atr = tr.ewm(alpha=1 / period, adjust=False).mean()
        plus_di = 100 * (plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr)
        minus_di = 100 * (minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr)

        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx = dx.ewm(alpha=1 / period, adjust=False).mean()
        val = adx.iloc[-1]
        return None if pd.isna(val) else float(val)
    except Exception as exc:
        logger.debug("ADX calculation error: %s", exc)
        return None


def _calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Return ATR series."""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def _atr_percentile(atr: pd.Series, window: int = 252) -> Optional[float]:
    """Return the percentile rank of the current ATR within the rolling window."""
    if len(atr) < window:
        window = len(atr)
    if window < 2:
        return None
    rolling = atr.iloc[-window:]
    current = atr.iloc[-1]
    if pd.isna(current):
        return None
    pct = (rolling < current).sum() / len(rolling) * 100.0
    return float(pct)
