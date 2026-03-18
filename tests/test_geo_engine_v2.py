"""Tests for src.analysis.geo_engine_v2."""

from unittest.mock import AsyncMock, patch

import pytest

from src.analysis.geo_engine_v2 import (
    GeoEngineV2,
    _symbol_to_countries,
    _tone_to_score,
)


# ── _tone_to_score ────────────────────────────────────────────────────────────

class TestToneToScore:
    def test_neutral_zero(self):
        assert _tone_to_score(0.0) == 0.0

    def test_very_positive_returns_100(self):
        assert _tone_to_score(5.0) == 100.0

    def test_very_negative_returns_minus_100(self):
        assert _tone_to_score(-5.0) == -100.0

    def test_positive_tone_positive_score(self):
        assert _tone_to_score(1.5) > 0

    def test_negative_tone_negative_score(self):
        assert _tone_to_score(-1.5) < 0

    def test_bounded_positive(self):
        assert _tone_to_score(100.0) <= 100.0

    def test_bounded_negative(self):
        assert _tone_to_score(-100.0) >= -100.0

    def test_monotonic_positive(self):
        scores = [_tone_to_score(x) for x in [0.5, 1.0, 2.0, 3.0]]
        assert scores == sorted(scores)

    def test_monotonic_negative(self):
        scores = [_tone_to_score(x) for x in [-3.0, -2.0, -1.0, -0.5]]
        assert scores == sorted(scores)


# ── _symbol_to_countries ──────────────────────────────────────────────────────

class TestSymbolToCountries:
    def test_eurusd_maps_countries(self):
        countries = _symbol_to_countries("EURUSD")
        assert len(countries) > 0

    def test_unknown_symbol_empty(self):
        countries = _symbol_to_countries("XYZABC")
        assert countries == []

    def test_btc_usdt_maps_to_crypto(self):
        countries = _symbol_to_countries("BTC/USDT")
        assert len(countries) > 0

    def test_case_insensitive(self):
        assert _symbol_to_countries("eurusd") == _symbol_to_countries("EURUSD")


# ── GeoEngineV2 ───────────────────────────────────────────────────────────────

class TestGeoEngineV2:
    def test_instantiate(self):
        engine = GeoEngineV2()
        assert engine is not None

    @pytest.mark.asyncio
    async def test_score_unknown_symbol_returns_zero(self):
        engine = GeoEngineV2()
        score = await engine.score("XYZABC")
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_score_returns_float(self):
        engine = GeoEngineV2()
        with patch.object(engine, "fetch_gdelt_tone", new=AsyncMock(return_value=0.0)):
            score = await engine.score("EURUSD")
        assert isinstance(score, float)

    @pytest.mark.asyncio
    async def test_score_bounded(self):
        engine = GeoEngineV2()
        with patch.object(engine, "fetch_gdelt_tone", new=AsyncMock(return_value=-5.0)):
            score = await engine.score("EURUSD")
        assert -100.0 <= score <= 100.0

    @pytest.mark.asyncio
    async def test_negative_tone_bearish_score(self):
        engine = GeoEngineV2()
        with patch.object(engine, "fetch_gdelt_tone", new=AsyncMock(return_value=-4.0)):
            score = await engine.score("EURUSD")
        assert score < 0

    @pytest.mark.asyncio
    async def test_positive_tone_bullish_score(self):
        engine = GeoEngineV2()
        with patch.object(engine, "fetch_gdelt_tone", new=AsyncMock(return_value=4.0)):
            score = await engine.score("EURUSD")
        assert score > 0

    @pytest.mark.asyncio
    async def test_gdelt_unavailable_returns_zero(self):
        engine = GeoEngineV2()
        with patch.object(engine, "fetch_gdelt_tone", new=AsyncMock(return_value=None)):
            score = await engine.score("EURUSD")
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_score_exception_returns_zero(self):
        engine = GeoEngineV2()
        with patch.object(
            engine, "calculate_geopolitical_risk", new=AsyncMock(side_effect=RuntimeError)
        ):
            score = await engine.score("EURUSD")
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_fetch_gdelt_tone_network_error_returns_none(self):
        """When HTTP fails, fetch_gdelt_tone returns None (circuit breaker)."""
        engine = GeoEngineV2()
        with patch("httpx.AsyncClient.get", side_effect=Exception("connection refused")):
            tone = await engine.fetch_gdelt_tone("US")
        assert tone is None

    @pytest.mark.asyncio
    async def test_detect_risk_events_returns_list(self):
        engine = GeoEngineV2()
        # Mock HTTP to return empty articles
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: {"articles": []}
        with patch.object(engine._client, "get", new=AsyncMock(return_value=mock_resp)):
            events = await engine.detect_risk_events("US")
        assert isinstance(events, list)

    @pytest.mark.asyncio
    async def test_detect_risk_events_with_data(self):
        engine = GeoEngineV2()
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: {
            "articles": [
                {"title": "Crisis", "url": "http://x.com", "tone": "-5.2,1.2,0.8", "seendate": "20260101"},
                {"title": "War", "url": "http://y.com", "tone": "-8.1,2.0,1.0", "seendate": "20260102"},
            ]
        }
        with patch.object(engine._client, "get", new=AsyncMock(return_value=mock_resp)):
            events = await engine.detect_risk_events("US")
        assert len(events) == 2
        assert events[0]["tone"] < 0
        assert "title" in events[0]

    @pytest.mark.asyncio
    async def test_detect_risk_events_error_returns_empty(self):
        engine = GeoEngineV2()
        with patch.object(engine._client, "get", side_effect=Exception("timeout")):
            events = await engine.detect_risk_events("US")
        assert events == []

    @pytest.mark.asyncio
    async def test_fetch_gdelt_tone_cache_hit(self):
        engine = GeoEngineV2()
        with patch("src.analysis.geo_engine_v2.cache") as mock_cache:
            mock_cache.get = AsyncMock(return_value="3.5")
            tone = await engine.fetch_gdelt_tone("US")
        assert tone == pytest.approx(3.5)

    @pytest.mark.asyncio
    async def test_fetch_gdelt_tone_parses_articles(self):
        engine = GeoEngineV2()
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: {
            "articles": [
                {"tone": "4.5,1.0,0.5"},
                {"tone": "2.5,0.8,0.2"},
            ]
        }
        with patch("src.analysis.geo_engine_v2.cache") as mock_cache:
            mock_cache.get = AsyncMock(return_value=None)
            mock_cache.set = AsyncMock()
            with patch.object(engine._client, "get", new=AsyncMock(return_value=mock_resp)):
                tone = await engine.fetch_gdelt_tone("US")
        assert tone == pytest.approx(3.5)

    @pytest.mark.asyncio
    async def test_fetch_gdelt_tone_no_articles_returns_none(self):
        engine = GeoEngineV2()
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: {"articles": []}
        with patch("src.analysis.geo_engine_v2.cache") as mock_cache:
            mock_cache.get = AsyncMock(return_value=None)
            with patch.object(engine._client, "get", new=AsyncMock(return_value=mock_resp)):
                tone = await engine.fetch_gdelt_tone("US")
        assert tone is None
