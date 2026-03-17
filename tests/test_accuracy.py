"""Tests for Accuracy Tracker metrics calculation."""

import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.tracker.accuracy import AccuracyTracker


# ── Helpers ──────────────────────────────────────────────────────────────────

_BASE_DT = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)


def make_row(result: str, pnl_pips: float, exit_offset_hours: int = 0):
    """Create a (SignalResult, Signal) row mock as returned by SQLAlchemy."""
    sr = MagicMock()
    sr.result = result
    sr.pnl_pips = Decimal(str(pnl_pips))
    sr.exit_at = _BASE_DT + datetime.timedelta(hours=exit_offset_hours)
    return (sr, MagicMock())  # (SignalResult, Signal)


# ── _calculate_metrics ────────────────────────────────────────────────────────

class TestCalculateMetrics:
    """Test the pure metric computation method."""

    def test_empty_rows_returns_null_metrics(self):
        tracker = AccuracyTracker()
        m = tracker._calculate_metrics([])
        assert m["total_signals"] == 0
        assert m["win_rate"] is None
        assert m["profit_factor"] is None
        assert m["sharpe_ratio"] is None
        assert m["expectancy"] is None
        assert m["avg_win_pips"] is None
        assert m["avg_loss_pips"] is None

    def test_counts_wins_losses_breakevens(self):
        rows = [
            make_row("win", 20.0),
            make_row("win", 30.0),
            make_row("loss", -10.0),
            make_row("breakeven", 0.0),
        ]
        tracker = AccuracyTracker()
        m = tracker._calculate_metrics(rows)
        assert m["wins"] == 2
        assert m["losses"] == 1
        assert m["breakevens"] == 1
        assert m["total_signals"] == 4

    def test_win_rate_all_wins(self):
        rows = [make_row("win", 15.0) for _ in range(4)]
        tracker = AccuracyTracker()
        m = tracker._calculate_metrics(rows)
        assert m["win_rate"] == Decimal("1.0000")

    def test_win_rate_all_losses(self):
        rows = [make_row("loss", -15.0) for _ in range(3)]
        tracker = AccuracyTracker()
        m = tracker._calculate_metrics(rows)
        assert m["win_rate"] == Decimal("0.0000")

    def test_win_rate_fifty_percent(self):
        rows = [make_row("win", 20.0), make_row("loss", -15.0)]
        tracker = AccuracyTracker()
        m = tracker._calculate_metrics(rows)
        assert abs(float(m["win_rate"]) - 0.5) < 0.001

    def test_profit_factor_no_losses(self):
        """Profit factor is None when there are no losses."""
        rows = [make_row("win", 20.0), make_row("win", 30.0)]
        tracker = AccuracyTracker()
        m = tracker._calculate_metrics(rows)
        assert m["profit_factor"] is None

    def test_profit_factor_calculation(self):
        """PF = sum(wins) / sum(abs(losses))."""
        rows = [
            make_row("win", 30.0),
            make_row("win", 30.0),
            make_row("loss", -15.0),
        ]
        tracker = AccuracyTracker()
        m = tracker._calculate_metrics(rows)
        # PF = 60 / 15 = 4.0
        assert abs(float(m["profit_factor"]) - 4.0) < 0.1

    def test_avg_win_pips(self):
        rows = [make_row("win", 20.0), make_row("win", 40.0)]
        tracker = AccuracyTracker()
        m = tracker._calculate_metrics(rows)
        assert abs(float(m["avg_win_pips"]) - 30.0) < 0.01

    def test_avg_loss_pips(self):
        rows = [make_row("loss", -10.0), make_row("loss", -30.0)]
        tracker = AccuracyTracker()
        m = tracker._calculate_metrics(rows)
        # avg of absolute values: (10+30)/2 = 20
        assert abs(float(m["avg_loss_pips"]) - 20.0) < 0.01

    def test_expectancy_positive_edge(self):
        """Expectancy should be positive when win rate and avg win exceed losses."""
        rows = [
            make_row("win", 20.0),
            make_row("win", 20.0),
            make_row("loss", -10.0),
        ]
        tracker = AccuracyTracker()
        m = tracker._calculate_metrics(rows)
        # WR = 2/3, avg_win=20, avg_loss=10
        # Expectancy = (2/3 * 20) - (1/3 * 10) = 13.33 - 3.33 = 10
        assert float(m["expectancy"]) > 0

    def test_expectancy_negative_edge(self):
        rows = [
            make_row("win", 5.0),
            make_row("loss", -30.0),
            make_row("loss", -30.0),
        ]
        tracker = AccuracyTracker()
        m = tracker._calculate_metrics(rows)
        assert float(m["expectancy"]) < 0

    def test_sharpe_ratio_single_trade(self):
        """Single trade: std_dev=0, sharpe should be None."""
        rows = [make_row("win", 20.0)]
        tracker = AccuracyTracker()
        m = tracker._calculate_metrics(rows)
        assert m["sharpe_ratio"] is None

    def test_sharpe_ratio_multiple_trades(self):
        rows = [make_row("win", 20.0), make_row("loss", -10.0), make_row("win", 30.0)]
        tracker = AccuracyTracker()
        m = tracker._calculate_metrics(rows)
        assert m["sharpe_ratio"] is not None

    def test_max_drawdown_non_negative(self):
        """Max drawdown percentage should always be >= 0."""
        rows = [
            make_row("win", 20.0),
            make_row("loss", -50.0),
            make_row("win", 10.0),
        ]
        tracker = AccuracyTracker()
        m = tracker._calculate_metrics(rows)
        assert float(m["max_drawdown_pct"]) >= 0

    def test_max_drawdown_zero_when_no_loss(self):
        """No losses after peak → drawdown is 0."""
        rows = [make_row("win", 10.0), make_row("win", 20.0), make_row("win", 30.0)]
        tracker = AccuracyTracker()
        m = tracker._calculate_metrics(rows)
        assert float(m["max_drawdown_pct"]) == 0.0

    def test_decimal_types_returned(self):
        """Numeric metrics should be Decimal."""
        rows = [make_row("win", 20.0), make_row("loss", -10.0)]
        tracker = AccuracyTracker()
        m = tracker._calculate_metrics(rows)
        assert isinstance(m["win_rate"], Decimal)
        assert isinstance(m["max_drawdown_pct"], Decimal)

    def test_only_breakevens(self):
        """Only breakeven trades: win_rate = 0 (no wins or losses counted)."""
        rows = [make_row("breakeven", 0.0) for _ in range(3)]
        tracker = AccuracyTracker()
        m = tracker._calculate_metrics(rows)
        assert m["breakevens"] == 3
        assert m["win_rate"] is None  # no wins+losses to compute ratio


# ── Equity Curve ──────────────────────────────────────────────────────────────

class TestMetricsEdgeCases:
    """Additional edge-case tests."""

    def test_single_win_row(self):
        rows = [make_row("win", 15.0)]
        tracker = AccuracyTracker()
        m = tracker._calculate_metrics(rows)
        assert m["wins"] == 1
        assert m["total_signals"] == 1
        assert m["win_rate"] == Decimal("1.0000")

    def test_sharpe_identical_returns(self):
        """Identical returns → std=0 → sharpe is None."""
        rows = [make_row("win", 10.0) for _ in range(5)]
        tracker = AccuracyTracker()
        m = tracker._calculate_metrics(rows)
        assert m["sharpe_ratio"] is None

    def test_large_dataset(self):
        """Should handle 100+ rows without error."""
        rows = [make_row("win", 15.0) for _ in range(60)]
        rows += [make_row("loss", -10.0) for _ in range(40)]
        tracker = AccuracyTracker()
        m = tracker._calculate_metrics(rows)
        assert m["total_signals"] == 100
        assert abs(float(m["win_rate"]) - 0.6) < 0.01
