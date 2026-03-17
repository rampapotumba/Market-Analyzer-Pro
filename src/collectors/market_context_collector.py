"""Market context collector: DXY, VIX, TNX via yfinance + Binance funding rates."""

import datetime
import logging
from decimal import Decimal
from typing import Any, Optional

import httpx

from src.collectors.base import BaseCollector, CollectorResult

logger = logging.getLogger(__name__)

# Symbols to collect via yfinance
YFINANCE_SYMBOLS = {
    "DX=F": "DXY",
    "^VIX": "VIX",
    "^TNX": "TNX",
}

# Binance futures symbols for funding rates
BINANCE_FUNDING_SYMBOLS = {
    "BTCUSDT": "FUNDING_RATE_BTC",
    "ETHUSDT": "FUNDING_RATE_ETH",
    "SOLUSDT": "FUNDING_RATE_SOL",
}

BINANCE_FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"


class MarketContextCollector(BaseCollector):
    """Collects market context data: DXY, VIX, TNX, and Binance funding rates."""

    def __init__(self) -> None:
        super().__init__("MarketContext")

    async def _fetch_yfinance_price(self, ticker: str) -> Optional[float]:
        """Fetch last close price for a yfinance ticker."""
        try:
            import yfinance as yf

            data = yf.Ticker(ticker)
            hist = data.history(period="2d")
            if hist.empty:
                self.logger.warning(f"[MarketContext] No data from yfinance for {ticker}")
                return None
            last_close = float(hist["Close"].iloc[-1])
            return last_close
        except Exception as exc:
            self.logger.warning(f"[MarketContext] yfinance fetch failed for {ticker}: {exc}")
            return None

    async def _fetch_binance_funding_rate(self, symbol: str) -> Optional[float]:
        """Fetch the latest funding rate for a Binance perpetual futures symbol."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    BINANCE_FUNDING_URL,
                    params={"symbol": symbol, "limit": 1},
                )
                response.raise_for_status()
                data = response.json()
                if data and isinstance(data, list) and len(data) > 0:
                    rate = float(data[0].get("fundingRate", 0.0))
                    return rate
                return None
        except Exception as exc:
            self.logger.warning(
                f"[MarketContext] Binance funding rate fetch failed for {symbol}: {exc}"
            )
            return None

    async def collect(self) -> CollectorResult:
        """
        Collect DXY, VIX, TNX prices and Binance funding rates.
        Stores all in macro_data table.
        Returns CollectorResult with records_count of stored records.
        """
        from src.database.crud import upsert_macro_data
        from src.database.engine import async_session_factory

        records: list[dict[str, Any]] = []
        now_utc = datetime.datetime.now(datetime.timezone.utc)

        # Collect yfinance symbols
        for ticker, indicator_name in YFINANCE_SYMBOLS.items():
            try:
                price = await self._fetch_yfinance_price(ticker)
                if price is not None:
                    records.append({
                        "indicator_name": indicator_name,
                        "country": "GLOBAL",
                        "value": Decimal(str(round(price, 6))),
                        "release_date": now_utc,
                        "source": "yfinance",
                    })
                    self.logger.debug(f"[MarketContext] {indicator_name} = {price}")
                else:
                    self.logger.warning(f"[MarketContext] Skipping {indicator_name}: no data")
            except Exception as exc:
                self.logger.error(f"[MarketContext] Error collecting {ticker}: {exc}")

        # Collect Binance funding rates
        for symbol, indicator_name in BINANCE_FUNDING_SYMBOLS.items():
            try:
                rate = await self._fetch_binance_funding_rate(symbol)
                if rate is not None:
                    # Store funding rate as percentage (×100 for readability)
                    records.append({
                        "indicator_name": indicator_name,
                        "country": "GLOBAL",
                        "value": Decimal(str(round(rate, 8))),
                        "release_date": now_utc,
                        "source": "binance",
                    })
                    self.logger.debug(f"[MarketContext] {indicator_name} = {rate}")
                else:
                    self.logger.warning(f"[MarketContext] Skipping {indicator_name}: no data")
            except Exception as exc:
                self.logger.error(f"[MarketContext] Error collecting {symbol}: {exc}")

            await self._rate_limit()

        # Store to database
        stored = 0
        if records:
            try:
                async with async_session_factory() as db:
                    async with db.begin():
                        stored = await upsert_macro_data(db, records)
                self.logger.info(
                    f"[MarketContext] Stored {stored}/{len(records)} records to macro_data"
                )
            except Exception as exc:
                self.logger.error(f"[MarketContext] DB storage failed: {exc}")
                return CollectorResult(
                    success=False,
                    error=str(exc),
                    records_count=0,
                    data=records,
                )

        return CollectorResult(
            success=True,
            data=records,
            records_count=len(records),
            metadata={
                "stored": stored,
                "yfinance_collected": sum(1 for r in records if r.get("source") == "yfinance"),
                "binance_collected": sum(1 for r in records if r.get("source") == "binance"),
            },
        )

    async def health_check(self) -> bool:
        """Check if yfinance is reachable."""
        try:
            price = await self._fetch_yfinance_price("^VIX")
            return price is not None
        except Exception:
            return False
