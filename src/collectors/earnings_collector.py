"""Earnings Calendar Collector.

Fetches upcoming earnings dates and consensus estimates from Finnhub and
Yahoo Finance, then persists them to the `economic_events` table.

Signal engine rules:
  - 2d to earnings → skip signal entirely (risk too high)
  - 3–5d to earnings → reduce score by 30% (EARNINGS_DISCOUNT_DAYS)
"""

import datetime
import logging
from decimal import Decimal
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.collectors.base import BaseCollector
from src.config import settings
from src.database.engine import async_session_factory
from src.database.models import EconomicEvent, Instrument

logger = logging.getLogger(__name__)

_FINNHUB_EARNINGS_URL = "https://finnhub.io/api/v1/calendar/earnings"
_YAHOO_EARNINGS_URL = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"


class EarningsCollector(BaseCollector):
    """Collects upcoming and past earnings events for stock instruments."""

    def __init__(self) -> None:
        super().__init__("EarningsCollector")

    async def collect(self):  # type: ignore[override]
        """Celery entry point."""
        await self._collect_all()

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    _FINNHUB_EARNINGS_URL,
                    params={"token": settings.FINNHUB_KEY, "from": "2000-01-01", "to": "2000-01-02"},
                )
                return resp.status_code in (200, 403, 401)
        except Exception:
            return False

    async def get_earnings_risk(
        self,
        ticker: str,
        as_of: Optional[datetime.datetime] = None,
    ) -> dict:
        """Return earnings risk metadata for a ticker.

        Returns::

            {
                "days_to_earnings": Optional[int],
                "skip": bool,       # True if within 2d (EARNINGS_SKIP_DAYS)
                "discount": bool,   # True if within 5d (EARNINGS_DISCOUNT_DAYS)
                "expected_move": Optional[float],  # % from options IV
                "consensus_eps": Optional[float],
            }
        """
        now = as_of or datetime.datetime.now(datetime.timezone.utc)
        next_date = await self._get_next_earnings_date(ticker)

        if next_date is None:
            return {"days_to_earnings": None, "skip": False, "discount": False,
                    "expected_move": None, "consensus_eps": None}

        days = (next_date - now.date()).days

        skip = days <= settings.EARNINGS_SKIP_DAYS
        discount = settings.EARNINGS_SKIP_DAYS < days <= settings.EARNINGS_DISCOUNT_DAYS

        return {
            "days_to_earnings": days,
            "skip": skip,
            "discount": discount,
            "expected_move": None,   # calculated separately via options IV
            "consensus_eps": None,   # fetched from Finnhub fundamentals
        }

    # ── Private ────────────────────────────────────────────────────────────────

    async def _collect_all(self) -> None:
        """Collect earnings for all active stock instruments."""
        async with async_session_factory() as session:
            result = await session.execute(
                select(Instrument).where(
                    Instrument.is_active.is_(True),
                    Instrument.market_type == "stocks",
                )
            )
            instruments = result.scalars().all()

        now = datetime.datetime.now(datetime.timezone.utc)
        date_from = now.date()
        date_to = date_from + datetime.timedelta(days=90)

        # Fetch calendar in bulk from Finnhub
        all_earnings = await self._fetch_finnhub_calendar(date_from, date_to)

        tickers = {
            inst.symbol.split("/")[0].upper(): inst.id
            for inst in instruments
        }

        async with async_session_factory() as session:
            for item in all_earnings:
                ticker = item.get("symbol", "").upper()
                inst_id = tickers.get(ticker)
                if inst_id is None:
                    continue

                date_str = item.get("date")
                if not date_str:
                    continue
                try:
                    event_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(
                        tzinfo=datetime.timezone.utc
                    )
                except ValueError:
                    continue

                eps_est = item.get("epsEstimate")

                stmt = pg_insert(EconomicEvent).values(
                    instrument_id=inst_id,
                    event_type="earnings",
                    event_date=event_date,
                    description=f"{ticker} Earnings",
                    expected_impact="high",
                    eps_estimate=Decimal(str(eps_est)) if eps_est is not None else None,
                )
                # Upsert: update estimate if the event already exists
                stmt = stmt.on_conflict_do_update(
                    index_elements=["instrument_id", "event_date", "event_type"],
                    set_={"eps_estimate": stmt.excluded.eps_estimate},
                )
                await session.execute(stmt)

            await session.commit()

        logger.info("EarningsCollector: saved %d events", len(all_earnings))

    async def _fetch_finnhub_calendar(
        self,
        date_from: datetime.date,
        date_to: datetime.date,
    ) -> list[dict]:
        """Fetch earnings calendar from Finnhub."""
        if not settings.FINNHUB_KEY:
            logger.warning("EarningsCollector: FINNHUB_KEY not set, skipping")
            return []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    _FINNHUB_EARNINGS_URL,
                    params={
                        "from": date_from.isoformat(),
                        "to": date_to.isoformat(),
                        "token": settings.FINNHUB_KEY,
                    },
                )
                resp.raise_for_status()
                return resp.json().get("earningsCalendar", [])
        except Exception as exc:
            logger.error("EarningsCollector: Finnhub fetch failed: %s", exc)
            return []

    async def _get_next_earnings_date(self, ticker: str) -> Optional[datetime.date]:
        """Return the next earnings date for a ticker from the DB or Finnhub."""
        now = datetime.datetime.now(datetime.timezone.utc)
        async with async_session_factory() as session:
            result = await session.execute(
                select(EconomicEvent)
                .join(Instrument, Instrument.id == EconomicEvent.instrument_id)
                .where(
                    Instrument.symbol.like(f"{ticker}%"),
                    EconomicEvent.event_type == "earnings",
                    EconomicEvent.event_date >= now,
                )
                .order_by(EconomicEvent.event_date)
                .limit(1)
            )
            event = result.scalar_one_or_none()

        if event is None:
            return None
        return event.event_date.date() if hasattr(event.event_date, "date") else event.event_date
