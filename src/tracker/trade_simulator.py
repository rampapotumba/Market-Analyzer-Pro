"""Trade Simulator: live-price SL/TP monitoring for virtual positions.

Runs in background (no frontend required). Every minute:
  1. Gets all active signals with open virtual positions.
  2. Fetches the current live price from the exchange API.
  3. Delegates SL/TP checks to SignalTracker (with injected live price).
  4. Commits results to DB.

Account: $1,000 virtual USD. P&L in USD = pnl_pct / 100 × 1000.
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
    """Idempotently open a virtual portfolio position at signal entry price.

    Returns True if a new position was created.
    """
    from src.database.crud import create_virtual_position, get_virtual_position

    try:
        if await get_virtual_position(db, signal.id) is not None:
            return False  # already open
        if signal.entry_price is None:
            return False

        await create_virtual_position(db, data={
            "signal_id": signal.id,
            "size_pct": Decimal("1.0"),
            "entry_price": signal.entry_price,
            "status": "open",
        })
        logger.info(
            f"[Simulator] Opened #{signal.id}: {signal.direction} "
            f"{signal.timeframe} @ {signal.entry_price}"
        )
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

def pnl_usd(pnl_pct: Optional[float]) -> float:
    """Convert pnl_percent (%) to USD assuming $1,000 account."""
    if pnl_pct is None:
        return 0.0
    return round(float(pnl_pct) / 100.0 * ACCOUNT_SIZE_FLOAT, 2)
