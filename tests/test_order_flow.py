"""Tests for src.collectors.order_flow_collector."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.order_flow_collector import OrderFlowCollector, _SymbolBuffer


# ── _SymbolBuffer ─────────────────────────────────────────────────────────────

class TestSymbolBuffer:
    def test_initial_cvd_zero(self):
        buf = _SymbolBuffer("BTCUSDT")
        assert buf.cvd == 0.0

    def test_buy_trade_positive_cvd(self):
        buf = _SymbolBuffer("BTCUSDT")
        buf.add_trade(qty=1.5, is_buyer_maker=False)  # taker buy
        assert buf.cvd > 0

    def test_sell_trade_negative_cvd(self):
        buf = _SymbolBuffer("BTCUSDT")
        buf.add_trade(qty=1.5, is_buyer_maker=True)  # maker sell
        assert buf.cvd < 0

    def test_mixed_trades(self):
        buf = _SymbolBuffer("BTCUSDT")
        buf.add_trade(qty=2.0, is_buyer_maker=False)  # buy 2
        buf.add_trade(qty=1.0, is_buyer_maker=True)   # sell 1
        assert buf.cvd == pytest.approx(1.0)

    def test_reset_clears_cvd(self):
        buf = _SymbolBuffer("BTCUSDT")
        buf.add_trade(qty=5.0, is_buyer_maker=False)
        buf.reset()
        assert buf.cvd == 0.0


# ── OrderFlowCollector ────────────────────────────────────────────────────────

class TestOrderFlowCollector:
    def test_instantiate(self):
        collector = OrderFlowCollector()
        assert collector is not None

    @pytest.mark.asyncio
    async def test_health_check_on_success(self):
        """health_check returns True when Binance API responds 200."""
        collector = OrderFlowCollector()
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
            result = await collector.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_on_failure(self):
        collector = OrderFlowCollector()
        with patch("httpx.AsyncClient.get", side_effect=Exception("timeout")):
            result = await collector.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_fetch_open_interest_cached(self):
        """Second call returns cached value without HTTP."""
        collector = OrderFlowCollector()
        async with __import__("httpx").AsyncClient() as client:
            # Mock cache.get to return cached value
            with patch("src.collectors.order_flow_collector.cache.get", new=AsyncMock(return_value="50000.0")):
                val = await collector._fetch_open_interest("BTCUSDT", client)
        assert val == pytest.approx(50000.0)

    @pytest.mark.asyncio
    async def test_fetch_open_interest_network_error(self):
        collector = OrderFlowCollector()
        async with __import__("httpx").AsyncClient() as client:
            with patch.object(client, "get", side_effect=Exception("network error")):
                val = await collector._fetch_open_interest("BTCUSDT", client)
        assert val is None

    @pytest.mark.asyncio
    async def test_fetch_funding_rate_parses_response(self):
        """Successful response returns float funding rate."""
        collector = OrderFlowCollector()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: {"lastFundingRate": "0.000100"}

        async with __import__("httpx").AsyncClient() as client:
            with patch.object(client, "get", new=AsyncMock(return_value=mock_resp)):
                val = await collector._fetch_funding_rate("BTCUSDT", client)
        assert val == pytest.approx(0.0001)

    @pytest.mark.asyncio
    async def test_fetch_cvd_approx_empty_trades(self):
        collector = OrderFlowCollector()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: []

        async with __import__("httpx").AsyncClient() as client:
            with patch.object(client, "get", new=AsyncMock(return_value=mock_resp)):
                val = await collector._fetch_cvd_approx("BTCUSDT", client)
        assert val == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_fetch_cvd_buy_dominant(self):
        """More buy trades → positive CVD."""
        collector = OrderFlowCollector()
        # m=False means taker is buyer → buy
        trades = [{"q": "1.0", "m": False}] * 3 + [{"q": "1.0", "m": True}] * 1
        mock_resp = MagicMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: trades

        async with __import__("httpx").AsyncClient() as client:
            with patch.object(client, "get", new=AsyncMock(return_value=mock_resp)):
                val = await collector._fetch_cvd_approx("BTCUSDT", client)
        assert val == pytest.approx(2.0)  # 3 buys - 1 sell = +2
