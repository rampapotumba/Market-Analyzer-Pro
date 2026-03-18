"""Tests for src.signals.portfolio_risk."""

import pandas as pd
import pytest

from src.signals.portfolio_risk import OpenPosition, PortfolioRiskManager


def _make_pos(
    signal_id: int,
    symbol: str = "EURUSD",
    market_type: str = "forex",
    risk_pct: float = 1.0,
    direction: str = "LONG",
) -> OpenPosition:
    return OpenPosition(
        signal_id=signal_id,
        symbol=symbol,
        market_type=market_type,
        risk_pct=risk_pct,
        direction=direction,
    )


class TestPortfolioRiskManager:
    def test_empty_portfolio_heat_zero(self):
        pm = PortfolioRiskManager()
        assert pm.portfolio_heat() == 0.0

    def test_heat_accumulates(self):
        pm = PortfolioRiskManager()
        pm.add_position(_make_pos(1, risk_pct=1.0))
        pm.add_position(_make_pos(2, risk_pct=2.0))
        assert pm.portfolio_heat() == pytest.approx(3.0)

    def test_remove_position(self):
        pm = PortfolioRiskManager()
        pm.add_position(_make_pos(1, risk_pct=1.0))
        pm.add_position(_make_pos(2, risk_pct=2.0))
        pm.remove_position(1)
        assert pm.portfolio_heat() == pytest.approx(2.0)

    def test_open_count_by_market(self):
        pm = PortfolioRiskManager()
        pm.add_position(_make_pos(1, market_type="forex"))
        pm.add_position(_make_pos(2, market_type="forex"))
        pm.add_position(_make_pos(3, market_type="crypto"))
        assert pm.open_count("forex") == 2
        assert pm.open_count("crypto") == 1
        assert pm.open_count("stocks") == 0

    def test_can_open_allows_within_limits(self):
        pm = PortfolioRiskManager()
        allowed, _ = pm.can_open("EURUSD", "forex", 1.0)
        assert allowed is True

    def test_can_open_rejects_excess_heat(self):
        pm = PortfolioRiskManager()
        # 2 forex positions totalling 5.5% — below max-open limit but high heat
        pm.add_position(_make_pos(1, market_type="forex", risk_pct=3.0))
        pm.add_position(_make_pos(2, market_type="forex", risk_pct=2.5))
        # heat = 5.5%; adding 1.0% would push to 6.5% > MAX_HEAT (6%)
        allowed, reason = pm.can_open("AUDUSD", "forex", 1.0)
        assert allowed is False
        assert "heat" in reason.lower()

    def test_can_open_rejects_max_forex(self):
        """Max open forex positions is 3."""
        pm = PortfolioRiskManager()
        pm.add_position(_make_pos(1, market_type="forex", risk_pct=0.5))
        pm.add_position(_make_pos(2, market_type="forex", risk_pct=0.5))
        pm.add_position(_make_pos(3, market_type="forex", risk_pct=0.5))
        allowed, reason = pm.can_open("GBPUSD", "forex", 0.5)
        assert allowed is False
        assert "Max open" in reason

    def test_can_open_rejects_max_crypto(self):
        """Max open crypto positions is 2."""
        pm = PortfolioRiskManager()
        pm.add_position(_make_pos(1, market_type="crypto", risk_pct=0.5))
        pm.add_position(_make_pos(2, market_type="crypto", risk_pct=0.5))
        allowed, _ = pm.can_open("ETH/USDT", "crypto", 0.5)
        assert allowed is False

    def test_summary_structure(self):
        pm = PortfolioRiskManager()
        pm.add_position(_make_pos(1, market_type="forex", risk_pct=1.0))
        s = pm.summary()
        assert "total_positions" in s
        assert "portfolio_heat" in s
        assert "by_market" in s
        assert s["total_positions"] == 1

    def test_correlation_no_positions_returns_one(self):
        pm = PortfolioRiskManager()
        adj = pm.correlation_adjustment("EURUSD", "LONG", {})
        assert adj == pytest.approx(1.0)

    def test_correlation_uncorrelated_returns_one(self):
        """Positions in different instruments with low correlation → no reduction."""
        import numpy as np

        n = 60
        pm = PortfolioRiskManager()
        pm.add_position(_make_pos(1, symbol="GBPUSD", direction="LONG"))

        # Orthogonal price series
        prices_eur = pd.Series(range(n), dtype=float)
        prices_gbp = pd.Series([float(i % 3) for i in range(n)], dtype=float)

        price_history = {"EURUSD": prices_eur, "GBPUSD": prices_gbp}
        adj = pm.correlation_adjustment("EURUSD", "LONG", price_history)
        assert adj == pytest.approx(1.0)

    def test_correlation_highly_correlated_reduces_size(self):
        """Highly correlated same-direction position should reduce size."""
        n = 60
        pm = PortfolioRiskManager()
        pm.add_position(_make_pos(1, symbol="GBPUSD", direction="LONG"))

        base = pd.Series(range(n), dtype=float)
        # Nearly identical series → ρ ≈ 1.0
        prices = {"EURUSD": base + 0.001, "GBPUSD": base}
        adj = pm.correlation_adjustment("EURUSD", "LONG", prices)
        assert adj < 1.0

    def test_correlation_score_range(self):
        """Correlation score is in [-50, 0]."""
        n = 60
        pm = PortfolioRiskManager()
        pm.add_position(_make_pos(1, symbol="GBPUSD", direction="LONG"))

        base = pd.Series(range(n), dtype=float)
        prices = {"EURUSD": base, "GBPUSD": base}
        score = pm.correlation_score("EURUSD", "LONG", prices)
        assert -50.0 <= score <= 0.0
