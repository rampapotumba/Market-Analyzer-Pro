"""Company Fundamentals Collector.

Sources:
  - Finnhub API  — analyst recommendations, earnings surprises, insider transactions
  - yfinance     — P/E, EPS, margins, revenue growth, debt/equity, ROE

Saves to `company_fundamentals` table (one row per instrument per period).
"""

import datetime
import logging
from decimal import Decimal
from typing import Optional

import httpx

from src.cache import cache
from src.collectors.base import BaseCollector
from src.config import settings
from src.database.engine import async_session_factory
from src.database.models import CompanyFundamentals

logger = logging.getLogger(__name__)

_FINNHUB_BASE = "https://finnhub.io/api/v1"
_CACHE_TTL = 86400  # 24h — fundamentals change slowly


class FundamentalsCollector(BaseCollector):
    """
    Fetches and persists company fundamental metrics.

    Coverage: US stocks (S&P 500 constituents tracked in instruments table).
    Skips forex and crypto instruments (no company fundamentals).
    """

    def __init__(self) -> None:
        super().__init__("FundamentalsCollector")

    async def collect(self) -> None:  # type: ignore[override]
        """Celery entry point — collect all active stock instruments."""
        await self._collect_all()

    async def health_check(self) -> bool:
        if not settings.FINNHUB_KEY:
            return False
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{_FINNHUB_BASE}/stock/symbol",
                    params={"exchange": "US", "token": settings.FINNHUB_KEY},
                )
                return resp.status_code == 200
        except Exception:
            return False

    # ── Private ────────────────────────────────────────────────────────────────

    async def _collect_all(self) -> None:
        from sqlalchemy import select
        from src.database.models import Instrument

        async with async_session_factory() as session:
            result = await session.execute(
                select(Instrument).where(
                    Instrument.is_active.is_(True),
                    Instrument.market == "stocks",
                )
            )
            instruments = result.scalars().all()

        async with httpx.AsyncClient(timeout=15.0) as client:
            for instrument in instruments:
                ticker = instrument.symbol.replace("/", ".")
                try:
                    await self._collect_instrument(
                        instrument.id, ticker, client
                    )
                except Exception as exc:
                    logger.error(
                        "FundamentalsCollector: failed for %s: %s",
                        ticker,
                        exc,
                    )

    async def _collect_instrument(
        self,
        instrument_id: int,
        ticker: str,
        client: httpx.AsyncClient,
    ) -> None:
        period = self._current_quarter()

        # Fetch from both sources concurrently-ish (sequential for safety)
        yf_data = await self._fetch_yfinance(ticker)
        finnhub_data = await self._fetch_finnhub(ticker, client)

        row: dict = {
            "instrument_id": instrument_id,
            "period": period,
            "source": "finnhub+yfinance",
        }

        # yfinance metrics
        if yf_data:
            row.update({
                "pe_ratio": _dec(yf_data.get("pe_ratio")),
                "eps": _dec(yf_data.get("eps")),
                "revenue_growth_yoy": _dec(yf_data.get("revenue_growth_yoy")),
                "gross_margin": _dec(yf_data.get("gross_margin")),
                "net_margin": _dec(yf_data.get("net_margin")),
                "debt_to_equity": _dec(yf_data.get("debt_to_equity")),
                "roe": _dec(yf_data.get("roe")),
            })

        # Finnhub metrics
        if finnhub_data:
            row.update({
                "analyst_rating": finnhub_data.get("analyst_rating"),
                "analyst_target": _dec(finnhub_data.get("analyst_target")),
                "earnings_surprise_avg": _dec(finnhub_data.get("earnings_surprise_avg")),
                "insider_net_shares": finnhub_data.get("insider_net_shares"),
            })

        # Upsert
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        async with async_session_factory() as session:
            stmt = pg_insert(CompanyFundamentals).values(**row)
            stmt = stmt.on_conflict_do_update(
                constraint="uix_company_fundamentals",
                set_={k: v for k, v in row.items() if k not in ("instrument_id", "period")},
            )
            await session.execute(stmt)
            await session.commit()

        logger.info("FundamentalsCollector: saved %s/%s", ticker, period)

    # ── yfinance ──────────────────────────────────────────────────────────────

    async def _fetch_yfinance(self, ticker: str) -> Optional[dict]:
        """Fetch key metrics via yfinance (sync, run in executor)."""
        cache_key = f"fundamentals:yf:{ticker}"
        cached = await cache.get(cache_key)
        if cached:
            import json
            return json.loads(cached)

        try:
            import asyncio
            import functools
            import json as _json
            import yfinance as yf

            def _fetch():
                info = yf.Ticker(ticker).info
                return {
                    "pe_ratio": info.get("trailingPE"),
                    "eps": info.get("trailingEps"),
                    "revenue_growth_yoy": (
                        info.get("revenueGrowth", 0) * 100
                        if info.get("revenueGrowth") is not None
                        else None
                    ),
                    "gross_margin": (
                        info.get("grossMargins", 0) * 100
                        if info.get("grossMargins") is not None
                        else None
                    ),
                    "net_margin": (
                        info.get("profitMargins", 0) * 100
                        if info.get("profitMargins") is not None
                        else None
                    ),
                    "debt_to_equity": info.get("debtToEquity"),
                    "roe": (
                        info.get("returnOnEquity", 0) * 100
                        if info.get("returnOnEquity") is not None
                        else None
                    ),
                }

            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, _fetch)
            await cache.set(cache_key, _json.dumps({k: v for k, v in data.items() if v is not None}), ttl=_CACHE_TTL)
            return data
        except Exception as exc:
            logger.debug("yfinance fetch failed for %s: %s", ticker, exc)
            return None

    # ── Finnhub ───────────────────────────────────────────────────────────────

    async def _fetch_finnhub(
        self, ticker: str, client: httpx.AsyncClient
    ) -> Optional[dict]:
        if not settings.FINNHUB_KEY:
            return None

        cache_key = f"fundamentals:finnhub:{ticker}"
        cached = await cache.get(cache_key)
        if cached:
            import json
            return json.loads(cached)

        try:
            analyst_rating = await self._fetch_analyst_rating(ticker, client)
            earnings_surprise = await self._fetch_earnings_surprise(ticker, client)
            insider_net = await self._fetch_insider(ticker, client)

            data = {
                "analyst_rating": analyst_rating,
                "analyst_target": None,  # requires premium endpoint
                "earnings_surprise_avg": earnings_surprise,
                "insider_net_shares": insider_net,
            }
            import json
            await cache.set(cache_key, json.dumps({k: v for k, v in data.items() if v is not None}), ttl=_CACHE_TTL)
            return data
        except Exception as exc:
            logger.debug("Finnhub fetch failed for %s: %s", ticker, exc)
            return None

    async def _fetch_analyst_rating(
        self, ticker: str, client: httpx.AsyncClient
    ) -> Optional[str]:
        try:
            resp = await client.get(
                f"{_FINNHUB_BASE}/stock/recommendation",
                params={"symbol": ticker, "token": settings.FINNHUB_KEY},
                timeout=8.0,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data:
                return None
            latest = data[0]
            buy = latest.get("buy", 0) + latest.get("strongBuy", 0)
            sell = latest.get("sell", 0) + latest.get("strongSell", 0)
            hold = latest.get("hold", 0)
            total = buy + sell + hold
            if total == 0:
                return None
            if buy / total >= 0.6:
                return "buy"
            elif sell / total >= 0.4:
                return "sell"
            else:
                return "hold"
        except Exception as exc:
            logger.debug("Finnhub analyst for %s failed: %s", ticker, exc)
            return None

    async def _fetch_earnings_surprise(
        self, ticker: str, client: httpx.AsyncClient
    ) -> Optional[float]:
        try:
            resp = await client.get(
                f"{_FINNHUB_BASE}/stock/earnings",
                params={"symbol": ticker, "limit": "4", "token": settings.FINNHUB_KEY},
                timeout=8.0,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data:
                return None
            surprises = [
                d.get("surprisePercent", 0)
                for d in data
                if d.get("surprisePercent") is not None
            ]
            return round(sum(surprises) / len(surprises), 4) if surprises else None
        except Exception as exc:
            logger.debug("Finnhub earnings surprise for %s failed: %s", ticker, exc)
            return None

    async def _fetch_insider(
        self, ticker: str, client: httpx.AsyncClient
    ) -> Optional[int]:
        try:
            from_date = (
                datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=90)
            ).strftime("%Y-%m-%d")
            resp = await client.get(
                f"{_FINNHUB_BASE}/stock/insider-transactions",
                params={
                    "symbol": ticker,
                    "from": from_date,
                    "token": settings.FINNHUB_KEY,
                },
                timeout=8.0,
            )
            resp.raise_for_status()
            data = resp.json()
            transactions = data.get("data", [])
            net = sum(
                int(t.get("share", 0)) * (1 if t.get("transactionCode") == "P" else -1)
                for t in transactions
                if t.get("transactionCode") in ("P", "S")
            )
            return net
        except Exception as exc:
            logger.debug("Finnhub insider for %s failed: %s", ticker, exc)
            return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _current_quarter() -> str:
        now = datetime.datetime.now(datetime.timezone.utc)
        q = (now.month - 1) // 3 + 1
        return f"{now.year}-Q{q}"


def _dec(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(round(float(value), 6)))
    except (TypeError, ValueError):
        return None
