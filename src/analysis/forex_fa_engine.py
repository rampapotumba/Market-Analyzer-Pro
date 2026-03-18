"""Forex Fundamental Analysis Engine v2.

Score components (differential analysis):
  - Interest rate differential (IRD):  40%
  - GDP growth differential:            20%
  - Inflation differential (CPI):       20%
  - Employment differential (unemploy): 10%
  - Trade balance differential:         10%

Score range: [-100, +100] where positive = bullish for base currency.
"""

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.analysis.interest_rate_diff import InterestRateDifferential, _split_forex_symbol
from src.database.models import MacroData

logger = logging.getLogger(__name__)

# Component weights (must sum to 1.0)
_WEIGHTS = {
    "ird": 0.40,
    "gdp": 0.20,
    "cpi": 0.20,
    "employment": 0.10,
    "trade": 0.10,
}

# FRED/ECB/other series IDs mapped to country + indicator type
# key: (country_code, indicator_type)
_INDICATOR_KEYS: dict[str, dict[str, str]] = {
    "USD": {
        "gdp": "GDP",
        "cpi": "CPIAUCSL",
        "employment": "UNRATE",   # unemployment rate (lower = better)
        "trade": "BOPGSTB",
    },
    "EUR": {
        "gdp": "EUGDP",
        "cpi": "HICP",
        "employment": "EURUNR",
        "trade": "EUTRB",
    },
    "JPY": {
        "gdp": "JPNRGDPEXP",
        "cpi": "JPNCPIALLMINMEI",
        "employment": "LRHUTTTTJPM156S",
        "trade": "JPNXTOT",
    },
    "GBP": {
        "gdp": "UKRGDPNQDSMEI",
        "cpi": "GBRCPIALLMINMEI",
        "employment": "LRHUTTTTGBM156S",
        "trade": "GBRXTOT",
    },
    "AUD": {
        "gdp": "AUSGDPEXPQDSMEI",
        "cpi": "AUSCPIALLQINMEI",
        "employment": "LRHUTTTTAUM156S",
        "trade": "AUSXTOT",
    },
    "CAD": {
        "gdp": "CANGDPEXPQDSMEI",
        "cpi": "CPALCY01CAM661N",
        "employment": "LRHUTTTTCAM156S",
        "trade": "CANXTOT",
    },
    "CHF": {
        "gdp": "CHEGDPEXPQDSMEI",
        "cpi": "CHECPIALLMINMEI",
        "employment": "LRHUTTTTCHM156S",
        "trade": "CHEXTOT",
    },
    "NZD": {
        "gdp": "NZLGDPEXPQDSMEI",
        "cpi": "NZLCPIALLQINMEI",
        "employment": "LRHUTTTTEZM156S",
        "trade": "NZLXTOT",
    },
}


class ForexFAEngine:
    """Computes a fundamental analysis score for a forex pair."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._ird = InterestRateDifferential(db)
        self._macro_cache: dict[tuple[str, str], Optional[float]] = {}

    # ── Public ────────────────────────────────────────────────────────────────

    async def analyze(self, symbol: str) -> dict:
        """Return FA analysis dict with score and component breakdown.

        Returns:
            {
                "score": float,           # composite [-100, +100]
                "components": {
                    "ird": float,
                    "gdp": float,
                    "cpi": float,
                    "employment": float,
                    "trade": float,
                },
                "differential_trend": float,
                "symbol": str,
            }
        """
        base, quote = _split_forex_symbol(symbol)
        if not base or not quote:
            return {"score": 0.0, "components": {}, "differential_trend": 0.0, "symbol": symbol}

        # 1. Interest rate differential (already scaled [-100, +100])
        ird_score = await self._ird.score(symbol)
        differential_trend = await self._ird.calculate_differential_trend(symbol)

        # 2. GDP differential (YoY growth %)
        gdp_score = await self._differential_score(base, quote, "gdp", higher_is_better=True)

        # 3. CPI differential (higher CPI = more inflation = bearish for currency)
        cpi_score = await self._differential_score(base, quote, "cpi", higher_is_better=False)

        # 4. Employment: unemployment rate (lower = better)
        emp_score = await self._differential_score(
            base, quote, "employment", higher_is_better=False
        )

        # 5. Trade balance differential (positive trade balance = bullish)
        trade_score = await self._differential_score(base, quote, "trade", higher_is_better=True)

        components = {
            "ird": ird_score,
            "gdp": gdp_score,
            "cpi": cpi_score,
            "employment": emp_score,
            "trade": trade_score,
        }

        composite = sum(_WEIGHTS[k] * v for k, v in components.items())
        composite = max(-100.0, min(100.0, composite))

        logger.debug(
            "ForexFA %s: score=%.2f components=%s",
            symbol,
            composite,
            {k: f"{v:.1f}" for k, v in components.items()},
        )

        return {
            "score": composite,
            "components": components,
            "differential_trend": differential_trend,
            "symbol": symbol,
        }

    async def _score_currency(self, currency: str) -> dict[str, Optional[float]]:
        """Return raw macro values for a single currency."""
        result: dict[str, Optional[float]] = {}
        keys = _INDICATOR_KEYS.get(currency, {})
        for indicator_type, series_id in keys.items():
            result[indicator_type] = await self._get_macro_value(currency, series_id)
        return result

    # ── Private ───────────────────────────────────────────────────────────────

    async def _differential_score(
        self,
        base: str,
        quote: str,
        indicator_type: str,
        higher_is_better: bool = True,
    ) -> float:
        """Compute a [-100, +100] score from the differential of an indicator.

        If data is unavailable for either currency, returns 0.0.
        """
        base_keys = _INDICATOR_KEYS.get(base, {})
        quote_keys = _INDICATOR_KEYS.get(quote, {})

        base_series = base_keys.get(indicator_type)
        quote_series = quote_keys.get(indicator_type)

        if not base_series or not quote_series:
            return 0.0

        base_val = await self._get_macro_value(base, base_series)
        quote_val = await self._get_macro_value(quote, quote_series)

        if base_val is None or quote_val is None:
            return 0.0

        diff = base_val - quote_val
        if not higher_is_better:
            diff = -diff  # invert: lower is better means base lower → bullish base

        # Scale: clamp to ±5 percentage points → ±100 score
        score = (diff / 5.0) * 100.0
        return max(-100.0, min(100.0, score))

    async def _get_macro_value(self, country: str, series_id: str) -> Optional[float]:
        """Fetch the most recent value for a macro indicator from the DB."""
        cache_key = (country, series_id)
        if cache_key in self._macro_cache:
            return self._macro_cache[cache_key]

        stmt = (
            select(MacroData.value)
            .where(
                MacroData.country == country,
                MacroData.indicator_name == series_id,
            )
            .order_by(MacroData.release_date.desc())
            .limit(1)
        )
        result = await self._db.execute(stmt)
        row = result.scalar_one_or_none()
        value = float(row) if row is not None else None
        self._macro_cache[cache_key] = value
        return value
