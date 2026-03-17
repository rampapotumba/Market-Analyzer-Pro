"""Price data collectors: YFinance (stocks/forex) and CCXT (crypto)."""

import asyncio
import datetime
import logging
from decimal import Decimal
from typing import Any, Optional

import pandas as pd
import yfinance as yf

from src.collectors.base import BaseCollector, CollectorResult
from src.database.crud import bulk_upsert_price_data, get_instrument_by_symbol
from src.database.engine import async_session_factory

logger = logging.getLogger(__name__)

# Timeframe mapping: our format → yfinance interval
YFINANCE_TF_MAP: dict[str, str] = {
    "M1": "1m",
    "M5": "5m",
    "M15": "15m",
    "H1": "1h",
    "H4": "4h",
    "D1": "1d",
    "W1": "1wk",
    "MN1": "1mo",
}

# Timeframe mapping: our format → CCXT timeframe
CCXT_TF_MAP: dict[str, str] = {
    "M1": "1m",
    "M5": "5m",
    "M15": "15m",
    "H1": "1h",
    "H4": "4h",
    "D1": "1d",
    "W1": "1w",
    "MN1": "1M",
}


def _df_to_records(df: pd.DataFrame, instrument_id: int, timeframe: str) -> list[dict[str, Any]]:
    """Convert OHLCV DataFrame to list of dicts for DB insertion."""
    records = []
    for ts, row in df.iterrows():
        # Ensure timezone-aware
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        elif hasattr(ts, "tzinfo") and ts.tzinfo is not None:
            ts = ts.tz_convert("UTC")

        records.append({
            "instrument_id": instrument_id,
            "timeframe": timeframe,
            "timestamp": ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
            "open": Decimal(str(row.get("Open", row.get("open", 0)))),
            "high": Decimal(str(row.get("High", row.get("high", 0)))),
            "low": Decimal(str(row.get("Low", row.get("low", 0)))),
            "close": Decimal(str(row.get("Close", row.get("close", 0)))),
            "volume": Decimal(str(row.get("Volume", row.get("volume", 0)) or 0)),
        })
    return records


class YFinanceCollector(BaseCollector):
    """Collects price data for stocks and forex using yfinance."""

    def __init__(self) -> None:
        super().__init__("YFinance")

    async def _fetch_yfinance(
        self,
        symbol: str,
        interval: str,
        period: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        """Run yfinance download in thread pool (blocking I/O)."""
        loop = asyncio.get_event_loop()

        def _download() -> pd.DataFrame:
            ticker = yf.Ticker(symbol)
            if period:
                df = ticker.history(period=period, interval=interval, auto_adjust=True)
            else:
                df = ticker.history(start=start, end=end, interval=interval, auto_adjust=True)
            return df

        return await loop.run_in_executor(None, _download)

    async def collect_historical(
        self,
        symbol: str,
        timeframe: str,
        start: Optional[datetime.datetime] = None,
        end: Optional[datetime.datetime] = None,
    ) -> CollectorResult:
        interval = YFINANCE_TF_MAP.get(timeframe, "1h")

        try:
            if start and end:
                df = await self._with_retry(
                    self._fetch_yfinance,
                    symbol,
                    interval,
                    None,
                    start.strftime("%Y-%m-%d"),
                    end.strftime("%Y-%m-%d"),
                )
            else:
                # Default: last 60 days
                df = await self._with_retry(
                    self._fetch_yfinance,
                    symbol,
                    interval,
                    "60d",
                )

            if df is None or df.empty:
                return CollectorResult(success=False, error=f"No data returned for {symbol}")

            # Flatten MultiIndex columns if present
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            async with async_session_factory() as session:
                async with session.begin():
                    instrument = await get_instrument_by_symbol(session, symbol)
                    if not instrument:
                        return CollectorResult(
                            success=False, error=f"Instrument {symbol} not found in DB"
                        )
                    records = _df_to_records(df, instrument.id, timeframe)
                    count = await bulk_upsert_price_data(session, records)

            return CollectorResult(success=True, records_count=count, data=df)

        except Exception as exc:
            logger.error(f"[YFinance] Error collecting {symbol}: {exc}")
            return CollectorResult(success=False, error=str(exc))

    async def collect_latest(
        self,
        symbol: str,
        timeframe: str,
        n_candles: int = 200,
    ) -> CollectorResult:
        """Collect latest N candles."""
        interval = YFINANCE_TF_MAP.get(timeframe, "1h")

        # Choose period based on timeframe
        period_map = {
            "M1": "5d", "M5": "5d", "M15": "10d",
            "H1": "30d", "H4": "60d", "D1": "1y", "W1": "5y", "MN1": "10y",
        }
        period = period_map.get(timeframe, "30d")

        try:
            df = await self._with_retry(self._fetch_yfinance, symbol, interval, period)

            if df is None or df.empty:
                return CollectorResult(success=False, error=f"No data returned for {symbol}")

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df = df.tail(n_candles)

            async with async_session_factory() as session:
                async with session.begin():
                    instrument = await get_instrument_by_symbol(session, symbol)
                    if not instrument:
                        return CollectorResult(
                            success=False, error=f"Instrument {symbol} not found in DB"
                        )
                    records = _df_to_records(df, instrument.id, timeframe)
                    count = await bulk_upsert_price_data(session, records)

            return CollectorResult(success=True, records_count=count, data=df)

        except Exception as exc:
            logger.error(f"[YFinance] Error collecting latest {symbol}: {exc}")
            return CollectorResult(success=False, error=str(exc))

    async def collect(self) -> CollectorResult:
        """Collect latest prices for all yfinance instruments."""
        from src.database.crud import get_all_instruments

        results = []
        async with async_session_factory() as session:
            instruments = await get_all_instruments(session)

        for instrument in instruments:
            if instrument.market in ("stocks", "forex"):
                result = await self.collect_latest(instrument.symbol, "H1")
                results.append(result)
                await self._rate_limit()

        success_count = sum(1 for r in results if r.success)
        return CollectorResult(
            success=True,
            records_count=sum(r.records_count for r in results),
            metadata={"success_count": success_count, "total": len(results)},
        )

    async def health_check(self) -> bool:
        try:
            df = await self._fetch_yfinance("EURUSD=X", "1d", "5d")
            return df is not None and not df.empty
        except Exception:
            return False


class CcxtCollector(BaseCollector):
    """Collects crypto price data via CCXT (Binance public API)."""

    def __init__(self) -> None:
        super().__init__("CCXT")
        self._exchange = None

    def _get_exchange(self):
        if self._exchange is None:
            import ccxt
            self._exchange = ccxt.binance({"enableRateLimit": True})
        return self._exchange

    async def _fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[int] = None,
        limit: int = 500,
    ) -> list:
        loop = asyncio.get_event_loop()
        exchange = self._get_exchange()
        ccxt_tf = CCXT_TF_MAP.get(timeframe, "1h")

        def _fetch():
            return exchange.fetch_ohlcv(symbol, ccxt_tf, since=since, limit=limit)

        return await loop.run_in_executor(None, _fetch)

    async def collect_historical(
        self,
        symbol: str,
        timeframe: str,
        start: Optional[datetime.datetime] = None,
        end: Optional[datetime.datetime] = None,
    ) -> CollectorResult:
        try:
            since = None
            if start:
                since = int(start.timestamp() * 1000)

            ohlcv = await self._with_retry(self._fetch_ohlcv, symbol, timeframe, since, 500)

            if not ohlcv:
                return CollectorResult(success=False, error=f"No data for {symbol}")

            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df.set_index("timestamp", inplace=True)

            async with async_session_factory() as session:
                async with session.begin():
                    instrument = await get_instrument_by_symbol(session, symbol)
                    if not instrument:
                        return CollectorResult(
                            success=False, error=f"Instrument {symbol} not found in DB"
                        )
                    records = _df_to_records(df, instrument.id, timeframe)
                    count = await bulk_upsert_price_data(session, records)

            return CollectorResult(success=True, records_count=count, data=df)

        except Exception as exc:
            logger.error(f"[CCXT] Error collecting {symbol}: {exc}")
            return CollectorResult(success=False, error=str(exc))

    async def collect_latest(
        self,
        symbol: str,
        timeframe: str,
        n_candles: int = 200,
    ) -> CollectorResult:
        try:
            ohlcv = await self._with_retry(self._fetch_ohlcv, symbol, timeframe, None, n_candles)

            if not ohlcv:
                return CollectorResult(success=False, error=f"No data for {symbol}")

            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df.set_index("timestamp", inplace=True)

            async with async_session_factory() as session:
                async with session.begin():
                    instrument = await get_instrument_by_symbol(session, symbol)
                    if not instrument:
                        return CollectorResult(
                            success=False, error=f"Instrument {symbol} not found in DB"
                        )
                    records = _df_to_records(df, instrument.id, timeframe)
                    count = await bulk_upsert_price_data(session, records)

            return CollectorResult(success=True, records_count=count, data=df)

        except Exception as exc:
            logger.error(f"[CCXT] Error collecting latest {symbol}: {exc}")
            return CollectorResult(success=False, error=str(exc))

    async def collect(self) -> CollectorResult:
        """Collect latest prices for all crypto instruments."""
        from src.database.crud import get_all_instruments

        async with async_session_factory() as session:
            instruments = await get_all_instruments(session)

        results = []
        for instrument in instruments:
            if instrument.market == "crypto":
                result = await self.collect_latest(instrument.symbol, "H1")
                results.append(result)
                await self._rate_limit()

        success_count = sum(1 for r in results if r.success)
        return CollectorResult(
            success=True,
            records_count=sum(r.records_count for r in results),
            metadata={"success_count": success_count, "total": len(results)},
        )

    async def health_check(self) -> bool:
        try:
            ohlcv = await self._fetch_ohlcv("BTC/USDT", "1d", limit=1)
            return bool(ohlcv)
        except Exception:
            return False
