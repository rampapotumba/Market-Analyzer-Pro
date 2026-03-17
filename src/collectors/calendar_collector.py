"""Economic calendar collector: Finnhub Calendar API."""

import asyncio
import datetime
import logging
from typing import Any, Optional

import httpx

from src.collectors.base import BaseCollector, CollectorResult
from src.config import settings

logger = logging.getLogger(__name__)

FINNHUB_BASE_URL = "https://finnhub.io/api/v1"


class FinnhubCalendarCollector(BaseCollector):
    """Collects economic calendar events from Finnhub."""

    def __init__(self) -> None:
        super().__init__("FinnhubCalendar")
        self.api_key = settings.FINNHUB_KEY

    async def _fetch_economic_calendar(
        self,
        from_date: str,
        to_date: str,
    ) -> dict[str, Any]:
        if not self.api_key:
            return {"economicCalendar": []}

        params = {
            "from": from_date,
            "to": to_date,
            "token": self.api_key,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{FINNHUB_BASE_URL}/calendar/economic", params=params
            )
            if response.status_code == 403:
                logger.debug("[FinnhubCalendar] Economic calendar requires paid plan — skipping")
                return {"economicCalendar": []}
            response.raise_for_status()
            return response.json()

    async def get_upcoming_events(
        self,
        days_ahead: int = 7,
    ) -> list[dict[str, Any]]:
        """Return upcoming economic events for the next N days."""
        if not self.api_key:
            logger.info("[FinnhubCalendar] No API key, returning empty calendar")
            return []

        today = datetime.date.today()
        from_date = today.strftime("%Y-%m-%d")
        to_date = (today + datetime.timedelta(days=days_ahead)).strftime("%Y-%m-%d")

        try:
            data = await self._with_retry(
                self._fetch_economic_calendar, from_date, to_date
            )
            events = data.get("economicCalendar", [])
            return events
        except Exception as exc:
            logger.error(f"[FinnhubCalendar] Error fetching calendar: {exc}")
            return []

    async def collect(self) -> CollectorResult:
        """Collect upcoming economic events."""
        events = await self.get_upcoming_events(days_ahead=7)
        return CollectorResult(
            success=True,
            data=events,
            records_count=len(events),
        )

    async def health_check(self) -> bool:
        if not self.api_key:
            return True  # Gracefully degraded
        try:
            today = datetime.date.today().strftime("%Y-%m-%d")
            data = await self._fetch_economic_calendar(today, today)
            return "economicCalendar" in data
        except Exception:
            return False
