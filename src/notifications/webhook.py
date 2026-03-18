"""Outgoing webhooks for external trading platforms.

Supported targets:
  - MT5 (MetaTrader 5) — sends trade commands via HTTP
  - 3Commas — DCA bot deal start/stop
  - TradingView — alert webhook (inbound-style payload)

All methods are fire-and-forget (log on failure, never raise).
Webhook URLs come from settings (WEBHOOK_MT5_URL, etc.).
"""

import logging
from decimal import Decimal
from typing import Any, Optional

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0  # seconds
_HEADERS = {"Content-Type": "application/json"}


class WebhookSender:
    """
    Sends trade signals to external platforms.

    Usage:
        sender = WebhookSender()
        await sender.send_mt5(signal)
        await sender.send_3commas(signal, bot_id="12345", pair="USDT_BTC")
        await sender.send_tradingview(signal)
    """

    async def send_mt5(self, signal: dict[str, Any]) -> bool:
        """
        POST to MT5 bridge / EA webhook.

        Expected EA bridge accepts:
          {
            "action": "BUY" | "SELL",
            "symbol": "EURUSD",
            "sl": 1.09250,
            "tp": 1.11500,
            "risk_pct": 1.0,
            "comment": "MAP-v2"
          }
        """
        url = settings.WEBHOOK_MT5_URL
        if not url:
            return False

        payload = {
            "action": signal.get("direction", "HOLD"),
            "symbol": signal.get("symbol", ""),
            "sl": float(signal.get("stop_loss") or 0),
            "tp": float(signal.get("take_profit_1") or 0),
            "tp2": float(signal.get("take_profit_2") or 0),
            "tp3": float(signal.get("take_profit_3") or 0),
            "risk_pct": float(signal.get("position_size_pct") or 1.0),
            "composite": float(signal.get("composite_score") or 0),
            "confidence": float(signal.get("confidence") or 0),
            "regime": signal.get("regime", ""),
            "comment": f"MAP-v2 {signal.get('signal_strength', '')}",
        }
        return await self._post(url, payload, "MT5")

    async def send_3commas(
        self,
        signal: dict[str, Any],
        bot_id: str,
        pair: str,
        secret: Optional[str] = None,
    ) -> bool:
        """
        POST to 3Commas custom signal endpoint.

        3Commas expects:
          { "message": "start_bot", "bot_id": 123, "pair": "USDT_BTC", ... }
        """
        url = settings.WEBHOOK_3COMMAS_URL
        if not url:
            return False

        direction = signal.get("direction", "HOLD")
        if direction == "LONG":
            message = "start_deal"
        elif direction == "SHORT":
            message = "start_deal"  # 3Commas handles short via bot config
        else:
            return False

        payload: dict[str, Any] = {
            "message": message,
            "bot_id": bot_id,
            "pair": pair,
        }
        if secret:
            payload["secret"] = secret

        return await self._post(url, payload, "3Commas")

    async def send_tradingview(self, signal: dict[str, Any]) -> bool:
        """
        POST TradingView-compatible alert payload.

        TradingView strategy alerts expect plain JSON matching the
        alert message template configured in the broker integration.
        """
        url = settings.WEBHOOK_TRADINGVIEW_URL
        if not url:
            return False

        direction = signal.get("direction", "HOLD")
        if direction not in ("LONG", "SHORT"):
            return False

        payload = {
            "ticker": signal.get("symbol", ""),
            "action": "buy" if direction == "LONG" else "sell",
            "price": float(signal.get("entry_price") or 0),
            "sl": float(signal.get("stop_loss") or 0),
            "tp1": float(signal.get("take_profit_1") or 0),
            "tp2": float(signal.get("take_profit_2") or 0),
            "tp3": float(signal.get("take_profit_3") or 0),
            "rr": float(signal.get("risk_reward") or 0),
            "strength": signal.get("signal_strength", ""),
            "confidence": float(signal.get("confidence") or 0),
        }
        return await self._post(url, payload, "TradingView")

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _post(self, url: str, payload: dict, platform: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(url, json=payload, headers=_HEADERS)
                if resp.status_code < 300:
                    logger.info(
                        "Webhook %s: sent successfully (HTTP %d)", platform, resp.status_code
                    )
                    return True
                else:
                    logger.warning(
                        "Webhook %s: unexpected status %d — %s",
                        platform,
                        resp.status_code,
                        resp.text[:200],
                    )
                    return False
        except Exception as exc:
            logger.error("Webhook %s: send failed — %s", platform, exc)
            return False


# Module-level singleton
webhook_sender = WebhookSender()
