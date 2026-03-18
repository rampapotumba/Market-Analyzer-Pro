"""Tests for src.collectors.social_collector."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.social_collector import SocialCollector


class TestSocialCollector:
    def test_instantiate(self):
        collector = SocialCollector()
        assert collector is not None

    @pytest.mark.asyncio
    async def test_health_check_success(self):
        collector = SocialCollector()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
            result = await collector.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self):
        collector = SocialCollector()
        with patch("httpx.AsyncClient.get", side_effect=Exception("timeout")):
            result = await collector.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_fetch_fear_greed_cached(self):
        collector = SocialCollector()
        with patch("src.collectors.social_collector.cache.get", new=AsyncMock(return_value="65.5")):
            val = await collector._fetch_fear_greed()
        assert val == pytest.approx(65.5)

    @pytest.mark.asyncio
    async def test_fetch_fear_greed_network_error_returns_none(self):
        from src.cache import cache
        await cache.delete("social:fear_greed")
        collector = SocialCollector()
        with patch("httpx.AsyncClient.get", side_effect=Exception("network error")):
            val = await collector._fetch_fear_greed()
        assert val is None

    @pytest.mark.asyncio
    async def test_fetch_fear_greed_parses_api(self):
        from src.cache import cache
        await cache.delete("social:fear_greed")
        collector = SocialCollector()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: {"data": [{"value": "75"}]}
        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
            val = await collector._fetch_fear_greed()
        assert val == pytest.approx(75.0)
        await cache.delete("social:fear_greed")

    @pytest.mark.asyncio
    async def test_fetch_pcr_network_error_returns_none(self):
        collector = SocialCollector()
        async with __import__("httpx").AsyncClient() as client:
            with patch.object(client, "get", side_effect=Exception("timeout")):
                val = await collector._fetch_pcr("AAPL", client)
        assert val is None

    @pytest.mark.asyncio
    async def test_fetch_stocktwits_network_error_returns_none(self):
        collector = SocialCollector()
        async with __import__("httpx").AsyncClient() as client:
            with patch.object(client, "get", side_effect=Exception("timeout")):
                val = await collector._fetch_stocktwits("AAPL", client)
        assert val is None

    @pytest.mark.asyncio
    async def test_fetch_stocktwits_parses_messages(self):
        """Bullish messages → positive score."""
        collector = SocialCollector()
        messages = [
            {"entities": {"sentiment": {"basic": "Bullish"}}} for _ in range(3)
        ] + [
            {"entities": {"sentiment": {"basic": "Bearish"}}} for _ in range(1)
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: {"messages": messages}

        async with __import__("httpx").AsyncClient() as client:
            with patch.object(client, "get", new=AsyncMock(return_value=mock_resp)):
                result = await collector._fetch_stocktwits("AAPL", client)

        assert result is not None
        assert result["score"] > 0
        assert result["bullish_pct"] == pytest.approx(75.0)
