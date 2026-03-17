"""Economic calendar collector using ForexFactory XML feed.

Free, no API key required. Covers current week events with
impact level (High/Medium/Low), forecast, and previous values.

Source: https://nfs.faireconomy.media/ff_calendar_thisweek.xml
Times are in US Eastern Time (EST/EDT auto-detected by date).
"""

import datetime
import json
import logging
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

from src.collectors.base import BaseCollector, CollectorResult

logger = logging.getLogger(__name__)

FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
ET_ZONE = ZoneInfo("America/New_York")

# Currency → instrument symbols
CURRENCY_TO_INSTRUMENTS: dict[str, list[str]] = {
    "USD": ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "SPY", "BTC/USDT", "ETH/USDT"],
    "EUR": ["EURUSD=X"],
    "GBP": ["GBPUSD=X"],
    "JPY": ["USDJPY=X"],
    "CAD": [],
    "AUD": [],
    "NZD": [],
    "CHF": ["EURUSD=X"],
    "CNY": ["BTC/USDT"],
}

IMPACT_MAP = {"High": "high", "Medium": "medium", "Low": "low"}


def _resolve_instruments(currency: str) -> list[str]:
    return sorted(CURRENCY_TO_INSTRUMENTS.get(currency.upper(), []))


def _parse_ff_datetime(date_str: str, time_str: str) -> Optional[datetime.datetime]:
    """
    Parse ForexFactory date + time strings to UTC datetime.
    date_str: "03-18-2026"
    time_str: "6:00pm" | "12:30am" | "All Day" | ""
    Times are in US Eastern Time.
    """
    try:
        date = datetime.datetime.strptime(date_str.strip(), "%m-%d-%Y")
    except ValueError:
        return None

    time_str = time_str.strip()
    if not time_str or time_str.lower() in ("all day", "tentative"):
        # Treat as midnight ET
        local_dt = datetime.datetime(date.year, date.month, date.day, 0, 0, tzinfo=ET_ZONE)
        return local_dt.astimezone(datetime.timezone.utc)

    try:
        t = datetime.datetime.strptime(time_str, "%I:%M%p")
    except ValueError:
        try:
            t = datetime.datetime.strptime(time_str, "%I%p")
        except ValueError:
            return None

    local_dt = datetime.datetime(
        date.year, date.month, date.day,
        t.hour, t.minute,
        tzinfo=ET_ZONE,
    )
    return local_dt.astimezone(datetime.timezone.utc)


def _parse_decimal(value: str) -> Optional[Decimal]:
    if not value or not value.strip():
        return None
    # Strip % and K/M suffixes for storage (keep raw number)
    cleaned = value.strip().rstrip("%KMBm").replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _parse_unit(value: str) -> Optional[str]:
    """Extract unit indicator from forecast/previous string."""
    if not value:
        return None
    v = value.strip()
    if v.endswith("%"):
        return "%"
    if v.endswith("K"):
        return "K"
    if v.endswith("M"):
        return "M"
    if v.endswith("B"):
        return "B"
    return None


class FMPCalendarCollector(BaseCollector):
    """
    Fetches current-week economic calendar from ForexFactory XML feed.
    No API key required. Maps currency codes to instrument symbols.
    Aliased as FMPCalendarCollector to maintain import compatibility.
    """

    def __init__(self) -> None:
        super().__init__("FFCalendar")

    async def _fetch_xml(self) -> Optional[str]:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                FF_CALENDAR_URL,
                headers={"User-Agent": "Mozilla/5.0 (compatible; MarketAnalyzerBot/1.0)"},
            )
            if resp.status_code != 200:
                logger.warning(f"[FFCalendar] HTTP {resp.status_code}")
                return None
            return resp.content.decode("windows-1252", errors="replace")

    async def collect(self) -> CollectorResult:
        xml_content = await self._fetch_xml()
        if not xml_content:
            return CollectorResult(success=False, error="Failed to fetch calendar XML")

        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as exc:
            logger.error(f"[FFCalendar] XML parse error: {exc}")
            return CollectorResult(success=False, error=str(exc))

        now = datetime.datetime.now(datetime.timezone.utc)
        raw_events = root.findall("event")
        logger.info(f"[FFCalendar] Parsed {len(raw_events)} events from XML")

        from src.database.crud import delete_old_economic_events, upsert_economic_event
        from src.database.engine import async_session_factory

        saved = 0
        async with async_session_factory() as session:
            async with session.begin():
                await delete_old_economic_events(session)

                for ev in raw_events:
                    try:
                        currency = (ev.findtext("country") or "").strip().upper()
                        event_name = (ev.findtext("title") or "").strip()
                        date_str = (ev.findtext("date") or "").strip()
                        time_str = (ev.findtext("time") or "").strip()
                        impact_raw = (ev.findtext("impact") or "Low").strip()
                        forecast_raw = (ev.findtext("forecast") or "").strip()
                        previous_raw = (ev.findtext("previous") or "").strip()

                        if not event_name or not date_str:
                            continue

                        event_date = _parse_ff_datetime(date_str, time_str)
                        if not event_date or event_date <= now:
                            continue

                        instruments = _resolve_instruments(currency)
                        if not instruments:
                            continue  # Skip currencies with no mapped instruments

                        impact = IMPACT_MAP.get(impact_raw, "low")
                        unit = _parse_unit(forecast_raw) or _parse_unit(previous_raw)

                        await upsert_economic_event(session, {
                            "event_date": event_date,
                            "country": currency[:8],    # FF uses currency as country field
                            "currency": currency[:8],
                            "event_name": event_name[:256],
                            "impact": impact,
                            "previous": _parse_decimal(previous_raw),
                            "estimate": _parse_decimal(forecast_raw),
                            "actual": None,             # FF XML doesn't include actual in weekly feed
                            "unit": unit,
                            "related_instruments": json.dumps(instruments),
                            "source": "ForexFactory",
                        })
                        saved += 1
                    except Exception as exc:
                        logger.debug(f"[FFCalendar] Failed to save event: {exc}")

        logger.info(f"[FFCalendar] Saved {saved} upcoming events for this week")
        return CollectorResult(success=True, records_count=saved)

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(FF_CALENDAR_URL, headers={"User-Agent": "Mozilla/5.0"})
                return r.status_code == 200
        except Exception:
            return False
