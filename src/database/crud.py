"""Async CRUD operations for all database tables."""

import datetime
import json
import logging
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import and_, delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    AccuracyStats,
    EconomicEvent,
    Instrument,
    MacroData,
    NewsEvent,
    PriceData,
    Signal,
    SignalResult,
)

logger = logging.getLogger(__name__)


# ── Instruments ──────────────────────────────────────────────────────────────


async def get_instrument_by_symbol(session: AsyncSession, symbol: str) -> Optional[Instrument]:
    result = await session.execute(select(Instrument).where(Instrument.symbol == symbol))
    return result.scalar_one_or_none()


async def get_instrument_by_id(session: AsyncSession, instrument_id: int) -> Optional[Instrument]:
    result = await session.execute(
        select(Instrument).where(Instrument.id == instrument_id)
    )
    return result.scalar_one_or_none()


async def get_all_instruments(
    session: AsyncSession, active_only: bool = True
) -> Sequence[Instrument]:
    stmt = select(Instrument)
    if active_only:
        stmt = stmt.where(Instrument.is_active == True)  # noqa: E712
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_or_create_instrument(
    session: AsyncSession,
    symbol: str,
    market: str,
    name: str,
    pip_size: Decimal = Decimal("0.0001"),
) -> tuple[Instrument, bool]:
    """Return (instrument, created) tuple."""
    instrument = await get_instrument_by_symbol(session, symbol)
    if instrument:
        return instrument, False

    instrument = Instrument(symbol=symbol, market=market, name=name, pip_size=pip_size)
    session.add(instrument)
    await session.flush()
    return instrument, True


# ── PriceData ─────────────────────────────────────────────────────────────────


async def bulk_upsert_price_data(
    session: AsyncSession,
    records: list[dict[str, Any]],
) -> int:
    """Bulk insert price data, ignoring conflicts. Returns count inserted."""
    if not records:
        return 0

    stmt = sqlite_insert(PriceData).values(records)
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["instrument_id", "timeframe", "timestamp"]
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount or 0


async def get_price_data(
    session: AsyncSession,
    instrument_id: int,
    timeframe: str,
    from_dt: Optional[datetime.datetime] = None,
    to_dt: Optional[datetime.datetime] = None,
    limit: int = 500,
) -> Sequence[PriceData]:
    stmt = (
        select(PriceData)
        .where(
            and_(
                PriceData.instrument_id == instrument_id,
                PriceData.timeframe == timeframe,
            )
        )
        .order_by(PriceData.timestamp.desc())
    )
    if from_dt:
        stmt = stmt.where(PriceData.timestamp >= from_dt)
    if to_dt:
        stmt = stmt.where(PriceData.timestamp <= to_dt)
    stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    rows.reverse()  # chronological order
    return rows


# ── Signals ───────────────────────────────────────────────────────────────────


async def cancel_open_signals(
    session: AsyncSession, instrument_id: int, timeframe: str
) -> int:
    """Cancel all non-completed signals for instrument+timeframe. Returns count cancelled."""
    result = await session.execute(
        update(Signal)
        .where(
            Signal.instrument_id == instrument_id,
            Signal.timeframe == timeframe,
            Signal.status.in_(["created", "active", "tracking"]),
        )
        .values(status="cancelled")
    )
    await session.flush()
    return result.rowcount or 0


async def create_signal(session: AsyncSession, data: dict[str, Any]) -> Signal:
    signal = Signal(**data)
    session.add(signal)
    await session.flush()
    await session.refresh(signal)
    return signal


async def get_signal_by_id(
    session: AsyncSession, signal_id: int
) -> Optional[Signal]:
    result = await session.execute(
        select(Signal).where(Signal.id == signal_id)
    )
    return result.scalar_one_or_none()


async def get_signals(
    session: AsyncSession,
    status: Optional[str] = None,
    market: Optional[str] = None,
    from_dt: Optional[datetime.datetime] = None,
    to_dt: Optional[datetime.datetime] = None,
    limit: int = 50,
) -> Sequence[Signal]:
    stmt = select(Signal).order_by(Signal.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(Signal.status == status)
    if market:
        stmt = stmt.join(Signal.instrument).where(Instrument.market == market)
    if from_dt:
        stmt = stmt.where(Signal.created_at >= from_dt)
    if to_dt:
        stmt = stmt.where(Signal.created_at <= to_dt)
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_latest_signal_for_instrument(
    session: AsyncSession,
    instrument_id: int,
    timeframe: Optional[str] = None,
) -> Optional[Signal]:
    """Return the most recent signal for a given instrument (and optionally timeframe)."""
    stmt = (
        select(Signal)
        .where(Signal.instrument_id == instrument_id)
        .order_by(Signal.created_at.desc())
        .limit(1)
    )
    if timeframe:
        stmt = stmt.where(Signal.timeframe == timeframe)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_active_signals(session: AsyncSession) -> Sequence[Signal]:
    result = await session.execute(
        select(Signal).where(
            Signal.status.in_(["created", "active", "tracking"])
        ).order_by(Signal.created_at.desc())
    )
    return result.scalars().all()


async def update_signal_status(
    session: AsyncSession, signal_id: int, status: str
) -> None:
    await session.execute(
        update(Signal).where(Signal.id == signal_id).values(status=status)
    )
    await session.flush()


# ── SignalResults ─────────────────────────────────────────────────────────────


async def create_signal_result(
    session: AsyncSession, data: dict[str, Any]
) -> SignalResult:
    result_obj = SignalResult(**data)
    session.add(result_obj)
    await session.flush()
    return result_obj


async def get_signal_results(
    session: AsyncSession,
    limit: int = 200,
) -> Sequence[SignalResult]:
    result = await session.execute(
        select(SignalResult).order_by(SignalResult.exit_at.desc()).limit(limit)
    )
    return result.scalars().all()


# ── AccuracyStats ─────────────────────────────────────────────────────────────


async def upsert_accuracy_stats(
    session: AsyncSession, data: dict[str, Any]
) -> AccuracyStats:
    stats = AccuracyStats(**data)
    session.add(stats)
    await session.flush()
    return stats


async def get_accuracy_stats(
    session: AsyncSession,
    period: str = "all_time",
    instrument_id: Optional[int] = None,
) -> Sequence[AccuracyStats]:
    stmt = select(AccuracyStats).where(AccuracyStats.period == period)
    if instrument_id:
        stmt = stmt.where(AccuracyStats.instrument_id == instrument_id)
    result = await session.execute(stmt)
    return result.scalars().all()


# ── MacroData ─────────────────────────────────────────────────────────────────


async def upsert_macro_data(
    session: AsyncSession, records: list[dict[str, Any]]
) -> int:
    if not records:
        return 0
    count = 0
    for record in records:
        stmt = sqlite_insert(MacroData).values(record)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["indicator_name", "country", "release_date"]
        )
        result = await session.execute(stmt)
        count += result.rowcount or 0
    await session.flush()
    return count


async def get_macro_data(
    session: AsyncSession, country: Optional[str] = None, limit: int = 100
) -> Sequence[MacroData]:
    stmt = select(MacroData).order_by(MacroData.release_date.desc()).limit(limit)
    if country:
        stmt = stmt.where(MacroData.country == country)
    result = await session.execute(stmt)
    return result.scalars().all()


# ── NewsEvents ────────────────────────────────────────────────────────────────


async def create_news_event(
    session: AsyncSession, data: dict[str, Any]
) -> NewsEvent:
    event = NewsEvent(**data)
    session.add(event)
    await session.flush()
    return event


async def get_news_events(
    session: AsyncSession,
    limit: int = 50,
    from_dt: Optional[datetime.datetime] = None,
) -> Sequence[NewsEvent]:
    stmt = select(NewsEvent).order_by(NewsEvent.published_at.desc()).limit(limit)
    if from_dt:
        stmt = stmt.where(NewsEvent.published_at >= from_dt)
    result = await session.execute(stmt)
    return result.scalars().all()


# ── EconomicEvent ─────────────────────────────────────────────────────────────


async def upsert_economic_event(session: AsyncSession, data: dict[str, Any]) -> None:
    """Insert economic event; on conflict update estimate/actual/previous."""
    stmt = sqlite_insert(EconomicEvent).values(**data)
    stmt = stmt.on_conflict_do_update(
        index_elements=["event_date", "country", "event_name"],
        set_={
            "estimate": data.get("estimate"),
            "actual": data.get("actual"),
            "previous": data.get("previous"),
        },
    )
    await session.execute(stmt)


async def get_upcoming_economic_events(
    session: AsyncSession,
    from_dt: Optional[datetime.datetime] = None,
    to_dt: Optional[datetime.datetime] = None,
    impact_min: Optional[str] = None,
) -> Sequence[EconomicEvent]:
    """Return upcoming events ordered by date ascending."""
    now = from_dt or datetime.datetime.now(datetime.timezone.utc)
    end = to_dt or (now + datetime.timedelta(days=7))
    stmt = (
        select(EconomicEvent)
        .where(EconomicEvent.event_date >= now)
        .where(EconomicEvent.event_date <= end)
        .order_by(EconomicEvent.event_date.asc())
    )
    if impact_min:
        impact_order = {"high": ["high"], "medium": ["high", "medium"], "low": ["high", "medium", "low"]}
        allowed = impact_order.get(impact_min, ["high", "medium", "low"])
        stmt = stmt.where(EconomicEvent.impact.in_(allowed))
    result = await session.execute(stmt)
    return result.scalars().all()


async def delete_old_economic_events(session: AsyncSession) -> int:
    """Remove events older than 24 hours (already passed)."""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)
    result = await session.execute(
        delete(EconomicEvent).where(EconomicEvent.event_date < cutoff)
    )
    await session.flush()
    return result.rowcount or 0
