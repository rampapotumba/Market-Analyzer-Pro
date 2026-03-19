"""BacktestEngine v4 — candle-by-candle historical simulation (SIM-22).

Architecture (per SPEC_SIMULATOR_V4.md §SIM-22):
  1. create_backtest_run (status=running)
  2. Load price_data for each symbol in [start_date, end_date]
  3. Iterate candles chronologically — NO lookahead (slice [0..i-1])
  4. Compute TA score + regime from slice; fa/sentiment/geo = 0.0 (neutral per SIM-17)
  5. Generate signal (SignalEngineV2 core logic without LLM/DB guards)
  6. Entry fill on NEXT candle open price
  7. SL/TP check per SIM-09 logic (high/low of candle) — worst case when both hit = SL
  8. Accumulate BacktestTradeResult list in memory
  9. Bulk insert → backtest_trades
  10. Compute summary: win_rate, PF, max_drawdown, equity_curve, monthly_returns
  11. Update backtest_run (status=completed, summary=...)

Isolation guarantee: this module NEVER writes to signal_results or virtual_portfolio.
"""

import asyncio
import datetime
import logging
from decimal import Decimal
from typing import Any, Optional

import pandas as pd

from sqlalchemy.ext.asyncio import AsyncSession

from src.backtesting.backtest_params import BacktestParams, BacktestTradeResult
from src.config import INSTRUMENT_OVERRIDES
from src.database.crud import (
    create_backtest_run,
    create_backtest_trades_bulk,
    get_instrument_by_symbol,
    get_price_data,
    update_backtest_run,
)
from src.signals.filter_pipeline import SignalFilterPipeline
from src.signals.risk_manager_v2 import REGIME_RR_MAP, ATR_SL_MULTIPLIER_MAP, RiskManagerV2

logger = logging.getLogger(__name__)

# ── In-memory progress tracker: run_id → pct (0–100) ─────────────────────────
# Updated from worker thread — safe due to GIL for simple dict writes.
# Cleared when run completes or fails.
_backtest_progress: dict[str, float] = {}


# SIM-31: Allowed signal strengths (weak signals filtered out)
ALLOWED_SIGNAL_STRENGTHS = {"BUY", "STRONG_BUY", "SELL", "STRONG_SELL"}

# SIM-32: Weekday filter config
WEEKDAY_FILTER = {
    "monday_block_until_utc": 10,   # Mon 00:00–10:00 UTC blocked
    "friday_block_from_utc": 18,    # Fri 18:00–23:59 UTC blocked
    "crypto_exempt_monday": True,   # crypto exempt from Monday gap filter
}


def get_backtest_progress(run_id: str) -> float | None:
    """Return progress percentage (0–100) for a running backtest, or None if not tracked."""
    return _backtest_progress.get(run_id)


# ── Thresholds (mirror SignalEngineV2) ────────────────────────────────────────
_BUY_THRESHOLD = 10.0     # composite score to emit a signal
_SELL_THRESHOLD = -10.0

# Minimum bars needed before first signal attempt
_MIN_BARS_HISTORY = 50

# Weight used for TA when fa/sentiment/geo = 0.0
# Mirrors DEFAULT_TA_WEIGHT in settings (0.45 default)
_TA_WEIGHT = 0.45

# Market type → pip size for P&L calculation
_PIP_SIZE: dict[str, Decimal] = {
    "forex": Decimal("0.0001"),
    "crypto": Decimal("0.01"),
    "stocks": Decimal("0.01"),
}

# Slippage for SL exits (mirrors SIM-10)
_SL_SLIPPAGE: dict[str, Decimal] = {
    "forex": Decimal("0.0001"),   # 1 pip
    "stocks": Decimal("0.01"),    # 1 pip (dollar)
    "crypto": Decimal("0.001"),   # 0.1%
}

# Cooldown per timeframe in minutes — mirrors SignalEngine.SIGNAL_COOLDOWN_MINUTES
_COOLDOWN_MINUTES: dict[str, int] = {
    "M1": 1, "M5": 5, "M15": 15,
    "H1": 60, "H4": 240, "D1": 1440,
}

# EU/NA forex pairs blocked during Asian low-liquidity session (00:00–06:59 UTC)
# mirrors SignalEngine._FOREX_PAIRS_EU_NA + _is_low_liquidity_session
_FOREX_PAIRS_EU_NA: frozenset[str] = frozenset({
    "EURUSD=X", "GBPUSD=X", "USDCHF=X", "EURGBP=X",
    "EURCAD=X", "GBPCAD=X", "EURCHF=X", "GBPCHF=X",
})
_ASIAN_SESSION_UTC_START = 0   # 00:00 UTC
_ASIAN_SESSION_UTC_END   = 7   # 07:00 UTC (exclusive)


def _is_asian_session(candle_ts: datetime.datetime) -> bool:
    """Return True if candle timestamp falls in low-liquidity Asian hours (UTC)."""
    hour = candle_ts.hour if candle_ts.tzinfo else candle_ts.replace(
        tzinfo=datetime.timezone.utc
    ).hour
    return _ASIAN_SESSION_UTC_START <= hour < _ASIAN_SESSION_UTC_END


def _get_signal_strength(composite: float) -> str:
    """Map composite score to signal strength bucket (SIM-14 score buckets).

    strong_buy:  >= +15
    buy:          +10..+15
    weak_buy:      +7..+10
    neutral:       -7..+7
    weak_sell:    -10..-7
    sell:         -15..-10
    strong_sell:  <= -15
    """
    if composite >= 15.0:
        return "STRONG_BUY"
    elif composite >= 10.0:
        return "BUY"
    elif composite >= 7.0:
        return "WEAK_BUY"
    elif composite <= -15.0:
        return "STRONG_SELL"
    elif composite <= -10.0:
        return "SELL"
    elif composite <= -7.0:
        return "WEAK_SELL"
    else:
        return "HOLD"


def _detect_regime_from_df(df: pd.DataFrame) -> str:
    """Lightweight regime detection from a price DataFrame.

    Returns one of: STRONG_TREND_BULL, STRONG_TREND_BEAR, TREND_BULL, TREND_BEAR,
                    RANGING, VOLATILE, DEFAULT.
    Uses ADX and SMA-200 — matches RegimeDetector._detect_regime() logic.
    """
    from src.analysis.regime_detector import RegimeDetector

    rd = RegimeDetector()
    regime, _adx, _atr_pct = rd._detect_regime(df, vix=None)

    # Normalise to keys used by ATR_SL_MULTIPLIER_MAP / REGIME_RR_MAP
    _MAP = {
        "HIGH_VOLATILITY": "VOLATILE",
        "LOW_VOLATILITY": "RANGING",
        "WEAK_TREND_BULL": "TREND_BULL",
        "WEAK_TREND_BEAR": "TREND_BEAR",
        "STRONG_TREND_BULL": "STRONG_TREND_BULL",
        "STRONG_TREND_BEAR": "STRONG_TREND_BEAR",
        "RANGING": "RANGING",
        "VOLATILE": "VOLATILE",
    }
    return _MAP.get(regime, "DEFAULT")


def _to_ohlcv_df(price_rows: list) -> pd.DataFrame:
    """Convert price_data ORM rows to pandas DataFrame for TAEngine."""
    records = [
        {
            "timestamp": row.timestamp,
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "volume": float(row.volume or 0),
        }
        for row in price_rows
    ]
    df = pd.DataFrame(records)
    if df.empty:
        return df
    df = df.set_index("timestamp").sort_index()
    return df


def _compute_sl_exit_price(
    sl_price: Decimal,
    direction: str,
    market_type: str,
    entry_price: Decimal,
) -> Decimal:
    """Apply SL slippage per SIM-10."""
    slip = _SL_SLIPPAGE.get(market_type, Decimal("0.0001"))
    if market_type == "crypto":
        slip = entry_price * Decimal("0.001")  # 0.1% of entry
    if direction == "LONG":
        return sl_price - slip
    else:
        return sl_price + slip


def _compute_pnl(
    direction: str,
    entry: Decimal,
    exit_price: Decimal,
    position_pct: Decimal,
    account_size: Decimal,
    market_type: str,
) -> tuple[Decimal, Decimal]:
    """Return (pnl_pips, pnl_usd)."""
    pip = _PIP_SIZE.get(market_type, Decimal("0.0001"))
    if direction == "LONG":
        price_move = exit_price - entry
    else:
        price_move = entry - exit_price

    pnl_pips = price_move / pip
    risk_usd = account_size * position_pct / Decimal("100")
    sl_dist = None  # estimated below if needed

    # Simplified P&L: proportional to price move relative to position size
    # pnl_usd = account * size_pct% * (move / entry)  (approximate)
    if entry > 0:
        pnl_usd = risk_usd * (price_move / entry) * Decimal("100")
    else:
        pnl_usd = Decimal("0")

    return pnl_pips.quantize(Decimal("0.0001")), pnl_usd.quantize(Decimal("0.0001"))


def _compute_summary(
    trades: list[BacktestTradeResult],
    account_size: Decimal,
) -> dict[str, Any]:
    """Compute aggregate backtest statistics."""
    total = len(trades)
    wins = [t for t in trades if t.result == "win"]
    losses = [t for t in trades if t.result == "loss"]

    win_rate = (len(wins) / total * 100) if total > 0 else 0.0
    total_pnl = sum(float(t.pnl_usd or 0) for t in trades)
    gross_win = sum(float(t.pnl_usd or 0) for t in wins)
    gross_loss = abs(sum(float(t.pnl_usd or 0) for t in losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None

    avg_dur = (
        sum(t.duration_minutes or 0 for t in trades) / total if total > 0 else 0
    )

    long_count = sum(1 for t in trades if t.direction == "LONG")
    short_count = sum(1 for t in trades if t.direction == "SHORT")

    # Equity curve: running balance, downsampled to max 200 points
    balance = float(account_size)
    raw_curve: list[dict] = []
    for t in sorted(trades, key=lambda x: x.exit_at or datetime.datetime.min):
        balance += float(t.pnl_usd or 0)
        raw_curve.append({
            "date": (t.exit_at.isoformat() if t.exit_at else ""),
            "balance": round(balance, 4),
        })
    # Downsample: keep first, last, and evenly spaced points in between
    _max_points = 200
    if len(raw_curve) > _max_points:
        step = len(raw_curve) / (_max_points - 1)
        indices = set(int(i * step) for i in range(_max_points - 1))
        indices.add(len(raw_curve) - 1)
        equity_curve = [raw_curve[i] for i in sorted(indices)]
    else:
        equity_curve = raw_curve

    # Max drawdown
    peak = float(account_size)
    max_dd = 0.0
    running = float(account_size)
    for t in sorted(trades, key=lambda x: x.exit_at or datetime.datetime.min):
        running += float(t.pnl_usd or 0)
        if running > peak:
            peak = running
        dd = (peak - running) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # Monthly returns
    monthly: dict[str, dict] = {}
    for t in trades:
        if t.exit_at:
            key = t.exit_at.strftime("%Y-%m")
            if key not in monthly:
                monthly[key] = {"month": key, "pnl_usd": 0.0, "trades": 0}
            monthly[key]["pnl_usd"] += float(t.pnl_usd or 0)
            monthly[key]["trades"] += 1

    # By symbol
    by_symbol: dict[str, dict] = {}
    for t in trades:
        s = t.symbol
        if s not in by_symbol:
            by_symbol[s] = {"trades": 0, "wins": 0, "pnl_usd": 0.0}
        by_symbol[s]["trades"] += 1
        if t.result == "win":
            by_symbol[s]["wins"] += 1
        by_symbol[s]["pnl_usd"] += float(t.pnl_usd or 0)

    # By score bucket (mirrors SIM-14 buckets)
    def _score_bucket(score: Optional[Decimal]) -> str:
        if score is None:
            return "unknown"
        s = float(score)
        if s >= 15:
            return "strong_buy"
        if s >= 10:
            return "buy"
        if s >= 7:
            return "weak_buy"
        if s <= -15:
            return "strong_sell"
        if s <= -10:
            return "sell"
        if s <= -7:
            return "weak_sell"
        return "neutral"

    by_score: dict[str, dict] = {}
    for t in trades:
        bucket = _score_bucket(t.composite_score)
        if bucket not in by_score:
            by_score[bucket] = {"trades": 0, "wins": 0, "pnl_usd": 0.0}
        by_score[bucket]["trades"] += 1
        if t.result == "win":
            by_score[bucket]["wins"] += 1
        by_score[bucket]["pnl_usd"] += float(t.pnl_usd or 0)

    # ── SIM-44: Extended metrics ──────────────────────────────────────────────

    # Direction-specific win rates
    long_trades = [t for t in trades if t.direction == "LONG"]
    short_trades = [t for t in trades if t.direction == "SHORT"]
    long_wins = [t for t in long_trades if t.result == "win"]
    short_wins = [t for t in short_trades if t.result == "win"]
    win_rate_long = (len(long_wins) / len(long_trades) * 100) if long_trades else 0.0
    win_rate_short = (len(short_wins) / len(short_trades) * 100) if short_trades else 0.0

    # Duration by result
    win_durations = [t.duration_minutes or 0 for t in wins]
    loss_durations = [t.duration_minutes or 0 for t in losses]
    avg_win_dur = sum(win_durations) / len(win_durations) if win_durations else 0.0
    avg_loss_dur = sum(loss_durations) / len(loss_durations) if loss_durations else 0.0

    # By weekday
    by_weekday: dict[str, dict] = {}
    for t in trades:
        if t.entry_at:
            wd = str(t.entry_at.weekday())  # 0=Mon..4=Fri
            if wd not in by_weekday:
                by_weekday[wd] = {"trades": 0, "wins": 0, "pnl_usd": 0.0}
            by_weekday[wd]["trades"] += 1
            if t.result == "win":
                by_weekday[wd]["wins"] += 1
            by_weekday[wd]["pnl_usd"] += float(t.pnl_usd or 0)

    # By hour UTC
    by_hour: dict[str, dict] = {}
    for t in trades:
        if t.entry_at:
            hr = str(t.entry_at.hour)
            if hr not in by_hour:
                by_hour[hr] = {"trades": 0, "wins": 0}
            by_hour[hr]["trades"] += 1
            if t.result == "win":
                by_hour[hr]["wins"] += 1
    by_hour_utc = {
        hr: {
            "trades": v["trades"],
            "win_rate_pct": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] > 0 else 0.0,
        }
        for hr, v in by_hour.items()
    }

    # By regime
    by_regime: dict[str, dict] = {}
    for t in trades:
        regime_key = getattr(t, "regime", None) or "UNKNOWN"
        if regime_key not in by_regime:
            by_regime[regime_key] = {"trades": 0, "wins": 0, "pnl_usd": 0.0}
        by_regime[regime_key]["trades"] += 1
        if t.result == "win":
            by_regime[regime_key]["wins"] += 1
        by_regime[regime_key]["pnl_usd"] += float(t.pnl_usd or 0)

    # Exit reason counts
    sl_hit_count = sum(1 for t in trades if t.exit_reason == "sl_hit")
    tp_hit_count = sum(1 for t in trades if t.exit_reason == "tp_hit")
    mae_exit_count = sum(1 for t in trades if t.exit_reason == "mae_exit")
    time_exit_count = sum(1 for t in trades if t.exit_reason == "time_exit")

    # Average MAE (use raw mae field)
    mae_values = [float(t.mae or 0) for t in trades if t.mae is not None and t.mae > 0]
    avg_mae_pct_of_sl = sum(mae_values) / len(mae_values) if mae_values else 0.0

    return {
        "total_trades": total,
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "total_pnl_usd": round(total_pnl, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "avg_duration_minutes": round(avg_dur, 1),
        "long_count": long_count,
        "short_count": short_count,
        "equity_curve": equity_curve,
        "monthly_returns": sorted(monthly.values(), key=lambda x: x["month"]),
        "by_symbol": by_symbol,
        "by_score_bucket": by_score,
        # SIM-44: Extended metrics
        "win_rate_long_pct": round(win_rate_long, 2),
        "win_rate_short_pct": round(win_rate_short, 2),
        "avg_win_duration_minutes": round(avg_win_dur, 1),
        "avg_loss_duration_minutes": round(avg_loss_dur, 1),
        "by_weekday": by_weekday,
        "by_hour_utc": by_hour_utc,
        "by_regime": by_regime,
        "sl_hit_count": sl_hit_count,
        "tp_hit_count": tp_hit_count,
        "mae_exit_count": mae_exit_count,
        "time_exit_count": time_exit_count,
        "avg_mae_pct_of_sl": round(avg_mae_pct_of_sl, 4),
    }


class BacktestEngine:
    """Candle-by-candle backtest engine (SIM-22).

    Isolation guarantee: NEVER writes to signal_results or virtual_portfolio.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._rm = RiskManagerV2()

    async def run_backtest(self, params: BacktestParams) -> str:
        """
        Run a full backtest. Creates a run record, simulates candle-by-candle,
        persists results, and returns the run_id (UUID string).
        """
        run_id = await create_backtest_run(self.db, params.model_dump(mode="json"))
        await update_backtest_run(self.db, run_id, "running")
        await self.db.commit()

        try:
            trades = await self._simulate(params)
            summary = _compute_summary(trades, params.account_size)

            trade_dicts = [
                {
                    "symbol": t.symbol,
                    "timeframe": t.timeframe,
                    "direction": t.direction,
                    "entry_price": str(t.entry_price),
                    "exit_price": str(t.exit_price) if t.exit_price is not None else None,
                    "exit_reason": t.exit_reason,
                    "pnl_pips": str(t.pnl_pips) if t.pnl_pips is not None else None,
                    "pnl_usd": str(t.pnl_usd) if t.pnl_usd is not None else None,
                    "result": t.result,
                    "composite_score": str(t.composite_score) if t.composite_score is not None else None,
                    "entry_at": t.entry_at,
                    "exit_at": t.exit_at,
                    "duration_minutes": t.duration_minutes,
                    "mfe": str(t.mfe) if t.mfe is not None else None,
                    "mae": str(t.mae) if t.mae is not None else None,
                }
                for t in trades
            ]

            await create_backtest_trades_bulk(self.db, run_id, trade_dicts)
            await update_backtest_run(self.db, run_id, "completed", summary)
            await self.db.commit()

            logger.info(
                "[SIM-22] Backtest %s completed: %d trades, win_rate=%.1f%%, PF=%s",
                run_id,
                summary["total_trades"],
                summary["win_rate_pct"],
                summary.get("profit_factor"),
            )
        except Exception as exc:
            logger.error("[SIM-22] Backtest %s failed: %s", run_id, exc, exc_info=True)
            await update_backtest_run(self.db, run_id, "failed", {"error": str(exc)})
            await self.db.commit()

        return run_id

    async def _simulate(
        self,
        params: BacktestParams,
        run_id: str | None = None,
    ) -> list[BacktestTradeResult]:
        """Core simulation loop. Returns list of completed trades.

        If run_id is provided, updates _backtest_progress[run_id] (0–100)
        after each symbol completes so callers can poll progress.
        """
        trades: list[BacktestTradeResult] = []

        start_dt = datetime.datetime.fromisoformat(params.start_date).replace(
            tzinfo=datetime.timezone.utc
        )
        end_dt = datetime.datetime.fromisoformat(params.end_date).replace(
            hour=23, minute=59, second=59, tzinfo=datetime.timezone.utc
        )

        # SIM-33: Pre-load economic events for calendar filter
        economic_events: list = []
        try:
            from src.database.crud import get_economic_events_in_range
            economic_events = await get_economic_events_in_range(self.db, start_dt, end_dt)
            logger.info("[SIM-33] Loaded %d HIGH-impact economic events", len(economic_events))
        except Exception as exc:
            logger.warning("[SIM-33] Could not load economic events: %s", exc)

        total_symbols = len(params.symbols)
        if run_id:
            _backtest_progress[run_id] = 0.0

        for sym_idx, symbol in enumerate(params.symbols):
            instrument = await get_instrument_by_symbol(self.db, symbol)
            if instrument is None:
                logger.warning("[SIM-22] Instrument %s not found — skipping", symbol)
                if run_id:
                    _backtest_progress[run_id] = round((sym_idx + 1) / total_symbols * 100, 1)
                continue

            market_type: str = instrument.market or "forex"

            price_rows = await get_price_data(
                self.db,
                instrument.id,
                params.timeframe,
                from_dt=start_dt,
                to_dt=end_dt,
                limit=100_000,
            )

            if len(price_rows) < _MIN_BARS_HISTORY + 2:
                logger.warning(
                    "[SIM-22] %s: only %d candles — need at least %d, skipping",
                    symbol, len(price_rows), _MIN_BARS_HISTORY + 2,
                )
                if run_id:
                    _backtest_progress[run_id] = round((sym_idx + 1) / total_symbols * 100, 1)
                continue

            # progress_cb: called from the worker thread after every N candles
            sym_base_pct = sym_idx / total_symbols * 100
            sym_share = 100.0 / total_symbols

            def _progress_cb(candle_pct: float) -> None:
                if run_id:
                    _backtest_progress[run_id] = round(sym_base_pct + candle_pct * sym_share / 100, 1)

            symbol_trades = await asyncio.to_thread(
                self._simulate_symbol,
                symbol=symbol,
                market_type=market_type,
                timeframe=params.timeframe,
                price_rows=price_rows,
                account_size=params.account_size,
                apply_slippage=params.apply_slippage,
                progress_cb=_progress_cb,
                economic_events=economic_events,
                params=params,
            )
            trades.extend(symbol_trades)

            if run_id:
                _backtest_progress[run_id] = round((sym_idx + 1) / total_symbols * 100, 1)
                logger.info("[SIM-22] %s done — progress %.1f%%", symbol, _backtest_progress[run_id])

        return trades

    def _simulate_symbol(
        self,
        symbol: str,
        market_type: str,
        timeframe: str,
        price_rows: list,
        account_size: Decimal,
        apply_slippage: bool,
        progress_cb: Any = None,
        economic_events: list | None = None,
        params: Optional[BacktestParams] = None,
    ) -> list[BacktestTradeResult]:
        """Simulate one symbol over all its candles. Returns closed trades.

        NOTE: sync (no awaits) — called via asyncio.to_thread() to avoid blocking event loop.
        progress_cb(pct: float) is called every 100 candles if provided.

        All signal-level filtering is handled by SignalFilterPipeline.run_all().
        _generate_signal() only computes the raw signal (TA, composite, regime, ATR).
        """
        # Build filter pipeline from BacktestParams (SIM-42/SIM-43)
        pipeline = SignalFilterPipeline(
            apply_score_filter=True,  # always on
            apply_regime_filter=params.apply_ranging_filter if params else True,
            apply_d1_trend_filter=params.apply_d1_trend_filter if params else True,
            apply_volume_filter=params.apply_volume_filter if params else True,
            apply_momentum_filter=params.apply_momentum_filter if params else True,
            apply_weekday_filter=params.apply_weekday_filter if params else True,
            apply_calendar_filter=params.apply_calendar_filter if params else True,
            apply_session_filter=params.apply_session_filter if params else True,
            apply_dxy_filter=True,  # always on when DXY data is available
            min_composite_score=params.min_composite_score if params else None,
        )

        trades: list[BacktestTradeResult] = []
        open_trade: Optional[dict[str, Any]] = None  # one position at a time per symbol
        last_signal_ts: Optional[datetime.datetime] = None  # cooldown tracking
        cooldown_minutes = _COOLDOWN_MINUTES.get(timeframe, 60)

        n = len(price_rows)
        _progress_interval = 100  # call progress_cb every N candles

        for i in range(_MIN_BARS_HISTORY, n - 1):
            # ── Progress callback every N candles ─────────────────────────────
            if progress_cb is not None and (i - _MIN_BARS_HISTORY) % _progress_interval == 0:
                progress_cb((i - _MIN_BARS_HISTORY) / max(n - _MIN_BARS_HISTORY - 1, 1) * 100)

            # ── NO LOOKAHEAD: only rows [0..i-1] visible to signal generation
            history = price_rows[:i]
            df = _to_ohlcv_df(history)
            current_candle = price_rows[i]
            next_candle = price_rows[i + 1]

            # ── Check open trade SL/TP on current candle ──────────────────────
            if open_trade is not None:
                closed = self._check_exit(
                    open_trade=open_trade,
                    candle=current_candle,
                    market_type=market_type,
                    apply_slippage=apply_slippage,
                )
                if closed:
                    trades.append(closed)
                    open_trade = None
                else:
                    # Update MAE/MFE
                    candle_high = float(current_candle.high)
                    candle_low = float(current_candle.low)
                    direction = open_trade["direction"]
                    entry = float(open_trade["entry_price"])
                    if direction == "LONG":
                        mfe_candidate = candle_high - entry
                        mae_candidate = entry - candle_low
                    else:
                        mfe_candidate = entry - candle_low
                        mae_candidate = candle_high - entry
                    if mfe_candidate > float(open_trade["mfe"]):
                        open_trade["mfe"] = mfe_candidate
                    if mae_candidate > float(open_trade["mae"]):
                        open_trade["mae"] = mae_candidate

            # ── Generate signal from history slice ────────────────────────────
            if open_trade is not None:
                continue  # one position per symbol at a time

            candle_ts: datetime.datetime = current_candle.timestamp

            # Cooldown filter: mirror SignalEngine cooldown per timeframe
            if last_signal_ts is not None:
                elapsed = (candle_ts - last_signal_ts).total_seconds() / 60
                if elapsed < cooldown_minutes:
                    continue

            # _generate_signal() returns raw signal with ta_indicators — no filtering inside
            signal = self._generate_signal(df, symbol, market_type, timeframe)
            if signal is None:
                continue

            # ── Run unified filter pipeline (SIM-42) ─────────────────────────
            filter_context = {
                "composite_score": float(signal["composite_score"]),
                "market_type": market_type,
                "symbol": symbol,
                "regime": signal["regime"],
                "direction": signal["direction"],
                "timeframe": timeframe,
                "df": df,
                "ta_indicators": signal.get("ta_indicators", {}),
                "candle_ts": candle_ts,
                "d1_rows": [],   # D1 data not available in candle-level loop
                "economic_events": economic_events or [],
                "dxy_rsi": None,  # DXY not available in backtest without external data
            }
            passed, reason = pipeline.run_all(filter_context)
            if not passed:
                logger.debug("[Pipeline] Signal blocked for %s: %s", symbol, reason)
                continue

            # ── Entry on NEXT candle open (no lookahead) ──────────────────────
            entry_price = Decimal(str(next_candle.open))
            entry_at = next_candle.timestamp
            direction = signal["direction"]

            sl, tp = self._recalc_sl_tp(
                entry=entry_price,
                atr=signal["atr"],
                direction=direction,
                regime=signal["regime"],
                symbol=symbol,
                support_levels=signal.get("support_levels"),
                resistance_levels=signal.get("resistance_levels"),
            )
            if sl is None or tp is None:
                continue

            last_signal_ts = candle_ts
            open_trade = {
                "symbol": symbol,
                "timeframe": timeframe,
                "direction": direction,
                "entry_price": entry_price,
                "entry_at": entry_at,
                "stop_loss": sl,
                "take_profit": tp,
                "composite_score": signal["composite_score"],
                "position_pct": signal["position_pct"],
                "mfe": 0.0,
                "mae": 0.0,
                "regime": signal["regime"],
            }
            # Skip to next candle
            i += 1

        # ── If position still open at end of data — close at last price ───────
        if open_trade is not None:
            last = price_rows[-1]
            exit_price = Decimal(str(last.close))
            pnl_pips, pnl_usd = _compute_pnl(
                open_trade["direction"],
                open_trade["entry_price"],
                exit_price,
                Decimal(str(open_trade["position_pct"])),
                account_size,
                market_type,
            )
            dur = None
            if open_trade["entry_at"] and last.timestamp:
                dur = int(
                    (last.timestamp - open_trade["entry_at"]).total_seconds() / 60
                )
            trades.append(BacktestTradeResult(
                symbol=symbol,
                timeframe=timeframe,
                direction=open_trade["direction"],
                entry_price=open_trade["entry_price"],
                exit_price=exit_price,
                exit_reason="end_of_data",
                pnl_pips=pnl_pips,
                pnl_usd=pnl_usd,
                result="win" if pnl_usd >= 0 else "loss",
                composite_score=open_trade.get("composite_score"),
                entry_at=open_trade["entry_at"],
                exit_at=last.timestamp,
                duration_minutes=dur,
                mfe=Decimal(str(round(open_trade["mfe"], 8))),
                mae=Decimal(str(round(open_trade["mae"], 8))),
                regime=open_trade.get("regime"),
            ))

        return trades

    def _check_exit(
        self,
        open_trade: dict[str, Any],
        candle: Any,
        market_type: str,
        apply_slippage: bool,
    ) -> Optional[BacktestTradeResult]:
        """
        SIM-09 logic: check SL and TP by candle high/low.
        Worst case: if both SL and TP are breached → exit at SL.
        Returns a closed BacktestTradeResult or None if still open.
        """
        direction = open_trade["direction"]
        sl = open_trade["stop_loss"]
        tp = open_trade["take_profit"]
        entry = open_trade["entry_price"]
        candle_high = Decimal(str(candle.high))
        candle_low = Decimal(str(candle.low))

        sl_hit = False
        tp_hit = False

        if direction == "LONG":
            sl_hit = candle_low <= sl
            tp_hit = candle_high >= tp
        else:
            sl_hit = candle_high >= sl
            tp_hit = candle_low <= tp

        if not sl_hit and not tp_hit:
            return None

        # Worst case (SIM-06): if both hit → SL
        if sl_hit:
            exit_price = sl
            if apply_slippage:
                exit_price = _compute_sl_exit_price(sl, direction, market_type, entry)
            exit_reason = "sl_hit"
        else:
            exit_price = tp
            exit_reason = "tp_hit"

        pnl_pips, pnl_usd = _compute_pnl(
            direction, entry, exit_price,
            Decimal(str(open_trade["position_pct"])),
            Decimal("1000"),  # account size for P&L pct calculation
            market_type,
        )

        dur = None
        if open_trade["entry_at"] and candle.timestamp:
            dur = int(
                (candle.timestamp - open_trade["entry_at"]).total_seconds() / 60
            )

        return BacktestTradeResult(
            symbol=open_trade["symbol"],
            timeframe=open_trade["timeframe"],
            direction=direction,
            entry_price=entry,
            exit_price=exit_price,
            exit_reason=exit_reason,
            pnl_pips=pnl_pips,
            pnl_usd=pnl_usd,
            result="win" if pnl_usd >= 0 else "loss",
            composite_score=open_trade.get("composite_score"),
            entry_at=open_trade["entry_at"],
            exit_at=candle.timestamp,
            duration_minutes=dur,
            mfe=Decimal(str(round(open_trade["mfe"], 8))),
            mae=Decimal(str(round(open_trade["mae"], 8))),
            regime=open_trade.get("regime"),
        )

    # ── Filter delegation methods ─────────────────────────────────────────────
    # These are thin wrappers that delegate to SignalFilterPipeline.
    # Kept for backward compatibility with existing tests and external callers.
    # The canonical implementation lives in SignalFilterPipeline.

    @staticmethod
    def _check_d1_trend_alignment(
        symbol: str,
        direction: str,
        timeframe: str,
        d1_rows: list,
    ) -> bool:
        """SIM-27: D1 MA200 trend alignment filter. Delegates to SignalFilterPipeline."""
        pipeline = SignalFilterPipeline.__new__(SignalFilterPipeline)
        passed, _ = pipeline.check_d1_trend(symbol, direction, timeframe, d1_rows)
        return passed

    @staticmethod
    def _check_volume_confirmation(df: pd.DataFrame) -> bool:
        """SIM-29: Volume >= 120% of MA20. Delegates to SignalFilterPipeline."""
        pipeline = SignalFilterPipeline.__new__(SignalFilterPipeline)
        # Pass "stocks" to avoid the forex skip — this matches original behaviour
        # (original method had no market_type awareness; callers pass non-forex DFs).
        passed, _ = pipeline.check_volume(df, market_type="stocks")
        return passed

    @staticmethod
    def _check_momentum_alignment(ta_indicators: dict, direction: str) -> bool:
        """SIM-30: Momentum alignment filter. Delegates to SignalFilterPipeline."""
        pipeline = SignalFilterPipeline.__new__(SignalFilterPipeline)
        passed, _ = pipeline.check_momentum(ta_indicators, direction)
        return passed

    @staticmethod
    def _check_weekday_filter(ts: datetime.datetime, market_type: str) -> bool:
        """SIM-32: Weekday filter. Delegates to SignalFilterPipeline."""
        pipeline = SignalFilterPipeline.__new__(SignalFilterPipeline)
        passed, _ = pipeline.check_weekday(ts, market_type)
        return passed

    @staticmethod
    def _check_economic_calendar(candle_ts: datetime.datetime, economic_events: list) -> bool:
        """SIM-33: Economic calendar filter. Delegates to SignalFilterPipeline."""
        pipeline = SignalFilterPipeline.__new__(SignalFilterPipeline)
        passed, _ = pipeline.check_calendar(candle_ts, economic_events)
        return passed

    @staticmethod
    def _check_dxy_alignment(direction: str, symbol: str, dxy_rsi: Optional[float]) -> bool:
        """SIM-38: DXY RSI filter. Delegates to SignalFilterPipeline."""
        pipeline = SignalFilterPipeline.__new__(SignalFilterPipeline)
        passed, _ = pipeline.check_dxy_alignment(direction, symbol, dxy_rsi)
        return passed

    def _generate_signal(
        self,
        df: pd.DataFrame,
        symbol: str,
        market_type: str,
        timeframe: str,
    ) -> Optional[dict[str, Any]]:
        """
        Lightweight raw signal generation from a DataFrame slice.

        Mirrors SignalEngineV2.generate() core logic:
        - ta_score via TAEngine
        - fa/sentiment/geo = 0.0 (no historical data available in backtest)
        - composite = TA weight × ta_score (neutral others)

        Returns the raw signal dict with ta_indicators included.
        ALL signal-level filtering is delegated to SignalFilterPipeline.run_all()
        in _simulate_symbol(). LLM and DB guards are deliberately skipped.
        """
        from src.analysis.ta_engine import TAEngine

        try:
            ta_engine = TAEngine(df, timeframe=timeframe)
            ta_score = ta_engine.calculate_ta_score()
            atr = ta_engine.get_atr(14)
        except Exception as exc:
            logger.debug("[SIM-22] TAEngine error for %s: %s", symbol, exc)
            return None

        if atr is None or atr <= 0:
            return None

        composite = _TA_WEIGHT * ta_score

        # composite == 0 → no clear direction, skip
        if composite == 0:
            return None

        direction = "LONG" if composite > 0 else "SHORT"

        regime = "DEFAULT"
        try:
            regime = _detect_regime_from_df(df)
        except Exception as exc:
            logger.debug("[SIM-22] Regime detection error for %s: %s", symbol, exc)

        # Compute all TA indicators for pipeline (SIM-30 momentum, SIM-36 S/R)
        ta_indicators: dict = {}
        support_levels: list[Decimal] = []
        resistance_levels: list[Decimal] = []
        try:
            ta_indicators = ta_engine.calculate_all_indicators()
            raw_support = ta_indicators.get("support_levels") or []
            raw_resistance = ta_indicators.get("resistance_levels") or []
            if isinstance(raw_support, list):
                support_levels = [Decimal(str(v)) for v in raw_support if v is not None]
            if isinstance(raw_resistance, list):
                resistance_levels = [Decimal(str(v)) for v in raw_resistance if v is not None]
        except Exception:
            pass

        return {
            "direction": direction,
            "composite_score": Decimal(str(round(composite, 4))),
            "ta_score": ta_score,
            "regime": regime,
            "atr": atr,
            "position_pct": 2.0,  # fixed 2% risk per SIM-19 default
            "ta_indicators": ta_indicators,         # passed to pipeline for SIM-30
            "support_levels": support_levels,       # SIM-36
            "resistance_levels": resistance_levels, # SIM-36
        }

    def _recalc_sl_tp(
        self,
        entry: Decimal,
        atr: Decimal,
        direction: str,
        regime: str,
        symbol: str = "",
        support_levels: Optional[list[Decimal]] = None,
        resistance_levels: Optional[list[Decimal]] = None,
    ) -> tuple[Optional[Decimal], Optional[Decimal]]:
        """Compute SL and TP for the backtest position using regime-adaptive rules.

        SIM-36: S/R levels passed to RiskManagerV2 for SL snapping.
        """
        try:
            # SIM-28: Apply per-instrument SL ATR multiplier override
            overrides = INSTRUMENT_OVERRIDES.get(symbol, {})
            sl_atr_multiplier_override = overrides.get("sl_atr_multiplier")
            levels = self._rm.calculate_levels_for_regime(
                entry=entry,
                atr=atr,
                direction=direction,
                regime=regime,
                support_levels=support_levels,
                resistance_levels=resistance_levels,
                sl_atr_multiplier_override=sl_atr_multiplier_override,
            )
            return levels.get("stop_loss"), levels.get("take_profit_1")
        except Exception as exc:
            logger.debug("[SIM-22] RiskManager error: %s", exc)
            return None, None
