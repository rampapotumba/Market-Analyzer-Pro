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
import bisect
import datetime
import hashlib
import json
import logging
import math
from decimal import Decimal
from typing import Any, Optional

import numpy as np
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


def get_backtest_progress(run_id: str) -> Optional[float]:
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

# SIM-35: Maximum candles before time-based exit (only when unrealized PnL <= 0).
# Mirrors TIME_EXIT_CANDLES in config.py.
# CAL3-03: Restored H1 to 48 (was 24 in cal-r2 — too aggressive, 70% time_exit rate).
_TIME_EXIT_CANDLES: dict[str, int] = {"H1": 48, "H4": 20, "D1": 10}

# CAL2-06: Trailing stop constants.
# When MFE >= 50% of TP distance, move SL to lock 20% of TP distance profit.
_TRAILING_STOP_MFE_TRIGGER = Decimal("0.5")   # activate when MFE >= 50% of TP dist
_TRAILING_STOP_LOCK_RATIO = Decimal("0.2")    # lock entry + 20% of TP dist

# OPT-03: S/R cache refresh interval in candles.
# S/R levels are slow-moving (support/resistance from recent highs/lows, ~2 trading days).
# Recomputing every 50 H1 candles reduces ~3000-5000 full TAEngine calls to ~60-100 per symbol.
_SR_CACHE_INTERVAL = 50

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
    return _get_signal_strength_scaled(composite, scale=1.0)


def _get_signal_strength_scaled(composite: float, scale: float = 1.0) -> str:
    """Map composite score to signal strength bucket with scaled thresholds.

    V6 (TASK-V6-02): when only TA is available (scale=0.45), thresholds are
    proportionally reduced so the same quality gates apply in backtest.

    Examples:
      scale=1.0 (live):      STRONG_BUY >= 15.0, BUY >= 10.0
      scale=0.45 (backtest): STRONG_BUY >= 6.75, BUY >= 4.5
    """
    abs_score = abs(composite)
    if abs_score >= 15.0 * scale:
        return "STRONG_BUY" if composite > 0 else "STRONG_SELL"
    elif abs_score >= 10.0 * scale:
        return "BUY" if composite > 0 else "SELL"
    elif abs_score >= 7.0 * scale:
        return "WEAK_BUY" if composite > 0 else "WEAK_SELL"
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
    filter_stats: Optional[dict] = None,
) -> dict[str, Any]:
    """Compute aggregate backtest statistics.

    V6 TASK-V6-12: Primary metrics (WR, PF, avg_duration) exclude end_of_data trades.
    end_of_data trades are reported separately in summary["end_of_data_count/pnl"].

    V6 TASK-V6-10: filter_stats dict is merged into summary if provided.
    """
    total = len(trades)

    # V6 TASK-V6-12: Separate end_of_data trades from real trades for metric computation
    eod_trades = [t for t in trades if t.exit_reason == "end_of_data"]
    real_trades = [t for t in trades if t.exit_reason != "end_of_data"]

    # Warn if end_of_data trades represent a large fraction of all trades
    if total > 0 and len(eod_trades) / total > 0.20:
        logger.warning(
            "end_of_data trades: %d/%d (%.1f%%) — metrics may be unreliable",
            len(eod_trades), total, len(eod_trades) / total * 100,
        )

    # Primary metrics use only real_trades (no end_of_data distortion)
    metric_trades = real_trades
    metric_total = len(metric_trades)
    wins = [t for t in metric_trades if t.result == "win"]
    losses = [t for t in metric_trades if t.result == "loss"]

    win_rate = (len(wins) / metric_total * 100) if metric_total > 0 else 0.0
    # CAL2-01: total_pnl excludes end_of_data trades — these are unrealised positions,
    # not real exits. Including them inflated PF/PnL by ~37% in v6-cal-r1 backtest.
    total_pnl = sum(float(t.pnl_usd or 0) for t in metric_trades)
    total_pnl_incl_eod = sum(float(t.pnl_usd or 0) for t in trades)
    gross_win = sum(float(t.pnl_usd or 0) for t in wins)
    gross_loss = abs(sum(float(t.pnl_usd or 0) for t in losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None
    eod_warning = total > 0 and len(eod_trades) / total > 0.05

    avg_dur = (
        sum(t.duration_minutes or 0 for t in metric_trades) / metric_total
        if metric_total > 0 else 0
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

    # By symbol — CAL2-02: exclude end_of_data trades, add win_rate_pct
    by_symbol: dict[str, dict] = {}
    for t in metric_trades:
        s = t.symbol
        if s not in by_symbol:
            by_symbol[s] = {"trades": 0, "wins": 0, "pnl_usd": 0.0}
        by_symbol[s]["trades"] += 1
        if t.result == "win":
            by_symbol[s]["wins"] += 1
        by_symbol[s]["pnl_usd"] += float(t.pnl_usd or 0)
    # Add win_rate_pct to each symbol entry
    for s, d in by_symbol.items():
        d["win_rate_pct"] = round(d["wins"] / d["trades"] * 100, 2) if d["trades"] > 0 else 0.0

    # CAL2-09: Concentration risk warning
    concentration_warning: Optional[str] = None
    if by_symbol and total_pnl > 0:
        symbol_pnls = sorted(
            [(s, d["pnl_usd"]) for s, d in by_symbol.items()],
            key=lambda x: x[1], reverse=True,
        )
        top1_pct = symbol_pnls[0][1] / total_pnl * 100 if total_pnl > 0 else 0
        top2_pct = (
            (symbol_pnls[0][1] + symbol_pnls[1][1]) / total_pnl * 100
            if len(symbol_pnls) >= 2 and total_pnl > 0 else top1_pct
        )
        concentration_warnings: list[str] = []
        if top1_pct > 40:
            concentration_warnings.append(
                f"{symbol_pnls[0][0]} contributes {top1_pct:.1f}% of PnL"
            )
        if top2_pct > 70:
            concentration_warnings.append(
                f"Top 2 instruments contribute {top2_pct:.1f}% of PnL"
            )
        if concentration_warnings:
            concentration_warning = "; ".join(concentration_warnings)

    # By score bucket (mirrors SIM-14 buckets, unscaled — kept for backward compatibility)
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

    def _score_bucket_scaled(score: Optional[Decimal], scale: float) -> str:
        """V6-CAL-08: Score bucket with scaled thresholds.

        At scale=0.65 (with floor): strong >= 9.75, buy >= 6.5, weak >= 4.55.
        """
        if score is None:
            return "unknown"
        s = float(score)
        strong = 15.0 * scale
        buy = 10.0 * scale
        weak = 7.0 * scale
        if s >= strong:
            return "strong_buy"
        if s >= buy:
            return "buy"
        if s >= weak:
            return "weak_buy"
        if s <= -strong:
            return "strong_sell"
        if s <= -buy:
            return "sell"
        if s <= -weak:
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

    # V6-CAL-08: Scaled score buckets for accurate v6+ reporting
    from src.config import AVAILABLE_WEIGHT_FLOOR
    _bucket_scale = max(_TA_WEIGHT, AVAILABLE_WEIGHT_FLOOR)
    by_score_scaled: dict[str, dict] = {}
    for t in trades:
        bucket = _score_bucket_scaled(t.composite_score, _bucket_scale)
        if bucket not in by_score_scaled:
            by_score_scaled[bucket] = {"trades": 0, "wins": 0, "pnl_usd": 0.0}
        by_score_scaled[bucket]["trades"] += 1
        if t.result == "win":
            by_score_scaled[bucket]["wins"] += 1
        by_score_scaled[bucket]["pnl_usd"] += float(t.pnl_usd or 0)

    # ── SIM-44: Extended metrics ──────────────────────────────────────────────

    # Direction-specific win rates (V6-12: exclude end_of_data)
    long_trades = [t for t in metric_trades if t.direction == "LONG"]
    short_trades = [t for t in metric_trades if t.direction == "SHORT"]
    long_wins = [t for t in long_trades if t.result == "win"]
    short_wins = [t for t in short_trades if t.result == "win"]
    win_rate_long = (len(long_wins) / len(long_trades) * 100) if long_trades else 0.0
    win_rate_short = (len(short_wins) / len(short_trades) * 100) if short_trades else 0.0

    # Duration by result
    win_durations = [t.duration_minutes or 0 for t in wins]
    loss_durations = [t.duration_minutes or 0 for t in losses]
    avg_win_dur = sum(win_durations) / len(win_durations) if win_durations else 0.0
    avg_loss_dur = sum(loss_durations) / len(loss_durations) if loss_durations else 0.0

    # By weekday — CAL2-02: exclude end_of_data trades
    by_weekday: dict[str, dict] = {}
    for t in metric_trades:
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

    # By regime — CAL2-02: exclude end_of_data trades
    by_regime: dict[str, dict] = {}
    for t in metric_trades:
        regime_key = getattr(t, "regime", None) or "UNKNOWN"
        if regime_key not in by_regime:
            by_regime[regime_key] = {"trades": 0, "wins": 0, "pnl_usd": 0.0}
        by_regime[regime_key]["trades"] += 1
        if t.result == "win":
            by_regime[regime_key]["wins"] += 1
        by_regime[regime_key]["pnl_usd"] += float(t.pnl_usd or 0)

    # CAL3-06: by_adjustment — breakdown of crypto trades with F&G and FR adjustments.
    # Groups trades into 4 buckets based on which adjustments were applied.
    by_adjustment: dict[str, dict] = {
        "none": {"trades": 0, "wins": 0, "pnl_usd": 0.0},
        "fg_only": {"trades": 0, "wins": 0, "pnl_usd": 0.0},
        "fr_only": {"trades": 0, "wins": 0, "pnl_usd": 0.0},
        "fg_and_fr": {"trades": 0, "wins": 0, "pnl_usd": 0.0},
    }
    for t in metric_trades:
        has_fg = getattr(t, "fg_adjustment", None) is not None
        has_fr = getattr(t, "fr_adjustment", None) is not None
        if has_fg and has_fr:
            bucket = "fg_and_fr"
        elif has_fg:
            bucket = "fg_only"
        elif has_fr:
            bucket = "fr_only"
        else:
            bucket = "none"
        by_adjustment[bucket]["trades"] += 1
        if t.result == "win":
            by_adjustment[bucket]["wins"] += 1
        by_adjustment[bucket]["pnl_usd"] += float(t.pnl_usd or 0)

    # Exit reason counts
    sl_hit_count = sum(1 for t in trades if t.exit_reason == "sl_hit")
    tp_hit_count = sum(1 for t in trades if t.exit_reason == "tp_hit")
    mae_exit_count = sum(1 for t in trades if t.exit_reason == "mae_exit")
    time_exit_count = sum(1 for t in trades if t.exit_reason == "time_exit")
    # CAL2-06: trailing stop count
    trailing_stop_count = sum(1 for t in trades if t.exit_reason == "trailing_stop")

    # V6-CAL-07: MAE as percentage of SL distance (not raw price units).
    # mae / sl_distance * 100 gives real % — how far price moved against position
    # relative to the SL level.
    mae_pct_values: list[float] = []
    mae_pct_values_winners: list[float] = []
    mae_pct_values_losers: list[float] = []
    for t in trades:
        if t.mae is None or t.mae <= 0:
            continue
        sl_dist = None
        sl_field = getattr(t, "sl_price", None)
        if sl_field is not None:
            sl_dist = abs(float(t.entry_price) - float(sl_field))
        if sl_dist is None or sl_dist == 0:
            continue
        pct = float(t.mae) / sl_dist * 100
        mae_pct_values.append(pct)
        if t.result == "win":
            mae_pct_values_winners.append(pct)
        else:
            mae_pct_values_losers.append(pct)

    avg_mae_pct_of_sl = (
        sum(mae_pct_values) / len(mae_pct_values) if mae_pct_values else 0.0
    )
    avg_mae_pct_of_sl_winners = (
        sum(mae_pct_values_winners) / len(mae_pct_values_winners)
        if mae_pct_values_winners else 0.0
    )
    avg_mae_pct_of_sl_losers = (
        sum(mae_pct_values_losers) / len(mae_pct_values_losers)
        if mae_pct_values_losers else 0.0
    )

    # V6 TASK-V6-01: data_hash — deterministic fingerprint of the trade set
    _trade_dicts_for_hash = [
        {
            "symbol": t.symbol,
            "direction": t.direction,
            "entry_price": str(t.entry_price),
            "exit_price": str(t.exit_price),
            "pnl_usd": str(t.pnl_usd),
            "entry_at": t.entry_at.isoformat() if t.entry_at else None,
            "exit_at": t.exit_at.isoformat() if t.exit_at else None,
            "exit_reason": t.exit_reason,
        }
        for t in sorted(trades, key=lambda x: (x.entry_at or datetime.datetime.min, x.symbol))
    ]
    _trade_data_str = json.dumps(_trade_dicts_for_hash, sort_keys=True, default=str)
    data_hash = hashlib.sha256(_trade_data_str.encode()).hexdigest()[:16]

    result: dict[str, Any] = {
        "total_trades": total,
        # V6 TASK-V6-12: primary metrics exclude end_of_data trades
        "total_trades_excl_eod": metric_total,
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        # CAL2-01: total_pnl_usd excludes end_of_data (real metric)
        "total_pnl_usd": round(total_pnl, 4),
        # CAL2-01: full PnL including end_of_data for reference
        "total_pnl_usd_incl_eod": round(total_pnl_incl_eod, 4),
        # CAL2-01: warning flag when end_of_data > 5% of total trades
        "eod_warning": eod_warning,
        "max_drawdown_pct": round(max_dd, 4),
        "avg_duration_minutes": round(avg_dur, 1),
        "long_count": long_count,
        "short_count": short_count,
        "equity_curve": equity_curve,
        "monthly_returns": sorted(monthly.values(), key=lambda x: x["month"]),
        "by_symbol": by_symbol,
        "by_score_bucket": by_score,
        # V6-CAL-08: Scaled score buckets for accurate v6+ reporting
        "by_score_bucket_scaled": by_score_scaled,
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
        # CAL2-06: trailing stop exits
        "trailing_stop_count": trailing_stop_count,
        # V6-CAL-07: MAE as % of SL distance (fixed from raw price units)
        "avg_mae_pct_of_sl": round(avg_mae_pct_of_sl, 2),
        "avg_mae_pct_of_sl_winners": round(avg_mae_pct_of_sl_winners, 2),
        "avg_mae_pct_of_sl_losers": round(avg_mae_pct_of_sl_losers, 2),
        # V6 TASK-V6-01: data integrity hash
        "data_hash": data_hash,
        # V6 TASK-V6-12: end_of_data trades reported separately
        "end_of_data_count": len(eod_trades),
        "end_of_data_pnl": round(sum(float(t.pnl_usd or 0) for t in eod_trades), 4),
        # CAL2-09: concentration risk warning
        "concentration_warning": concentration_warning,
        # CAL3-06: F&G and funding rate adjustment breakdown
        "by_adjustment": by_adjustment,
    }

    # V6 TASK-V6-10: merge filter diagnostics into summary if provided
    if filter_stats is not None:
        result["filter_stats"] = filter_stats

    # CAL3-05: Viability assessment — automatic pass/fail check for key metrics.
    blocking_factors: list[str] = []
    pf_viable = profit_factor is not None and float(profit_factor) >= 1.3
    if not pf_viable:
        blocking_factors.append("pf_below_1.3")
    wr_viable = win_rate >= 25.0
    if not wr_viable:
        blocking_factors.append("wr_below_25pct")
    dd_viable = max_dd <= 25.0
    if not dd_viable:
        blocking_factors.append("dd_above_25pct")
    # Concentration: no single instrument > 40% of total PnL
    concentration_viable = True
    if by_symbol and total_pnl > 0:
        max_sym_pct = max(d["pnl_usd"] / total_pnl * 100 for d in by_symbol.values())
        if max_sym_pct > 40:
            concentration_viable = False
            blocking_factors.append("concentration_risk")
    overall_viability = (
        "VIABLE"
        if all([pf_viable, wr_viable, dd_viable, concentration_viable])
        else "NOT_VIABLE"
    )
    result["viability_assessment"] = {
        "pf_viable": pf_viable,
        "wr_viable": wr_viable,
        "dd_viable": dd_viable,
        "concentration_viable": concentration_viable,
        "overall": overall_viability,
        "blocking_factors": blocking_factors,
    }

    return result


def _compute_path_dependence(
    isolation_per_symbol: dict[str, dict],
    combined_by_symbol: dict[str, dict],
    threshold_pct: float = 30.0,
) -> dict[str, Any]:
    """TASK-V7-18: Compare per-symbol PnL between isolation mode and combined mode.

    A symbol is flagged as "path-dependent" when the absolute PnL difference
    exceeds threshold_pct (default 30%) of the isolation PnL.

    Returns a dict with:
      - path_dependent: list[str] — symbols with PnL diff > threshold_pct
      - comparison: dict[symbol] -> {isolated_pnl, combined_pnl, diff_pct}
    """
    path_dependent: list[str] = []
    comparison: dict[str, dict] = {}

    all_symbols = set(isolation_per_symbol.keys()) | set(combined_by_symbol.keys())
    for symbol in all_symbols:
        isolated_pnl = (isolation_per_symbol.get(symbol) or {}).get("total_pnl_usd", 0.0)
        combined_pnl = (combined_by_symbol.get(symbol) or {}).get("pnl_usd", 0.0)

        if isolated_pnl is None:
            isolated_pnl = 0.0
        if combined_pnl is None:
            combined_pnl = 0.0

        isolated_pnl = float(isolated_pnl)
        combined_pnl = float(combined_pnl)

        if abs(isolated_pnl) > 0:
            diff_pct = abs(isolated_pnl - combined_pnl) / abs(isolated_pnl) * 100
        elif abs(combined_pnl) > 0:
            diff_pct = 100.0  # isolated is zero, combined is not — maximum difference
        else:
            diff_pct = 0.0

        comparison[symbol] = {
            "isolated_pnl": round(isolated_pnl, 4),
            "combined_pnl": round(combined_pnl, 4),
            "diff_pct": round(diff_pct, 2),
        }

        if diff_pct > threshold_pct:
            path_dependent.append(symbol)
            logger.info(
                "[V7-18] Path-dependent instrument: %s — isolated=%.4f combined=%.4f diff=%.1f%%",
                symbol, isolated_pnl, combined_pnl, diff_pct,
            )

    return {
        "path_dependent": sorted(path_dependent),
        "comparison": comparison,
        "threshold_pct": threshold_pct,
    }


def _nan_to_none(val: float) -> Optional[float]:
    """Convert NaN to None, keep valid floats."""
    if val is None:
        return None
    try:
        if math.isnan(val):
            return None
    except (TypeError, ValueError):
        return None
    return float(val)


def _precompute_ta_scores(
    ta_arrays: dict[str, np.ndarray],
    n: int,
    timeframe: str = "H1",
) -> np.ndarray:
    """Compute ta_score at each candle index from pre-computed indicator arrays.

    Replicates TAEngine.generate_ta_signals() + calculate_ta_score() logic
    for every index in [0, n). Uses indicator values at each index (causal).

    S/R signal component is set to 0 (bounded impact: max 5 points).
    Candle patterns component is set to 0 (requires TA-Lib array pre-computation
    which is handled separately; impact is only 5%).

    Returns np.ndarray of shape (n,) with ta_score values.
    NaN for indices where indicators are not yet available (warmup period).
    """
    from src.analysis.ta_engine import TA_WEIGHTS, TF_INDICATOR_PERIODS

    periods = TF_INDICATOR_PERIODS.get(timeframe, TF_INDICATOR_PERIODS["_default"])

    rsi_arr = ta_arrays.get("rsi")
    macd_arr = ta_arrays.get("macd")
    macd_sig_arr = ta_arrays.get("macd_signal")
    macd_hist_arr = ta_arrays.get("macd_hist")
    bb_upper_arr = ta_arrays.get("bb_upper")
    bb_middle_arr = ta_arrays.get("bb_middle")
    bb_lower_arr = ta_arrays.get("bb_lower")
    sma_fast_arr = ta_arrays.get("sma_fast")
    sma_slow_arr = ta_arrays.get("sma_slow")
    sma_long_arr = ta_arrays.get("sma_long")
    ema_fast_arr = ta_arrays.get("ema_fast")
    ema_slow_arr = ta_arrays.get("ema_slow")
    adx_arr = ta_arrays.get("adx")
    plus_di_arr = ta_arrays.get("plus_di")
    minus_di_arr = ta_arrays.get("minus_di")
    stoch_k_arr = ta_arrays.get("stoch_k")
    stoch_d_arr = ta_arrays.get("stoch_d")
    close_arr = ta_arrays.get("close")
    volume_arr = ta_arrays.get("volume")

    scores = np.full(n, np.nan)

    for i in range(n):
        rsi = _nan_to_none(rsi_arr[i]) if rsi_arr is not None else None
        macd_v = _nan_to_none(macd_arr[i]) if macd_arr is not None else None
        macd_s = _nan_to_none(macd_sig_arr[i]) if macd_sig_arr is not None else None
        macd_h = _nan_to_none(macd_hist_arr[i]) if macd_hist_arr is not None else None
        bb_upper = _nan_to_none(bb_upper_arr[i]) if bb_upper_arr is not None else None
        bb_middle = _nan_to_none(bb_middle_arr[i]) if bb_middle_arr is not None else None
        bb_lower = _nan_to_none(bb_lower_arr[i]) if bb_lower_arr is not None else None
        sma_fast = _nan_to_none(sma_fast_arr[i]) if sma_fast_arr is not None else None
        sma_slow = _nan_to_none(sma_slow_arr[i]) if sma_slow_arr is not None else None
        sma_long_v = _nan_to_none(sma_long_arr[i]) if sma_long_arr is not None else None
        ema_fast = _nan_to_none(ema_fast_arr[i]) if ema_fast_arr is not None else None
        ema_slow = _nan_to_none(ema_slow_arr[i]) if ema_slow_arr is not None else None
        adx = _nan_to_none(adx_arr[i]) if adx_arr is not None else None
        plus_di = _nan_to_none(plus_di_arr[i]) if plus_di_arr is not None else None
        minus_di = _nan_to_none(minus_di_arr[i]) if minus_di_arr is not None else None
        stoch_k = _nan_to_none(stoch_k_arr[i]) if stoch_k_arr is not None else None
        stoch_d = _nan_to_none(stoch_d_arr[i]) if stoch_d_arr is not None else None
        close = float(close_arr[i]) if close_arr is not None else 0.0
        vol = float(volume_arr[i]) if volume_arr is not None else 0.0

        # ── RSI signal (mirrors TAEngine._rsi_signal) ──────────────────────
        if rsi is not None:
            trending = adx is not None and adx >= 25
            if not trending:
                if rsi < 30:
                    rsi_sig = {"signal": 1, "strength": (30 - rsi) / 30}
                elif rsi > 70:
                    rsi_sig = {"signal": -1, "strength": (rsi - 70) / 30}
                else:
                    rsi_sig = {"signal": 0, "strength": 0.0}
            else:
                if 40 <= rsi <= 55:
                    rsi_sig = {"signal": 1, "strength": (55 - rsi) / 15}
                elif 45 <= rsi <= 60:
                    rsi_sig = {"signal": -1, "strength": (rsi - 45) / 15}
                elif rsi < 30:
                    rsi_sig = {"signal": 1, "strength": (30 - rsi) / 30 * 0.4}
                elif rsi > 70:
                    rsi_sig = {"signal": -1, "strength": (rsi - 70) / 30 * 0.4}
                else:
                    rsi_sig = {"signal": 0, "strength": 0.0}
        else:
            rsi_sig = {"signal": 0, "strength": 0.0}

        # ── MACD signal ────────────────────────────────────────────────────
        if macd_v is not None and macd_s is not None and macd_h is not None:
            if macd_v > macd_s and macd_h > 0:
                macd_sig_d = {"signal": 1, "strength": min(abs(macd_h) / max(abs(macd_v), 1e-10), 1.0)}
            elif macd_v < macd_s and macd_h < 0:
                macd_sig_d = {"signal": -1, "strength": min(abs(macd_h) / max(abs(macd_v), 1e-10), 1.0)}
            else:
                macd_sig_d = {"signal": 0, "strength": 0.0}
        else:
            macd_sig_d = {"signal": 0, "strength": 0.0}

        # ── Bollinger Bands signal ─────────────────────────────────────────
        if bb_upper and bb_lower and bb_middle:
            bb_range = bb_upper - bb_lower
            if bb_range > 0:
                position = (close - bb_lower) / bb_range
                if close < bb_lower:
                    bb_sig = {"signal": 1, "strength": min((bb_lower - close) / bb_range, 1.0)}
                elif close > bb_upper:
                    bb_sig = {"signal": -1, "strength": min((close - bb_upper) / bb_range, 1.0)}
                else:
                    if position < 0.3:
                        bb_sig = {"signal": 1, "strength": (0.3 - position) / 0.3}
                    elif position > 0.7:
                        bb_sig = {"signal": -1, "strength": (position - 0.7) / 0.3}
                    else:
                        bb_sig = {"signal": 0, "strength": 0.0}
            else:
                bb_sig = {"signal": 0, "strength": 0.0}
        else:
            bb_sig = {"signal": 0, "strength": 0.0}

        # ── MA Cross signal ────────────────────────────────────────────────
        ma_score = 0.0
        ma_count = 0.0
        if sma_fast is not None and sma_slow is not None:
            if close > sma_fast > sma_slow:
                ma_score += 1
            elif close < sma_fast < sma_slow:
                ma_score -= 1
            ma_count += 1
        if sma_long_v is not None:
            if close > sma_long_v:
                ma_score += 0.5
            else:
                ma_score -= 0.5
            ma_count += 0.5
        if ema_fast is not None and ema_slow is not None:
            if ema_fast > ema_slow:
                ma_score += 1
            else:
                ma_score -= 1
            ma_count += 1
        if ma_count > 0:
            norm = ma_score / ma_count
            ma_sig = {
                "signal": 1 if norm > 0.3 else (-1 if norm < -0.3 else 0),
                "strength": min(abs(norm), 1.0),
            }
        else:
            ma_sig = {"signal": 0, "strength": 0.0}

        # ── ADX signal ────────────────────────────────────────────────────
        if adx is not None and plus_di is not None and minus_di is not None:
            if adx >= 20 and plus_di != minus_di:
                if plus_di > minus_di:
                    adx_sig = {"signal": 1, "strength": min(adx / 100, 1.0)}
                else:
                    adx_sig = {"signal": -1, "strength": min(adx / 100, 1.0)}
            else:
                adx_sig = {"signal": 0, "strength": adx / 100}
        else:
            adx_sig = {"signal": 0, "strength": 0.0}

        # ── Stochastic signal ─────────────────────────────────────────────
        if stoch_k is not None and stoch_d is not None:
            if stoch_k < 20:
                base_strength = (20 - stoch_k) / 20
                boost = 1.2 if stoch_k > stoch_d else 1.0
                stoch_sig = {"signal": 1, "strength": min(base_strength * boost, 1.0)}
            elif stoch_k > 80:
                base_strength = (stoch_k - 80) / 20
                boost = 1.2 if stoch_k < stoch_d else 1.0
                stoch_sig = {"signal": -1, "strength": min(base_strength * boost, 1.0)}
            else:
                stoch_sig = {"signal": 0, "strength": 0.0}
        else:
            stoch_sig = {"signal": 0, "strength": 0.0}

        # ── Volume signal ─────────────────────────────────────────────────
        # Use rolling mean of last 20 bars up to index i
        if volume_arr is not None and i >= 1:
            start_vol = max(0, i - 19)
            avg_vol_20 = float(np.mean(volume_arr[start_vol:i + 1]))
            if avg_vol_20 > 0:
                vol_ratio = vol / avg_vol_20
                if vol_ratio > 1.5:
                    price_dir = 0
                    if sma_fast is not None and sma_fast != 0:
                        price_dir = 1 if close > sma_fast else -1
                    vol_sig = {"signal": price_dir, "strength": min((vol_ratio - 1) / 2, 1.0)}
                else:
                    vol_sig = {"signal": 0, "strength": vol_ratio / 1.5}
            else:
                vol_sig = {"signal": 0, "strength": 0.0}
        else:
            vol_sig = {"signal": 0, "strength": 0.0}

        # ── S/R signal: set to 0 (position-dependent, 5% weight) ──────────
        sr_sig = {"signal": 0, "strength": 0.0}

        # ── Candle patterns: set to 0 (computed separately, 5% weight) ────
        candle_sig = {"signal": 0, "strength": 0.0}

        # ── Weighted sum (mirrors TAEngine.calculate_ta_score) ─────────────
        components = {
            "macd": macd_sig_d,
            "rsi": rsi_sig,
            "bollinger": bb_sig,
            "ma_cross": ma_sig,
            "adx": adx_sig,
            "stochastic": stoch_sig,
            "volume": vol_sig,
            "support_resistance": sr_sig,
            "candle_patterns": candle_sig,
        }
        total = 0.0
        for key, weight in TA_WEIGHTS.items():
            sig_d = components.get(key, {"signal": 0, "strength": 0.0})
            total += sig_d["signal"] * sig_d["strength"] * weight * 100

        scores[i] = max(-100.0, min(100.0, total))

    return scores


def _precompute_regimes(
    adx_array: np.ndarray,
    atr_array: np.ndarray,
    close_array: np.ndarray,
    sma200_array: np.ndarray,
    n: int,
) -> list[str]:
    """Compute regime string at each candle index using pre-computed arrays.

    Uses rolling ATR percentile (window=252) to classify volatility regimes.
    Maps raw regime names to the same keys used by REGIME_RR_MAP / ATR_SL_MULTIPLIER_MAP.

    Returns list of length n with regime strings.
    """
    from src.analysis.regime_detector import classify_regime_at_point

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

    # Pre-compute rolling ATR percentile using pandas rolling apply
    atr_series = pd.Series(atr_array)
    # For each position, find percentile rank within rolling window of up to 252 bars
    atr_pct_series = atr_series.rolling(252, min_periods=2).apply(
        lambda w: (w[:-1] < w.iloc[-1]).sum() / (len(w) - 1) * 100.0
        if len(w) >= 2 else 50.0,
        raw=False,
    )
    atr_pct_array = atr_pct_series.values

    regimes: list[str] = []
    for i in range(n):
        adx_v = _nan_to_none(adx_array[i]) if adx_array is not None else None
        atr_pct_v = _nan_to_none(atr_pct_array[i])
        close_v = float(close_array[i])
        sma200_v = float(sma200_array[i]) if sma200_array is not None else float("nan")

        raw_regime = classify_regime_at_point(
            adx=adx_v,
            atr_pct=atr_pct_v,
            close=close_v,
            sma200=sma200_v,
        )
        regimes.append(_MAP.get(raw_regime, "DEFAULT"))

    return regimes


def _to_utc(ts: datetime.datetime) -> datetime.datetime:
    """Normalize a datetime to UTC-aware, regardless of whether it is naive or aware."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=datetime.timezone.utc)
    return ts.astimezone(datetime.timezone.utc)


def _compute_dxy_rsi(dxy_rows: list) -> dict:
    """CAL3-01: Compute RSI(14) over DXY H1 price rows using Wilder smoothing.

    Returns a dict mapping each candle's UTC-aware timestamp → RSI value.
    All keys are normalized to UTC-aware datetimes so lookup works regardless of
    whether the trading-symbol price rows carry tzinfo or not.
    Only includes timestamps where RSI is defined (from index 14 onward).

    Wilder smoothing: initial avg_gain/avg_loss = simple mean of first 14 changes,
    then each subsequent: avg = (prev_avg * 13 + current) / 14.
    """
    if len(dxy_rows) < 15:
        return {}

    closes = [float(r.close) for r in dxy_rows]
    timestamps = [_to_utc(r.timestamp) for r in dxy_rows]
    rsi_period = 14
    result: dict = {}

    # Compute price changes
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    # Seed: simple mean of first 14 changes
    gains = [max(c, 0.0) for c in changes[:rsi_period]]
    losses = [abs(min(c, 0.0)) for c in changes[:rsi_period]]
    avg_gain = sum(gains) / rsi_period
    avg_loss = sum(losses) / rsi_period

    # RSI at index rsi_period (corresponds to closes[rsi_period])
    def _rsi_from_avg(ag: float, al: float) -> float:
        if al == 0.0:
            return 100.0
        rs = ag / al
        return 100.0 - (100.0 / (1.0 + rs))

    result[timestamps[rsi_period]] = _rsi_from_avg(avg_gain, avg_loss)

    # Wilder smoothing for remaining candles
    for j in range(rsi_period, len(changes)):
        change = changes[j]
        gain = max(change, 0.0)
        loss = abs(min(change, 0.0))
        avg_gain = (avg_gain * (rsi_period - 1) + gain) / rsi_period
        avg_loss = (avg_loss * (rsi_period - 1) + loss) / rsi_period
        result[timestamps[j + 1]] = _rsi_from_avg(avg_gain, avg_loss)

    return result


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
            # TASK-V7-18: Isolation mode runs each symbol independently
            if params.isolation_mode:
                trades, filter_stats, isolation_results = await self._simulate_isolated(
                    params, run_id=run_id
                )
                summary = _compute_summary(trades, params.account_size, filter_stats=filter_stats)
                # Compute path-dependence: compare isolated per-symbol PnL
                # vs combined by_symbol PnL (same trades, different grouping perspective).
                # In this engine, trades are already independent per symbol so diff should be ~0,
                # but the comparison is still useful for detecting unexpected dependencies.
                path_dep = _compute_path_dependence(
                    isolation_per_symbol=isolation_results.get("per_instrument", {}),
                    combined_by_symbol=summary.get("by_symbol", {}),
                )
                isolation_results["path_dependence"] = path_dep
                summary["isolation_results"] = isolation_results
                logger.info(
                    "[V7-18] Path-dependent instruments: %s",
                    path_dep.get("path_dependent", []),
                )
            else:
                trades, filter_stats = await self._simulate(params)
                summary = _compute_summary(trades, params.account_size, filter_stats=filter_stats)

            # V6 TASK-V6-13: Walk-forward validation — run IS and OOS separately
            if params.enable_walk_forward:
                from dateutil.relativedelta import relativedelta

                start_dt = datetime.datetime.fromisoformat(params.start_date)
                is_end_dt = start_dt + relativedelta(months=params.in_sample_months)
                oos_end_dt = is_end_dt + relativedelta(months=params.out_of_sample_months)

                is_params = params.model_copy(update={
                    "start_date": params.start_date,
                    "end_date": is_end_dt.date().isoformat(),
                    "enable_walk_forward": False,
                })
                oos_params = params.model_copy(update={
                    "start_date": is_end_dt.date().isoformat(),
                    "end_date": min(
                        oos_end_dt.date().isoformat(),
                        params.end_date,
                    ),
                    "enable_walk_forward": False,
                })

                is_trades, is_filter_stats = await self._simulate(is_params)
                oos_trades, oos_filter_stats = await self._simulate(oos_params)

                is_summary = _compute_summary(is_trades, params.account_size, filter_stats=is_filter_stats)
                oos_summary = _compute_summary(oos_trades, params.account_size, filter_stats=oos_filter_stats)

                is_wr = is_summary.get("win_rate_pct", 0.0)
                oos_wr = oos_summary.get("win_rate_pct", 0.0)
                is_pf = is_summary.get("profit_factor") or 0.0
                oos_pf = oos_summary.get("profit_factor") or 0.0

                summary["walk_forward"] = {
                    "in_sample_period": f"{params.start_date} – {is_end_dt.date().isoformat()}",
                    "out_of_sample_period": f"{is_end_dt.date().isoformat()} – {oos_end_dt.date().isoformat()}",
                    "in_sample": {
                        "total_trades": is_summary["total_trades"],
                        "win_rate_pct": is_wr,
                        "profit_factor": is_pf,
                        "total_pnl_usd": is_summary["total_pnl_usd"],
                    },
                    "out_of_sample": {
                        "total_trades": oos_summary["total_trades"],
                        "win_rate_pct": oos_wr,
                        "profit_factor": oos_pf,
                        "total_pnl_usd": oos_summary["total_pnl_usd"],
                    },
                    "wr_delta": round(oos_wr - is_wr, 2),
                    "pf_delta": round(float(oos_pf) - float(is_pf), 4),
                }
                logger.info(
                    "[V6-13] Walk-forward: IS WR=%.1f%% PF=%s → OOS WR=%.1f%% PF=%s",
                    is_wr, is_pf, oos_wr, oos_pf,
                )

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
                    # V6 TASK-V6-05: persist regime so by_regime summary has real names
                    "regime": t.regime,
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
        run_id: Optional[str] = None,
    ) -> tuple[list[BacktestTradeResult], dict]:
        """Core simulation loop. Returns (trades, aggregated_filter_stats).

        If run_id is provided, updates _backtest_progress[run_id] (0–100)
        after each symbol completes so callers can poll progress.
        """
        trades: list[BacktestTradeResult] = []
        # V6 TASK-V6-10: aggregate filter stats across all symbols
        agg_filter_stats: dict[str, int] = {}

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

        # R5: Instrument whitelist — restrict backtest to proven performers.
        # Placed before D1 and DXY preloads so we don't waste DB queries on
        # instruments that will be skipped. Does NOT affect live SignalEngine
        # (filter_pipeline.check_blocked_instrument handles live+backtest blocking;
        # this is backtest-only scope restriction).
        from src.config import BACKTEST_INSTRUMENT_WHITELIST
        if BACKTEST_INSTRUMENT_WHITELIST:
            original_count = len(params.symbols)
            symbols_to_run = [s for s in params.symbols if s in BACKTEST_INSTRUMENT_WHITELIST]
            if len(symbols_to_run) < original_count:
                filtered_out = [s for s in params.symbols if s not in BACKTEST_INSTRUMENT_WHITELIST]
                logger.info(
                    "[R5] Whitelist active: %d/%d symbols will run (%s). "
                    "Filtered out: %s",
                    len(symbols_to_run), original_count, symbols_to_run, filtered_out,
                )
        else:
            symbols_to_run = params.symbols

        # V6 TASK-V6-06: Pre-load D1 data per symbol for MA200 trend filter.
        # 300 extra days of history before start_dt ensure MA200 is warmed up from day 1.
        # Iterates symbols_to_run (post-whitelist) to skip instruments that won't be simulated.
        _D1_WARMUP_DAYS = 300
        d1_data_cache: dict[str, list] = {}
        if params.apply_d1_trend_filter:
            d1_start = start_dt - datetime.timedelta(days=_D1_WARMUP_DAYS)
            for symbol in symbols_to_run:
                try:
                    instrument_for_d1 = await get_instrument_by_symbol(self.db, symbol)
                    if instrument_for_d1 is not None:
                        d1_rows = await get_price_data(
                            self.db,
                            instrument_for_d1.id,
                            "D1",
                            from_dt=d1_start,
                            to_dt=end_dt,
                            limit=10_000,
                        )
                        d1_data_cache[symbol] = d1_rows
                        logger.info(
                            "[V6-D1] D1 data for %s: %d rows (MA200 filter %s)",
                            symbol, len(d1_rows),
                            "active" if len(d1_rows) >= 200 else "will degrade (insufficient rows)",
                        )
                except Exception as exc:
                    logger.warning("[V6-D1] Could not load D1 data for %s: %s", symbol, exc)

        # CAL3-01: Pre-load DXY H1 data and compute RSI(14) once for the whole period.
        # DXY RSI is used by SIM-38 to filter forex LONG/SHORT during strong/weak dollar.
        # Graceful degradation: if DXY instrument not found → dxy_rsi_by_ts stays empty.
        dxy_rsi_by_ts: dict[datetime.datetime, float] = {}
        _DXY_SYMBOLS = ["DX-Y.NYB", "DXY", "DX=F"]
        _dxy_h1_warmup_days = 30  # need 14+ candles for RSI(14)
        dxy_start = start_dt - datetime.timedelta(days=_dxy_h1_warmup_days)
        for _dxy_sym in _DXY_SYMBOLS:
            try:
                dxy_instrument = await get_instrument_by_symbol(self.db, _dxy_sym)
                if dxy_instrument is not None:
                    dxy_rows = await get_price_data(
                        self.db,
                        dxy_instrument.id,
                        "H1",
                        from_dt=dxy_start,
                        to_dt=end_dt,
                        limit=100_000,
                    )
                    if len(dxy_rows) >= 14:
                        dxy_rsi_by_ts = _compute_dxy_rsi(dxy_rows)
                        logger.info(
                            "[CAL3-01] DXY RSI loaded from %s: %d candles → %d RSI values",
                            _dxy_sym, len(dxy_rows), len(dxy_rsi_by_ts),
                        )
                    else:
                        logger.warning(
                            "[CAL3-01] DXY %s: only %d candles (need 14+) — SIM-38 will degrade",
                            _dxy_sym, len(dxy_rows),
                        )
                    break  # found instrument — stop trying other symbols
            except Exception as exc:
                logger.warning("[CAL3-01] Could not load DXY data for %s: %s", _dxy_sym, exc)
        if not dxy_rsi_by_ts:
            logger.warning(
                "[CAL3-01] DXY data not available (tried %s) — SIM-38 filter will degrade gracefully",
                _DXY_SYMBOLS,
            )

        total_symbols = len(symbols_to_run)
        if run_id:
            _backtest_progress[run_id] = 0.0

        for sym_idx, symbol in enumerate(symbols_to_run):
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

            symbol_trades, symbol_filter_stats = await asyncio.to_thread(
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
                d1_rows_all=d1_data_cache.get(symbol, []),
                dxy_rsi_by_ts=dxy_rsi_by_ts,
            )
            trades.extend(symbol_trades)
            # V6 TASK-V6-10: accumulate filter stats across symbols
            for k, v in symbol_filter_stats.items():
                agg_filter_stats[k] = agg_filter_stats.get(k, 0) + v

            if run_id:
                _backtest_progress[run_id] = round((sym_idx + 1) / total_symbols * 100, 1)
                logger.info("[SIM-22] %s done — progress %.1f%%", symbol, _backtest_progress[run_id])

        return trades, agg_filter_stats

    async def _simulate_isolated(
        self,
        params: BacktestParams,
        run_id: Optional[str] = None,
    ) -> tuple[list[BacktestTradeResult], dict, dict[str, Any]]:
        """TASK-V7-18: Isolation mode simulation.

        Runs each symbol in a completely independent loop with no shared state:
        - No correlation guard between symbols
        - No position cooldowns shared across instruments
        - Each symbol gets full capital allocation (position_pct unaffected by others)

        Returns (all_trades, aggregated_filter_stats, isolation_results) where
        isolation_results contains per-instrument summaries and path-dependence flags.
        """
        # Collect all trades (combined) and per-symbol data for comparison
        all_trades: list[BacktestTradeResult] = []
        agg_filter_stats: dict[str, int] = {}
        per_symbol_summaries: dict[str, Any] = {}

        start_dt = datetime.datetime.fromisoformat(params.start_date).replace(
            tzinfo=datetime.timezone.utc
        )
        end_dt = datetime.datetime.fromisoformat(params.end_date).replace(
            hour=23, minute=59, second=59, tzinfo=datetime.timezone.utc
        )

        # Pre-load shared data (economic events, D1 cache, DXY RSI)
        # reuse the same helpers as _simulate to avoid duplication
        economic_events: list = []
        try:
            from src.database.crud import get_economic_events_in_range
            economic_events = await get_economic_events_in_range(self.db, start_dt, end_dt)
        except Exception as exc:
            logger.warning("[V7-18] Could not load economic events: %s", exc)

        from src.config import BACKTEST_INSTRUMENT_WHITELIST
        if BACKTEST_INSTRUMENT_WHITELIST:
            symbols_to_run = [s for s in params.symbols if s in BACKTEST_INSTRUMENT_WHITELIST]
        else:
            symbols_to_run = params.symbols

        _D1_WARMUP_DAYS = 300
        d1_data_cache: dict[str, list] = {}
        if params.apply_d1_trend_filter:
            d1_start = start_dt - datetime.timedelta(days=_D1_WARMUP_DAYS)
            for symbol in symbols_to_run:
                try:
                    instr = await get_instrument_by_symbol(self.db, symbol)
                    if instr is not None:
                        d1_rows = await get_price_data(
                            self.db, instr.id, "D1",
                            from_dt=d1_start, to_dt=end_dt, limit=10_000,
                        )
                        d1_data_cache[symbol] = d1_rows
                except Exception as exc:
                    logger.warning("[V7-18] Could not load D1 data for %s: %s", symbol, exc)

        dxy_rsi_by_ts: dict[datetime.datetime, float] = {}
        _DXY_SYMBOLS = ["DX-Y.NYB", "DXY", "DX=F"]
        dxy_start = start_dt - datetime.timedelta(days=30)
        for _dxy_sym in _DXY_SYMBOLS:
            try:
                dxy_instrument = await get_instrument_by_symbol(self.db, _dxy_sym)
                if dxy_instrument is not None:
                    dxy_rows = await get_price_data(
                        self.db, dxy_instrument.id, "H1",
                        from_dt=dxy_start, to_dt=end_dt, limit=100_000,
                    )
                    if len(dxy_rows) >= 14:
                        dxy_rsi_by_ts = _compute_dxy_rsi(dxy_rows)
                    break
            except Exception as exc:
                logger.warning("[V7-18] Could not load DXY data for %s: %s", _dxy_sym, exc)

        total_symbols = len(symbols_to_run)
        if run_id:
            _backtest_progress[run_id] = 0.0

        # Run each symbol in complete isolation — no shared open_positions state
        for sym_idx, symbol in enumerate(symbols_to_run):
            instrument = await get_instrument_by_symbol(self.db, symbol)
            if instrument is None:
                logger.warning("[V7-18] Instrument %s not found — skipping", symbol)
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
                    "[V7-18] %s: only %d candles — need at least %d, skipping",
                    symbol, len(price_rows), _MIN_BARS_HISTORY + 2,
                )
                if run_id:
                    _backtest_progress[run_id] = round((sym_idx + 1) / total_symbols * 100, 1)
                continue

            sym_base_pct = sym_idx / total_symbols * 100
            sym_share = 100.0 / total_symbols

            def _progress_cb(candle_pct: float) -> None:
                if run_id:
                    _backtest_progress[run_id] = round(
                        sym_base_pct + candle_pct * sym_share / 100, 1
                    )

            # Each symbol runs independently — no shared state passed between runs
            symbol_trades, symbol_filter_stats = await asyncio.to_thread(
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
                d1_rows_all=d1_data_cache.get(symbol, []),
                dxy_rsi_by_ts=dxy_rsi_by_ts,
            )

            all_trades.extend(symbol_trades)
            for k, v in symbol_filter_stats.items():
                agg_filter_stats[k] = agg_filter_stats.get(k, 0) + v

            # Compute isolated per-symbol summary for comparison
            sym_summary = _compute_summary(symbol_trades, params.account_size)
            per_symbol_summaries[symbol] = {
                "total_trades": sym_summary["total_trades"],
                "win_rate_pct": sym_summary["win_rate_pct"],
                "profit_factor": sym_summary["profit_factor"],
                "total_pnl_usd": sym_summary["total_pnl_usd"],
            }

            if run_id:
                _backtest_progress[run_id] = round((sym_idx + 1) / total_symbols * 100, 1)
                logger.info("[V7-18] %s isolated done — progress %.1f%%", symbol, _backtest_progress[run_id])

        isolation_results: dict[str, Any] = {
            "mode": "isolation",
            "per_instrument": per_symbol_summaries,
        }

        logger.info(
            "[V7-18] Isolation mode complete: %d symbols, %d total trades",
            total_symbols, len(all_trades),
        )

        return all_trades, agg_filter_stats, isolation_results

    def _simulate_symbol(
        self,
        symbol: str,
        market_type: str,
        timeframe: str,
        price_rows: list,
        account_size: Decimal,
        apply_slippage: bool,
        progress_cb: Any = None,
        economic_events: Optional[list] = None,
        params: Optional[BacktestParams] = None,
        d1_rows_all: Optional[list] = None,
        dxy_rsi_by_ts: Optional[dict] = None,
    ) -> tuple[list[BacktestTradeResult], dict]:
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

        # ── Pre-computation: build full DataFrame and indicator arrays once ──
        from src.analysis.ta_engine import TAEngine

        full_df = _to_ohlcv_df(price_rows)
        try:
            ta_engine_full = TAEngine(full_df, timeframe=timeframe)
            ta_arrays = ta_engine_full.calculate_all_indicators_arrays()
        except Exception as exc:
            logger.warning(
                "[SIM-22] TAEngine pre-computation failed for %s: %s — falling back to O(n^2)",
                symbol, exc,
            )
            ta_arrays = {}

        # Pre-compute ta_score at every index
        try:
            ta_scores_precomp = _precompute_ta_scores(ta_arrays, n, timeframe)
        except Exception as exc:
            logger.warning(
                "[SIM-22] ta_score pre-computation failed for %s: %s", symbol, exc
            )
            ta_scores_precomp = np.full(n, np.nan)

        # Pre-compute regime at every index
        regimes_precomp: list[str] = []
        try:
            adx_arr = ta_arrays.get("adx", np.full(n, np.nan))
            atr_arr = ta_arrays.get("atr", np.full(n, np.nan))
            close_arr_pre = ta_arrays.get("close", np.zeros(n))
            sma_long_arr = ta_arrays.get("sma_long", np.full(n, np.nan))
            regimes_precomp = _precompute_regimes(adx_arr, atr_arr, close_arr_pre, sma_long_arr, n)
        except Exception as exc:
            logger.warning(
                "[SIM-22] Regime pre-computation failed for %s: %s", symbol, exc
            )
            regimes_precomp = ["DEFAULT"] * n

        # ── OPT-01: Pre-sort D1 timestamps for O(log n) bisect lookup ─────────
        # D1 data is already ORDER BY timestamp from DB — guaranteed sorted.
        # bisect_right on sorted timestamps replaces O(m) list comprehension
        # that was running 12,000 × 500 = 6M comparisons per symbol.
        _d1_all: list = d1_rows_all or []
        _d1_timestamps: list[datetime.datetime] = [r.timestamp for r in _d1_all]

        # ── OPT-02: Pre-map DXY RSI to price_rows indices ────────────────────
        # Replaces per-candle _to_utc() + exact dict.get() which:
        # 1. Allocates a new datetime object 12,000× per symbol
        # 2. Silently returns None when DXY and symbol timestamps don't align exactly
        # bisect nearest-previous lookup is both faster and more correct (causal).
        dxy_rsi_at_idx: list[Optional[float]] = [None] * n
        if dxy_rsi_by_ts:
            _dxy_ts_sorted: list[datetime.datetime] = sorted(dxy_rsi_by_ts.keys())
            _dxy_rsi_sorted: list[float] = [dxy_rsi_by_ts[ts] for ts in _dxy_ts_sorted]
            for _pi in range(n):
                _pts = _to_utc(price_rows[_pi].timestamp)
                _di = bisect.bisect_right(_dxy_ts_sorted, _pts) - 1
                if _di >= 0:
                    dxy_rsi_at_idx[_pi] = _dxy_rsi_sorted[_di]

            # R5-02: Permanent diagnostic logging for DXY RSI mapping.
            # Root cause analysis: overlap between momentum filter (RSI > 50 + MACD) and
            # DXY filter means most EURUSD/GBPUSD LONG signals are already rejected by
            # momentum before reaching DXY filter. DXY provides independent confirmation
            # but rarely fires uniquely. Overlap is by design — not a bug.
            _non_none_count = sum(1 for x in dxy_rsi_at_idx if x is not None)
            _above_55_count = sum(1 for x in dxy_rsi_at_idx if x is not None and x > 55)
            _below_45_count = sum(1 for x in dxy_rsi_at_idx if x is not None and x < 45)
            logger.info(
                "[DXY-DIAG] %s: %d/%d candles mapped to DXY RSI "
                "(>55: %d, <45: %d, first_dxy_ts=%s, last_dxy_ts=%s, "
                "first_price_ts=%s, last_price_ts=%s)",
                symbol, _non_none_count, n,
                _above_55_count, _below_45_count,
                _dxy_ts_sorted[0].isoformat() if _dxy_ts_sorted else "N/A",
                _dxy_ts_sorted[-1].isoformat() if _dxy_ts_sorted else "N/A",
                _to_utc(price_rows[0].timestamp).isoformat(),
                _to_utc(price_rows[-1].timestamp).isoformat(),
            )

        # ── OPT-03: S/R cache — refresh every 50 candles instead of per-signal
        # TAEngine.calculate_all_indicators() recomputes RSI/MACD/BB/MA/ADX/etc.
        # just to extract support/resistance. S/R levels are slow-moving (change
        # over trading days, not hourly), so caching for 50 H1 candles (~2 days)
        # reduces ~3,000-5,000 full TAEngine calls to ~60-100 per symbol.
        _sr_cache: dict[str, Any] = {
            "support": [],
            "resistance": [],
            "last_idx": -_SR_CACHE_INTERVAL,  # force computation on first signal
        }

        for i in range(_MIN_BARS_HISTORY, n - 1):
            # ── Progress callback every N candles ─────────────────────────────
            if progress_cb is not None and (i - _MIN_BARS_HISTORY) % _progress_interval == 0:
                progress_cb((i - _MIN_BARS_HISTORY) / max(n - _MIN_BARS_HISTORY - 1, 1) * 100)

            current_candle = price_rows[i]
            next_candle = price_rows[i + 1]

            # ── Check open trade SL/TP on current candle ──────────────────────
            if open_trade is not None:
                candles_since_entry = i - open_trade.get("entry_bar_index", i)
                closed = self._check_exit(
                    open_trade=open_trade,
                    candle=current_candle,
                    market_type=market_type,
                    apply_slippage=apply_slippage,
                    candles_since_entry=candles_since_entry,
                    account_size=account_size,
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

            # ── Generate signal from pre-computed values ───────────────────────
            if open_trade is not None:
                continue  # one position per symbol at a time

            candle_ts: datetime.datetime = current_candle.timestamp

            # Cooldown filter: mirror SignalEngine cooldown per timeframe
            if last_signal_ts is not None:
                elapsed = (candle_ts - last_signal_ts).total_seconds() / 60
                if elapsed < cooldown_minutes:
                    continue

            # Use index i-1 (last completed candle visible before current candle)
            idx = i - 1
            ta_score_i = float(ta_scores_precomp[idx]) if not math.isnan(ta_scores_precomp[idx]) else None
            regime_i = regimes_precomp[idx] if regimes_precomp else "DEFAULT"
            atr_i = _nan_to_none(ta_arrays["atr"][idx]) if "atr" in ta_arrays else None

            if ta_score_i is None:
                continue

            # Build indicator dict from pre-computed arrays at index [idx]
            def _get_arr(key: str) -> Optional[float]:
                arr = ta_arrays.get(key)
                if arr is None:
                    return None
                return _nan_to_none(arr[idx])

            ta_indicators_i: dict[str, Any] = {
                "rsi": _get_arr("rsi"),
                "macd": _get_arr("macd"),
                "macd_signal": _get_arr("macd_signal"),
                "macd_hist": _get_arr("macd_hist"),
                "bb_upper": _get_arr("bb_upper"),
                "bb_middle": _get_arr("bb_middle"),
                "bb_lower": _get_arr("bb_lower"),
                "sma20": _get_arr("sma_fast"),
                "sma50": _get_arr("sma_slow"),
                "sma200": _get_arr("sma_long"),
                "ema12": _get_arr("ema_fast"),
                "ema26": _get_arr("ema_slow"),
                "adx": _get_arr("adx"),
                "plus_di": _get_arr("plus_di"),
                "minus_di": _get_arr("minus_di"),
                "stoch_k": _get_arr("stoch_k"),
                "stoch_d": _get_arr("stoch_d"),
                "atr": atr_i,
                "atr14": atr_i,
                "current_price": _nan_to_none(ta_arrays["close"][idx]) if "close" in ta_arrays else None,
                "current_volume": _nan_to_none(ta_arrays["volume"][idx]) if "volume" in ta_arrays else None,
                "avg_volume_20": None,  # pipeline check_volume uses df; set via context
            }

            signal = self._generate_signal_fast(
                ta_score=ta_score_i,
                atr_value=atr_i,
                regime=regime_i,
                ta_indicators_at_i=ta_indicators_i,
                symbol=symbol,
                market_type=market_type,
                timeframe=timeframe,
                df_slice=full_df.iloc[:i],  # O(1) view — for S/R computation only
                candle_idx=i,
                sr_cache=_sr_cache,
            )
            if signal is None:
                continue

            # ── OPT-01: O(log m) D1 slice via bisect (replaces O(m) list comprehension) ──
            # D1 timestamps are pre-sorted in the pre-loop phase above.
            # bisect_right returns insertion point AFTER candle_ts, so [0:idx] gives
            # all D1 rows with timestamp <= candle_ts (correct: no lookahead).
            _d1_idx = bisect.bisect_right(_d1_timestamps, candle_ts)
            d1_rows_for_filter = _d1_all[max(0, _d1_idx - 200):_d1_idx]

            # ── Run unified filter pipeline (SIM-42) ─────────────────────────
            filter_context = {
                "composite_score": float(signal["composite_score"]),
                "market_type": market_type,
                "symbol": symbol,
                "regime": signal["regime"],
                "direction": signal["direction"],
                "timeframe": timeframe,
                "df": full_df.iloc[:i],   # O(1) pandas view — no copy
                "ta_indicators": signal.get("ta_indicators", {}),
                "candle_ts": candle_ts,
                "d1_rows": d1_rows_for_filter,  # V6-06: populated from pre-loaded D1 cache
                "economic_events": economic_events or [],
                # OPT-02: O(1) array index replaces per-candle _to_utc() + dict.get().
                # dxy_rsi_at_idx is pre-mapped to price_rows indices using bisect
                # nearest-previous — more correct than exact-match which could miss
                # when DXY and symbol timestamps don't align to the same second.
                # idx = i-1 (previous candle) consistent with all other TA indicators.
                "dxy_rsi": dxy_rsi_at_idx[idx],
                # V6 TASK-V6-02: only TA available in backtest → proportional scaling
                "available_weight": _TA_WEIGHT,
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
                # V6 TASK-V6-11: track bar index for time/MAE exit
                "entry_bar_index": i + 1,  # entry happens at next_candle (i+1)
                # CAL2-06: trailing stop tracking
                "trailing_sl_active": False,
                # Worst-case: preserve original SL even after trailing stop activates
                "original_stop_loss": sl,
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
                sl_price=open_trade["stop_loss"],
            ))

        # V6 TASK-V6-10: return filter stats alongside trades
        return trades, pipeline.get_stats()

    def _check_exit(
        self,
        open_trade: dict[str, Any],
        candle: Any,
        market_type: str,
        apply_slippage: bool,
        candles_since_entry: int = 0,
        account_size: Decimal = Decimal("1000"),
    ) -> Optional[BacktestTradeResult]:
        """
        SIM-09 logic: check SL and TP by candle high/low.
        Worst case: if both SL and TP are breached → exit at SL.

        V6 TASK-V6-11: also checks time exit and MAE exit.
        Order: SL → TP → time_exit → mae_exit.

        Returns a closed BacktestTradeResult or None if still open.
        """
        direction = open_trade["direction"]
        sl = open_trade["stop_loss"]
        tp = open_trade["take_profit"]
        entry = open_trade["entry_price"]
        candle_high = Decimal(str(candle.high))
        candle_low = Decimal(str(candle.low))
        candle_close = Decimal(str(candle.close))
        timeframe = open_trade.get("timeframe", "H1")

        exit_price: Optional[Decimal] = None
        exit_reason: Optional[str] = None

        # CAL2-06: Trailing stop activation.
        # When MFE >= 50% of TP distance, move SL to entry + 20% of TP dist.
        # Runs FIRST so that SL check below uses the updated (trailing) SL level.
        tp_distance = abs(tp - entry)
        mfe_current = Decimal(str(open_trade.get("mfe", 0)))
        if (
            tp_distance > Decimal("0")
            and mfe_current >= tp_distance * _TRAILING_STOP_MFE_TRIGGER
            and not open_trade.get("trailing_sl_active")
        ):
            if direction == "LONG":
                trailing_sl_new = entry + tp_distance * _TRAILING_STOP_LOCK_RATIO
            else:
                trailing_sl_new = entry - tp_distance * _TRAILING_STOP_LOCK_RATIO
            open_trade["trailing_sl_active"] = True
            open_trade["stop_loss"] = trailing_sl_new
            # Update sl to the new trailing value for this candle's hit check
            sl = trailing_sl_new

        # Worst-case: preserve original SL for gap scenarios even when trailing is active.
        # If price gaps through both trailing SL and original SL, exit at original SL ("sl_hit").
        original_sl = open_trade.get("original_stop_loss", sl)

        sl_hit = False
        tp_hit = False
        original_sl_hit = False

        if direction == "LONG":
            sl_hit = candle_low <= sl
            tp_hit = candle_high >= tp
            # Worst case: gap through original SL overrides trailing stop exit
            if open_trade.get("trailing_sl_active") and original_sl != sl:
                original_sl_hit = candle_low <= original_sl
        else:
            sl_hit = candle_high >= sl
            tp_hit = candle_low <= tp
            # Worst case: gap through original SL overrides trailing stop exit
            if open_trade.get("trailing_sl_active") and original_sl != sl:
                original_sl_hit = candle_high >= original_sl

        if original_sl_hit or sl_hit or tp_hit:
            # Worst case (CLAUDE.md §6): original SL > trailing SL > TP
            if original_sl_hit:
                # Gap hit original SL — worst case, exit at original SL with full loss
                exit_price = original_sl
                if apply_slippage:
                    exit_price = _compute_sl_exit_price(original_sl, direction, market_type, entry)
                exit_reason = "sl_hit"
            elif sl_hit:
                exit_price = sl
                if apply_slippage:
                    exit_price = _compute_sl_exit_price(sl, direction, market_type, entry)
                # CAL2-06: if trailing was active, label as trailing_stop
                exit_reason = "trailing_stop" if open_trade.get("trailing_sl_active") else "sl_hit"
            else:
                exit_price = tp
                exit_reason = "tp_hit"

        # V6 TASK-V6-11: Time exit — after N candles without profit
        # V6-CAL-03: H1 сокращен с 48 до 24 — 61.6% trades выходили по time_exit,
        # сидя 2 дня без прогресса. 1 день достаточен для H1 сигналов.
        if exit_reason is None:
            max_candles = _TIME_EXIT_CANDLES.get(timeframe, 24)
            if candles_since_entry >= max_candles:
                # Compute unrealized PnL at candle close
                if direction == "LONG":
                    unrealized_move = candle_close - entry
                else:
                    unrealized_move = entry - candle_close
                if unrealized_move <= Decimal("0"):
                    exit_price = candle_close
                    exit_reason = "time_exit"

        # V6 TASK-V6-11: MAE exit — drawdown too large without progress
        if exit_reason is None:
            current_mae = Decimal(str(open_trade.get("mae", 0)))
            current_mfe = Decimal(str(open_trade.get("mfe", 0)))
            # Use original_stop_loss for MAE threshold to avoid distortion after trailing
            # stop activation (trailing SL is much smaller — only 20% of TP distance).
            mae_sl = open_trade.get("original_stop_loss", sl)
            sl_distance = abs(entry - mae_sl)
            tp_distance = abs(tp - entry)
            mae_threshold = sl_distance * Decimal("0.60")
            mfe_threshold = tp_distance * Decimal("0.20")
            if (
                sl_distance > 0
                and current_mae >= mae_threshold
                and current_mfe < mfe_threshold
                and candles_since_entry >= 3
            ):
                exit_price = candle_close
                exit_reason = "mae_exit"

        if exit_reason is None:
            return None

        pnl_pips, pnl_usd = _compute_pnl(
            direction, entry, exit_price,
            Decimal(str(open_trade["position_pct"])),
            account_size,
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
            sl_price=sl,  # V6-CAL-07: preserve SL level for accurate MAE % calculation
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

    def _generate_signal_fast(
        self,
        ta_score: float,
        atr_value: Optional[float],
        regime: str,
        ta_indicators_at_i: dict[str, Any],
        symbol: str,
        market_type: str,
        timeframe: str,
        df_slice: Optional[pd.DataFrame] = None,
        candle_idx: int = 0,
        sr_cache: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Lightweight signal generation from pre-computed scalar values.

        Called from the O(n) main loop in _simulate_symbol() where TAEngine
        has already been applied to the full DataFrame and all indicator values
        are available as pre-computed arrays.

        Args:
            ta_score:          Pre-computed TA score at index [i-1].
            atr_value:         Pre-computed ATR at index [i-1].
            regime:            Pre-computed regime string at index [i-1].
            ta_indicators_at_i: Dict of scalar indicator values at [i-1].
            symbol:            Instrument symbol.
            market_type:       "forex", "crypto", or "stocks".
            timeframe:         Timeframe string, e.g. "H1".
            df_slice:          O(1) pandas view full_df.iloc[:i] — used only for
                               S/R computation (called ~200-500 times per symbol).
            candle_idx:        Current candle index i in the main loop.
            sr_cache:          Mutable dict shared across calls for S/R caching (OPT-03).
                               Keys: "support", "resistance", "last_idx".

        Returns raw signal dict or None if no signal.
        """
        if atr_value is None or atr_value <= 0:
            return None

        composite = _TA_WEIGHT * ta_score

        if composite == 0:
            return None

        direction = "LONG" if composite > 0 else "SHORT"

        # OPT-03: S/R levels with cache — recompute only every _SR_CACHE_INTERVAL candles.
        # S/R levels are slow-moving (change over trading days, not hourly).
        # sr_cache is a mutable dict passed from _simulate_symbol() and shared across calls.
        # When sr_cache is None (e.g. tests calling _generate_signal_fast directly),
        # falls back to always-recompute behavior for correctness.
        support_levels: list[Decimal] = []
        resistance_levels: list[Decimal] = []
        _should_recompute = (
            sr_cache is None
            or (candle_idx - sr_cache.get("last_idx", -_SR_CACHE_INTERVAL)) >= _SR_CACHE_INTERVAL
        )
        if _should_recompute and df_slice is not None and len(df_slice) >= 20:
            try:
                from src.analysis.ta_engine import TAEngine as _TAE
                _ta_sr = _TAE(df_slice, timeframe=timeframe)
                ta_inds = _ta_sr.calculate_all_indicators()
                raw_support = ta_inds.get("support_levels") or [ta_inds.get("support")]
                raw_resistance = ta_inds.get("resistance_levels") or [ta_inds.get("resistance")]
                # Normalise: calculate_all_indicators returns scalar, not list
                if isinstance(raw_support, (int, float)) and raw_support:
                    raw_support = [raw_support]
                if isinstance(raw_resistance, (int, float)) and raw_resistance:
                    raw_resistance = [raw_resistance]
                new_support = [Decimal(str(v)) for v in raw_support if v] if isinstance(raw_support, list) else []
                new_resistance = [Decimal(str(v)) for v in raw_resistance if v] if isinstance(raw_resistance, list) else []
                if sr_cache is not None:
                    sr_cache["support"] = new_support
                    sr_cache["resistance"] = new_resistance
                    sr_cache["last_idx"] = candle_idx
                support_levels = new_support
                resistance_levels = new_resistance
            except Exception:
                if sr_cache is not None:
                    support_levels = sr_cache.get("support", [])
                    resistance_levels = sr_cache.get("resistance", [])
        elif sr_cache is not None:
            support_levels = sr_cache.get("support", [])
            resistance_levels = sr_cache.get("resistance", [])

        atr_decimal = Decimal(str(round(atr_value, 8)))

        return {
            "direction": direction,
            "composite_score": Decimal(str(round(composite, 4))),
            "ta_score": ta_score,
            "regime": regime,
            "atr": atr_decimal,
            "position_pct": 2.0,  # fixed 2% risk per SIM-19 default
            "ta_indicators": ta_indicators_at_i,
            "support_levels": support_levels,
            "resistance_levels": resistance_levels,
        }

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
