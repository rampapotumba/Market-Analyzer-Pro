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
    python scripts/backfill_historical.py --source coinmetrics --start 2020-01-01
"""

import argparse
import asyncio
import datetime
import logging
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import httpx

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Shared constants ──────────────────────────────────────────────────────────

_BACKFILL_START_DEFAULT = "2018-01-01"
_BACKFILL_END_DEFAULT = datetime.date.today().isoformat()

# Summary counters — populated by fear_greed backfill (used by tests)
SUMMARY: dict[str, dict[str, Any]] = {}


# ── FRED backfill (TASK-V7-06) ────────────────────────────────────────────────

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
    from src.collectors.macro_collector import FRED_BASE_URL  # noqa: PLC0415

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
            logger.warning(
                "[FRED] Skipping observation for %s date=%s: %s",
                series_id,
                obs.get("date"),
                exc,
            )
    return records


async def backfill_fred(
    start: str = _FRED_BACKFILL_START,
    end: str = _BACKFILL_END_DEFAULT,
    dry_run: bool = False,
) -> int:
    """Backfill FRED macro data (25-year history).

    Fetches macroeconomic indicators from the Federal Reserve Economic Data API:
    GDP, CPI, unemployment rate, federal funds rate, etc.
    Implemented in TASK-V7-06.

    Args:
        start: ISO date string for the start of the backfill (default 2000-01-01).
        end: ISO date string for the end of the backfill (default today).
        dry_run: If True, log what would be inserted but skip DB writes.

    Returns:
        Total number of records upserted (0 in dry_run mode).
    """
    from src.collectors.macro_collector import FRED_SERIES  # noqa: PLC0415
    from src.config import settings  # noqa: PLC0415
    from src.database.crud import upsert_macro_data  # noqa: PLC0415
    from src.database.engine import async_session_factory  # noqa: PLC0415

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
                    observation_start=start,
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
                    async with async_session_factory() as session:
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


# ── Fear & Greed backfill (TASK-V7-07) ───────────────────────────────────────

FEAR_GREED_FULL_HISTORY_URL = "https://api.alternative.me/fng/?limit=0&format=json"

FEAR_GREED_INDICATOR_NAME = "FEAR_GREED"
FEAR_GREED_COUNTRY = "GLOBAL"
FEAR_GREED_SOURCE = "alternative.me"

# Valid range per API spec: 0–100
FEAR_GREED_MIN_VALUE = 0
FEAR_GREED_MAX_VALUE = 100

# Log progress every N records parsed
_FG_LOG_PROGRESS_EVERY = 500


async def backfill_fear_greed(
    start: str = "2018-01-01",
    end: str = _BACKFILL_END_DEFAULT,
    dry_run: bool = False,
) -> int:
    """Fetch complete Fear & Greed Index history and upsert into macro_data.

    A single API call to alternative.me with limit=0 returns all available
    daily data points (~3000 records) since February 2018.

    Each record is stored as:
        indicator_name = "FEAR_GREED"
        country        = "GLOBAL"
        source         = "alternative.me"
        release_date   = midnight UTC of the recorded day
        value          = integer 0..100

    Operation is idempotent: ON CONFLICT DO NOTHING prevents duplicate rows.
    The ``start`` and ``end`` parameters are accepted for API consistency but
    the Alternative.me API always returns the full history in one call — date
    filtering is not supported server-side.

    Args:
        start: Unused (API returns full history); kept for interface consistency.
        end: Unused (API returns full history); kept for interface consistency.
        dry_run: When True, parse and validate data but do not write to DB.

    Returns:
        Number of records inserted (0 when dry_run=True).
    """
    logger.info("[FearGreed] Starting full-history backfill (dry_run=%s)", dry_run)
    logger.info("[FearGreed] Fetching from %s ...", FEAR_GREED_FULL_HISTORY_URL)

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(FEAR_GREED_FULL_HISTORY_URL)
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("[FearGreed] HTTP error %d: %s", exc.response.status_code, exc)
        SUMMARY["fear_greed"] = {"fetched": 0, "stored": 0, "error": str(exc)}
        return 0
    except Exception as exc:
        logger.error("[FearGreed] Request failed: %s", exc)
        SUMMARY["fear_greed"] = {"fetched": 0, "stored": 0, "error": str(exc)}
        return 0

    raw_entries: list[dict] = payload.get("data", [])
    if not raw_entries:
        logger.warning("[FearGreed] API returned empty data array")
        SUMMARY["fear_greed"] = {"fetched": 0, "stored": 0}
        return 0

    logger.info("[FearGreed] Received %d raw entries from API", len(raw_entries))

    records: list[dict[str, Any]] = []
    skipped_invalid = 0

    for idx, item in enumerate(raw_entries):
        try:
            raw_value = int(item["value"])
            unix_ts = int(item["timestamp"])
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("[FearGreed] Skipping entry %d — parse error: %s", idx, exc)
            skipped_invalid += 1
            continue

        # Validate value is in the documented 0–100 range
        if not (FEAR_GREED_MIN_VALUE <= raw_value <= FEAR_GREED_MAX_VALUE):
            logger.warning(
                "[FearGreed] Skipping entry %d — value %d outside valid range [%d, %d]",
                idx,
                raw_value,
                FEAR_GREED_MIN_VALUE,
                FEAR_GREED_MAX_VALUE,
            )
            skipped_invalid += 1
            continue

        # Convert Unix timestamp → midnight UTC datetime (normalise to day boundary)
        ts = datetime.datetime.fromtimestamp(unix_ts, tz=datetime.timezone.utc)
        ts = ts.replace(hour=0, minute=0, second=0, microsecond=0)

        records.append(
            {
                "indicator_name": FEAR_GREED_INDICATOR_NAME,
                "country": FEAR_GREED_COUNTRY,
                "value": Decimal(str(raw_value)),
                "release_date": ts,
                "source": FEAR_GREED_SOURCE,
            }
        )

        # Log progress every _FG_LOG_PROGRESS_EVERY parsed records
        if len(records) % _FG_LOG_PROGRESS_EVERY == 0:
            logger.info(
                "[FearGreed] Parsed %d/%d records ...",
                len(records),
                len(raw_entries),
            )

    logger.info(
        "[FearGreed] Parsed %d valid records, %d skipped (invalid)",
        len(records),
        skipped_invalid,
    )

    if not records:
        logger.warning("[FearGreed] No valid records to store")
        SUMMARY["fear_greed"] = {"fetched": 0, "stored": 0, "skipped": skipped_invalid}
        return 0

    # Log date range of data received
    dates = [r["release_date"] for r in records]
    earliest = min(dates)
    latest = max(dates)
    logger.info(
        "[FearGreed] Date range: %s → %s",
        earliest.date(),
        latest.date(),
    )

    if dry_run:
        logger.info(
            "[FearGreed] DRY RUN — would insert %d records (skipped %d), NOT writing to DB",
            len(records),
            skipped_invalid,
        )
        SUMMARY["fear_greed"] = {
            "fetched": len(records),
            "stored": 0,
            "skipped": skipped_invalid,
            "dry_run": True,
            "date_range": f"{earliest.date()} → {latest.date()}",
        }
        return 0

    # Write to database
    from src.database.crud import upsert_macro_data  # noqa: PLC0415
    from src.database.engine import async_session_factory  # noqa: PLC0415

    stored = 0
    try:
        # Chunk to avoid PostgreSQL 32767-parameter limit
        chunk_size = 3000
        for i in range(0, len(records), chunk_size):
            chunk = records[i : i + chunk_size]
            async with async_session_factory() as db:
                async with db.begin():
                    chunk_stored = await upsert_macro_data(db, chunk)
                    stored += chunk_stored
            logger.info(
                "[FearGreed] Stored chunk %d/%d — %d new rows",
                min(i + chunk_size, len(records)),
                len(records),
                chunk_stored,
            )
    except Exception as exc:
        logger.error("[FearGreed] DB error: %s", exc)
        SUMMARY["fear_greed"] = {
            "fetched": len(records),
            "stored": stored,
            "skipped": skipped_invalid,
            "error": str(exc),
        }
        return stored

    logger.info(
        "[FearGreed] Complete: fetched=%d stored=%d skipped=%d range=%s → %s",
        len(records),
        stored,
        skipped_invalid,
        earliest.date(),
        latest.date(),
    )
    SUMMARY["fear_greed"] = {
        "fetched": len(records),
        "stored": stored,
        "skipped": skipped_invalid,
        "date_range": f"{earliest.date()} → {latest.date()}",
    }
    return stored


# ── Central bank rates backfill (TASK-V7-08) ─────────────────────────────────

_RATES_FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
_RATES_ECB_BASE_URL = "https://data-api.ecb.europa.eu/service/data"

# FRED series per bank: (bank_code, currency, series_id, source_label)
_FRED_BANK_SERIES: list[tuple[str, str, str, str]] = [
    ("FED", "USD", "FEDFUNDS",          "FRED/FEDFUNDS"),
    ("BOE", "GBP", "INTDSRGBM193N",     "FRED/INTDSRGBM193N"),
    ("BOJ", "JPY", "INTDSRJPM193N",     "FRED/INTDSRJPM193N"),
    ("BOC", "CAD", "IR3TIB01CAM156N",   "FRED/IR3TIB01CAM156N"),
    ("RBA", "AUD", "RBATCTR",           "FRED/RBATCTR"),
    ("SNB", "CHF", "IRSTCI01CHM156N",   "FRED/IRSTCI01CHM156N"),
    ("RBNZ","NZD", "IRSTCI01NZM156N",   "FRED/IRSTCI01NZM156N"),
]

# ECB endpoint: MRO rate (Main Refinancing Operations, fixed rate)
_ECB_SERIES_KEY = "FM/B.U2.EUR.4F.KR.MRR_FR.LEV"

_RATES_FRED_REQUEST_DELAY = 1.0  # seconds between FRED requests (rate limit)
_RATES_HTTP_TIMEOUT = 30.0


def _parse_rate_date(date_str: str) -> Optional[datetime.datetime]:
    """Parse YYYY-MM-DD or YYYY-MM into UTC-aware datetime (first of month)."""
    if not date_str or date_str == ".":
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            d = datetime.datetime.strptime(date_str.strip(), fmt)
            return d.replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            continue
    try:
        d = datetime.date.fromisoformat(date_str[:10])
        return datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc)
    except (ValueError, IndexError):
        logger.debug("Cannot parse date: %r", date_str)
        return None


async def _upsert_rate_records(
    session: Any,
    records: list[dict],
    dry_run: bool,
) -> int:
    """Insert records into central_bank_rates with ON CONFLICT DO UPDATE.

    Returns the number of rows inserted/updated.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: PLC0415

    from src.database.models import CentralBankRate  # noqa: PLC0415

    if not records:
        return 0

    if dry_run:
        for r in records:
            logger.info(
                "[DRY-RUN] Would upsert: bank=%s date=%s rate=%s",
                r["bank"],
                r["effective_date"].date() if r.get("effective_date") else "?",
                r.get("rate"),
            )
        return len(records)

    count = 0
    for r in records:
        try:
            stmt = (
                pg_insert(CentralBankRate)
                .values(
                    bank=r["bank"],
                    currency=r["currency"],
                    rate=r["rate"],
                    effective_date=r["effective_date"],
                    source=r.get("source"),
                    bias=None,
                )
                .on_conflict_do_update(
                    constraint="uix_central_bank_rates",
                    set_={
                        "rate": r["rate"],
                        "source": r.get("source"),
                    },
                )
            )
            await session.execute(stmt)
            count += 1
        except Exception as exc:
            logger.error(
                "Upsert failed for %s / %s: %s",
                r["bank"],
                r.get("effective_date"),
                exc,
            )

    await session.flush()
    return count


async def _fetch_rates_fred_series(
    client: httpx.AsyncClient,
    series_id: str,
    api_key: str,
    start_date: str,
) -> list[dict]:
    """Fetch all observations for a FRED series from start_date to present."""
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "asc",
        "observation_start": start_date,
        "limit": 10000,
    }
    logger.info("Fetching FRED series %s from %s ...", series_id, start_date)
    try:
        resp = await client.get(
            _RATES_FRED_BASE_URL,
            params=params,
            timeout=_RATES_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 400:
            logger.warning("FRED series %s not found (400). Skipping.", series_id)
        else:
            logger.warning("FRED HTTP error for %s: %s", series_id, exc)
        return []
    except httpx.RequestError as exc:
        logger.warning("FRED request error for %s: %s", series_id, exc)
        return []

    data = resp.json()
    observations = data.get("observations", [])
    return [obs for obs in observations if obs.get("value") not in (".", "", None)]


async def _fetch_ecb_history(
    client: httpx.AsyncClient,
    start_period: str,
) -> list[dict]:
    """Fetch ECB MRO rate history from startPeriod to present."""
    url = f"{_RATES_ECB_BASE_URL}/{_ECB_SERIES_KEY}"
    params = {
        "format": "jsondata",
        "startPeriod": start_period,
    }
    logger.info("Fetching ECB MRO rate history from %s ...", start_period)
    try:
        resp = await client.get(
            url,
            params=params,
            headers={"Accept": "application/json"},
            timeout=_RATES_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.warning("ECB HTTP error: %s", exc)
        return []
    except httpx.RequestError as exc:
        logger.warning("ECB request error: %s", exc)
        return []

    try:
        data = resp.json()
        series = data["dataSets"][0]["series"]
        key = list(series.keys())[0]
        observations = series[key]["observations"]
        time_dim = data["structure"]["dimensions"]["observation"][0]["values"]

        results: list[dict] = []
        for obs_key, obs_values in observations.items():
            idx = int(obs_key)
            rate_val = obs_values[0]
            if rate_val is None:
                continue
            date_str = time_dim[idx]["id"]
            if len(date_str) == 7:
                date_str = date_str + "-01"
            results.append({"date": date_str, "value": rate_val})

        results.sort(key=lambda x: x["date"])
        logger.info("ECB: fetched %d observations", len(results))
        return results

    except (KeyError, IndexError, ValueError) as exc:
        logger.warning("ECB response parse error: %s", exc)
        return []


async def _backfill_fred_bank(
    session: Any,
    client: httpx.AsyncClient,
    bank: str,
    currency: str,
    series_id: str,
    source_label: str,
    api_key: str,
    start_date: str,
    dry_run: bool,
) -> int:
    """Backfill a single bank's history from FRED. Returns count upserted."""
    observations = await _fetch_rates_fred_series(client, series_id, api_key, start_date)
    if not observations:
        logger.warning(
            "[%s] No data returned from FRED series %s — skipping", bank, series_id
        )
        return 0

    records: list[dict] = []
    for obs in observations:
        dt = _parse_rate_date(obs["date"])
        if dt is None:
            continue
        try:
            rate = Decimal(str(obs["value"]))
        except Exception:
            logger.debug("[%s] Cannot parse rate value: %r", bank, obs["value"])
            continue
        records.append(
            {
                "bank": bank,
                "currency": currency,
                "rate": rate,
                "effective_date": dt,
                "source": source_label,
            }
        )

    count = await _upsert_rate_records(session, records, dry_run)
    logger.info("[%s] Upserted %d records from FRED/%s", bank, count, series_id)
    return count


async def _backfill_ecb(
    session: Any,
    client: httpx.AsyncClient,
    start_date: str,
    dry_run: bool,
) -> int:
    """Backfill ECB MRO rate history. Returns count upserted."""
    start_period = start_date[:7]  # YYYY-MM
    observations = await _fetch_ecb_history(client, start_period)
    if not observations:
        logger.warning("[ECB] No data returned — skipping")
        return 0

    records: list[dict] = []
    for obs in observations:
        dt = _parse_rate_date(obs["date"])
        if dt is None:
            continue
        try:
            rate = Decimal(str(obs["value"]))
        except Exception:
            logger.debug("[ECB] Cannot parse rate value: %r", obs["value"])
            continue
        records.append(
            {
                "bank": "ECB",
                "currency": "EUR",
                "rate": rate,
                "effective_date": dt,
                "source": "ECB/MRR_FR",
            }
        )

    count = await _upsert_rate_records(session, records, dry_run)
    logger.info("[ECB] Upserted %d records", count)
    return count


async def backfill_rates(
    start: str = "2000-01-01",
    end: str = _BACKFILL_END_DEFAULT,
    dry_run: bool = False,
    banks: Optional[list[str]] = None,
) -> int:
    """Backfill central bank interest rates history.

    Fetches historical central bank policy rates for major economies:
    Fed, ECB, BoE, BoJ, RBA, etc.
    Implemented in TASK-V7-08.

    Args:
        start: ISO date string for the start of the backfill (default 2000-01-01).
        end: ISO date string (unused — kept for interface consistency).
        dry_run: If True, log what would be inserted without writing to DB.
        banks: Optional list of bank codes to restrict (e.g. ["FED", "ECB"]).

    Returns:
        Total number of records upserted across all banks (0 in dry_run mode).
    """
    from src.config import settings  # noqa: PLC0415
    from src.database.engine import async_session_factory  # noqa: PLC0415

    target_banks: set[str] = set(b.upper() for b in banks) if banks else set()

    if not settings.FRED_KEY:
        logger.warning(
            "[Rates] FRED_KEY not configured. FRED-sourced banks (FED, BOE, BOJ, BOC, RBA, "
            "SNB, RBNZ) will be skipped. Set FRED_KEY in .env to enable."
        )

    total = 0

    async with async_session_factory() as session:
        async with session.begin():
            async with httpx.AsyncClient(
                timeout=_RATES_HTTP_TIMEOUT,
                follow_redirects=True,
            ) as client:
                # ── FRED-sourced banks ─────────────────────────────────────
                if settings.FRED_KEY:
                    for bank, currency, series_id, source_label in _FRED_BANK_SERIES:
                        if target_banks and bank not in target_banks:
                            continue
                        try:
                            count = await _backfill_fred_bank(
                                session=session,
                                client=client,
                                bank=bank,
                                currency=currency,
                                series_id=series_id,
                                source_label=source_label,
                                api_key=settings.FRED_KEY,
                                start_date=start,
                                dry_run=dry_run,
                            )
                            total += count
                        except Exception as exc:
                            logger.warning("[%s] Backfill failed: %s — continuing", bank, exc)

                        await asyncio.sleep(_RATES_FRED_REQUEST_DELAY)

                # ── ECB (no API key required) ──────────────────────────────
                if not target_banks or "ECB" in target_banks:
                    try:
                        count = await _backfill_ecb(
                            session=session,
                            client=client,
                            start_date=start,
                            dry_run=dry_run,
                        )
                        total += count
                    except Exception as exc:
                        logger.warning("[ECB] Backfill failed: %s — continuing", exc)

    logger.info("[Rates] backfill_rates() complete: %d total records", total)
    return total


# ── ACLED backfill (TASK-V7-09) ───────────────────────────────────────────────

_ACLED_DEFAULT_START = datetime.date(2018, 1, 1)


async def backfill_acled(
    start: str = "2018-01-01",
    end: str = _BACKFILL_END_DEFAULT,
    dry_run: bool = False,
) -> int:
    """Backfill ACLED geopolitical conflict events.

    Fetches historical armed conflict and geopolitical event data from the
    Armed Conflict Location & Event Data Project (ACLED) API.
    Implemented in TASK-V7-09.

    Args:
        start: ISO date string for the beginning of the range (default 2018-01-01).
        end: ISO date string for the end of the range (default today).
        dry_run: If True, count records without writing to DB.

    Returns:
        Number of records inserted (0 in dry_run mode).
    """
    from src.collectors.acled_collector import ACLEDCollector  # noqa: PLC0415
    from src.config import settings  # noqa: PLC0415

    if not settings.ACLED_API_KEY or not settings.ACLED_EMAIL:
        logger.warning(
            "[backfill_acled] ACLED_API_KEY or ACLED_EMAIL not configured in settings. "
            "Set these environment variables and re-run. Returning 0."
        )
        return 0

    try:
        start_date = datetime.date.fromisoformat(start)
    except ValueError:
        logger.error("[backfill_acled] Invalid start date: %r", start)
        return 0

    try:
        end_date = datetime.date.fromisoformat(end)
    except ValueError:
        logger.error("[backfill_acled] Invalid end date: %r", end)
        return 0

    if start_date > end_date:
        logger.error(
            "[backfill_acled] start_date %s is after end_date %s", start_date, end_date
        )
        return 0

    logger.info(
        "[backfill_acled] Starting ACLED backfill: %s → %s (dry_run=%s)",
        start_date,
        end_date,
        dry_run,
    )

    collector = ACLEDCollector()
    total = await collector._collect_range(start_date, end_date, dry_run=dry_run)

    mode = "would insert" if dry_run else "inserted"
    logger.info("[backfill_acled] Done. %s %d ACLED records.", mode, total)
    return total


# ── CoinMetrics backfill (TASK-V7-10) ────────────────────────────────────────

_COINMETRICS_BASE = "https://community-api.coinmetrics.io/v4"

# Metrics to backfill for BTC and ETH
_CM_ASSETS = ("btc", "eth")
_CM_METRICS = ("CapMVRVCur", "AdrActCnt", "TxCnt")

# Mapping: metric_key → short suffix stored in indicator_name
_METRIC_SHORT: dict[str, str] = {
    "CapMVRVCur": "MVRV",
    "AdrActCnt": "ADRACT",
    "TxCnt": "TXCNT",
}

# CoinMetrics community API: conservative rate limit
_CM_REQUEST_DELAY_SECONDS = 2.0
# CoinMetrics returns up to 10 000 rows per request
_CM_PAGE_SIZE = 10_000

_LOG_PROGRESS_EVERY = 1_000


async def backfill_coinmetrics(
    start: str = _BACKFILL_START_DEFAULT,
    end: str = _BACKFILL_END_DEFAULT,
    dry_run: bool = False,
) -> int:
    """Backfill CoinMetrics community on-chain metrics for BTC and ETH.

    Stores records in the ``macro_data`` table with indicator names following
    the pattern ``COINMETRICS_{ASSET}_{METRIC}``
    (e.g. ``COINMETRICS_BTC_MVRV``, ``COINMETRICS_ETH_TXCNT``).

    Idempotent — uses upsert on (indicator_name, country, release_date).
    Implemented in TASK-V7-10.

    Args:
        start: ISO date string for the beginning of the range (default 2018-01-01).
        end:   ISO date string for the end of the range (default today).
        dry_run: When True, log records but skip all DB writes.

    Returns:
        Total number of records inserted (0 when dry_run=True).
    """
    from src.database.crud import upsert_macro_data  # noqa: PLC0415
    from src.database.engine import async_session_factory  # noqa: PLC0415

    logger.info(
        "CoinMetrics backfill: assets=%s metrics=%s range=%s..%s dry_run=%s",
        _CM_ASSETS,
        _CM_METRICS,
        start,
        end,
        dry_run,
    )

    total_inserted = 0
    assets_str = ",".join(_CM_ASSETS)
    metrics_str = ",".join(_CM_METRICS)

    async with httpx.AsyncClient(timeout=30.0) as client:
        next_page_token: Optional[str] = None
        page_num = 0
        batch: list[dict] = []

        while True:
            page_num += 1
            params: dict[str, str] = {
                "assets": assets_str,
                "metrics": metrics_str,
                "start_time": start,
                "end_time": end,
                "frequency": "1d",
                "page_size": str(_CM_PAGE_SIZE),
            }
            if next_page_token:
                params["next_page_token"] = next_page_token

            logger.debug(
                "CoinMetrics: fetching page %d (token=%s)", page_num, next_page_token
            )

            try:
                resp = await client.get(
                    f"{_COINMETRICS_BASE}/timeseries/asset-metrics",
                    params=params,
                )
                resp.raise_for_status()
            except Exception as exc:
                logger.error("CoinMetrics: request failed (page %d): %s", page_num, exc)
                break

            payload = resp.json()
            rows: list[dict] = payload.get("data", [])

            if not rows:
                logger.info("CoinMetrics: empty response on page %d — stopping", page_num)
                break

            records = _parse_coinmetrics_rows(rows)
            batch.extend(records)

            if len(batch) >= _LOG_PROGRESS_EVERY:
                logger.info(
                    "CoinMetrics: fetched %d records so far (page %d)...",
                    len(batch),
                    page_num,
                )

            next_page_token = payload.get("next_page_token")
            if not next_page_token:
                break

            await asyncio.sleep(_CM_REQUEST_DELAY_SECONDS)

    logger.info("CoinMetrics: total records parsed: %d", len(batch))

    if not batch:
        logger.warning("CoinMetrics: no records to store")
        return 0

    if dry_run:
        logger.info("CoinMetrics: dry_run=True — skipping DB write (%d records)", len(batch))
        _log_sample(batch)
        return 0

    # Write in chunks to stay within PostgreSQL parameter limits
    chunk_size = 500
    for i in range(0, len(batch), chunk_size):
        chunk = batch[i : i + chunk_size]
        async with async_session_factory() as session:
            async with session.begin():
                inserted = await upsert_macro_data(session, chunk)
                total_inserted += inserted

        progress = min(i + chunk_size, len(batch))
        if progress % _LOG_PROGRESS_EVERY == 0 or progress == len(batch):
            logger.info(
                "CoinMetrics: stored %d / %d records (%d inserted)",
                progress,
                len(batch),
                total_inserted,
            )

    logger.info(
        "CoinMetrics backfill complete: %d records inserted out of %d parsed",
        total_inserted,
        len(batch),
    )
    return total_inserted


def _parse_coinmetrics_rows(rows: list[dict]) -> list[dict]:
    """Convert raw CoinMetrics API rows to macro_data dicts.

    Each API row has the shape:
        {"asset": "btc", "time": "2018-01-02T00:00:00.000000000Z",
         "CapMVRVCur": "1.23", "AdrActCnt": "456789", "TxCnt": "210000"}

    Produces one macro_data record per (asset, metric, date) triple.
    Skips rows where a metric value is null or non-numeric.
    """
    records: list[dict] = []

    for row in rows:
        asset_code: str = row.get("asset", "").upper()  # "BTC" / "ETH"
        time_str: Optional[str] = row.get("time")

        if not asset_code or not time_str:
            continue

        try:
            # Normalize: strip trailing Z, remove nanoseconds, append UTC offset
            ts_norm = time_str.rstrip("Z")
            if "." in ts_norm:
                ts_norm = ts_norm.split(".")[0]
            release_dt = datetime.datetime.fromisoformat(ts_norm + "+00:00")
        except ValueError:
            logger.warning(
                "CoinMetrics: cannot parse timestamp %r — skipping row", time_str
            )
            continue

        for metric_key in _CM_METRICS:
            raw_value: Optional[str] = row.get(metric_key)
            if raw_value is None:
                continue

            try:
                value = Decimal(str(raw_value))
            except Exception:
                logger.debug(
                    "CoinMetrics: non-numeric %s=%r for %s on %s — skipping",
                    metric_key,
                    raw_value,
                    asset_code,
                    release_dt.date(),
                )
                continue

            short = _METRIC_SHORT.get(metric_key, metric_key)
            indicator_name = f"COINMETRICS_{asset_code}_{short}"

            records.append(
                {
                    "indicator_name": indicator_name,
                    "country": "GLOBAL",
                    "value": value,
                    "previous_value": None,
                    "forecast_value": None,
                    "release_date": release_dt,
                    "source": "coinmetrics",
                }
            )

    return records


def _log_sample(records: list[dict], n: int = 5) -> None:
    """Log a few sample records for dry-run verification."""
    for rec in records[:n]:
        logger.info(
            "  [dry-run] %s  country=%s  date=%s  value=%s",
            rec["indicator_name"],
            rec["country"],
            rec.get("release_date", "?"),
            rec["value"],
        )
    if len(records) > n:
        logger.info("  [dry-run] ... and %d more records", len(records) - n)


# ── CLI ────────────────────────────────────────────────────────────────────────

# Registry mapping source names to their backfill functions.
# Each function has signature: async (start, end, dry_run) -> int
_SOURCE_MAP: dict[str, Any] = {
    "fred": backfill_fred,
    "fear_greed": backfill_fear_greed,
    "rates": backfill_rates,
    "acled": backfill_acled,
    "coinmetrics": backfill_coinmetrics,
}

ALL_SOURCES = list(_SOURCE_MAP.keys())


async def main(
    sources: list[str],
    start: str = _BACKFILL_START_DEFAULT,
    end: str = _BACKFILL_END_DEFAULT,
    dry_run: bool = False,
) -> None:
    """Run backfill for the requested sources sequentially.

    Args:
        sources: List of source names to backfill (subset of _SOURCE_MAP keys).
        start: ISO date string for the beginning of the backfill range.
        end: ISO date string for the end of the backfill range.
        dry_run: If True, skip actual DB writes and only log what would happen.
    """
    t0 = time.time()

    if dry_run:
        logger.info("DRY-RUN mode — no data will be written to the database")

    logger.info("=" * 60)
    logger.info("Backfilling historical data: %s", ", ".join(sources))
    logger.info("Range: %s → %s", start, end)
    logger.info("=" * 60)

    total = 0
    for source_name in sources:
        fn = _SOURCE_MAP[source_name]
        logger.info("Starting backfill: %s", source_name)
        try:
            count = await fn(start=start, end=end, dry_run=dry_run)
            logger.info(
                "Finished backfill: %s — %d records upserted", source_name, count
            )
            total += count
        except Exception:
            logger.exception(
                "backfill_%s failed — continuing with next source", source_name
            )

    elapsed = time.time() - t0
    logger.info("=" * 60)
    logger.info("All done. Total records upserted: %d in %.1fs", total, elapsed)


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
        "--start",
        default=_BACKFILL_START_DEFAULT,
        help=f"Start date ISO 8601 (default: {_BACKFILL_START_DEFAULT})",
    )
    parser.add_argument(
        "--end",
        default=_BACKFILL_END_DEFAULT,
        help="End date ISO 8601 (default: today)",
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
    asyncio.run(
        main(
            sources=requested_sources,
            start=args.start,
            end=args.end,
            dry_run=args.dry_run,
        )
    )
