#!/usr/bin/env python3
"""Historical data backfill script for Market Analyzer Pro.

Backfills historical data from multiple external sources into the local DB.
Each source is independent and can be run separately or all at once.

Usage:
    python scripts/backfill_historical.py --source fred
    python scripts/backfill_historical.py --source fear_greed
    python scripts/backfill_historical.py --source rates
    python scripts/backfill_historical.py --source acled
    python scripts/backfill_historical.py --source coinmetrics
    python scripts/backfill_historical.py --source all
    python scripts/backfill_historical.py --source all --dry-run
"""

import argparse
import asyncio
import datetime
import logging
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Coroutine

import httpx

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.macro_collector import FRED_BASE_URL, FRED_SERIES
from src.config import settings
from src.database.crud import upsert_macro_data
from src.database.engine import async_session_factory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_FRED_BACKFILL_START = "2000-01-01"
_FRED_REQUEST_LIMIT = 10000
_FRED_RATE_LIMIT_SECONDS = 1.0  # conservative: FRED allows 120/min


async def _fetch_fred_series(
    client: httpx.AsyncClient,
    series_id: str,
    api_key: str,
    observation_start: str,
) -> list[dict[str, Any]]:
    """Fetch all observations for a single FRED series.

    Args:
        client: Shared httpx async client.
        series_id: FRED series identifier (e.g. "FEDFUNDS").
        api_key: FRED API key from settings.
        observation_start: ISO date string, e.g. "2000-01-01".

    Returns:
        List of observation dicts from the FRED API response.
    """
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": observation_start,
        "sort_order": "asc",
        "limit": _FRED_REQUEST_LIMIT,
    }
    response = await client.get(FRED_BASE_URL, params=params)
    response.raise_for_status()
    data = response.json()
    return data.get("observations", [])


def _parse_fred_observations(
    series_id: str,
    observations: list[dict[str, Any]],
    country: str,
) -> list[dict[str, Any]]:
    """Convert raw FRED observations into records ready for upsert.

    Skips observations where value == "." (FRED missing data marker).

    Args:
        series_id: FRED series identifier used as indicator_name.
        observations: Raw list from FRED API.
        country: Country code from FRED_SERIES metadata (e.g. "US").

    Returns:
        List of dicts matching MacroData table fields.
    """
    records: list[dict[str, Any]] = []
    for obs in observations:
        raw_value = obs.get("value", ".")
        if raw_value == ".":
            continue
        try:
            release_dt = datetime.datetime.strptime(
                obs["date"], "%Y-%m-%d"
            ).replace(tzinfo=datetime.timezone.utc)
            records.append({
                "indicator_name": series_id,
                "country": country,
                "value": Decimal(str(raw_value)),
                "previous_value": None,
                "forecast_value": None,
                "release_date": release_dt,
                "source": "FRED",
            })
        except (ValueError, KeyError, Exception) as exc:
            logger.warning("[FRED] Skipping observation for %s date=%s: %s", series_id, obs.get("date"), exc)
    return records


async def backfill_fred(session: AsyncSession, dry_run: bool = False) -> int:
    """Backfill FRED macro data (25-year history).

    Fetches macroeconomic indicators from the Federal Reserve Economic Data API:
    GDP, CPI, unemployment rate, federal funds rate, etc.
    Implemented in TASK-V7-06.

    Args:
        session: Async SQLAlchemy session (transaction managed here).
        dry_run: If True, log what would be inserted but skip DB writes.

    Returns:
        Total number of records upserted (0 in dry_run mode).
    """
    api_key = settings.FRED_KEY
    if not api_key:
        logger.warning("[FRED] FRED_KEY not configured — skipping backfill")
        return 0

    total_upserted = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        for series_id, meta in FRED_SERIES.items():
            country = meta.get("country", "US")
            series_name = meta.get("name", series_id)

            try:
                observations = await _fetch_fred_series(
                    client=client,
                    series_id=series_id,
                    api_key=api_key,
                    observation_start=_FRED_BACKFILL_START,
                )
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "[FRED] HTTP error fetching %s: %s %s",
                    series_id,
                    exc.response.status_code,
                    exc.response.text[:200],
                )
                await asyncio.sleep(_FRED_RATE_LIMIT_SECONDS)
                continue
            except httpx.RequestError as exc:
                logger.error("[FRED] Request error fetching %s: %s", series_id, exc)
                await asyncio.sleep(_FRED_RATE_LIMIT_SECONDS)
                continue

            records = _parse_fred_observations(series_id, observations, country)
            skipped = len(observations) - len(records)

            if dry_run:
                logger.info(
                    "[FRED] DRY-RUN %s (%s): would upsert %d records, skipped %d missing",
                    series_id,
                    series_name,
                    len(records),
                    skipped,
                )
            else:
                if records:
                    async with session.begin():
                        count = await upsert_macro_data(session, records)
                    total_upserted += count
                    logger.info(
                        "[FRED] %s (%s): upserted %d / %d observations (skipped %d missing)",
                        series_id,
                        series_name,
                        count,
                        len(observations),
                        skipped,
                    )
                else:
                    logger.info(
                        "[FRED] %s (%s): no valid observations (all %d were missing)",
                        series_id,
                        series_name,
                        len(observations),
                    )

            # Rate limit between requests
            await asyncio.sleep(_FRED_RATE_LIMIT_SECONDS)

    if not dry_run:
        logger.info("[FRED] Backfill complete. Total upserted: %d", total_upserted)
    else:
        logger.info("[FRED] DRY-RUN complete. No records written.")

    return total_upserted


async def backfill_fear_greed(session: AsyncSession, dry_run: bool = False) -> int:
    """Backfill Alternative.me Fear & Greed index since 2018.

    Fetches the daily crypto Fear & Greed index from the Alternative.me API.
    Used by the SIM-39 filter in the signal pipeline.
    Implemented in TASK-V7-07.
    """
    logger.warning("backfill_fear_greed: not yet implemented (TASK-V7-07)")
    return 0


async def backfill_rates(session: AsyncSession, dry_run: bool = False) -> int:
    """Backfill central bank interest rates history.

    Fetches historical central bank policy rates for major economies:
    Fed, ECB, BoE, BoJ, RBA, etc.
    Implemented in TASK-V7-08.
    """
    logger.warning("backfill_rates: not yet implemented (TASK-V7-08)")
    return 0


async def backfill_acled(session: AsyncSession, dry_run: bool = False) -> int:
    """Backfill ACLED geopolitical conflict events.

    Fetches historical armed conflict and geopolitical event data from the
    Armed Conflict Location & Event Data Project (ACLED) API.
    Implemented in TASK-V7-09.
    """
    logger.warning("backfill_acled: not yet implemented (TASK-V7-09)")
    return 0


async def backfill_coinmetrics(session: AsyncSession, dry_run: bool = False) -> int:
    """Backfill on-chain data from CoinMetrics since 2018.

    Fetches historical blockchain metrics: active addresses, transaction volume,
    NVT ratio, SOPR, etc.
    Implemented in TASK-V7-10.
    """
    logger.warning("backfill_coinmetrics: not yet implemented (TASK-V7-10)")
    return 0


# Registry mapping source names to their backfill functions.
# Each function has signature: async (session, dry_run) -> int
SOURCES: dict[str, Callable[..., Coroutine[object, object, int]]] = {
    "fred": backfill_fred,
    "fear_greed": backfill_fear_greed,
    "rates": backfill_rates,
    "acled": backfill_acled,
    "coinmetrics": backfill_coinmetrics,
}

ALL_SOURCES = list(SOURCES.keys())


async def main(sources: list[str], dry_run: bool = False) -> None:
    """Run backfill for the requested sources sequentially.

    Args:
        sources: List of source names to backfill (subset of SOURCES keys).
        dry_run: If True, skip actual DB writes and only log what would happen.
    """
    if dry_run:
        logger.info("DRY-RUN mode — no data will be written to the database")

    total_upserted = 0

    async with async_session_factory() as session:
        for source_name in sources:
            fn = SOURCES[source_name]
            logger.info("Starting backfill: %s", source_name)
            try:
                count = await fn(session, dry_run=dry_run)
                logger.info("Finished backfill: %s — %d records upserted", source_name, count)
                total_upserted += count
            except Exception:
                logger.exception("backfill_%s failed — continuing with next source", source_name)

    logger.info("All done. Total records upserted: %d", total_upserted)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Backfill historical data from external sources into Market Analyzer Pro DB.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join([
            "Available sources:",
            "  fred        — FRED macro indicators (25-year history)",
            "  fear_greed  — Alternative.me Fear & Greed index (since 2018)",
            "  rates       — Central bank interest rates history",
            "  acled       — ACLED geopolitical conflict events",
            "  coinmetrics — On-chain blockchain data (since 2018)",
            "  all         — Run all sources sequentially",
        ]),
    )
    parser.add_argument(
        "--source",
        required=True,
        choices=[*ALL_SOURCES, "all"],
        metavar="SOURCE",
        help="Data source to backfill. Use 'all' to run every source. "
             f"Choices: {', '.join([*ALL_SOURCES, 'all'])}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Log what would be done without writing to the database.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    requested_sources = ALL_SOURCES if args.source == "all" else [args.source]
    asyncio.run(main(sources=requested_sources, dry_run=args.dry_run))
