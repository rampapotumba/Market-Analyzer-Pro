"""Order Flow Collector — CVD, Funding Rate, Open Interest, Liquidations.

Connects to Binance WebSocket streams for real-time aggTrades data.
Periodically writes aggregated snapshots to the `order_flow_data` hypertable.

Architecture:
  - WebSocket consumer runs per symbol (asyncio task)
  - Aggregates CVD, OI, funding, liquidations in memory
  - Flushes to DB every FLUSH_INTERVAL seconds
"""

import asyncio
import datetime
import logging
from decimal import Decimal
from typing import Optional

import httpx

from src.cache import cache
from src.collectors.base import BaseCollector
from src.database.engine import async_session_factory
from src.database.models import Instrument, OrderFlowData

logger = logging.getLogger(__name__)

_BINANCE_REST = "https://fapi.binance.com"
_FLUSH_INTERVAL = 60  # seconds between DB writes
_BINANCE_WS = "wss://fstream.binance.com/ws/{symbol}@aggTrade"

# Cache TTLs
_OI_CACHE_TTL = 60
_FUNDING_CACHE_TTL = 300


class _SymbolBuffer:
    """In-memory accumulator for a single symbol."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.buy_volume = 0.0
        self.sell_volume = 0.0
        self.liq_count = 0
        self.liq_size_usd = 0.0
        self._last_flush = datetime.datetime.now(datetime.timezone.utc)

    def add_trade(self, qty: float, is_buyer_maker: bool) -> None:
        if is_buyer_maker:
            self.sell_volume += qty   # buyer is maker → aggressive sell
        else:
            self.buy_volume += qty    # buyer is taker → aggressive buy

    @property
    def cvd(self) -> float:
        """Cumulative Volume Delta: buy_vol - sell_vol."""
        return self.buy_volume - self.sell_volume

    def reset(self) -> None:
        self.buy_volume = 0.0
        self.sell_volume = 0.0
        self.liq_count = 0
        self.liq_size_usd = 0.0
        self._last_flush = datetime.datetime.now(datetime.timezone.utc)


class OrderFlowCollector(BaseCollector):
    """Collects order flow metrics from Binance Futures REST API.

    Uses REST polling for OI, funding, and liquidations.
    CVD is approximated from aggTrades via REST (last N trades per interval).
    """

    def __init__(self) -> None:
        super().__init__("OrderFlowCollector")
        self._buffers: dict[str, _SymbolBuffer] = {}

    # ── Public API ─────────────────────────────────────────────────────────────

    async def collect(self):  # type: ignore[override]
        """Celery entry point — delegates to collect_all."""
        await self.collect_all()

    async def health_check(self) -> bool:
        """Check if Binance Futures REST API is reachable."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{_BINANCE_REST}/fapi/v1/ping")
                return resp.status_code == 200
        except Exception:
            return False

    async def collect_all(self) -> None:
        """Snapshot order flow metrics for all active crypto instruments."""
        from sqlalchemy import select
        async with async_session_factory() as session:
            result = await session.execute(
                select(Instrument).where(
                    Instrument.is_active.is_(True),
                    Instrument.market_type == "crypto",
                )
            )
            instruments = result.scalars().all()

        async with httpx.AsyncClient(timeout=10.0) as client:
            for instrument in instruments:
                try:
                    await self._collect_instrument(instrument, client)
                except Exception as exc:
                    logger.error(
                        "OrderFlowCollector: failed for %s: %s",
                        instrument.symbol,
                        exc,
                    )

    # ── Per-instrument ─────────────────────────────────────────────────────────

    async def _collect_instrument(
        self,
        instrument: Instrument,
        client: httpx.AsyncClient,
    ) -> None:
        # Convert symbol (BTC/USDT → BTCUSDT)
        binance_sym = instrument.symbol.replace("/", "").upper()
        perp_sym = binance_sym if binance_sym.endswith("USDT") else f"{binance_sym}USDT"

        # Fetch metrics
        oi = await self._fetch_open_interest(perp_sym, client)
        funding_rate = await self._fetch_funding_rate(perp_sym, client)
        cvd = await self._fetch_cvd_approx(perp_sym, client)
        liq_long, liq_short = await self._fetch_liquidations(perp_sym, client)

        now = datetime.datetime.now(datetime.timezone.utc)

        row = OrderFlowData(
            instrument_id=instrument.id,
            timestamp=now,
            timeframe="1m",
            cvd=Decimal(str(round(cvd, 4))) if cvd is not None else None,
            open_interest=Decimal(str(round(oi, 2))) if oi is not None else None,
            funding_rate=Decimal(str(round(funding_rate, 6))) if funding_rate is not None else None,
            liq_long_usd=Decimal(str(round(liq_long, 2))) if liq_long is not None else None,
            liq_short_usd=Decimal(str(round(liq_short, 2))) if liq_short is not None else None,
        )

        async with async_session_factory() as session:
            session.add(row)
            await session.commit()

        logger.debug(
            "OrderFlow: %s — oi=%.0f, fund=%.6f, cvd=%.2f",
            instrument.symbol,
            oi or 0,
            funding_rate or 0,
            cvd or 0,
        )

    # ── Binance REST fetchers ──────────────────────────────────────────────────

    async def _fetch_open_interest(
        self,
        symbol: str,
        client: httpx.AsyncClient,
    ) -> Optional[float]:
        cache_key = f"of:oi:{symbol}"
        cached = await cache.get(cache_key)
        if cached is not None:
            return float(cached)
        try:
            resp = await client.get(
                f"{_BINANCE_REST}/fapi/v1/openInterest",
                params={"symbol": symbol},
            )
            resp.raise_for_status()
            oi = float(resp.json()["openInterest"])
            await cache.set(cache_key, str(oi), ttl=_OI_CACHE_TTL)
            return oi
        except Exception as exc:
            logger.debug("OI fetch failed for %s: %s", symbol, exc)
            return None

    async def _fetch_funding_rate(
        self,
        symbol: str,
        client: httpx.AsyncClient,
    ) -> Optional[float]:
        cache_key = f"of:funding:{symbol}"
        cached = await cache.get(cache_key)
        if cached is not None:
            return float(cached)
        try:
            resp = await client.get(
                f"{_BINANCE_REST}/fapi/v1/premiumIndex",
                params={"symbol": symbol},
            )
            resp.raise_for_status()
            fr = float(resp.json()["lastFundingRate"])
            await cache.set(cache_key, str(fr), ttl=_FUNDING_CACHE_TTL)
            return fr
        except Exception as exc:
            logger.debug("Funding rate fetch failed for %s: %s", symbol, exc)
            return None

    async def _fetch_cvd_approx(
        self,
        symbol: str,
        client: httpx.AsyncClient,
        limit: int = 500,
    ) -> Optional[float]:
        """Approximate CVD from the most recent aggTrades."""
        try:
            resp = await client.get(
                f"{_BINANCE_REST}/fapi/v1/aggTrades",
                params={"symbol": symbol, "limit": limit},
            )
            resp.raise_for_status()
            trades = resp.json()
            buy_vol = sum(float(t["q"]) for t in trades if not t["m"])   # maker=sell
            sell_vol = sum(float(t["q"]) for t in trades if t["m"])
            return buy_vol - sell_vol
        except Exception as exc:
            logger.debug("CVD fetch failed for %s: %s", symbol, exc)
            return None

    async def _fetch_liquidations(
        self,
        symbol: str,
        client: httpx.AsyncClient,
    ) -> tuple[Optional[float], Optional[float]]:
        """Fetch recent liquidations from Binance.  Returns (long_usd, short_usd)."""
        try:
            resp = await client.get(
                f"{_BINANCE_REST}/fapi/v1/allForceOrders",
                params={"symbol": symbol, "limit": 100},
            )
            resp.raise_for_status()
            orders = resp.json()
            liq_long = sum(
                float(o["origQty"]) * float(o["price"])
                for o in orders
                if o.get("side") == "SELL"  # liquidated longs are sold
            )
            liq_short = sum(
                float(o["origQty"]) * float(o["price"])
                for o in orders
                if o.get("side") == "BUY"   # liquidated shorts are bought
            )
            return liq_long, liq_short
        except Exception as exc:
            logger.debug("Liquidations fetch failed for %s: %s", symbol, exc)
            return None, None


# ── WebSocket live streaming (Phase 3) ────────────────────────────────────────


class OrderFlowWebSocketCollector:
    """Real-time aggTrades WebSocket collector with automatic DB flushing.

    Maintains one WebSocket connection per symbol.  Aggregates CVD in
    `_SymbolBuffer` and persists to `order_flow_data` every FLUSH_INTERVAL
    seconds (default: 60s).

    Usage (long-running asyncio task):
        ws = OrderFlowWebSocketCollector(["BTCUSDT", "ETHUSDT"])
        await ws.run()   # runs until cancelled

    Integration with Celery:
        Launched as a persistent task in src/celery_app.py via
        ``celery_worker`` → ``order_flow_ws_start`` beat entry.
    """

    def __init__(self, symbols: list[str], flush_interval: int = _FLUSH_INTERVAL) -> None:
        self._symbols = [s.upper() for s in symbols]
        self._flush_interval = flush_interval
        self._buffers: dict[str, _SymbolBuffer] = {
            sym: _SymbolBuffer(sym) for sym in self._symbols
        }
        self._running = False

    async def run(self) -> None:
        """Start all WebSocket streams and the flush loop concurrently."""
        self._running = True
        tasks = [self._stream(sym) for sym in self._symbols]
        tasks.append(self._flush_loop())
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            self._running = False
            raise

    async def stop(self) -> None:
        """Signal the collector to stop gracefully."""
        self._running = False

    async def _stream(self, symbol: str) -> None:
        """Maintain a persistent aggTrade WebSocket for *symbol*."""
        try:
            import websockets  # type: ignore[import-untyped]
        except ImportError:
            logger.error("OrderFlowWS: 'websockets' package not installed; cannot stream %s", symbol)
            return

        url = _BINANCE_WS.format(symbol=symbol.lower())
        buf = self._buffers[symbol]
        reconnect_delay = 5.0

        while self._running:
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=20,
                ) as ws:
                    logger.info("OrderFlowWS: connected to %s", url)
                    reconnect_delay = 5.0  # reset on success
                    async for raw_msg in ws:
                        if not self._running:
                            break
                        try:
                            import json as _json
                            msg = _json.loads(raw_msg)
                            qty = float(msg["q"])
                            is_buyer_maker: bool = msg["m"]
                            buf.add_trade(qty, is_buyer_maker)
                        except Exception as exc:
                            logger.debug("OrderFlowWS: parse error: %s", exc)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(
                    "OrderFlowWS: %s disconnected (%s) — reconnecting in %.0fs",
                    symbol, exc, reconnect_delay,
                )
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60.0)

    async def _flush_loop(self) -> None:
        """Periodically flush all symbol buffers to the DB."""
        while self._running:
            await asyncio.sleep(self._flush_interval)
            if not self._running:
                break
            for symbol, buf in self._buffers.items():
                await self._flush(symbol, buf)

    async def _flush(self, symbol: str, buf: _SymbolBuffer) -> None:
        """Write the current buffer snapshot to `order_flow_data` and reset."""
        from sqlalchemy import select

        cvd = buf.cvd
        total_vol = buf.buy_volume + buf.sell_volume
        if total_vol == 0:
            # No trades received since last flush — skip
            return

        try:
            async with async_session_factory() as session:
                res = await session.execute(
                    select(Instrument).where(
                        Instrument.is_active.is_(True),
                        Instrument.market_type == "crypto",
                    )
                )
                instruments = {
                    instr.symbol.replace("/", "").upper(): instr
                    for instr in res.scalars().all()
                }

            instr = instruments.get(symbol)
            if instr is None:
                logger.debug("OrderFlowWS: no instrument record for %s", symbol)
                buf.reset()
                return

            row = OrderFlowData(
                instrument_id=instr.id,
                timestamp=datetime.datetime.now(datetime.timezone.utc),
                timeframe="1m",
                cvd=Decimal(str(round(cvd, 4))),
                open_interest=None,   # fetched separately via REST
                funding_rate=None,
                liq_long_usd=None,
                liq_short_usd=None,
            )
            async with async_session_factory() as session:
                session.add(row)
                await session.commit()

            logger.debug(
                "OrderFlowWS: flushed %s — cvd=%.4f, buy=%.4f, sell=%.4f",
                symbol, cvd, buf.buy_volume, buf.sell_volume,
            )
        except Exception as exc:
            logger.error("OrderFlowWS: flush error for %s: %s", symbol, exc)
        finally:
            buf.reset()
