"""
Fetch historical price data for all instruments and store in price_data table.

Sources:
  - yfinance  → forex, stocks  (H1 up to 730 days, H4/D1 unlimited)
  - CCXT      → crypto         (H1/H4/D1 from any date, paginated 500 candles/request)

Usage:
  python scripts/fetch_historical.py
  python scripts/fetch_historical.py --timeframes H1 H4 D1
  python scripts/fetch_historical.py --symbols EURUSD=X BTC/USDT
  python scripts/fetch_historical.py --start 2025-01-01
"""

import argparse
import asyncio
import datetime
import logging
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Optional

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

CCXT_TF_MAP = {
    "M5": "5m", "M15": "15m", "M30": "30m",
    "H1": "1h", "H4": "4h", "D1": "1d",
}
YFINANCE_TF_MAP = {
    "M5": "5m", "M15": "15m", "M30": "30m",
    "H1": "1h", "H4": "4h", "D1": "1d",
}
# yfinance max lookback per timeframe (days)
YFINANCE_MAX_DAYS = {
    "M5": 60, "M15": 60, "M30": 60,
    "H1": 729, "H4": 729, "D1": 3650,
}


def _df_to_records(df: pd.DataFrame, instrument_id: int, timeframe: str) -> list[dict]:
    records = []
    for ts, row in df.iterrows():
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        elif hasattr(ts, "tzinfo") and ts.tzinfo is not None:
            ts = ts.tz_convert("UTC")
        records.append({
            "instrument_id": instrument_id,
            "timeframe": timeframe,
            "timestamp": ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
            "open":   Decimal(str(row.get("Open",   row.get("open",  0)))),
            "high":   Decimal(str(row.get("High",   row.get("high",  0)))),
            "low":    Decimal(str(row.get("Low",    row.get("low",   0)))),
            "close":  Decimal(str(row.get("Close",  row.get("close", 0)))),
            "volume": Decimal(str(row.get("Volume", row.get("volume",0)) or 0)),
        })
    return records


async def fetch_yfinance(symbol: str, timeframe: str, start: datetime.date, end: datetime.date) -> pd.DataFrame:
    import yfinance as yf

    interval = YFINANCE_TF_MAP[timeframe]
    max_days = YFINANCE_MAX_DAYS.get(timeframe, 729)
    # Clamp start to max lookback
    min_start = datetime.date.today() - datetime.timedelta(days=max_days)
    if start < min_start:
        logger.warning(f"[yfinance] {symbol} {timeframe}: clamping start {start} → {min_start}")
        start = min_start

    def _dl():
        return yf.Ticker(symbol).history(
            start=start.isoformat(),
            end=(end + datetime.timedelta(days=1)).isoformat(),
            interval=interval,
            auto_adjust=True,
        )

    loop = asyncio.get_event_loop()
    df = await loop.run_in_executor(None, _dl)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


async def fetch_ccxt_paginated(symbol: str, timeframe: str, start: datetime.date, end: datetime.date) -> pd.DataFrame:
    import ccxt

    exchange = ccxt.binance({"enableRateLimit": True})
    ccxt_tf = CCXT_TF_MAP[timeframe]

    # Timeframe in ms
    tf_ms = {
        "M5": 5*60*1000, "M15": 15*60*1000, "M30": 30*60*1000,
        "H1": 3600*1000, "H4": 4*3600*1000, "D1": 86400*1000,
    }[timeframe]

    since_ms = int(datetime.datetime(start.year, start.month, start.day, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    end_ms   = int(datetime.datetime(end.year,   end.month,   end.day,   23, 59, 59, tzinfo=datetime.timezone.utc).timestamp() * 1000)

    all_rows = []
    loop = asyncio.get_event_loop()

    while since_ms < end_ms:
        def _fetch(s=since_ms):
            return exchange.fetch_ohlcv(symbol, ccxt_tf, since=s, limit=500)

        rows = await loop.run_in_executor(None, _fetch)
        if not rows:
            break

        # Filter rows beyond end
        rows = [r for r in rows if r[0] <= end_ms]
        all_rows.extend(rows)

        last_ts = rows[-1][0]
        if last_ts >= end_ms or len(rows) < 500:
            break
        since_ms = last_ts + tf_ms
        await asyncio.sleep(0.3)  # respect rate limit

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df = df[~df.index.duplicated(keep="last")]
    return df


async def fetch_and_store(
    symbol: str,
    market: str,
    instrument_id: int,
    timeframe: str,
    start: datetime.date,
    end: datetime.date,
) -> int:
    from src.collectors.price_collector import _df_to_records as _r
    from src.database.crud import bulk_upsert_price_data
    from src.database.engine import async_session_factory

    try:
        if market == "crypto":
            df = await fetch_ccxt_paginated(symbol, timeframe, start, end)
        else:
            df = await fetch_yfinance(symbol, timeframe, start, end)

        if df is None or df.empty:
            logger.warning(f"  {symbol} {timeframe}: no data")
            return 0

        records = _df_to_records(df, instrument_id, timeframe)
        if not records:
            return 0

        # Chunk to avoid PostgreSQL 32767-parameter limit (8 fields × ~4000 rows)
        chunk_size = 3000
        count = 0
        for i in range(0, len(records), chunk_size):
            chunk = records[i:i + chunk_size]
            async with async_session_factory() as session:
                async with session.begin():
                    count += await bulk_upsert_price_data(session, chunk)

        logger.info(f"  ✓ {symbol:15s} {timeframe}  {count:5d} candles  "
                    f"{df.index[0].date()} → {df.index[-1].date()}")
        return count

    except Exception as exc:
        logger.error(f"  ✗ {symbol} {timeframe}: {exc}")
        return 0


async def main(
    timeframes: list[str],
    symbols_filter: Optional[list[str]],
    start: datetime.date,
    end: datetime.date,
) -> None:
    from src.database.crud import get_all_instruments
    from src.database.engine import async_session_factory

    async with async_session_factory() as session:
        instruments = await get_all_instruments(session)

    if symbols_filter:
        instruments = [i for i in instruments if i.symbol in symbols_filter]

    total_candles = 0
    t0 = time.time()

    logger.info(f"Fetching {len(instruments)} instruments × {timeframes} from {start} to {end}")
    logger.info("─" * 60)

    for inst in instruments:
        for tf in timeframes:
            count = await fetch_and_store(
                inst.symbol, inst.market, inst.id, tf, start, end
            )
            total_candles += count
            await asyncio.sleep(0.5)  # be polite to APIs

    elapsed = time.time() - t0
    logger.info("─" * 60)
    logger.info(f"Done. {total_candles:,} candles stored in {elapsed:.1f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch historical price data")
    parser.add_argument(
        "--timeframes", nargs="+", default=["H1", "H4", "D1"],
        choices=["M5", "M15", "M30", "H1", "H4", "D1"],
    )
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument(
        "--start", default="2024-01-01",
        help="Start date YYYY-MM-DD (yfinance H1 limited to last 730 days)",
    )
    parser.add_argument("--end", default=datetime.date.today().isoformat())
    args = parser.parse_args()

    asyncio.run(main(
        timeframes=args.timeframes,
        symbols_filter=args.symbols,
        start=datetime.date.fromisoformat(args.start),
        end=datetime.date.fromisoformat(args.end),
    ))
