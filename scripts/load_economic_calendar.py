"""
Load HIGH-impact economic calendar events for backtesting (2024-2025).

This script populates the `economic_events` table with known HIGH-impact events
that are required for the SIM-33 calendar filter to function during backtests.

Without this data the calendar filter is inert because the table is empty.

Data sources used to compile these dates:
  - FED (FOMC): federalreserve.gov/monetarypolicy/fomccalendars.htm
  - ECB: ecb.europa.eu/press/govcdec/mopo/
  - BOE: bankofengland.co.uk/monetary-policy/the-interest-rate-decisions
  - BLS (NFP): bls.gov/schedule/news_release/empsit.htm

For production use, replace this script with an API-based pipeline
(e.g., ForexFactory API, investing.com calendar, or Nasdaq Data Link).

Usage:
  python scripts/load_economic_calendar.py
  python scripts/load_economic_calendar.py --dry-run
"""

import argparse
import asyncio
import datetime
import logging
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Time constants ─────────────────────────────────────────────────────────────
# All times in UTC.
FOMC_TIME_UTC = datetime.time(19, 0)   # 14:00 ET = 19:00 UTC (NY non-DST)
FOMC_DST_TIME_UTC = datetime.time(18, 0)  # 14:00 ET = 18:00 UTC (NY DST, Mar-Nov)
NFP_TIME_UTC = datetime.time(13, 30)   # 08:30 ET = 13:30 UTC
ECB_TIME_UTC = datetime.time(13, 15)   # 14:15 CET = 13:15 UTC (non-DST)
ECB_DST_TIME_UTC = datetime.time(12, 15)  # 14:15 CEST = 12:15 UTC (DST, Mar-Oct)
BOE_TIME_UTC = datetime.time(12, 0)    # 12:00 UTC (noon London)

# NY DST periods: second Sunday of March to first Sunday of November
# CET DST periods: last Sunday of March to last Sunday of October
NY_DST_DATES_2024 = (datetime.date(2024, 3, 10), datetime.date(2024, 11, 3))
NY_DST_DATES_2025 = (datetime.date(2025, 3, 9), datetime.date(2025, 11, 2))
CET_DST_DATES_2024 = (datetime.date(2024, 3, 31), datetime.date(2024, 10, 27))
CET_DST_DATES_2025 = (datetime.date(2025, 3, 30), datetime.date(2025, 10, 26))


def _is_ny_dst(d: datetime.date) -> bool:
    """Return True if New York is on Daylight Saving Time on date d."""
    if d.year == 2024:
        return NY_DST_DATES_2024[0] <= d < NY_DST_DATES_2024[1]
    if d.year == 2025:
        return NY_DST_DATES_2025[0] <= d < NY_DST_DATES_2025[1]
    return False


def _is_cet_dst(d: datetime.date) -> bool:
    """Return True if Central European Time is on DST on date d."""
    if d.year == 2024:
        return CET_DST_DATES_2024[0] <= d < CET_DST_DATES_2024[1]
    if d.year == 2025:
        return CET_DST_DATES_2025[0] <= d < CET_DST_DATES_2025[1]
    return False


def _fomc_dt(d: datetime.date) -> datetime.datetime:
    """Return FOMC announcement datetime in UTC, accounting for NY DST."""
    t = FOMC_DST_TIME_UTC if _is_ny_dst(d) else FOMC_TIME_UTC
    return datetime.datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=datetime.timezone.utc)


def _nfp_dt(d: datetime.date) -> datetime.datetime:
    """Return NFP release datetime in UTC (always 13:30 UTC, NY always 08:30 ET)."""
    return datetime.datetime(d.year, d.month, d.day, 13, 30, tzinfo=datetime.timezone.utc)


def _ecb_dt(d: datetime.date) -> datetime.datetime:
    """Return ECB press conference datetime in UTC, accounting for CET DST."""
    t = ECB_DST_TIME_UTC if _is_cet_dst(d) else ECB_TIME_UTC
    return datetime.datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=datetime.timezone.utc)


def _boe_dt(d: datetime.date) -> datetime.datetime:
    """Return BOE MPC decision datetime in UTC (12:00 UTC)."""
    return datetime.datetime(d.year, d.month, d.day, 12, 0, tzinfo=datetime.timezone.utc)


# ── Event definitions ──────────────────────────────────────────────────────────

def _build_event_list() -> list[dict]:
    """Return the full list of HIGH-impact events for 2024-2025."""
    events: list[dict] = []

    # ── FOMC Rate Decisions (Federal Reserve) ──────────────────────────────────
    # Source: federalreserve.gov — dates are when the decision is announced (day 2 of meeting)
    fomc_dates_2024 = [
        datetime.date(2024, 1, 31),
        datetime.date(2024, 3, 20),
        datetime.date(2024, 5, 1),
        datetime.date(2024, 6, 12),
        datetime.date(2024, 7, 31),
        datetime.date(2024, 9, 18),
        datetime.date(2024, 11, 7),
        datetime.date(2024, 12, 18),
    ]
    fomc_dates_2025 = [
        datetime.date(2025, 1, 29),
        datetime.date(2025, 3, 19),
        datetime.date(2025, 5, 7),
        datetime.date(2025, 6, 18),
        datetime.date(2025, 7, 30),
        datetime.date(2025, 9, 17),
        datetime.date(2025, 10, 29),
        datetime.date(2025, 12, 17),
    ]
    for d in fomc_dates_2024 + fomc_dates_2025:
        events.append({
            "event_date": _fomc_dt(d),
            "country": "US",
            "currency": "USD",
            "event_name": "FOMC Rate Decision",
            "impact": "high",
            "source": "FED",
        })

    # ── NFP — Non-Farm Payrolls (Bureau of Labor Statistics) ──────────────────
    # Released first Friday of each month for the prior month.
    # Source: bls.gov/schedule/news_release/empsit.htm
    nfp_dates_2024 = [
        datetime.date(2024, 1, 5),
        datetime.date(2024, 2, 2),
        datetime.date(2024, 3, 8),
        datetime.date(2024, 4, 5),
        datetime.date(2024, 5, 3),
        datetime.date(2024, 6, 7),
        datetime.date(2024, 7, 5),
        datetime.date(2024, 8, 2),
        datetime.date(2024, 9, 6),
        datetime.date(2024, 10, 4),
        datetime.date(2024, 11, 1),
        datetime.date(2024, 12, 6),
    ]
    nfp_dates_2025 = [
        datetime.date(2025, 1, 10),
        datetime.date(2025, 2, 7),
        datetime.date(2025, 3, 7),
        datetime.date(2025, 4, 4),
        datetime.date(2025, 5, 2),
        datetime.date(2025, 6, 6),
        datetime.date(2025, 7, 3),  # July 4 holiday → moved to July 3 if needed (using actual schedule)
        datetime.date(2025, 8, 1),
        datetime.date(2025, 9, 5),
        datetime.date(2025, 10, 3),
        datetime.date(2025, 11, 7),
        datetime.date(2025, 12, 5),
    ]
    for d in nfp_dates_2024 + nfp_dates_2025:
        events.append({
            "event_date": _nfp_dt(d),
            "country": "US",
            "currency": "USD",
            "event_name": "Non-Farm Payrolls (NFP)",
            "impact": "high",
            "source": "BLS",
        })

    # ── ECB Rate Decisions (European Central Bank) ─────────────────────────────
    # Source: ecb.europa.eu — Governing Council monetary policy meetings
    # ECB meets every 6 weeks; press conference starts at 14:15 CET/CEST.
    ecb_dates_2024 = [
        datetime.date(2024, 1, 25),
        datetime.date(2024, 3, 7),
        datetime.date(2024, 4, 11),
        datetime.date(2024, 6, 6),
        datetime.date(2024, 7, 18),
        datetime.date(2024, 9, 12),
        datetime.date(2024, 10, 17),
        datetime.date(2024, 12, 12),
    ]
    ecb_dates_2025 = [
        datetime.date(2025, 1, 30),
        datetime.date(2025, 3, 6),
        datetime.date(2025, 4, 17),
        datetime.date(2025, 6, 5),
        datetime.date(2025, 7, 24),
        datetime.date(2025, 9, 11),
        datetime.date(2025, 10, 30),
        datetime.date(2025, 12, 18),
    ]
    for d in ecb_dates_2024 + ecb_dates_2025:
        events.append({
            "event_date": _ecb_dt(d),
            "country": "EU",
            "currency": "EUR",
            "event_name": "ECB Rate Decision",
            "impact": "high",
            "source": "ECB",
        })

    # ── BOE Rate Decisions (Bank of England MPC) ───────────────────────────────
    # Source: bankofengland.co.uk — MPC decisions released at 12:00 UTC
    boe_dates_2024 = [
        datetime.date(2024, 2, 1),
        datetime.date(2024, 3, 21),
        datetime.date(2024, 5, 9),
        datetime.date(2024, 6, 20),
        datetime.date(2024, 8, 1),
        datetime.date(2024, 9, 19),
        datetime.date(2024, 11, 7),
        datetime.date(2024, 12, 19),
    ]
    boe_dates_2025 = [
        datetime.date(2025, 2, 6),
        datetime.date(2025, 3, 20),
        datetime.date(2025, 5, 8),
        datetime.date(2025, 6, 19),
        datetime.date(2025, 8, 7),
        datetime.date(2025, 9, 18),
        datetime.date(2025, 11, 6),
        datetime.date(2025, 12, 18),
    ]
    for d in boe_dates_2024 + boe_dates_2025:
        events.append({
            "event_date": _boe_dt(d),
            "country": "GB",
            "currency": "GBP",
            "event_name": "BOE Rate Decision (MPC)",
            "impact": "high",
            "source": "BOE",
        })

    # ── US CPI releases ────────────────────────────────────────────────────────
    # Released mid-month, typically 13:30 UTC. Second or third Wednesday/Thursday.
    # Source: bls.gov/schedule/news_release/cpi.htm
    us_cpi_dates_2024 = [
        datetime.date(2024, 1, 11),
        datetime.date(2024, 2, 13),
        datetime.date(2024, 3, 12),
        datetime.date(2024, 4, 10),
        datetime.date(2024, 5, 15),
        datetime.date(2024, 6, 12),
        datetime.date(2024, 7, 11),
        datetime.date(2024, 8, 14),
        datetime.date(2024, 9, 11),
        datetime.date(2024, 10, 10),
        datetime.date(2024, 11, 13),
        datetime.date(2024, 12, 11),
    ]
    us_cpi_dates_2025 = [
        datetime.date(2025, 1, 15),
        datetime.date(2025, 2, 12),
        datetime.date(2025, 3, 12),
        datetime.date(2025, 4, 10),
        datetime.date(2025, 5, 13),
        datetime.date(2025, 6, 11),
        datetime.date(2025, 7, 15),
        datetime.date(2025, 8, 13),
        datetime.date(2025, 9, 10),
        datetime.date(2025, 10, 14),
        datetime.date(2025, 11, 12),
        datetime.date(2025, 12, 10),
    ]
    for d in us_cpi_dates_2024 + us_cpi_dates_2025:
        events.append({
            "event_date": datetime.datetime(
                d.year, d.month, d.day, 13, 30, tzinfo=datetime.timezone.utc
            ),
            "country": "US",
            "currency": "USD",
            "event_name": "US CPI (Consumer Price Index)",
            "impact": "high",
            "source": "BLS",
        })

    return events


# ── Database insertion ─────────────────────────────────────────────────────────

async def load_events(dry_run: bool = False) -> None:
    """Insert all events into the economic_events table."""
    events = _build_event_list()

    logger.info("Total events to load: %d", len(events))

    if dry_run:
        logger.info("[DRY RUN] No database writes will be performed.")
        for ev in events:
            logger.info(
                "[DRY RUN] %s | %s | %s | %s | %s",
                ev["event_date"].strftime("%Y-%m-%d %H:%M UTC"),
                ev["country"],
                ev["currency"],
                ev["impact"].upper(),
                ev["event_name"],
            )
        logger.info("[DRY RUN] Total: %d events", len(events))
        return

    from src.database.crud import upsert_economic_event
    from src.database.engine import async_session_factory

    inserted = 0
    failed = 0

    async with async_session_factory() as session:
        async with session.begin():
            for ev in events:
                try:
                    await upsert_economic_event(session, ev)
                    inserted += 1
                except Exception as exc:
                    logger.error(
                        "Failed to insert event %s / %s: %s",
                        ev["event_name"],
                        ev["event_date"],
                        exc,
                    )
                    failed += 1

    logger.info("Done. Inserted/updated: %d, failed: %d", inserted, failed)


# ── Entry point ────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load HIGH-impact economic calendar events for 2024-2025 into the database."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print events to stdout without writing to the database.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(load_events(dry_run=args.dry_run))
