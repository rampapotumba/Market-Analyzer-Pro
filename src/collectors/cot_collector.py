"""COT (Commitment of Traders) report collector from CFTC."""

import csv
import datetime
import io
import logging
import zipfile
from decimal import Decimal
from typing import Any, Optional

import httpx

from src.collectors.base import BaseCollector, CollectorResult

logger = logging.getLogger(__name__)

# CFTC legacy combined futures+options ZIP file (text/CSV format)
# Contains forex, indices, crypto — the markets we care about
CFTC_LEGACY_URL = "https://www.cftc.gov/files/dea/history/deacot{year}.zip"

# Exact market name prefixes to match → our instrument symbol
# Using exact prefix matching to avoid false positives (e.g., "MICRO BITCOIN")
COT_MARKETS = {
    "EURO FX - CHICAGO MERCANTILE EXCHANGE": "EURUSD=X",
    "BRITISH POUND - CHICAGO MERCANTILE EXCHANGE": "GBPUSD=X",
    "JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE": "USDJPY=X",
    "BITCOIN - CHICAGO MERCANTILE EXCHANGE": "BTC/USDT",
    "E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE": "SPY",
}


class COTCollector(BaseCollector):
    """Collects Commitment of Traders (COT) data from CFTC annual ZIP files."""

    def __init__(self) -> None:
        super().__init__("COTCollector")

    async def _fetch_zip(self, year: int) -> Optional[bytes]:
        """Download CFTC annual COT ZIP file. Returns raw bytes or None."""
        url = CFTC_LEGACY_URL.format(year=year)
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(url, follow_redirects=True)
                if response.status_code == 200:
                    return response.content
                logger.debug(f"[COTCollector] ZIP not found for {year}: {response.status_code}")
                return None
        except Exception as exc:
            logger.warning(f"[COTCollector] Failed to download ZIP for {year}: {exc}")
            return None

    def _parse_zip(self, zip_bytes: bytes) -> list[dict[str, Any]]:
        """Parse COT CSV from ZIP file. Returns list of latest records per market."""
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                txt_files = [f for f in zf.namelist() if f.lower().endswith(('.txt', '.csv'))]
                if not txt_files:
                    logger.warning("[COTCollector] No TXT/CSV in ZIP")
                    return []
                with zf.open(txt_files[0]) as f:
                    content = f.read().decode('utf-8', errors='replace')

            lines = content.splitlines()
            if not lines:
                return []

            # Use csv.reader to properly handle quoted fields
            reader = csv.reader(lines)
            header = [h.strip().lower() for h in next(reader)]

            def col(name: str) -> int:
                try:
                    return header.index(name)
                except ValueError:
                    return -1

            # Legacy format column names
            market_col = col('market and exchange names')
            date_col = col('as of date in form yyyy-mm-dd')
            if date_col == -1:
                date_col = col('as of date in form yymmdd')
            long_col = col('noncommercial positions-long (all)')
            short_col = col('noncommercial positions-short (all)')

            if market_col == -1 or long_col == -1 or short_col == -1:
                logger.warning(f"[COTCollector] Column not found. Header sample: {header[:8]}")
                return []

            # Collect most recent record per matched market (CSV is newest-first)
            latest: dict[str, dict[str, Any]] = {}
            for row in reader:
                if not row or len(row) <= max(market_col, long_col, short_col):
                    continue
                market_name = row[market_col].strip().upper()

                for exact_key, our_symbol in COT_MARKETS.items():
                    if market_name == exact_key:
                        if our_symbol not in latest:
                            try:
                                longs = float(row[long_col].strip().replace(',', '') or 0)
                                shorts = float(row[short_col].strip().replace(',', '') or 0)
                                date_str = row[date_col].strip() if date_col >= 0 and date_col < len(row) else ""
                                latest[our_symbol] = {
                                    "symbol": our_symbol,
                                    "market": market_name,
                                    "net": longs - shorts,
                                    "date_str": date_str,
                                }
                            except (ValueError, IndexError):
                                pass
                        break

            return list(latest.values())

        except Exception as exc:
            logger.error(f"[COTCollector] Parse error: {exc}")
            return []

    def _parse_date(self, date_str: str) -> datetime.datetime:
        """Parse date string to UTC datetime."""
        now = datetime.datetime.now(datetime.timezone.utc)
        if not date_str:
            return now
        for fmt in ("%Y-%m-%d", "%y%m%d", "%m/%d/%Y"):
            try:
                return datetime.datetime.strptime(date_str, fmt).replace(
                    tzinfo=datetime.timezone.utc
                )
            except ValueError:
                continue
        return now

    async def collect(self) -> CollectorResult:
        """Collect COT data. Tries current year, then previous year as fallback."""
        from src.database.crud import upsert_macro_data
        from src.database.engine import async_session_factory

        current_year = datetime.datetime.now().year
        zip_bytes = await self._fetch_zip(current_year)
        if not zip_bytes:
            zip_bytes = await self._fetch_zip(current_year - 1)
        if not zip_bytes:
            logger.warning("[COTCollector] Could not download COT data — skipping")
            return CollectorResult(success=True, records_count=0, data=[])

        parsed = self._parse_zip(zip_bytes)
        if not parsed:
            logger.warning("[COTCollector] No matching markets found in COT data")
            return CollectorResult(success=True, records_count=0, data=[])

        records = []
        for item in parsed:
            release_dt = self._parse_date(item["date_str"])
            indicator_name = f"COT_NET_{item['symbol']}"
            records.append({
                "indicator_name": indicator_name,
                "country": "US",
                "value": Decimal(str(round(item["net"], 0))),
                "release_date": release_dt,
                "source": "CFTC",
            })
            logger.debug(
                f"[COTCollector] {item['market'][:50]}: net={item['net']:,.0f}"
            )

        stored = 0
        if records:
            try:
                async with async_session_factory() as db:
                    async with db.begin():
                        stored = await upsert_macro_data(db, records)
                logger.info(f"[COTCollector] Stored {stored}/{len(records)} COT records")
            except Exception as exc:
                logger.error(f"[COTCollector] DB storage failed: {exc}")

        return CollectorResult(
            success=True,
            data=records,
            records_count=len(records),
        )

    async def health_check(self) -> bool:
        year = datetime.datetime.now().year
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.head(
                    CFTC_LEGACY_URL.format(year=year), follow_redirects=True
                )
                return r.status_code in (200, 302, 301)
        except Exception:
            return False


def get_cot_fa_adjustment(
    net_positions: Optional[float],
    change_week: Optional[float],
) -> float:
    """SIM-41: Calculate FA score adjustment from COT data.

    Non-commercials net long + increasing → +5
    Non-commercials net short + increasing (more negative) → -5
    No data → 0
    """
    if net_positions is None or change_week is None:
        return 0.0

    if net_positions > 0 and change_week > 0:
        return 5.0   # net long and growing → bullish
    if net_positions < 0 and change_week < 0:
        return -5.0  # net short and growing → bearish
    return 0.0
