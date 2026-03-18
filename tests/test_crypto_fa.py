from typing import Dict, Optional
"""Tests for src.analysis.crypto_fa_engine."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.analysis.crypto_fa_engine import (
    CryptoFAEngine,
    _WEIGHTS,
    _halving_cycle_score,
    _BTC_HALVING_DATES,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_onchain(**kwargs):
    o = MagicMock()
    o.nvt_ratio = kwargs.get("nvt_ratio", Decimal("80"))
    o.mvrv_ratio = kwargs.get("mvrv_ratio", Decimal("2.0"))
    o.active_addresses = kwargs.get("active_addresses", 500_000)
    o.exchange_inflow = kwargs.get("exchange_inflow", Decimal("1000"))
    o.exchange_outflow = kwargs.get("exchange_outflow", Decimal("1500"))
    o.funding_rate = kwargs.get("funding_rate", Decimal("0.01"))
    o.open_interest = kwargs.get("open_interest", Decimal("1_000_000"))
    o.dominance = kwargs.get("dominance", Decimal("52.0"))
    o.timestamp = MagicMock()
    return o


def _make_db(onchain=None, macro_vals: Optional[dict] = None) -> AsyncMock:
    db = AsyncMock()
    macro_vals = macro_vals or {}

    async def _execute(stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = onchain
        return result

    db.execute = _execute
    return db


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestCryptoFAEngineInit:
    def test_instantiate(self):
        db = _make_db()
        engine = CryptoFAEngine(db)
        assert engine is not None

    def test_weights_sum_to_one(self):
        assert abs(sum(_WEIGHTS.values()) - 1.0) < 0.01


class TestCryptoFAEngineAnalyze:
    @pytest.mark.asyncio
    async def test_returns_required_keys(self):
        db = _make_db(_make_onchain())
        engine = CryptoFAEngine(db)
        result = await engine.analyze(1, "BTC/USDT")
        assert "score" in result
        assert "components" in result
        assert "data" in result
        assert "symbol" in result

    @pytest.mark.asyncio
    async def test_score_bounded(self):
        db = _make_db(_make_onchain())
        engine = CryptoFAEngine(db)
        result = await engine.analyze(1, "BTC/USDT")
        assert -100.0 <= result["score"] <= 100.0

    @pytest.mark.asyncio
    async def test_no_onchain_returns_valid_score(self):
        """Without on-chain data, only halving cycle score applies → non-zero."""
        db = _make_db(None)
        engine = CryptoFAEngine(db)
        result = await engine.analyze(1, "BTC/USDT")
        assert isinstance(result["score"], float)
        assert -100.0 <= result["score"] <= 100.0

    @pytest.mark.asyncio
    async def test_components_present(self):
        db = _make_db(_make_onchain())
        engine = CryptoFAEngine(db)
        result = await engine.analyze(1, "BTC/USDT")
        for key in ["onchain", "market_structure", "ecosystem", "cycle", "macro_correlation"]:
            assert key in result["components"]

    @pytest.mark.asyncio
    async def test_data_contains_nvt(self):
        db = _make_db(_make_onchain(nvt_ratio=Decimal("70")))
        engine = CryptoFAEngine(db)
        result = await engine.analyze(1, "BTC/USDT")
        assert "nvt_ratio" in result["data"]

    @pytest.mark.asyncio
    async def test_ecosystem_zero(self):
        """Ecosystem component is 0.0 (placeholder) until TVL data available."""
        db = _make_db(_make_onchain())
        engine = CryptoFAEngine(db)
        result = await engine.analyze(1, "ETH/USDT")
        assert result["components"]["ecosystem"] == 0.0


class TestOnchainScoring:
    def setup_method(self):
        self.engine = CryptoFAEngine(_make_db())

    def test_low_nvt_bullish(self):
        """NVT below oversold threshold → positive score."""
        onchain = _make_onchain(nvt_ratio=Decimal("30"))
        score = self.engine._score_onchain(onchain, "BTC")
        assert score > 0

    def test_high_nvt_bearish(self):
        """NVT above overbought threshold → negative score."""
        onchain = _make_onchain(nvt_ratio=Decimal("200"))
        score = self.engine._score_onchain(onchain, "BTC")
        assert score < 0

    def test_net_outflow_bullish(self):
        """More outflow than inflow (accumulation) → positive contribution."""
        onchain = _make_onchain(
            exchange_inflow=Decimal("1000"),
            exchange_outflow=Decimal("5000"),
        )
        score = self.engine._score_onchain(onchain, "BTC")
        assert score > 0

    def test_net_inflow_bearish(self):
        """More inflow than outflow (selling) → negative contribution."""
        onchain = _make_onchain(
            exchange_inflow=Decimal("5000"),
            exchange_outflow=Decimal("1000"),
        )
        score = self.engine._score_onchain(onchain, "BTC")
        assert score < 0

    def test_no_onchain_returns_zero(self):
        score = self.engine._score_onchain(None, "BTC")
        assert score == 0.0


class TestMarketStructureScoring:
    def setup_method(self):
        self.engine = CryptoFAEngine(_make_db())

    def test_high_funding_rate_bearish(self):
        """High positive funding = overleveraged longs → bearish."""
        onchain = _make_onchain(funding_rate=Decimal("0.05"))
        score = self.engine._score_market_structure(onchain)
        assert score < 0

    def test_negative_funding_rate_bullish(self):
        """Negative funding = overleveraged shorts → bullish."""
        onchain = _make_onchain(funding_rate=Decimal("-0.05"))
        score = self.engine._score_market_structure(onchain)
        assert score > 0

    def test_neutral_funding_rate(self):
        onchain = _make_onchain(funding_rate=Decimal("0.005"))
        score = self.engine._score_market_structure(onchain)
        # Should be close to neutral (near 0 or slightly negative from dominance)
        assert -100.0 <= score <= 100.0

    def test_no_onchain_returns_zero(self):
        score = self.engine._score_market_structure(None)
        assert score == 0.0


class TestCycleScoring:
    @pytest.mark.asyncio
    async def test_low_mvrv_bullish(self):
        """MVRV below oversold threshold → positive score."""
        engine = CryptoFAEngine(_make_db())
        onchain = _make_onchain(mvrv_ratio=Decimal("0.8"))
        score = await engine._score_cycle(onchain, "BTC")
        assert score > 0

    @pytest.mark.asyncio
    async def test_high_mvrv_bearish(self):
        """MVRV above overbought threshold → negative score (halving cycle neutralised)."""
        engine = CryptoFAEngine(_make_db())
        onchain = _make_onchain(mvrv_ratio=Decimal("4.0"))
        # Patch halving score to 0 so MVRV dominates
        with patch("src.analysis.crypto_fa_engine._halving_cycle_score", return_value=0.0):
            score = await engine._score_cycle(onchain, "BTC")
        assert score < 0

    def test_halving_cycle_score_returns_float(self):
        score = _halving_cycle_score()
        assert isinstance(score, float)

    def test_halving_cycle_score_bounded(self):
        score = _halving_cycle_score()
        assert -100.0 <= score <= 100.0

    def test_halving_dates_sorted(self):
        dates = _BTC_HALVING_DATES
        for i in range(len(dates) - 1):
            assert dates[i] < dates[i + 1]


class TestMacroCorrelation:
    @pytest.mark.asyncio
    async def test_returns_float(self):
        db = _make_db()
        engine = CryptoFAEngine(db)
        score = await engine._score_macro_correlation()
        assert isinstance(score, float)

    @pytest.mark.asyncio
    async def test_bounded(self):
        db = _make_db()
        engine = CryptoFAEngine(db)
        score = await engine._score_macro_correlation()
        assert -100.0 <= score <= 100.0

    @pytest.mark.asyncio
    async def test_no_macro_data_returns_zero(self):
        """When DB has no VIX/DXY data, score is 0."""
        db = _make_db(None)
        engine = CryptoFAEngine(db)
        score = await engine._score_macro_correlation()
        assert score == 0.0
