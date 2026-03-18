"""Async event logger: persists system events to the DB.

Usage (awaited — at end of a job where result is already known):
    await log_event("PRICE_COLLECT", "Collected 150 records", source="jobs", details={"total": 150})

Usage (fire-and-forget — inside signal_engine mid-transaction):
    asyncio.create_task(log_event("SIGNAL_GENERATED", "LONG EURUSD/H1", symbol="EURUSD"))

Event levels: INFO / WARNING / ERROR
Event types (see EventType constants below).
"""

import asyncio
import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Event type constants ──────────────────────────────────────────────────────

class EventType:
    # Data collection
    PRICE_COLLECT = "PRICE_COLLECT"
    NEWS_COLLECT = "NEWS_COLLECT"
    MACRO_COLLECT = "MACRO_COLLECT"
    MARKET_CONTEXT = "MARKET_CONTEXT"
    COT_COLLECT = "COT_COLLECT"
    CALENDAR_COLLECT = "CALENDAR_COLLECT"
    # Signals
    SIGNAL_GENERATED = "SIGNAL_GENERATED"
    SIGNAL_BATCH = "SIGNAL_BATCH"
    SIGNAL_CHECK = "SIGNAL_CHECK"
    # System
    SYSTEM_START = "SYSTEM_START"
    CLEANUP = "CLEANUP"
    COLLECTOR_ERROR = "COLLECTOR_ERROR"
    SIGNAL_ERROR = "SIGNAL_ERROR"


# ── Core log function ─────────────────────────────────────────────────────────

async def log_event(
    event_type: str,
    message: str,
    level: str = "INFO",
    source: str = "",
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> None:
    """Write a system event to the database. Swallows all exceptions so it never crashes the caller."""
    try:
        from src.database.engine import async_session_factory
        from src.database.models import SystemEvent

        async with async_session_factory() as session:
            event = SystemEvent(
                event_type=event_type,
                level=level,
                source=source,
                symbol=symbol,
                timeframe=timeframe,
                message=message,
                details=json.dumps(details) if details else None,
            )
            session.add(event)
            await session.commit()
    except Exception as exc:
        # Never propagate — logging failures must not break the main flow
        logger.warning(f"[EventLogger] Failed to persist event {event_type}: {exc}")


def log_event_bg(
    event_type: str,
    message: str,
    level: str = "INFO",
    source: str = "",
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> None:
    """Schedule log_event as a background task (fire-and-forget, non-blocking).

    Use this inside an active DB transaction where you don't want to wait.
    The asyncio event loop must be running.
    """
    try:
        asyncio.get_running_loop().create_task(
            log_event(event_type, message, level=level, source=source,
                      symbol=symbol, timeframe=timeframe, details=details)
        )
    except RuntimeError:
        # No running event loop — skip silently
        pass
