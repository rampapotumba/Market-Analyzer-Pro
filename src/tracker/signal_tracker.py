"""Signal Tracker: monitors active signals and updates their status."""

import asyncio
import datetime
import logging
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.crud import (
    create_signal_result,
    get_active_signals,
    get_instrument_by_id,
    get_price_data,
    update_signal_status,
)
from src.database.engine import async_session_factory
from src.database.models import Signal
from src.notifications.telegram import telegram

logger = logging.getLogger(__name__)

# Entry tolerance: signal is considered "active" when price is within this % of entry
ENTRY_TOLERANCE_PCT = Decimal("0.001")  # 0.1%


def _utc(dt: Optional[datetime.datetime]) -> Optional[datetime.datetime]:
    """Ensure datetime is UTC-aware. SQLite returns naive datetimes."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt


class SignalTracker:
    """
    Monitors active signals and updates their status based on price movements.

    State machine:
        created → active: price reaches entry ± tolerance
        active → tracking: entry filled, tracking SL/TP
        tracking → completed: SL or TP hit
        tracking → expired: signal has expired
    """

    def __init__(self) -> None:
        self._mfe: dict[int, Decimal] = {}  # signal_id -> max favorable excursion
        self._mae: dict[int, Decimal] = {}  # signal_id -> max adverse excursion

    async def _get_current_price(
        self, db: AsyncSession, instrument_id: int, timeframe: str = "M5"
    ) -> Optional[Decimal]:
        """Get the most recent price for an instrument."""
        records = await get_price_data(db, instrument_id, timeframe, limit=1)
        if not records:
            # Try H1 as fallback
            records = await get_price_data(db, instrument_id, "H1", limit=1)
        if not records:
            return None
        return records[-1].close

    def _check_entry(
        self,
        current_price: Decimal,
        entry_price: Decimal,
        direction: str,
    ) -> bool:
        """Check if entry price has been reached."""
        if not entry_price:
            return False
        tolerance = entry_price * ENTRY_TOLERANCE_PCT
        return abs(current_price - entry_price) <= tolerance

    def _check_sl_hit(
        self,
        current_price: Decimal,
        stop_loss: Optional[Decimal],
        direction: str,
    ) -> bool:
        """Check if stop loss has been triggered."""
        if not stop_loss:
            return False
        if direction == "LONG":
            return current_price <= stop_loss
        elif direction == "SHORT":
            return current_price >= stop_loss
        return False

    def _check_tp_hit(
        self,
        current_price: Decimal,
        take_profit: Optional[Decimal],
        direction: str,
    ) -> tuple[bool, str]:
        """Check if take profit has been hit. Returns (hit, tp_name)."""
        if not take_profit:
            return False, ""
        if direction == "LONG" and current_price >= take_profit:
            return True, "tp_hit"
        elif direction == "SHORT" and current_price <= take_profit:
            return True, "tp_hit"
        return False, ""

    def _update_mfe_mae(
        self,
        signal: Signal,
        current_price: Decimal,
        entry_price: Decimal,
    ) -> None:
        """Update Maximum Favorable/Adverse Excursion."""
        sig_id = signal.id
        if sig_id not in self._mfe:
            self._mfe[sig_id] = Decimal("0")
            self._mae[sig_id] = Decimal("0")

        if signal.direction == "LONG":
            favorable = current_price - entry_price
            adverse = entry_price - current_price
        else:
            favorable = entry_price - current_price
            adverse = current_price - entry_price

        self._mfe[sig_id] = max(self._mfe[sig_id], favorable)
        self._mae[sig_id] = max(self._mae[sig_id], adverse)

    def _calculate_pnl(
        self,
        direction: str,
        entry_price: Decimal,
        exit_price: Decimal,
        pip_size: Decimal = Decimal("0.0001"),
    ) -> tuple[Decimal, Decimal]:
        """Calculate P&L in pips and percent."""
        if direction == "LONG":
            raw_diff = exit_price - entry_price
        else:
            raw_diff = entry_price - exit_price

        pnl_pips = raw_diff / pip_size
        pnl_pct = (raw_diff / entry_price) * Decimal("100") if entry_price > 0 else Decimal("0")
        return pnl_pips, pnl_pct

    async def check_signal(
        self,
        db: AsyncSession,
        signal: Signal,
    ) -> None:
        """Process a single signal and update its status."""
        now = datetime.datetime.now(datetime.timezone.utc)

        # Check expiry
        if signal.expires_at and now > _utc(signal.expires_at):
            if signal.status in ("created", "active", "tracking"):
                instrument = await get_instrument_by_id(db, signal.instrument_id)
                current_price = await self._get_current_price(db, signal.instrument_id)

                result_data = {
                    "signal_id": signal.id,
                    "exit_at": now,
                    "exit_price": current_price,
                    "exit_reason": "expired",
                    "price_at_expiry": current_price,
                    "result": "breakeven",
                    "max_favorable_excursion": self._mfe.get(signal.id, Decimal("0")),
                    "max_adverse_excursion": self._mae.get(signal.id, Decimal("0")),
                }
                if signal.entry_price and current_price:
                    pips, pct = self._calculate_pnl(
                        signal.direction, signal.entry_price, current_price
                    )
                    result_data["pnl_pips"] = pips
                    result_data["pnl_percent"] = pct
                    result_data["result"] = "win" if pips > 0 else ("loss" if pips < 0 else "breakeven")

                    if signal.created_at:
                        duration = (now - _utc(signal.created_at)).total_seconds() / 60
                        result_data["duration_minutes"] = int(duration)

                async with db.begin_nested():
                    await create_signal_result(db, result_data)
                    await update_signal_status(db, signal.id, "expired")
                logger.info(f"[Tracker] Signal {signal.id} expired")
            return

        # Get current price
        current_price = await self._get_current_price(db, signal.instrument_id)
        if current_price is None:
            logger.debug(f"[Tracker] No price data for signal {signal.id}")
            return

        entry_price = signal.entry_price or current_price

        # Update MFE/MAE
        self._update_mfe_mae(signal, current_price, entry_price)

        if signal.status == "created":
            # Check if entry has been reached
            if self._check_entry(current_price, entry_price, signal.direction):
                async with db.begin_nested():
                    await update_signal_status(db, signal.id, "tracking")
                logger.info(
                    f"[Tracker] Signal {signal.id} activated: "
                    f"{signal.direction} @ {current_price}"
                )

        elif signal.status in ("active", "tracking"):
            # Check TP2 first (higher profit target)
            tp2_hit, _ = self._check_tp_hit(current_price, signal.take_profit_2, signal.direction)
            tp1_hit, _ = self._check_tp_hit(current_price, signal.take_profit_1, signal.direction)
            sl_hit = self._check_sl_hit(current_price, signal.stop_loss, signal.direction)

            if tp2_hit:
                await self._close_signal(db, signal, current_price, now, "tp2_hit")
            elif tp1_hit:
                await self._close_signal(db, signal, current_price, now, "tp1_hit")
            elif sl_hit:
                await self._close_signal(db, signal, current_price, now, "sl_hit")

    async def _close_signal(
        self,
        db: AsyncSession,
        signal: Signal,
        exit_price: Decimal,
        exit_at: datetime.datetime,
        exit_reason: str,
    ) -> None:
        """Close a signal and create result record."""
        entry_price = signal.entry_price or exit_price
        instrument = await get_instrument_by_id(db, signal.instrument_id)
        pip_size = instrument.pip_size if instrument else Decimal("0.0001")

        pnl_pips, pnl_pct = self._calculate_pnl(
            signal.direction, entry_price, exit_price, pip_size
        )

        result = "win" if pnl_pips > 0 else ("loss" if pnl_pips < 0 else "breakeven")

        duration_minutes = None
        if signal.created_at:
            duration_minutes = int((exit_at - _utc(signal.created_at)).total_seconds() / 60)

        result_data = {
            "signal_id": signal.id,
            "entry_actual_price": entry_price,
            "entry_filled_at": signal.created_at,
            "exit_at": exit_at,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "pnl_pips": pnl_pips.quantize(Decimal("0.01")),
            "pnl_percent": pnl_pct.quantize(Decimal("0.0001")),
            "result": result,
            "max_favorable_excursion": self._mfe.get(signal.id, Decimal("0")),
            "max_adverse_excursion": self._mae.get(signal.id, Decimal("0")),
            "duration_minutes": duration_minutes,
        }

        async with db.begin_nested():
            await create_signal_result(db, result_data)
            await update_signal_status(db, signal.id, "completed")

        # Clean up tracking data
        self._mfe.pop(signal.id, None)
        self._mae.pop(signal.id, None)

        logger.info(
            f"[Tracker] Signal {signal.id} completed: {exit_reason}, "
            f"PnL={pnl_pips:.1f} pips ({result})"
        )

        # Send Telegram result notification
        try:
            instrument_name = instrument.name if instrument else str(signal.instrument_id)
            await telegram.send_signal_result(
                instrument_name, signal.direction, exit_reason, float(pnl_pips)
            )
        except Exception as exc:
            logger.warning(f"[Tracker] Telegram result notification failed: {exc}")

    async def check_active_signals(self, db: Optional[AsyncSession] = None) -> None:
        """Check all active signals."""
        if db is None:
            async with async_session_factory() as session:
                await self._run_checks(session)
        else:
            await self._run_checks(db)

    async def _run_checks(self, db: AsyncSession) -> None:
        """Run checks within a given session."""
        signals = await get_active_signals(db)
        if not signals:
            return

        logger.debug(f"[Tracker] Checking {len(signals)} active signals")
        for signal in signals:
            try:
                await self.check_signal(db, signal)
            except Exception as exc:
                logger.error(f"[Tracker] Error checking signal {signal.id}: {exc}")
