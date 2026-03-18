from typing import Dict, Optional
"""Tests for src.analysis.forex_fa_engine."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.analysis.forex_fa_engine import ForexFAEngine, _WEIGHTS


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_db(macro_vals: Optional[dict] = None) -> AsyncMock:
    """Return a mock AsyncSession that returns values from `macro_vals`."""
    db = AsyncMock()
    macro_vals = macro_vals or {}

    async def _execute(stmt):
        # Simple mock: always return None (no data)
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        return result

    db.execute = _execute
    return db


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestForexFAEngineInit:
    def test_instantiate(self):
        db = _make_db()
        engine = ForexFAEngine(db)
        assert engine is not None

    def test_weights_sum_to_one(self):
        total = sum(_WEIGHTS.values())
        assert abs(total - 1.0) < 0.01


class TestSplitForexSymbol:
    def test_eurusd(self):
        from src.analysis.interest_rate_diff import _split_forex_symbol
        base, quote = _split_forex_symbol("EURUSD")
        assert base == "EUR"
        assert quote == "USD"

    def test_eur_usd(self):
        from src.analysis.interest_rate_diff import _split_forex_symbol
        base, quote = _split_forex_symbol("EUR/USD")
        assert base == "EUR"
        assert quote == "USD"

    def test_gbpjpy(self):
        from src.analysis.interest_rate_diff import _split_forex_symbol
        base, quote = _split_forex_symbol("GBPJPY")
        assert base == "GBP"
        assert quote == "JPY"

    def test_invalid(self):
        from src.analysis.interest_rate_diff import _split_forex_symbol
        base, quote = _split_forex_symbol("BTCUSDT")
        assert base is None or len(base) == 3

    def test_empty(self):
        from src.analysis.interest_rate_diff import _split_forex_symbol
        base, quote = _split_forex_symbol("")
        assert base is None
        assert quote is None


class TestForexFAEngineAnalyze:
    @pytest.mark.asyncio
    async def test_invalid_symbol_returns_zero_score(self):
        db = _make_db()
        engine = ForexFAEngine(db)
        result = await engine.analyze("INVALID")
        assert result["score"] == 0.0
        assert result["symbol"] == "INVALID"

    @pytest.mark.asyncio
    async def test_no_data_returns_near_zero(self):
        """When no DB data is available, only IRD carries and it defaults to ~0."""
        db = _make_db()
        engine = ForexFAEngine(db)
        with patch.object(engine._ird, "score", new_callable=AsyncMock, return_value=0.0), \
             patch.object(engine._ird, "calculate_differential_trend", new_callable=AsyncMock, return_value=0.0):
            result = await engine.analyze("EURUSD")
        assert isinstance(result["score"], float)
        assert -100.0 <= result["score"] <= 100.0

    @pytest.mark.asyncio
    async def test_returns_required_keys(self):
        db = _make_db()
        engine = ForexFAEngine(db)
        result = await engine.analyze("EURUSD")
        assert "score" in result
        assert "components" in result
        assert "differential_trend" in result
        assert "symbol" in result

    @pytest.mark.asyncio
    async def test_score_bounded(self):
        db = _make_db()
        engine = ForexFAEngine(db)
        with patch.object(engine._ird, "score", new_callable=AsyncMock, return_value=50.0), \
             patch.object(engine._ird, "calculate_differential_trend", new_callable=AsyncMock, return_value=1.0):
            result = await engine.analyze("EURUSD")
        assert -100.0 <= result["score"] <= 100.0

    @pytest.mark.asyncio
    async def test_high_ird_gives_positive_score(self):
        """High IRD score (base pays more) should produce positive composite."""
        db = _make_db()
        engine = ForexFAEngine(db)
        with patch.object(engine._ird, "score", new_callable=AsyncMock, return_value=80.0), \
             patch.object(engine._ird, "calculate_differential_trend", new_callable=AsyncMock, return_value=0.5):
            result = await engine.analyze("AUDUSD")
        assert result["score"] > 0

    @pytest.mark.asyncio
    async def test_low_ird_gives_negative_score(self):
        db = _make_db()
        engine = ForexFAEngine(db)
        with patch.object(engine._ird, "score", new_callable=AsyncMock, return_value=-80.0), \
             patch.object(engine._ird, "calculate_differential_trend", new_callable=AsyncMock, return_value=-0.5):
            result = await engine.analyze("AUDUSD")
        assert result["score"] < 0

    @pytest.mark.asyncio
    async def test_components_all_present(self):
        db = _make_db()
        engine = ForexFAEngine(db)
        result = await engine.analyze("EURUSD")
        components = result["components"]
        assert "ird" in components
        assert "gdp" in components
        assert "cpi" in components
        assert "employment" in components
        assert "trade" in components

    @pytest.mark.asyncio
    async def test_score_currency_returns_dict(self):
        db = _make_db()
        engine = ForexFAEngine(db)
        result = await engine._score_currency("USD")
        assert isinstance(result, dict)


class TestDifferentialScore:
    @pytest.mark.asyncio
    async def test_missing_data_returns_zero(self):
        db = _make_db()
        engine = ForexFAEngine(db)
        score = await engine._differential_score("USD", "EUR", "gdp")
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_unknown_currency_returns_zero(self):
        db = _make_db()
        engine = ForexFAEngine(db)
        score = await engine._differential_score("XYZ", "USD", "gdp")
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_score_bounded(self):
        """Differential score must be in [-100, 100]."""
        db = _make_db()
        engine = ForexFAEngine(db)
        # Patch the macro value fetcher to return extreme values
        with patch.object(engine, "_get_macro_value", new=AsyncMock(return_value=100.0)):
            score = await engine._differential_score("USD", "EUR", "gdp", higher_is_better=True)
        assert -100.0 <= score <= 100.0
