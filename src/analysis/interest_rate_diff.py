"""Interest Rate Differential analysis for forex pairs.

The differential is computed as:
    diff = base_currency_rate - quote_currency_rate

A positive differential (base yields more) is bullish for the pair (carry trade).
A negative differential (quote yields more) is bearish (pair should weaken).

Score range: [-100, +100]
Mapping:
    diff ≥  +2.0 pp → +80 to +100 (strong bullish carry)
    diff ≥  +1.0 pp → +40 to +80
    diff ≥  +0.25 pp → +10 to +40
    diff around 0   → neutral
    diff ≤  -0.25 pp → -10 to -40
    diff ≤  -1.0 pp → -40 to -80
    diff ≤  -2.0 pp → -80 to -100 (strong bearish carry)

The trend component adds ±20% to the raw score if the differential is
widening/narrowing over the past `lookback_months`.
"""

import datetime
import logging
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import CentralBankRate

logger = logging.getLogger(__name__)

# Maps currency code → central bank identifier stored in central_bank_rates
_CURRENCY_TO_BANK: dict[str, str] = {
    "USD": "FED",
    "EUR": "ECB",
    "JPY": "BOJ",
    "GBP": "BOE",
    "AUD": "RBA",
    "CAD": "BOC",
    "CHF": "SNB",
    "NZD": "RBNZ",
}

# Forex symbols understood (base/quote extracted from symbol string)
# e.g. "EURUSD" → base="EUR", quote="USD"
# e.g. "EUR/USD" → same


def _split_forex_symbol(symbol: str) -> tuple[Optional[str], Optional[str]]:
    """Return (base, quote) currencies from a forex symbol.

    Handles 'EURUSD', 'EUR/USD', 'EUR_USD' formats for 3-letter currency codes.
    """
    clean = symbol.upper().replace("/", "").replace("_", "").replace("-", "")
    if len(clean) == 6:
        return clean[:3], clean[3:]
    return None, None


class InterestRateDifferential:
    """Computes interest rate differential scores for forex pairs."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._rate_cache: dict[str, Optional[float]] = {}

    # ── Public API ─────────────────────────────────────────────────────────────

    async def calculate_differential(self, symbol: str) -> float:
        """Return the raw interest rate differential (base - quote, in percentage points).

        Returns 0.0 if either currency rate is unavailable.
        """
        base, quote = _split_forex_symbol(symbol)
        if not base or not quote:
            logger.debug("IRD: cannot parse symbol %r", symbol)
            return 0.0

        base_rate = await self._get_rate(base)
        quote_rate = await self._get_rate(quote)

        if base_rate is None or quote_rate is None:
            logger.debug(
                "IRD: missing rate for %s (base=%s, quote=%s) — returning 0",
                symbol,
                base_rate,
                quote_rate,
            )
            return 0.0

        diff = base_rate - quote_rate
        logger.debug(
            "IRD: %s diff=%.4f pp (%s=%.4f, %s=%.4f)",
            symbol,
            diff,
            base,
            base_rate,
            quote,
            quote_rate,
        )
        return diff

    async def calculate_differential_trend(
        self, symbol: str, lookback_months: int = 6
    ) -> float:
        """Return the change in the differential over `lookback_months`.

        Positive = differential widened in favour of base currency (bullish).
        Negative = differential narrowed (bearish for base).
        Returns 0.0 if historical data is insufficient.
        """
        base, quote = _split_forex_symbol(symbol)
        if not base or not quote:
            return 0.0

        current_diff = await self.calculate_differential(symbol)
        past_diff = await self._get_historical_differential(base, quote, lookback_months)
        if past_diff is None:
            return 0.0

        return current_diff - past_diff

    async def score(self, symbol: str) -> float:
        """Return a composite score in [-100, +100] for the forex symbol.

        Combines absolute differential level with 6-month trend.
        """
        diff = await self.calculate_differential(symbol)
        trend = await self.calculate_differential_trend(symbol, lookback_months=6)

        # Map differential to base score
        base_score = _diff_to_score(diff)

        # Trend modifier: ±20 points max
        trend_modifier = _diff_to_score(trend) * 0.20

        raw = base_score + trend_modifier
        return max(-100.0, min(100.0, raw))

    # ── Private helpers ────────────────────────────────────────────────────────

    async def _get_rate(self, currency: str) -> Optional[float]:
        """Fetch the most recent policy rate for the given currency from DB."""
        if currency in self._rate_cache:
            return self._rate_cache[currency]

        bank = _CURRENCY_TO_BANK.get(currency)
        if not bank:
            logger.debug("IRD: no bank mapping for currency %s", currency)
            self._rate_cache[currency] = None
            return None

        stmt = (
            select(CentralBankRate.rate)
            .where(CentralBankRate.bank == bank)
            .order_by(CentralBankRate.effective_date.desc())
            .limit(1)
        )
        result = await self._db.execute(stmt)
        row = result.scalar_one_or_none()
        rate = float(row) if row is not None else None
        self._rate_cache[currency] = rate
        return rate

    async def _get_historical_differential(
        self, base: str, quote: str, lookback_months: int
    ) -> Optional[float]:
        """Fetch rates from approximately `lookback_months` ago and compute the diff."""
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=lookback_months * 30
        )

        base_bank = _CURRENCY_TO_BANK.get(base)
        quote_bank = _CURRENCY_TO_BANK.get(quote)
        if not base_bank or not quote_bank:
            return None

        async def _fetch_at(bank: str) -> Optional[float]:
            stmt = (
                select(CentralBankRate.rate)
                .where(
                    CentralBankRate.bank == bank,
                    CentralBankRate.effective_date <= cutoff,
                )
                .order_by(CentralBankRate.effective_date.desc())
                .limit(1)
            )
            result = await self._db.execute(stmt)
            row = result.scalar_one_or_none()
            return float(row) if row is not None else None

        base_hist = await _fetch_at(base_bank)
        quote_hist = await _fetch_at(quote_bank)

        if base_hist is None or quote_hist is None:
            return None
        return base_hist - quote_hist


# ── Score mapping ──────────────────────────────────────────────────────────────

def _diff_to_score(diff: float) -> float:
    """Map an interest-rate differential (pp) to [-100, +100] score linearly.

    Breakpoints (differential → score):
      ≥+3.0 → +100    +2.0 → +80    +1.0 → +40    +0.25 → +10
      ≈0.0  →   0
      -0.25 → -10    -1.0 → -40    -2.0 → -80    ≤-3.0 → -100
    """
    if diff >= 3.0:
        return 100.0
    if diff >= 2.0:
        return 80.0 + (diff - 2.0) * 20.0          # 2.0→80, 3.0→100
    if diff >= 1.0:
        return 40.0 + (diff - 1.0) * 40.0           # 1.0→40, 2.0→80
    if diff >= 0.25:
        return 10.0 + (diff - 0.25) / 0.75 * 30.0  # 0.25→10, 1.0→40
    if diff >= -0.25:
        return diff / 0.25 * 10.0                   # linear around zero
    if diff >= -1.0:
        # -0.25→-10, -1.0→-40  (change of -30 over 0.75 pp)
        return -10.0 + (diff + 0.25) / 0.75 * 30.0
    if diff >= -2.0:
        # -1.0→-40, -2.0→-80  (change of -40 over 1 pp)
        return -40.0 + (diff + 1.0) * 40.0
    if diff >= -3.0:
        # -2.0→-80, -3.0→-100  (change of -20 over 1 pp)
        return -80.0 + (diff + 2.0) * 20.0
    return -100.0
