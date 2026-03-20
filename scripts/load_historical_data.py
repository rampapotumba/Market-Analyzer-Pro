"""
Load historical macro data from free APIs into the database.

Sources:
  1. Fear & Greed Index  — Alternative.me API (730 days, no API key)
  2. DXY H1/D1 candles   — yfinance, symbol DX-Y.NYB (stored in price_data)
  3. Funding rates       — Binance REST API, BTC/USDT + ETH/USDT (2024-2025)
  4. COT data            — CFTC ZIP files, 2024 + 2025, all weekly records

All operations are idempotent (ON CONFLICT DO NOTHING / upsert).

Usage:
    python scripts/load_historical_data.py
    python scripts/load_historical_data.py --sources fear_greed dxy funding_rates cot
"""

import argparse
import asyncio
import csv
import datetime
import io
import logging
import sys
import time
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import httpx

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=730&format=json"

DXY_YFINANCE_SYMBOL = "DX-Y.NYB"
DXY_INSTRUMENT_SYMBOL = "DX-Y.NYB"
DXY_TIMEFRAMES = ["H1", "D1"]

BINANCE_FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
# Maps Binance symbol → indicator_name stored in macro_data.
# Must match names used in market_context_collector.py and correlation_engine.py.
FUNDING_SYMBOLS = {
    "BTCUSDT": "FUNDING_RATE_BTC",
    "ETHUSDT": "FUNDING_RATE_ETH",
}
FUNDING_START_MS = int(
    datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000
)
FUNDING_END_MS = int(
    datetime.datetime(2025, 12, 31, 23, 59, 59, tzinfo=datetime.timezone.utc).timestamp() * 1000
)

CFTC_ZIP_URL = "https://www.cftc.gov/files/dea/history/deacot{year}.zip"
COT_YEARS = [2024, 2025]
COT_MARKETS = {
    "EURO FX - CHICAGO MERCANTILE EXCHANGE": "EURUSD=X",
    "BRITISH POUND - CHICAGO MERCANTILE EXCHANGE": "GBPUSD=X",
    "JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE": "USDJPY=X",
    "AUSTRALIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE": "AUDUSD=X",
    "CANADIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE": "USDCAD=X",
    "NEW ZEALAND DOLLAR - CHICAGO MERCANTILE EXCHANGE": "NZDUSD=X",
    "SWISS FRANC - CHICAGO MERCANTILE EXCHANGE": "USDCHF=X",
    "BITCOIN - CHICAGO MERCANTILE EXCHANGE": "BTC/USDT",
    "E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE": "SPY",
}

YFINANCE_TF_MAP = {"H1": "1h", "D1": "1d"}
YFINANCE_MAX_DAYS = {"H1": 729, "D1": 3650}

# ── Summary counters ───────────────────────────────────────────────────────────

SUMMARY: dict[str, dict[str, int]] = {}


# ── Fear & Greed ───────────────────────────────────────────────────────────────


async def load_fear_greed() -> None:
    """Fetch 730 days of Fear & Greed Index and store in macro_data."""
    from src.database.crud import upsert_macro_data
    from src.database.engine import async_session_factory

    logger.info("[FearGreed] Fetching from alternative.me ...")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(FEAR_GREED_URL)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.error("[FearGreed] HTTP error: %s", exc)
        SUMMARY["fear_greed"] = {"fetched": 0, "stored": 0, "error": 1}
        return

    entries = data.get("data", [])
    if not entries:
        logger.warning("[FearGreed] Empty response from API")
        SUMMARY["fear_greed"] = {"fetched": 0, "stored": 0}
        return

    records: list[dict[str, Any]] = []
    for item in entries:
        try:
            ts = datetime.datetime.fromtimestamp(
                int(item["timestamp"]), tz=datetime.timezone.utc
            )
            # Normalise to midnight UTC so each day is one record
            ts = ts.replace(hour=0, minute=0, second=0, microsecond=0)
            value = Decimal(str(int(item["value"])))
            records.append(
                {
                    "indicator_name": "FEAR_GREED",
                    "country": "GLOBAL",
                    "value": value,
                    "release_date": ts,
                    "source": "alternative.me",
                }
            )
        except (KeyError, ValueError) as exc:
            logger.warning("[FearGreed] Skipping invalid row: %s", exc)

    stored = 0
    if records:
        try:
            async with async_session_factory() as db:
                async with db.begin():
                    stored = await upsert_macro_data(db, records)
        except Exception as exc:
            logger.error("[FearGreed] DB error: %s", exc)

    logger.info("[FearGreed] fetched=%d stored=%d", len(records), stored)
    SUMMARY["fear_greed"] = {"fetched": len(records), "stored": stored}


# ── DXY price data ─────────────────────────────────────────────────────────────


async def _fetch_dxy_yfinance(timeframe: str) -> "pd.DataFrame":
    import pandas as pd
    import yfinance as yf

    interval = YFINANCE_TF_MAP[timeframe]
    max_days = YFINANCE_MAX_DAYS[timeframe]

    start = datetime.date.today() - datetime.timedelta(days=max_days - 1)
    end = datetime.date.today()

    loop = asyncio.get_event_loop()

    def _dl() -> "pd.DataFrame":
        return yf.Ticker(DXY_YFINANCE_SYMBOL).history(
            start=start.isoformat(),
            end=(end + datetime.timedelta(days=1)).isoformat(),
            interval=interval,
            auto_adjust=True,
        )

    df = await loop.run_in_executor(None, _dl)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def _df_to_price_records(df: "pd.DataFrame", instrument_id: int, timeframe: str) -> list[dict]:
    import pandas as pd

    records = []
    for ts, row in df.iterrows():
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        elif hasattr(ts, "tzinfo") and ts.tzinfo is not None:
            ts = ts.tz_convert("UTC")
        open_v = row.get("Open", row.get("open", 0))
        high_v = row.get("High", row.get("high", 0))
        low_v = row.get("Low", row.get("low", 0))
        close_v = row.get("Close", row.get("close", 0))
        volume_v = row.get("Volume", row.get("volume", 0)) or 0
        records.append(
            {
                "instrument_id": instrument_id,
                "timeframe": timeframe,
                "timestamp": ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                "open": Decimal(str(open_v)),
                "high": Decimal(str(high_v)),
                "low": Decimal(str(low_v)),
                "close": Decimal(str(close_v)),
                "volume": Decimal(str(volume_v)),
            }
        )
    return records


async def load_dxy() -> None:
    """Register DXY instrument if needed and load H1/D1 candles into price_data."""
    from src.database.crud import bulk_upsert_price_data, get_or_create_instrument
    from src.database.engine import async_session_factory

    # Ensure instrument exists
    async with async_session_factory() as db:
        async with db.begin():
            instrument, created = await get_or_create_instrument(
                db,
                symbol=DXY_INSTRUMENT_SYMBOL,
                market="index",
                name="US Dollar Index",
                pip_size=Decimal("0.001"),
            )
            instrument_id = instrument.id

    if created:
        logger.info("[DXY] Created instrument %s (id=%d)", DXY_INSTRUMENT_SYMBOL, instrument_id)
    else:
        logger.info("[DXY] Instrument exists (id=%d)", instrument_id)

    total_stored = 0
    total_fetched = 0

    for tf in DXY_TIMEFRAMES:
        try:
            df = await _fetch_dxy_yfinance(tf)
            if df is None or df.empty:
                logger.warning("[DXY] No data for timeframe %s", tf)
                continue

            records = _df_to_price_records(df, instrument_id, tf)
            total_fetched += len(records)

            # Chunk inserts to respect PostgreSQL parameter limit
            chunk_size = 3000
            stored = 0
            for i in range(0, len(records), chunk_size):
                chunk = records[i : i + chunk_size]
                async with async_session_factory() as db:
                    async with db.begin():
                        stored += await bulk_upsert_price_data(db, chunk)

            total_stored += stored
            logger.info(
                "[DXY] %s: fetched=%d stored=%d  %s → %s",
                tf,
                len(records),
                stored,
                df.index[0].date(),
                df.index[-1].date(),
            )
            await asyncio.sleep(1.0)

        except Exception as exc:
            logger.error("[DXY] Error loading %s: %s", tf, exc)

    logger.info("[DXY] Total: fetched=%d stored=%d", total_fetched, total_stored)
    SUMMARY["dxy"] = {"fetched": total_fetched, "stored": total_stored}


# ── Binance Funding Rates ──────────────────────────────────────────────────────


async def _fetch_funding_page(
    client: httpx.AsyncClient,
    binance_symbol: str,
    start_ms: int,
    end_ms: int,
) -> list[dict]:
    """Fetch one page (up to 1000) of funding rate records from Binance."""
    params = {
        "symbol": binance_symbol,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": 1000,
    }
    resp = await client.get(BINANCE_FUNDING_URL, params=params)
    resp.raise_for_status()
    return resp.json()


async def load_funding_rates() -> None:
    """Load historical Binance funding rates (2024-2025) into macro_data."""
    from src.database.crud import upsert_macro_data
    from src.database.engine import async_session_factory

    # 8 hours in ms — funding rate interval
    interval_ms = 8 * 3600 * 1000

    all_stored = 0
    all_fetched = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        for binance_sym, indicator_name in FUNDING_SYMBOLS.items():
            records: list[dict[str, Any]] = []

            # Paginate through full 2024-2025 range
            current_start = FUNDING_START_MS
            while current_start < FUNDING_END_MS:
                try:
                    page = await _fetch_funding_page(
                        client, binance_sym, current_start, FUNDING_END_MS
                    )
                except Exception as exc:
                    logger.error("[Funding] %s page error: %s", binance_sym, exc)
                    break

                if not page:
                    break

                for item in page:
                    try:
                        funding_time_ms = int(item["fundingTime"])
                        ts = datetime.datetime.fromtimestamp(
                            funding_time_ms / 1000, tz=datetime.timezone.utc
                        )
                        rate = Decimal(str(item["fundingRate"]))
                        records.append(
                            {
                                "indicator_name": indicator_name,
                                "country": "GLOBAL",
                                "value": rate,
                                "release_date": ts,
                                "source": "binance",
                            }
                        )
                    except (KeyError, ValueError) as exc:
                        logger.warning("[Funding] Skipping row: %s", exc)

                last_ts = int(page[-1]["fundingTime"])
                if len(page) < 1000 or last_ts >= FUNDING_END_MS:
                    break
                current_start = last_ts + interval_ms
                await asyncio.sleep(0.2)

            # Store this symbol's records
            stored = 0
            if records:
                try:
                    async with async_session_factory() as db:
                        async with db.begin():
                            stored = await upsert_macro_data(db, records)
                except Exception as exc:
                    logger.error("[Funding] DB error for %s: %s", indicator_name, exc)

            logger.info(
                "[Funding] %s: fetched=%d stored=%d", indicator_name, len(records), stored
            )
            all_fetched += len(records)
            all_stored += stored
            await asyncio.sleep(0.5)

    logger.info("[Funding] Total: fetched=%d stored=%d", all_fetched, all_stored)
    SUMMARY["funding_rates"] = {"fetched": all_fetched, "stored": all_stored}


# ── COT Historical Data ────────────────────────────────────────────────────────


async def _download_cot_zip(year: int) -> Optional[bytes]:
    """Download CFTC annual COT ZIP. Returns bytes or None."""
    url = CFTC_ZIP_URL.format(year=year)
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.get(url, follow_redirects=True)
            if resp.status_code == 200:
                logger.info("[COT] Downloaded %s (%d KB)", url, len(resp.content) // 1024)
                return resp.content
            logger.warning("[COT] ZIP not found for %d: HTTP %d", year, resp.status_code)
            return None
    except Exception as exc:
        logger.error("[COT] Download error for %d: %s", year, exc)
        return None


def _parse_cot_zip_all_rows(zip_bytes: bytes) -> list[dict[str, Any]]:
    """Parse COT CSV and return ALL matching rows (not just latest per market).

    Unlike the production COTCollector which keeps only the latest snapshot,
    this function returns every weekly row so we can build a full history.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            txt_files = [f for f in zf.namelist() if f.lower().endswith((".txt", ".csv"))]
            if not txt_files:
                logger.warning("[COT] No TXT/CSV in ZIP")
                return []
            with zf.open(txt_files[0]) as f:
                content = f.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.error("[COT] ZIP read error: %s", exc)
        return []

    lines = content.splitlines()
    if not lines:
        return []

    reader = csv.reader(lines)
    try:
        header = [h.strip().lower() for h in next(reader)]
    except StopIteration:
        return []

    def col(name: str) -> int:
        try:
            return header.index(name)
        except ValueError:
            return -1

    market_col = col("market and exchange names")
    date_col = col("as of date in form yyyy-mm-dd")
    if date_col == -1:
        date_col = col("as of date in form yymmdd")
    long_col = col("noncommercial positions-long (all)")
    short_col = col("noncommercial positions-short (all)")

    if market_col == -1 or long_col == -1 or short_col == -1:
        logger.warning("[COT] Required columns not found. Header: %s", header[:8])
        return []

    rows_out: list[dict[str, Any]] = []
    for row in reader:
        if not row or len(row) <= max(market_col, long_col, short_col):
            continue
        market_name = row[market_col].strip().upper()

        for exact_key, our_symbol in COT_MARKETS.items():
            if market_name == exact_key:
                try:
                    longs = float(row[long_col].strip().replace(",", "") or 0)
                    shorts = float(row[short_col].strip().replace(",", "") or 0)
                    date_str = (
                        row[date_col].strip()
                        if date_col >= 0 and date_col < len(row)
                        else ""
                    )
                    rows_out.append(
                        {
                            "symbol": our_symbol,
                            "market": market_name,
                            "net": longs - shorts,
                            "date_str": date_str,
                        }
                    )
                except (ValueError, IndexError) as exc:
                    logger.debug("[COT] Row parse error: %s", exc)
                break

    return rows_out


def _parse_cot_date(date_str: str) -> Optional[datetime.datetime]:
    """Parse COT date string to UTC datetime. Returns None on failure."""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%y%m%d", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(date_str, fmt).replace(
                tzinfo=datetime.timezone.utc
            )
        except ValueError:
            continue
    return None


async def load_cot() -> None:
    """Load full COT history for 2024 and 2025 into macro_data."""
    from src.database.crud import upsert_macro_data
    from src.database.engine import async_session_factory

    all_records: list[dict[str, Any]] = []

    for year in COT_YEARS:
        zip_bytes = await _download_cot_zip(year)
        if not zip_bytes:
            logger.warning("[COT] Skipping year %d — download failed", year)
            continue

        rows = _parse_cot_zip_all_rows(zip_bytes)
        logger.info("[COT] %d: parsed %d rows from ZIP", year, len(rows))

        skipped = 0
        for item in rows:
            release_dt = _parse_cot_date(item["date_str"])
            if release_dt is None:
                skipped += 1
                continue
            all_records.append(
                {
                    "indicator_name": f"COT_NET_{item['symbol']}",
                    "country": "US",
                    "value": Decimal(str(round(item["net"], 0))),
                    "release_date": release_dt,
                    "source": "CFTC",
                }
            )
        if skipped:
            logger.warning("[COT] %d rows skipped (invalid date) in year %d", skipped, year)

    stored = 0
    if all_records:
        # Deduplicate in-memory before sending to DB
        # (same indicator_name + release_date can appear in both yearly ZIPs
        # if CFTC overlaps a few weeks)
        seen: set[tuple[str, datetime.datetime]] = set()
        unique: list[dict[str, Any]] = []
        for r in all_records:
            key = (r["indicator_name"], r["release_date"])
            if key not in seen:
                seen.add(key)
                unique.append(r)

        logger.info(
            "[COT] Sending %d unique records to DB (%d total, %d dupes removed)",
            len(unique),
            len(all_records),
            len(all_records) - len(unique),
        )
        try:
            async with async_session_factory() as db:
                async with db.begin():
                    stored = await upsert_macro_data(db, unique)
        except Exception as exc:
            logger.error("[COT] DB error: %s", exc)
        all_records = unique

    logger.info("[COT] Total: fetched=%d stored=%d", len(all_records), stored)
    SUMMARY["cot"] = {"fetched": len(all_records), "stored": stored}


# ── upsert_macro_data helper (if not in crud) ─────────────────────────────────
# We rely on the existing crud.upsert_macro_data. Let's verify it exists first.


async def _ensure_upsert_macro_data_exists() -> bool:
    """Quick check that upsert_macro_data is importable from crud."""
    try:
        from src.database.crud import upsert_macro_data  # noqa: F401

        return True
    except ImportError:
        return False


# ── Main ───────────────────────────────────────────────────────────────────────

SOURCE_FUNCS = {
    "fear_greed": load_fear_greed,
    "dxy": load_dxy,
    "funding_rates": load_funding_rates,
    "cot": load_cot,
}


async def main(sources: list[str]) -> None:
    # Verify upsert_macro_data exists
    if not await _ensure_upsert_macro_data_exists():
        logger.error("crud.upsert_macro_data not found — aborting")
        sys.exit(1)

    t0 = time.time()
    logger.info("=" * 60)
    logger.info("Loading historical data: %s", ", ".join(sources))
    logger.info("=" * 60)

    for source in sources:
        if source not in SOURCE_FUNCS:
            logger.warning("Unknown source '%s' — skipped", source)
            continue
        logger.info("")
        logger.info("── %s ──────────────────────────────────────────────", source.upper())
        try:
            await SOURCE_FUNCS[source]()
        except Exception as exc:
            logger.error("[%s] Unexpected error: %s", source, exc)
            SUMMARY[source] = {"error": str(exc)}

    elapsed = time.time() - t0
    logger.info("")
    logger.info("=" * 60)
    logger.info("SUMMARY (%.1fs)", elapsed)
    logger.info("=" * 60)
    for source, stats in SUMMARY.items():
        logger.info("  %-16s %s", source, stats)
    logger.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Load historical macro/price data from free APIs"
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        default=list(SOURCE_FUNCS.keys()),
        choices=list(SOURCE_FUNCS.keys()),
        help="Which data sources to load (default: all)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.sources))
