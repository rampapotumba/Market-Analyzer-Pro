"""REST API v2 — all endpoints per ARCHITECTURE.md section 4.1."""

import asyncio
import datetime
import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from collections import defaultdict
from sqlalchemy import delete, func, over, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.crud import (
    create_backtest_run,
    delete_backtest_run,
    get_backtest_trades,
    get_active_signals,
    get_all_instruments,
    get_backtest_results,
    get_backtest_run,
    get_instrument_by_symbol,
    get_latest_signal_for_instrument,
    get_open_positions,
    get_price_data,
    get_signal_by_id,
    get_signals,
    get_upcoming_economic_events,
    get_virtual_account,
    list_backtest_runs,
)
from src.database.engine import async_session_factory
from src.database.models import (
    AccuracyStats,
    BacktestRun,
    BacktestTrade,
    CentralBankRate,
    Instrument,
    MacroData,
    OrderFlowData,
    RegimeState,
    Signal,
    SignalResult,
    VirtualAccount,
    VirtualPortfolio,
)

logger = logging.getLogger(__name__)

router_v2 = APIRouter(prefix="/api/v2", tags=["v2"])


# ── Dependency ────────────────────────────────────────────────────────────────

async def get_session():  # noqa: D401
    async with async_session_factory() as session:
        yield session


# ── Pydantic response models ──────────────────────────────────────────────────

class SignalV2(BaseModel):
    id: int
    instrument_id: int
    symbol: Optional[str] = None
    timeframe: str
    direction: str
    signal_strength: str
    composite_score: float
    ta_score: float
    fa_score: float
    sentiment_score: float
    geo_score: float
    of_score: Optional[float]
    confidence: float
    regime: Optional[str]
    entry_price: Optional[float]
    stop_loss: Optional[float]
    take_profit_1: Optional[float]
    take_profit_2: Optional[float]
    take_profit_3: Optional[float]
    risk_reward: Optional[float]
    position_size_pct: Optional[float]
    earnings_days_ahead: Optional[int]
    portfolio_heat: Optional[float]
    status: str
    created_at: datetime.datetime
    # LLM (Claude) analysis
    llm_score: Optional[float] = None
    llm_bias: Optional[str] = None
    llm_confidence: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)


class SignalDetailV2(SignalV2):
    """Extended signal response with Claude's full reasoning."""
    llm_reasoning: Optional[str] = None
    llm_key_factors: list[str] = []

    @classmethod
    def from_signal(cls, signal: Any) -> "SignalDetailV2":
        data = {c: getattr(signal, c, None) for c in SignalV2.model_fields if c != "symbol"}
        data["symbol"] = signal.instrument.symbol if signal.instrument else None
        reasoning = {}
        if signal.reasoning:
            try:
                reasoning = json.loads(signal.reasoning)
            except (ValueError, TypeError):
                pass
        data["llm_reasoning"] = reasoning.get("llm_reasoning") or None
        data["llm_key_factors"] = [
            f.removeprefix("Claude: ")
            for f in reasoning.get("factors", [])
            if f.startswith("Claude: ")
        ]
        return cls(**data)


class InstrumentV2(BaseModel):
    id: int
    symbol: str
    market: str
    name: str
    is_active: bool
    sector: Optional[str]
    base_currency: Optional[str]
    quote_currency: Optional[str]
    central_bank: Optional[str]

    model_config = ConfigDict(from_attributes=True)


class PortfolioPosition(BaseModel):
    id: int
    signal_id: int
    status: str
    size_pct: float
    entry_price: float
    current_price: Optional[float]
    unrealized_pnl_pct: Optional[float]
    realized_pnl_pct: Optional[float]
    opened_at: datetime.datetime
    closed_at: Optional[datetime.datetime]

    model_config = ConfigDict(from_attributes=True)


class PortfolioHeat(BaseModel):
    total_positions: int
    portfolio_heat_pct: float
    max_heat_pct: float
    heat_remaining_pct: float


class PriceV2(BaseModel):
    timestamp: datetime.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    model_config = ConfigDict(from_attributes=True)


class RegimeResponse(BaseModel):
    regime: str
    adx: Optional[float]
    atr_percentile: Optional[float]
    detected_at: datetime.datetime

    model_config = ConfigDict(from_attributes=True)


class AccuracyV2(BaseModel):
    period: str
    market: Optional[str]
    timeframe: Optional[str]
    total_signals: int
    wins: int
    losses: int
    win_rate: Optional[float]
    profit_factor: Optional[float]
    avg_win_pips: Optional[float]
    avg_loss_pips: Optional[float]
    sharpe_ratio: Optional[float]
    max_drawdown_pct: Optional[float]

    model_config = ConfigDict(from_attributes=True)


class BacktestRunResponse(BaseModel):
    """SIM-22 v4 schema — lightweight, no summary/params payload."""
    id: str
    status: str
    progress_pct: Optional[float] = None
    started_at: Optional[datetime.datetime] = None
    completed_at: Optional[datetime.datetime] = None
    # params returned as parsed dict so frontend can show symbols/TF without heavy summary
    params: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class BacktestTradeResponse(BaseModel):
    """SIM-22 v4 schema."""
    id: int
    run_id: str
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    direction: Optional[str] = None
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl_pips: Optional[float] = None
    pnl_usd: Optional[float] = None
    result: Optional[str] = None
    composite_score: Optional[float] = None
    entry_at: Optional[datetime.datetime] = None
    exit_at: Optional[datetime.datetime] = None
    duration_minutes: Optional[int] = None
    mfe: Optional[float] = None
    mae: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)


class OrderFlowResponse(BaseModel):
    timestamp: datetime.datetime
    cvd: Optional[float]
    funding_rate: Optional[float]
    open_interest: Optional[float]
    open_interest_prev: Optional[float]

    model_config = ConfigDict(from_attributes=True)


class MacroRateResponse(BaseModel):
    country: str
    indicator: str
    value: Optional[float]
    collected_at: Optional[datetime.datetime]

    model_config = ConfigDict(from_attributes=True)


# ── Signals ───────────────────────────────────────────────────────────────────

@router_v2.get("/signals", response_model=list[SignalV2])
async def list_signals_v2(
    market: Optional[str] = None,
    timeframe: Optional[str] = None,
    status: Optional[str] = None,
    regime: Optional[str] = None,
    date_from: Optional[datetime.datetime] = None,
    date_to: Optional[datetime.datetime] = None,
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_session),
):
    signals = await get_signals(
        db,
        market=market,
        status=status,
        limit=limit,
    )
    # Apply additional filters not in base crud
    result = []
    for s in signals:
        if timeframe and s.timeframe != timeframe:
            continue
        if regime and s.regime != regime:
            continue
        if date_from and s.created_at < date_from:
            continue
        if date_to and s.created_at > date_to:
            continue
        d = {f: getattr(s, f, None) for f in SignalV2.model_fields if f != "symbol"}
        d["symbol"] = s.instrument.symbol if s.instrument else None
        result.append(SignalV2(**d))
    return result


@router_v2.get("/signals/active", response_model=list[SignalV2])
async def get_active_signals_v2(
    db: AsyncSession = Depends(get_session),
):
    """Get all active (created/tracking) signals."""
    signals = await get_active_signals(db)
    result = []
    for s in signals:
        d = {f: getattr(s, f, None) for f in SignalV2.model_fields if f != "symbol"}
        d["symbol"] = s.instrument.symbol if s.instrument else None
        result.append(SignalV2(**d))
    return result


@router_v2.get("/signals/latest/{symbol:path}")
async def get_latest_signal(
    symbol: str,
    timeframe: str = Query("H1"),
    db: AsyncSession = Depends(get_session),
):
    """Return the most recent signal for a symbol/timeframe (used by dashboard signal panel)."""
    instrument = await get_instrument_by_symbol(db, symbol)
    if instrument is None:
        raise HTTPException(status_code=404, detail=f"Instrument {symbol!r} not found")

    signal = await get_latest_signal_for_instrument(db, instrument.id, timeframe)
    if signal is None:
        return None

    return {
        "status": signal.status,
        "id": signal.id,
        "symbol": symbol,
        "timeframe": signal.timeframe,
        "direction": signal.direction,
        "signal_strength": signal.signal_strength,
        "composite_score": float(signal.composite_score),
        "ta_score": float(signal.ta_score),
        "fa_score": float(signal.fa_score),
        "sentiment_score": float(signal.sentiment_score),
        "geo_score": float(signal.geo_score),
        "confidence": float(signal.confidence),
        "entry_price": float(signal.entry_price) if signal.entry_price else None,
        "stop_loss": float(signal.stop_loss) if signal.stop_loss else None,
        "take_profit_1": float(signal.take_profit_1) if signal.take_profit_1 else None,
        "take_profit_2": float(signal.take_profit_2) if signal.take_profit_2 else None,
        "risk_reward": float(signal.risk_reward) if signal.risk_reward else None,
        "position_size_pct": float(signal.position_size_pct) if signal.position_size_pct else None,
        "horizon": signal.horizon,
        "reasoning": signal.reasoning,
        "indicators_snapshot": signal.indicators_snapshot,
        "created_at": signal.created_at.isoformat(),
    }


@router_v2.get("/signals/{signal_id}", response_model=SignalDetailV2)
async def get_signal_v2(
    signal_id: int,
    db: AsyncSession = Depends(get_session),
):
    signal = await get_signal_by_id(db, signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    return SignalDetailV2.from_signal(signal)


@router_v2.post("/signals/{signal_id}/feedback")
async def signal_feedback(
    signal_id: int,
    feedback: dict[str, Any],
    db: AsyncSession = Depends(get_session),
):
    signal = await get_signal_by_id(db, signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    # Store feedback in reasoning JSON
    existing = json.loads(signal.reasoning or "{}")
    existing["operator_feedback"] = feedback
    await db.execute(
        update(Signal)
        .where(Signal.id == signal_id)
        .values(reasoning=json.dumps(existing))
    )
    await db.commit()
    return {"status": "ok"}


# ── Instruments ───────────────────────────────────────────────────────────────

@router_v2.get("/instruments", response_model=list[InstrumentV2])
async def list_instruments_v2(
    market: Optional[str] = None,
    db: AsyncSession = Depends(get_session),
):
    instruments = await get_all_instruments(db, active_only=True)
    if market:
        instruments = [i for i in instruments if i.market == market]
    return instruments


@router_v2.get("/regime")
async def list_all_regimes(db: AsyncSession = Depends(get_session)):
    """Return latest regime state for each instrument."""
    from sqlalchemy import func
    subq = (
        select(RegimeState.instrument_id, func.max(RegimeState.detected_at).label("max_dt"))
        .group_by(RegimeState.instrument_id)
        .subquery()
    )
    result = await db.execute(
        select(RegimeState).join(
            subq,
            (RegimeState.instrument_id == subq.c.instrument_id) &
            (RegimeState.detected_at == subq.c.max_dt),
        )
    )
    rows = result.scalars().all()
    return [
        {
            "instrument_id": r.instrument_id,
            "regime": r.regime,
            "adx": float(r.adx) if r.adx is not None else None,
            "atr_percentile": float(r.atr_percentile) if r.atr_percentile is not None else None,
            "vix": None,
            "detected_at": r.detected_at.isoformat(),
        }
        for r in rows
    ]


@router_v2.get("/instruments/{instrument_id}/regime", response_model=Optional[RegimeResponse])
async def get_instrument_regime(
    instrument_id: int,
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(
        select(RegimeState)
        .where(RegimeState.instrument_id == instrument_id)
        .order_by(RegimeState.detected_at.desc())
        .limit(1)
    )
    regime = result.scalar_one_or_none()
    if regime is None:
        raise HTTPException(status_code=404, detail="No regime data for instrument")
    return regime


# ── Portfolio ─────────────────────────────────────────────────────────────────

@router_v2.get("/portfolio", response_model=list[PortfolioPosition])
async def get_portfolio(db: AsyncSession = Depends(get_session)):
    positions = await get_open_positions(db)
    return list(positions)


@router_v2.get("/portfolio/heat", response_model=PortfolioHeat)
async def get_portfolio_heat(db: AsyncSession = Depends(get_session)):
    from src.config import settings

    positions = await get_open_positions(db)
    heat = sum(float(p.size_pct or 0) for p in positions)
    max_heat = settings.MAX_PORTFOLIO_HEAT

    return PortfolioHeat(
        total_positions=len(positions),
        portfolio_heat_pct=round(heat, 2),
        max_heat_pct=max_heat,
        heat_remaining_pct=round(max_heat - heat, 2),
    )


# ── Accuracy ──────────────────────────────────────────────────────────────────

@router_v2.get("/accuracy", response_model=list[AccuracyV2])
async def get_accuracy_v2(db: AsyncSession = Depends(get_session)):
    result = await db.execute(select(AccuracyStats))
    return list(result.scalars().all())


@router_v2.get("/accuracy/{market}", response_model=list[AccuracyV2])
async def get_accuracy_by_market(
    market: str,
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(
        select(AccuracyStats).where(AccuracyStats.market == market)
    )
    return list(result.scalars().all())


@router_v2.get("/accuracy/{market}/{timeframe}", response_model=list[AccuracyV2])
async def get_accuracy_by_market_tf(
    market: str,
    timeframe: str,
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(
        select(AccuracyStats)
        .where(AccuracyStats.market == market)
        .where(AccuracyStats.timeframe == timeframe)
    )
    return list(result.scalars().all())


# ── Backtesting ───────────────────────────────────────────────────────────────

@router_v2.get("/backtest/list", response_model=list[BacktestRunResponse])
async def list_backtests_v4(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_session),
):
    """SIM-22: List all backtest runs, newest first."""
    from src.backtesting.backtest_engine import get_backtest_progress
    runs = await list_backtest_runs(db, limit=limit)
    result = []
    for run in runs:
        d = BacktestRunResponse.model_validate(run)
        if run.status == "running":
            d.progress_pct = get_backtest_progress(run.id)
        result.append(d)
    return result


@router_v2.get("/backtest/{run_id}/status")
async def get_backtest_status(run_id: str, db: AsyncSession = Depends(get_session)):
    """SIM-22: Current status of a backtest run."""
    from src.backtesting.backtest_engine import get_backtest_progress
    run = await get_backtest_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    progress = get_backtest_progress(run_id) if run.status == "running" else None
    return {
        "run_id": run.id,
        "status": run.status,
        "progress_pct": progress,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


@router_v2.get("/backtest/{run_id}/results")
async def get_backtest_results_endpoint(
    run_id: str,
    db: AsyncSession = Depends(get_session),
):
    """SIM-22: Full backtest results: summary + trade list."""
    results = await get_backtest_results(db, run_id)
    if not results:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    return results


@router_v2.post("/backtest/run", status_code=202)
async def run_backtest_v4(
    body: dict,
    db: AsyncSession = Depends(get_session),
):
    """SIM-22: Start a new backtest run (async, non-blocking).

    Body: {symbols, timeframe, start_date, end_date, account_size,
           apply_slippage, apply_swap}

    Returns run_id immediately; poll /backtest/{run_id}/status for progress.
    """
    from src.backtesting.backtest_engine import BacktestEngine
    from src.backtesting.backtest_params import BacktestParams
    import asyncio

    try:
        params = BacktestParams(**body)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Create the run record immediately so caller gets run_id
    run_id = await create_backtest_run(db, params.model_dump(mode="json"))
    await db.commit()

    # Launch simulation in background (fire-and-forget)
    async def _run_bg() -> None:
        from src.database.engine import async_session_factory
        from src.backtesting.backtest_engine import BacktestEngine
        async with async_session_factory() as bg_session:
            engine = BacktestEngine(bg_session)
            # Use the existing run_id but skip create_backtest_run (already done)
            from src.database.crud import update_backtest_run, create_backtest_trades_bulk
            from src.backtesting.backtest_engine import _compute_summary
            await update_backtest_run(bg_session, run_id, "running")
            await bg_session.commit()
            try:
                trades, filter_stats = await engine._simulate(params, run_id=run_id)
                summary = _compute_summary(trades, params.account_size, filter_stats=filter_stats)
                trade_dicts = [
                    {
                        "symbol": t.symbol, "timeframe": t.timeframe,
                        "direction": t.direction,
                        "entry_price": str(t.entry_price),
                        "exit_price": str(t.exit_price) if t.exit_price else None,
                        "exit_reason": t.exit_reason,
                        "pnl_pips": str(t.pnl_pips) if t.pnl_pips else None,
                        "pnl_usd": str(t.pnl_usd) if t.pnl_usd else None,
                        "result": t.result,
                        "composite_score": str(t.composite_score) if t.composite_score else None,
                        "entry_at": t.entry_at, "exit_at": t.exit_at,
                        "duration_minutes": t.duration_minutes,
                        "mfe": str(t.mfe) if t.mfe else None,
                        "mae": str(t.mae) if t.mae else None,
                        "regime": t.regime,
                    }
                    for t in trades
                ]
                await create_backtest_trades_bulk(bg_session, run_id, trade_dicts)
                await update_backtest_run(bg_session, run_id, "completed", summary)
                await bg_session.commit()
                logger.info("[SIM-22] Background backtest %s completed: %d trades", run_id, len(trades))
            except Exception as exc:
                logger.error("[SIM-22] Background backtest %s failed: %s", run_id, exc)
                await update_backtest_run(bg_session, run_id, "failed", {"error": str(exc)})
                await bg_session.commit()
            finally:
                from src.backtesting.backtest_engine import _backtest_progress
                _backtest_progress.pop(run_id, None)

    asyncio.create_task(_run_bg())

    return {"run_id": run_id, "status": "running"}


@router_v2.get("/backtest/{run_id}/trades")
async def get_backtest_trades_endpoint(
    run_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_session),
):
    """Paginated trade list for a backtest run."""
    trades, total = await get_backtest_trades(db, run_id, limit=limit, offset=offset)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "trades": [
            {
                "id": t.id,
                "symbol": t.symbol,
                "direction": t.direction,
                "entry_price": float(t.entry_price) if t.entry_price is not None else None,
                "exit_price": float(t.exit_price) if t.exit_price is not None else None,
                "exit_reason": t.exit_reason,
                "pnl_pips": float(t.pnl_pips) if t.pnl_pips is not None else None,
                "pnl_usd": float(t.pnl_usd) if t.pnl_usd is not None else None,
                "result": t.result,
                "entry_at": t.entry_at.isoformat() if t.entry_at else None,
                "exit_at": t.exit_at.isoformat() if t.exit_at else None,
                "duration_minutes": t.duration_minutes,
            }
            for t in trades
        ],
    }


@router_v2.delete("/backtest/{run_id}", status_code=204)
async def delete_backtest_run_endpoint(
    run_id: str,
    db: AsyncSession = Depends(get_session),
):
    """SIM-22: Delete a backtest run and all its trades."""
    deleted = await delete_backtest_run(db, run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    await db.commit()


# ── Macroeconomics ────────────────────────────────────────────────────────────

@router_v2.get("/macroeconomics")
async def get_macro_all(db: AsyncSession = Depends(get_session)):
    # Get the latest 2 records per (country, indicator_name) using a window function.
    # This ensures all indicators appear regardless of how many historical records exist.
    row_num = over(
        func.row_number(),
        partition_by=[MacroData.country, MacroData.indicator_name],
        order_by=MacroData.release_date.desc(),
    ).label("rn")

    subq = select(MacroData, row_num).subquery()

    result = await db.execute(
        select(subq).where(subq.c.rn <= 2).order_by(
            subq.c.country, subq.c.indicator_name, subq.c.release_date.desc()
        )
    )
    rows = result.mappings().all()
    return [
        {
            "country": r["country"],
            "indicator": r["indicator_name"],
            "value": float(r["value"]) if r["value"] is not None else None,
            "previous_value": float(r["previous_value"]) if r["previous_value"] is not None else None,
            "release_date": r["release_date"],
        }
        for r in rows
    ]


@router_v2.get("/macroeconomics/rates")
async def get_interest_rates(db: AsyncSession = Depends(get_session)):
    result = await db.execute(
        select(CentralBankRate).order_by(CentralBankRate.effective_date.desc())
    )
    rates = result.scalars().all()
    return [
        {
            "bank": r.bank,
            "rate": float(r.rate) if r.rate is not None else None,
            "effective_date": r.effective_date,
        }
        for r in rates
    ]


@router_v2.get("/macroeconomics/calendar")
async def get_macro_calendar(db: AsyncSession = Depends(get_session)):
    now = datetime.datetime.now(datetime.timezone.utc)
    events = await get_upcoming_economic_events(
        db,
        from_dt=now,
        to_dt=now + datetime.timedelta(hours=48),
    )
    return [
        {
            "currency": e.currency,
            "event": e.event_name,
            "impact": e.impact,
            "event_date": e.event_date,
            "forecast": float(e.estimate) if e.estimate is not None else None,
            "previous": float(e.previous) if e.previous is not None else None,
        }
        for e in events
    ]


# ── Prices ────────────────────────────────────────────────────────────────────

# How stale (hours) before we trigger a background refresh per timeframe
_STALE_HOURS: dict[str, float] = {
    "M1": 0.1, "M5": 0.25, "M15": 0.5,
    "H1": 2.0, "H4": 8.0, "D1": 36.0, "W1": 200.0, "MN1": 800.0,
}


_TF_HOURS: dict[str, float] = {
    "M1": 1/60, "M5": 5/60, "M15": 15/60,
    "H1": 1.0, "H4": 4.0, "D1": 24.0, "W1": 168.0, "MN1": 720.0,
}


async def _collect_if_stale(
    instrument: Any,
    timeframe: str,
    latest_ts: Optional[datetime.datetime],
    earliest_ts: Optional[datetime.datetime] = None,
    row_count: int = 0,
) -> None:
    """Background task: collect candles when data is stale or has an internal gap.

    Gap detection: if the time span between first and last returned row implies
    significantly more candles than we actually have, a historical fill is triggered
    from earliest_ts to now — this closes gaps caused by downtime.
    """
    from src.collectors.price_collector import CcxtCollector, YFinanceCollector

    stale_hours = _STALE_HOURS.get(timeframe, 2.0)
    now = datetime.datetime.now(datetime.timezone.utc)
    gap_fill_from: Optional[datetime.datetime] = None

    if latest_ts is None:
        pass  # no data at all → fall through to collect
    else:
        if latest_ts.tzinfo is None:
            latest_ts = latest_ts.replace(tzinfo=datetime.timezone.utc)
        age_hours = (now - latest_ts).total_seconds() / 3600

        if age_hours < stale_hours:
            # Latest candle is fresh — but check for internal gap
            if earliest_ts is not None and row_count > 1:
                if earliest_ts.tzinfo is None:
                    earliest_ts = earliest_ts.replace(tzinfo=datetime.timezone.utc)
                span_hours = (latest_ts - earliest_ts).total_seconds() / 3600
                tf_h = _TF_HOURS.get(timeframe, 1.0)
                # Forex/stocks are closed weekends (~5/7 of time); use 0.6 as conservative factor
                expected_rows = (span_hours / tf_h) * 0.60
                if row_count < expected_rows:
                    # Gap detected — fill from first known candle
                    gap_fill_from = earliest_ts
                    logger.info(
                        "[Prices] Gap detected %s/%s: %d rows vs ~%.0f expected — filling from %s",
                        instrument.symbol, timeframe, row_count, expected_rows,
                        earliest_ts.strftime("%Y-%m-%d"),
                    )
                else:
                    return  # data is fresh and complete
            else:
                return  # fresh, no gap check possible

    try:
        if instrument.market == "crypto":
            collector: Any = CcxtCollector()
        else:
            collector = YFinanceCollector()

        if gap_fill_from is not None:
            await collector.collect_historical(instrument.symbol, timeframe, start=gap_fill_from)
            logger.info("[Prices] Gap-filled %s/%s from %s", instrument.symbol, timeframe, gap_fill_from)
        else:
            await collector.collect_latest(instrument.symbol, timeframe, n_candles=500)
            logger.info("[Prices] Auto-collected %s/%s (stale or empty)", instrument.symbol, timeframe)
    except Exception as exc:
        logger.warning(
            "[Prices] Auto-collect failed for %s/%s: %s", instrument.symbol, timeframe, exc
        )


@router_v2.get("/prices/{symbol:path}")
async def get_prices_v2(
    symbol: str,
    timeframe: str = "H1",
    date_from: Optional[datetime.datetime] = None,
    date_to: Optional[datetime.datetime] = None,
    limit: int = Query(200, ge=1, le=2000),
    db: AsyncSession = Depends(get_session),
):
    instrument = await get_instrument_by_symbol(db, symbol)
    if instrument is None:
        raise HTTPException(status_code=404, detail=f"Instrument {symbol!r} not found")

    rows = await get_price_data(
        db,
        instrument_id=instrument.id,
        timeframe=timeframe,
        from_dt=date_from,
        to_dt=date_to,
        limit=limit,
    )

    # Auto-collect in background if data is stale or has an internal gap
    latest_ts = rows[-1].timestamp if rows else None
    earliest_ts = rows[0].timestamp if rows else None
    asyncio.create_task(
        _collect_if_stale(instrument, timeframe, latest_ts, earliest_ts=earliest_ts, row_count=len(rows)),
        name=f"autocollect-{symbol}-{timeframe}",
    )

    return [
        {
            "timestamp": r.timestamp,
            "open": float(r.open),
            "high": float(r.high),
            "low": float(r.low),
            "close": float(r.close),
            "volume": float(r.volume) if r.volume is not None else 0.0,
        }
        for r in rows
    ]


@router_v2.get("/prices/{symbol:path}/orderflow")
async def get_order_flow(
    symbol: str,
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_session),
):
    instrument = await get_instrument_by_symbol(db, symbol)
    if instrument is None:
        raise HTTPException(status_code=404, detail=f"Instrument {symbol!r} not found")

    result = await db.execute(
        select(OrderFlowData)
        .where(OrderFlowData.instrument_id == instrument.id)
        .order_by(OrderFlowData.timestamp.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    return [
        {
            "timestamp": r.timestamp,
            "cvd": float(r.cvd) if r.cvd is not None else None,
            "funding_rate": float(r.funding_rate) if r.funding_rate is not None else None,
            "open_interest": float(r.open_interest) if r.open_interest is not None else None,
        }
        for r in rows
    ]


# ── Analyze ───────────────────────────────────────────────────────────────────

@router_v2.post("/analyze/{symbol:path}")
async def analyze_v2(
    symbol: str,
    timeframe: str = Query("H1"),
    db: AsyncSession = Depends(get_session),
):
    """Generate signal for symbol/timeframe. Returns signal or no_signal HOLD."""
    from src.collectors.price_collector import CcxtCollector, YFinanceCollector
    from src.signals.signal_engine import SignalEngine

    instrument = await get_instrument_by_symbol(db, symbol)
    if instrument is None:
        raise HTTPException(status_code=404, detail=f"Instrument {symbol!r} not found")

    # Auto-collect if not enough data
    existing = await get_price_data(db, instrument.id, timeframe, limit=35)
    if len(existing) < 30:
        try:
            if instrument.market == "crypto":
                await CcxtCollector().collect_latest(symbol, timeframe, n_candles=300)
            else:
                await YFinanceCollector().collect_latest(symbol, timeframe, n_candles=300)
        except Exception as exc:
            logger.warning("Auto-collect failed for %s: %s", symbol, exc)

    engine = SignalEngine()
    try:
        signal = await engine.generate_signal(instrument, timeframe, db)
    except Exception as exc:
        logger.error("Signal generation error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    if signal is None:
        hold_indicators: dict = {}
        hold_ta_score: float = 0.0
        hold_fa_score: float = 0.0
        hold_sentiment_score: float = 0.0
        hold_geo_score: float = 0.0
        news_records = None

        # TA indicators
        try:
            from src.analysis.ta_engine import TAEngine
            from src.signals.signal_engine import _price_data_to_df as _pdf
            price_records = await get_price_data(db, instrument.id, timeframe, limit=300)
            if len(price_records) >= 30:
                df = _pdf(price_records)
                if df is not None and not df.empty:
                    ta = TAEngine(df)
                    raw = ta.calculate_all_indicators()
                    hold_ta_score = ta.calculate_ta_score()
                    hold_indicators = {}
                    for k, v in raw.items():
                        if v is None:
                            hold_indicators[k] = None
                        else:
                            try:
                                hold_indicators[k] = round(float(v), 6)
                            except (TypeError, ValueError):
                                hold_indicators[k] = None
        except Exception:
            pass

        # FA score (same logic as SignalEngine)
        try:
            from src.analysis.fa_engine import FAEngine
            from src.analysis.crypto_fa_engine import CryptoFAEngine
            from src.database.crud import get_macro_data, get_news_events
            if instrument.market == "crypto":
                crypto_fa = CryptoFAEngine(db)
                crypto_result = await crypto_fa.analyze(instrument.id, instrument.symbol)
                hold_fa_score = crypto_result["score"]
            else:
                macro_records = await get_macro_data(db, limit=200)
                news_records = await get_news_events(db, limit=30)
                fa_engine = FAEngine(instrument, macro_records, news_records)
                hold_fa_score = fa_engine.calculate_fa_score()
        except Exception:
            pass

        # Sentiment score
        try:
            from src.analysis.sentiment_engine_v2 import SentimentEngineV2
            from src.database.crud import get_news_events as _get_news
            if news_records is None:
                news_records = await _get_news(db, limit=30)
            sent_engine = SentimentEngineV2(news_events=news_records)
            hold_sentiment_score = await sent_engine.calculate()
        except Exception:
            pass

        # Geo score
        try:
            from src.analysis.geo_engine_v2 import GeoEngineV2
            geo_engine = GeoEngineV2()
            hold_geo_score = await geo_engine.score(symbol)
            await geo_engine.close()
        except Exception:
            pass

        # Compute actual composite using same weights as signal_engine
        from src.signals.mtf_filter import MTFFilter
        _weights = MTFFilter().get_timeframe_weights(timeframe)
        hold_composite = (
            _weights["ta"] * hold_ta_score
            + _weights["fa"] * hold_fa_score
            + _weights["sentiment"] * hold_sentiment_score
            + _weights["geo"] * hold_geo_score
        )
        hold_composite = max(-100.0, min(100.0, hold_composite))

        return {
            "status": "no_signal",
            "direction": "HOLD",
            "signal_strength": "HOLD",
            "symbol": symbol,
            "timeframe": timeframe,
            "composite_score": round(hold_composite, 4),
            "ta_score": hold_ta_score,
            "fa_score": hold_fa_score,
            "sentiment_score": hold_sentiment_score,
            "geo_score": hold_geo_score,
            "confidence": 0.0,
            "indicators_snapshot": json.dumps(hold_indicators),
            "message": (
                f"No actionable signal for {symbol}/{timeframe}. "
                "Market in consolidation or cooldown active."
            ),
        }

    return {
        "status": "ok",
        "id": signal.id,
        "symbol": symbol,
        "timeframe": signal.timeframe,
        "direction": signal.direction,
        "signal_strength": signal.signal_strength,
        "composite_score": float(signal.composite_score),
        "ta_score": float(signal.ta_score),
        "fa_score": float(signal.fa_score),
        "sentiment_score": float(signal.sentiment_score),
        "geo_score": float(signal.geo_score),
        "confidence": float(signal.confidence),
        "entry_price": float(signal.entry_price) if signal.entry_price else None,
        "stop_loss": float(signal.stop_loss) if signal.stop_loss else None,
        "take_profit_1": float(signal.take_profit_1) if signal.take_profit_1 else None,
        "take_profit_2": float(signal.take_profit_2) if signal.take_profit_2 else None,
        "risk_reward": float(signal.risk_reward) if signal.risk_reward else None,
        "horizon": signal.horizon,
        "status": signal.status,
        "reasoning": signal.reasoning,
        "indicators_snapshot": signal.indicators_snapshot,
        "created_at": signal.created_at.isoformat(),
    }


# ── Trade Simulator ───────────────────────────────────────────────────────────

@router_v2.get("/simulator/stats")
async def simulator_stats(db: AsyncSession = Depends(get_session)):
    """Aggregate trade simulator statistics (based on $1,000 virtual account)."""
    from src.tracker.trade_simulator import ACCOUNT_SIZE_FLOAT, pnl_usd

    # Closed trade results — exclude "cancelled" (SIM-03: no entry was filled)
    _real_trades = SignalResult.exit_reason != "cancelled"

    total_q = await db.execute(
        select(func.count(SignalResult.id)).where(_real_trades)
    )
    total = total_q.scalar() or 0

    wins_q = await db.execute(
        select(func.count(SignalResult.id))
        .where(_real_trades, SignalResult.result == "win")
    )
    wins = wins_q.scalar() or 0

    losses_q = await db.execute(
        select(func.count(SignalResult.id))
        .where(_real_trades, SignalResult.result == "loss")
    )
    losses = losses_q.scalar() or 0

    breakevens_q = await db.execute(
        select(func.count(SignalResult.id))
        .where(_real_trades, SignalResult.result == "breakeven")
    )
    breakevens = breakevens_q.scalar() or 0

    # P&L: use stored pnl_usd when available; fall back to pnl_pct-based computation
    total_pnl_usd_q = await db.execute(
        select(func.sum(SignalResult.pnl_usd)).where(_real_trades)
    )
    total_pnl_usd_stored = float(total_pnl_usd_q.scalar() or 0)

    pnl_pct_q = await db.execute(
        select(func.sum(SignalResult.pnl_percent)).where(_real_trades)
    )
    total_pnl_pct = float(pnl_pct_q.scalar() or 0)

    # Count how many real trades have pnl_usd stored (for fallback detection)
    pnl_usd_stored_count_q = await db.execute(
        select(func.count(SignalResult.id))
        .where(_real_trades, SignalResult.pnl_usd.isnot(None))
    )
    pnl_usd_stored_count = pnl_usd_stored_count_q.scalar() or 0

    avg_win_pnl_q = await db.execute(
        select(func.avg(SignalResult.pnl_usd))
        .where(_real_trades, SignalResult.result == "win")
    )
    avg_win_usd = float(avg_win_pnl_q.scalar() or 0)

    avg_loss_pnl_q = await db.execute(
        select(func.avg(SignalResult.pnl_usd))
        .where(_real_trades, SignalResult.result == "loss")
    )
    avg_loss_usd = float(avg_loss_pnl_q.scalar() or 0)

    # Fallback for old records without stored pnl_usd.
    # Use pnl_usd_stored_count == 0 to detect missing data (not "== 0" sum, which is ambiguous).
    # Default size_pct=2.0 matches the simulator default (avoids 100x overestimate from None→100%).
    _DEFAULT_SIZE = 2.0
    if pnl_usd_stored_count == 0 and total_pnl_pct != 0:
        total_pnl_usd_stored = pnl_usd(total_pnl_pct, _DEFAULT_SIZE)
    if pnl_usd_stored_count == 0 and wins > 0:
        avg_win_pct_q = await db.execute(
            select(func.avg(SignalResult.pnl_percent))
            .where(_real_trades, SignalResult.result == "win")
        )
        avg_win_usd = pnl_usd(float(avg_win_pct_q.scalar() or 0), _DEFAULT_SIZE)
    if pnl_usd_stored_count == 0 and losses > 0:
        avg_loss_pct_q = await db.execute(
            select(func.avg(SignalResult.pnl_percent))
            .where(_real_trades, SignalResult.result == "loss")
        )
        avg_loss_usd = pnl_usd(float(avg_loss_pct_q.scalar() or 0), _DEFAULT_SIZE)

    # Open positions
    open_q = await db.execute(
        select(func.count(VirtualPortfolio.id)).where(VirtualPortfolio.status == "open")
    )
    open_positions = open_q.scalar() or 0

    unrealized_q = await db.execute(
        select(func.sum(VirtualPortfolio.unrealized_pnl_pct)).where(
            VirtualPortfolio.status == "open"
        )
    )
    unrealized_pct = float(unrealized_q.scalar() or 0)

    win_rate = round(wins / total * 100, 1) if total > 0 else 0.0

    # Profit factor = gross wins USD / abs(gross losses USD)
    gross_wins_q = await db.execute(
        select(func.sum(SignalResult.pnl_usd))
        .where(_real_trades, SignalResult.pnl_usd > 0)
    )
    gross_losses_q = await db.execute(
        select(func.sum(SignalResult.pnl_usd))
        .where(_real_trades, SignalResult.pnl_usd < 0)
    )
    gross_wins_val = float(gross_wins_q.scalar() or 0)
    gross_losses_val = abs(float(gross_losses_q.scalar() or 0))
    # Fallback to pnl_percent if pnl_usd not yet stored
    if gross_wins_val == 0 and gross_losses_val == 0:
        gw_q = await db.execute(
            select(func.sum(SignalResult.pnl_percent)).where(_real_trades, SignalResult.pnl_percent > 0)
        )
        gl_q = await db.execute(
            select(func.sum(SignalResult.pnl_percent)).where(_real_trades, SignalResult.pnl_percent < 0)
        )
        gross_wins_val = float(gw_q.scalar() or 0)
        gross_losses_val = abs(float(gl_q.scalar() or 0))
    profit_factor = round(gross_wins_val / gross_losses_val, 2) if gross_losses_val > 0 else None

    # SIM-15: additional stats fields
    cancelled_q = await db.execute(
        select(func.count(SignalResult.id)).where(SignalResult.exit_reason == "cancelled")
    )
    cancelled_count = cancelled_q.scalar() or 0

    avg_dur_q = await db.execute(
        select(func.avg(SignalResult.duration_minutes)).where(_real_trades)
    )
    avg_duration_minutes = float(avg_dur_q.scalar() or 0)

    avg_mfe_q = await db.execute(
        select(func.avg(SignalResult.max_favorable_excursion)).where(_real_trades)
    )
    avg_mfe = float(avg_mfe_q.scalar() or 0)

    avg_mae_q = await db.execute(
        select(func.avg(SignalResult.max_adverse_excursion)).where(_real_trades)
    )
    avg_mae = float(avg_mae_q.scalar() or 0)

    swap_q = await db.execute(
        select(func.sum(SignalResult.swap_usd)).where(_real_trades)
    )
    total_swap_usd = float(swap_q.scalar() or 0)

    # best_exit_reason: exit_reason with best avg_pnl_usd
    best_exit_q = await db.execute(
        select(SignalResult.exit_reason, func.avg(SignalResult.pnl_usd).label("avg_pnl"))
        .where(_real_trades, SignalResult.exit_reason.isnot(None))
        .group_by(SignalResult.exit_reason)
        .order_by(func.avg(SignalResult.pnl_usd).desc())
        .limit(1)
    )
    best_exit_row = best_exit_q.first()
    best_exit_reason = best_exit_row[0] if best_exit_row else None

    # SIM-24: partial close count
    partial_close_q = await db.execute(
        select(func.count(VirtualPortfolio.id)).where(
            VirtualPortfolio.partial_closed == True  # noqa: E712
        )
    )
    partial_close_count = partial_close_q.scalar() or 0

    # SIM-16: virtual account fields
    account = await get_virtual_account(db)
    account_initial = float(account.initial_balance) if account else ACCOUNT_SIZE_FLOAT
    account_current = float(account.current_balance) if account else ACCOUNT_SIZE_FLOAT
    account_peak = float(account.peak_balance) if account else ACCOUNT_SIZE_FLOAT
    account_drawdown_pct = round(
        (account_peak - account_current) / account_peak * 100, 2
    ) if account_peak > 0 else 0.0
    account_total_return_pct = round(
        (account_current - account_initial) / account_initial * 100, 2
    ) if account_initial > 0 else 0.0

    # SIM-12: unrealized_pnl_usd from stored field
    unrealized_usd_q = await db.execute(
        select(func.sum(VirtualPortfolio.unrealized_pnl_usd)).where(
            VirtualPortfolio.status.in_(["open", "partial"])
        )
    )
    unrealized_usd_stored = float(unrealized_usd_q.scalar() or 0)
    # Fallback to pnl_pct-based calculation for old positions
    if unrealized_usd_stored == 0 and unrealized_pct != 0:
        unrealized_usd_stored = pnl_usd(unrealized_pct)

    return {
        "account_size_usd":           ACCOUNT_SIZE_FLOAT,
        "total_trades":               total,
        "open_positions":             open_positions,
        "wins":                       wins,
        "losses":                     losses,
        "breakevens":                 breakevens,
        "win_rate_pct":               win_rate,
        "total_pnl_usd":              round(total_pnl_usd_stored, 2),
        "total_pnl_pct":              round(total_pnl_pct, 4),
        "unrealized_pnl_usd":         round(unrealized_usd_stored, 2),
        "avg_win_usd":                round(avg_win_usd, 2),
        "avg_loss_usd":               round(avg_loss_usd, 2),
        "profit_factor":              profit_factor,
        # SIM-15 new fields
        "cancelled_count":            cancelled_count,
        "avg_duration_minutes":       round(avg_duration_minutes, 1),
        "avg_mfe_pips":               round(avg_mfe, 4),
        "avg_mae_pips":               round(avg_mae, 4),
        "total_swap_usd":             round(total_swap_usd, 2),
        "best_exit_reason":           best_exit_reason,
        # SIM-16 account fields
        "account_initial_balance":    round(account_initial, 2),
        "account_current_balance":    round(account_current, 2),
        "account_peak_balance":       round(account_peak, 2),
        "account_drawdown_pct":       account_drawdown_pct,
        "account_total_return_pct":   account_total_return_pct,
        # SIM-24 partial close diagnostic
        "partial_close_count":        partial_close_count,
    }


@router_v2.get("/simulator/score-analysis")
async def simulator_score_analysis(db: AsyncSession = Depends(get_session)):
    """SIM-14: Score → Outcome analytics grouped by composite_score buckets."""
    from src.config import settings

    BUCKETS = [
        ("strong_sell", None,  -15.0),
        ("sell",        -15.0, -10.0),
        ("weak_sell",   -10.0,  -7.0),
        ("neutral",      -7.0,   7.0),
        ("weak_buy",      7.0,  10.0),
        ("buy",          10.0,  15.0),
        ("strong_buy",   15.0,  None),
    ]

    BUCKET_LABELS = {
        "strong_sell": "≤−15 (STRONG_SELL)",
        "sell":        "−15..−10 (SELL)",
        "weak_sell":   "−10..−7 (WEAK_SELL)",
        "neutral":     "−7..+7 (NEUTRAL)",
        "weak_buy":    "+7..+10 (WEAK_BUY)",
        "buy":         "+10..+15 (BUY)",
        "strong_buy":  "≥+15 (STRONG_BUY)",
    }

    # Fetch all closed trades with composite_score
    stmt = (
        select(SignalResult)
        .where(
            SignalResult.exit_reason != "cancelled",
            SignalResult.composite_score.isnot(None),
            SignalResult.exit_at.isnot(None),
        )
    )
    rows = list((await db.execute(stmt)).scalars().all())

    def assign_bucket(score: float) -> str:
        if score <= -15:
            return "strong_sell"
        elif score <= -10:
            return "sell"
        elif score <= -7:
            return "weak_sell"
        elif score < 7:
            return "neutral"
        elif score <= 10:
            return "weak_buy"
        elif score <= 15:
            return "buy"
        else:
            return "strong_buy"

    # Group by bucket
    groups: dict[str, list[SignalResult]] = defaultdict(list)
    for r in rows:
        bucket_key = assign_bucket(float(r.composite_score))
        groups[bucket_key].append(r)

    score_buckets = []
    for bucket_name, range_min, range_max in BUCKETS:
        bucket_rows = groups[bucket_name]
        total = len(bucket_rows)
        wins = sum(1 for r in bucket_rows if r.result == "win")
        losses = sum(1 for r in bucket_rows if r.result == "loss")
        breakevens = total - wins - losses

        gross_wins = sum(float(r.pnl_usd or 0) for r in bucket_rows if (r.pnl_usd or 0) > 0)
        gross_losses = abs(sum(float(r.pnl_usd or 0) for r in bucket_rows if (r.pnl_usd or 0) < 0))
        profit_factor_b = round(gross_wins / gross_losses, 2) if gross_losses > 0 else None

        avg_pnl_usd = round(sum(float(r.pnl_usd or 0) for r in bucket_rows) / total, 4) if total > 0 else 0.0
        avg_pnl_pips = round(sum(float(r.pnl_pips or 0) for r in bucket_rows) / total, 2) if total > 0 else 0.0
        avg_dur = round(sum(r.duration_minutes or 0 for r in bucket_rows) / total, 1) if total > 0 else 0.0
        avg_mfe = round(sum(float(r.max_favorable_excursion or 0) for r in bucket_rows) / total, 4) if total > 0 else 0.0
        avg_mae = round(sum(float(r.max_adverse_excursion or 0) for r in bucket_rows) / total, 4) if total > 0 else 0.0

        score_buckets.append({
            "bucket_name":          bucket_name,
            "range_label":          BUCKET_LABELS[bucket_name],
            "range_min":            range_min,
            "range_max":            range_max,
            "total":                total,
            "wins":                 wins,
            "losses":               losses,
            "breakevens":           breakevens,
            "win_rate_pct":         round(wins / total * 100, 1) if total > 0 else 0.0,
            "profit_factor":        profit_factor_b,
            "avg_pnl_usd":          avg_pnl_usd,
            "avg_pnl_pips":         avg_pnl_pips,
            "avg_duration_minutes": avg_dur,
            "avg_mfe_pips":         avg_mfe,
            "avg_mae_pips":         avg_mae,
            "insufficient_data":    total < 3,
        })

    # Threshold recommendations
    eligible = [b for b in score_buckets if not b["insufficient_data"] and b["total"] >= 5]
    pf_positive = [b for b in eligible if b["profit_factor"] and b["profit_factor"] > 1.0]
    suggested_min = (
        min(b["range_min"] for b in pf_positive if b["range_min"] is not None)
        if pf_positive else None
    )
    best_win_rate_bucket = max(eligible, key=lambda b: b["win_rate_pct"]) if eligible else None

    return {
        "score_buckets": score_buckets,
        "threshold_recommendations": {
            "current_buy_threshold": float(settings.BUY_THRESHOLD) if hasattr(settings, "BUY_THRESHOLD") else 7.0,
            "suggested_min_score_for_positive_edge": suggested_min,
            "score_with_best_win_rate": {
                "score_min": best_win_rate_bucket["range_min"],
                "win_rate": best_win_rate_bucket["win_rate_pct"],
            } if best_win_rate_bucket else None,
        },
    }


@router_v2.get("/simulator/breakdown")
async def simulator_breakdown(
    by: str = Query(..., description="timeframe|direction|exit_reason|market|month"),
    db: AsyncSession = Depends(get_session),
):
    """SIM-15: Breakdown analytics by dimension."""
    ALLOWED_DIMS = {"timeframe", "direction", "exit_reason", "market", "month"}
    if by not in ALLOWED_DIMS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid dimension '{by}'. Allowed: {', '.join(sorted(ALLOWED_DIMS))}",
        )

    # Fetch all closed trades (join Signal + Instrument for market/timeframe/direction)
    stmt = (
        select(SignalResult, Signal, Instrument)
        .join(Signal, SignalResult.signal_id == Signal.id)
        .join(Instrument, Signal.instrument_id == Instrument.id)
        .where(SignalResult.exit_reason != "cancelled")
        .where(SignalResult.exit_at.isnot(None))
    )
    trade_rows = list((await db.execute(stmt)).all())

    def get_key(sr: SignalResult, sig: Signal, instr: Instrument) -> Optional[str]:
        if by == "timeframe":
            return sig.timeframe
        elif by == "direction":
            return sig.direction
        elif by == "exit_reason":
            return sr.exit_reason or "unknown"
        elif by == "market":
            return instr.market
        elif by == "month":
            return sr.exit_at.strftime("%Y-%m") if sr.exit_at else None
        return None

    groups: dict[str, list[tuple]] = defaultdict(list)
    for sr, sig, instr in trade_rows:
        key = get_key(sr, sig, instr)
        if key is not None:
            groups[key].append((sr, sig, instr))

    rows_out = []
    for key, trades in sorted(groups.items()):
        total = len(trades)
        wins = sum(1 for sr, _, _ in trades if sr.result == "win")
        losses = sum(1 for sr, _, _ in trades if sr.result == "loss")
        breakevens = total - wins - losses

        gross_wins = sum(float(sr.pnl_usd or 0) for sr, _, _ in trades if (sr.pnl_usd or 0) > 0)
        gross_losses = abs(sum(float(sr.pnl_usd or 0) for sr, _, _ in trades if (sr.pnl_usd or 0) < 0))
        pf = round(gross_wins / gross_losses, 2) if gross_losses > 0 else None

        avg_pnl = round(sum(float(sr.pnl_usd or 0) for sr, _, _ in trades) / total, 4) if total > 0 else 0.0
        avg_dur = round(sum(sr.duration_minutes or 0 for sr, _, _ in trades) / total, 1) if total > 0 else 0.0
        scores = [float(sr.composite_score) for sr, _, _ in trades if sr.composite_score is not None]
        avg_score = round(sum(scores) / len(scores), 2) if scores else None

        rows_out.append({
            "key":                  key,
            "total":                total,
            "wins":                 wins,
            "losses":               losses,
            "breakevens":           breakevens,
            "win_rate_pct":         round(wins / total * 100, 1) if total > 0 else 0.0,
            "profit_factor":        pf,
            "avg_pnl_usd":          avg_pnl,
            "avg_duration_minutes": avg_dur,
            "avg_composite_score":  avg_score,
        })

    return {"dimension": by, "rows": rows_out}


@router_v2.get("/simulator/trades")
async def simulator_trades(
    limit: int = Query(100, le=500),
    result_filter: Optional[str] = Query(None, alias="result"),
    db: AsyncSession = Depends(get_session),
):
    """Return closed trade history with P&L in USD."""
    from src.tracker.trade_simulator import pnl_usd

    stmt = (
        select(Signal, SignalResult, Instrument)
        .join(SignalResult, Signal.id == SignalResult.signal_id)
        .join(Instrument, Signal.instrument_id == Instrument.id)
        # Exclude cancelled signals (SIM-03: no entry filled, not real trades)
        .where(SignalResult.exit_reason != "cancelled")
        .order_by(SignalResult.exit_at.desc())
        .limit(limit)
    )
    if result_filter:
        stmt = stmt.where(SignalResult.result == result_filter)

    rows = (await db.execute(stmt)).all()

    trades = []
    for signal, sr, instr in rows:
        # Use stored pnl_usd if available, else compute from pnl_percent (old records)
        pnl_usd_val = (
            float(sr.pnl_usd)
            if sr.pnl_usd is not None
            else pnl_usd(float(sr.pnl_percent or 0), float(signal.position_size_pct or 0) or None)
        )
        trades.append({
            "signal_id":        signal.id,
            "symbol":           instr.symbol,
            "name":             instr.name,
            "timeframe":        signal.timeframe,
            "direction":        signal.direction,
            "entry_price":      float(sr.entry_actual_price or signal.entry_price or 0),
            "exit_price":       float(sr.exit_price or 0),
            "stop_loss":        float(signal.stop_loss or 0),
            "take_profit_1":    float(signal.take_profit_1 or 0),
            "exit_reason":      sr.exit_reason,
            "pnl_pips":         float(sr.pnl_pips or 0),
            "pnl_pct":          float(sr.pnl_percent or 0),
            "pnl_usd":          round(pnl_usd_val, 2),
            "result":           sr.result,
            "duration_minutes": sr.duration_minutes,
            "entry_at":         sr.entry_filled_at.isoformat() if sr.entry_filled_at else None,
            "exit_at":          sr.exit_at.isoformat() if sr.exit_at else None,
            "composite_score":  float(signal.composite_score or 0),
        })

    return trades


@router_v2.get("/simulator/open")
async def simulator_open_positions(db: AsyncSession = Depends(get_session)):
    """Return currently open virtual positions with unrealized P&L."""
    from src.tracker.trade_simulator import pnl_usd

    stmt = (
        select(VirtualPortfolio, Signal, Instrument)
        .join(Signal, VirtualPortfolio.signal_id == Signal.id)
        .join(Instrument, Signal.instrument_id == Instrument.id)
        .where(VirtualPortfolio.status == "open")
        .order_by(VirtualPortfolio.opened_at.desc())
    )
    rows = (await db.execute(stmt)).all()

    positions = []
    for pos, signal, instr in rows:
        # SIM-12: use stored unrealized_pnl_usd; fallback to pnl_pct computation
        unrealized_usd_val = (
            float(pos.unrealized_pnl_usd)
            if pos.unrealized_pnl_usd is not None
            else pnl_usd(float(pos.unrealized_pnl_pct or 0))
        )
        positions.append({
            "signal_id": signal.id,
            "symbol": instr.symbol,
            "name": instr.name,
            "timeframe": signal.timeframe,
            "direction": signal.direction,
            "entry_price": float(pos.entry_price),
            "current_price": float(pos.current_price or pos.entry_price),
            "stop_loss": float(signal.stop_loss or 0),
            "take_profit_1": float(signal.take_profit_1 or 0),
            "unrealized_pnl_pct": float(pos.unrealized_pnl_pct or 0),
            "unrealized_pnl_usd": round(unrealized_usd_val, 2),
            "size_pct": float(pos.size_pct),
            "size_remaining_pct": float(pos.size_remaining_pct),
            "account_balance_at_entry": float(pos.account_balance_at_entry) if pos.account_balance_at_entry else None,
            "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
        })

    return positions


# ── Simulator Reset ───────────────────────────────────────────────────────────

@router_v2.post("/simulator/reset")
async def simulator_reset(db: AsyncSession = Depends(get_session)):
    """Full simulator reset: delete all positions and signals, restore balance."""
    from src.config import settings

    # 1. Delete ALL virtual positions (all statuses — clean slate)
    pos_result = await db.execute(
        delete(VirtualPortfolio).returning(VirtualPortfolio.id)
    )
    deleted_positions = len(pos_result.fetchall())

    # 2. Delete ALL signals
    sig_result = await db.execute(
        delete(Signal).returning(Signal.id)
    )
    deleted_signals = len(sig_result.fetchall())

    # 3. Reset virtual account to initial balance
    initial = settings.VIRTUAL_ACCOUNT_SIZE_USD
    account = (await db.execute(select(VirtualAccount).limit(1))).scalar_one_or_none()
    if account:
        account.current_balance = initial  # type: ignore[assignment]
        account.peak_balance = initial     # type: ignore[assignment]
        account.total_realized_pnl = 0    # type: ignore[assignment]
        account.total_trades = 0
    else:
        db.add(VirtualAccount(
            initial_balance=initial,
            current_balance=initial,
            peak_balance=initial,
            total_realized_pnl=0,
            total_trades=0,
        ))

    await db.commit()
    logger.info(
        f"[SimReset] Deleted {deleted_positions} positions, "
        f"{deleted_signals} signals; balance restored to ${initial}"
    )
    return {
        "ok": True,
        "deleted_positions": deleted_positions,
        "deleted_signals": deleted_signals,
        "balance_restored_to": initial,
    }


# ── SIM-17: Diagnostic endpoint ───────────────────────────────────────────────

@router_v2.get("/diagnostics/scoring-breakdown")
async def diagnostics_scoring_breakdown(
    timeframe: str = Query("H1"),
    db: AsyncSession = Depends(get_session),
):
    """Return per-component score breakdown for all active instruments.

    Used to detect systematic bias in scoring (SIM-17).
    Response includes bias_flags and a summary with suspected bias sources.
    """
    from src.analysis.fa_engine import FAEngine
    from src.analysis.crypto_fa_engine import CryptoFAEngine
    from src.analysis.sentiment_engine_v2 import SentimentEngineV2
    from src.analysis.geo_engine_v2 import GeoEngineV2
    from src.analysis.ta_engine import TAEngine
    from src.database.crud import get_macro_data, get_news_events
    from src.signals.mtf_filter import MTFFilter
    from src.signals.signal_engine import _price_data_to_df

    instruments = await get_all_instruments(db)
    macro_records = await get_macro_data(db, limit=200)
    news_records = await get_news_events(db, limit=30)
    mtf = MTFFilter()
    weights = mtf.get_timeframe_weights(timeframe)

    result_instruments = []

    for instr in instruments:
        if not instr.is_active:
            continue

        symbol = instr.symbol
        bias_flags: list[str] = []

        # TA
        ta_score: Optional[float] = None
        try:
            price_records = await get_price_data(db, instr.id, timeframe, limit=300)
            if len(price_records) >= 30:
                df = _price_data_to_df(price_records)
                if df is not None and not df.empty:
                    ta_eng = TAEngine(df)
                    ta_score = ta_eng.calculate_ta_score()
        except Exception as exc:
            logger.warning("[SIM-17] TA fallback for %s: %s", symbol, exc)
            bias_flags.append(f"ta_score: error — {exc}")

        # FA
        fa_score: Optional[float] = None
        try:
            if instr.market == "crypto":
                crypto_fa = CryptoFAEngine(db)
                res = await crypto_fa.analyze(instr.id, symbol)
                fa_score = float(res["score"])
            else:
                fa_eng = FAEngine(instr, macro_records, news_records)
                fa_score = fa_eng.calculate_fa_score()
        except Exception as exc:
            logger.warning("[SIM-17] FA fallback 0.0 for %s: %s", symbol, exc)
            fa_score = 0.0
            bias_flags.append(f"fa_score returned default (no data): {exc}")

        # Sentiment
        sentiment_score: Optional[float] = None
        try:
            sent_eng = SentimentEngineV2(news_events=news_records)
            sentiment_score = await sent_eng.calculate()
            if not news_records:
                bias_flags.append("sentiment_score returned default (no news data)")
        except Exception as exc:
            logger.warning("[SIM-17] Sentiment fallback 0.0 for %s: %s", symbol, exc)
            sentiment_score = 0.0
            bias_flags.append(f"sentiment_score returned default (error): {exc}")

        # Geo
        geo_score: Optional[float] = None
        try:
            geo_eng = GeoEngineV2()
            geo_score = await geo_eng.score(symbol)
            await geo_eng.close()
        except Exception as exc:
            logger.warning("[SIM-17] Geo fallback 0.0 for %s: %s", symbol, exc)
            geo_score = 0.0
            bias_flags.append(f"geo_score returned default (no data): {exc}")

        # Composite
        composite: Optional[float] = None
        if all(v is not None for v in [ta_score, fa_score, sentiment_score, geo_score]):
            composite = (
                weights["ta"] * (ta_score or 0.0)
                + weights["fa"] * (fa_score or 0.0)
                + weights["sentiment"] * (sentiment_score or 0.0)
                + weights["geo"] * (geo_score or 0.0)
            )
            composite = max(-100.0, min(100.0, composite))

        result_instruments.append({
            "symbol": symbol,
            "timeframe": timeframe,
            "composite_score": round(composite, 4) if composite is not None else None,
            "components": {
                "ta_score":        round(ta_score, 4) if ta_score is not None else None,
                "ta_weight":       weights["ta"],
                "fa_score":        round(fa_score, 4) if fa_score is not None else None,
                "fa_weight":       weights["fa"],
                "sentiment_score": round(sentiment_score, 4) if sentiment_score is not None else None,
                "sentiment_weight": weights["sentiment"],
                "geo_score":       round(geo_score, 4) if geo_score is not None else None,
                "geo_weight":      weights["geo"],
                "of_score":        None,  # Order flow not yet implemented
                "of_weight":       0.0,
            },
            "bias_flags": bias_flags,
        })

    composites = [r["composite_score"] for r in result_instruments if r["composite_score"] is not None]
    avg_composite = sum(composites) / len(composites) if composites else None
    pct_negative = (
        sum(1 for c in composites if c < 0) / len(composites) * 100
        if composites else None
    )

    # Detect suspected bias sources: component avg < -3.0
    suspected_bias: list[str] = []
    for component in ("fa_score", "sentiment_score", "geo_score"):
        vals = [
            r["components"][component]
            for r in result_instruments
            if r["components"][component] is not None
        ]
        if vals and sum(vals) / len(vals) < -3.0:
            suspected_bias.append(component)

    return {
        "instruments": result_instruments,
        "summary": {
            "avg_composite": round(avg_composite, 4) if avg_composite is not None else None,
            "pct_negative":  round(pct_negative, 1) if pct_negative is not None else None,
            "suspected_bias_sources": suspected_bias,
            "instrument_count": len(result_instruments),
        },
    }


@router_v2.post("/system/logs/clear")
async def clear_system_logs(db: AsyncSession = Depends(get_session)):
    """Delete all system event logs."""
    from src.database.crud import delete_all_system_events
    deleted = await delete_all_system_events(db)
    await db.commit()
    logger.info(f"[Logs] Cleared {deleted} system events via API")
    return {"ok": True, "deleted": deleted}


# ── News Feed ─────────────────────────────────────────────────────────────────

@router_v2.get("/news")
async def list_news(
    category: Optional[str] = Query(None, description="crypto|forex|stock|general"),
    importance: Optional[str] = Query(None, description="low|medium|high|critical"),
    search: Optional[str] = Query(None, description="keyword search in headline"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_session),
):
    """Return news feed with optional filters."""
    from src.database.models import NewsEvent as _NewsEvent

    stmt = (
        select(_NewsEvent)
        .order_by(_NewsEvent.published_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if category:
        stmt = stmt.where(_NewsEvent.category == category)
    if importance == "medium+":
        stmt = stmt.where(_NewsEvent.importance.in_(["medium", "high", "critical"]))
    elif importance == "high+":
        stmt = stmt.where(_NewsEvent.importance.in_(["high", "critical"]))
    elif importance:
        stmt = stmt.where(_NewsEvent.importance == importance)
    if search:
        stmt = stmt.where(_NewsEvent.headline.ilike(f"%{search}%"))

    rows = (await db.execute(stmt)).scalars().all()

    return [
        {
            "id":               r.id,
            "headline":         r.headline,
            "summary":          r.summary,
            "source":           r.source,
            "url":              r.url,
            "published_at":     r.published_at.isoformat() if r.published_at else None,
            "sentiment_score":  float(r.sentiment_score) if r.sentiment_score is not None else None,
            "importance":       r.importance,
            "category":         r.category,
        }
        for r in rows
    ]


# ── System Events ─────────────────────────────────────────────────────────────

@router_v2.get("/system/events")
async def get_system_events_endpoint(
    level: Optional[str] = Query(None, description="INFO / WARNING / ERROR"),
    event_type: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    limit: int = Query(200, le=500),
    db: AsyncSession = Depends(get_session),
):
    """Return recent system events (max 3 days retention)."""
    from src.database.crud import get_system_events

    events = await get_system_events(db, level=level, event_type=event_type, symbol=symbol, limit=limit)
    return [
        {
            "id": e.id,
            "level": e.level,
            "event_type": e.event_type,
            "source": e.source,
            "symbol": e.symbol,
            "timeframe": e.timeframe,
            "message": e.message,
            "details": json.loads(e.details) if e.details else None,
            "created_at": e.created_at.isoformat(),
        }
        for e in events
    ]


# ── System / Health ───────────────────────────────────────────────────────────

@router_v2.get("/health")
async def health_v2(db: AsyncSession = Depends(get_session)):
    try:
        await db.execute(select(func.now()))
        db_status = "ok"
    except Exception as exc:
        db_status = f"error: {exc}"
    return {"status": "ok", "version": "2.0", "database": db_status}


@router_v2.get("/health/postgres")
async def health_postgres(db: AsyncSession = Depends(get_session)):
    """Check PostgreSQL connectivity and return server version."""
    try:
        result = await db.execute(select(func.version()))
        version_str: str = result.scalar_one()
        # e.g. "PostgreSQL 15.3 on ..."
        short = version_str.split(" on ")[0] if " on " in version_str else version_str[:30]
        return {"status": "ok", "detail": short}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


@router_v2.get("/health/redis")
async def health_redis():
    """Check Redis connectivity via PING."""
    try:
        import redis.asyncio as aioredis
        from src.config import settings
        client = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=3)
        await client.ping()
        info = await client.info("server")
        version = info.get("redis_version", "")
        await client.aclose()
        return {"status": "ok", "detail": f"v{version}" if version else "ok"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


@router_v2.get("/health/scheduler")
async def health_scheduler():
    """Check APScheduler status and return running job count."""
    try:
        from src.scheduler.jobs import scheduler
        if not scheduler.running:
            return {"status": "error", "detail": "not running"}
        jobs = scheduler.get_jobs()
        return {"status": "ok", "detail": f"{len(jobs)} jobs"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


@router_v2.get("/health/finbert")
async def health_finbert():
    """Proxy FinBERT health check (avoids browser CORS issues)."""
    import httpx
    from src.config import settings
    url = f"{settings.FINBERT_SERVICE_URL}/health"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url)
            if resp.is_success:
                return {"status": "ok", **resp.json()}
            return {"status": "error", "detail": f"HTTP {resp.status_code}"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


@router_v2.get("/health/collectors")
async def health_collectors():
    """Check health of each collector (lightweight ping)."""
    from src.collectors.order_flow_collector import OrderFlowCollector
    from src.collectors.social_collector import SocialCollector
    from src.collectors.onchain_collector import OnchainCollector

    results: dict[str, bool] = {}
    for name, cls in [
        ("order_flow", OrderFlowCollector),
        ("social", SocialCollector),
        ("onchain", OnchainCollector),
    ]:
        try:
            collector = cls()
            results[name] = await collector.health_check()
        except Exception as exc:
            logger.warning("Health check failed for %s: %s", name, exc)
            results[name] = False

    overall = all(results.values())
    return {"healthy": overall, "collectors": results}


# ── SIM-23: Diagnostic endpoints ──────────────────────────────────────────────


@router_v2.get("/diagnostics/score-components")
async def diagnostics_score_components(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_session),
):
    """SIM-23: Average score components from signals generated in the last N days.

    Returns per-component avg, min, max, zero_pct plus bias_warning flag.
    """
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)

    stmt = select(Signal).where(Signal.created_at >= cutoff)
    result = await db.execute(stmt)
    signals = result.scalars().all()

    components = ["ta_score", "fa_score", "sentiment_score", "geo_score", "of_score"]
    stats: dict[str, Any] = {}
    for comp in components:
        values = [float(getattr(s, comp)) for s in signals if getattr(s, comp, None) is not None]
        if values:
            avg_v = sum(values) / len(values)
            stats[comp] = {
                "avg": round(avg_v, 4),
                "min": round(min(values), 4),
                "max": round(max(values), 4),
                "zero_pct": round(sum(1 for v in values if v == 0.0) / len(values) * 100, 2),
                "count": len(values),
                "bias_warning": avg_v < -3.0,
            }
        else:
            stats[comp] = {
                "avg": None, "min": None, "max": None,
                "zero_pct": None, "count": 0, "bias_warning": False,
            }

    # LONG/SHORT signal ratio
    directions = [s.direction for s in signals if s.direction in ("LONG", "SHORT")]
    long_count = sum(1 for d in directions if d == "LONG")
    short_count = sum(1 for d in directions if d == "SHORT")
    total_dir = long_count + short_count
    pct_short = (short_count / total_dir * 100) if total_dir > 0 else 0
    pct_long = (long_count / total_dir * 100) if total_dir > 0 else 0

    return {
        "period_days": days,
        "total_signals": len(signals),
        "components": stats,
        "signal_direction": {
            "long_count": long_count,
            "short_count": short_count,
            "pct_long": round(pct_long, 1),
            "pct_short": round(pct_short, 1),
            "bias_warning": pct_short > 80 or pct_long > 80,
        },
    }


@router_v2.get("/diagnostics/mfe-mae-distribution")
async def diagnostics_mfe_mae_distribution(db: AsyncSession = Depends(get_session)):
    """SIM-23: MFE and MAE percentile distributions across all closed trades."""
    import statistics

    stmt = select(SignalResult).where(
        SignalResult.exit_reason.notin_(["cancelled"]),
        SignalResult.max_favorable_excursion.isnot(None),
        SignalResult.max_adverse_excursion.isnot(None),
    )
    result = await db.execute(stmt)
    closed = result.scalars().all()

    def _percentiles(values: list[float], pcts: list[int]) -> dict[str, float]:
        if not values:
            return {f"p{p}": None for p in pcts}
        s = sorted(values)
        n = len(s)
        out = {}
        for p in pcts:
            idx = int(p / 100 * (n - 1))
            out[f"p{p}"] = round(s[idx], 6)
        return out

    mfe_vals = [float(t.max_favorable_excursion) for t in closed if t.max_favorable_excursion is not None]
    mae_vals = [float(t.max_adverse_excursion) for t in closed if t.max_adverse_excursion is not None]

    # early_exit_viability: estimate of trades where MAE > 60% of SL distance
    # (proxy: using pnl_pips as denominator if SL distance not stored)
    early_exit_count = 0
    for t in closed:
        if t.max_adverse_excursion is None:
            continue
        # Approximate SL distance from pnl_pips on losing trades
        # If pnl_pips < 0 (loss), SL distance ≈ abs(pnl_pips) pips
        if t.pnl_pips and t.pnl_pips < 0:
            sl_dist_est = abs(float(t.pnl_pips))
            if sl_dist_est > 0:
                mae_ratio = abs(float(t.max_adverse_excursion)) / sl_dist_est
                if mae_ratio >= 0.6:
                    early_exit_count += 1
    early_exit_viability = (
        round(early_exit_count / len(closed) * 100, 2) if closed else 0.0
    )

    return {
        "total_closed": len(closed),
        "mfe_distribution": _percentiles(mfe_vals, [10, 25, 50, 75, 90]),
        "mae_distribution": _percentiles(mae_vals, [10, 25, 50, 75, 90]),
        "early_exit_viability_pct": early_exit_viability,
    }


@router_v2.get("/diagnostics/signal-timing")
async def diagnostics_signal_timing(db: AsyncSession = Depends(get_session)):
    """SIM-23: Signal distribution by hour UTC + win rate per hour."""
    stmt = select(Signal).where(Signal.direction.in_(["LONG", "SHORT"]))
    result = await db.execute(stmt)
    signals = result.scalars().all()

    # For win rate by hour, join with SignalResult
    stmt2 = select(SignalResult).where(
        SignalResult.exit_reason.notin_(["cancelled"]),
        SignalResult.result.in_(["win", "loss", "breakeven"]),
    )
    result2 = await db.execute(stmt2)
    results_list = result2.scalars().all()
    result_map = {r.signal_id: r for r in results_list}

    by_hour: dict[int, dict] = {h: {"count": 0, "wins": 0, "total_with_result": 0} for h in range(24)}
    for sig in signals:
        if sig.created_at:
            h = sig.created_at.hour
            by_hour[h]["count"] += 1
            if sig.id in result_map:
                r = result_map[sig.id]
                by_hour[h]["total_with_result"] += 1
                if r.result == "win":
                    by_hour[h]["wins"] += 1

    timing = []
    for h in range(24):
        rec = by_hour[h]
        total_r = rec["total_with_result"]
        win_rate = round(rec["wins"] / total_r * 100, 1) if total_r > 0 else None
        timing.append({
            "hour_utc": h,
            "signal_count": rec["count"],
            "win_rate_pct": win_rate,
        })

    return {"by_hour": timing}


@router_v2.get("/diagnostics/partial-close-analysis")
async def diagnostics_partial_close_analysis(db: AsyncSession = Depends(get_session)):
    """SIM-23: Analysis of partial close execution rates."""
    # TP1 hits = exit_reason='tp1_hit' in signal_results
    tp1_q = await db.execute(
        select(func.count(SignalResult.id)).where(SignalResult.exit_reason == "tp1_hit")
    )
    tp1_hit_count = tp1_q.scalar() or 0

    # Partial closes
    partial_q = await db.execute(
        select(func.count(VirtualPortfolio.id)).where(
            VirtualPortfolio.partial_closed == True  # noqa: E712
        )
    )
    partial_close_count = partial_q.scalar() or 0

    # TP2 hits (continued after partial close)
    tp2_q = await db.execute(
        select(func.count(SignalResult.id)).where(SignalResult.exit_reason == "tp2_hit")
    )
    tp2_hit_count = tp2_q.scalar() or 0

    # SL/breakeven hits on second half (returned to SL after partial close)
    sl_after_partial_q = await db.execute(
        select(func.count(SignalResult.id)).where(
            SignalResult.exit_reason.in_(["sl_hit", "trailing_sl_hit"]),
            SignalResult.partial_close_pnl_usd.isnot(None),
        )
    )
    sl_after_partial = sl_after_partial_q.scalar() or 0

    # MAE early exits
    mae_exit_q = await db.execute(
        select(func.count(SignalResult.id)).where(SignalResult.exit_reason == "mae_early_exit")
    )
    mae_early_exits = mae_exit_q.scalar() or 0

    total_closed_q = await db.execute(
        select(func.count(SignalResult.id)).where(
            SignalResult.exit_reason.notin_(["cancelled"])
        )
    )
    total_closed = total_closed_q.scalar() or 0

    return {
        "total_closed_trades": total_closed,
        "tp1_hit_count": tp1_hit_count,
        "partial_close_count": partial_close_count,
        "pct_tp1_hit": round(tp1_hit_count / total_closed * 100, 2) if total_closed else 0,
        "pct_partial_close": round(partial_close_count / total_closed * 100, 2) if total_closed else 0,
        "of_tp1_continued_to_tp2": tp2_hit_count,
        "of_tp1_returned_to_sl": sl_after_partial,
        "mae_early_exits": mae_early_exits,
        "pct_mae_early_exits": round(mae_early_exits / total_closed * 100, 2) if total_closed else 0,
    }
