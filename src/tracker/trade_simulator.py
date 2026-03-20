"""Trade Simulator: live-price SL/TP monitoring for virtual positions.

Runs in background (no frontend required). Every minute:
  1. Gets all active signals with open virtual positions.
  2. Fetches the current live price from the exchange API.
  3. Delegates SL/TP checks to SignalTracker (with injected live price).
  4. Commits results to DB.

Account size: configurable via VIRTUAL_ACCOUNT_SIZE_USD setting.
P&L in USD (v2): account × (position_size_pct/100) × (pnl_pct/100)
"""

import asyncio
import logging
from decimal import Decimal
from typing import Optional

from src.config import settings

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
# v3: account size is now configurable via VIRTUAL_ACCOUNT_SIZE_USD (was hardcoded $1k)

ACCOUNT_SIZE = Decimal(str(settings.VIRTUAL_ACCOUNT_SIZE_USD))
ACCOUNT_SIZE_FLOAT = settings.VIRTUAL_ACCOUNT_SIZE_USD


# ── Live price fetching ───────────────────────────────────────────────────────

async def fetch_live_price(symbol: str, market: str) -> Optional[Decimal]:
    """Return the current market price from the exchange API.

    - crypto  → Binance via ccxt (public, no API key)
    - stocks/forex → yfinance fast_info (no API key)
    Returns None if the request fails (tracker will skip that position).
    """
    try:
        if market == "crypto":
            return await _ccxt_price(symbol)
        else:
            return await _yf_price(symbol)
    except Exception as exc:
        logger.warning(f"[Simulator] Price fetch failed for {symbol}: {exc}")
        return None


async def _yf_price(symbol: str) -> Optional[Decimal]:
    import yfinance as yf

    def _get() -> Optional[float]:
        try:
            info = yf.Ticker(symbol).fast_info
            # fast_info attributes differ by yfinance version
            price = getattr(info, "last_price", None) or getattr(info, "lastPrice", None)
            if price is None or price == 0:
                # Fallback: 1-day 1m history last close
                df = yf.Ticker(symbol).history(period="1d", interval="1m")
                if not df.empty:
                    price = float(df["Close"].iloc[-1])
            return price
        except Exception:
            return None

    price = await asyncio.get_event_loop().run_in_executor(None, _get)
    return Decimal(str(price)) if price else None


async def _ccxt_price(symbol: str) -> Optional[Decimal]:
    def _get() -> Optional[float]:
        try:
            import ccxt
            exchange = ccxt.binance({"enableRateLimit": True})
            ticker = exchange.fetch_ticker(symbol)
            return ticker.get("last") or ticker.get("close")
        except Exception:
            return None

    price = await asyncio.get_event_loop().run_in_executor(None, _get)
    return Decimal(str(price)) if price else None


# ── Position management ───────────────────────────────────────────────────────

async def open_position_for_signal(signal, db) -> bool:
    """Idempotently open a virtual portfolio position (fallback for tracking signals).

    Applies spread adjustment and records entry_filled_at.
    Returns True if a new position was created.
    """
    import datetime as _dt
    from src.database.crud import create_virtual_position, get_instrument_by_id, get_virtual_position
    from src.tracker.signal_tracker import _apply_spread

    try:
        if await get_virtual_position(db, signal.id) is not None:
            return False  # already open
        if signal.entry_price is None:
            return False

        instrument = await get_instrument_by_id(db, signal.instrument_id)
        market = instrument.market if instrument else "forex"
        pip_size = instrument.pip_size if instrument else Decimal("0.0001")

        # Apply spread (SIM-02)
        actual_price = _apply_spread(signal.entry_price, signal.direction, market, pip_size)
        spread_diff = abs(actual_price - signal.entry_price)
        spread_pips = (spread_diff / pip_size).quantize(Decimal("0.0001")) if pip_size > 0 else Decimal("0")

        size_pct = Decimal("2.0")
        if signal.position_size_pct and signal.position_size_pct > 0:
            size_pct = Decimal(str(signal.position_size_pct))

        # SIM-16: snapshot current balance at entry time
        from src.database.crud import get_virtual_account as _get_account  # noqa: PLC0415
        account = await _get_account(db)
        account_balance_at_entry = account.current_balance if account else ACCOUNT_SIZE

        now = _dt.datetime.now(_dt.timezone.utc)
        await create_virtual_position(db, data={
            "signal_id":                signal.id,
            "size_pct":                 size_pct,
            "entry_price":              actual_price,
            "status":                   "open",
            "entry_filled_at":          now,
            "current_stop_loss":        signal.stop_loss,
            "size_remaining_pct":       Decimal("1.0"),
            "mfe":                      Decimal("0"),
            "mae":                      Decimal("0"),
            "breakeven_moved":          False,
            "partial_closed":           False,
            "account_balance_at_entry": account_balance_at_entry,
            "spread_pips_applied":      spread_pips,
        })
        logger.info(
            f"[Simulator] Opened #{signal.id}: {signal.direction} "
            f"{signal.timeframe} @ {actual_price} (spread applied)"
        )

        # Telegram notification
        try:
            from src.notifications.telegram import telegram
            await telegram.send_position_opened(
                instrument_symbol=instrument.symbol if instrument else "—",
                instrument_name=instrument.name if instrument else "—",
                direction=signal.direction,
                timeframe=signal.timeframe,
                entry_price=float(actual_price),
                stop_loss=float(signal.stop_loss) if signal.stop_loss else None,
                take_profit_1=float(signal.take_profit_1) if signal.take_profit_1 else None,
                take_profit_2=float(signal.take_profit_2) if signal.take_profit_2 else None,
                size_pct=float(size_pct),
                risk_reward=float(signal.risk_reward) if signal.risk_reward else None,
                spread_pips=float(spread_pips) if spread_pips else None,
            )
        except Exception as _tg_exc:
            logger.warning(f"[Simulator] Telegram open-alert failed: {_tg_exc}")

        return True
    except Exception as exc:
        logger.warning(f"[Simulator] Failed to open position #{signal.id}: {exc}")
        return False


# ── Main simulator tick ───────────────────────────────────────────────────────

async def run_simulator_tick() -> None:
    """Single tick: check all open simulator positions with live prices."""
    from src.database.crud import get_active_signals, get_instrument_by_id
    from src.database.engine import async_session_factory
    from src.tracker.signal_tracker import SignalTracker

    async with async_session_factory() as db:
        signals = await get_active_signals(db)
        if not signals:
            return

        tracker = SignalTracker()
        opened = 0
        checked = 0
        errors = 0

        for signal in signals:
            try:
                instrument = await get_instrument_by_id(db, signal.instrument_id)
                if instrument is None:
                    continue

                # Ensure a virtual position exists for "tracking" signals
                if signal.status == "tracking" and signal.entry_price is not None:
                    if await open_position_for_signal(signal, db):
                        opened += 1

                # Fetch live price (slight delay between API calls to avoid rate limits)
                live_price = await fetch_live_price(instrument.symbol, instrument.market)
                if live_price is None:
                    logger.debug(f"[Simulator] No live price for {instrument.symbol}")
                    continue

                # Run SL/TP check with injected live price
                await tracker.check_signal(db, signal, current_price=live_price)
                checked += 1
                await asyncio.sleep(0.2)  # rate-limit friendly pause

            except Exception as exc:
                logger.warning(f"[Simulator] Signal #{signal.id} error: {exc}")
                errors += 1

        if opened or checked:
            await db.commit()
            logger.info(
                f"[Simulator] Tick done — opened: {opened}, checked: {checked}, errors: {errors}"
            )


# ── Stats helpers (used by API) ───────────────────────────────────────────────

def pnl_usd(pnl_pct: Optional[float], position_size_pct: Optional[float] = None) -> float:
    """Convert pnl_percent (%) to USD with position sizing (SIM-06).

    v2: pnl_usd = account × (size_pct/100) × (pnl_pct/100)
    v1 compat: if position_size_pct is None, uses 100% (old behaviour, overestimates).
    """
    if pnl_pct is None:
        return 0.0
    size = position_size_pct if (position_size_pct and position_size_pct > 0) else 100.0
    return round(float(pnl_pct) / 100.0 * (size / 100.0) * ACCOUNT_SIZE_FLOAT, 2)
