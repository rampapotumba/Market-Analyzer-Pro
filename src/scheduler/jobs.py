"""APScheduler background jobs."""

import asyncio
import datetime
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.config import settings
from src.utils.event_logger import EventType, log_event

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


COLLECT_TIMEFRAMES = ["H1", "H4", "D1", "W1", "MN1"]


async def job_collect_prices() -> None:
    """Collect latest prices for all instruments across multiple timeframes."""
    logger.info("[Scheduler] Running price collection job")
    errors: list[str] = []
    try:
        from src.collectors.price_collector import CcxtCollector, YFinanceCollector
        from src.database.crud import get_all_instruments
        from src.database.engine import async_session_factory

        async with async_session_factory() as db:
            instruments = await get_all_instruments(db)

        yf_collector = YFinanceCollector()
        ccxt_collector = CcxtCollector()
        total = 0

        for instrument in instruments:
            for tf in COLLECT_TIMEFRAMES:
                if instrument.market in ("stocks", "forex"):
                    result = await yf_collector.collect_latest(instrument.symbol, tf)
                    if result.success:
                        total += result.records_count
                    else:
                        err = f"{instrument.symbol}/{tf}: {result.error}"
                        logger.warning(f"[Scheduler] YF failed for {err}")
                        errors.append(err)
                elif instrument.market == "crypto":
                    result = await ccxt_collector.collect_latest(instrument.symbol, tf)
                    if result.success:
                        total += result.records_count
                    else:
                        err = f"{instrument.symbol}/{tf}: {result.error}"
                        logger.warning(f"[Scheduler] CCXT failed for {err}")
                        errors.append(err)
                await asyncio.sleep(0.5)  # small pause between requests

        logger.info(f"[Scheduler] Price collection completed: {total} records upserted")
        level = "WARNING" if errors else "INFO"
        await log_event(
            EventType.PRICE_COLLECT,
            f"Collected {total} records across {len(instruments)} instruments × {len(COLLECT_TIMEFRAMES)} TFs",
            level=level,
            source="job_collect_prices",
            details={"total_records": total, "instruments": len(instruments), "errors": errors[:10]},
        )
        # Signal generation is handled by TF-specific scheduled jobs (D. implementation)
    except Exception as exc:
        logger.error(f"[Scheduler] Price collection job error: {exc}")
        await log_event(
            EventType.PRICE_COLLECT, str(exc), level="ERROR", source="job_collect_prices"
        )


async def job_collect_rss_news() -> None:
    """Collect real-time news from RSS feeds (runs every 2 min).

    C. Smart news routing: only triggers signal generation for instruments
    explicitly mentioned in new articles. General/central bank news triggers
    signal generation for all instruments.
    """
    try:
        from src.collectors.rss_collector import RSSNewsCollector

        collector = RSSNewsCollector()
        result = await collector.collect()
        if result.records_count > 0:
            logger.info(f"[Scheduler] RSS news: {result.records_count} new articles")

            affected_symbols: list[str] = result.metadata.get("affected_symbols", [])
            has_general_news: bool = result.metadata.get("has_general_news", False)

            await log_event(
                EventType.NEWS_COLLECT,
                f"RSS: {result.records_count} new articles, affected={affected_symbols}, general={has_general_news}",
                source="job_collect_rss_news",
                details={"count": result.records_count, "affected_symbols": affected_symbols, "general": has_general_news},
            )

            if has_general_news or not affected_symbols:
                # General/macro news → only targeted H1 (not full sweep, to avoid Telegram spam)
                logger.info("[Scheduler] General news detected — skipping bulk signal regen (H1 job handles it)")
            else:
                # Targeted: only instruments explicitly mentioned in news
                await job_generate_signals_for_symbols(affected_symbols, ["H1"])
    except Exception as exc:
        logger.error(f"[Scheduler] RSS news job error: {exc}")
        await log_event(EventType.NEWS_COLLECT, str(exc), level="ERROR", source="job_collect_rss_news")


async def job_collect_news() -> None:
    """Collect latest news."""
    logger.info("[Scheduler] Running news collection job")
    try:
        from src.collectors.news_collector import FinnhubNewsCollector

        collector = FinnhubNewsCollector()
        result = await collector.collect()
        logger.info(f"[Scheduler] News collected: {result.records_count} items")
        await log_event(
            EventType.NEWS_COLLECT,
            f"Finnhub: {result.records_count} items",
            source="job_collect_news",
            details={"count": result.records_count},
        )
        # Recalculate signals after news update (affects Sentiment Score)
        await job_generate_signals()
    except Exception as exc:
        logger.error(f"[Scheduler] News collection job error: {exc}")
        await log_event(EventType.NEWS_COLLECT, str(exc), level="ERROR", source="job_collect_news")


async def job_collect_macro() -> None:
    """Collect macro data (runs once per day)."""
    logger.info("[Scheduler] Running macro data collection job")
    try:
        from src.collectors.macro_collector import FREDCollector

        collector = FREDCollector()
        result = await collector.collect()
        logger.info(f"[Scheduler] Macro data collected: {result.records_count} items")
        await log_event(
            EventType.MACRO_COLLECT,
            f"FRED: {result.records_count} indicators",
            source="job_collect_macro",
            details={"count": result.records_count},
        )
        # Macro affects FA score — regenerate longer-timeframe signals
        await job_generate_signals_daily()
    except Exception as exc:
        logger.error(f"[Scheduler] Macro collection job error: {exc}")
        await log_event(EventType.MACRO_COLLECT, str(exc), level="ERROR", source="job_collect_macro")


async def job_generate_signals_for_timeframes(timeframes: list[str]) -> None:
    """Generate signals for all active instruments across specified timeframes."""
    logger.info(f"[Scheduler] Generating signals for timeframes: {timeframes}")
    try:
        from src.database.crud import get_all_instruments
        from src.database.engine import async_session_factory
        from src.signals.signal_engine import SignalEngine

        engine = SignalEngine()
        generated = 0
        skipped = 0

        async with async_session_factory() as db:
            instruments = await get_all_instruments(db)
            for instrument in instruments:
                for tf in timeframes:
                    try:
                        signal = await engine.generate_signal(instrument, tf, db)
                        if signal:
                            generated += 1
                            logger.info(
                                f"[Scheduler] Signal: {instrument.symbol}/{tf} → "
                                f"{signal.direction} ({signal.signal_strength}, "
                                f"score={float(signal.composite_score):.1f})"
                            )
                        else:
                            skipped += 1
                    except Exception as exc:
                        logger.warning(
                            f"[Scheduler] Signal failed for {instrument.symbol}/{tf}: {exc}"
                        )
                        skipped += 1

        logger.info(
            f"[Scheduler] Signal generation done ({timeframes}): "
            f"{generated} generated, {skipped} skipped"
        )
        await log_event(
            EventType.SIGNAL_BATCH,
            f"Batch {timeframes}: {generated} generated, {skipped} skipped",
            source="job_generate_signals_for_timeframes",
            details={"timeframes": timeframes, "generated": generated, "skipped": skipped},
        )
    except Exception as exc:
        logger.error(f"[Scheduler] Signal generation job error: {exc}")
        await log_event(
            EventType.SIGNAL_BATCH, str(exc), level="ERROR",
            source="job_generate_signals_for_timeframes",
            details={"timeframes": timeframes},
        )


async def job_generate_signals_for_symbols(
    symbols: list[str], timeframes: list[str]
) -> None:
    """C. Smart news routing: generate signals only for specified symbols."""
    if not symbols:
        return
    logger.info(f"[Scheduler] Targeted signal generation for {symbols} / {timeframes}")
    try:
        from src.database.crud import get_instrument_by_symbol
        from src.database.engine import async_session_factory
        from src.signals.signal_engine import SignalEngine

        engine = SignalEngine()
        generated = 0

        async with async_session_factory() as db:
            for symbol in symbols:
                instrument = await get_instrument_by_symbol(db, symbol)
                if not instrument:
                    continue
                for tf in timeframes:
                    try:
                        signal = await engine.generate_signal(instrument, tf, db)
                        if signal:
                            generated += 1
                            logger.info(
                                f"[Scheduler] Targeted signal: {symbol}/{tf} → "
                                f"{signal.direction} ({signal.signal_strength})"
                            )
                    except Exception as exc:
                        logger.warning(f"[Scheduler] Targeted signal failed {symbol}/{tf}: {exc}")

        logger.info(f"[Scheduler] Targeted signal generation done: {generated} generated")
    except Exception as exc:
        logger.error(f"[Scheduler] Targeted signal generation error: {exc}")


# Convenience aliases for scheduler jobs
async def job_generate_signals() -> None:
    """Generate signals for all instruments across all timeframes (legacy alias)."""
    await job_generate_signals_for_timeframes(["H1", "H4", "D1", "W1", "MN1"])


async def job_generate_signals_h1() -> None:
    """D. Hourly signal generation for H1 timeframe."""
    await job_generate_signals_for_timeframes(["H1"])


async def job_generate_signals_h4() -> None:
    """D. 4-hourly signal generation for H4 timeframe."""
    await job_generate_signals_for_timeframes(["H4"])


async def job_generate_signals_daily() -> None:
    """D. Daily signal generation for D1, W1, MN1 timeframes."""
    await job_generate_signals_for_timeframes(["D1", "W1", "MN1"])


async def job_collect_market_context() -> None:
    """Collect DXY, VIX, TNX, funding rates."""
    try:
        from src.collectors.market_context_collector import MarketContextCollector
        collector = MarketContextCollector()
        result = await collector.collect()
        logger.info(f"[Scheduler] Market context collected: {result.records_count} records")
        await log_event(
            EventType.MARKET_CONTEXT,
            f"DXY/VIX/TNX/funding: {result.records_count} records",
            source="job_collect_market_context",
            details={"count": result.records_count},
        )
    except Exception as exc:
        logger.error(f"[Scheduler] Market context job error: {exc}")
        await log_event(EventType.MARKET_CONTEXT, str(exc), level="ERROR", source="job_collect_market_context")


async def job_collect_economic_calendar() -> None:
    """Collect upcoming economic calendar events from FMP (runs twice daily)."""
    try:
        from src.collectors.fmp_calendar_collector import FMPCalendarCollector
        collector = FMPCalendarCollector()
        result = await collector.collect()
        if result.records_count > 0:
            logger.info(f"[Scheduler] Economic calendar: {result.records_count} events saved")
        await log_event(
            EventType.CALENDAR_COLLECT,
            f"FMP calendar: {result.records_count} events (next 14 days)",
            source="job_collect_economic_calendar",
            details={"count": result.records_count},
        )
    except Exception as exc:
        logger.error(f"[Scheduler] Economic calendar job error: {exc}")
        await log_event(EventType.CALENDAR_COLLECT, str(exc), level="ERROR", source="job_collect_economic_calendar")


async def job_collect_cot() -> None:
    """Collect COT reports from CFTC."""
    try:
        from src.collectors.cot_collector import COTCollector
        collector = COTCollector()
        result = await collector.collect()
        logger.info(f"[Scheduler] COT data collected: {result.records_count} records")
        await log_event(
            EventType.COT_COLLECT,
            f"CFTC COT: {result.records_count} records",
            source="job_collect_cot",
            details={"count": result.records_count},
        )
    except Exception as exc:
        logger.error(f"[Scheduler] COT collection job error: {exc}")
        await log_event(EventType.COT_COLLECT, str(exc), level="ERROR", source="job_collect_cot")


async def job_check_signals() -> None:
    """Check and update active signal statuses."""
    try:
        from src.database.engine import async_session_factory
        from src.tracker.signal_tracker import SignalTracker

        from src.database.crud import get_active_signals
        tracker = SignalTracker()
        async with async_session_factory() as db:
            active = await get_active_signals(db)
            active_count = len(active)
            if active_count:
                await tracker.check_active_signals(db)
                await db.commit()
        if active_count:
            await log_event(
                EventType.SIGNAL_CHECK,
                f"Checked {active_count} active signal(s)",
                source="job_check_signals",
                details={"active_count": active_count},
            )
    except Exception as exc:
        logger.error(f"[Scheduler] Signal check job error: {exc}")
        await log_event(EventType.SIGNAL_CHECK, str(exc), level="ERROR", source="job_check_signals")


async def job_simulate_trades() -> None:
    """Simulator tick: fetch live prices and check SL/TP for all open positions."""
    try:
        from src.tracker.trade_simulator import run_simulator_tick
        await run_simulator_tick()
    except Exception as exc:
        logger.error(f"[Scheduler] Simulator tick error: {exc}")


async def job_detect_regimes() -> None:
    """Detect market regime for all active instruments (runs every 4 hours)."""
    try:
        from src.analysis.regime_detector import RegimeDetector

        detector = RegimeDetector()
        await detector.detect_all()
        logger.info("[Scheduler] Regime detection completed")
        await log_event(
            EventType.SIGNAL_BATCH,
            "Regime detection completed for all instruments",
            source="job_detect_regimes",
        )
    except Exception as exc:
        logger.error(f"[Scheduler] Regime detection job error: {exc}")
        await log_event(EventType.SIGNAL_BATCH, str(exc), level="ERROR", source="job_detect_regimes")


async def job_cleanup_system_events() -> None:
    """Delete system_events older than 3 days and news older than 2 days (runs daily)."""
    try:
        from src.database.crud import cleanup_news_events, cleanup_system_events
        from src.database.engine import async_session_factory

        async with async_session_factory() as db:
            deleted_events = await cleanup_system_events(db, days=3)
            deleted_news = await cleanup_news_events(db, days=2)
            await db.commit()

        logger.info(
            f"[Scheduler] Cleanup: {deleted_events} system events, {deleted_news} news articles deleted"
        )
        if deleted_events or deleted_news:
            await log_event(
                EventType.CLEANUP,
                f"Deleted {deleted_events} system events (>3d), {deleted_news} news articles (>2d)",
                source="job_cleanup_system_events",
                details={"deleted_events": deleted_events, "deleted_news": deleted_news},
            )
    except Exception as exc:
        logger.error(f"[Scheduler] Cleanup job error: {exc}")


def start_scheduler() -> None:
    """Start the APScheduler with all configured jobs."""
    now = datetime.datetime.now(datetime.timezone.utc)

    # Price collection — starts IMMEDIATELY on startup, then every N minutes
    scheduler.add_job(
        job_collect_prices,
        trigger=IntervalTrigger(minutes=settings.PRICE_COLLECT_INTERVAL_MINUTES),
        id="collect_prices",
        name="Collect Price Data",
        replace_existing=True,
        misfire_grace_time=60,
        next_run_time=now,  # run immediately on startup
    )

    # Finnhub news — disabled, replaced by RSS collector (every 2 min)

    # Macro data — 60 seconds after startup, then every 24 hours
    scheduler.add_job(
        job_collect_macro,
        trigger=IntervalTrigger(hours=settings.MACRO_COLLECT_INTERVAL_HOURS),
        id="collect_macro",
        name="Collect Macro Data",
        replace_existing=True,
        misfire_grace_time=3600,
        next_run_time=now + datetime.timedelta(seconds=60),
    )

    # Signal tracking every 5 minutes
    scheduler.add_job(
        job_check_signals,
        trigger=IntervalTrigger(minutes=settings.TRACKER_CHECK_INTERVAL_MINUTES),
        id="check_signals",
        name="Check Active Signals",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # Market context — every hour, starts 90s after scheduler start
    scheduler.add_job(
        job_collect_market_context,
        trigger=IntervalTrigger(hours=1),
        id="collect_market_context",
        name="Collect Market Context",
        replace_existing=True,
        misfire_grace_time=300,
        next_run_time=now + datetime.timedelta(seconds=90),
    )

    # COT — every 7 days (weekly data), starts 120s after scheduler start
    scheduler.add_job(
        job_collect_cot,
        trigger=IntervalTrigger(days=7),
        id="collect_cot",
        name="Collect COT Reports",
        replace_existing=True,
        misfire_grace_time=3600,
        next_run_time=now + datetime.timedelta(seconds=120),
    )

    # Economic calendar — twice daily (00:01 UTC and 12:01 UTC), first run 30s after boot
    scheduler.add_job(
        job_collect_economic_calendar,
        trigger=IntervalTrigger(hours=12),
        id="collect_economic_calendar",
        name="Collect Economic Calendar",
        replace_existing=True,
        misfire_grace_time=3600,
        next_run_time=now + datetime.timedelta(seconds=30),
    )

    # RSS news — every 2 minutes, real-time feed with smart signal routing
    scheduler.add_job(
        job_collect_rss_news,
        trigger=IntervalTrigger(minutes=2),
        id="collect_rss_news",
        name="Collect RSS News",
        replace_existing=True,
        misfire_grace_time=60,
        next_run_time=now + datetime.timedelta(seconds=15),
    )

    # D. Timeframe-specific signal generation
    # H1: every hour (starts 3min after boot, after first price collection completes)
    scheduler.add_job(
        job_generate_signals_h1,
        trigger=IntervalTrigger(hours=1),
        id="signals_h1",
        name="Generate H1 Signals",
        replace_existing=True,
        misfire_grace_time=300,
        next_run_time=now + datetime.timedelta(minutes=3),
    )

    # H4: every 4 hours (starts 4min after boot)
    scheduler.add_job(
        job_generate_signals_h4,
        trigger=IntervalTrigger(hours=4),
        id="signals_h4",
        name="Generate H4 Signals",
        replace_existing=True,
        misfire_grace_time=600,
        next_run_time=now + datetime.timedelta(minutes=4),
    )

    # D1/W1/MN1: once per day (starts 5min after boot)
    scheduler.add_job(
        job_generate_signals_daily,
        trigger=IntervalTrigger(hours=24),
        id="signals_daily",
        name="Generate D1/W1/MN1 Signals",
        replace_existing=True,
        misfire_grace_time=3600,
        next_run_time=now + datetime.timedelta(minutes=5),
    )

    # Trade simulator — every 1 minute (live SL/TP monitoring, starts 2 min after boot)
    scheduler.add_job(
        job_simulate_trades,
        trigger=IntervalTrigger(minutes=1),
        id="simulate_trades",
        name="Trade Simulator Tick",
        replace_existing=True,
        misfire_grace_time=30,
        next_run_time=now + datetime.timedelta(minutes=2),
    )

    # Regime detection — every 4 hours, starts 6min after boot (after first price collection)
    scheduler.add_job(
        job_detect_regimes,
        trigger=IntervalTrigger(hours=4),
        id="detect_regimes",
        name="Detect Market Regimes",
        replace_existing=True,
        misfire_grace_time=600,
        next_run_time=now + datetime.timedelta(minutes=6),
    )

    # System events cleanup — daily, deletes records older than 3 days
    scheduler.add_job(
        job_cleanup_system_events,
        trigger=IntervalTrigger(hours=24),
        id="cleanup_system_events",
        name="Cleanup System Events",
        replace_existing=True,
        misfire_grace_time=3600,
        next_run_time=now + datetime.timedelta(hours=1),
    )

    scheduler.start()
    logger.info(
        "[Scheduler] APScheduler started:\n"
        f"  • Prices:  every {settings.PRICE_COLLECT_INTERVAL_MINUTES}min\n"
        f"  • RSS:     every 2min (real-time feeds, smart signal routing)\n"
        f"  • Macro:   every {settings.MACRO_COLLECT_INTERVAL_HOURS}h → all-TF signals\n"
        f"  • Signals: H1 hourly | H4 every 4h | D1/W1/MN1 daily (with cooldown+ATR filter)\n"
        f"  • Tracker: every {settings.TRACKER_CHECK_INTERVAL_MINUTES}min\n"
        f"  • Market Context: every 1h (DXY, VIX, TNX, funding rates)\n"
        f"  • COT:     every 7 days (CFTC COT reports)\n"
        f"  • Calendar: every 12h (FMP economic events, next 14 days)\n"
        f"  • Regime:  every 4h (ADX/ATR/VIX regime classification)\n"
        f"  • Simulator: every 1min (live SL/TP monitoring)"
    )


