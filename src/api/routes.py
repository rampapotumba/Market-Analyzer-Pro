"""FastAPI REST API routes."""

import datetime
import json
import logging
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.price_collector import CcxtCollector, YFinanceCollector
from src.database.crud import (
    get_accuracy_stats,
    get_active_signals,
    get_all_instruments,
    get_instrument_by_symbol,
    get_latest_signal_for_instrument,
    get_macro_data,
    get_news_events,
    get_price_data,
    get_signal_by_id,
    get_signals,
    get_upcoming_economic_events,
)
from src.database.engine import get_session
from src.signals.signal_engine import SignalEngine
from src.tracker.accuracy import AccuracyTracker

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


# ── Pydantic Schemas ──────────────────────────────────────────────────────────


class InstrumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    market: str
    name: str
    pip_size: Decimal
    is_active: bool


class PriceDataResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timestamp: datetime.datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    timeframe: str


class SignalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    instrument_id: int
    timeframe: str
    created_at: datetime.datetime
    direction: str
    signal_strength: str
    entry_price: Optional[Decimal]
    stop_loss: Optional[Decimal]
    take_profit_1: Optional[Decimal]
    take_profit_2: Optional[Decimal]
    risk_reward: Optional[Decimal]
    position_size_pct: Optional[Decimal]
    composite_score: Decimal
    ta_score: Decimal
    fa_score: Decimal
    sentiment_score: Decimal
    geo_score: Decimal
    confidence: float
    horizon: Optional[str]
    reasoning: Optional[str]
    indicators_snapshot: Optional[str]
    status: str
    expires_at: Optional[datetime.datetime]


class MacroDataResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    indicator_name: str
    country: str
    value: Optional[Decimal]
    previous_value: Optional[Decimal]
    release_date: Optional[datetime.datetime]
    source: Optional[str]


class NewsEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    headline: str
    summary: Optional[str]
    source: Optional[str]
    url: Optional[str]
    published_at: Optional[datetime.datetime]
    sentiment_score: Optional[Decimal]
    importance: str
    category: Optional[str]


class AccuracyResponse(BaseModel):
    total_signals: int
    wins: int
    losses: int
    breakevens: int
    win_rate: Optional[float]
    profit_factor: Optional[float]
    avg_win_pips: Optional[float]
    avg_loss_pips: Optional[float]
    sharpe_ratio: Optional[float]
    max_drawdown_pct: Optional[float]
    expectancy: Optional[float]


class HealthResponse(BaseModel):
    status: str
    timestamp: datetime.datetime
    database: str
    version: str


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_session)) -> HealthResponse:
    """Health check endpoint."""
    try:
        instruments = await get_all_instruments(db, active_only=False)
        db_status = f"ok ({len(instruments)} instruments)"
    except Exception as e:
        db_status = f"error: {e}"

    return HealthResponse(
        status="ok",
        timestamp=datetime.datetime.now(datetime.timezone.utc),
        database=db_status,
        version="1.0.0",
    )


@router.get("/instruments", response_model=list[InstrumentResponse])
async def list_instruments(
    db: AsyncSession = Depends(get_session),
) -> list[InstrumentResponse]:
    """Get all active instruments."""
    instruments = await get_all_instruments(db, active_only=True)
    return [InstrumentResponse.model_validate(i) for i in instruments]


@router.get("/prices/{symbol:path}", response_model=list[PriceDataResponse])
async def get_prices(
    symbol: str,
    timeframe: str = Query("H1", description="Timeframe: M1/M5/M15/H1/H4/D1/W1"),
    from_dt: Optional[datetime.datetime] = Query(None, alias="from"),
    to_dt: Optional[datetime.datetime] = Query(None, alias="to"),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_session),
) -> list[PriceDataResponse]:
    """Get price data for a symbol."""
    instrument = await get_instrument_by_symbol(db, symbol)
    if not instrument:
        raise HTTPException(status_code=404, detail=f"Instrument {symbol} not found")

    records = await get_price_data(
        db, instrument.id, timeframe, from_dt, to_dt, limit
    )
    return [
        PriceDataResponse(
            timestamp=r.timestamp,
            open=r.open,
            high=r.high,
            low=r.low,
            close=r.close,
            volume=r.volume,
            timeframe=r.timeframe,
        )
        for r in records
    ]


@router.post("/analyze/{symbol:path}")
async def analyze_symbol(
    symbol: str,
    timeframe: str = Query("H4", description="Timeframe: M1/M5/M15/H1/H4/D1/W1"),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Run analysis for a symbol and generate a trading signal.
    Auto-collects price data if not enough exists in the DB.
    Returns the signal if actionable, or a no_signal response with reason for HOLD.
    """
    instrument = await get_instrument_by_symbol(db, symbol)
    if not instrument:
        raise HTTPException(status_code=404, detail=f"Instrument {symbol} not found")

    # Check if we have enough price data; if not, collect it first
    existing = await get_price_data(db, instrument.id, timeframe, limit=35)
    if len(existing) < 30:
        logger.info(
            f"[Analyze] Not enough data for {symbol}/{timeframe} "
            f"({len(existing)} records). Auto-collecting..."
        )
        try:
            if instrument.market == "crypto":
                collector = CcxtCollector()
                result = await collector.collect_latest(symbol, timeframe, n_candles=300)
            else:
                collector = YFinanceCollector()
                result = await collector.collect_latest(symbol, timeframe, n_candles=300)
            logger.info(
                f"[Analyze] Auto-collected {result.records_count} records for {symbol}/{timeframe}"
            )
        except Exception as exc:
            logger.error(f"[Analyze] Auto-collect failed for {symbol}: {exc}")

    engine = SignalEngine()
    try:
        signal = await engine.generate_signal(instrument, timeframe, db)
    except Exception as exc:
        logger.error(f"Signal generation error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    if signal is None:
        # No actionable signal (HOLD zone, cooldown, or insufficient data)
        # Return 200 with a no_signal status so the frontend can display analysis info
        return {
            "status": "no_signal",
            "direction": "HOLD",
            "signal_strength": "HOLD",
            "symbol": symbol,
            "timeframe": timeframe,
            "composite_score": "0",
            "confidence": 0.0,
            "horizon": None,
            "entry_price": None,
            "stop_loss": None,
            "take_profit_1": None,
            "take_profit_2": None,
            "risk_reward": None,
            "ta_score": "0",
            "fa_score": "0",
            "sentiment_score": "0",
            "geo_score": "0",
            "reasoning": None,
            "indicators_snapshot": None,
            "message": (
                f"No actionable signal for {symbol}/{timeframe}. "
                "Market is in consolidation or cooldown is active. "
                "Analysis is running — try again shortly or switch timeframe."
            ),
        }

    data = SignalResponse.model_validate(signal).model_dump()
    data["status_ok"] = True
    return data


@router.get("/signals", response_model=list[SignalResponse])
async def list_signals(
    status: Optional[str] = Query(None),
    market: Optional[str] = Query(None),
    from_dt: Optional[datetime.datetime] = Query(None, alias="from"),
    to_dt: Optional[datetime.datetime] = Query(None, alias="to"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_session),
) -> list[SignalResponse]:
    """Get signals with optional filters."""
    signals = await get_signals(db, status=status, market=market, from_dt=from_dt, to_dt=to_dt, limit=limit)
    return [SignalResponse.model_validate(s) for s in signals]


@router.get("/signals/latest/{symbol:path}", response_model=Optional[SignalResponse])
async def get_latest_signal(
    symbol: str,
    timeframe: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_session),
) -> Optional[SignalResponse]:
    """Get the most recent signal for an instrument."""
    instrument = await get_instrument_by_symbol(db, symbol)
    if not instrument:
        raise HTTPException(status_code=404, detail=f"Instrument {symbol} not found")
    signal = await get_latest_signal_for_instrument(db, instrument.id, timeframe)
    if not signal:
        return None
    return SignalResponse.model_validate(signal)


@router.get("/signals/active", response_model=list[SignalResponse])
async def get_active(
    db: AsyncSession = Depends(get_session),
) -> list[SignalResponse]:
    """Get all active signals."""
    signals = await get_active_signals(db)
    return [SignalResponse.model_validate(s) for s in signals]


@router.get("/signals/{signal_id}", response_model=SignalResponse)
async def get_signal(
    signal_id: int,
    db: AsyncSession = Depends(get_session),
) -> SignalResponse:
    """Get a specific signal by ID."""
    signal = await get_signal_by_id(db, signal_id)
    if not signal:
        raise HTTPException(status_code=404, detail=f"Signal {signal_id} not found")
    return SignalResponse.model_validate(signal)


@router.get("/accuracy", response_model=AccuracyResponse)
async def get_accuracy(
    period: str = Query("all_time", description="all_time/monthly/weekly"),
    db: AsyncSession = Depends(get_session),
) -> AccuracyResponse:
    """Get accuracy statistics."""
    tracker = AccuracyTracker()
    metrics = await tracker.calculate_stats(db, period=period)

    def _to_float(v):
        if v is None:
            return None
        return float(v)

    return AccuracyResponse(
        total_signals=metrics["total_signals"],
        wins=metrics["wins"],
        losses=metrics["losses"],
        breakevens=metrics["breakevens"],
        win_rate=_to_float(metrics["win_rate"]),
        profit_factor=_to_float(metrics["profit_factor"]),
        avg_win_pips=_to_float(metrics["avg_win_pips"]),
        avg_loss_pips=_to_float(metrics["avg_loss_pips"]),
        sharpe_ratio=_to_float(metrics["sharpe_ratio"]),
        max_drawdown_pct=_to_float(metrics["max_drawdown_pct"]),
        expectancy=_to_float(metrics["expectancy"]),
    )


@router.get("/macro/{country}", response_model=list[MacroDataResponse])
async def get_macro(
    country: str,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_session),
) -> list[MacroDataResponse]:
    """Get macro data for a country."""
    records = await get_macro_data(db, country=country.upper(), limit=limit)
    return [MacroDataResponse.model_validate(r) for r in records]


@router.get("/news", response_model=list[NewsEventResponse])
async def get_news(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_session),
) -> list[NewsEventResponse]:
    """Get recent news events."""
    events = await get_news_events(db, limit=limit)
    return [NewsEventResponse.model_validate(e) for e in events]


# ── Economic Calendar ──────────────────────────────────────────────────────────


class EconomicEventItem(BaseModel):
    """Single upcoming economic event."""
    event_date: datetime.datetime
    country: str
    currency: str
    event_name: str
    impact: str
    previous: Optional[Decimal]
    estimate: Optional[Decimal]
    actual: Optional[Decimal]
    unit: Optional[str]


@router.get("/calendar/upcoming")
async def get_upcoming_calendar(
    days: int = Query(7, ge=1, le=14),
    db: AsyncSession = Depends(get_session),
) -> dict[str, list[EconomicEventItem]]:
    """
    Return upcoming economic events for the next N days, indexed by instrument symbol.

    Response shape:
    {
        "EURUSD=X": [ { event_date, event_name, impact, ... }, ... ],
        "BTC/USDT":  [ ... ],
        ...
    }
    Events are sorted by date ascending within each symbol.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    end = now + datetime.timedelta(days=days)
    events = await get_upcoming_economic_events(db, from_dt=now, to_dt=end)

    result: dict[str, list[EconomicEventItem]] = {}
    for ev in events:
        try:
            instruments: list[str] = json.loads(ev.related_instruments or "[]")
        except Exception:
            instruments = []

        item = EconomicEventItem(
            event_date=ev.event_date,
            country=ev.country,
            currency=ev.currency,
            event_name=ev.event_name,
            impact=ev.impact,
            previous=ev.previous,
            estimate=ev.estimate,
            actual=ev.actual,
            unit=ev.unit,
        )
        for sym in instruments:
            result.setdefault(sym, []).append(item)

    return result


@router.post("/calendar/refresh")
async def refresh_calendar() -> dict[str, Any]:
    """Trigger immediate calendar data collection."""
    try:
        from src.collectors.fmp_calendar_collector import FMPCalendarCollector
        collector = FMPCalendarCollector()
        result = await collector.collect()
        return {"success": result.success, "records_count": result.records_count}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
