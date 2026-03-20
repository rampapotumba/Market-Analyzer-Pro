"""WebSocket v2 — real-time streams for signals, prices, and portfolio.

Endpoints:
  ws://host/ws/v2/signals          — new signals broadcast
  ws://host/ws/v2/prices/{symbol}  — price tick passthrough
  ws://host/ws/v2/portfolio        — portfolio position updates
"""

import asyncio
import json
import logging
from decimal import Decimal
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from src.database.engine import async_session_factory

logger = logging.getLogger(__name__)


def _json_safe(obj: Any) -> Any:
    """Recursively convert Decimal/datetime for JSON."""
    if isinstance(obj, Decimal):
        return float(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(i) for i in obj]
    return obj


class ConnectionManagerV2:
    """
    Manages v2 WebSocket connections with typed subscription channels.

    Channels:
      - signal_subs   : subscribers to new signal events
      - portfolio_subs: subscribers to portfolio update events
      - price_subs    : dict[symbol -> list[ws]]
    """

    def __init__(self) -> None:
        self.signal_subs: list[WebSocket] = []
        self.portfolio_subs: list[WebSocket] = []
        self.price_subs: dict[str, list[WebSocket]] = {}
        self.all_price_subs: list[WebSocket] = []

    # ── Connection management ─────────────────────────────────────────────────

    async def connect_signals(self, ws: WebSocket) -> None:
        await ws.accept()
        self.signal_subs.append(ws)
        logger.debug("WS-v2 signals: new subscriber (%d total)", len(self.signal_subs))

    async def connect_portfolio(self, ws: WebSocket) -> None:
        await ws.accept()
        self.portfolio_subs.append(ws)
        logger.debug(
            "WS-v2 portfolio: new subscriber (%d total)", len(self.portfolio_subs)
        )

    async def connect_price(self, ws: WebSocket, symbol: str) -> None:
        await ws.accept()
        self.price_subs.setdefault(symbol, []).append(ws)
        logger.debug(
            "WS-v2 prices/%s: new subscriber (%d total)",
            symbol,
            len(self.price_subs[symbol]),
        )

    def disconnect_signals(self, ws: WebSocket) -> None:
        if ws in self.signal_subs:
            self.signal_subs.remove(ws)

    def disconnect_portfolio(self, ws: WebSocket) -> None:
        if ws in self.portfolio_subs:
            self.portfolio_subs.remove(ws)

    async def connect_all_prices(self, ws: WebSocket) -> None:
        await ws.accept()
        self.all_price_subs.append(ws)
        logger.debug("WS-v2 /ws/prices: new subscriber (%d total)", len(self.all_price_subs))

    def disconnect_all_prices(self, ws: WebSocket) -> None:
        if ws in self.all_price_subs:
            self.all_price_subs.remove(ws)

    def disconnect_price(self, ws: WebSocket, symbol: str) -> None:
        subs = self.price_subs.get(symbol, [])
        if ws in subs:
            subs.remove(ws)

    # ── Broadcast helpers ─────────────────────────────────────────────────────

    async def broadcast_signal(self, signal_data: dict) -> None:
        """Push a new signal event to all signal subscribers."""
        payload = json.dumps(_json_safe({"type": "signal", "data": signal_data}))
        dead: list[WebSocket] = []
        for ws in list(self.signal_subs):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect_signals(ws)

    async def broadcast_portfolio(self, update: dict) -> None:
        """Push a portfolio update to all portfolio subscribers."""
        payload = json.dumps(_json_safe({"type": "portfolio", "data": update}))
        dead: list[WebSocket] = []
        for ws in list(self.portfolio_subs):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect_portfolio(ws)

    async def broadcast_price(self, symbol: str, tick: dict) -> None:
        """Push a price tick to per-symbol subscribers and all-price subscribers.

        Payload is the tick dict directly so frontend can read msg.type/msg.price at root.
        """
        payload = json.dumps(_json_safe(tick))
        dead_sym: list[WebSocket] = []
        for ws in list(self.price_subs.get(symbol, [])):
            try:
                await ws.send_text(payload)
            except Exception:
                dead_sym.append(ws)
        for ws in dead_sym:
            self.disconnect_price(ws, symbol)

        dead_all: list[WebSocket] = []
        for ws in list(self.all_price_subs):
            try:
                await ws.send_text(payload)
            except Exception:
                dead_all.append(ws)
        for ws in dead_all:
            self.disconnect_all_prices(ws)


# Module-level singleton
ws_manager_v2 = ConnectionManagerV2()


# ── FastAPI route handlers ────────────────────────────────────────────────────

async def signals_ws_handler(websocket: WebSocket) -> None:
    """Stream new signals to connected clients."""
    await ws_manager_v2.connect_signals(websocket)
    try:
        # Send initial snapshot of active signals
        from src.database.crud import get_active_signals

        async with async_session_factory() as session:
            signals = await get_active_signals(session)
            for sig in signals:
                await websocket.send_text(
                    json.dumps(
                        _json_safe(
                            {
                                "type": "signal",
                                "data": {
                                    "id": sig.id,
                                    "direction": sig.direction,
                                    "signal_strength": sig.signal_strength,
                                    "composite_score": float(sig.composite_score),
                                    "regime": sig.regime,
                                    "status": sig.status,
                                },
                            }
                        )
                    )
                )

        # Keep-alive loop
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if msg == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "heartbeat"}))

    except WebSocketDisconnect:
        ws_manager_v2.disconnect_signals(websocket)
    except Exception as exc:
        logger.error("signals WS error: %s", exc)
        ws_manager_v2.disconnect_signals(websocket)


async def prices_ws_handler(websocket: WebSocket, symbol: str) -> None:
    """Forward price ticks for a specific symbol to connected clients."""
    await ws_manager_v2.connect_price(websocket, symbol)
    try:
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if msg == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "heartbeat"}))

    except WebSocketDisconnect:
        ws_manager_v2.disconnect_price(websocket, symbol)
    except Exception as exc:
        logger.error("prices WS error for %s: %s", symbol, exc)
        ws_manager_v2.disconnect_price(websocket, symbol)


async def portfolio_ws_handler(websocket: WebSocket) -> None:
    """Stream portfolio position updates to connected clients."""
    await ws_manager_v2.connect_portfolio(websocket)
    try:
        # Send initial portfolio snapshot
        from src.database.crud import get_open_positions

        async with async_session_factory() as session:
            positions = await get_open_positions(session)
            snapshot = [
                {
                    "signal_id": p.signal_id,
                    "status": p.status,
                    "size_pct": float(p.size_pct),
                    "entry_price": float(p.entry_price),
                    "current_price": float(p.current_price) if p.current_price else None,
                    "unrealized_pnl_pct": float(p.unrealized_pnl_pct)
                    if p.unrealized_pnl_pct
                    else None,
                }
                for p in positions
            ]
            await websocket.send_text(
                json.dumps({"type": "portfolio_snapshot", "data": snapshot})
            )

        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if msg == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "heartbeat"}))

    except WebSocketDisconnect:
        ws_manager_v2.disconnect_portfolio(websocket)
    except Exception as exc:
        logger.error("portfolio WS error: %s", exc)
        ws_manager_v2.disconnect_portfolio(websocket)


async def all_prices_ws_handler(websocket: WebSocket) -> None:
    """Stream all symbol price ticks to connected clients (used by sidebar)."""
    await ws_manager_v2.connect_all_prices(websocket)
    try:
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if msg == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "heartbeat"}))

    except WebSocketDisconnect:
        ws_manager_v2.disconnect_all_prices(websocket)
    except Exception as exc:
        logger.error("all_prices WS error: %s", exc)
        ws_manager_v2.disconnect_all_prices(websocket)
