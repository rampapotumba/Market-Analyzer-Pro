"""Walk-forward backtesting engine for signal weight optimisation.

Algorithm:
  1. Split history into IS (in-sample) and OOS (out-of-sample) windows.
  2. Grid-search weights (TA/FA/Sentiment/Geo) on IS window to maximise Sharpe.
  3. Validate optimal weights on OOS window.
  4. Repeat with a rolling window (5+ folds).
  5. If OOS metrics pass thresholds → update signal engine weights.
  6. Run Monte Carlo on equity curve to estimate drawdown CI.

OOS validation criteria (from config):
  - MIN_OOS_TRADES   ≥ 30
  - MIN_OOS_SHARPE   ≥ 0.8
  - MIN_OOS_PROFIT_FACTOR ≥ 1.3
"""

import datetime
import itertools
import json
import logging
import math
import random
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database.engine import async_session_factory
from src.database.models import (
    BacktestRun,
    BacktestTrade,
    Instrument,
    PriceData,
    Signal,
    SignalResult,
)

logger = logging.getLogger(__name__)


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class WeightSet:
    ta: float
    fa: float
    sentiment: float
    geo: float

    def as_dict(self) -> dict:
        return {"ta": self.ta, "fa": self.fa, "sentiment": self.sentiment, "geo": self.geo}

    def validate(self) -> bool:
        return abs(self.ta + self.fa + self.sentiment + self.geo - 1.0) < 0.01


@dataclass
class TradeRecord:
    direction: str       # LONG / SHORT
    entry_at: datetime.datetime
    exit_at: Optional[datetime.datetime]
    entry_price: float
    exit_price: Optional[float]
    pnl_pct: Optional[float]
    result: Optional[str]  # win/loss/breakeven
    composite_score: float
    phase: str = "oos"


@dataclass
class BacktestMetrics:
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    breakevens: int = 0
    sharpe: float = 0.0
    profit_factor: float = 0.0
    win_rate: float = 0.0
    max_drawdown: float = 0.0
    expectancy: float = 0.0
    pnl_series: list[float] = field(default_factory=list)

    def passes_validation(self) -> bool:
        return (
            self.total_trades >= settings.MIN_OOS_TRADES
            and self.sharpe >= settings.MIN_OOS_SHARPE
            and self.profit_factor >= settings.MIN_OOS_PROFIT_FACTOR
        )


# ── Weight grid ───────────────────────────────────────────────────────────────

def _generate_weight_grid(step: float = 0.05) -> list[WeightSet]:
    """Generate all weight combinations that sum to 1.0."""
    values = [round(v * step, 2) for v in range(int(1.0 / step) + 1)]
    grid = []
    for ta, fa, sent in itertools.product(values, repeat=3):
        geo = round(1.0 - ta - fa - sent, 2)
        if 0.0 <= geo <= 1.0:
            ws = WeightSet(ta=ta, fa=fa, sentiment=sent, geo=geo)
            if ws.validate():
                grid.append(ws)
    return grid


# ── Engine ────────────────────────────────────────────────────────────────────

class BacktestEngine:
    """Walk-forward + Monte Carlo backtesting engine."""

    def __init__(self) -> None:
        self._weight_grid = _generate_weight_grid(step=0.05)

    async def run_weekly(self) -> None:
        """Entry point called by Celery beat on Sundays."""
        logger.info("BacktestEngine: starting weekly walk-forward run")
        async with async_session_factory() as session:
            instruments = await self._get_active_instruments(session)
            historical_trades = await self._load_historical_trades(session)

        if len(historical_trades) < settings.MIN_OOS_TRADES * 2:
            logger.warning(
                "BacktestEngine: not enough historical trades (%d) to run backtest",
                len(historical_trades),
            )
            return

        result = await self.run_walk_forward(historical_trades)
        await self._persist_run(result)

    async def run_walk_forward(
        self, trades: list[TradeRecord]
    ) -> dict:
        """Run 5-fold walk-forward optimisation.

        Returns a dict with optimal_weights, oos_metrics, passed_validation.
        """
        if not trades:
            return {"passed_validation": False, "optimal_weights": None}

        trades_sorted = sorted(trades, key=lambda t: t.entry_at)
        n = len(trades_sorted)
        is_months = settings.BACKTEST_IN_SAMPLE_MONTHS
        oos_months = settings.BACKTEST_OUT_OF_SAMPLE_MONTHS
        fold_days = (is_months + oos_months) * 30
        step_days = oos_months * 30

        start = trades_sorted[0].entry_at
        end = trades_sorted[-1].entry_at

        best_weights: Optional[WeightSet] = None
        all_oos_trades: list[TradeRecord] = []

        fold = 0
        window_start = start
        while True:
            is_end = window_start + datetime.timedelta(days=is_months * 30)
            oos_end = is_end + datetime.timedelta(days=oos_months * 30)
            if oos_end > end:
                break

            is_trades = [t for t in trades_sorted if window_start <= t.entry_at < is_end]
            oos_trades = [t for t in trades_sorted if is_end <= t.entry_at < oos_end]

            if len(is_trades) < 10 or len(oos_trades) < 5:
                window_start += datetime.timedelta(days=step_days)
                continue

            fold_weights = self.optimize_weights(is_trades)
            for t in oos_trades:
                t.phase = "oos"
            all_oos_trades.extend(oos_trades)
            best_weights = fold_weights
            fold += 1

            window_start += datetime.timedelta(days=step_days)

        if not all_oos_trades or best_weights is None:
            return {"passed_validation": False, "optimal_weights": None, "folds": fold}

        oos_metrics = self.calculate_report(all_oos_trades)
        mc_drawdown = MonteCarlo(settings.MONTE_CARLO_SIMULATIONS).run(
            oos_metrics.pnl_series
        )

        passed = oos_metrics.passes_validation()

        logger.info(
            "BacktestEngine: folds=%d OOS trades=%d Sharpe=%.2f PF=%.2f passed=%s",
            fold,
            oos_metrics.total_trades,
            oos_metrics.sharpe,
            oos_metrics.profit_factor,
            passed,
        )

        return {
            "passed_validation": passed,
            "optimal_weights": best_weights.as_dict() if best_weights else None,
            "oos_sharpe": oos_metrics.sharpe,
            "oos_profit_factor": oos_metrics.profit_factor,
            "oos_win_rate": oos_metrics.win_rate,
            "oos_max_drawdown": oos_metrics.max_drawdown,
            "oos_total_trades": oos_metrics.total_trades,
            "monte_carlo_ci_drawdown": mc_drawdown,
            "folds": fold,
        }

    def optimize_weights(self, trades: list[TradeRecord]) -> WeightSet:
        """Grid-search weight combinations to maximise Sharpe on the IS window."""
        best_sharpe = -math.inf
        best_ws = WeightSet(ta=0.45, fa=0.25, sentiment=0.20, geo=0.10)

        for ws in self._weight_grid:
            # Re-weight composite scores (approximation: adjust threshold)
            adjusted = self._apply_weights(trades, ws)
            metrics = self.calculate_report(adjusted)
            if metrics.sharpe > best_sharpe:
                best_sharpe = metrics.sharpe
                best_ws = ws

        logger.debug(
            "optimize_weights: best Sharpe=%.3f weights=%s", best_sharpe, best_ws.as_dict()
        )
        return best_ws

    def calculate_report(self, trades: list[TradeRecord]) -> BacktestMetrics:
        """Calculate all performance metrics from a list of trades."""
        if not trades:
            return BacktestMetrics()

        pnl = [t.pnl_pct for t in trades if t.pnl_pct is not None]
        if not pnl:
            return BacktestMetrics()

        wins = [p for p in pnl if p > 0]
        losses = [p for p in pnl if p < 0]
        breakevens = [p for p in pnl if p == 0]

        win_rate = len(wins) / len(pnl) if pnl else 0.0
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
        profit_factor = (sum(wins) / abs(sum(losses))) if losses else math.inf

        # Sharpe ratio (daily returns approximation)
        pnl_arr = np.array(pnl, dtype=float)
        mean_ret = np.mean(pnl_arr)
        std_ret = np.std(pnl_arr, ddof=1)
        sharpe = (mean_ret / std_ret * math.sqrt(252)) if std_ret > 0 else 0.0

        # Max drawdown from equity curve
        equity = np.cumsum(pnl_arr)
        running_max = np.maximum.accumulate(equity)
        drawdown = running_max - equity
        max_dd = float(np.max(drawdown)) if len(drawdown) > 0 else 0.0

        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

        return BacktestMetrics(
            total_trades=len(pnl),
            wins=len(wins),
            losses=len(losses),
            breakevens=len(breakevens),
            sharpe=float(sharpe),
            profit_factor=float(profit_factor),
            win_rate=win_rate,
            max_drawdown=max_dd,
            expectancy=expectancy,
            pnl_series=pnl,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _apply_weights(
        self, trades: list[TradeRecord], ws: WeightSet
    ) -> list[TradeRecord]:
        """Return trades filtered by composite score threshold based on weights.

        This is an approximation: trades with composite_score above 0 are kept
        (direction already encoded in sign convention of score vs direction).
        Full weight re-application would require original component scores,
        which are not stored per-trade in this simplified version.
        """
        # For now, return trades unchanged — full weight application requires
        # storing TA/FA/Sent/Geo scores per-trade, which will be added in 2.1.6.
        return trades

    async def _get_active_instruments(self, session: AsyncSession) -> list[Instrument]:
        result = await session.execute(
            select(Instrument).where(Instrument.is_active.is_(True))
        )
        return list(result.scalars().all())

    async def _load_historical_trades(
        self, session: AsyncSession
    ) -> list[TradeRecord]:
        """Load completed signal results as TradeRecord objects."""
        stmt = (
            select(Signal, SignalResult)
            .join(SignalResult, Signal.id == SignalResult.signal_id)
            .where(SignalResult.exit_at.isnot(None))
            .order_by(Signal.created_at.asc())
        )
        result = await session.execute(stmt)
        rows = result.all()

        trades = []
        for signal, sr in rows:
            pnl = float(sr.pnl_percent) if sr.pnl_percent is not None else None
            trades.append(
                TradeRecord(
                    direction=signal.direction,
                    entry_at=signal.created_at,
                    exit_at=sr.exit_at,
                    entry_price=float(sr.entry_actual_price) if sr.entry_actual_price else 0.0,
                    exit_price=float(sr.exit_price) if sr.exit_price else None,
                    pnl_pct=pnl,
                    result=sr.result,
                    composite_score=float(signal.composite_score),
                )
            )
        return trades

    async def _persist_run(self, result: dict) -> None:
        """Persist backtest run metadata to the DB."""
        async with async_session_factory() as session:
            async with session.begin():
                run = BacktestRun(
                    completed_at=datetime.datetime.now(datetime.timezone.utc),
                    optimal_weights=json.dumps(result.get("optimal_weights")),
                    oos_sharpe=_to_decimal(result.get("oos_sharpe")),
                    oos_profit_factor=_to_decimal(result.get("oos_profit_factor")),
                    oos_win_rate=_to_decimal(result.get("oos_win_rate")),
                    oos_max_drawdown=_to_decimal(result.get("oos_max_drawdown")),
                    oos_total_trades=result.get("oos_total_trades"),
                    monte_carlo_ci_drawdown=_to_decimal(result.get("monte_carlo_ci_drawdown")),
                    passed_validation=result.get("passed_validation"),
                    notes=f"folds={result.get('folds', 0)}",
                )
                session.add(run)


# ── Monte Carlo ───────────────────────────────────────────────────────────────

class MonteCarlo:
    """Bootstrapped Monte Carlo simulation of equity curve drawdowns."""

    def __init__(self, simulations: int = 10_000) -> None:
        self.simulations = simulations

    def run(self, pnl_series: list[float], ci: float = 0.95) -> float:
        """Return the `ci`-th percentile max drawdown from Monte Carlo simulations."""
        if not pnl_series:
            return 0.0

        pnl_arr = np.array(pnl_series, dtype=float)
        n = len(pnl_arr)
        max_drawdowns = []

        for _ in range(self.simulations):
            # Bootstrap: sample with replacement
            sim = np.random.choice(pnl_arr, size=n, replace=True)
            equity = np.cumsum(sim)
            running_max = np.maximum.accumulate(equity)
            dd = float(np.max(running_max - equity))
            max_drawdowns.append(dd)

        max_drawdowns.sort()
        idx = int(ci * self.simulations)
        return max_drawdowns[min(idx, len(max_drawdowns) - 1)]


# ── Weight validator ──────────────────────────────────────────────────────────

class WeightValidator:
    """Validates and updates live signal engine weights after a backtest."""

    async def validate_and_update(self, backtest_result: dict) -> bool:
        """If backtest passed validation, update config-level weights in DB/cache.

        Returns True if weights were updated.
        """
        if not backtest_result.get("passed_validation"):
            logger.info("WeightValidator: backtest did not pass — keeping current weights")
            return False

        weights = backtest_result.get("optimal_weights")
        if not weights:
            return False

        # In v2, weights are read from the latest BacktestRun at startup.
        # We just log the update here; the signal engine reads from DB on init.
        logger.info(
            "WeightValidator: new validated weights → %s (Sharpe=%.2f, PF=%.2f)",
            weights,
            backtest_result.get("oos_sharpe", 0),
            backtest_result.get("oos_profit_factor", 0),
        )
        return True


# ── Utils ─────────────────────────────────────────────────────────────────────

def _to_decimal(val: Optional[float]) -> Optional[Decimal]:
    if val is None:
        return None
    return Decimal(str(round(val, 4)))
