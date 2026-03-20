"""Pydantic models for backtesting parameters and results (SIM-22)."""

import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, field_validator

# Maps external timeframe aliases to internal canonical format
_TF_ALIAS_MAP: dict[str, str] = {
    "1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30",
    "1h": "H1", "4h": "H4", "1d": "D1", "1w": "W1",
    "m1": "M1", "m5": "M5", "m15": "M15", "m30": "M30",
    "h1": "H1", "h4": "H4", "d1": "D1", "w1": "W1",
}


class BacktestParams(BaseModel):
    """Input parameters for a backtest run."""

    symbols: list[str]
    timeframe: str = "H1"
    start_date: str          # ISO date string: "2024-01-01"
    end_date: str            # ISO date string: "2025-12-31"
    account_size: Decimal = Decimal("1000.0")
    apply_slippage: bool = True
    apply_swap: bool = True

    # SIM-43: Per-filter toggles for parameterized backtesting
    apply_ranging_filter: bool = True
    apply_d1_trend_filter: bool = True
    apply_volume_filter: bool = True
    apply_weekday_filter: bool = True
    apply_momentum_filter: bool = True
    apply_calendar_filter: bool = True
    apply_session_filter: bool = True
    min_composite_score: Optional[float] = None  # None = use global config

    # V6 TASK-V6-13: Walk-forward validation parameters
    enable_walk_forward: bool = False
    in_sample_months: int = 18
    out_of_sample_months: int = 6

    @field_validator("timeframe")
    @classmethod
    def normalize_timeframe(cls, v: str) -> str:
        """Normalize external timeframe aliases (e.g. '1h') to internal format ('H1')."""
        return _TF_ALIAS_MAP.get(v, v)

    @field_validator("symbols")
    @classmethod
    def symbols_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("symbols must not be empty")
        return v

    @field_validator("end_date")
    @classmethod
    def end_after_start(cls, end: str, info: Any) -> str:
        start = info.data.get("start_date")
        if start and end <= start:
            raise ValueError("end_date must be after start_date")
        return end

    @field_validator("account_size")
    @classmethod
    def account_positive(cls, v: Decimal) -> Decimal:
        if v <= Decimal("0"):
            raise ValueError("account_size must be positive")
        return v


class BacktestTradeResult(BaseModel):
    """Result for a single backtested trade."""

    symbol: str
    timeframe: str
    direction: str                           # LONG / SHORT
    entry_price: Decimal
    exit_price: Optional[Decimal] = None
    exit_reason: Optional[str] = None
    pnl_pips: Optional[Decimal] = None
    pnl_usd: Optional[Decimal] = None
    result: Optional[str] = None            # win / loss / breakeven
    composite_score: Optional[Decimal] = None
    entry_at: Optional[datetime.datetime] = None
    exit_at: Optional[datetime.datetime] = None
    duration_minutes: Optional[int] = None
    mfe: Optional[Decimal] = None
    mae: Optional[Decimal] = None
    regime: Optional[str] = None            # SIM-44: market regime at entry
    sl_price: Optional[Decimal] = None      # V6-CAL-07: SL level for MAE % calculation


class BacktestResult(BaseModel):
    """Aggregate summary returned after a backtest run completes."""

    run_id: str
    status: str                              # completed / failed
    total_trades: int = 0
    win_rate_pct: Optional[Decimal] = None
    profit_factor: Optional[Decimal] = None
    total_pnl_usd: Optional[Decimal] = None
    max_drawdown_pct: Optional[Decimal] = None
    avg_duration_minutes: Optional[int] = None
    long_count: int = 0
    short_count: int = 0
    by_symbol: dict[str, Any] = {}
    by_score_bucket: dict[str, Any] = {}
    equity_curve: list[dict[str, Any]] = []
    monthly_returns: list[dict[str, Any]] = []
    error: Optional[str] = None
    # V6 TASK-V6-13: Walk-forward validation results
    walk_forward: Optional[dict[str, Any]] = None
