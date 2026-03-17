"""
Telegram Bot notifications for trading signals (Phase 3).

Sends signal alerts to a configured Telegram chat.
"""

import logging
from typing import Any, Optional

import httpx

from src.config import settings
from src.database.models import Signal

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    """
    Sends trading signal notifications via Telegram Bot API.

    Setup:
        1. Create a bot via @BotFather → get TELEGRAM_BOT_TOKEN
        2. Get chat ID: send a message to the bot, then call /getUpdates
        3. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
    """

    def __init__(self) -> None:
        self.token = settings.TELEGRAM_BOT_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID
        self.enabled = bool(self.token and self.chat_id)

        if not self.enabled:
            logger.info("[Telegram] Not configured (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing)")

    async def send_message(self, text: str) -> bool:
        """Send a plain text message to the configured chat."""
        if not self.enabled:
            return False

        url = TELEGRAM_API_URL.format(token=self.token)
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                logger.info("[Telegram] Message sent successfully")
                return True
        except Exception as exc:
            logger.error(f"[Telegram] Failed to send message: {exc}")
            return False

    async def send_signal_alert(
        self,
        signal: Signal,
        instrument_symbol: str,
        instrument_name: str,
    ) -> bool:
        """
        Send a formatted trading signal alert.

        Format:
            🔔 NEW SIGNAL: EUR/USD
            Direction: ▲ LONG (STRONG BUY)
            Entry: 1.08500
            Stop Loss: 1.08350 (-15 pips)
            TP1: 1.08700 (+20 pips)
            TP2: 1.08975 (+47.5 pips)
            R:R: 1:1.33
            Score: +72.5 | Confidence: 85%
            Timeframe: H4 | Horizon: 4-24 hours
        """
        if not self.enabled:
            return False

        direction = signal.direction
        dir_symbol = "▲" if direction == "LONG" else ("▼" if direction == "SHORT" else "●")
        score = float(signal.composite_score)
        score_sign = "+" if score >= 0 else ""

        entry = f"{float(signal.entry_price):.5f}" if signal.entry_price else "—"
        sl = f"{float(signal.stop_loss):.5f}" if signal.stop_loss else "—"
        tp1 = f"{float(signal.take_profit_1):.5f}" if signal.take_profit_1 else "—"
        tp2 = f"{float(signal.take_profit_2):.5f}" if signal.take_profit_2 else "—"
        rr = f"1:{float(signal.risk_reward):.2f}" if signal.risk_reward else "—"

        emoji = "🟢" if direction == "LONG" else ("🔴" if direction == "SHORT" else "⚪")

        text = (
            f"{emoji} <b>NEW SIGNAL: {instrument_name}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Direction: <b>{dir_symbol} {direction}</b> ({signal.signal_strength.replace('_', ' ')})\n"
            f"Entry: <code>{entry}</code>\n"
            f"Stop Loss: <code>{sl}</code>\n"
            f"TP1: <code>{tp1}</code>\n"
            f"TP2: <code>{tp2}</code>\n"
            f"R:R: <b>{rr}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Score: <b>{score_sign}{score:.1f}</b> | Confidence: <b>{signal.confidence:.1f}%</b>\n"
            f"Timeframe: {signal.timeframe} | Horizon: {signal.horizon or '—'}\n"
            f"Symbol: <code>{instrument_symbol}</code>"
        )

        return await self.send_message(text)

    async def send_signal_result(
        self,
        instrument_name: str,
        direction: str,
        exit_reason: str,
        pnl_pips: float,
    ) -> bool:
        """Send notification when a signal is closed."""
        if not self.enabled:
            return False

        result_emoji = "✅" if pnl_pips > 0 else ("❌" if pnl_pips < 0 else "➖")
        pnl_sign = "+" if pnl_pips >= 0 else ""
        reason_map = {
            "tp1_hit": "Take Profit 1 Hit",
            "tp2_hit": "Take Profit 2 Hit ⭐",
            "sl_hit": "Stop Loss Hit",
            "expired": "Signal Expired",
            "manual": "Manual Close",
        }

        text = (
            f"{result_emoji} <b>SIGNAL CLOSED: {instrument_name}</b>\n"
            f"Direction: {direction}\n"
            f"Reason: {reason_map.get(exit_reason, exit_reason)}\n"
            f"P&L: <b>{pnl_sign}{pnl_pips:.1f} pips</b>"
        )

        return await self.send_message(text)


# Singleton instance
telegram = TelegramNotifier()
