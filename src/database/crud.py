"""Async CRUD operations for all database tables."""

import datetime
import json
import logging
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import and_, delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.database.models import (
    AccuracyStats,
    EconomicEvent,
    Instrument,
    MacroData,
    NewsEvent,
    OrderFlowData,
    PriceData,
    Signal,
    SignalResult,
    SystemEvent,
    VirtualAccount,
    VirtualPortfolio,
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

    stmt = pg_insert(PriceData).values(records)
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
        select(Signal)
        .options(selectinload(Signal.instrument))
        .where(Signal.id == signal_id)
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
    stmt = (
        select(Signal)
        .options(selectinload(Signal.instrument))
        .order_by(Signal.created_at.desc())
        .limit(limit)
    )
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
        select(Signal)
        .options(selectinload(Signal.instrument))
        .where(Signal.status.in_(["created", "active", "tracking"]))
        .order_by(Signal.created_at.desc())
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
        stmt = pg_insert(MacroData).values(record)
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
) -> Optional[NewsEvent]:
    """Insert a news event. Returns None if url already exists (dedup by url)."""
    stmt = (
        pg_insert(NewsEvent)
        .values(**data)
        .on_conflict_do_nothing(index_elements=["url"])
        .returning(NewsEvent)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


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


def _get_instrument_keywords(symbol: str, market: str) -> list[str]:
    """Return keyword list for instrument-aware news filtering.

    v3: used by get_news_events_for_instrument to filter news relevant
    to a specific instrument rather than returning last-N global news.
    """
    sym_upper = symbol.upper().replace("/", "").replace("-", "").replace("_", "")

    if market == "forex":
        # "EURUSD" → ["EUR", "USD", "ECB", "Federal Reserve", "euro", "dollar"]
        _forex_keywords: dict[str, list[str]] = {
            "EUR": ["EUR", "euro", "ECB", "European Central Bank"],
            "USD": ["USD", "dollar", "Federal Reserve", "Fed", "FOMC"],
            "GBP": ["GBP", "pound", "sterling", "Bank of England", "BOE"],
            "JPY": ["JPY", "yen", "Bank of Japan", "BOJ"],
            "AUD": ["AUD", "aussie", "Reserve Bank of Australia", "RBA"],
            "CAD": ["CAD", "loonie", "Bank of Canada", "BOC"],
            "CHF": ["CHF", "franc", "Swiss National Bank", "SNB"],
            "NZD": ["NZD", "kiwi", "Reserve Bank of New Zealand", "RBNZ"],
        }
        keywords: list[str] = []
        # Extract 3-letter currency codes from the symbol (e.g. EURUSD → EUR, USD)
        for i in range(0, len(sym_upper) - 2, 3):
            ccy = sym_upper[i : i + 3]
            keywords.extend(_forex_keywords.get(ccy, [ccy]))
        return keywords or [sym_upper]

    elif market == "crypto":
        _crypto_keywords: dict[str, list[str]] = {
            "BTC": ["bitcoin", "BTC", "Bitcoin"],
            "ETH": ["ethereum", "ETH", "Ethereum"],
            "BNB": ["BNB", "Binance"],
            "SOL": ["solana", "SOL", "Solana"],
            "XRP": ["XRP", "Ripple"],
            "ADA": ["cardano", "ADA", "Cardano"],
            "DOGE": ["dogecoin", "DOGE", "Dogecoin"],
            "MATIC": ["polygon", "MATIC", "Polygon"],
        }
        # symbol can be BTCUSDT, BTC/USDT, etc.
        base = sym_upper[:3]
        return _crypto_keywords.get(base, [base, symbol.split("/")[0].upper()])

    elif market == "stocks":
        # Use ticker directly + company name heuristic
        ticker = sym_upper
        return [ticker]

    elif market == "commodities":
        _commodity_keywords: dict[str, list[str]] = {
            "GOLD": ["gold", "XAU", "bullion"],
            "XAUUSD": ["gold", "XAU", "bullion"],
            "SILVER": ["silver", "XAG"],
            "XAGUSD": ["silver", "XAG"],
            "OIL": ["oil", "crude", "WTI", "Brent", "OPEC"],
            "USOIL": ["oil", "crude", "WTI", "OPEC"],
            "UKOIL": ["oil", "crude", "Brent", "OPEC"],
        }
        return _commodity_keywords.get(sym_upper, [sym_upper])

    return [symbol]


async def get_news_events_for_instrument(
    session: AsyncSession,
    symbol: str,
    market: str,
    limit: int = 30,
    hours_back: int = 24,
    fallback_limit: int = 5,
) -> list[NewsEvent]:
    """Return news events filtered by relevance to the given instrument.

    v3 fix: replaces generic get_news_events(db, limit=30) which returned
    unrelated news for all instruments.

    Strategy:
    1. Fetch recent news matching instrument keywords in headline/summary.
    2. If fewer than `fallback_limit` results, supplement with generic macro news.

    Args:
        symbol:         instrument symbol, e.g. "EUR/USD", "AAPL", "BTC/USDT"
        market:         "forex" | "stocks" | "crypto" | "commodities"
        limit:          max results to return
        hours_back:     look-back window in hours
        fallback_limit: minimum results before adding generic macro news
    """
    from_dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours_back)
    keywords = _get_instrument_keywords(symbol, market)

    # Build OR filter: headline or summary contains any keyword (case-insensitive)
    from sqlalchemy import or_, func as sa_func  # noqa: PLC0415

    keyword_filters = []
    for kw in keywords:
        kw_lower = kw.lower()
        keyword_filters.append(sa_func.lower(NewsEvent.headline).contains(kw_lower))
        keyword_filters.append(sa_func.lower(sa_func.coalesce(NewsEvent.summary, "")).contains(kw_lower))

    stmt = (
        select(NewsEvent)
        .where(NewsEvent.published_at >= from_dt)
        .where(or_(*keyword_filters))
        .order_by(NewsEvent.published_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())

    # Fallback: supplement with generic macro news if too few instrument-specific results
    if len(rows) < fallback_limit:
        macro_stmt = (
            select(NewsEvent)
            .where(NewsEvent.published_at >= from_dt)
            .where(NewsEvent.importance.in_(["high", "critical"]))
            .order_by(NewsEvent.published_at.desc())
            .limit(fallback_limit - len(rows))
        )
        macro_result = await session.execute(macro_stmt)
        existing_ids = {r.id for r in rows}
        for macro_row in macro_result.scalars().all():
            if macro_row.id not in existing_ids:
                rows.append(macro_row)

    return rows


# ── EconomicEvent ─────────────────────────────────────────────────────────────


async def upsert_economic_event(session: AsyncSession, data: dict[str, Any]) -> None:
    """Insert economic event; on conflict update estimate/actual/previous."""
    stmt = pg_insert(EconomicEvent).values(**data)
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


# ── Virtual Portfolio ─────────────────────────────────────────────────────────


async def create_virtual_position(
    session: AsyncSession, data: dict[str, Any]
) -> VirtualPortfolio:
    """Open a new virtual position."""
    pos = VirtualPortfolio(**data)
    session.add(pos)
    await session.flush()
    return pos


async def get_virtual_position(
    session: AsyncSession, signal_id: int
) -> Optional[VirtualPortfolio]:
    result = await session.execute(
        select(VirtualPortfolio).where(VirtualPortfolio.signal_id == signal_id)
    )
    return result.scalar_one_or_none()


async def get_open_positions(session: AsyncSession) -> Sequence[VirtualPortfolio]:
    """Return all positions with status 'open' or 'partial'."""
    result = await session.execute(
        select(VirtualPortfolio).where(
            VirtualPortfolio.status.in_(["open", "partial"])
        )
    )
    return result.scalars().all()


async def has_open_position_for_instrument(
    session: AsyncSession, instrument_id: int
) -> bool:
    """Return True if any open/partial virtual position exists for the given instrument."""
    result = await session.execute(
        select(VirtualPortfolio.signal_id)
        .join(Signal, Signal.id == VirtualPortfolio.signal_id)
        .where(
            Signal.instrument_id == instrument_id,
            VirtualPortfolio.status.in_(["open", "partial"]),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def count_open_positions_in_group(
    session: AsyncSession,
    symbols: set[str],
    direction: str,
) -> int:
    """Return number of open/partial positions for any symbol in the group with given direction.

    Used by SIM-21 correlation guard.
    """
    from src.database.models import Instrument as _Instrument

    result = await session.execute(
        select(func.count(VirtualPortfolio.signal_id))
        .join(Signal, Signal.id == VirtualPortfolio.signal_id)
        .join(_Instrument, _Instrument.id == Signal.instrument_id)
        .where(
            _Instrument.symbol.in_(symbols),
            Signal.direction == direction,
            VirtualPortfolio.status.in_(["open", "partial"]),
        )
    )
    return result.scalar_one() or 0


async def is_position_blocked_by_correlation(
    session: AsyncSession,
    instrument_id: int,
    symbol: str,
    direction: str,
) -> tuple[bool, str]:
    """Check if a new position is blocked by existing open positions (SIM-21).

    Rules:
      1. Same instrument (any timeframe) → always blocked.
      2. Same correlated group + same direction → blocked if count >= CROSS_GROUP_MAX.
      3. Opposite direction in same group → allowed (hedge).
      4. Symbol not in any group → allowed.

    Returns (is_blocked, reason_string).
    """
    from src.signals.portfolio_risk import CROSS_GROUP_MAX, get_correlation_group

    # Rule 1: direct instrument guard
    if await has_open_position_for_instrument(session, instrument_id):
        return True, f"open position exists for instrument_id={instrument_id} ({symbol})"

    # Rule 2: correlation guard
    group = get_correlation_group(symbol)
    if group:
        open_count = await count_open_positions_in_group(session, group, direction)
        if open_count >= CROSS_GROUP_MAX:
            return True, (
                f"correlation group limit reached: "
                f"{open_count} {direction} position(s) already open in group"
            )

    return False, "OK"


async def update_virtual_position(
    session: AsyncSession, signal_id: int, updates: dict[str, Any]
) -> None:
    await session.execute(
        update(VirtualPortfolio)
        .where(VirtualPortfolio.signal_id == signal_id)
        .values(**updates)
    )
    await session.flush()


async def close_virtual_position(
    session: AsyncSession,
    signal_id: int,
    close_price: Decimal,
    entry_price: Decimal,
    direction: str,
    status: str = "closed",
) -> None:
    """Mark position as closed and compute realized PnL %."""
    if direction == "LONG":
        pnl_pct = (close_price - entry_price) / entry_price * Decimal("100")
    else:
        pnl_pct = (entry_price - close_price) / entry_price * Decimal("100")

    await session.execute(
        update(VirtualPortfolio)
        .where(VirtualPortfolio.signal_id == signal_id)
        .values(
            status=status,
            closed_at=datetime.datetime.now(datetime.timezone.utc),
            current_price=close_price,
            realized_pnl_pct=pnl_pct.quantize(Decimal("0.0001")),
            unrealized_pnl_pct=Decimal("0"),
        )
    )
    await session.flush()


# ── SystemEvent ───────────────────────────────────────────────────────────────


async def create_system_event(session: AsyncSession, data: dict[str, Any]) -> SystemEvent:
    event = SystemEvent(**data)
    session.add(event)
    await session.flush()
    return event


async def get_system_events(
    session: AsyncSession,
    level: Optional[str] = None,
    event_type: Optional[str] = None,
    symbol: Optional[str] = None,
    limit: int = 100,
) -> Sequence[SystemEvent]:
    stmt = select(SystemEvent).order_by(SystemEvent.created_at.desc()).limit(limit)
    if level:
        stmt = stmt.where(SystemEvent.level == level)
    if event_type:
        stmt = stmt.where(SystemEvent.event_type == event_type)
    if symbol:
        stmt = stmt.where(SystemEvent.symbol == symbol)
    result = await session.execute(stmt)
    return result.scalars().all()


async def cleanup_system_events(session: AsyncSession, days: int = 3) -> int:
    """Delete system events older than `days` days. Returns count deleted."""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    result = await session.execute(
        delete(SystemEvent).where(SystemEvent.created_at < cutoff)
    )
    await session.flush()
    return result.rowcount or 0


async def cleanup_news_events(session: AsyncSession, days: int = 2) -> int:
    """Delete news articles older than `days` days. Returns count deleted."""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    result = await session.execute(
        delete(NewsEvent).where(NewsEvent.published_at < cutoff)
    )
    await session.flush()
    return result.rowcount or 0


async def delete_all_system_events(session: AsyncSession) -> int:
    """Delete ALL system events. Returns count deleted."""
    result = await session.execute(delete(SystemEvent))
    await session.flush()
    return result.rowcount or 0


# ── VirtualAccount (SIM-16) ───────────────────────────────────────────────────


async def get_virtual_account(session: AsyncSession) -> Optional[VirtualAccount]:
    """Return the single virtual account record (id=1)."""
    result = await session.execute(
        select(VirtualAccount).order_by(VirtualAccount.id.asc()).limit(1)
    )
    return result.scalar_one_or_none()


async def update_virtual_account(session: AsyncSession, data: dict[str, Any]) -> None:
    """Update fields of the virtual account (assumes exactly one row)."""
    account = await get_virtual_account(session)
    if account is None:
        return
    await session.execute(
        update(VirtualAccount).where(VirtualAccount.id == account.id).values(**data)
    )
    await session.flush()


async def create_virtual_account_if_not_exists(session: AsyncSession) -> VirtualAccount:
    """Create the virtual account row if it does not exist yet."""
    from src.config import settings  # noqa: PLC0415

    account = await get_virtual_account(session)
    if account is not None:
        return account

    initial = Decimal(str(settings.VIRTUAL_ACCOUNT_SIZE_USD))
    account = VirtualAccount(
        initial_balance=initial,
        current_balance=initial,
        peak_balance=initial,
        total_realized_pnl=Decimal("0"),
        total_trades=0,
    )
    session.add(account)
    await session.flush()
    await session.refresh(account)
    return account


# ── OrderFlowData (SIM-13: funding rate) ──────────────────────────────────────


async def get_latest_funding_rate(
    session: AsyncSession, instrument_id: int
) -> Optional[Decimal]:
    """Return the most recent funding_rate from order_flow_data for the instrument."""
    result = await session.execute(
        select(OrderFlowData.funding_rate)
        .where(
            and_(
                OrderFlowData.instrument_id == instrument_id,
                OrderFlowData.funding_rate.isnot(None),
            )
        )
        .order_by(OrderFlowData.timestamp.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return Decimal(str(row)) if row is not None else None


async def get_portfolio_pnl(session: AsyncSession) -> dict[str, Any]:
    """Aggregate open and closed PnL."""
    open_positions = await get_open_positions(session)
    result = await session.execute(
        select(VirtualPortfolio).where(
            VirtualPortfolio.status.notin_(["open", "partial"])
        )
    )
    closed_positions = result.scalars().all()

    open_pnl = sum(
        float(p.unrealized_pnl_pct or 0) for p in open_positions
    )
    realized_pnl = sum(
        float(p.realized_pnl_pct or 0) for p in closed_positions
    )

    return {
        "open_count": len(open_positions),
        "open_unrealized_pnl_pct": round(open_pnl, 4),
        "closed_count": len(closed_positions),
        "realized_pnl_pct": round(realized_pnl, 4),
        "total_pnl_pct": round(open_pnl + realized_pnl, 4),
    }
