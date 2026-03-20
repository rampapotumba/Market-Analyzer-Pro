"""
Download HIGH-impact economic calendar events for 2024-2025.

Hybrid approach:
  1. TradingEconomics free API (guest:guest) for US events — auto-fetched day by day
  2. Comprehensive manual list for all other countries (ECB, BOE, BOJ, RBA, RBNZ, BOC, SNB)

The TE guest API only allows US data (403 for other countries) and returns max 3 records
per request. For US events, this captures NFP, CPI, FOMC, GDP, ISM PMI, Retail Sales, etc.

For non-US countries, we include central bank rate decisions and key data releases compiled
from official sources (ECB, BOE, BOJ, RBA, RBNZ, BOC, SNB).

Output: CSV file with columns:
  date, time_utc, country, country_name, currency, event_name, impact, actual, previous,
  forecast, te_forecast, ticker, source

Usage:
    python scripts/download_economic_calendar_te.py
    python scripts/download_economic_calendar_te.py --from 2024-01-01 --to 2025-12-31
    python scripts/download_economic_calendar_te.py --output data/economic_calendar.csv
    python scripts/download_economic_calendar_te.py --us-only     # skip manual non-US events
    python scripts/download_economic_calendar_te.py --manual-only  # skip TE API download
    python scripts/download_economic_calendar_te.py --load-db      # insert into DB
    python scripts/download_economic_calendar_te.py --load-db --dry-run
"""

import argparse
import asyncio
import csv
import datetime
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import requests

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

TE_API_BASE = "https://api.tradingeconomics.com/calendar"
TE_GUEST_KEY = "guest:guest"

COUNTRY_ISO = {
    "united states": "US",
    "euro area": "EU",
    "germany": "DE",
    "united kingdom": "GB",
    "japan": "JP",
    "canada": "CA",
    "australia": "AU",
    "new zealand": "NZ",
    "switzerland": "CH",
    "china": "CN",
}

HIGH_IMPORTANCE = 3
REQUEST_DELAY = 0.3

DEFAULT_FROM = "2024-01-01"
DEFAULT_TO = "2025-12-31"
DEFAULT_OUTPUT = "data/economic_calendar_full.csv"


# ── TradingEconomics API (US only) ───────────────────────────────────────────

def fetch_day_us(
    session: requests.Session,
    date_str: str,
) -> list[dict[str, Any]]:
    """Fetch up to 3 HIGH-impact US economic events for a single day."""
    url = f"{TE_API_BASE}/country/united states/{date_str}/{date_str}"
    params = {
        "c": TE_GUEST_KEY,
        "f": "json",
        "importance": str(HIGH_IMPORTANCE),
    }
    try:
        resp = session.get(url, params=params, timeout=15)
        if resp.status_code == 429:
            logger.warning("Rate limited, waiting 60s...")
            time.sleep(60)
            resp = session.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.error("Error fetching US / %s: %s", date_str, exc)
        return []


def parse_te_event(raw: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Parse a TradingEconomics event dict into our standard format."""
    event_name = str(raw.get("Event") or "").strip()
    if not event_name:
        return None

    date_str = str(raw.get("Date") or "").strip()
    if not date_str:
        return None

    event_dt = None
    for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
        try:
            event_dt = datetime.datetime.strptime(date_str, fmt)
            event_dt = event_dt.replace(tzinfo=datetime.timezone.utc)
            break
        except ValueError:
            continue

    if event_dt is None:
        return None

    country_raw = str(raw.get("Country") or "").strip()
    country_iso = COUNTRY_ISO.get(country_raw.lower(), country_raw[:2].upper())
    ticker = str(raw.get("Ticker") or "").strip()

    return {
        "event_date": event_dt,
        "date": event_dt.strftime("%Y-%m-%d"),
        "time_utc": event_dt.strftime("%H:%M"),
        "country": country_iso,
        "country_name": country_raw,
        "currency": "USD",
        "event_name": event_name,
        "impact": "high",
        "actual": str(raw.get("Actual") or "").strip() or None,
        "previous": str(raw.get("Previous") or "").strip() or None,
        "forecast": str(raw.get("Forecast") or "").strip() or None,
        "te_forecast": str(raw.get("TEForecast") or "").strip() or None,
        "ticker": ticker,
        "source": "TradingEconomics",
    }


def generate_weekdays(date_from: str, date_to: str) -> list[str]:
    """Generate weekday date strings (YYYY-MM-DD) between from and to (inclusive)."""
    start = datetime.date.fromisoformat(date_from)
    end = datetime.date.fromisoformat(date_to)
    dates = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            dates.append(current.isoformat())
        current += datetime.timedelta(days=1)
    return dates


def download_us_events(date_from: str, date_to: str) -> list[dict[str, Any]]:
    """Download US HIGH-impact events from TradingEconomics API."""
    dates = generate_weekdays(date_from, date_to)
    logger.info("Downloading US events from TE API: %d trading days", len(dates))
    logger.info("  Estimated time: %.1f minutes", len(dates) * REQUEST_DELAY / 60)

    events: list[dict[str, Any]] = []
    seen_keys: set[tuple] = set()

    session = requests.Session()
    session.headers.update({
        "User-Agent": "MarketAnalyzerPro/1.0",
    })

    for i, date_str in enumerate(dates):
        raw_events = fetch_day_us(session, date_str)

        for raw in raw_events:
            parsed = parse_te_event(raw)
            if parsed is None:
                continue
            key = (parsed["date"], parsed["country"], parsed["event_name"])
            if key not in seen_keys:
                seen_keys.add(key)
                events.append(parsed)

        time.sleep(REQUEST_DELAY)

        if (i + 1) % 50 == 0:
            logger.info("  US progress: %d/%d days, %d events", i + 1, len(dates), len(events))

    logger.info("US download complete: %d events", len(events))
    return events


# ── Manual events for non-US countries ────────────────────────────────────────
# Compiled from official central bank calendars and key data release schedules.

def _dt(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime.datetime:
    """Create a UTC-aware datetime."""
    return datetime.datetime(year, month, day, hour, minute, tzinfo=datetime.timezone.utc)


def _make_event(
    dt: datetime.datetime, country: str, currency: str,
    name: str, source: str = "Official",
) -> dict[str, Any]:
    """Create a standardized event dict."""
    return {
        "event_date": dt,
        "date": dt.strftime("%Y-%m-%d"),
        "time_utc": dt.strftime("%H:%M"),
        "country": country,
        "country_name": country,
        "currency": currency,
        "event_name": name,
        "impact": "high",
        "actual": None,
        "previous": None,
        "forecast": None,
        "te_forecast": None,
        "ticker": "",
        "source": source,
    }


def build_manual_events() -> list[dict[str, Any]]:
    """Build comprehensive list of HIGH-impact non-US events for 2024-2025."""
    events: list[dict[str, Any]] = []

    # ── ECB Rate Decisions ────────────────────────────────────────────────────
    # Source: ecb.europa.eu — press conference at 14:15 CET (13:15 UTC winter, 12:15 UTC summer)
    ecb_dates_2024 = [
        (2024, 1, 25, 13, 15), (2024, 3, 7, 13, 15), (2024, 4, 11, 12, 15),
        (2024, 6, 6, 12, 15), (2024, 7, 18, 12, 15), (2024, 9, 12, 12, 15),
        (2024, 10, 17, 12, 15), (2024, 12, 12, 13, 15),
    ]
    ecb_dates_2025 = [
        (2025, 1, 30, 13, 15), (2025, 3, 6, 13, 15), (2025, 4, 17, 12, 15),
        (2025, 6, 5, 12, 15), (2025, 7, 24, 12, 15), (2025, 9, 11, 12, 15),
        (2025, 10, 30, 13, 15), (2025, 12, 18, 13, 15),
    ]
    for args in ecb_dates_2024 + ecb_dates_2025:
        events.append(_make_event(_dt(*args), "EU", "EUR", "ECB Interest Rate Decision", "ECB"))

    # ── BOE Rate Decisions (MPC) ──────────────────────────────────────────────
    # Source: bankofengland.co.uk — announced at 12:00 UTC
    boe_dates_2024 = [
        (2024, 2, 1), (2024, 3, 21), (2024, 5, 9), (2024, 6, 20),
        (2024, 8, 1), (2024, 9, 19), (2024, 11, 7), (2024, 12, 19),
    ]
    boe_dates_2025 = [
        (2025, 2, 6), (2025, 3, 20), (2025, 5, 8), (2025, 6, 19),
        (2025, 8, 7), (2025, 9, 18), (2025, 11, 6), (2025, 12, 18),
    ]
    for args in boe_dates_2024 + boe_dates_2025:
        events.append(_make_event(_dt(*args, 12, 0), "GB", "GBP", "BOE Interest Rate Decision", "BOE"))

    # ── BOJ Rate Decisions ────────────────────────────────────────────────────
    # Source: boj.or.jp — usually announced around 03:00 UTC (12:00 JST)
    boj_dates_2024 = [
        (2024, 1, 23), (2024, 3, 19), (2024, 4, 26), (2024, 6, 14),
        (2024, 7, 31), (2024, 9, 20), (2024, 10, 31), (2024, 12, 19),
    ]
    boj_dates_2025 = [
        (2025, 1, 24), (2025, 3, 14), (2025, 5, 1), (2025, 6, 17),
        (2025, 7, 31), (2025, 9, 19), (2025, 10, 31), (2025, 12, 19),
    ]
    for args in boj_dates_2024 + boj_dates_2025:
        events.append(_make_event(_dt(*args, 3, 0), "JP", "JPY", "BOJ Interest Rate Decision", "BOJ"))

    # ── RBA Rate Decisions ────────────────────────────────────────────────────
    # Source: rba.gov.au — announced at 04:30 UTC (14:30 AEST)
    rba_dates_2024 = [
        (2024, 2, 6), (2024, 3, 19), (2024, 5, 7), (2024, 6, 18),
        (2024, 8, 6), (2024, 9, 24), (2024, 11, 5), (2024, 12, 10),
    ]
    rba_dates_2025 = [
        (2025, 2, 18), (2025, 4, 1), (2025, 5, 20), (2025, 7, 8),
        (2025, 8, 12), (2025, 9, 30), (2025, 11, 4), (2025, 12, 9),
    ]
    for args in rba_dates_2024 + rba_dates_2025:
        events.append(_make_event(_dt(*args, 4, 30), "AU", "AUD", "RBA Interest Rate Decision", "RBA"))

    # ── RBNZ Rate Decisions ───────────────────────────────────────────────────
    # Source: rbnz.govt.nz — announced at 01:00 UTC (14:00 NZST)
    rbnz_dates_2024 = [
        (2024, 2, 28), (2024, 4, 10), (2024, 5, 22), (2024, 7, 10),
        (2024, 8, 14), (2024, 10, 9), (2024, 11, 27),
    ]
    rbnz_dates_2025 = [
        (2025, 2, 19), (2025, 4, 9), (2025, 5, 28), (2025, 7, 9),
        (2025, 8, 20), (2025, 10, 8), (2025, 11, 26),
    ]
    for args in rbnz_dates_2024 + rbnz_dates_2025:
        events.append(_make_event(_dt(*args, 1, 0), "NZ", "NZD", "RBNZ Interest Rate Decision", "RBNZ"))

    # ── BOC Rate Decisions ────────────────────────────────────────────────────
    # Source: bankofcanada.ca — announced at 14:45 UTC (09:45 ET)
    boc_dates_2024 = [
        (2024, 1, 24), (2024, 3, 6), (2024, 4, 10), (2024, 6, 5),
        (2024, 7, 24), (2024, 9, 4), (2024, 10, 23), (2024, 12, 11),
    ]
    boc_dates_2025 = [
        (2025, 1, 29), (2025, 3, 12), (2025, 4, 16), (2025, 6, 4),
        (2025, 7, 30), (2025, 9, 17), (2025, 10, 29), (2025, 12, 10),
    ]
    for args in boc_dates_2024 + boc_dates_2025:
        events.append(_make_event(_dt(*args, 14, 45), "CA", "CAD", "BOC Interest Rate Decision", "BOC"))

    # ── SNB Rate Decisions ────────────────────────────────────────────────────
    # Source: snb.ch — announced at 08:30 UTC (09:30 CET)
    snb_dates_2024 = [
        (2024, 3, 21), (2024, 6, 20), (2024, 9, 26), (2024, 12, 12),
    ]
    snb_dates_2025 = [
        (2025, 3, 20), (2025, 6, 19), (2025, 9, 18), (2025, 12, 11),
    ]
    for args in snb_dates_2024 + snb_dates_2025:
        events.append(_make_event(_dt(*args, 8, 30), "CH", "CHF", "SNB Interest Rate Decision", "SNB"))

    # ── UK CPI ────────────────────────────────────────────────────────────────
    # Source: ONS — released at 07:00 UTC, mid-month
    uk_cpi_2024 = [
        (2024, 1, 17), (2024, 2, 14), (2024, 3, 20), (2024, 4, 17),
        (2024, 5, 22), (2024, 6, 19), (2024, 7, 17), (2024, 8, 14),
        (2024, 9, 18), (2024, 10, 16), (2024, 11, 20), (2024, 12, 18),
    ]
    uk_cpi_2025 = [
        (2025, 1, 15), (2025, 2, 19), (2025, 3, 26), (2025, 4, 16),
        (2025, 5, 21), (2025, 6, 18), (2025, 7, 16), (2025, 8, 20),
        (2025, 9, 17), (2025, 10, 15), (2025, 11, 19), (2025, 12, 17),
    ]
    for args in uk_cpi_2024 + uk_cpi_2025:
        events.append(_make_event(_dt(*args, 7, 0), "GB", "GBP", "UK CPI YoY", "ONS"))

    # ── EU CPI (Flash Estimate) ───────────────────────────────────────────────
    # Source: Eurostat — released at 10:00 UTC, end of month
    eu_cpi_2024 = [
        (2024, 1, 5), (2024, 2, 1), (2024, 3, 1), (2024, 3, 29),
        (2024, 5, 3), (2024, 5, 31), (2024, 7, 2), (2024, 7, 31),
        (2024, 9, 3), (2024, 10, 1), (2024, 10, 31), (2024, 11, 29),
    ]
    eu_cpi_2025 = [
        (2025, 1, 7), (2025, 2, 3), (2025, 3, 3), (2025, 4, 1),
        (2025, 5, 5), (2025, 6, 3), (2025, 7, 1), (2025, 8, 1),
        (2025, 9, 2), (2025, 10, 1), (2025, 10, 31), (2025, 12, 2),
    ]
    for args in eu_cpi_2024 + eu_cpi_2025:
        events.append(_make_event(_dt(*args, 10, 0), "EU", "EUR", "EU CPI Flash Estimate YoY", "Eurostat"))

    # ── Canada CPI ────────────────────────────────────────────────────────────
    # Source: Statistics Canada — released at 13:30 UTC
    ca_cpi_2024 = [
        (2024, 1, 16), (2024, 2, 20), (2024, 3, 19), (2024, 4, 16),
        (2024, 5, 21), (2024, 6, 25), (2024, 7, 16), (2024, 8, 20),
        (2024, 9, 17), (2024, 10, 15), (2024, 11, 19), (2024, 12, 17),
    ]
    ca_cpi_2025 = [
        (2025, 1, 21), (2025, 2, 18), (2025, 3, 18), (2025, 4, 15),
        (2025, 5, 20), (2025, 6, 17), (2025, 7, 15), (2025, 8, 19),
        (2025, 9, 16), (2025, 10, 21), (2025, 11, 18), (2025, 12, 16),
    ]
    for args in ca_cpi_2024 + ca_cpi_2025:
        events.append(_make_event(_dt(*args, 13, 30), "CA", "CAD", "Canada CPI YoY", "StatCan"))

    # ── Australia CPI (Quarterly) ─────────────────────────────────────────────
    # Source: ABS — released at 00:30 UTC
    au_cpi = [
        (2024, 1, 31), (2024, 4, 24), (2024, 7, 31), (2024, 10, 30),
        (2025, 1, 29), (2025, 4, 30), (2025, 7, 30), (2025, 10, 29),
    ]
    for args in au_cpi:
        events.append(_make_event(_dt(*args, 0, 30), "AU", "AUD", "Australia CPI QoQ", "ABS"))

    # ── Japan CPI ─────────────────────────────────────────────────────────────
    # Source: Statistics Bureau — released at 23:30 UTC (08:30 JST next day)
    jp_cpi_2024 = [
        (2024, 1, 19), (2024, 2, 27), (2024, 3, 22), (2024, 4, 19),
        (2024, 5, 24), (2024, 6, 21), (2024, 7, 19), (2024, 8, 23),
        (2024, 9, 20), (2024, 10, 18), (2024, 11, 22), (2024, 12, 20),
    ]
    jp_cpi_2025 = [
        (2025, 1, 24), (2025, 2, 21), (2025, 3, 21), (2025, 4, 18),
        (2025, 5, 23), (2025, 6, 20), (2025, 7, 18), (2025, 8, 22),
        (2025, 9, 19), (2025, 10, 24), (2025, 11, 21), (2025, 12, 19),
    ]
    for args in jp_cpi_2024 + jp_cpi_2025:
        events.append(_make_event(_dt(*args, 23, 30), "JP", "JPY", "Japan CPI YoY", "StatBureau"))

    # ── UK GDP ────────────────────────────────────────────────────────────────
    # Source: ONS — released at 07:00 UTC
    uk_gdp_2024 = [
        (2024, 2, 15), (2024, 5, 10), (2024, 6, 28), (2024, 8, 15),
        (2024, 9, 30), (2024, 11, 15), (2024, 12, 23),
    ]
    uk_gdp_2025 = [
        (2025, 2, 13), (2025, 3, 28), (2025, 5, 15), (2025, 6, 27),
        (2025, 8, 14), (2025, 9, 30), (2025, 11, 14), (2025, 12, 23),
    ]
    for args in uk_gdp_2024 + uk_gdp_2025:
        events.append(_make_event(_dt(*args, 7, 0), "GB", "GBP", "UK GDP QoQ", "ONS"))

    # ── EU GDP ────────────────────────────────────────────────────────────────
    # Source: Eurostat — released at 10:00 UTC
    eu_gdp = [
        (2024, 1, 30), (2024, 4, 30), (2024, 7, 30), (2024, 10, 30),
        (2025, 1, 30), (2025, 4, 30), (2025, 7, 30), (2025, 10, 30),
    ]
    for args in eu_gdp:
        events.append(_make_event(_dt(*args, 10, 0), "EU", "EUR", "EU GDP Flash Estimate QoQ", "Eurostat"))

    # ── Canada GDP ────────────────────────────────────────────────────────────
    ca_gdp = [
        (2024, 2, 29), (2024, 5, 31), (2024, 8, 30), (2024, 11, 29),
        (2025, 2, 28), (2025, 5, 30), (2025, 8, 29), (2025, 11, 28),
    ]
    for args in ca_gdp:
        events.append(_make_event(_dt(*args, 13, 30), "CA", "CAD", "Canada GDP MoM", "StatCan"))

    # ── Japan GDP ─────────────────────────────────────────────────────────────
    jp_gdp = [
        (2024, 2, 15), (2024, 5, 16), (2024, 8, 15), (2024, 11, 15),
        (2025, 2, 17), (2025, 5, 16), (2025, 8, 13), (2025, 11, 17),
    ]
    for args in jp_gdp:
        events.append(_make_event(_dt(*args, 23, 50), "JP", "JPY", "Japan GDP QoQ", "Cabinet Office"))

    # ── Australia Employment ──────────────────────────────────────────────────
    # Source: ABS — monthly, released at 00:30 UTC
    au_emp_2024 = [
        (2024, 1, 18), (2024, 2, 15), (2024, 3, 21), (2024, 4, 18),
        (2024, 5, 16), (2024, 6, 13), (2024, 7, 18), (2024, 8, 15),
        (2024, 9, 19), (2024, 10, 17), (2024, 11, 14), (2024, 12, 12),
    ]
    au_emp_2025 = [
        (2025, 1, 16), (2025, 2, 20), (2025, 3, 20), (2025, 4, 17),
        (2025, 5, 15), (2025, 6, 19), (2025, 7, 17), (2025, 8, 14),
        (2025, 9, 18), (2025, 10, 16), (2025, 11, 13), (2025, 12, 11),
    ]
    for args in au_emp_2024 + au_emp_2025:
        events.append(_make_event(_dt(*args, 0, 30), "AU", "AUD", "Australia Employment Change", "ABS"))

    # ── Canada Employment ─────────────────────────────────────────────────────
    # Source: Statistics Canada — released at 13:30 UTC, first Friday of month
    ca_emp_2024 = [
        (2024, 1, 5), (2024, 2, 9), (2024, 3, 8), (2024, 4, 5),
        (2024, 5, 10), (2024, 6, 7), (2024, 7, 5), (2024, 8, 9),
        (2024, 9, 6), (2024, 10, 11), (2024, 11, 8), (2024, 12, 6),
    ]
    ca_emp_2025 = [
        (2025, 1, 10), (2025, 2, 7), (2025, 3, 7), (2025, 4, 4),
        (2025, 5, 9), (2025, 6, 6), (2025, 7, 4), (2025, 8, 8),
        (2025, 9, 5), (2025, 10, 10), (2025, 11, 7), (2025, 12, 5),
    ]
    for args in ca_emp_2024 + ca_emp_2025:
        events.append(_make_event(_dt(*args, 13, 30), "CA", "CAD", "Canada Employment Change", "StatCan"))

    # ── NZ GDP (Quarterly) ────────────────────────────────────────────────────
    nz_gdp = [
        (2024, 3, 21), (2024, 6, 20), (2024, 9, 19), (2024, 12, 19),
        (2025, 3, 20), (2025, 6, 19), (2025, 9, 18), (2025, 12, 18),
    ]
    for args in nz_gdp:
        events.append(_make_event(_dt(*args, 21, 45), "NZ", "NZD", "New Zealand GDP QoQ", "StatsNZ"))

    # ── NZ CPI (Quarterly) ────────────────────────────────────────────────────
    nz_cpi = [
        (2024, 1, 24), (2024, 4, 17), (2024, 7, 17), (2024, 10, 16),
        (2025, 1, 22), (2025, 4, 16), (2025, 7, 16), (2025, 10, 15),
    ]
    for args in nz_cpi:
        events.append(_make_event(_dt(*args, 21, 45), "NZ", "NZD", "New Zealand CPI QoQ", "StatsNZ"))

    # ── NZ Employment (Quarterly) ─────────────────────────────────────────────
    nz_emp = [
        (2024, 2, 7), (2024, 5, 1), (2024, 8, 7), (2024, 11, 6),
        (2025, 2, 5), (2025, 5, 7), (2025, 8, 6), (2025, 11, 5),
    ]
    for args in nz_emp:
        events.append(_make_event(_dt(*args, 21, 45), "NZ", "NZD", "New Zealand Employment Change QoQ", "StatsNZ"))

    # ── China GDP (Quarterly) ─────────────────────────────────────────────────
    cn_gdp = [
        (2024, 1, 17), (2024, 4, 16), (2024, 7, 15), (2024, 10, 18),
        (2025, 1, 17), (2025, 4, 16), (2025, 7, 15), (2025, 10, 17),
    ]
    for args in cn_gdp:
        events.append(_make_event(_dt(*args, 2, 0), "CN", "CNY", "China GDP YoY", "NBS"))

    # ── China CPI ─────────────────────────────────────────────────────────────
    cn_cpi_2024 = [
        (2024, 1, 12), (2024, 2, 8), (2024, 3, 9), (2024, 4, 11),
        (2024, 5, 11), (2024, 6, 12), (2024, 7, 10), (2024, 8, 9),
        (2024, 9, 9), (2024, 10, 13), (2024, 11, 9), (2024, 12, 9),
    ]
    cn_cpi_2025 = [
        (2025, 1, 9), (2025, 2, 14), (2025, 3, 10), (2025, 4, 10),
        (2025, 5, 12), (2025, 6, 10), (2025, 7, 9), (2025, 8, 9),
        (2025, 9, 9), (2025, 10, 11), (2025, 11, 9), (2025, 12, 10),
    ]
    for args in cn_cpi_2024 + cn_cpi_2025:
        events.append(_make_event(_dt(*args, 1, 30), "CN", "CNY", "China CPI YoY", "NBS"))

    # ── Switzerland CPI ───────────────────────────────────────────────────────
    ch_cpi_2024 = [
        (2024, 1, 4), (2024, 2, 5), (2024, 3, 4), (2024, 4, 4),
        (2024, 5, 2), (2024, 6, 4), (2024, 7, 3), (2024, 8, 1),
        (2024, 9, 3), (2024, 10, 3), (2024, 11, 1), (2024, 12, 3),
    ]
    ch_cpi_2025 = [
        (2025, 1, 6), (2025, 2, 4), (2025, 3, 4), (2025, 4, 3),
        (2025, 5, 5), (2025, 6, 3), (2025, 7, 3), (2025, 8, 4),
        (2025, 9, 2), (2025, 10, 2), (2025, 11, 4), (2025, 12, 2),
    ]
    for args in ch_cpi_2024 + ch_cpi_2025:
        events.append(_make_event(_dt(*args, 7, 30), "CH", "CHF", "Switzerland CPI YoY", "FSO"))

    # ── UK Employment / Claimant Count ────────────────────────────────────────
    uk_emp_2024 = [
        (2024, 1, 16), (2024, 2, 13), (2024, 3, 12), (2024, 4, 16),
        (2024, 5, 14), (2024, 6, 11), (2024, 7, 16), (2024, 8, 13),
        (2024, 9, 10), (2024, 10, 15), (2024, 11, 12), (2024, 12, 17),
    ]
    uk_emp_2025 = [
        (2025, 1, 21), (2025, 2, 18), (2025, 3, 18), (2025, 4, 15),
        (2025, 5, 13), (2025, 6, 17), (2025, 7, 15), (2025, 8, 12),
        (2025, 9, 16), (2025, 10, 14), (2025, 11, 11), (2025, 12, 16),
    ]
    for args in uk_emp_2024 + uk_emp_2025:
        events.append(_make_event(_dt(*args, 7, 0), "GB", "GBP", "UK Claimant Count Change", "ONS"))

    logger.info("Manual non-US events: %d total", len(events))
    return events


# ── CSV output ────────────────────────────────────────────────────────────────

CSV_COLUMNS = [
    "date", "time_utc", "country", "country_name", "currency",
    "event_name", "impact", "actual", "previous", "forecast",
    "te_forecast", "ticker", "source",
]


def save_csv(events: list[dict[str, Any]], output_path: str) -> None:
    """Save events to CSV file."""
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(events)

    logger.info("Saved to %s (%d rows)", output_path, len(events))


def print_summary(events: list[dict[str, Any]]) -> None:
    """Print event count summary."""
    by_country: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for ev in events:
        c = ev.get("country", "?")
        by_country[c] = by_country.get(c, 0) + 1
        name = ev.get("event_name", "?")
        # Simplify name for grouping
        for key in ["CPI", "GDP", "NFP", "Non Farm", "Employment", "Interest Rate",
                     "FOMC", "ECB", "BOE", "BOJ", "RBA", "RBNZ", "BOC", "SNB",
                     "ISM", "Retail Sales", "PMI"]:
            if key.lower() in name.lower():
                by_type[key] = by_type.get(key, 0) + 1
                break
        else:
            by_type["Other"] = by_type.get("Other", 0) + 1

    logger.info("Events by country:")
    for c, count in sorted(by_country.items(), key=lambda x: -x[1]):
        logger.info("  %s: %d", c, count)

    logger.info("Events by type:")
    for t, count in sorted(by_type.items(), key=lambda x: -x[1]):
        logger.info("  %s: %d", t, count)


# ── Database loading ──────────────────────────────────────────────────────────

async def load_to_db(events: list[dict[str, Any]], dry_run: bool = False) -> None:
    """Insert parsed events into the economic_events database table."""
    if dry_run:
        logger.info("[DRY RUN] Would store %d events:", len(events))
        for ev in events[:30]:
            logger.info(
                "[DRY RUN]  %s %s | %-2s | %-3s | %s",
                ev["date"], ev.get("time_utc", "??:??"),
                ev["country"], ev["currency"],
                ev["event_name"],
            )
        if len(events) > 30:
            logger.info("[DRY RUN]  ... and %d more", len(events) - 30)
        return

    from src.database.crud import upsert_economic_event
    from src.database.engine import async_session_factory

    inserted = 0
    failed = 0

    async with async_session_factory() as session:
        async with session.begin():
            for ev in events:
                try:
                    db_record = {
                        "event_date": ev["event_date"],
                        "country": ev["country"],
                        "currency": ev["currency"],
                        "event_name": ev["event_name"],
                        "impact": ev["impact"],
                        "source": ev.get("source", "Manual"),
                    }
                    for src_field, db_field in [("actual", "actual"), ("previous", "previous"), ("forecast", "estimate")]:
                        val = ev.get(src_field)
                        if val and val not in ("", "None"):
                            clean = val.replace("%", "").replace("$", "").replace("K", "").replace("M", "").replace("B", "").strip()
                            try:
                                from decimal import Decimal
                                db_record[db_field] = Decimal(clean)
                            except Exception:
                                pass

                    await upsert_economic_event(session, db_record)
                    inserted += 1
                except Exception as exc:
                    logger.error("Failed to store %s / %s: %s", ev["event_name"], ev["date"], exc)
                    failed += 1

    logger.info("Database load complete. Inserted/updated: %d, failed: %d", inserted, failed)


# ── CSV loading ───────────────────────────────────────────────────────────────

def load_csv(path: str) -> list[dict[str, Any]]:
    """Load events from an existing CSV file."""
    events = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date_str = f"{row['date']} {row.get('time_utc', '00:00')}"
            try:
                event_dt = datetime.datetime.strptime(date_str, "%Y-%m-%d %H:%M")
                event_dt = event_dt.replace(tzinfo=datetime.timezone.utc)
            except ValueError:
                event_dt = datetime.datetime.strptime(row["date"], "%Y-%m-%d")
                event_dt = event_dt.replace(tzinfo=datetime.timezone.utc)
            row["event_date"] = event_dt
            events.append(row)
    logger.info("Loaded %d events from %s", len(events), path)
    return events


# ── Entry point ───────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download HIGH-impact economic calendar (hybrid: TE API for US + manual for others)."
    )
    parser.add_argument("--from", dest="date_from", default=DEFAULT_FROM,
                        help=f"Start date YYYY-MM-DD (default: {DEFAULT_FROM})")
    parser.add_argument("--to", dest="date_to", default=DEFAULT_TO,
                        help=f"End date YYYY-MM-DD (default: {DEFAULT_TO})")
    parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT,
                        help=f"Output CSV file path (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--us-only", action="store_true",
                        help="Only download US events from TE API (skip manual non-US)")
    parser.add_argument("--manual-only", action="store_true",
                        help="Only use manual events (skip TE API download)")
    parser.add_argument("--load-db", action="store_true",
                        help="Also load events into the PostgreSQL economic_events table")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --load-db: print events instead of writing to DB")
    parser.add_argument("--csv-only", action="store_true",
                        help="Only load from existing CSV (skip download). Use with --load-db")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    if args.csv_only:
        if not os.path.exists(args.output):
            logger.error("CSV file not found: %s", args.output)
            sys.exit(1)
        all_events = load_csv(args.output)
    else:
        all_events: list[dict[str, Any]] = []
        seen_keys: set[tuple] = set()

        # Step 1: Download US events from TE API
        if not args.manual_only:
            us_events = download_us_events(args.date_from, args.date_to)
            for ev in us_events:
                key = (ev["date"], ev["country"], ev["event_name"])
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_events.append(ev)

        # Step 2: Add manual non-US events
        if not args.us_only:
            manual_events = build_manual_events()
            # Filter by date range
            start_date = args.date_from
            end_date = args.date_to
            for ev in manual_events:
                if start_date <= ev["date"] <= end_date:
                    key = (ev["date"], ev["country"], ev["event_name"])
                    if key not in seen_keys:
                        seen_keys.add(key)
                        all_events.append(ev)

        # Sort by date
        all_events.sort(key=lambda e: e["event_date"])

        logger.info("Total events: %d", len(all_events))
        print_summary(all_events)

        # Save to CSV
        save_csv(all_events, args.output)

    if args.load_db:
        asyncio.run(load_to_db(all_events, dry_run=args.dry_run))
