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


    async def send_signal_alert_v2(
        self,
        signal: Signal,
        instrument_symbol: str,
        instrument_name: str,
    ) -> bool:
        """
        Enhanced v2 signal alert with regime, TP3, OF score, portfolio heat.

        Format:
            🟢 NEW SIGNAL v2: EUR/USD (STRONG BUY)
            📊 Regime: STRONG_TREND_BULL
            ─────────────────────
            ▲ LONG  |  H4  |  Confidence: 82%
            Score: +74.5  (TA:65 FA:55 Sent:40 Geo:10 OF:60)
            ─────────────────────
            Entry:   1.08500
            SL:      1.08250  (risk 0.23%)
            TP1:     1.08875  (RR 1:1.5)
            TP2:     1.09250
            TP3:     1.09750
            ─────────────────────
            Portfolio heat: 3.5%  (max 6%)
        """
        if not self.enabled:
            return False

        direction = signal.direction
        dir_symbol = "▲" if direction == "LONG" else "▼"
        emoji = "🟢" if direction == "LONG" else "🔴"
        score = float(signal.composite_score or 0)
        score_sign = "+" if score >= 0 else ""

        def _fmt(val, decimals=5):
            return f"{float(val):.{decimals}f}" if val is not None else "—"

        # Score breakdown
        ta = f"TA:{signal.ta_score:.0f}" if signal.ta_score is not None else ""
        fa = f"FA:{signal.fa_score:.0f}" if signal.fa_score is not None else ""
        sent = f"Sent:{signal.sentiment_score:.0f}" if signal.sentiment_score is not None else ""
        geo = f"Geo:{signal.geo_score:.0f}" if signal.geo_score is not None else ""
        of = f"OF:{signal.of_score:.0f}" if signal.of_score is not None else ""
        breakdown = " ".join(filter(None, [ta, fa, sent, geo, of]))

        rr = f"1:{float(signal.risk_reward):.2f}" if signal.risk_reward else ""
        tp1_line = f"TP1:     <code>{_fmt(signal.take_profit_1)}</code>"
        if rr:
            tp1_line += f"  (RR {rr})"

        heat = f"{float(signal.portfolio_heat):.1f}%" if signal.portfolio_heat else "—"

        text = (
            f"{emoji} <b>SIGNAL v2: {instrument_name}</b>  "
            f"({signal.signal_strength.replace('_', ' ')})\n"
            f"📊 Regime: <b>{signal.regime or '—'}</b>\n"
            f"─────────────────────\n"
            f"<b>{dir_symbol} {direction}</b>  |  {signal.timeframe}  |  "
            f"Confidence: <b>{float(signal.confidence):.1f}%</b>\n"
            f"Score: <b>{score_sign}{score:.1f}</b>  ({breakdown})\n"
            f"─────────────────────\n"
            f"Entry:   <code>{_fmt(signal.entry_price)}</code>\n"
            f"SL:      <code>{_fmt(signal.stop_loss)}</code>\n"
            f"{tp1_line}\n"
            f"TP2:     <code>{_fmt(signal.take_profit_2)}</code>\n"
            f"TP3:     <code>{_fmt(signal.take_profit_3)}</code>\n"
            f"─────────────────────\n"
            f"Portfolio heat: <b>{heat}</b>  (max 6%)\n"
            f"<code>{instrument_symbol}</code>"
        )

        return await self.send_message(text)

    async def send_lifecycle_alert(
        self,
        instrument_name: str,
        direction: str,
        action: str,
        price: float,
        new_sl: float = None,
        pnl_pct: float = None,
    ) -> bool:
        """Send a trade-management event alert (breakeven, partial close, trail)."""
        if not self.enabled:
            return False

        action_map = {
            "breakeven": ("⚖️", "Breakeven set"),
            "partial_close": ("💰", "Partial close 50%"),
            "trailing_update": ("🔄", "Trailing stop updated"),
            "exit_tp1": ("✅", "TP1 hit"),
            "exit_tp2": ("✅✅", "TP2 hit"),
            "exit_tp3": ("✅✅✅", "TP3 hit"),
            "exit_sl": ("❌", "Stop Loss hit"),
        }
        icon, label = action_map.get(action, ("ℹ️", action))

        lines = [f"{icon} <b>{label}</b>: {instrument_name} ({direction})"]
        lines.append(f"Price: <code>{price:.5f}</code>")
        if new_sl is not None:
            lines.append(f"New SL: <code>{new_sl:.5f}</code>")
        if pnl_pct is not None:
            sign = "+" if pnl_pct >= 0 else ""
            lines.append(f"P&L: <b>{sign}{pnl_pct:.2f}%</b>")

        return await self.send_message("\n".join(lines))


# Singleton instance
telegram = TelegramNotifier()
