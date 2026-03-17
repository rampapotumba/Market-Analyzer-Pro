"""Accuracy Tracker: calculates performance metrics from completed signals."""

import datetime
import logging
import math
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.crud import get_signal_results, upsert_accuracy_stats
from src.database.models import Signal, SignalResult

logger = logging.getLogger(__name__)


class AccuracyTracker:
    """
    Calculates and stores accuracy metrics for signal performance.

    Metrics:
        - Win Rate = wins / (wins + losses)
        - Profit Factor = sum(wins_pips) / abs(sum(losses_pips))
        - Sharpe Ratio = avg_return / std_return * sqrt(n)
        - Max Drawdown = maximum peak-to-trough decline
        - Expectancy = (Win Rate × Avg Win) - (Loss Rate × Avg Loss)
    """

    async def _get_completed_results(
        self,
        db: AsyncSession,
        instrument_id: Optional[int] = None,
        market: Optional[str] = None,
        timeframe: Optional[str] = None,
        period: str = "all_time",
        period_start: Optional[datetime.datetime] = None,
    ) -> list[Any]:
        """Fetch completed signal results with optional filters."""
        stmt = (
            select(SignalResult, Signal)
            .join(Signal, SignalResult.signal_id == Signal.id)
            .where(SignalResult.result.in_(["win", "loss", "breakeven"]))
        )
        if instrument_id:
            stmt = stmt.where(Signal.instrument_id == instrument_id)
        if market:
            from src.database.models import Instrument
            stmt = stmt.join(Instrument, Signal.instrument_id == Instrument.id)
            stmt = stmt.where(Instrument.market == market)
        if timeframe:
            stmt = stmt.where(Signal.timeframe == timeframe)
        if period_start:
            stmt = stmt.where(SignalResult.exit_at >= period_start)

        result = await db.execute(stmt)
        return result.all()

    def _calculate_metrics(
        self, rows: list[Any]
    ) -> dict[str, Any]:
        """Calculate all accuracy metrics from result rows."""
        if not rows:
            return {
                "total_signals": 0,
                "wins": 0,
                "losses": 0,
                "breakevens": 0,
                "win_rate": None,
                "profit_factor": None,
                "avg_win_pips": None,
                "avg_loss_pips": None,
                "sharpe_ratio": None,
                "max_drawdown_pct": None,
                "expectancy": None,
            }

        wins, losses, breakevens = 0, 0, 0
        win_pips: list[float] = []
        loss_pips: list[float] = []
        all_returns: list[float] = []
        equity_curve: list[float] = [0.0]
        running_max = 0.0
        max_drawdown = 0.0

        for row in rows:
            sr = row[0]  # SignalResult
            pips = float(sr.pnl_pips) if sr.pnl_pips is not None else 0.0
            result = sr.result

            if result == "win":
                wins += 1
                win_pips.append(pips)
            elif result == "loss":
                losses += 1
                loss_pips.append(abs(pips))
            else:
                breakevens += 1

            all_returns.append(pips)
            equity_curve.append(equity_curve[-1] + pips)
            running_max = max(running_max, equity_curve[-1])
            drawdown = running_max - equity_curve[-1]
            max_drawdown = max(max_drawdown, drawdown)

        total = wins + losses + breakevens
        win_rate = wins / (wins + losses) if (wins + losses) > 0 else None

        avg_win = sum(win_pips) / len(win_pips) if win_pips else None
        avg_loss = sum(loss_pips) / len(loss_pips) if loss_pips else None

        # Profit Factor
        total_wins = sum(win_pips) if win_pips else 0
        total_losses = sum(loss_pips) if loss_pips else 0
        profit_factor = total_wins / total_losses if total_losses > 0 else None

        # Sharpe Ratio (simplified, using pips as returns)
        sharpe = None
        if len(all_returns) > 1:
            avg_r = sum(all_returns) / len(all_returns)
            variance = sum((r - avg_r) ** 2 for r in all_returns) / len(all_returns)
            std_r = math.sqrt(variance) if variance > 0 else 0
            if std_r > 0:
                sharpe = (avg_r / std_r) * math.sqrt(len(all_returns))

        # Expectancy = (WR × Avg Win) - (LR × Avg Loss)
        expectancy = None
        if win_rate is not None and avg_win is not None and avg_loss is not None:
            loss_rate = 1 - win_rate
            expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)

        # Max drawdown as percentage of peak equity
        peak_equity = max(equity_curve) if equity_curve else 0
        max_drawdown_pct = (max_drawdown / peak_equity * 100) if peak_equity > 0 else 0

        return {
            "total_signals": total,
            "wins": wins,
            "losses": losses,
            "breakevens": breakevens,
            "win_rate": Decimal(str(round(win_rate, 4))) if win_rate is not None else None,
            "profit_factor": Decimal(str(round(profit_factor, 4))) if profit_factor is not None else None,
            "avg_win_pips": Decimal(str(round(avg_win, 4))) if avg_win is not None else None,
            "avg_loss_pips": Decimal(str(round(avg_loss, 4))) if avg_loss is not None else None,
            "sharpe_ratio": Decimal(str(round(sharpe, 4))) if sharpe is not None else None,
            "max_drawdown_pct": Decimal(str(round(max_drawdown_pct, 4))),
            "expectancy": Decimal(str(round(expectancy, 4))) if expectancy is not None else None,
        }

    async def calculate_stats(
        self,
        db: AsyncSession,
        period: str = "all_time",
        instrument_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Calculate accuracy stats and save to DB.

        Args:
            db: Database session
            period: 'all_time', 'monthly', 'weekly'
            instrument_id: Filter by instrument (None = all)

        Returns:
            dict with all metrics
        """
        period_start = None
        now = datetime.datetime.now(datetime.timezone.utc)

        if period == "monthly":
            period_start = now - datetime.timedelta(days=30)
        elif period == "weekly":
            period_start = now - datetime.timedelta(days=7)

        rows = await self._get_completed_results(
            db, instrument_id=instrument_id, period=period, period_start=period_start
        )
        metrics = self._calculate_metrics(rows)

        # Save to DB
        stats_data = {
            "period": period,
            "period_start": period_start,
            "instrument_id": instrument_id,
            **metrics,
            "updated_at": now,
        }

        await upsert_accuracy_stats(db, stats_data)
        return metrics

    async def get_equity_curve(
        self,
        db: AsyncSession,
        instrument_id: Optional[int] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Get equity curve data points for charting.

        Returns:
            List of dicts with 'date' and 'equity' (cumulative pips).
        """
        stmt = (
            select(SignalResult, Signal)
            .join(Signal, SignalResult.signal_id == Signal.id)
            .where(SignalResult.result.in_(["win", "loss", "breakeven"]))
            .order_by(SignalResult.exit_at.asc())
            .limit(limit)
        )
        if instrument_id:
            stmt = stmt.where(Signal.instrument_id == instrument_id)

        result = await db.execute(stmt)
        rows = result.all()

        equity_curve = []
        running_pips = 0.0

        for row in rows:
            sr = row[0]
            pips = float(sr.pnl_pips) if sr.pnl_pips is not None else 0.0
            running_pips += pips
            equity_curve.append({
                "date": sr.exit_at.isoformat() if sr.exit_at else None,
                "pips": round(pips, 2),
                "cumulative_pips": round(running_pips, 2),
                "result": sr.result,
            })

        return equity_curve

    async def calculate_all_stats(self, db: AsyncSession) -> dict[str, Any]:
        """Calculate stats for all periods and instruments."""
        results = {}

        # Overall stats
        for period in ["all_time", "monthly", "weekly"]:
            results[period] = await self.calculate_stats(db, period=period)

        return results
