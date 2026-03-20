"""
Load HIGH-impact economic calendar events from Financial Modeling Prep (FMP) API.

FMP endpoint:
  GET https://financialmodelingprep.com/api/v3/economic_calendar
      ?from=YYYY-MM-DD&to=YYYY-MM-DD&apikey=<key>

Free tier restricts the date range per request; the script splits the full
2024-2025 period into 6-month chunks and fetches them sequentially.

Only events with impact == "High" (case-insensitive) are stored.
Additionally, events are filtered to the key event types we track:
  FOMC, NFP, CPI, ECB rate decision, BOE rate decision.

The FMP_API_KEY must be set in the .env file or as an environment variable.

Usage:
    python scripts/load_economic_calendar_fmp.py
    python scripts/load_economic_calendar_fmp.py --dry-run
    python scripts/load_economic_calendar_fmp.py --from 2024-01-01 --to 2024-06-30
"""

import argparse
import asyncio
import datetime
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

import httpx

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Load .env before importing src modules
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

# FMP changed /api/v3/economic_calendar to a legacy-only endpoint after Aug 2025.
# The stable API uses /stable/economic-calendar.
# The script tries the stable endpoint first; on 403/subscription error it falls
# back to the legacy v3 URL so that users with legacy subscriptions still work.
FMP_STABLE_URL = "https://financialmodelingprep.com/stable/economic-calendar"
FMP_LEGACY_URL = "https://financialmodelingprep.com/api/v3/economic_calendar"
FMP_BASE_URL = FMP_STABLE_URL  # primary — override via --legacy flag if needed

# Date range chunks (6 months each — safe for FMP free tier)
DEFAULT_DATE_CHUNKS: list[tuple[str, str]] = [
    ("2024-01-01", "2024-06-30"),
    ("2024-07-01", "2024-12-31"),
    ("2025-01-01", "2025-06-30"),
    ("2025-07-01", "2025-12-31"),
]

# FMP uses "High" / "Medium" / "Low" for impact field
HIGH_IMPACT_VALUES = {"high"}

# Substring patterns (lower-case) to match events we care about.
# Any event whose name contains at least one of these keywords is accepted.
KEY_EVENT_KEYWORDS = [
    "fomc",
    "federal funds",
    "interest rate decision",      # generic rate decision
    "nonfarm payroll",
    "non-farm payroll",
    "nfp",
    "cpi",
    "consumer price index",
    "ecb",
    "european central bank",
    "bank of england",
    "boe",
]

# Map FMP country codes / currencies to our DB values
COUNTRY_MAP: dict[str, str] = {
    "US": "US",
    "EU": "EU",
    "GB": "GB",
    "JP": "JP",
    "CA": "CA",
    "AU": "AU",
    "NZ": "NZ",
    "CH": "CH",
}

# ── FMP fetching ───────────────────────────────────────────────────────────────


def _is_key_event(event_name: str) -> bool:
    """Return True if the event name matches one of our tracked event types."""
    name_lower = event_name.lower()
    return any(kw in name_lower for kw in KEY_EVENT_KEYWORDS)


def _parse_fmp_datetime(date_str: str) -> Optional[datetime.datetime]:
    """Parse FMP date string to UTC-aware datetime.

    FMP returns dates in ISO format, sometimes with and sometimes without time:
      "2024-01-31" or "2024-01-31 19:00:00"
    """
    if not date_str:
        return None
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            dt = datetime.datetime.strptime(date_str, fmt)
            return dt.replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            continue
    return None


def _fmp_item_to_db_record(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Convert a single FMP calendar entry to a dict ready for upsert_economic_event.

    Returns None if the item should be skipped (wrong impact, missing fields, etc.).
    """
    # Impact filter
    impact_raw = str(item.get("impact") or "").strip().lower()
    if impact_raw not in HIGH_IMPACT_VALUES:
        return None

    event_name: str = str(item.get("event") or "").strip()
    if not event_name:
        return None

    # Key-event filter
    if not _is_key_event(event_name):
        return None

    date_str: str = str(item.get("date") or "").strip()
    event_date = _parse_fmp_datetime(date_str)
    if event_date is None:
        logger.warning("[FMP] Cannot parse date %r for event %r — skipped", date_str, event_name)
        return None

    country: str = COUNTRY_MAP.get(
        str(item.get("country") or "").strip().upper(), ""
    ) or str(item.get("country") or "").strip().upper()

    currency: str = str(item.get("currency") or "").strip().upper()
    if not currency and country == "US":
        currency = "USD"
    elif not currency and country == "EU":
        currency = "EUR"
    elif not currency and country == "GB":
        currency = "GBP"

    def _to_decimal_or_none(val: Any):
        if val is None or val == "" or val == "None":
            return None
        try:
            from decimal import Decimal
            return Decimal(str(val))
        except Exception:
            return None

    return {
        "event_date": event_date,
        "country": country,
        "currency": currency,
        "event_name": event_name,
        "impact": "high",
        "previous": _to_decimal_or_none(item.get("previous")),
        "estimate": _to_decimal_or_none(item.get("estimate")),
        "actual": _to_decimal_or_none(item.get("actual")),
        "unit": str(item.get("unit") or "").strip() or None,
        "source": "FMP",
    }


_SUBSCRIPTION_ERROR_PHRASES = (
    "restricted endpoint",
    "not available under your current subscription",
    "legacy endpoint",
    "only available for legacy",
)


def _is_subscription_error(text: str) -> bool:
    """Return True if the response body indicates a subscription restriction."""
    lower = text.lower()
    return any(phrase in lower for phrase in _SUBSCRIPTION_ERROR_PHRASES)


async def _fetch_chunk(
    client: httpx.AsyncClient,
    api_key: str,
    date_from: str,
    date_to: str,
    base_url: str = FMP_BASE_URL,
) -> list[dict[str, Any]]:
    """Fetch one date-range chunk from FMP API. Returns raw JSON list.

    Tries the stable endpoint first; if that returns a subscription error,
    falls back to the legacy v3 URL (for users with legacy accounts).
    """
    params = {
        "from": date_from,
        "to": date_to,
        "apikey": api_key,
    }
    urls_to_try = [base_url]
    if base_url == FMP_STABLE_URL:
        urls_to_try.append(FMP_LEGACY_URL)
    elif base_url == FMP_LEGACY_URL:
        urls_to_try.append(FMP_STABLE_URL)

    for url in urls_to_try:
        try:
            resp = await client.get(url, params=params, timeout=30.0)

            if resp.status_code == 401:
                raise RuntimeError("FMP API key is invalid or expired (HTTP 401)")

            if resp.status_code == 429:
                logger.warning("[FMP] Rate limit hit for chunk %s → %s — waiting 60s", date_from, date_to)
                await asyncio.sleep(60.0)
                resp = await client.get(url, params=params, timeout=30.0)

            if resp.status_code == 403 or _is_subscription_error(resp.text):
                logger.warning(
                    "[FMP] Endpoint %s not available for this subscription — trying next URL", url
                )
                continue

            resp.raise_for_status()
            data = resp.json()

            if isinstance(data, dict):
                if "Error Message" in data or _is_subscription_error(str(data)):
                    logger.warning("[FMP] API error from %s: %s — trying next URL", url, data)
                    continue
                # Unexpected dict response that is not an error
                return []

            return data if isinstance(data, list) else []

        except RuntimeError:
            raise
        except Exception as exc:
            logger.error("[FMP] HTTP error for chunk %s → %s at %s: %s", date_from, date_to, url, exc)

    logger.error(
        "[FMP] All FMP endpoints failed for chunk %s → %s. "
        "The /api/v3/economic_calendar endpoint requires a paid FMP subscription "
        "(plan with economic calendar access). "
        "The existing manual calendar (scripts/load_economic_calendar.py) covers 2024-2025 "
        "and can be used as a fallback.",
        date_from,
        date_to,
    )
    return []


# ── Main loader ────────────────────────────────────────────────────────────────


async def load_fmp_calendar(
    api_key: str,
    date_chunks: list[tuple[str, str]],
    dry_run: bool = False,
    base_url: str = FMP_BASE_URL,
) -> None:
    """Fetch FMP economic calendar and store HIGH-impact key events to DB."""
    all_records: list[dict[str, Any]] = []

    async with httpx.AsyncClient() as client:
        for date_from, date_to in date_chunks:
            logger.info("[FMP] Fetching %s → %s ...", date_from, date_to)
            raw_items = await _fetch_chunk(client, api_key, date_from, date_to, base_url=base_url)
            logger.info("[FMP] Received %d raw events for chunk %s → %s", len(raw_items), date_from, date_to)

            chunk_accepted = 0
            for item in raw_items:
                record = _fmp_item_to_db_record(item)
                if record:
                    all_records.append(record)
                    chunk_accepted += 1

            logger.info("[FMP] Accepted %d HIGH-impact key events from chunk %s → %s", chunk_accepted, date_from, date_to)

            # Brief pause to avoid hammering the free-tier rate limit
            await asyncio.sleep(1.0)

    logger.info("[FMP] Total events accepted: %d", len(all_records))

    if not all_records:
        logger.warning("[FMP] No events to store — check API key and date ranges")
        return

    if dry_run:
        logger.info("[DRY RUN] Would store %d events:", len(all_records))
        for rec in sorted(all_records, key=lambda r: r["event_date"]):
            logger.info(
                "[DRY RUN]  %s | %-4s | %s",
                rec["event_date"].strftime("%Y-%m-%d %H:%M UTC"),
                rec.get("country", ""),
                rec["event_name"],
            )
        return

    from src.database.crud import upsert_economic_event
    from src.database.engine import async_session_factory

    inserted = 0
    failed = 0

    async with async_session_factory() as session:
        async with session.begin():
            for rec in all_records:
                try:
                    await upsert_economic_event(session, rec)
                    inserted += 1
                except Exception as exc:
                    logger.error(
                        "[FMP] Failed to store event %r (%s): %s",
                        rec.get("event_name"),
                        rec.get("event_date"),
                        exc,
                    )
                    failed += 1

    logger.info("[FMP] Done. Stored/updated: %d, failed: %d", inserted, failed)


# ── Entry point ────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load HIGH-impact economic calendar from FMP API into the database."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print accepted events without writing to the database.",
    )
    parser.add_argument(
        "--from",
        dest="date_from",
        default=None,
        help="Start date YYYY-MM-DD (overrides default chunks; requires --to as well).",
    )
    parser.add_argument(
        "--to",
        dest="date_to",
        default=None,
        help="End date YYYY-MM-DD (overrides default chunks; requires --from as well).",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="FMP API key (overrides FMP_API_KEY env variable).",
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="Use legacy /api/v3/economic_calendar endpoint (for accounts created before Aug 2025).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    api_key: str = args.api_key or os.environ.get("FMP_API_KEY", "")
    if not api_key:
        logger.error(
            "FMP API key not found. Set FMP_API_KEY in .env or pass --api-key."
        )
        sys.exit(1)

    if args.date_from and args.date_to:
        chunks: list[tuple[str, str]] = [(args.date_from, args.date_to)]
    elif args.date_from or args.date_to:
        logger.error("Both --from and --to must be provided together.")
        sys.exit(1)
    else:
        chunks = DEFAULT_DATE_CHUNKS

    endpoint_url = FMP_LEGACY_URL if args.legacy else FMP_STABLE_URL
    asyncio.run(load_fmp_calendar(api_key, chunks, dry_run=args.dry_run, base_url=endpoint_url))
