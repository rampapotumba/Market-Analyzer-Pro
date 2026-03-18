"""Signal Tracker v2: monitors active signals and updates their status.

Changes from v1:
  SIM-01: MFE/MAE stored in DB (virtual_portfolio.mfe/mae), not in-memory dict
  SIM-02: Spread model applied at entry (forex/stocks pips, crypto % fee)
  SIM-03: expired (had entry) vs cancelled (no entry) — clean stat separation
  SIM-04: Duration from entry_filled_at, not created_at
  SIM-05: TradeLifecycleManager integrated for breakeven/trailing/partial close
  SIM-06: pnl_usd = account × (size_pct/100) × (pnl_pct/100)
  SIM-07: Partial close at 95% of TP1 distance
  SIM-08: Entry tolerance by market type (forex=0.03%, stocks=0.1%, crypto=0.2%)
"""

import datetime
import json
import logging
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database.crud import (
    close_virtual_position,
    create_signal_result,
    create_virtual_position,
    get_active_signals,
    get_instrument_by_id,
    get_price_data,
    get_virtual_position,
    update_signal_status,
    update_virtual_position,
)
from src.database.engine import async_session_factory
from src.database.models import Signal
from src.notifications.telegram import telegram
from src.signals.trade_lifecycle import TradeLifecycleManager

logger = logging.getLogger(__name__)

# Account size for P&L USD calculation (SIM-06)
ACCOUNT_SIZE = Decimal(str(settings.VIRTUAL_ACCOUNT_SIZE_USD))

# ── Entry tolerance by market (SIM-08) ────────────────────────────────────────

ENTRY_TOLERANCE_BY_MARKET: dict[str, Decimal] = {
    "forex":  Decimal("0.0003"),   # 0.03% ≈ 3 pips
    "stocks": Decimal("0.001"),    # 0.1%
    "crypto": Decimal("0.002"),    # 0.2%
}

# ── Spread model (SIM-02) ─────────────────────────────────────────────────────

SPREAD_PIPS_BY_MARKET: dict[str, Decimal] = {
    "forex":  Decimal("1.5"),   # 1.5 pips (Pepperstone Razor)
    "stocks": Decimal("2.0"),   # 2 cents / 0.01 pip_size = 2 pips
}
CRYPTO_SPREAD_PCT: Decimal = Decimal("0.00075")  # 0.075% Binance taker fee


# ── Helpers ───────────────────────────────────────────────────────────────────

def _utc(dt: Optional[datetime.datetime]) -> Optional[datetime.datetime]:
    """Ensure datetime is UTC-aware (handles naive datetimes from legacy data)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _apply_spread(
    entry_price: Decimal,
    direction: str,
    market: str,
    pip_size: Decimal,
) -> Decimal:
    """Return entry_actual_price after applying bid/ask spread (SIM-02).

    LONG:  pays the ask (higher price)
    SHORT: receives the bid (lower price)
    """
    if market == "crypto":
        spread = entry_price * CRYPTO_SPREAD_PCT
    else:
        pips = SPREAD_PIPS_BY_MARKET.get(market, Decimal("1.5"))
        spread = pips * pip_size

    return entry_price + spread if direction == "LONG" else entry_price - spread


class SignalTracker:
    """
    Monitors active signals and updates their status based on price movements.

    State machine (v2):
        created  → tracking  : price reaches entry ± market tolerance
        tracking → completed : SL / TP / trailing SL hit
        tracking → expired   : signal expired after entry (P&L computed)
        created  → cancelled : signal expired before entry (0 P&L, excluded from stats)
    """

    def __init__(self) -> None:
        pass  # All state is persisted in DB (SIM-01 — no in-memory MFE/MAE)

    # ── Price helpers ─────────────────────────────────────────────────────────

    async def _get_current_price(
        self, db: AsyncSession, instrument_id: int, timeframe: str = "M5"
    ) -> Optional[Decimal]:
        records = await get_price_data(db, instrument_id, timeframe, limit=1)
        if not records:
            records = await get_price_data(db, instrument_id, "H1", limit=1)
        if not records:
            return None
        return records[-1].close

    # ── Entry check (SIM-08) ──────────────────────────────────────────────────

    def _check_entry(
        self,
        current_price: Decimal,
        entry_price: Decimal,
        market: str,
    ) -> bool:
        if not entry_price:
            return False
        tol_pct = ENTRY_TOLERANCE_BY_MARKET.get(market, Decimal("0.001"))
        tolerance = entry_price * tol_pct
        return abs(current_price - entry_price) <= tolerance

    # ── Simple SL/TP checks (used as fallback when no TP1 defined) ────────────

    def _check_sl_hit(
        self, current_price: Decimal, stop_loss: Optional[Decimal], direction: str
    ) -> bool:
        if not stop_loss:
            return False
        if direction == "LONG":
            return current_price <= stop_loss
        return current_price >= stop_loss

    def _check_tp_hit(
        self, current_price: Decimal, take_profit: Optional[Decimal], direction: str
    ) -> bool:
        if not take_profit:
            return False
        if direction == "LONG":
            return current_price >= take_profit
        return current_price <= take_profit

    # ── P&L calculation ───────────────────────────────────────────────────────

    def _calculate_pnl(
        self,
        direction: str,
        entry_price: Decimal,
        exit_price: Decimal,
        pip_size: Decimal = Decimal("0.0001"),
    ) -> tuple[Decimal, Decimal]:
        """Return (pnl_pips, pnl_pct)."""
        if direction == "LONG":
            raw_diff = exit_price - entry_price
        else:
            raw_diff = entry_price - exit_price
        pnl_pips = raw_diff / pip_size
        pnl_pct = (raw_diff / entry_price) * Decimal("100") if entry_price > 0 else Decimal("0")
        return pnl_pips, pnl_pct

    def _pnl_usd(
        self,
        pnl_pct: Decimal,
        position_size_pct: Optional[Decimal],
    ) -> Decimal:
        """SIM-06: pnl_usd = account × (size_pct/100) × (pnl_pct/100)."""
        size = position_size_pct if (position_size_pct and position_size_pct > 0) else Decimal("2.0")
        return ACCOUNT_SIZE * (size / Decimal("100")) * (pnl_pct / Decimal("100"))

    # ── MFE/MAE (SIM-01) — DB-persisted ──────────────────────────────────────

    async def _update_mfe_mae(
        self,
        db: AsyncSession,
        signal: Signal,
        current_price: Decimal,
        entry_price: Decimal,
    ) -> None:
        """Update MFE/MAE in virtual_portfolio row (persists across ticks)."""
        try:
            position = await get_virtual_position(db, signal.id)
            if position is None:
                return

            if signal.direction == "LONG":
                favorable = current_price - entry_price
                adverse = entry_price - current_price
            else:
                favorable = entry_price - current_price
                adverse = current_price - entry_price

            cur_mfe = position.mfe if position.mfe is not None else Decimal("0")
            cur_mae = position.mae if position.mae is not None else Decimal("0")
            new_mfe = max(cur_mfe, favorable)
            new_mae = max(cur_mae, adverse)

            if new_mfe != cur_mfe or new_mae != cur_mae:
                await update_virtual_position(
                    db, signal.id, {"mfe": new_mfe, "mae": new_mae}
                )
        except Exception as exc:
            logger.debug(f"[Tracker] MFE/MAE update failed for signal {signal.id}: {exc}")

    # ── ATR helper (for TradeLifecycleManager) ────────────────────────────────

    async def _get_atr(self, db: AsyncSession, signal: Signal, instrument: Any) -> Decimal:
        """Get ATR from signal's indicators_snapshot, or compute a fallback."""
        try:
            if signal.indicators_snapshot:
                indicators = json.loads(signal.indicators_snapshot)
                for key in ("atr", "ATR", "atr_14", "ATR_14"):
                    atr_val = indicators.get(key)
                    if atr_val and float(atr_val) > 0:
                        return Decimal(str(atr_val))
        except Exception:
            pass

        # Fallback: 14× pip_size as a minimal ATR estimate
        if instrument and instrument.pip_size:
            return instrument.pip_size * Decimal("14")
        return Decimal("0.0014")

    # ── Main entry point ──────────────────────────────────────────────────────

    async def check_signal(
        self,
        db: AsyncSession,
        signal: Signal,
        current_price: Optional[Decimal] = None,
    ) -> None:
        """Process a single signal tick and update its state."""
        now = datetime.datetime.now(datetime.timezone.utc)

        # Load instrument once for market/pip_size
        instrument = await get_instrument_by_id(db, signal.instrument_id)
        market = instrument.market if instrument else "forex"
        pip_size = instrument.pip_size if instrument else Decimal("0.0001")

        # ── Expiry check ──────────────────────────────────────────────────────
        if signal.expires_at and now > _utc(signal.expires_at):
            if signal.status in ("created", "active", "tracking"):
                if current_price is None:
                    current_price = await self._get_current_price(db, signal.instrument_id)

                if signal.status == "created":
                    # SIM-03: no entry was ever filled → cancelled (excluded from stats)
                    result_data: dict[str, Any] = {
                        "signal_id":              signal.id,
                        "exit_at":                now,
                        "exit_price":             current_price,
                        "exit_reason":            "cancelled",
                        "result":                 "breakeven",
                        "pnl_pips":               Decimal("0"),
                        "pnl_percent":            Decimal("0"),
                        "pnl_usd":                Decimal("0"),
                        "max_favorable_excursion": Decimal("0"),
                        "max_adverse_excursion":   Decimal("0"),
                    }
                    async with db.begin_nested():
                        await create_signal_result(db, result_data)
                        await update_signal_status(db, signal.id, "cancelled")
                    logger.info(f"[Tracker] Signal {signal.id} cancelled (no entry filled)")

                else:
                    # SIM-03: had entry → expired with real P&L
                    position = await get_virtual_position(db, signal.id)
                    actual_entry = (
                        (position.entry_price if position else None)
                        or signal.entry_price
                        or current_price
                    )

                    result_data = {
                        "signal_id":               signal.id,
                        "exit_at":                 now,
                        "exit_price":              current_price,
                        "exit_reason":             "expired",
                        "price_at_expiry":         current_price,
                        "result":                  "breakeven",
                        "max_favorable_excursion": (position.mfe if position else None) or Decimal("0"),
                        "max_adverse_excursion":   (position.mae if position else None) or Decimal("0"),
                    }

                    if actual_entry and current_price:
                        pips, pct = self._calculate_pnl(
                            signal.direction, actual_entry, current_price, pip_size
                        )
                        result_data["entry_actual_price"] = actual_entry
                        result_data["pnl_pips"]    = pips
                        result_data["pnl_percent"] = pct
                        result_data["pnl_usd"]     = self._pnl_usd(pct, signal.position_size_pct)
                        result_data["result"]      = "win" if pips > 0 else ("loss" if pips < 0 else "breakeven")

                        # SIM-04: duration from entry_filled_at
                        entry_time = (
                            _utc(position.entry_filled_at if position else None)
                            or _utc(signal.created_at)
                        )
                        if entry_time:
                            result_data["duration_minutes"] = int(
                                (now - entry_time).total_seconds() / 60
                            )
                            result_data["entry_filled_at"] = entry_time

                    async with db.begin_nested():
                        await create_signal_result(db, result_data)
                        await update_signal_status(db, signal.id, "expired")
                    logger.info(f"[Tracker] Signal {signal.id} expired")

            return

        # ── Get live price ────────────────────────────────────────────────────
        if current_price is None:
            current_price = await self._get_current_price(db, signal.instrument_id)
        if current_price is None:
            logger.debug(f"[Tracker] No price data for signal {signal.id}")
            return

        entry_price = signal.entry_price or current_price

        # ── State: created → tracking (entry fill) ────────────────────────────
        if signal.status == "created":
            if self._check_entry(current_price, entry_price, market):
                # SIM-02: spread-adjusted actual entry
                actual_price = _apply_spread(entry_price, signal.direction, market, pip_size)
                entry_ts = datetime.datetime.now(datetime.timezone.utc)
                async with db.begin_nested():
                    await update_signal_status(db, signal.id, "tracking")
                    await self._open_virtual_position(db, signal, actual_price, entry_ts)
                logger.info(
                    f"[Tracker] Signal {signal.id} entry filled: "
                    f"{signal.direction} @ {actual_price} (spread applied, market={market})"
                )

        # ── State: active/tracking → SL/TP/lifecycle ─────────────────────────
        elif signal.status in ("active", "tracking"):
            position = await get_virtual_position(db, signal.id)
            if position is None:
                return

            actual_entry = position.entry_price or entry_price

            # Update unrealized P&L
            await self._update_virtual_unrealized(db, signal, current_price, actual_entry)
            # Update MFE/MAE in DB (SIM-01)
            await self._update_mfe_mae(db, signal, current_price, actual_entry)

            # SIM-05: use TradeLifecycleManager when SL and TP1 are defined
            if signal.stop_loss and signal.take_profit_1:
                atr = await self._get_atr(db, signal, instrument)
                current_sl = position.current_stop_loss or signal.stop_loss

                lifecycle = TradeLifecycleManager()
                action = lifecycle.check(
                    direction=signal.direction,
                    entry=actual_entry,
                    stop_loss=current_sl,
                    take_profit_1=signal.take_profit_1,
                    take_profit_2=signal.take_profit_2,
                    take_profit_3=signal.take_profit_3,
                    current_price=current_price,
                    atr=atr,
                    regime=signal.regime or "RANGING",
                    partial_closed=bool(position.partial_closed),
                    breakeven_moved=bool(position.breakeven_moved),
                    trailing_stop=position.trailing_stop,
                )

                act = action["action"]

                if act.startswith("exit_"):
                    if act == "exit_sl" and position.trailing_stop:
                        exit_reason = "trailing_sl_hit"
                    else:
                        exit_reason = act.replace("exit_", "") + "_hit"
                    await self._close_signal(
                        db, signal, current_price, now, exit_reason, position, pip_size
                    )

                elif act == "breakeven":
                    await update_virtual_position(db, signal.id, {
                        "current_stop_loss": action["new_stop_loss"],
                        "breakeven_moved":   True,
                    })
                    logger.debug(
                        f"[Tracker] Signal {signal.id} breakeven: SL → {action['new_stop_loss']}"
                    )

                elif act == "partial_close" and not position.partial_closed:
                    # SIM-07: close 50% at TP1 zone
                    await self._partial_close(
                        db, signal, position, current_price, now, pip_size
                    )

                elif act == "trailing_update":
                    await update_virtual_position(db, signal.id, {
                        "trailing_stop":    action["new_stop_loss"],
                        "current_stop_loss": action["new_stop_loss"],
                    })

            else:
                # Fallback: simple SL/TP check (no lifecycle)
                tp2_hit = self._check_tp_hit(current_price, signal.take_profit_2, signal.direction)
                tp1_hit = self._check_tp_hit(current_price, signal.take_profit_1, signal.direction)
                sl_hit  = self._check_sl_hit(current_price, signal.stop_loss,    signal.direction)

                if tp2_hit:
                    await self._close_signal(db, signal, current_price, now, "tp2_hit", position, pip_size)
                elif tp1_hit:
                    await self._close_signal(db, signal, current_price, now, "tp1_hit", position, pip_size)
                elif sl_hit:
                    await self._close_signal(db, signal, current_price, now, "sl_hit",  position, pip_size)

    # ── Lifecycle helpers ─────────────────────────────────────────────────────

    async def _open_virtual_position(
        self,
        db: AsyncSession,
        signal: Signal,
        actual_entry_price: Decimal,
        entry_filled_at: datetime.datetime,
    ) -> None:
        """Create virtual portfolio position at spread-adjusted entry (SIM-02)."""
        try:
            if await get_virtual_position(db, signal.id) is not None:
                return  # idempotent

            size_pct = Decimal("2.0")  # default risk %
            if signal.position_size_pct and signal.position_size_pct > 0:
                size_pct = Decimal(str(signal.position_size_pct))

            await create_virtual_position(db, data={
                "signal_id":        signal.id,
                "size_pct":         size_pct,
                "entry_price":      actual_entry_price,
                "status":           "open",
                "entry_filled_at":  entry_filled_at,
                "current_stop_loss": signal.stop_loss,
                "size_remaining_pct": Decimal("1.0"),
                "mfe":              Decimal("0"),
                "mae":              Decimal("0"),
                "breakeven_moved":  False,
                "partial_closed":   False,
            })
            logger.info(
                f"[Tracker] Virtual position opened for signal {signal.id} @ {actual_entry_price}"
            )
        except Exception as exc:
            logger.warning(
                f"[Tracker] Failed to open virtual position for signal {signal.id}: {exc}"
            )

    async def _update_virtual_unrealized(
        self,
        db: AsyncSession,
        signal: Signal,
        current_price: Decimal,
        entry_price: Decimal,
    ) -> None:
        try:
            if signal.direction == "LONG":
                pnl_pct = (current_price - entry_price) / entry_price * Decimal("100")
            else:
                pnl_pct = (entry_price - current_price) / entry_price * Decimal("100")
            await update_virtual_position(db, signal.id, {
                "current_price":      current_price,
                "unrealized_pnl_pct": pnl_pct.quantize(Decimal("0.0001")),
            })
        except Exception as exc:
            logger.debug(
                f"[Tracker] Unrealized P&L update failed for signal {signal.id}: {exc}"
            )

    async def _partial_close(
        self,
        db: AsyncSession,
        signal: Signal,
        position: Any,
        exit_price: Decimal,
        exit_at: datetime.datetime,
        pip_size: Decimal,
    ) -> None:
        """SIM-07: Close 50% at TP1 zone, move SL to entry."""
        entry = position.entry_price or signal.entry_price or exit_price
        _, pnl_pct = self._calculate_pnl(signal.direction, entry, exit_price, pip_size)
        remaining = (position.size_remaining_pct or Decimal("1.0")) * Decimal("0.5")

        await update_virtual_position(db, signal.id, {
            "size_remaining_pct":  remaining,
            "partial_closed":      True,
            "partial_close_price": exit_price,
            "partial_close_at":    exit_at,
            "partial_pnl_pct":     pnl_pct.quantize(Decimal("0.0001")),
            "current_stop_loss":   entry,    # SL → entry after partial close
            "breakeven_moved":     True,
        })
        logger.info(
            f"[Tracker] Signal {signal.id} partial close @ {exit_price}: "
            f"50% at {pnl_pct:.4f}%, SL moved to entry {entry}"
        )

    async def _close_signal(
        self,
        db: AsyncSession,
        signal: Signal,
        exit_price: Decimal,
        exit_at: datetime.datetime,
        exit_reason: str,
        position: Any,
        pip_size: Decimal,
    ) -> None:
        """Close signal: create signal_result and close virtual position."""
        actual_entry = (
            (position.entry_price if position else None)
            or signal.entry_price
            or exit_price
        )
        pnl_pips, pnl_pct = self._calculate_pnl(
            signal.direction, actual_entry, exit_price, pip_size
        )
        result = "win" if pnl_pips > 0 else ("loss" if pnl_pips < 0 else "breakeven")

        # SIM-04: duration from entry_filled_at
        entry_time = (
            _utc(position.entry_filled_at if position else None)
            or _utc(signal.created_at)
        )
        duration_minutes = None
        if entry_time:
            duration_minutes = int((exit_at - entry_time).total_seconds() / 60)

        # SIM-01: MFE/MAE from DB
        mfe = (position.mfe if position else None) or Decimal("0")
        mae = (position.mae if position else None) or Decimal("0")

        # SIM-06: P&L USD with position size
        pnl_usd_val = self._pnl_usd(pnl_pct, signal.position_size_pct)

        # SIM-07: blended P&L when partial close happened
        partial_pnl_pct = position.partial_pnl_pct if position else None
        partial_close_pnl_usd: Optional[Decimal] = None
        full_close_pnl_usd: Optional[Decimal] = None
        if partial_pnl_pct is not None and position and position.partial_closed:
            partial_close_pnl_usd = (
                self._pnl_usd(partial_pnl_pct, signal.position_size_pct) * Decimal("0.5")
            )
            full_close_pnl_usd = (
                self._pnl_usd(pnl_pct, signal.position_size_pct) * Decimal("0.5")
            )
            blended_pct = (partial_pnl_pct + pnl_pct) / Decimal("2")
            pnl_usd_val = self._pnl_usd(blended_pct, signal.position_size_pct)

        result_data: dict[str, Any] = {
            "signal_id":               signal.id,
            "entry_actual_price":      actual_entry,
            "entry_filled_at":         entry_time,
            "exit_at":                 exit_at,
            "exit_price":              exit_price,
            "exit_reason":             exit_reason,
            "pnl_pips":               pnl_pips.quantize(Decimal("0.01")),
            "pnl_percent":             pnl_pct.quantize(Decimal("0.0001")),
            "pnl_usd":                 pnl_usd_val.quantize(Decimal("0.01")),
            "result":                  result,
            "max_favorable_excursion": mfe,
            "max_adverse_excursion":   mae,
            "duration_minutes":        duration_minutes,
        }
        if partial_close_pnl_usd is not None:
            result_data["partial_close_pnl_usd"] = partial_close_pnl_usd.quantize(Decimal("0.01"))
            result_data["full_close_pnl_usd"]    = full_close_pnl_usd.quantize(Decimal("0.01"))

        async with db.begin_nested():
            await create_signal_result(db, result_data)
            await update_signal_status(db, signal.id, "completed")
            try:
                if position and position.status == "open":
                    await close_virtual_position(
                        db,
                        signal_id=signal.id,
                        close_price=exit_price,
                        entry_price=actual_entry,
                        direction=signal.direction,
                    )
            except Exception as exc:
                logger.warning(
                    f"[Tracker] Failed to close virtual position for signal {signal.id}: {exc}"
                )

        logger.info(
            f"[Tracker] Signal {signal.id} completed: {exit_reason}, "
            f"PnL={pnl_pips:.1f} pips ({result}), USD={pnl_usd_val:.2f}"
        )

        try:
            instr = await get_instrument_by_id(db, signal.instrument_id)
            instrument_name = instr.name if instr else str(signal.instrument_id)
            await telegram.send_signal_result(
                instrument_name, signal.direction, exit_reason, float(pnl_pips)
            )
        except Exception as exc:
            logger.warning(f"[Tracker] Telegram notification failed: {exc}")

    # ── Batch check ───────────────────────────────────────────────────────────

    async def check_active_signals(self, db: Optional[AsyncSession] = None) -> None:
        if db is None:
            async with async_session_factory() as session:
                await self._run_checks(session)
        else:
            await self._run_checks(db)

    async def _run_checks(self, db: AsyncSession) -> None:
        signals = await get_active_signals(db)
        if not signals:
            return
        logger.debug(f"[Tracker] Checking {len(signals)} active signals")
        for signal in signals:
            try:
                await self.check_signal(db, signal)
            except Exception as exc:
                logger.error(f"[Tracker] Error checking signal {signal.id}: {exc}")
