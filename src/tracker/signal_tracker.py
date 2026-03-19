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
    create_virtual_account_if_not_exists,
    get_active_signals,
    get_instrument_by_id,
    get_latest_funding_rate,
    get_price_data,
    get_virtual_account,
    get_virtual_position,
    update_signal_status,
    update_virtual_account,
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

# ── SL slippage constants (SIM-10) ────────────────────────────────────────────

SL_SLIPPAGE_PIPS: dict[str, Decimal] = {
    "forex":  Decimal("1.0"),   # 1 pip — нормальные условия ECN
    "stocks": Decimal("1.0"),   # 1 цент / 0.01 pip_size
    "crypto": Decimal("0.0"),   # для крипто — % от цены
}
SL_SLIPPAGE_CRYPTO_PCT: Decimal = Decimal("0.001")  # 0.1% — taker при пробое

# ── Overnight swap constants (SIM-13, SIM-37) ─────────────────────────────────

# SIM-37: Hardcoded fallback rates (used when config/swap_rates.json is missing)
# Типовые дневные swap-ставки (в пипах за 1 лот, ориентировочно)
# Положительный = начисляется держателю позиции
SWAP_DAILY_PIPS_HARDCODE: dict[str, dict[str, Decimal]] = {
    "EURUSD=X": {"long": Decimal("-0.5"),  "short": Decimal("0.3")},
    "USDJPY=X": {"long": Decimal("1.2"),   "short": Decimal("-1.5")},
    "GBPUSD=X": {"long": Decimal("-0.8"),  "short": Decimal("0.5")},
    "AUDUSD=X": {"long": Decimal("0.2"),   "short": Decimal("-0.4")},
    "USDCAD=X": {"long": Decimal("0.4"),   "short": Decimal("-0.7")},
    "USDCHF=X": {"long": Decimal("-0.3"),  "short": Decimal("0.1")},
    "NZDUSD=X": {"long": Decimal("0.1"),   "short": Decimal("-0.3")},
}

TRIPLE_SWAP_WEEKDAY: int = 2    # Wednesday (0=Mon)
ROLLOVER_HOUR_UTC: int = 22     # 22:00 UTC


def _load_swap_rates() -> dict[str, dict[str, Decimal]]:
    """SIM-37: Load swap rates from config/swap_rates.json. Falls back to hardcode."""
    import json
    import os

    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "config", "swap_rates.json"
    )
    try:
        with open(config_path, "r") as f:
            data = json.load(f)

        # Check staleness (> 90 days)
        updated_at_str = data.get("updated_at", "")
        if updated_at_str:
            import datetime as _dt
            try:
                updated_at = _dt.datetime.fromisoformat(updated_at_str).date()
                days_old = (_dt.date.today() - updated_at).days
                if days_old > 90:
                    logger.warning("[SIM-37] Swap rates are stale (%d days old)", days_old)
            except ValueError:
                pass

        rates: dict[str, dict[str, Decimal]] = {}
        for symbol, vals in data.get("rates", {}).items():
            rates[symbol] = {
                "long": Decimal(str(vals["long"])),
                "short": Decimal(str(vals["short"])),
            }
        logger.info("[SIM-37] Loaded swap rates for %d symbols from JSON", len(rates))
        return rates
    except FileNotFoundError:
        logger.warning("[SIM-37] config/swap_rates.json not found, using hardcoded rates")
        return SWAP_DAILY_PIPS_HARDCODE
    except Exception as exc:
        logger.warning("[SIM-37] Failed to load swap rates: %s, using hardcoded", exc)
        return SWAP_DAILY_PIPS_HARDCODE


# SIM-37: Load from JSON (falls back to hardcode if file missing or unreadable)
SWAP_DAILY_PIPS = _load_swap_rates()


# ── SIM-34: Breakeven buffer — SL moves to 50% of distance to TP1 ─────────────

BREAKEVEN_BUFFER_RATIO = Decimal("0.5")

# ── SIM-35: Time-based exit for stale positions ───────────────────────────────

TIME_EXIT_CANDLES: dict[str, int] = {
    "H1": 48,   # 48 hours
    "H4": 20,   # ~3.3 days
    "D1": 10,   # 2 weeks
}

# ── MAE Early Exit config (SIM-20) ────────────────────────────────────────────

MAE_EARLY_EXIT_CONFIG: dict = {
    "enabled": True,
    "threshold_pct_of_sl": Decimal("0.60"),   # MAE >= 60% of SL distance
    "min_candles": 3,                          # minimum 3 candles elapsed
    "mfe_max_ratio": Decimal("0.20"),          # MFE < 20% of MAE
}

# Timeframe → approximate seconds per candle (for candles_elapsed estimate)
_TF_SECONDS: dict[str, int] = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "H12": 43200,
    "D1": 86400, "W1": 604800,
}


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

    # ── Candle High/Low (SIM-09) ──────────────────────────────────────────────

    async def _get_candle_prices(
        self,
        db: AsyncSession,
        instrument_id: int,
        timeframe: str,
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Return (last_close, candle_high, candle_low) of last completed candle.

        Falls back to H1 if no data for the requested timeframe.
        """
        records = await get_price_data(db, instrument_id, timeframe, limit=1)
        if not records:
            records = await get_price_data(db, instrument_id, "H1", limit=1)
        if not records:
            return Decimal("0"), Decimal("0"), Decimal("0")
        candle = records[-1]
        return (
            Decimal(str(candle.close)),
            Decimal(str(candle.high)),
            Decimal(str(candle.low)),
        )

    # ── SL slippage (SIM-10) ──────────────────────────────────────────────────

    @staticmethod
    def _apply_sl_slippage(
        sl_price: Decimal,
        direction: str,
        market: str,
        pip_size: Decimal,
    ) -> Decimal:
        """Ухудшить цену выхода по SL на величину проскальзывания (SIM-10).

        LONG SL: цена исполнения ниже SL (хуже для покупателя)
        SHORT SL: цена исполнения выше SL (хуже для продавца)
        """
        if market == "crypto":
            slip = sl_price * SL_SLIPPAGE_CRYPTO_PCT
        else:
            slip = SL_SLIPPAGE_PIPS.get(market, Decimal("1.0")) * pip_size

        return sl_price - slip if direction == "LONG" else sl_price + slip

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

    # ── SL/TP checks with candle High/Low (SIM-09) ───────────────────────────

    def _check_sl_hit(
        self,
        current_price: Decimal,
        stop_loss: Optional[Decimal],
        direction: str,
        candle_low: Optional[Decimal] = None,
        candle_high: Optional[Decimal] = None,
    ) -> bool:
        """SIM-09: SL hit if current_price OR candle extreme breaches the level."""
        if not stop_loss:
            return False
        if direction == "LONG":
            by_price = current_price <= stop_loss
            by_candle = (candle_low is not None) and (candle_low <= stop_loss)
            return by_price or by_candle
        else:
            by_price = current_price >= stop_loss
            by_candle = (candle_high is not None) and (candle_high >= stop_loss)
            return by_price or by_candle

    def _check_tp_hit(
        self,
        current_price: Decimal,
        take_profit: Optional[Decimal],
        direction: str,
        candle_high: Optional[Decimal] = None,
        candle_low: Optional[Decimal] = None,
    ) -> tuple[bool, str]:
        """SIM-09: TP hit if current_price OR candle extreme reaches the level.

        Returns (hit, tp_name) for backward compatibility with v2 callers.
        """
        if not take_profit:
            return False, ""
        if direction == "LONG":
            by_price = current_price >= take_profit
            by_candle = (candle_high is not None) and (candle_high >= take_profit)
            hit = by_price or by_candle
        else:
            by_price = current_price <= take_profit
            by_candle = (candle_low is not None) and (candle_low <= take_profit)
            hit = by_price or by_candle
        return hit, ("tp_hit" if hit else "")

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
        account_balance: Optional[Decimal] = None,
    ) -> Decimal:
        """SIM-06/SIM-16: pnl_usd = balance × (size_pct/100) × (pnl_pct/100).

        v3: uses account_balance_at_entry snapshot if provided (SIM-16).
        v2 compat: if account_balance is None → falls back to ACCOUNT_SIZE.
        """
        balance = account_balance if account_balance is not None else ACCOUNT_SIZE
        size = position_size_pct if (position_size_pct and position_size_pct > 0) else Decimal("2.0")
        return balance * (size / Decimal("100")) * (pnl_pct / Decimal("100"))

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

    # ── MAE Early Exit (SIM-20) ───────────────────────────────────────────────

    def _check_mae_early_exit(
        self,
        position: Any,
        signal: "Signal",
        current_price: Decimal,
        now: datetime.datetime,
    ) -> bool:
        """Return True if the position should be closed early due to MAE threshold.

        Conditions (all must be true):
          1. MAE_EARLY_EXIT_CONFIG["enabled"] is True
          2. mae_ratio = abs(mae) / sl_distance >= threshold_pct_of_sl
          3. candles_elapsed >= min_candles (estimated from entry_filled_at + timeframe)
          4. mfe == 0 OR abs(mae) / abs(mfe) >= 1 / mfe_max_ratio

        Graceful on zero-division (sl_distance=0, mfe=0): returns False.
        """
        cfg = MAE_EARLY_EXIT_CONFIG
        if not cfg.get("enabled"):
            return False

        mae: Decimal = position.mae if position.mae is not None else Decimal("0")
        current_sl: Optional[Decimal] = position.current_stop_loss or signal.stop_loss
        entry_price: Optional[Decimal] = position.entry_actual_price or signal.entry_price

        # Guard: need SL distance to compute ratio
        if current_sl is None or entry_price is None:
            return False

        sl_distance = abs(entry_price - current_sl)
        if sl_distance == 0:
            return False

        mae_ratio = abs(mae) / sl_distance
        if mae_ratio < cfg["threshold_pct_of_sl"]:
            return False

        # Candles elapsed estimate from entry time and timeframe
        entry_time = position.entry_filled_at
        if entry_time is None:
            return False

        timeframe = signal.timeframe or "H1"
        tf_seconds = _TF_SECONDS.get(timeframe, 3600)
        elapsed_seconds = (now - _utc(entry_time)).total_seconds()
        candles_elapsed = int(elapsed_seconds / tf_seconds)

        if candles_elapsed < cfg["min_candles"]:
            return False

        # MFE ratio check
        mfe: Decimal = position.mfe if position.mfe is not None else Decimal("0")
        if mfe > 0:
            mfe_ratio = mfe / abs(mae) if abs(mae) > 0 else Decimal("999")
            # mfe < 20% of mae ↔ mae/mfe >= 1/0.20 = 5.0 ↔ mfe_ratio < mfe_max_ratio
            if mfe_ratio >= cfg["mfe_max_ratio"]:
                # MFE is large enough relative to MAE — don't early exit
                return False

        logger.info(
            "[SIM-20] MAE early exit: signal=%d mae_ratio=%.2f candles=%d mfe=%.6f mae=%.6f",
            signal.id, float(mae_ratio), candles_elapsed, float(mfe), float(mae),
        )
        return True

    # ── ATR helper (SIM-11: live Wilder's ATR with fallback chain) ───────────

    async def _get_live_atr(
        self,
        db: AsyncSession,
        instrument_id: int,
        timeframe: str,
        period: int = 14,
    ) -> Optional[Decimal]:
        """Compute ATR(14) from recent price_data candles using Wilder's smoothing (SIM-11).

        Returns None if insufficient candles for the calculation.
        """
        candles = await get_price_data(db, instrument_id, timeframe, limit=period + 1)
        if len(candles) < period + 1:
            return None

        trs: list[Decimal] = []
        for i in range(1, len(candles)):
            h = Decimal(str(candles[i].high))
            l = Decimal(str(candles[i].low))
            prev_c = Decimal(str(candles[i - 1].close))
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            trs.append(tr)

        # Wilder's smoothing: seed with SMA of first period, then smooth
        atr: Decimal = sum(trs[:period]) / Decimal(str(period))
        for tr in trs[period:]:
            atr = (atr * Decimal(str(period - 1)) + tr) / Decimal(str(period))
        return atr

    async def _get_atr(self, db: AsyncSession, signal: Signal, instrument: Any) -> Decimal:
        """Get ATR using fallback chain (SIM-11):
        1. Live ATR from price_data (signal's timeframe)
        2. Live ATR from price_data (H1)
        3. ATR from indicators_snapshot
        4. 14 × pip_size
        """
        pip_size = instrument.pip_size if instrument and instrument.pip_size else Decimal("0.0001")

        # 1. Live ATR — signal timeframe
        live_atr = await self._get_live_atr(db, signal.instrument_id, signal.timeframe)
        if live_atr and live_atr > 0:
            return live_atr

        # 2. Live ATR — H1 fallback
        if signal.timeframe != "H1":
            live_atr_h1 = await self._get_live_atr(db, signal.instrument_id, "H1")
            if live_atr_h1 and live_atr_h1 > 0:
                logger.warning(
                    f"[Tracker] ATR: no live data for {signal.timeframe}, "
                    f"using H1 ATR for signal {signal.id}"
                )
                return live_atr_h1

        # 3. Snapshot ATR
        try:
            if signal.indicators_snapshot:
                indicators = json.loads(signal.indicators_snapshot)
                for key in ("atr", "ATR", "atr_14", "ATR_14"):
                    atr_val = indicators.get(key)
                    if atr_val and float(atr_val) > 0:
                        logger.warning(
                            f"[Tracker] ATR: no live data, using snapshot for signal {signal.id}"
                        )
                        return Decimal(str(atr_val))
        except Exception:
            pass

        # 4. Last resort: 14 × pip_size
        logger.warning(
            f"[Tracker] ATR: all sources failed, using 14×pip_size for signal {signal.id}"
        )
        return pip_size * Decimal("14")

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

        # ── Get live price + candle High/Low (SIM-09) ────────────────────────
        if current_price is None:
            current_price = await self._get_current_price(db, signal.instrument_id)
        if current_price is None:
            logger.debug(f"[Tracker] No price data for signal {signal.id}")
            return

        # SIM-09: fetch last candle high/low for the signal's timeframe
        _close, candle_high, candle_low = await self._get_candle_prices(
            db, signal.instrument_id, signal.timeframe
        )
        # Use current_price if candle data unavailable (candle_high/low = 0 means no data)
        if candle_high == Decimal("0"):
            candle_high = current_price
        if candle_low == Decimal("0"):
            candle_low = current_price

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

            # SIM-13: accrue daily swap at rollover time
            await self._apply_daily_swap(db, signal, position, instrument, now)

            # Update unrealized P&L (SIM-12: pass position for sizing)
            await self._update_virtual_unrealized(db, signal, current_price, actual_entry, position)
            # Update MFE/MAE in DB (SIM-01)
            await self._update_mfe_mae(db, signal, current_price, actual_entry)
            # Reload position after MAE update so latest values are visible
            position = await get_virtual_position(db, signal.id) or position

            # SIM-20: MAE Early Exit — check after MAE update, before SL/TP
            if self._check_mae_early_exit(position, signal, current_price, now):
                await self._close_signal(
                    db, signal, current_price, now, "mae_early_exit", position, pip_size,
                    candle_high_at_exit=candle_high, candle_low_at_exit=candle_low,
                    market=market,
                )
                return

            # SIM-35: Time-based exit for stale positions (only if not profitable)
            tf = signal.timeframe
            max_candles = TIME_EXIT_CANDLES.get(tf)
            if max_candles is not None and position.entry_filled_at:
                entry_time = _utc(position.entry_filled_at)
                if entry_time:
                    tf_seconds = _TF_SECONDS.get(tf, 3600)
                    candles_elapsed = int((now - entry_time).total_seconds() / tf_seconds)
                    if candles_elapsed >= max_candles:
                        unrealized = position.unrealized_pnl_usd or Decimal("0")
                        if unrealized <= Decimal("0"):
                            logger.info(
                                "[SIM-35] Time exit: %s/%s after %d candles (max=%d), "
                                "unrealized=%.2f",
                                signal.instrument_id, tf, candles_elapsed, max_candles,
                                float(unrealized),
                            )
                            await self._close_signal(
                                db, signal, current_price, now, "time_exit",
                                position, pip_size, market=market,
                            )
                            return

            # SIM-05: use TradeLifecycleManager when SL and TP1 are defined
            if signal.stop_loss and signal.take_profit_1:
                atr = await self._get_atr(db, signal, instrument)
                current_sl = position.current_stop_loss or signal.stop_loss

                # SIM-09: candle-based SL/TP pre-check (worst case: SL wins)
                sl_hit = self._check_sl_hit(
                    current_price, current_sl, signal.direction, candle_low, candle_high
                )
                tp1_hit, _ = self._check_tp_hit(
                    current_price, signal.take_profit_1, signal.direction, candle_high, candle_low
                )

                if sl_hit or tp1_hit:
                    # SIM-09: worst case rule — if both hit, prefer SL
                    if sl_hit and tp1_hit:
                        exit_reason = "trailing_sl_hit" if position.trailing_stop else "sl_hit"
                        exit_price = current_sl
                        await self._close_signal(
                            db, signal, exit_price, now, exit_reason, position, pip_size,
                            candle_high_at_exit=candle_high, candle_low_at_exit=candle_low,
                            market=market,
                        )
                    elif sl_hit:
                        exit_reason = "trailing_sl_hit" if position.trailing_stop else "sl_hit"
                        exit_price = current_sl
                        await self._close_signal(
                            db, signal, exit_price, now, exit_reason, position, pip_size,
                            candle_high_at_exit=candle_high, candle_low_at_exit=candle_low,
                            market=market,
                        )
                    else:
                        # SIM-24 fix: TP1 hit — check if partial close should happen first
                        # SIM-07: if not yet partially closed, take 50% profit and let rest run
                        exit_price = signal.take_profit_1
                        if not position.partial_closed:
                            await self._partial_close(
                                db, signal, position, exit_price, now, pip_size
                            )
                        else:
                            # Already partially closed → close remaining half at TP1
                            await self._close_signal(
                                db, signal, exit_price, now, "tp1_hit", position, pip_size,
                                candle_high_at_exit=candle_high, candle_low_at_exit=candle_low,
                                market=market,
                            )
                else:
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
                            db, signal, current_price, now, exit_reason, position, pip_size,
                            candle_high_at_exit=candle_high, candle_low_at_exit=candle_low,
                            market=market,
                        )

                    elif act == "breakeven":
                        await update_virtual_position(db, signal.id, {
                            "current_stop_loss": action["new_stop_loss"],
                            "breakeven_moved":   True,
                            "breakeven_price":   action["new_stop_loss"],
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
                # Fallback: simple SL/TP check with candle H/L (SIM-09)
                tp2_hit, _ = self._check_tp_hit(
                    current_price, signal.take_profit_2, signal.direction, candle_high, candle_low
                )
                tp1_hit, _ = self._check_tp_hit(
                    current_price, signal.take_profit_1, signal.direction, candle_high, candle_low
                )
                sl_hit = self._check_sl_hit(
                    current_price, signal.stop_loss, signal.direction, candle_low, candle_high
                )

                # Determine exit_price (level, not current_price, for audit accuracy)
                if sl_hit and (tp1_hit or tp2_hit):
                    # Worst case: SL wins
                    exit_price_fb = signal.stop_loss or current_price
                    await self._close_signal(
                        db, signal, exit_price_fb, now, "sl_hit", position, pip_size,
                        candle_high_at_exit=candle_high, candle_low_at_exit=candle_low,
                        market=market,
                    )
                elif tp2_hit:
                    exit_price_fb = signal.take_profit_2 or current_price
                    await self._close_signal(
                        db, signal, exit_price_fb, now, "tp2_hit", position, pip_size,
                        candle_high_at_exit=candle_high, candle_low_at_exit=candle_low,
                        market=market,
                    )
                elif tp1_hit:
                    exit_price_fb = signal.take_profit_1 or current_price
                    await self._close_signal(
                        db, signal, exit_price_fb, now, "tp1_hit", position, pip_size,
                        candle_high_at_exit=candle_high, candle_low_at_exit=candle_low,
                        market=market,
                    )
                elif sl_hit:
                    exit_price_fb = signal.stop_loss or current_price
                    await self._close_signal(
                        db, signal, exit_price_fb, now, "sl_hit", position, pip_size,
                        candle_high_at_exit=candle_high, candle_low_at_exit=candle_low,
                        market=market,
                    )

    # ── Lifecycle helpers ─────────────────────────────────────────────────────

    async def _open_virtual_position(
        self,
        db: AsyncSession,
        signal: Signal,
        actual_entry_price: Decimal,
        entry_filled_at: datetime.datetime,
    ) -> None:
        """Create virtual portfolio position at spread-adjusted entry (SIM-02, SIM-16)."""
        try:
            if await get_virtual_position(db, signal.id) is not None:
                return  # idempotent

            size_pct = Decimal("2.0")  # default risk %
            if signal.position_size_pct and signal.position_size_pct > 0:
                size_pct = Decimal(str(signal.position_size_pct))

            # SIM-16: snapshot current balance at entry time
            account = await get_virtual_account(db)
            account_balance_at_entry = account.current_balance if account else ACCOUNT_SIZE

            await create_virtual_position(db, data={
                "signal_id":                signal.id,
                "size_pct":                 size_pct,
                "entry_price":              actual_entry_price,
                "status":                   "open",
                "entry_filled_at":          entry_filled_at,
                "current_stop_loss":        signal.stop_loss,
                "size_remaining_pct":       Decimal("1.0"),
                "mfe":                      Decimal("0"),
                "mae":                      Decimal("0"),
                "breakeven_moved":          False,
                "partial_closed":           False,
                "account_balance_at_entry": account_balance_at_entry,
            })
            logger.info(
                f"[Tracker] Virtual position opened for signal {signal.id} @ {actual_entry_price} "
                f"(balance_at_entry={account_balance_at_entry})"
            )
        except Exception as exc:
            logger.warning(
                f"[Tracker] Failed to open virtual position for signal {signal.id}: {exc}"
            )

    async def _update_account_balance(
        self,
        db: AsyncSession,
        realized_pnl_usd: Decimal,
    ) -> None:
        """SIM-16: update virtual_account balance after a (partial) close."""
        try:
            account = await get_virtual_account(db)
            if account is None:
                logger.warning("[Tracker] _update_account_balance: no virtual_account row found")
                return

            new_balance = account.current_balance + realized_pnl_usd
            new_peak = max(account.peak_balance, new_balance)

            await update_virtual_account(db, {
                "current_balance":    new_balance,
                "peak_balance":       new_peak,
                "total_realized_pnl": account.total_realized_pnl + realized_pnl_usd,
                "total_trades":       account.total_trades + 1,
                "updated_at":         datetime.datetime.now(datetime.timezone.utc),
            })
        except Exception as exc:
            logger.error(f"[Tracker] _update_account_balance failed: {exc}")

    async def _apply_daily_swap(
        self,
        db: AsyncSession,
        signal: Any,
        position: Any,
        instrument: Any,
        now: datetime.datetime,
    ) -> None:
        """SIM-13: Accrue daily swap for open positions at rollover time (22:00 UTC).

        Conditions:
        1. Position is open or partial
        2. Current time >= 22:00 UTC
        3. Swap not yet accrued today (last_swap_date != today)
        """
        try:
            if position.status not in ("open", "partial"):
                return
            if now.hour < ROLLOVER_HOUR_UTC:
                return
            today = now.date()
            if position.last_swap_date and position.last_swap_date >= today:
                return

            market = instrument.market if instrument else "unknown"
            symbol = instrument.symbol if instrument else ""
            direction_key = signal.direction.lower()

            swap_pips = Decimal("0")

            if market == "crypto":
                funding_rate = await get_latest_funding_rate(db, instrument.id)
                if funding_rate is not None:
                    # Long pays when rate > 0; short profits
                    swap_pct = funding_rate * (
                        Decimal("-1") if signal.direction == "LONG" else Decimal("1")
                    )
                    # Convert pct to pip equivalent (approximate)
                    pip_size = instrument.pip_size if instrument.pip_size else Decimal("0.01")
                    entry = position.entry_price or Decimal("1")
                    swap_pips = (entry * swap_pct / pip_size).quantize(Decimal("0.0001"))
                # else: zero funding rate → no swap
            elif symbol in SWAP_DAILY_PIPS:
                daily_rates = SWAP_DAILY_PIPS[symbol]
                rate = daily_rates.get(direction_key, Decimal("0"))
                multiplier = Decimal("3") if now.weekday() == TRIPLE_SWAP_WEEKDAY else Decimal("1")
                swap_pips = (rate * multiplier).quantize(Decimal("0.0001"))
            else:
                # Instrument not in table — no swap
                logger.warning(
                    f"[Tracker] Swap: no rate for {symbol} ({market}), skipping signal {signal.id}"
                )
                return

            # Convert pips to USD
            pip_size = instrument.pip_size if instrument.pip_size else Decimal("0.0001")
            size_pct = position.size_pct or Decimal("2.0")
            _raw_bal2 = position.account_balance_at_entry
            balance = _raw_bal2 if isinstance(_raw_bal2, Decimal) else ACCOUNT_SIZE
            # swap_usd = swap_pips × pip_size × position_value / entry_price
            # = fractional price move × notional position value
            # (dividing by entry_price converts from quote units to base-currency position)
            entry_price = position.entry_price or Decimal("1")
            position_value = balance * size_pct / Decimal("100")
            swap_usd = swap_pips * pip_size * position_value / entry_price
            swap_usd = swap_usd.quantize(Decimal("0.0001"))

            new_accrued_pips = (position.accrued_swap_pips or Decimal("0")) + swap_pips
            new_accrued_usd = (position.accrued_swap_usd or Decimal("0")) + swap_usd

            await update_virtual_position(db, signal.id, {
                "accrued_swap_pips": new_accrued_pips,
                "accrued_swap_usd":  new_accrued_usd,
                "last_swap_date":    today,
            })
            logger.info(
                f"[Tracker] Swap accrued for signal {signal.id}: "
                f"{swap_pips} pips / {swap_usd} USD (total: {new_accrued_pips} pips)"
            )
        except Exception as exc:
            logger.error(f"[Tracker] _apply_daily_swap failed for signal {signal.id}: {exc}")

    async def _update_virtual_unrealized(
        self,
        db: AsyncSession,
        signal: Signal,
        current_price: Decimal,
        entry_price: Decimal,
        position: Optional[Any] = None,
    ) -> None:
        """SIM-12: update unrealized P&L with position sizing and partial close awareness."""
        try:
            if signal.direction == "LONG":
                move_pct = (current_price - entry_price) / entry_price * Decimal("100")
            else:
                move_pct = (entry_price - current_price) / entry_price * Decimal("100")

            updates: dict[str, Any] = {
                "current_price":      current_price,
                "unrealized_pnl_pct": move_pct.quantize(Decimal("0.0001")),
            }

            # SIM-12: compute unrealized_pnl_usd with effective position size
            if position is not None:
                size_pct = position.size_pct or Decimal("2.0")
                remaining = position.size_remaining_pct or Decimal("1.0")
                effective_size = size_pct * remaining
                _raw_b = position.account_balance_at_entry
                balance = _raw_b if isinstance(_raw_b, Decimal) else ACCOUNT_SIZE
                unrealized_usd = (
                    balance
                    * (effective_size / Decimal("100"))
                    * (move_pct / Decimal("100"))
                )
                updates["unrealized_pnl_usd"] = unrealized_usd.quantize(Decimal("0.01"))

            await update_virtual_position(db, signal.id, updates)
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
        """SIM-07: Close 50% at TP1 zone, move SL to breakeven buffer (SIM-34)."""
        entry = position.entry_price or signal.entry_price or exit_price
        _, pnl_pct = self._calculate_pnl(signal.direction, entry, exit_price, pip_size)
        remaining = (position.size_remaining_pct or Decimal("1.0")) * Decimal("0.5")

        # SIM-34: Move SL to entry + 50% of distance to TP1 (not just entry)
        tp1 = signal.take_profit_1
        if tp1 is not None:
            if signal.direction == "LONG":
                new_sl = entry + BREAKEVEN_BUFFER_RATIO * (tp1 - entry)
            else:
                new_sl = entry - BREAKEVEN_BUFFER_RATIO * (entry - tp1)
            new_sl = new_sl.quantize(Decimal("0.00000001"))
        else:
            new_sl = entry  # fallback to entry if no TP1

        await update_virtual_position(db, signal.id, {
            "size_remaining_pct":  remaining,
            "partial_closed":      True,
            "partial_close_price": exit_price,
            "partial_close_at":    exit_at,
            "partial_pnl_pct":     pnl_pct.quantize(Decimal("0.0001")),
            "current_stop_loss":   new_sl,   # SIM-34: SL → entry + 50% buffer
            "breakeven_moved":     True,
            "breakeven_price":     new_sl,   # record exact BE level for analysis
        })

        # SIM-16: update account balance for the 50% partial close P&L
        _raw_bal = getattr(position, "account_balance_at_entry", None)
        account_balance = _raw_bal if isinstance(_raw_bal, Decimal) else None
        partial_pnl_usd = self._pnl_usd(pnl_pct, signal.position_size_pct, account_balance) * Decimal("0.5")
        await self._update_account_balance(db, partial_pnl_usd.quantize(Decimal("0.01")))

        logger.info(
            f"[Tracker] Signal {signal.id} partial close @ {exit_price}: "
            f"50% at {pnl_pct:.4f}%, SL moved to {new_sl} (SIM-34 buffer), "
            f"partial_pnl_usd={partial_pnl_usd:.2f}"
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
        candle_high_at_exit: Optional[Decimal] = None,
        candle_low_at_exit: Optional[Decimal] = None,
        market: Optional[str] = None,
    ) -> None:
        """Close signal: create signal_result and close virtual position.

        SIM-09: candle_high_at_exit / candle_low_at_exit stored for audit.
        SIM-10: slippage applied for SL exits (market orders).
        SIM-14: composite_score copied from signal.
        """
        # SIM-10: apply slippage for SL exits
        exit_slippage_pips: Optional[Decimal] = None
        if exit_reason in ("sl_hit", "trailing_sl_hit") and market:
            slippage_adjusted = self._apply_sl_slippage(
                exit_price, signal.direction, market, pip_size
            )
            if market == "crypto":
                raw_slip_usd = exit_price - slippage_adjusted if signal.direction == "LONG" else slippage_adjusted - exit_price
                exit_slippage_pips = (raw_slip_usd / pip_size).quantize(Decimal("0.0001"))
            else:
                exit_slippage_pips = -SL_SLIPPAGE_PIPS.get(market, Decimal("1.0"))
                if signal.direction == "SHORT":
                    exit_slippage_pips = -exit_slippage_pips
            exit_price = slippage_adjusted

        actual_entry = (
            (position.entry_price if position else None)
            or signal.entry_price
            or exit_price
        )
        pnl_pips, pnl_pct = self._calculate_pnl(
            signal.direction, actual_entry, exit_price, pip_size
        )
        # result is determined below after all P&L adjustments (partial + swap)

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

        # SIM-06/SIM-16: P&L USD with position size and account balance snapshot
        _raw_acct = getattr(position, "account_balance_at_entry", None) if position else None
        account_balance = _raw_acct if isinstance(_raw_acct, Decimal) else None
        pnl_usd_val = self._pnl_usd(pnl_pct, signal.position_size_pct, account_balance)

        # SIM-07: blended P&L when partial close happened
        partial_pnl_pct = position.partial_pnl_pct if position else None
        partial_close_pnl_usd: Optional[Decimal] = None
        full_close_pnl_usd: Optional[Decimal] = None
        if partial_pnl_pct is not None and position and position.partial_closed:
            partial_close_pnl_usd = (
                self._pnl_usd(partial_pnl_pct, signal.position_size_pct, account_balance) * Decimal("0.5")
            )
            full_close_pnl_usd = (
                self._pnl_usd(pnl_pct, signal.position_size_pct, account_balance) * Decimal("0.5")
            )
            blended_pct = (partial_pnl_pct + pnl_pct) / Decimal("2")
            pnl_usd_val = self._pnl_usd(blended_pct, signal.position_size_pct, account_balance)

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
            "result":                  "breakeven",  # overridden below after all P&L adjustments
            "max_favorable_excursion": mfe,
            "max_adverse_excursion":   mae,
            "duration_minutes":        duration_minutes,
            # SIM-09: candle audit fields
            "candle_high_at_exit":     candle_high_at_exit,
            "candle_low_at_exit":      candle_low_at_exit,
            # SIM-10: slippage
            "exit_slippage_pips":      exit_slippage_pips,
            # SIM-13: accumulated swap
            "swap_pips":               (position.accrued_swap_pips if position else None) or Decimal("0"),
            "swap_usd":                (position.accrued_swap_usd if position else None) or Decimal("0"),
            # SIM-14: denormalized composite_score
            "composite_score":         Decimal(str(signal.composite_score)) if signal.composite_score else None,
        }

        # SIM-13: total P&L includes accrued swap
        accrued_swap_usd = Decimal(str(position.accrued_swap_usd or 0)) if position else Decimal("0")
        if accrued_swap_usd != Decimal("0"):
            total_pnl_with_swap = pnl_usd_val + accrued_swap_usd
            result_data["pnl_usd"] = total_pnl_with_swap.quantize(Decimal("0.01"))
        if partial_close_pnl_usd is not None:
            result_data["partial_close_pnl_usd"] = partial_close_pnl_usd.quantize(Decimal("0.01"))
            result_data["full_close_pnl_usd"]    = full_close_pnl_usd.quantize(Decimal("0.01"))

        # Determine result from final pnl_usd — includes blended partial close + swap.
        # Using pnl_pips alone would mislabel a partial-close win as "loss" when the
        # final SL leg is slightly negative but overall trade is profitable.
        _final_pnl = result_data["pnl_usd"]
        result_data["result"] = "win" if _final_pnl > 0 else ("loss" if _final_pnl < 0 else "breakeven")

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

        # SIM-16: update account balance for the final close P&L
        # For partial close: only the remaining 50% is settled here;
        # the first 50% was already applied in _partial_close().
        if position and position.partial_closed:
            final_pnl = full_close_pnl_usd if full_close_pnl_usd is not None else pnl_usd_val
            await self._update_account_balance(db, final_pnl.quantize(Decimal("0.01")))
        else:
            await self._update_account_balance(db, pnl_usd_val.quantize(Decimal("0.01")))

        logger.info(
            f"[Tracker] Signal {signal.id} completed: {exit_reason}, "
            f"PnL={pnl_pips:.1f} pips ({result}), USD={pnl_usd_val:.2f}"
        )

        try:
            instr = await get_instrument_by_id(db, signal.instrument_id)
            await telegram.send_position_closed(
                instrument_name=instr.name if instr else str(signal.instrument_id),
                instrument_symbol=instr.symbol if instr else "—",
                direction=signal.direction,
                exit_reason=exit_reason,
                entry_price=float(actual_entry),
                exit_price=float(exit_price),
                pnl_pips=float(pnl_pips),
                pnl_usd=float(pnl_usd_val),
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
