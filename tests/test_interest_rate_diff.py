from typing import Dict, Optional
"""Tests for src.analysis.interest_rate_diff."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.analysis.interest_rate_diff import (
    InterestRateDifferential,
    _diff_to_score,
    _split_forex_symbol,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_db(rates: Optional[dict] = None) -> AsyncMock:
    """rates: currency → rate float, e.g. {'USD': 5.25, 'EUR': 4.0}"""
    rates = rates or {}
    db = AsyncMock()

    async def _execute(stmt):
        result = MagicMock()
        # We can't introspect the stmt easily in unit tests, return None
        result.scalar_one_or_none.return_value = None
        return result

    db.execute = _execute
    return db


# ── _split_forex_symbol ────────────────────────────────────────────────────────

class TestSplitForexSymbol:
    def test_eurusd(self):
        b, q = _split_forex_symbol("EURUSD")
        assert b == "EUR" and q == "USD"

    def test_slash_format(self):
        b, q = _split_forex_symbol("EUR/USD")
        assert b == "EUR" and q == "USD"

    def test_underscore_format(self):
        b, q = _split_forex_symbol("GBP_JPY")
        assert b == "GBP" and q == "JPY"

    def test_hyphen_format(self):
        b, q = _split_forex_symbol("AUD-CAD")
        assert b == "AUD" and q == "CAD"

    def test_lowercase(self):
        b, q = _split_forex_symbol("eurusd")
        assert b == "EUR" and q == "USD"

    def test_invalid_too_short(self):
        b, q = _split_forex_symbol("EUR")
        assert b is None and q is None

    def test_invalid_too_long(self):
        b, q = _split_forex_symbol("EURUSDX")
        assert b is None and q is None

    def test_empty(self):
        b, q = _split_forex_symbol("")
        assert b is None and q is None

    def test_all_pairs(self):
        pairs = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"]
        for pair in pairs:
            b, q = _split_forex_symbol(pair)
            assert b is not None and len(b) == 3
            assert q is not None and len(q) == 3


# ── _diff_to_score ────────────────────────────────────────────────────────────

class TestDiffToScore:
    def test_large_positive_returns_100(self):
        assert _diff_to_score(5.0) == 100.0

    def test_large_negative_returns_minus_100(self):
        assert _diff_to_score(-5.0) == -100.0

    def test_zero_neutral(self):
        score = _diff_to_score(0.0)
        assert abs(score) < 15.0  # near-zero

    def test_positive_diff_positive_score(self):
        assert _diff_to_score(1.5) > 0

    def test_negative_diff_negative_score(self):
        assert _diff_to_score(-1.5) < 0

    def test_bounded_upper(self):
        assert _diff_to_score(100.0) <= 100.0

    def test_bounded_lower(self):
        assert _diff_to_score(-100.0) >= -100.0

    def test_monotonic_positive(self):
        """Higher differential → higher score."""
        scores = [_diff_to_score(x) for x in [0.5, 1.0, 2.0, 3.0]]
        assert scores == sorted(scores)

    def test_monotonic_negative(self):
        scores = [_diff_to_score(x) for x in [-3.0, -2.0, -1.0, -0.5]]
        assert scores == sorted(scores)

    def test_2pp_gives_80(self):
        score = _diff_to_score(2.0)
        assert score == pytest.approx(80.0)

    def test_minus_2pp_gives_minus_80(self):
        score = _diff_to_score(-2.0)
        assert score == pytest.approx(-80.0)


# ── InterestRateDifferential ───────────────────────────────────────────────────

class TestInterestRateDifferential:
    def test_instantiate(self):
        db = _make_db()
        ird = InterestRateDifferential(db)
        assert ird is not None

    @pytest.mark.asyncio
    async def test_invalid_symbol_returns_zero(self):
        db = _make_db()
        ird = InterestRateDifferential(db)
        result = await ird.calculate_differential("INVALID")
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_no_data_returns_zero(self):
        db = _make_db()
        ird = InterestRateDifferential(db)
        result = await ird.calculate_differential("EURUSD")
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_calculate_differential_trend_no_data(self):
        db = _make_db()
        ird = InterestRateDifferential(db)
        result = await ird.calculate_differential_trend("EURUSD")
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_score_returns_float(self):
        db = _make_db()
        ird = InterestRateDifferential(db)
        result = await ird.score("EURUSD")
        assert isinstance(result, float)

    @pytest.mark.asyncio
    async def test_score_bounded(self):
        db = _make_db()
        ird = InterestRateDifferential(db)
        result = await ird.score("GBPUSD")
        assert -100.0 <= result <= 100.0

    @pytest.mark.asyncio
    async def test_get_rate_caches(self):
        """Second call to _get_rate should use in-memory cache, not DB."""
        # Use a proper AsyncMock so we can count calls
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=mock_result)

        ird = InterestRateDifferential(db)
        await ird._get_rate("USD")
        call_count_1 = db.execute.call_count
        await ird._get_rate("USD")  # should use cache
        call_count_2 = db.execute.call_count
        assert call_count_2 == call_count_1  # no additional DB call

    @pytest.mark.asyncio
    async def test_unknown_currency_no_bank(self):
        db = _make_db()
        ird = InterestRateDifferential(db)
        rate = await ird._get_rate("XYZ")
        assert rate is None

    @pytest.mark.asyncio
    async def test_all_major_currencies_have_bank_mapping(self):
        from src.analysis.interest_rate_diff import _CURRENCY_TO_BANK  # noqa
        for currency in ["USD", "EUR", "JPY", "GBP", "AUD", "CAD", "CHF", "NZD"]:
            assert currency in _CURRENCY_TO_BANK, f"{currency} missing bank mapping"
