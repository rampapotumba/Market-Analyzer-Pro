"""Real-time price streaming via WebSocket connections to external providers."""

import asyncio
import json
import logging
from typing import Optional

import websockets

from src.config import settings

logger = logging.getLogger(__name__)

# ── Symbol mapping ────────────────────────────────────────────────────────────

# Binance stream name → our symbol
BINANCE_SYMBOLS: dict[str, str] = {
    "btcusdt": "BTC/USDT",
    "ethusdt": "ETH/USDT",
    "solusdt": "SOL/USDT",
}

# Finnhub subscription symbol → our symbol
FINNHUB_SYMBOLS: dict[str, str] = {
    "OANDA:EUR_USD": "EURUSD=X",
    "OANDA:GBP_USD": "GBPUSD=X",
    "OANDA:USD_JPY": "USDJPY=X",
    "OANDA:AUD_USD": "AUDUSD=X",
    "OANDA:USD_CHF": "USDCHF=X",
    "AAPL":          "AAPL",
    "MSFT":          "MSFT",
    "GOOGL":         "GOOGL",
    "SPY":           "SPY",
    "QQQ":           "QQQ",
}

# Stocks to poll via yfinance (fallback when Finnhub key is absent)
YFINANCE_STOCK_SYMBOLS: list[str] = ["AAPL", "MSFT", "GOOGL", "SPY", "QQQ"]
YFINANCE_POLL_INTERVAL = 30  # seconds

# ── Shared price cache (symbol → last price) ──────────────────────────────────
last_prices: dict[str, float] = {}


async def _broadcast(symbol: str, price: float, source: str) -> None:
    """Push price tick to all WebSocket subscribers."""
    from src.api.websocket_v2 import ws_manager_v2  # avoid circular import at module level
    last_prices[symbol] = price
    await ws_manager_v2.broadcast_price(symbol, {
        "type": "tick",
        "symbol": symbol,
        "price": price,
        "source": source,
    })


# ── Binance WebSocket ─────────────────────────────────────────────────────────

BINANCE_WS_URL = "wss://stream.binance.com:9443/stream?streams=" + "/".join(
    f"{sym}@miniTicker" for sym in BINANCE_SYMBOLS
)


async def run_binance_stream() -> None:
    """Connect to Binance public WebSocket and stream crypto prices."""
    backoff = 1
    while True:
        try:
            logger.info("[Realtime] Connecting to Binance WebSocket...")
            async with websockets.connect(BINANCE_WS_URL, ping_interval=20) as ws:
                backoff = 1
                logger.info("[Realtime] Binance stream connected")
                async for raw in ws:
                    try:
                        data = json.loads(raw)
                        stream = data.get("stream", "")
                        payload = data.get("data", {})
                        # miniTicker: 'c' = close price
                        sym_key = stream.split("@")[0]  # e.g. "btcusdt"
                        our_symbol = BINANCE_SYMBOLS.get(sym_key)
                        if our_symbol and payload.get("c"):
                            price = float(payload["c"])
                            await _broadcast(our_symbol, price, "binance")
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning(f"[Realtime] Binance stream error: {exc}. Reconnecting in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


# ── Finnhub WebSocket ─────────────────────────────────────────────────────────

FINNHUB_WS_URL = "wss://ws.finnhub.io"


async def run_finnhub_stream() -> None:
    """Connect to Finnhub WebSocket and stream forex/stock prices."""
    if not settings.FINNHUB_KEY:
        logger.info("[Realtime] No FINNHUB_KEY — skipping Finnhub stream")
        return

    backoff = 1
    while True:
        try:
            url = f"{FINNHUB_WS_URL}?token={settings.FINNHUB_KEY}"
            logger.info("[Realtime] Connecting to Finnhub WebSocket...")
            async with websockets.connect(url, ping_interval=20) as ws:
                backoff = 1
                # Subscribe to all symbols
                for fh_sym in FINNHUB_SYMBOLS:
                    await ws.send(json.dumps({"type": "subscribe", "symbol": fh_sym}))
                logger.info(f"[Realtime] Finnhub stream connected, subscribed to {len(FINNHUB_SYMBOLS)} symbols")

                async for raw in ws:
                    try:
                        data = json.loads(raw)
                        if data.get("type") != "trade":
                            continue
                        for trade in data.get("data", []):
                            fh_sym = trade.get("s")
                            price = trade.get("p")
                            our_symbol = FINNHUB_SYMBOLS.get(fh_sym)
                            if our_symbol and price:
                                await _broadcast(our_symbol, float(price), "finnhub")
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning(f"[Realtime] Finnhub stream error: {exc}. Reconnecting in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


# ── yfinance polling (stocks fallback) ───────────────────────────────────────

async def run_yfinance_stock_poll() -> None:
    """Poll yfinance every 30s for stock prices and broadcast as ticks.

    This is the fallback path when Finnhub is not configured or doesn't
    cover a symbol. yfinance `fast_info.last_price` works during market hours.
    """
    import yfinance as yf

    logger.info("[Realtime] Starting yfinance stock polling (%ds interval)", YFINANCE_POLL_INTERVAL)
    while True:
        for symbol in YFINANCE_STOCK_SYMBOLS:
            try:
                ticker = yf.Ticker(symbol)
                price = ticker.fast_info.last_price
                if price and price > 0:
                    await _broadcast(symbol, float(price), "yfinance")
            except Exception as exc:
                logger.debug("[Realtime] yfinance poll failed for %s: %s", symbol, exc)
            await asyncio.sleep(0.5)  # small pause between tickers
        await asyncio.sleep(YFINANCE_POLL_INTERVAL)


# ── Entry point ───────────────────────────────────────────────────────────────

async def start_realtime_streams() -> None:
    """Launch all real-time streams as concurrent background tasks."""
    tasks = [
        asyncio.create_task(run_binance_stream(), name="binance-stream"),
        asyncio.create_task(run_finnhub_stream(), name="finnhub-stream"),
        asyncio.create_task(run_yfinance_stock_poll(), name="yfinance-stock-poll"),
    ]
    logger.info(f"[Realtime] Started {len(tasks)} stream(s)")
    # Tasks run forever in background — don't await
