"""REST API v2 — all endpoints per ARCHITECTURE.md section 4.1."""

import datetime
import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.crud import (
    get_all_instruments,
    get_instrument_by_symbol,
    get_open_positions,
    get_price_data,
    get_signal_by_id,
    get_signals,
    get_upcoming_economic_events,
    get_virtual_account,
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

    class Config:
        from_attributes = True


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

    class Config:
        from_attributes = True


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

    class Config:
        from_attributes = True


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

    class Config:
        from_attributes = True


class RegimeResponse(BaseModel):
    regime: str
    adx: Optional[float]
    atr_percentile: Optional[float]
    detected_at: datetime.datetime

    class Config:
        from_attributes = True


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

    class Config:
        from_attributes = True


class BacktestRunResponse(BaseModel):
    id: int
    started_at: datetime.datetime
    completed_at: Optional[datetime.datetime]
    oos_sharpe: Optional[float]
    oos_profit_factor: Optional[float]
    oos_win_rate: Optional[float]
    oos_max_drawdown: Optional[float]
    oos_total_trades: Optional[int]
    monte_carlo_ci_drawdown: Optional[float]
    passed_validation: Optional[bool]
    optimal_weights: Optional[str]

    class Config:
        from_attributes = True


class BacktestTradeResponse(BaseModel):
    id: int
    direction: Optional[str]
    entry_price: Optional[float]
    exit_price: Optional[float]
    sl: Optional[float]
    tp: Optional[float]
    pnl_pct: Optional[float]
    entry_time: Optional[datetime.datetime]
    exit_time: Optional[datetime.datetime]
    exit_reason: Optional[str]
    regime: Optional[str]

    class Config:
        from_attributes = True


class OrderFlowResponse(BaseModel):
    timestamp: datetime.datetime
    cvd: Optional[float]
    funding_rate: Optional[float]
    open_interest: Optional[float]
    open_interest_prev: Optional[float]

    class Config:
        from_attributes = True


class MacroRateResponse(BaseModel):
    country: str
    indicator: str
    value: Optional[float]
    collected_at: Optional[datetime.datetime]

    class Config:
        from_attributes = True


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
        result.append(s)
    return result


@router_v2.get("/signals/{signal_id}", response_model=SignalV2)
async def get_signal_v2(
    signal_id: int,
    db: AsyncSession = Depends(get_session),
):
    signal = await get_signal_by_id(db, signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    return signal


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
    from sqlalchemy import update as sa_update
    await db.execute(
        sa_update(Signal)
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

@router_v2.get("/backtests", response_model=list[BacktestRunResponse])
async def list_backtests(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(
        select(BacktestRun)
        .order_by(BacktestRun.started_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


@router_v2.get("/backtests/{run_id}", response_model=BacktestRunResponse)
async def get_backtest(run_id: int, db: AsyncSession = Depends(get_session)):
    result = await db.execute(select(BacktestRun).where(BacktestRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    return run


@router_v2.get("/backtests/{run_id}/trades", response_model=list[BacktestTradeResponse])
async def get_backtest_trades(
    run_id: int,
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(
        select(BacktestTrade).where(BacktestTrade.run_id == run_id)
    )
    return list(result.scalars().all())


@router_v2.post("/backtest/run")
async def trigger_backtest():
    """Trigger async backtest via Celery."""
    try:
        from src.scheduler.tasks import run_weekly_backtest
        task = run_weekly_backtest.delay()
        return {"status": "queued", "task_id": task.id}
    except Exception as exc:
        logger.error("Failed to queue backtest: %s", exc)
        raise HTTPException(status_code=503, detail="Celery unavailable")


# ── Macroeconomics ────────────────────────────────────────────────────────────

@router_v2.get("/macroeconomics")
async def get_macro_all(db: AsyncSession = Depends(get_session)):
    result = await db.execute(
        select(MacroData).order_by(MacroData.release_date.desc()).limit(200)
    )
    rows = result.scalars().all()
    return [
        {
            "country": r.country,
            "indicator": r.indicator_name,
            "value": float(r.value) if r.value is not None else None,
            "previous_value": float(r.previous_value) if r.previous_value is not None else None,
            "release_date": r.release_date,
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
        import json as _json
        hold_indicators: dict = {}
        hold_ta_score: float = 0.0
        hold_fa_score: float = 0.0
        hold_sentiment_score: float = 0.0
        hold_geo_score: float = 0.0

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
            if "news_records" not in dir():
                news_records = await _get_news(db, limit=30)  # type: ignore[assignment]
            sent_engine = SentimentEngineV2(news_events=news_records)  # type: ignore[arg-type]
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
            "indicators_snapshot": _json.dumps(hold_indicators),
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
        "status_signal": signal.status,
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

    # Fallback for old records without stored pnl_usd
    if total_pnl_usd_stored == 0 and total_pnl_pct != 0:
        total_pnl_usd_stored = pnl_usd(total_pnl_pct)
    if avg_win_usd == 0:
        avg_win_pct_q = await db.execute(
            select(func.avg(SignalResult.pnl_percent))
            .where(_real_trades, SignalResult.result == "win")
        )
        avg_win_usd = pnl_usd(float(avg_win_pct_q.scalar() or 0))
    if avg_loss_usd == 0:
        avg_loss_pct_q = await db.execute(
            select(func.avg(SignalResult.pnl_percent))
            .where(_real_trades, SignalResult.result == "loss")
        )
        avg_loss_usd = pnl_usd(float(avg_loss_pct_q.scalar() or 0))

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
    from collections import defaultdict
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

    from collections import defaultdict
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
            "account_balance_at_entry": float(pos.account_balance_at_entry) if pos.account_balance_at_entry else None,
            "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
        })

    return positions


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
async def health_v2():
    return {"status": "ok", "version": "2.0"}


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
