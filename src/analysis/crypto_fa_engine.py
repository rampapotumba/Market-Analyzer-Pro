"""Crypto Fundamental Analysis Engine v2.

Score components:
  - On-chain metrics (NVT, active addresses):      25%
  - Market structure (dominance, ETF flows):        20%
  - Funding ecosystem (revenue, TVL — placeholder): 15%
  - Cycle analysis (halving, MVRV):                 25%
  - Macro correlation (DXY, risk-on/off):           15%

Score range: [-100, +100] where positive = bullish.

Data flows from:
  - `onchain_data` table (collected by OnchainCollector)
  - `macro_data` table for DXY / VIX
  - Hard-coded Bitcoin halving schedule (updated as needed)
"""

import datetime
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import MacroData, OnchainData

logger = logging.getLogger(__name__)

_WEIGHTS = {
    "onchain": 0.25,
    "market_structure": 0.20,
    "ecosystem": 0.15,
    "cycle": 0.25,
    "macro_correlation": 0.15,
}

# Bitcoin halving dates (approximate, UTC)
_BTC_HALVING_DATES: list[datetime.datetime] = [
    datetime.datetime(2012, 11, 28, tzinfo=datetime.timezone.utc),
    datetime.datetime(2016, 7, 9, tzinfo=datetime.timezone.utc),
    datetime.datetime(2020, 5, 11, tzinfo=datetime.timezone.utc),
    datetime.datetime(2024, 4, 19, tzinfo=datetime.timezone.utc),
    datetime.datetime(2028, 4, 15, tzinfo=datetime.timezone.utc),  # estimated
]

# NVT "fair value" band
_NVT_OVERBOUGHT = 150.0
_NVT_OVERSOLD = 40.0

# MVRV thresholds
_MVRV_OVERBOUGHT = 3.5  # historically expensive
_MVRV_OVERSOLD = 1.0    # historically cheap

# BTC dominance: high dominance = risk-off crypto sentiment (alt bear)
_BTC_DOM_RISK_OFF = 65.0  # % dominance
_BTC_DOM_RISK_ON = 45.0


class CryptoFAEngine:
    """Computes fundamental analysis score for a crypto instrument."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ── Public ────────────────────────────────────────────────────────────────

    async def analyze(self, instrument_id: int, symbol: str) -> dict:
        """Return FA analysis dict with score and breakdown.

        Returns:
            {
                "score": float,
                "components": {...},
                "data": {...},
                "symbol": str,
            }
        """
        onchain = await self._get_latest_onchain(instrument_id)

        onchain_score = self._score_onchain(onchain, symbol)
        market_score = self._score_market_structure(onchain)
        ecosystem_score = 0.0  # placeholder until TVL/protocol revenue data available
        cycle_score = await self._score_cycle(onchain, symbol)
        macro_score = await self._score_macro_correlation()

        components = {
            "onchain": onchain_score,
            "market_structure": market_score,
            "ecosystem": ecosystem_score,
            "cycle": cycle_score,
            "macro_correlation": macro_score,
        }

        composite = sum(_WEIGHTS[k] * v for k, v in components.items())
        composite = max(-100.0, min(100.0, composite))

        data = {}
        if onchain:
            data = {
                "nvt_ratio": float(onchain.nvt_ratio) if onchain.nvt_ratio else None,
                "mvrv_ratio": float(onchain.mvrv_ratio) if onchain.mvrv_ratio else None,
                "active_addresses": onchain.active_addresses,
                "funding_rate": float(onchain.funding_rate) if onchain.funding_rate else None,
                "dominance": float(onchain.dominance) if onchain.dominance else None,
            }

        logger.debug(
            "CryptoFA %s: score=%.2f components=%s",
            symbol,
            composite,
            {k: f"{v:.1f}" for k, v in components.items()},
        )

        return {
            "score": composite,
            "components": components,
            "data": data,
            "symbol": symbol,
        }

    # ── Score components ──────────────────────────────────────────────────────

    def _score_onchain(self, onchain: Optional[OnchainData], symbol: str) -> float:
        """Score from NVT ratio and active address trend."""
        if onchain is None:
            return 0.0

        score_parts = []

        # NVT ratio: lower = undervalued network
        if onchain.nvt_ratio is not None:
            nvt = float(onchain.nvt_ratio)
            if nvt <= _NVT_OVERSOLD:
                nvt_score = 80.0 + ((_NVT_OVERSOLD - nvt) / _NVT_OVERSOLD) * 20.0
            elif nvt >= _NVT_OVERBOUGHT:
                nvt_score = -80.0 - ((nvt - _NVT_OVERBOUGHT) / _NVT_OVERBOUGHT) * 20.0
            else:
                # Linear interpolation between oversold and overbought
                ratio = (nvt - _NVT_OVERSOLD) / (_NVT_OVERBOUGHT - _NVT_OVERSOLD)
                nvt_score = 80.0 - ratio * 160.0
            score_parts.append(max(-100.0, min(100.0, nvt_score)))

        # Exchange flows: net outflow (inflow - outflow < 0) = accumulation = bullish
        if onchain.exchange_inflow is not None and onchain.exchange_outflow is not None:
            net_flow = float(onchain.exchange_inflow) - float(onchain.exchange_outflow)
            # Normalize: ±10,000 BTC/day → ±100 score; flip sign (outflow = bullish)
            flow_score = -(net_flow / 10_000) * 100.0
            score_parts.append(max(-100.0, min(100.0, flow_score)))

        return sum(score_parts) / len(score_parts) if score_parts else 0.0

    def _score_market_structure(self, onchain: Optional[OnchainData]) -> float:
        """Score from BTC dominance and funding rate."""
        if onchain is None:
            return 0.0

        score_parts = []

        # Funding rate: high positive = overleveraged longs = bearish
        if onchain.funding_rate is not None:
            fr = float(onchain.funding_rate)
            # Annualised: fr is 8h rate; threshold ±0.03%/8h
            if abs(fr) < 0.01:
                score_parts.append(0.0)  # neutral
            elif fr > 0:
                # Positive funding: longs pay shorts → overleveraged longs → bearish
                fr_score = -min(100.0, (fr / 0.05) * 100.0)
                score_parts.append(fr_score)
            else:
                # Negative funding: shorts pay longs → oversold → bullish
                fr_score = min(100.0, (abs(fr) / 0.05) * 100.0)
                score_parts.append(fr_score)

        # BTC dominance: relevant mainly for altcoins (non-BTC)
        if onchain.dominance is not None:
            dom = float(onchain.dominance)
            # High dominance = capital flowing to BTC away from alts
            dom_score = -((dom - 50.0) / 20.0) * 50.0  # flat at 50%, ±50pts at 70/30
            score_parts.append(max(-100.0, min(100.0, dom_score)))

        return sum(score_parts) / len(score_parts) if score_parts else 0.0

    async def _score_cycle(self, onchain: Optional[OnchainData], symbol: str) -> float:
        """Score from halving cycle position and MVRV ratio."""
        score_parts = []

        # MVRV ratio
        if onchain is not None and onchain.mvrv_ratio is not None:
            mvrv = float(onchain.mvrv_ratio)
            if mvrv <= _MVRV_OVERSOLD:
                score_parts.append(80.0)
            elif mvrv >= _MVRV_OVERBOUGHT:
                score_parts.append(-80.0)
            else:
                ratio = (mvrv - _MVRV_OVERSOLD) / (_MVRV_OVERBOUGHT - _MVRV_OVERSOLD)
                score_parts.append(80.0 - ratio * 160.0)

        # Halving cycle position (applies primarily to BTC, BTC-correlated assets)
        halving_score = _halving_cycle_score()
        score_parts.append(halving_score)

        return sum(score_parts) / len(score_parts) if score_parts else 0.0

    async def _score_macro_correlation(self) -> float:
        """Score from macro environment (DXY, VIX).

        Strong USD (high DXY) + risk-off (high VIX) = bearish for crypto.
        Weak USD + risk-on = bullish.
        """
        dxy = await self._get_macro_value("US", "DXY")
        vix = await self._get_macro_value("US", "VIXCLS")

        score_parts = []

        if dxy is not None:
            # DXY 100 = neutral; 110 = strong USD (bearish crypto); 90 = weak USD (bullish)
            dxy_score = -(dxy - 100.0) * 5.0  # each 1pt DXY → -5 score pts
            score_parts.append(max(-100.0, min(100.0, dxy_score)))

        if vix is not None:
            # VIX 20 = normal; 30 = elevated fear; 40+ = panic
            if vix < 20:
                vix_score = min(50.0, (20.0 - vix) * 5.0)   # low VIX = risk-on
            elif vix < 30:
                vix_score = -(vix - 20.0) * 5.0
            else:
                vix_score = -50.0 - (vix - 30.0) * 5.0
            score_parts.append(max(-100.0, min(100.0, vix_score)))

        return sum(score_parts) / len(score_parts) if score_parts else 0.0

    # ── DB helpers ────────────────────────────────────────────────────────────

    async def _get_latest_onchain(self, instrument_id: int) -> Optional[OnchainData]:
        stmt = (
            select(OnchainData)
            .where(OnchainData.instrument_id == instrument_id)
            .order_by(OnchainData.timestamp.desc())
            .limit(1)
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_macro_value(self, country: str, indicator: str) -> Optional[float]:
        stmt = (
            select(MacroData.value)
            .where(
                MacroData.country == country,
                MacroData.indicator_name == indicator,
            )
            .order_by(MacroData.release_date.desc())
            .limit(1)
        )
        result = await self._db.execute(stmt)
        row = result.scalar_one_or_none()
        return float(row) if row is not None else None


# ── Halving cycle helper ───────────────────────────────────────────────────────

def _halving_cycle_score() -> float:
    """Return a cycle-position score based on where we are between halvings.

    Phase mapping (days since last halving):
      0-180  (accumulation right after halving):  +60
      180-365 (early bull):                        +80
      365-730 (mid bull):                         +100
      730-900 (late bull / top risk):              +20
      900+   (bear / pre-halving):                 -20
    """
    now = datetime.datetime.now(datetime.timezone.utc)

    # Find last and next halving
    past_halvings = [h for h in _BTC_HALVING_DATES if h <= now]
    future_halvings = [h for h in _BTC_HALVING_DATES if h > now]

    if not past_halvings:
        return 0.0

    last_halving = past_halvings[-1]
    days_since = (now - last_halving).days

    if days_since < 180:
        return 60.0
    if days_since < 365:
        return 80.0
    if days_since < 730:
        return 100.0
    if days_since < 900:
        return 20.0
    return -20.0
