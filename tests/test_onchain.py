"""Tests for src.collectors.onchain_collector."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.onchain_collector import OnchainCollector


class TestOnchainCollector:
    def test_instantiate(self):
        collector = OnchainCollector()
        assert collector is not None

    @pytest.mark.asyncio
    async def test_health_check_success(self):
        collector = OnchainCollector()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
            result = await collector.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self):
        collector = OnchainCollector()
        with patch("httpx.AsyncClient.get", side_effect=Exception("timeout")):
            result = await collector.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_fetch_btc_dominance_cached(self):
        collector = OnchainCollector()
        with patch(
            "src.collectors.onchain_collector.cache.get",
            new=AsyncMock(return_value="58.5"),
        ):
            val = await collector._fetch_btc_dominance()
        assert val == pytest.approx(58.5)

    @pytest.mark.asyncio
    async def test_fetch_btc_dominance_parses_coingecko(self):
        from src.cache import cache
        await cache.delete("onchain:btc_dominance")

        collector = OnchainCollector()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: {
            "data": {"market_cap_percentage": {"btc": 52.34}}
        }
        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
            val = await collector._fetch_btc_dominance()
        assert val == pytest.approx(52.34)
        await cache.delete("onchain:btc_dominance")

    @pytest.mark.asyncio
    async def test_fetch_btc_dominance_network_error_returns_none(self):
        from src.cache import cache
        await cache.delete("onchain:btc_dominance")

        collector = OnchainCollector()
        with patch("httpx.AsyncClient.get", side_effect=Exception("network error")):
            val = await collector._fetch_btc_dominance()
        assert val is None

    @pytest.mark.asyncio
    async def test_fetch_glassnode_no_api_key(self):
        """Without GLASSNODE_API_KEY, returns None immediately."""
        collector = OnchainCollector()
        async with __import__("httpx").AsyncClient() as client:
            with patch(
                "src.collectors.onchain_collector.cache.get",
                new=AsyncMock(return_value=None),
            ):
                with patch(
                    "src.collectors.onchain_collector.settings"
                ) as mock_settings:
                    mock_settings.GLASSNODE_API_KEY = None
                    val = await collector._fetch_glassnode("BTC", "market/mvrv", client)
        assert val is None

    @pytest.mark.asyncio
    async def test_fetch_glassnode_cached(self):
        collector = OnchainCollector()
        async with __import__("httpx").AsyncClient() as client:
            with patch(
                "src.collectors.onchain_collector.cache.get",
                new=AsyncMock(return_value="2.15"),
            ):
                val = await collector._fetch_glassnode("BTC", "market/mvrv", client)
        assert val == pytest.approx(2.15)

    @pytest.mark.asyncio
    async def test_fetch_glassnode_parses_response(self):
        collector = OnchainCollector()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: [{"t": 1700000000, "v": 3.14}]

        async with __import__("httpx").AsyncClient() as client:
            with patch(
                "src.collectors.onchain_collector.cache.get",
                new=AsyncMock(return_value=None),
            ):
                with patch(
                    "src.collectors.onchain_collector.settings"
                ) as mock_settings:
                    mock_settings.GLASSNODE_API_KEY = "test_key"
                    with patch.object(
                        client, "get", new=AsyncMock(return_value=mock_resp)
                    ):
                        with patch(
                            "src.collectors.onchain_collector.cache.set",
                            new=AsyncMock(),
                        ):
                            val = await collector._fetch_glassnode(
                                "BTC", "market/mvrv", client
                            )
        assert val == pytest.approx(3.14)

    @pytest.mark.asyncio
    async def test_fetch_glassnode_empty_response_returns_none(self):
        collector = OnchainCollector()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: []

        async with __import__("httpx").AsyncClient() as client:
            with patch(
                "src.collectors.onchain_collector.cache.get",
                new=AsyncMock(return_value=None),
            ):
                with patch(
                    "src.collectors.onchain_collector.settings"
                ) as mock_settings:
                    mock_settings.GLASSNODE_API_KEY = "test_key"
                    with patch.object(
                        client, "get", new=AsyncMock(return_value=mock_resp)
                    ):
                        val = await collector._fetch_glassnode(
                            "BTC", "market/mvrv", client
                        )
        assert val is None

    @pytest.mark.asyncio
    async def test_fetch_glassnode_network_error_returns_none(self):
        collector = OnchainCollector()
        async with __import__("httpx").AsyncClient() as client:
            with patch(
                "src.collectors.onchain_collector.cache.get",
                new=AsyncMock(return_value=None),
            ):
                with patch(
                    "src.collectors.onchain_collector.settings"
                ) as mock_settings:
                    mock_settings.GLASSNODE_API_KEY = "test_key"
                    with patch.object(
                        client, "get", side_effect=Exception("network error")
                    ):
                        val = await collector._fetch_glassnode(
                            "BTC", "market/mvrv", client
                        )
        assert val is None
