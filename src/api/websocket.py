"""WebSocket handlers for real-time price and signal updates."""

import asyncio
import datetime
import json
import logging
from decimal import Decimal
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from src.database.crud import get_active_signals, get_instrument_by_symbol, get_price_data
from src.database.engine import async_session_factory

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages active WebSocket connections."""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []
        self.price_subscribers: dict[str, list[WebSocket]] = {}  # symbol -> [ws]
        self.all_price_subscribers: list[WebSocket] = []         # receives all ticks
        self.signal_subscribers: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.discard(websocket) if hasattr(
            self.active_connections, "discard"
        ) else None
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

        for symbol in list(self.price_subscribers.keys()):
            if websocket in self.price_subscribers[symbol]:
                self.price_subscribers[symbol].remove(websocket)
        if websocket in self.all_price_subscribers:
            self.all_price_subscribers.remove(websocket)
        if websocket in self.signal_subscribers:
            self.signal_subscribers.remove(websocket)

    def subscribe_prices(self, websocket: WebSocket, symbol: str) -> None:
        if symbol not in self.price_subscribers:
            self.price_subscribers[symbol] = []
        if websocket not in self.price_subscribers[symbol]:
            self.price_subscribers[symbol].append(websocket)

    def subscribe_all_prices(self, websocket: WebSocket) -> None:
        if websocket not in self.all_price_subscribers:
            self.all_price_subscribers.append(websocket)

    def subscribe_signals(self, websocket: WebSocket) -> None:
        if websocket not in self.signal_subscribers:
            self.signal_subscribers.append(websocket)

    async def broadcast_price(self, symbol: str, data: dict[str, Any]) -> None:
        """Send price update to symbol subscribers and all-prices subscribers."""
        dead = []
        for ws in self.price_subscribers.get(symbol, []) + self.all_price_subscribers:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def broadcast_signal(self, data: dict[str, Any]) -> None:
        """Send signal update to all signal subscribers."""
        dead = []
        for ws in self.signal_subscribers:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def send_personal(self, websocket: WebSocket, data: dict[str, Any]) -> None:
        try:
            await websocket.send_json(data)
        except Exception as exc:
            logger.warning(f"[WS] Send failed: {exc}")
            self.disconnect(websocket)


manager = ConnectionManager()


def _serialize_price(price_record: Any) -> dict[str, Any]:
    """Serialize a price record to JSON-safe dict."""
    return {
        "timestamp": price_record.timestamp.isoformat(),
        "open": float(price_record.open),
        "high": float(price_record.high),
        "low": float(price_record.low),
        "close": float(price_record.close),
        "volume": float(price_record.volume),
        "timeframe": price_record.timeframe,
    }


async def websocket_prices(websocket: WebSocket, symbol: str) -> None:
    """
    WebSocket endpoint: /ws/prices/{symbol}
    Streams real-time price updates for a symbol.
    """
    await manager.connect(websocket)
    manager.subscribe_prices(websocket, symbol)

    logger.info(f"[WS] Client connected to prices/{symbol}")

    try:
        # Send initial data
        async with async_session_factory() as db:
            instrument = await get_instrument_by_symbol(db, symbol)
            if not instrument:
                await websocket.send_json({"error": f"Symbol {symbol} not found"})
                await websocket.close()
                return

            records = await get_price_data(db, instrument.id, "H1", limit=100)
            if records:
                initial_data = {
                    "type": "initial",
                    "symbol": symbol,
                    "data": [_serialize_price(r) for r in records],
                }
                await manager.send_personal(websocket, initial_data)

        # Keep connection alive, send heartbeats
        while True:
            try:
                # Wait for client message or heartbeat timeout
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                msg = json.loads(data)

                if msg.get("type") == "subscribe":
                    new_symbol = msg.get("symbol", symbol)
                    manager.subscribe_prices(websocket, new_symbol)
                    await manager.send_personal(websocket, {
                        "type": "subscribed",
                        "symbol": new_symbol,
                    })
                elif msg.get("type") == "ping":
                    await manager.send_personal(websocket, {
                        "type": "pong",
                        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    })

            except asyncio.TimeoutError:
                # Send heartbeat
                await manager.send_personal(websocket, {
                    "type": "heartbeat",
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                })

    except WebSocketDisconnect:
        logger.info(f"[WS] Client disconnected from prices/{symbol}")
    except Exception as exc:
        logger.error(f"[WS] Error in prices/{symbol}: {exc}")
    finally:
        manager.disconnect(websocket)


async def websocket_all_prices(websocket: WebSocket) -> None:
    """
    WebSocket endpoint: /ws/prices
    Streams real-time price ticks for ALL symbols (for sidebar updates).
    """
    await manager.connect(websocket)
    manager.subscribe_all_prices(websocket)
    logger.info("[WS] Client connected to /ws/prices (all symbols)")

    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await manager.send_personal(websocket, {
                        "type": "pong",
                        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    })
            except asyncio.TimeoutError:
                await manager.send_personal(websocket, {
                    "type": "heartbeat",
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                })
    except WebSocketDisconnect:
        logger.info("[WS] Client disconnected from /ws/prices")
    except Exception as exc:
        logger.error(f"[WS] Error in /ws/prices: {exc}")
    finally:
        manager.disconnect(websocket)


async def websocket_signals(websocket: WebSocket) -> None:
    """
    WebSocket endpoint: /ws/signals
    Streams real-time signal updates.
    """
    await manager.connect(websocket)
    manager.subscribe_signals(websocket)

    logger.info("[WS] Client connected to /ws/signals")

    try:
        # Send current active signals
        async with async_session_factory() as db:
            signals = await get_active_signals(db)
            if signals:
                await manager.send_personal(websocket, {
                    "type": "active_signals",
                    "data": [
                        {
                            "id": s.id,
                            "instrument_id": s.instrument_id,
                            "timeframe": s.timeframe,
                            "direction": s.direction,
                            "signal_strength": s.signal_strength,
                            "composite_score": float(s.composite_score),
                            "entry_price": float(s.entry_price) if s.entry_price else None,
                            "stop_loss": float(s.stop_loss) if s.stop_loss else None,
                            "take_profit_1": float(s.take_profit_1) if s.take_profit_1 else None,
                            "status": s.status,
                            "created_at": s.created_at.isoformat(),
                        }
                        for s in signals
                    ],
                })

        # Keep alive
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await manager.send_personal(websocket, {
                        "type": "pong",
                        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    })
            except asyncio.TimeoutError:
                await manager.send_personal(websocket, {
                    "type": "heartbeat",
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                })

    except WebSocketDisconnect:
        logger.info("[WS] Client disconnected from /ws/signals")
    except Exception as exc:
        logger.error(f"[WS] Error in /ws/signals: {exc}")
    finally:
        manager.disconnect(websocket)
