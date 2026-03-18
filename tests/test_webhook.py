"""Tests for src.notifications.webhook."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.notifications.webhook import WebhookSender


def _make_signal(**overrides):
    base = {
        "symbol": "EURUSD",
        "direction": "LONG",
        "signal_strength": "STRONG_BUY",
        "composite_score": 72.5,
        "entry_price": 1.1000,
        "stop_loss": 1.0925,
        "take_profit_1": 1.1150,
        "take_profit_2": 1.1250,
        "take_profit_3": 1.1400,
        "risk_reward": 2.0,
        "position_size_pct": 1.0,
        "confidence": 78.0,
        "regime": "STRONG_TREND_BULL",
    }
    base.update(overrides)
    return base


class TestWebhookSenderMT5:
    @pytest.mark.asyncio
    async def test_no_url_returns_false(self):
        sender = WebhookSender()
        with patch("src.notifications.webhook.settings") as ms:
            ms.WEBHOOK_MT5_URL = ""
            ms.WEBHOOK_3COMMAS_URL = ""
            ms.WEBHOOK_TRADINGVIEW_URL = ""
            result = await sender.send_mt5(_make_signal())
        assert result is False

    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        sender = WebhookSender()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "OK"

        with patch("src.notifications.webhook.settings") as ms:
            ms.WEBHOOK_MT5_URL = "http://localhost:9090/mt5"
            ms.WEBHOOK_3COMMAS_URL = ""
            ms.WEBHOOK_TRADINGVIEW_URL = ""
            with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
                result = await sender.send_mt5(_make_signal())
        assert result is True

    @pytest.mark.asyncio
    async def test_non_2xx_returns_false(self):
        sender = WebhookSender()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        with patch("src.notifications.webhook.settings") as ms:
            ms.WEBHOOK_MT5_URL = "http://localhost:9090/mt5"
            ms.WEBHOOK_3COMMAS_URL = ""
            ms.WEBHOOK_TRADINGVIEW_URL = ""
            with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
                result = await sender.send_mt5(_make_signal())
        assert result is False

    @pytest.mark.asyncio
    async def test_network_error_returns_false(self):
        sender = WebhookSender()
        with patch("src.notifications.webhook.settings") as ms:
            ms.WEBHOOK_MT5_URL = "http://localhost:9090/mt5"
            ms.WEBHOOK_3COMMAS_URL = ""
            ms.WEBHOOK_TRADINGVIEW_URL = ""
            with patch(
                "httpx.AsyncClient.post", side_effect=Exception("connection refused")
            ):
                result = await sender.send_mt5(_make_signal())
        assert result is False


class TestWebhookSender3Commas:
    @pytest.mark.asyncio
    async def test_no_url_returns_false(self):
        sender = WebhookSender()
        with patch("src.notifications.webhook.settings") as ms:
            ms.WEBHOOK_MT5_URL = ""
            ms.WEBHOOK_3COMMAS_URL = ""
            ms.WEBHOOK_TRADINGVIEW_URL = ""
            result = await sender.send_3commas(_make_signal(), bot_id="123", pair="USDT_BTC")
        assert result is False

    @pytest.mark.asyncio
    async def test_hold_direction_returns_false(self):
        sender = WebhookSender()
        with patch("src.notifications.webhook.settings") as ms:
            ms.WEBHOOK_MT5_URL = ""
            ms.WEBHOOK_3COMMAS_URL = "http://3commas.test/webhook"
            ms.WEBHOOK_TRADINGVIEW_URL = ""
            result = await sender.send_3commas(
                _make_signal(direction="HOLD"), bot_id="123", pair="USDT_BTC"
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_success_with_secret(self):
        sender = WebhookSender()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "OK"

        with patch("src.notifications.webhook.settings") as ms:
            ms.WEBHOOK_MT5_URL = ""
            ms.WEBHOOK_3COMMAS_URL = "http://3commas.test/webhook"
            ms.WEBHOOK_TRADINGVIEW_URL = ""
            with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
                result = await sender.send_3commas(
                    _make_signal(),
                    bot_id="456",
                    pair="USDT_BTC",
                    secret="supersecret",
                )
        assert result is True


class TestWebhookSenderTradingView:
    @pytest.mark.asyncio
    async def test_no_url_returns_false(self):
        sender = WebhookSender()
        with patch("src.notifications.webhook.settings") as ms:
            ms.WEBHOOK_MT5_URL = ""
            ms.WEBHOOK_3COMMAS_URL = ""
            ms.WEBHOOK_TRADINGVIEW_URL = ""
            result = await sender.send_tradingview(_make_signal())
        assert result is False

    @pytest.mark.asyncio
    async def test_hold_direction_returns_false(self):
        sender = WebhookSender()
        with patch("src.notifications.webhook.settings") as ms:
            ms.WEBHOOK_MT5_URL = ""
            ms.WEBHOOK_3COMMAS_URL = ""
            ms.WEBHOOK_TRADINGVIEW_URL = "http://tv.test/webhook"
            result = await sender.send_tradingview(_make_signal(direction="HOLD"))
        assert result is False

    @pytest.mark.asyncio
    async def test_short_direction_sends_sell(self):
        sender = WebhookSender()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "OK"
        captured = {}

        async def fake_post(url, json=None, headers=None):
            captured["json"] = json
            return mock_resp

        with patch("src.notifications.webhook.settings") as ms:
            ms.WEBHOOK_MT5_URL = ""
            ms.WEBHOOK_3COMMAS_URL = ""
            ms.WEBHOOK_TRADINGVIEW_URL = "http://tv.test/webhook"
            with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=fake_post)):
                await sender.send_tradingview(_make_signal(direction="SHORT"))

        assert captured.get("json", {}).get("action") == "sell"

    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        sender = WebhookSender()
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.text = "Created"

        with patch("src.notifications.webhook.settings") as ms:
            ms.WEBHOOK_MT5_URL = ""
            ms.WEBHOOK_3COMMAS_URL = ""
            ms.WEBHOOK_TRADINGVIEW_URL = "http://tv.test/webhook"
            with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
                result = await sender.send_tradingview(_make_signal())
        assert result is True
