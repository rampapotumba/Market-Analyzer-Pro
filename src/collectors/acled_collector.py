"""ACLED (Armed Conflict Location and Event Data) collector.

Fetches geopolitical conflict events from the ACLED API and stores them
in the geo_events table for use in geopolitical risk scoring.

Requires ACLED_API_KEY and ACLED_EMAIL to be configured in settings.
Gracefully skips collection when credentials are missing.
"""

import datetime
import logging
from decimal import Decimal
from typing import Any, Optional

import httpx

from src.collectors.base import BaseCollector, CollectorResult
from src.config import settings

logger = logging.getLogger(__name__)

# ACLED API endpoint for reading conflict event records
ACLED_API_URL = "https://api.acleddata.com/acled/read"

# Minimum date for historical data backfill (per task spec)
ACLED_BACKFILL_START = datetime.date(2018, 1, 1)

# Maximum records per API page; ACLED supports up to 5000
ACLED_PAGE_SIZE = 5000

# Severity score mapping by ACLED event type (top-level category).
# Negative = destabilising event; positive = stabilising (strategic gains).
# Values reflect economic impact severity on market instruments.
ACLED_SEVERITY_MAP: dict[str, Decimal] = {
    "Battles": Decimal("-80"),
    "Violence against civilians": Decimal("-90"),
    "Explosions/Remote violence": Decimal("-70"),
    "Protests": Decimal("-30"),
    "Riots": Decimal("-50"),
    "Strategic developments": Decimal("-20"),
}

# Violent sub-types within "Protests" category raise severity
ACLED_VIOLENT_PROTEST_SUBTYPES: frozenset[str] = frozenset({
    "Violent demonstration",
    "Mob violence",
})

# Default severity when event type is unknown
ACLED_DEFAULT_SEVERITY = Decimal("-40")


def _map_severity(event_type: Optional[str], sub_event_type: Optional[str]) -> Decimal:
    """Map ACLED event type and sub-type to a numeric severity score.

    Returns a Decimal in the range [-90, +20].
    """
    if not event_type:
        return ACLED_DEFAULT_SEVERITY

    base = ACLED_SEVERITY_MAP.get(event_type, ACLED_DEFAULT_SEVERITY)

    # Violent protests are more severe than peaceful ones
    if event_type == "Protests" and sub_event_type in ACLED_VIOLENT_PROTEST_SUBTYPES:
        return Decimal("-50")

    return base


class ACLEDCollector(BaseCollector):
    """Collector for ACLED conflict event data.

    Requires ACLED_API_KEY and ACLED_EMAIL.  When not configured the
    collector logs a warning and returns an empty result — it never raises.
    """

    def __init__(self) -> None:
        super().__init__("ACLEDCollector")

    def _is_configured(self) -> bool:
        """Return True when both API key and email are set in settings."""
        return bool(settings.ACLED_API_KEY and settings.ACLED_EMAIL)

    async def _fetch_page(
        self,
        page: int,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> Optional[list[dict[str, Any]]]:
        """Fetch one page of ACLED events.  Returns list of raw event dicts or None on error."""
        params = {
            "key": settings.ACLED_API_KEY,
            "email": settings.ACLED_EMAIL,
            "event_date": f"{start_date.isoformat()}|{end_date.isoformat()}",
            "event_date_where": "BETWEEN",
            "limit": ACLED_PAGE_SIZE,
            "page": page,
            "fields": "event_date|event_type|sub_event_type|country|fatalities",
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(ACLED_API_URL, params=params)
                response.raise_for_status()
                payload = response.json()
                return payload.get("data", [])
        except httpx.HTTPStatusError as exc:
            logger.error(
                f"[ACLEDCollector] HTTP {exc.response.status_code} on page {page}: {exc}"
            )
            return None
        except Exception as exc:
            logger.error(f"[ACLEDCollector] Request failed on page {page}: {exc}")
            return None

    def _parse_event_date(self, date_str: str) -> datetime.datetime:
        """Parse ACLED date string (YYYY-MM-DD) to UTC-aware datetime."""
        try:
            d = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            return d.replace(tzinfo=datetime.timezone.utc)
        except (ValueError, TypeError):
            logger.warning(f"[ACLEDCollector] Unparseable date: {date_str!r}")
            return datetime.datetime.now(datetime.timezone.utc)

    def _build_record(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Convert a raw ACLED API event dict to a geo_events insert record."""
        event_type = raw.get("event_type") or None
        sub_event_type = raw.get("sub_event_type") or None
        country = raw.get("country") or "Unknown"
        date_str = raw.get("event_date", "")

        try:
            fatalities = int(raw.get("fatalities") or 0)
        except (ValueError, TypeError):
            fatalities = 0

        return {
            "source": "ACLED",
            "event_date": self._parse_event_date(date_str),
            "country": country[:100],
            "event_type": event_type[:100] if event_type else None,
            "fatalities": fatalities,
            "severity_score": _map_severity(event_type, sub_event_type),
            "raw_data": {
                "event_type": event_type,
                "sub_event_type": sub_event_type,
                "fatalities": fatalities,
                "country": country,
                "event_date": date_str,
            },
        }

    async def collect(self) -> CollectorResult:
        """Collect recent ACLED events (last 90 days).

        Full historical backfill is handled by backfill_acled() in scripts/.
        """
        if not self._is_configured():
            logger.warning(
                "[ACLEDCollector] ACLED_API_KEY or ACLED_EMAIL not configured — skipping"
            )
            return CollectorResult(success=True, records_count=0, data=[])

        end_date = datetime.date.today()
        start_date = end_date - datetime.timedelta(days=90)

        total = await self._collect_range(start_date, end_date, dry_run=False)
        return CollectorResult(success=True, records_count=total, data=[])

    async def _collect_range(
        self,
        start_date: datetime.date,
        end_date: datetime.date,
        dry_run: bool = False,
    ) -> int:
        """Fetch and store all ACLED events in [start_date, end_date].

        Returns the number of records stored (or that would be stored in dry_run mode).
        """
        from src.database.engine import async_session_factory

        total_stored = 0
        page = 1

        while True:
            raw_events = await self._fetch_page(page, start_date, end_date)
            if raw_events is None:
                logger.error(
                    f"[ACLEDCollector] Aborting at page {page} due to fetch error"
                )
                break
            if not raw_events:
                # No more pages
                break

            records = [self._build_record(e) for e in raw_events]

            if not dry_run:
                stored = await self._upsert_records(records, async_session_factory)
                total_stored += stored
            else:
                total_stored += len(records)

            if (total_stored % 1000) < len(records) or page == 1:
                logger.info(
                    f"[ACLEDCollector] Page {page}: {len(records)} events "
                    f"(total so far: {total_stored})"
                )

            if len(raw_events) < ACLED_PAGE_SIZE:
                # Last page — fewer results than page size means no more data
                break

            page += 1
            await self._rate_limit()

        return total_stored

    async def _upsert_records(
        self,
        records: list[dict[str, Any]],
        session_factory: Any,
    ) -> int:
        """Insert geo_event records, ignoring duplicates.

        Uses INSERT ... ON CONFLICT DO NOTHING for idempotency.
        Returns the count of rows actually inserted.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from src.database.models import GeoEvent

        if not records:
            return 0

        try:
            async with session_factory() as session:
                async with session.begin():
                    stmt = pg_insert(GeoEvent).values(records)
                    stmt = stmt.on_conflict_do_nothing()
                    result = await session.execute(stmt)
                    return result.rowcount if result.rowcount >= 0 else len(records)
        except Exception as exc:
            logger.error(f"[ACLEDCollector] DB upsert failed: {exc}")
            return 0

    async def health_check(self) -> bool:
        """Check ACLED API reachability with a minimal request."""
        if not self._is_configured():
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                params = {
                    "key": settings.ACLED_API_KEY,
                    "email": settings.ACLED_EMAIL,
                    "limit": 1,
                    "page": 1,
                    "fields": "event_date",
                }
                r = await client.get(ACLED_API_URL, params=params)
                return r.status_code == 200
        except Exception:
            return False
