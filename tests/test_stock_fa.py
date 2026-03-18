from typing import Dict, Optional
"""Tests for src.analysis.stock_fa_engine."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.analysis.stock_fa_engine import StockFAEngine, _WEIGHTS, _SECTOR_PE


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_fundamentals(**kwargs):
    f = MagicMock()
    f.pe_ratio = kwargs.get("pe_ratio", Decimal("20"))
    f.eps = kwargs.get("eps", Decimal("5.0"))
    f.revenue_growth_yoy = kwargs.get("revenue_growth_yoy", Decimal("10.0"))
    f.gross_margin = kwargs.get("gross_margin", Decimal("40.0"))
    f.net_margin = kwargs.get("net_margin", Decimal("15.0"))
    f.debt_to_equity = kwargs.get("debt_to_equity", Decimal("0.5"))
    f.roe = kwargs.get("roe", Decimal("18.0"))
    f.analyst_rating = kwargs.get("analyst_rating", "buy")
    f.analyst_target = kwargs.get("analyst_target", Decimal("200.0"))
    f.earnings_surprise_avg = kwargs.get("earnings_surprise_avg", Decimal("5.0"))
    f.insider_net_shares = kwargs.get("insider_net_shares", 100_000)
    return f


def _make_db(fundamentals=None) -> AsyncMock:
    db = AsyncMock()

    async def _execute(stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = fundamentals
        return result

    db.execute = _execute
    return db


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestStockFAEngineInit:
    def test_instantiate(self):
        db = _make_db()
        engine = StockFAEngine(db)
        assert engine is not None

    def test_weights_sum_to_one(self):
        assert abs(sum(_WEIGHTS.values()) - 1.0) < 0.01


class TestCalculateStockFAScore:
    @pytest.mark.asyncio
    async def test_returns_dict_with_required_keys(self):
        db = _make_db(_make_fundamentals())
        engine = StockFAEngine(db)
        result = await engine.calculate_stock_fa_score(1, "Technology")
        assert "score" in result
        assert "components" in result
        assert "data" in result

    @pytest.mark.asyncio
    async def test_score_bounded(self):
        db = _make_db(_make_fundamentals())
        engine = StockFAEngine(db)
        result = await engine.calculate_stock_fa_score(1, "Technology")
        assert -100.0 <= result["score"] <= 100.0

    @pytest.mark.asyncio
    async def test_no_fundamentals_returns_zero(self):
        db = _make_db(None)
        engine = StockFAEngine(db)
        result = await engine.calculate_stock_fa_score(1)
        assert result["score"] == 0.0

    @pytest.mark.asyncio
    async def test_buy_rating_positive_contribution(self):
        f = _make_fundamentals(analyst_rating="buy")
        db = _make_db(f)
        engine = StockFAEngine(db)
        result = await engine.calculate_stock_fa_score(1, "Technology")
        assert result["components"]["analyst"] > 0

    @pytest.mark.asyncio
    async def test_sell_rating_negative_contribution(self):
        f = _make_fundamentals(analyst_rating="sell")
        db = _make_db(f)
        engine = StockFAEngine(db)
        result = await engine.calculate_stock_fa_score(1, "Technology")
        assert result["components"]["analyst"] < 0

    @pytest.mark.asyncio
    async def test_strong_buy_higher_than_buy(self):
        f_buy = _make_fundamentals(analyst_rating="buy")
        f_sbuy = _make_fundamentals(analyst_rating="strong_buy")
        engine_buy = StockFAEngine(_make_db(f_buy))
        engine_sbuy = StockFAEngine(_make_db(f_sbuy))
        r_buy = await engine_buy.calculate_stock_fa_score(1)
        r_sbuy = await engine_sbuy.calculate_stock_fa_score(1)
        assert r_sbuy["components"]["analyst"] > r_buy["components"]["analyst"]

    @pytest.mark.asyncio
    async def test_positive_earnings_surprise(self):
        f = _make_fundamentals(earnings_surprise_avg=Decimal("8.0"))
        db = _make_db(f)
        engine = StockFAEngine(db)
        result = await engine.calculate_stock_fa_score(1)
        assert result["components"]["earnings_surprise"] > 0

    @pytest.mark.asyncio
    async def test_negative_earnings_surprise(self):
        f = _make_fundamentals(earnings_surprise_avg=Decimal("-8.0"))
        db = _make_db(f)
        engine = StockFAEngine(db)
        result = await engine.calculate_stock_fa_score(1)
        assert result["components"]["earnings_surprise"] < 0

    @pytest.mark.asyncio
    async def test_insider_buying_positive(self):
        f = _make_fundamentals(insider_net_shares=400_000)
        db = _make_db(f)
        engine = StockFAEngine(db)
        result = await engine.calculate_stock_fa_score(1)
        assert result["components"]["insider"] > 0

    @pytest.mark.asyncio
    async def test_insider_selling_negative(self):
        f = _make_fundamentals(insider_net_shares=-400_000)
        db = _make_db(f)
        engine = StockFAEngine(db)
        result = await engine.calculate_stock_fa_score(1)
        assert result["components"]["insider"] < 0


class TestScoringMethods:
    def setup_method(self):
        self.engine = StockFAEngine(_make_db())

    def test_valuation_cheap_positive(self):
        f = _make_fundamentals(pe_ratio=Decimal("10"))  # below Tech sector avg of 28
        score = self.engine._score_valuation(f, "Technology")
        assert score > 0

    def test_valuation_expensive_negative(self):
        f = _make_fundamentals(pe_ratio=Decimal("60"))  # way above sector avg
        score = self.engine._score_valuation(f, "Technology")
        assert score < 0

    def test_valuation_no_fundamentals(self):
        score = self.engine._score_valuation(None, "Technology")
        assert score == 0.0

    def test_valuation_negative_pe(self):
        f = _make_fundamentals(pe_ratio=Decimal("-5"))
        score = self.engine._score_valuation(f, "Technology")
        assert score < 0

    def test_earnings_score_with_growth(self):
        f = _make_fundamentals(revenue_growth_yoy=Decimal("15"), net_margin=Decimal("20"))
        score = self.engine._score_earnings(f)
        assert score > 0

    def test_earnings_score_with_decline(self):
        f = _make_fundamentals(revenue_growth_yoy=Decimal("-15"), net_margin=Decimal("-10"))
        score = self.engine._score_earnings(f)
        assert score < 0

    def test_analyst_hold_zero(self):
        f = _make_fundamentals(analyst_rating="hold")
        score = self.engine._score_analyst(f)
        assert score == 0.0

    def test_analyst_unknown_rating(self):
        f = _make_fundamentals(analyst_rating="neutral")
        score = self.engine._score_analyst(f)
        assert score == 0.0

    def test_insider_capped(self):
        f = _make_fundamentals(insider_net_shares=10_000_000)
        score = self.engine._score_insider(f)
        assert score == 100.0

    def test_insider_capped_negative(self):
        f = _make_fundamentals(insider_net_shares=-10_000_000)
        score = self.engine._score_insider(f)
        assert score == -100.0


class TestAuxiliaryMethods:
    @pytest.mark.asyncio
    async def test_get_company_metrics_none_when_no_data(self):
        engine = StockFAEngine(_make_db(None))
        result = await engine.get_company_metrics(1)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_company_metrics_returns_dict(self):
        engine = StockFAEngine(_make_db(_make_fundamentals()))
        result = await engine.get_company_metrics(1)
        assert isinstance(result, dict)
        assert "pe_ratio" in result

    @pytest.mark.asyncio
    async def test_get_analyst_consensus_none_when_no_data(self):
        engine = StockFAEngine(_make_db(None))
        result = await engine.get_analyst_consensus(1)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_analyst_consensus_returns_dict(self):
        engine = StockFAEngine(_make_db(_make_fundamentals()))
        result = await engine.get_analyst_consensus(1)
        assert result is not None
        assert "rating" in result

    @pytest.mark.asyncio
    async def test_get_earnings_surprise_returns_float(self):
        engine = StockFAEngine(_make_db(_make_fundamentals(earnings_surprise_avg=Decimal("3.5"))))
        result = await engine.get_earnings_surprise(1)
        assert result == pytest.approx(3.5)

    @pytest.mark.asyncio
    async def test_get_earnings_surprise_none_when_no_data(self):
        engine = StockFAEngine(_make_db(None))
        result = await engine.get_earnings_surprise(1)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_insider_activity_returns_int(self):
        engine = StockFAEngine(_make_db(_make_fundamentals(insider_net_shares=50_000)))
        result = await engine.get_insider_activity(1)
        assert result == 50_000
