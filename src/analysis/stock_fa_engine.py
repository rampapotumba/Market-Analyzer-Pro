"""Stock Fundamental Analysis Engine v2.

Score components (for US/EU equities):
  - Valuation (P/E ratio vs sector):  25%
  - Earnings quality (EPS growth):     25%
  - Analyst consensus:                 20%
  - Earnings surprise (4Q avg):        15%
  - Insider activity:                  15%

Score range: [-100, +100] where positive = bullish.

Data sources:
  - Finnhub API (FINNHUB_KEY): company metrics, recommendations, earnings
  - yfinance: supplementary data, insider trades
  - DB (company_fundamentals table): cached from prior collections

This module computes the score only. Actual fetching/persisting is done by
`fundamentals_collector.py`. Here we read from the local cache/DB.
"""

import logging
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import CompanyFundamentals

logger = logging.getLogger(__name__)

# Weights
_WEIGHTS = {
    "valuation": 0.25,
    "earnings": 0.25,
    "analyst": 0.20,
    "earnings_surprise": 0.15,
    "insider": 0.15,
}

# Sector-typical P/E ratios used for relative valuation
_SECTOR_PE: dict[str, float] = {
    "Technology": 28.0,
    "Healthcare": 22.0,
    "Financials": 14.0,
    "Consumer Discretionary": 25.0,
    "Consumer Staples": 20.0,
    "Energy": 12.0,
    "Utilities": 18.0,
    "Real Estate": 30.0,
    "Industrials": 20.0,
    "Materials": 16.0,
    "Communication Services": 22.0,
    "Unknown": 20.0,
}


class StockFAEngine:
    """Computes fundamental analysis score for a stock instrument."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ── Public ────────────────────────────────────────────────────────────────

    async def calculate_stock_fa_score(
        self,
        instrument_id: int,
        sector: Optional[str] = None,
    ) -> dict:
        """Return FA score dict for a stock.

        Returns:
            {
                "score": float,
                "components": {
                    "valuation": float,
                    "earnings": float,
                    "analyst": float,
                    "earnings_surprise": float,
                    "insider": float,
                },
                "data": {...},   # raw fundamental values
            }
        """
        fundamentals = await self._get_latest_fundamentals(instrument_id)

        valuation_score = self._score_valuation(fundamentals, sector or "Unknown")
        earnings_score = self._score_earnings(fundamentals)
        analyst_score = self._score_analyst(fundamentals)
        surprise_score = self._score_earnings_surprise(fundamentals)
        insider_score = self._score_insider(fundamentals)

        components = {
            "valuation": valuation_score,
            "earnings": earnings_score,
            "analyst": analyst_score,
            "earnings_surprise": surprise_score,
            "insider": insider_score,
        }

        composite = sum(_WEIGHTS[k] * v for k, v in components.items())
        composite = max(-100.0, min(100.0, composite))

        logger.debug(
            "StockFA instrument_id=%d: score=%.2f components=%s",
            instrument_id,
            composite,
            {k: f"{v:.1f}" for k, v in components.items()},
        )

        raw_data = {}
        if fundamentals:
            raw_data = {
                "pe_ratio": float(fundamentals.pe_ratio) if fundamentals.pe_ratio else None,
                "eps": float(fundamentals.eps) if fundamentals.eps else None,
                "revenue_growth_yoy": float(fundamentals.revenue_growth_yoy)
                    if fundamentals.revenue_growth_yoy else None,
                "analyst_rating": fundamentals.analyst_rating,
                "earnings_surprise_avg": float(fundamentals.earnings_surprise_avg)
                    if fundamentals.earnings_surprise_avg else None,
                "insider_net_shares": fundamentals.insider_net_shares,
            }

        return {
            "score": composite,
            "components": components,
            "data": raw_data,
        }

    async def get_company_metrics(self, instrument_id: int) -> Optional[dict]:
        """Return raw company metrics from the DB cache."""
        f = await self._get_latest_fundamentals(instrument_id)
        if not f:
            return None
        return {
            "pe_ratio": float(f.pe_ratio) if f.pe_ratio else None,
            "eps": float(f.eps) if f.eps else None,
            "revenue_growth_yoy": float(f.revenue_growth_yoy) if f.revenue_growth_yoy else None,
            "gross_margin": float(f.gross_margin) if f.gross_margin else None,
            "net_margin": float(f.net_margin) if f.net_margin else None,
            "debt_to_equity": float(f.debt_to_equity) if f.debt_to_equity else None,
            "roe": float(f.roe) if f.roe else None,
        }

    async def get_analyst_consensus(self, instrument_id: int) -> Optional[dict]:
        """Return analyst consensus from the DB cache."""
        f = await self._get_latest_fundamentals(instrument_id)
        if not f:
            return None
        return {
            "rating": f.analyst_rating,
            "target": float(f.analyst_target) if f.analyst_target else None,
        }

    async def get_earnings_surprise(self, instrument_id: int) -> Optional[float]:
        """Return the 4-quarter average earnings surprise (%)."""
        f = await self._get_latest_fundamentals(instrument_id)
        if not f or f.earnings_surprise_avg is None:
            return None
        return float(f.earnings_surprise_avg)

    async def get_insider_activity(self, instrument_id: int) -> Optional[int]:
        """Return net insider shares (positive = net buying, negative = selling)."""
        f = await self._get_latest_fundamentals(instrument_id)
        if not f:
            return None
        return f.insider_net_shares

    # ── Scoring ───────────────────────────────────────────────────────────────

    def _score_valuation(
        self, f: Optional[CompanyFundamentals], sector: str
    ) -> float:
        """Score based on P/E vs sector average.

        Below-sector P/E → bullish (undervalued relative to peers).
        """
        if f is None or f.pe_ratio is None:
            return 0.0
        pe = float(f.pe_ratio)
        if pe <= 0:
            return -50.0  # negative P/E (losses)

        sector_pe = _SECTOR_PE.get(sector, 20.0)
        # % deviation from sector: -50% = very cheap, +50% = very expensive
        deviation_pct = (pe - sector_pe) / sector_pe * 100.0
        # Map: deviation=-50 → +100, deviation=0 → 0, deviation=+50 → -100
        score = -deviation_pct * 2.0  # flip sign: cheap is good
        return max(-100.0, min(100.0, score))

    def _score_earnings(self, f: Optional[CompanyFundamentals]) -> float:
        """Score based on revenue growth YoY and net margin."""
        if f is None:
            return 0.0

        score = 0.0
        count = 0

        if f.revenue_growth_yoy is not None:
            growth = float(f.revenue_growth_yoy)
            # +20% YoY = +100, 0% = 0, -20% = -100
            score += max(-100.0, min(100.0, growth * 5.0))
            count += 1

        if f.net_margin is not None:
            margin = float(f.net_margin)
            # 20% margin = +100, 0% = 0, -20% = -100
            score += max(-100.0, min(100.0, margin * 5.0))
            count += 1

        return score / count if count > 0 else 0.0

    def _score_analyst(self, f: Optional[CompanyFundamentals]) -> float:
        """Score based on analyst consensus rating."""
        if f is None or f.analyst_rating is None:
            return 0.0
        rating_map = {
            "strong_buy": 100.0,
            "buy": 60.0,
            "hold": 0.0,
            "sell": -60.0,
            "strong_sell": -100.0,
        }
        return rating_map.get(f.analyst_rating.lower().replace(" ", "_"), 0.0)

    def _score_earnings_surprise(self, f: Optional[CompanyFundamentals]) -> float:
        """Score based on 4Q average earnings surprise (%)."""
        if f is None or f.earnings_surprise_avg is None:
            return 0.0
        surprise = float(f.earnings_surprise_avg)
        # +10% avg surprise = +100, -10% = -100
        return max(-100.0, min(100.0, surprise * 10.0))

    def _score_insider(self, f: Optional[CompanyFundamentals]) -> float:
        """Score based on net insider share transactions."""
        if f is None or f.insider_net_shares is None:
            return 0.0
        shares = f.insider_net_shares
        # Normalize: ±500,000 shares = ±100 score
        score = shares / 500_000 * 100.0
        return max(-100.0, min(100.0, score))

    # ── DB access ─────────────────────────────────────────────────────────────

    async def _get_latest_fundamentals(
        self, instrument_id: int
    ) -> Optional[CompanyFundamentals]:
        stmt = (
            select(CompanyFundamentals)
            .where(CompanyFundamentals.instrument_id == instrument_id)
            .order_by(CompanyFundamentals.collected_at.desc())
            .limit(1)
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()
