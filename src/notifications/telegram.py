"""
Telegram Bot notifications for virtual trade positions.

Sends alerts when positions are opened, closed, or reach lifecycle events
(breakeven, partial close, trailing stop update).
"""

import logging
from typing import Optional

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    """
    Sends virtual position notifications via Telegram Bot API.

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

    async def send_position_opened(
        self,
        instrument_symbol: str,
        instrument_name: str,
        direction: str,
        timeframe: str,
        entry_price: float,
        stop_loss: Optional[float],
        take_profit_1: Optional[float],
        take_profit_2: Optional[float],
        size_pct: float,
        risk_reward: Optional[float],
        spread_pips: Optional[float] = None,
    ) -> bool:
        """Send notification when a virtual position is opened."""
        if not self.enabled:
            return False

        dir_symbol = "▲" if direction == "LONG" else "▼"
        emoji = "🟢" if direction == "LONG" else "🔴"

        def _fmt(val: Optional[float]) -> str:
            if val is None:
                return "—"
            if val >= 10:
                return f"{val:.2f}"
            return f"{val:.5f}"

        rr_str = f"1:{risk_reward:.2f}" if risk_reward else "—"
        spread_str = f"{spread_pips:.1f} pip{'s' if (spread_pips or 0) != 1 else ''}" if spread_pips else "—"

        text = (
            f"{emoji} <b>POSITION OPENED: {instrument_name}</b>\n"
            f"─────────────────────\n"
            f"<b>{dir_symbol} {direction}</b>  |  {timeframe}  |  Size: <b>{size_pct:.1f}%</b>\n"
            f"─────────────────────\n"
            f"Entry:   <code>{_fmt(entry_price)}</code>  (spread: {spread_str})\n"
            f"SL:      <code>{_fmt(stop_loss)}</code>\n"
            f"TP1:     <code>{_fmt(take_profit_1)}</code>\n"
            f"TP2:     <code>{_fmt(take_profit_2)}</code>\n"
            f"R:R:     <b>{rr_str}</b>\n"
            f"─────────────────────\n"
            f"<code>{instrument_symbol}</code>"
        )

        return await self.send_message(text)

    async def send_position_closed(
        self,
        instrument_name: str,
        instrument_symbol: str,
        direction: str,
        exit_reason: str,
        entry_price: float,
        exit_price: float,
        pnl_pips: float,
        pnl_usd: Optional[float] = None,
    ) -> bool:
        """Send notification when a virtual position is closed."""
        if not self.enabled:
            return False

        result_emoji = "✅" if pnl_pips > 0 else ("❌" if pnl_pips < 0 else "➖")
        pnl_sign = "+" if pnl_pips >= 0 else ""
        usd_sign = "+" if (pnl_usd or 0) >= 0 else ""

        reason_map = {
            "tp1_hit":  "TP1 Hit 🎯",
            "tp2_hit":  "TP2 Hit 🎯🎯",
            "tp3_hit":  "TP3 Hit 🎯🎯🎯",
            "sl_hit":   "Stop Loss Hit",
            "expired":  "Expired",
            "manual":   "Manual Close",
        }
        reason_label = reason_map.get(exit_reason, exit_reason.replace("_", " ").title())

        def _fmt(val: float) -> str:
            return f"{val:.2f}" if val >= 10 else f"{val:.5f}"

        lines = [
            f"{result_emoji} <b>POSITION CLOSED: {instrument_name}</b>",
            f"─────────────────────",
            f"Direction: {direction}  |  Reason: <b>{reason_label}</b>",
            f"Entry: <code>{_fmt(entry_price)}</code>  →  Exit: <code>{_fmt(exit_price)}</code>",
            f"P&L: <b>{pnl_sign}{pnl_pips:.1f} pips</b>",
        ]
        if pnl_usd is not None:
            lines.append(f"P&L $: <b>{usd_sign}{pnl_usd:.2f} USD</b>")
        lines.append(f"<code>{instrument_symbol}</code>")

        return await self.send_message("\n".join(lines))

    async def send_lifecycle_alert(
        self,
        instrument_name: str,
        instrument_symbol: str,
        direction: str,
        action: str,
        price: float,
        new_sl: Optional[float] = None,
        pnl_pct: Optional[float] = None,
        pnl_usd: Optional[float] = None,
    ) -> bool:
        """Send a trade-management event (breakeven, partial close, trailing stop update)."""
        if not self.enabled:
            return False

        action_map = {
            "breakeven":        ("⚖️", "Breakeven set"),
            "partial_close":    ("💰", "Partial close 50%"),
            "trailing_update":  ("🔄", "Trailing stop updated"),
        }
        icon, label = action_map.get(action, ("ℹ️", action.replace("_", " ").title()))

        def _fmt(val: float) -> str:
            return f"{val:.2f}" if val >= 10 else f"{val:.5f}"

        lines = [
            f"{icon} <b>{label}</b>: {instrument_name} ({direction})",
            f"Price: <code>{_fmt(price)}</code>",
        ]
        if new_sl is not None:
            lines.append(f"New SL: <code>{_fmt(new_sl)}</code>")
        if pnl_pct is not None:
            sign = "+" if pnl_pct >= 0 else ""
            lines.append(f"P&L: <b>{sign}{pnl_pct:.2f}%</b>")
        if pnl_usd is not None:
            sign = "+" if pnl_usd >= 0 else ""
            lines.append(f"P&L $: <b>{sign}{pnl_usd:.2f} USD</b>")
        lines.append(f"<code>{instrument_symbol}</code>")

        return await self.send_message("\n".join(lines))


# Singleton instance
telegram = TelegramNotifier()
