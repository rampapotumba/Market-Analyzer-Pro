"""Tests for src.analysis.backtest_engine."""

import datetime
import math
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.analysis.backtest_engine import (
    BacktestEngine,
    BacktestMetrics,
    MonteCarlo,
    TradeRecord,
    WeightSet,
    WeightValidator,
    _generate_weight_grid,
    _to_decimal,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_trade(pnl: float, days_ago: int = 10) -> TradeRecord:
    now = datetime.datetime.now(datetime.timezone.utc)
    return TradeRecord(
        direction="LONG",
        entry_at=now - datetime.timedelta(days=days_ago),
        exit_at=now,
        entry_price=100.0,
        exit_price=100.0 + pnl,
        pnl_pct=pnl,
        result="win" if pnl > 0 else ("loss" if pnl < 0 else "breakeven"),
        composite_score=50.0 if pnl > 0 else -50.0,
    )


def _make_trades(wins: int = 20, losses: int = 10, win_pnl: float = 2.0, loss_pnl: float = -1.0) -> list[TradeRecord]:
    trades = []
    for i in range(wins):
        trades.append(_make_trade(win_pnl, days_ago=wins + losses - i))
    for i in range(losses):
        trades.append(_make_trade(loss_pnl, days_ago=losses - i))
    return sorted(trades, key=lambda t: t.entry_at)


# ── WeightSet ──────────────────────────────────────────────────────────────────

class TestWeightSet:
    def test_as_dict(self):
        ws = WeightSet(ta=0.45, fa=0.25, sentiment=0.20, geo=0.10)
        d = ws.as_dict()
        assert set(d.keys()) == {"ta", "fa", "sentiment", "geo"}

    def test_validate_valid(self):
        ws = WeightSet(ta=0.45, fa=0.25, sentiment=0.20, geo=0.10)
        assert ws.validate()

    def test_validate_invalid(self):
        ws = WeightSet(ta=0.50, fa=0.50, sentiment=0.50, geo=0.50)
        assert not ws.validate()

    def test_sum_to_one(self):
        ws = WeightSet(ta=0.45, fa=0.25, sentiment=0.20, geo=0.10)
        assert abs(ws.ta + ws.fa + ws.sentiment + ws.geo - 1.0) < 0.01


# ── Weight grid ────────────────────────────────────────────────────────────────

class TestGenerateWeightGrid:
    def test_grid_not_empty(self):
        grid = _generate_weight_grid(step=0.10)
        assert len(grid) > 0

    def test_all_valid(self):
        grid = _generate_weight_grid(step=0.10)
        for ws in grid:
            assert ws.validate(), f"Invalid: {ws}"

    def test_fine_grid_larger(self):
        coarse = _generate_weight_grid(step=0.20)
        fine = _generate_weight_grid(step=0.10)
        assert len(fine) >= len(coarse)


# ── BacktestMetrics ────────────────────────────────────────────────────────────

class TestBacktestMetrics:
    def test_passes_validation_positive(self):
        m = BacktestMetrics(
            total_trades=35,
            sharpe=1.2,
            profit_factor=1.5,
        )
        assert m.passes_validation()

    def test_fails_not_enough_trades(self):
        m = BacktestMetrics(total_trades=10, sharpe=1.2, profit_factor=1.5)
        assert not m.passes_validation()

    def test_fails_low_sharpe(self):
        m = BacktestMetrics(total_trades=35, sharpe=0.5, profit_factor=1.5)
        assert not m.passes_validation()

    def test_fails_low_profit_factor(self):
        m = BacktestMetrics(total_trades=35, sharpe=1.2, profit_factor=1.0)
        assert not m.passes_validation()


# ── BacktestEngine.calculate_report ───────────────────────────────────────────

class TestCalculateReport:
    def setup_method(self):
        self.engine = BacktestEngine()

    def test_empty_trades_returns_empty_metrics(self):
        metrics = self.engine.calculate_report([])
        assert metrics.total_trades == 0

    def test_no_pnl_trades(self):
        t = _make_trade(pnl=1.0)
        t.pnl_pct = None
        metrics = self.engine.calculate_report([t])
        assert metrics.total_trades == 0

    def test_wins_and_losses_counted(self):
        trades = _make_trades(wins=10, losses=5)
        metrics = self.engine.calculate_report(trades)
        assert metrics.wins == 10
        assert metrics.losses == 5
        assert metrics.total_trades == 15

    def test_win_rate_correct(self):
        trades = _make_trades(wins=20, losses=10)
        metrics = self.engine.calculate_report(trades)
        assert metrics.win_rate == pytest.approx(20 / 30, abs=0.01)

    def test_sharpe_positive_for_profitable_trades(self):
        trades = _make_trades(wins=20, losses=5)
        metrics = self.engine.calculate_report(trades)
        assert metrics.sharpe > 0

    def test_profit_factor_correct(self):
        trades = _make_trades(wins=10, losses=10, win_pnl=2.0, loss_pnl=-1.0)
        metrics = self.engine.calculate_report(trades)
        # PF = total wins / |total losses| = 20 / 10 = 2.0
        assert metrics.profit_factor == pytest.approx(2.0, abs=0.1)

    def test_max_drawdown_non_negative(self):
        trades = _make_trades(wins=5, losses=10)
        metrics = self.engine.calculate_report(trades)
        assert metrics.max_drawdown >= 0.0

    def test_pnl_series_populated(self):
        trades = _make_trades(wins=5, losses=5)
        metrics = self.engine.calculate_report(trades)
        assert len(metrics.pnl_series) == 10


# ── BacktestEngine.optimize_weights ───────────────────────────────────────────

class TestOptimizeWeights:
    def setup_method(self):
        self.engine = BacktestEngine()

    def test_returns_weight_set(self):
        trades = _make_trades(wins=20, losses=10)
        ws = self.engine.optimize_weights(trades)
        assert isinstance(ws, WeightSet)
        assert ws.validate()

    def test_empty_trades_returns_default(self):
        ws = self.engine.optimize_weights([])
        assert isinstance(ws, WeightSet)

    def test_weights_valid(self):
        trades = _make_trades(wins=15, losses=5)
        ws = self.engine.optimize_weights(trades)
        assert ws.validate()


# ── BacktestEngine.run_walk_forward ───────────────────────────────────────────

class TestRunWalkForward:
    @pytest.mark.asyncio
    async def test_insufficient_trades(self):
        engine = BacktestEngine()
        result = await engine.run_walk_forward([])
        assert result["passed_validation"] is False

    @pytest.mark.asyncio
    async def test_with_trades_returns_result(self):
        engine = BacktestEngine()
        # Create 60+ trades spread over 3 years
        import datetime  # noqa: PLC0415
        now = datetime.datetime.now(datetime.timezone.utc)
        trades = []
        for i in range(60):
            t = _make_trade(pnl=2.0 if i % 3 != 0 else -1.0, days_ago=900 - i * 15)
            trades.append(t)
        result = await engine.run_walk_forward(trades)
        assert "passed_validation" in result


# ── MonteCarlo ────────────────────────────────────────────────────────────────

class TestMonteCarlo:
    def test_empty_returns_zero(self):
        mc = MonteCarlo(100)
        result = mc.run([])
        assert result == 0.0

    def test_returns_positive_drawdown(self):
        mc = MonteCarlo(1000)
        pnl = [2.0, -1.0, 2.0, -1.0, 2.0, -3.0, 2.0, 2.0]
        result = mc.run(pnl)
        assert result >= 0.0

    def test_ci_95(self):
        mc = MonteCarlo(1000)
        pnl = [-1.0] * 10 + [2.0] * 10
        result_95 = mc.run(pnl, ci=0.95)
        result_50 = mc.run(pnl, ci=0.50)
        # 95th percentile drawdown should be >= 50th
        assert result_95 >= result_50

    def test_no_loss_trades_zero_drawdown(self):
        mc = MonteCarlo(500)
        pnl = [1.0] * 20
        result = mc.run(pnl)
        assert result == 0.0


# ── WeightValidator ────────────────────────────────────────────────────────────

class TestWeightValidator:
    @pytest.mark.asyncio
    async def test_not_passed_returns_false(self):
        wv = WeightValidator()
        result = await wv.validate_and_update({"passed_validation": False})
        assert result is False

    @pytest.mark.asyncio
    async def test_no_weights_returns_false(self):
        wv = WeightValidator()
        result = await wv.validate_and_update({"passed_validation": True, "optimal_weights": None})
        assert result is False

    @pytest.mark.asyncio
    async def test_passed_with_weights_returns_true(self):
        wv = WeightValidator()
        result = await wv.validate_and_update({
            "passed_validation": True,
            "optimal_weights": {"ta": 0.45, "fa": 0.25, "sentiment": 0.20, "geo": 0.10},
            "oos_sharpe": 1.2,
            "oos_profit_factor": 1.5,
        })
        assert result is True


# ── Utilities ─────────────────────────────────────────────────────────────────

class TestToDecimal:
    def test_float_to_decimal(self):
        result = _to_decimal(1.2345)
        assert isinstance(result, Decimal)

    def test_none_returns_none(self):
        result = _to_decimal(None)
        assert result is None

    def test_zero_returns_decimal(self):
        result = _to_decimal(0.0)
        assert result == Decimal("0.0000")
