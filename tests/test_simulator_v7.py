"""Tests for Trade Simulator v7 — TASK-V7-04: GeoEngineV2 GDELT fixes.

Covers:
  - test_v7_04_gdelt_symbol_format
  - test_v7_04_gdelt_fallback_on_empty
  - test_v7_04_gdelt_circuit_breaker
  - test_v7_04_geo_score_calculation
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.analysis.geo_engine_v2 import (
    GeoEngineV2,
    _COUNTRY_INSTRUMENTS,
    _CB_FAIL_THRESHOLD,
    _GDELT_CACHE_TTL,
    _symbol_to_countries,
    _tone_to_score,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_artlist_response(tones: list[float]) -> dict[str, Any]:
    """Build a fake GDELT artlist JSON response with the given tone values."""
    articles = [
        {
            "title": f"Article {i}",
            "url": f"https://example.com/{i}",
            "seendate": "20240101T120000Z",
            "tone": f"{t},0,0,0,0,0,0",  # GDELT tone CSV: first field is avg tone
        }
        for i, t in enumerate(tones)
    ]
    return {"articles": articles}


def _make_empty_response() -> dict[str, Any]:
    return {"articles": []}


# ── Symbol format tests ────────────────────────────────────────────────────────

class TestV7GdeltSymbolFormat:
    """TASK-V7-04: Verify _COUNTRY_INSTRUMENTS uses correct symbol format."""

    def test_v7_04_gdelt_symbol_format_us_contains_suffixed_symbols(self) -> None:
        """US instruments must use =X / GC=F format, not bare 'EURUSD'."""
        us_syms = _COUNTRY_INSTRUMENTS["US"]
        # Symbols with =X suffix
        assert "EURUSD=X" in us_syms
        assert "USDJPY=X" in us_syms
        assert "GBPUSD=X" in us_syms
        # Gold futures
        assert "GC=F" in us_syms

    def test_v7_04_gdelt_symbol_format_no_bare_forex(self) -> None:
        """Bare forex symbols like 'EURUSD' must NOT appear in the mapping."""
        for country, symbols in _COUNTRY_INSTRUMENTS.items():
            for sym in symbols:
                # Bare forex pairs (4-6 letter all-alpha) should not exist
                stripped = sym.replace("/", "").replace("=X", "").replace("=F", "")
                # If it looks like a forex pair and has no suffix, that's a bug
                is_bare_forex = (
                    stripped.isalpha()
                    and 6 <= len(stripped) <= 8
                    and sym == stripped  # no suffix was removed
                    and "SPY" not in sym
                    and "QQQ" not in sym
                )
                assert not is_bare_forex, (
                    f"Bare forex symbol '{sym}' found for country '{country}'. "
                    "Should use '=X' suffix."
                )

    def test_v7_04_gdelt_symbol_format_eu_instruments(self) -> None:
        eu_syms = _COUNTRY_INSTRUMENTS["EU"]
        assert "EURUSD=X" in eu_syms
        assert "EURJPY=X" in eu_syms
        assert "EURGBP=X" in eu_syms

    def test_v7_04_gdelt_symbol_format_uk_instruments(self) -> None:
        uk_syms = _COUNTRY_INSTRUMENTS["UK"]
        assert "GBPUSD=X" in uk_syms
        assert "EURGBP=X" in uk_syms

    def test_v7_04_gdelt_symbol_to_countries_eurusd_x(self) -> None:
        """_symbol_to_countries should resolve 'EURUSD=X' to US and EU."""
        countries = _symbol_to_countries("EURUSD=X")
        assert "US" in countries
        assert "EU" in countries

    def test_v7_04_gdelt_symbol_to_countries_btc(self) -> None:
        """BTC/USDT should resolve to US and CN."""
        countries = _symbol_to_countries("BTC/USDT")
        assert "US" in countries
        assert "CN" in countries

    def test_v7_04_gdelt_symbol_to_countries_gold(self) -> None:
        """GC=F (Gold) should resolve to RU and ME."""
        countries = _symbol_to_countries("GC=F")
        assert "RU" in countries or "ME" in countries

    def test_v7_04_gdelt_symbol_to_countries_unknown_returns_empty(self) -> None:
        assert _symbol_to_countries("UNKNOWN_SYMBOL") == []


# ── Fallback query tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestV7GdeltFallback:
    """TASK-V7-04: Fallback query activates when primary returns empty."""

    async def test_v7_04_gdelt_fallback_on_empty_primary(self) -> None:
        """When primary query returns empty articles, fallback query is attempted."""
        engine = GeoEngineV2()

        # Mock cache: always miss
        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock(return_value=True)
        mock_cache.delete = AsyncMock(return_value=True)

        # Primary returns empty; fallback returns tones
        fallback_response = _make_artlist_response([-2.0, -1.5])

        call_count = 0

        async def fake_get(url: str, *, params: dict, timeout: float) -> MagicMock:
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            if call_count == 1:
                mock_resp.json = MagicMock(return_value=_make_empty_response())
            else:
                mock_resp.json = MagicMock(return_value=fallback_response)
            return mock_resp

        with (
            patch("src.analysis.geo_engine_v2.cache", mock_cache),
            patch.object(engine._client, "get", side_effect=fake_get),
        ):
            tone = await engine.fetch_gdelt_tone("US")

        # Two requests must have been made (primary + fallback)
        assert call_count == 2
        # Tone should be average of fallback tones
        assert tone is not None
        assert abs(tone - (-1.75)) < 0.01

        await engine.close()

    async def test_v7_04_gdelt_no_fallback_when_primary_succeeds(self) -> None:
        """When primary returns articles, fallback is NOT called."""
        engine = GeoEngineV2()

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock(return_value=True)
        mock_cache.delete = AsyncMock(return_value=True)

        primary_response = _make_artlist_response([1.0, 2.0])
        call_count = 0

        async def fake_get(url: str, *, params: dict, timeout: float) -> MagicMock:
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = MagicMock(return_value=primary_response)
            return mock_resp

        with (
            patch("src.analysis.geo_engine_v2.cache", mock_cache),
            patch.object(engine._client, "get", side_effect=fake_get),
        ):
            tone = await engine.fetch_gdelt_tone("UK")

        assert call_count == 1
        assert tone is not None
        assert abs(tone - 1.5) < 0.01

        await engine.close()

    async def test_v7_04_gdelt_fallback_also_empty_returns_none(self) -> None:
        """When both primary and fallback return empty, tone is None."""
        engine = GeoEngineV2()

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock(return_value=True)
        mock_cache.delete = AsyncMock(return_value=True)

        async def fake_get(url: str, *, params: dict, timeout: float) -> MagicMock:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = MagicMock(return_value=_make_empty_response())
            return mock_resp

        with (
            patch("src.analysis.geo_engine_v2.cache", mock_cache),
            patch.object(engine._client, "get", side_effect=fake_get),
        ):
            tone = await engine.fetch_gdelt_tone("US")

        assert tone is None

        await engine.close()


# ── Circuit breaker tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestV7GdeltCircuitBreaker:
    """TASK-V7-04: Circuit breaker trips after 3 consecutive failures."""

    async def test_v7_04_gdelt_circuit_breaker_trips_after_threshold(self) -> None:
        """After CB_FAIL_THRESHOLD consecutive failures, circuit opens."""
        engine = GeoEngineV2()

        # Simulate in-memory cache counters
        in_memory: dict[str, Any] = {}

        async def fake_cache_get(key: str) -> Any:
            return in_memory.get(key)

        async def fake_cache_set(key: str, value: Any, ttl: int = 300) -> bool:
            in_memory[key] = value
            return True

        async def fake_cache_delete(key: str) -> bool:
            in_memory.pop(key, None)
            return True

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(side_effect=fake_cache_get)
        mock_cache.set = AsyncMock(side_effect=fake_cache_set)
        mock_cache.delete = AsyncMock(side_effect=fake_cache_delete)

        # Both primary and fallback always return empty → failure recorded
        async def fake_get(url: str, *, params: dict, timeout: float) -> MagicMock:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = MagicMock(return_value=_make_empty_response())
            return mock_resp

        with (
            patch("src.analysis.geo_engine_v2.cache", mock_cache),
            patch.object(engine._client, "get", side_effect=fake_get),
        ):
            # First CB_FAIL_THRESHOLD - 1 calls: circuit should still be closed
            for _ in range(_CB_FAIL_THRESHOLD - 1):
                await engine.fetch_gdelt_tone("EU")

            tripped_key = "geo:cb:tripped:EU"
            assert in_memory.get(tripped_key) is None, "Circuit should not be tripped yet"

            # One more failure — circuit trips
            await engine.fetch_gdelt_tone("EU")
            assert in_memory.get(tripped_key) == "1", "Circuit should be tripped now"

        await engine.close()

    async def test_v7_04_gdelt_circuit_breaker_skips_when_open(self) -> None:
        """When circuit is open, fetch_gdelt_tone returns None immediately."""
        engine = GeoEngineV2()

        call_count = 0
        in_memory: dict[str, Any] = {"geo:cb:tripped:JP": "1"}

        async def fake_cache_get(key: str) -> Any:
            return in_memory.get(key)

        async def fake_cache_set(key: str, value: Any, ttl: int = 300) -> bool:
            in_memory[key] = value
            return True

        async def fake_cache_delete(key: str) -> bool:
            in_memory.pop(key, None)
            return True

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(side_effect=fake_cache_get)
        mock_cache.set = AsyncMock(side_effect=fake_cache_set)
        mock_cache.delete = AsyncMock(side_effect=fake_cache_delete)

        async def fake_get(url: str, *, params: dict, timeout: float) -> MagicMock:
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = MagicMock(return_value=_make_artlist_response([1.0]))
            return mock_resp

        with (
            patch("src.analysis.geo_engine_v2.cache", mock_cache),
            patch.object(engine._client, "get", side_effect=fake_get),
        ):
            tone = await engine.fetch_gdelt_tone("JP")

        # No HTTP calls should have been made
        assert call_count == 0
        assert tone is None

        await engine.close()

    async def test_v7_04_gdelt_circuit_breaker_resets_on_success(self) -> None:
        """Successful response resets the failure counter."""
        engine = GeoEngineV2()

        in_memory: dict[str, Any] = {"geo:cb:fail:CN": "2"}  # 2 prior failures

        async def fake_cache_get(key: str) -> Any:
            return in_memory.get(key)

        async def fake_cache_set(key: str, value: Any, ttl: int = 300) -> bool:
            in_memory[key] = value
            return True

        async def fake_cache_delete(key: str) -> bool:
            in_memory.pop(key, None)
            return True

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(side_effect=fake_cache_get)
        mock_cache.set = AsyncMock(side_effect=fake_cache_set)
        mock_cache.delete = AsyncMock(side_effect=fake_cache_delete)

        async def fake_get(url: str, *, params: dict, timeout: float) -> MagicMock:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = MagicMock(return_value=_make_artlist_response([-1.0]))
            return mock_resp

        with (
            patch("src.analysis.geo_engine_v2.cache", mock_cache),
            patch.object(engine._client, "get", side_effect=fake_get),
        ):
            tone = await engine.fetch_gdelt_tone("CN")

        assert tone is not None
        # Failure counter must be removed after success
        assert in_memory.get("geo:cb:fail:CN") is None

        await engine.close()


# ── Geo score calculation tests ────────────────────────────────────────────────

@pytest.mark.asyncio
class TestV7GeoScoreCalculation:
    """TASK-V7-04: Geo score is computed correctly from mocked GDELT responses."""

    async def test_v7_04_geo_score_calculation_in_range(self) -> None:
        """Score must be in [-50, +50] range for any tone input."""
        engine = GeoEngineV2()

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock(return_value=True)
        mock_cache.delete = AsyncMock(return_value=True)

        # Very negative tones → should clamp to -50 (not -100)
        async def fake_get(url: str, *, params: dict, timeout: float) -> MagicMock:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = MagicMock(
                return_value=_make_artlist_response([-10.0, -8.0, -9.0])
            )
            return mock_resp

        with (
            patch("src.analysis.geo_engine_v2.cache", mock_cache),
            patch.object(engine._client, "get", side_effect=fake_get),
        ):
            result = await engine.calculate_geopolitical_risk("EURUSD=X")

        assert -50.0 <= result <= 50.0

        await engine.close()

    async def test_v7_04_geo_score_calculation_positive_tone(self) -> None:
        """Positive tones yield a positive score."""
        engine = GeoEngineV2()

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock(return_value=True)
        mock_cache.delete = AsyncMock(return_value=True)

        async def fake_get(url: str, *, params: dict, timeout: float) -> MagicMock:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = MagicMock(
                return_value=_make_artlist_response([2.0, 3.0, 2.5])
            )
            return mock_resp

        with (
            patch("src.analysis.geo_engine_v2.cache", mock_cache),
            patch.object(engine._client, "get", side_effect=fake_get),
        ):
            result = await engine.calculate_geopolitical_risk("EURUSD=X")

        assert result > 0.0
        assert result <= 50.0

        await engine.close()

    async def test_v7_04_geo_score_calculation_negative_tone(self) -> None:
        """Strongly negative tones yield a negative score."""
        engine = GeoEngineV2()

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock(return_value=True)
        mock_cache.delete = AsyncMock(return_value=True)

        async def fake_get(url: str, *, params: dict, timeout: float) -> MagicMock:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = MagicMock(
                return_value=_make_artlist_response([-5.0, -4.0])
            )
            return mock_resp

        with (
            patch("src.analysis.geo_engine_v2.cache", mock_cache),
            patch.object(engine._client, "get", side_effect=fake_get),
        ):
            result = await engine.calculate_geopolitical_risk("GBPUSD=X")

        assert result < 0.0
        assert result >= -50.0

        await engine.close()

    async def test_v7_04_geo_score_calculation_no_data_returns_zero(self) -> None:
        """When no GDELT data available, score must be 0 (graceful degradation)."""
        engine = GeoEngineV2()

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock(return_value=True)
        mock_cache.delete = AsyncMock(return_value=True)

        async def fake_get(url: str, *, params: dict, timeout: float) -> MagicMock:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = MagicMock(return_value=_make_empty_response())
            return mock_resp

        with (
            patch("src.analysis.geo_engine_v2.cache", mock_cache),
            patch.object(engine._client, "get", side_effect=fake_get),
        ):
            result = await engine.calculate_geopolitical_risk("EURUSD=X")

        assert result == 0.0

        await engine.close()

    async def test_v7_04_geo_score_unknown_symbol_returns_zero(self) -> None:
        """Unknown symbol with no country mapping returns 0."""
        engine = GeoEngineV2()
        result = await engine.calculate_geopolitical_risk("UNKNOWN_SYM=X")
        assert result == 0.0
        await engine.close()

    async def test_v7_04_geo_score_gdelt_exception_returns_zero(self) -> None:
        """Exception during GDELT fetch → score() returns 0 (not raising)."""
        engine = GeoEngineV2()

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock(return_value=True)
        mock_cache.delete = AsyncMock(return_value=True)

        async def fake_get(url: str, *, params: dict, timeout: float) -> MagicMock:
            raise httpx.TimeoutException("timeout")

        import httpx  # noqa: PLC0415 — needed for exception class in scope

        with (
            patch("src.analysis.geo_engine_v2.cache", mock_cache),
            patch.object(engine._client, "get", side_effect=fake_get),
        ):
            result = await engine.score("EURUSD=X")

        assert result == 0.0

        await engine.close()


# ── _tone_to_score unit tests ──────────────────────────────────────────────────

class TestToneToScore:
    """Unit tests for the _tone_to_score pure function."""

    def test_tone_neutral(self) -> None:
        assert _tone_to_score(0.0) == 0.0

    def test_tone_at_positive_threshold(self) -> None:
        assert _tone_to_score(3.0) == 100.0

    def test_tone_above_positive_threshold(self) -> None:
        assert _tone_to_score(5.0) == 100.0

    def test_tone_at_negative_threshold(self) -> None:
        assert _tone_to_score(-3.0) == -100.0

    def test_tone_below_negative_threshold(self) -> None:
        assert _tone_to_score(-10.0) == -100.0

    def test_tone_midpoint_positive(self) -> None:
        score = _tone_to_score(1.5)
        assert abs(score - 50.0) < 0.01

    def test_tone_midpoint_negative(self) -> None:
        score = _tone_to_score(-1.5)
        assert abs(score - (-50.0)) < 0.01
