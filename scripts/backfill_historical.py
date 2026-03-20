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
import logging
import sys
from pathlib import Path
from typing import Callable, Coroutine

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.engine import async_session_factory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


async def backfill_fred(session: AsyncSession, dry_run: bool = False) -> int:
    """Backfill FRED macro data (25-year history).

    Fetches macroeconomic indicators from the Federal Reserve Economic Data API:
    GDP, CPI, unemployment rate, federal funds rate, etc.
    Implemented in TASK-V7-06.
    """
    logger.warning("backfill_fred: not yet implemented (TASK-V7-06)")
    return 0


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
