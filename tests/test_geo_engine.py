"""Tests for GeoEngine (Phase 1: neutral stub)."""

from unittest.mock import MagicMock

import pytest

from src.analysis.geo_engine import GeoEngine


class TestGeoEngine:
    """Test the Phase 1 geopolitical engine stub."""

    def test_returns_zero_without_instrument(self):
        assert GeoEngine().calculate_geo_score() == 0.0

    def test_returns_zero_with_none_instrument(self):
        assert GeoEngine(None).calculate_geo_score() == 0.0

    def test_returns_zero_with_forex_instrument(self):
        inst = MagicMock()
        inst.symbol = "EURUSD=X"
        inst.market = "forex"
        assert GeoEngine(inst).calculate_geo_score() == 0.0

    def test_returns_zero_with_crypto_instrument(self):
        inst = MagicMock()
        inst.symbol = "BTC/USDT"
        inst.market = "crypto"
        assert GeoEngine(inst).calculate_geo_score() == 0.0

    def test_score_is_float(self):
        score = GeoEngine().calculate_geo_score()
        assert isinstance(score, float)

    def test_health_check_returns_true(self):
        assert GeoEngine().health_check() is True

    @pytest.mark.asyncio
    async def test_fetch_gdelt_returns_empty_list(self):
        """Phase 1: GDELT fetch is a stub returning []."""
        result = await GeoEngine().fetch_gdelt_events("US", days_back=7)
        assert result == []
