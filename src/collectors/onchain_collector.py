"""On-Chain Data Collector.

Sources (all free, no API key required):
  - CoinMetrics Community API — MVRV, active addresses, exchange flows, NVT proxy
  - CoinGecko (no key)        — BTC dominance, market cap

CoinMetrics Community endpoint:
  https://community-api.coinmetrics.io/v4/timeseries/asset-metrics
  Free metrics: AdrActCnt, CapMVRVCur, FlowInExUSD, FlowOutExUSD, CapMrktCurUSD, TxCnt

Persists to the `onchain_data` table.
"""

import datetime
import logging
from decimal import Decimal
from typing import Optional

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.cache import cache
from src.collectors.base import BaseCollector
from src.database.engine import async_session_factory
from src.database.models import OnchainData

logger = logging.getLogger(__name__)

# ── API endpoints ──────────────────────────────────────────────────────────────
_COINMETRICS_BASE = "https://community-api.coinmetrics.io/v4"
_COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"

_CACHE_TTL = 3600  # 1h — on-chain daily metrics update once per day

# CoinMetrics asset codes (lowercase)
_CM_ASSETS: dict[str, str] = {
    "BTC": "btc",
    "ETH": "eth",
    "SOL": "sol",
    "BNB": "bnb",
    "XRP": "xrp",
    "ADA": "ada",
    "DOGE": "doge",
    "DOT": "dot",
    "AVAX": "avax",
    "LINK": "link",
    "ATOM": "atom",
    "MATIC": "matic",
    "UNI": "uni",
}

# Metrics to fetch from CoinMetrics (all free in community tier)
_CM_METRICS = "AdrActCnt,CapMVRVCur,FlowInExUSD,FlowOutExUSD,CapMrktCurUSD,TxCnt"


class OnchainCollector(BaseCollector):
    """Collects on-chain metrics from CoinMetrics Community + CoinGecko."""

    def __init__(self) -> None:
        super().__init__("OnchainCollector")

    async def collect(self):  # type: ignore[override]
        """Celery entry point."""
        await self._collect_all()

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(_COINGECKO_GLOBAL)
                return resp.status_code == 200
        except Exception:
            return False

    # ── Private ────────────────────────────────────────────────────────────────

    async def _collect_all(self) -> None:
        """Collect on-chain data for all active crypto instruments."""
        from sqlalchemy import select
        from src.database.models import Instrument

        async with async_session_factory() as session:
            result = await session.execute(
                select(Instrument).where(
                    Instrument.is_active.is_(True),
                    Instrument.market_type == "crypto",
                )
            )
            instruments = result.scalars().all()

        # BTC dominance — from CoinGecko global (shared across all instruments)
        dominance = await self._fetch_btc_dominance()

        # Batch-fetch CoinMetrics for all supported assets in one request
        cm_data = await self._fetch_coinmetrics_batch(
            [i.symbol.split("/")[0].split("-")[0].upper() for i in instruments]
        )

        now = datetime.datetime.now(datetime.timezone.utc)
        for instrument in instruments:
            base = instrument.symbol.split("/")[0].split("-")[0].upper()
            metrics = cm_data.get(base, {})

            if not metrics and base not in _CM_ASSETS:
                logger.debug("OnchainCollector: no CoinMetrics data for %s, skipping", base)
                continue

            try:
                await self._save_asset(instrument.id, base, metrics, dominance, now)
            except Exception as exc:
                logger.error("OnchainCollector: save failed for %s: %s", instrument.symbol, exc)

    async def _save_asset(
        self,
        instrument_id: int,
        asset: str,
        metrics: dict,
        dominance: Optional[float],
        now: datetime.datetime,
    ) -> None:
        mvrv = metrics.get("CapMVRVCur")
        active_addr = metrics.get("AdrActCnt")
        flow_in = metrics.get("FlowInExUSD")
        flow_out = metrics.get("FlowOutExUSD")
        market_cap = metrics.get("CapMrktCurUSD")

        # NVT proxy: market_cap / tx_count (dimensionless ratio — higher = overvalued)
        # We don't have on-chain tx volume here, so we use market_cap alone as a proxy signal
        # A cleaner NVT would require on-chain settled volume (TxTfrValAdjUSD) — paid tier
        nvt = None  # placeholder; CoinMetrics TxTfrValAdjUSD is paid tier

        row: dict = {
            "instrument_id": instrument_id,
            "timestamp": now,
            "nvt_ratio": Decimal(str(round(nvt, 4))) if nvt is not None else None,
            "mvrv_ratio": Decimal(str(round(float(mvrv), 4))) if mvrv is not None else None,
            "active_addresses": int(float(active_addr)) if active_addr is not None else None,
            "exchange_inflow": (
                Decimal(str(round(float(flow_in), 2))) if flow_in is not None else None
            ),
            "exchange_outflow": (
                Decimal(str(round(float(flow_out), 2))) if flow_out is not None else None
            ),
            "dominance": (
                Decimal(str(round(dominance, 4))) if dominance is not None else None
            ),
            # Funding rate and OI come from OrderFlowCollector (real-time Binance)
            "funding_rate": None,
            "open_interest": None,
            "source": "coinmetrics",
        }

        async with async_session_factory() as session:
            stmt = pg_insert(OnchainData).values(**row)
            stmt = stmt.on_conflict_do_nothing()
            await session.execute(stmt)
            await session.commit()

        logger.info(
            "OnchainCollector: saved %s — mvrv=%.3f active=%s flow_in=$%.0fM flow_out=$%.0fM",
            asset,
            float(mvrv or 0),
            active_addr,
            float(flow_in or 0) / 1e6,
            float(flow_out or 0) / 1e6,
        )

    # ── CoinMetrics Community API ──────────────────────────────────────────────

    async def _fetch_coinmetrics_batch(
        self,
        asset_symbols: list[str],
    ) -> dict[str, dict]:
        """Fetch latest on-chain metrics for multiple assets in one request.

        Returns {asset_symbol: {metric_name: value}}.
        """
        cm_assets = [_CM_ASSETS[s] for s in asset_symbols if s in _CM_ASSETS]
        if not cm_assets:
            return {}

        cache_key = f"onchain:coinmetrics:{','.join(sorted(cm_assets))}"
        cached = await cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{_COINMETRICS_BASE}/timeseries/asset-metrics",
                    params={
                        "assets": ",".join(cm_assets),
                        "metrics": _CM_METRICS,
                        "frequency": "1d",
                        "page_size": str(len(cm_assets)),
                    },
                )
                resp.raise_for_status()
                rows = resp.json().get("data", [])

        except Exception as exc:
            logger.error("CoinMetrics batch fetch failed: %s", exc)
            return {}

        # Reverse map: cm_asset_code → symbol
        cm_to_symbol = {v: k for k, v in _CM_ASSETS.items()}

        result: dict[str, dict] = {}
        for row in rows:
            cm_asset = row.get("asset", "")
            sym = cm_to_symbol.get(cm_asset)
            if sym:
                result[sym] = {k: v for k, v in row.items() if k not in ("asset", "time")}

        if result:
            await cache.set(cache_key, result, ttl=_CACHE_TTL)

        return result

    # ── CoinGecko ─────────────────────────────────────────────────────────────

    async def _fetch_btc_dominance(self) -> Optional[float]:
        """BTC market cap dominance from CoinGecko global endpoint (free, no key)."""
        cache_key = "onchain:btc_dominance"
        cached = await cache.get(cache_key)
        if cached is not None:
            return float(cached)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(_COINGECKO_GLOBAL)
                resp.raise_for_status()
                dom = float(resp.json()["data"]["market_cap_percentage"]["btc"])
                await cache.set(cache_key, str(dom), ttl=_CACHE_TTL)
                return dom
        except Exception as exc:
            logger.debug("CoinGecko dominance fetch failed: %s", exc)
            return None
