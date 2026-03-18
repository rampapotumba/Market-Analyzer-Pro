"""Celery tasks — all background jobs for Market Analyzer Pro v2.

Each task is a thin async wrapper around the corresponding service/collector.
Heavy logic lives in the service modules; tasks handle:
  - Error isolation (exceptions are caught, logged, and swallowed so that
    one failing collector does not crash the beat schedule)
  - Retry policy (exponential backoff via Celery's built-in retry mechanism)
  - Async → sync bridge (asyncio.run / nest_asyncio not needed; we use a
    dedicated async runner helper)
"""

import asyncio
import logging

from celery import shared_task
from celery.utils.log import get_task_logger

from src.celery_app import app  # noqa: F401 — ensures app is initialised

logger = get_task_logger(__name__)


# ── Helper ────────────────────────────────────────────────────────────────────

def _run(coro):
    """Run an async coroutine from a sync Celery task."""
    return asyncio.run(coro)


# ── Price collectors ──────────────────────────────────────────────────────────

@shared_task(
    name="src.scheduler.tasks.collect_crypto_prices",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def collect_crypto_prices(self):
    """Collect OHLCV for all active crypto instruments via ccxt."""
    try:
        from src.collectors.price_collector import PriceCollector  # noqa: PLC0415

        _run(PriceCollector().collect_crypto())
        logger.info("collect_crypto_prices: done")
    except Exception as exc:
        logger.error("collect_crypto_prices failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


@shared_task(
    name="src.scheduler.tasks.collect_forex_prices",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def collect_forex_prices(self):
    """Collect OHLCV for all active forex pairs via yfinance."""
    try:
        from src.collectors.price_collector import PriceCollector  # noqa: PLC0415

        _run(PriceCollector().collect_forex())
        logger.info("collect_forex_prices: done")
    except Exception as exc:
        logger.error("collect_forex_prices failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


@shared_task(
    name="src.scheduler.tasks.collect_stock_prices",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def collect_stock_prices(self):
    """Collect OHLCV for all active stock instruments via yfinance."""
    try:
        from src.collectors.price_collector import PriceCollector  # noqa: PLC0415

        _run(PriceCollector().collect_stocks())
        logger.info("collect_stock_prices: done")
    except Exception as exc:
        logger.error("collect_stock_prices failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


# ── Order flow ────────────────────────────────────────────────────────────────

@shared_task(
    name="src.scheduler.tasks.collect_order_flow",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def collect_order_flow(self):
    """Collect CVD, funding rate, OI, liquidations from Binance via ccxt WebSocket."""
    try:
        from src.collectors.order_flow_collector import OrderFlowCollector  # noqa: PLC0415

        _run(OrderFlowCollector().collect())
        logger.info("collect_order_flow: done")
    except Exception as exc:
        logger.error("collect_order_flow failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


# ── News & social ─────────────────────────────────────────────────────────────

@shared_task(
    name="src.scheduler.tasks.collect_news",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    acks_late=True,
)
def collect_news(self):
    """Collect news from Finnhub and NewsAPI; score with FinBERT."""
    try:
        from src.collectors.news_collector import NewsCollector  # noqa: PLC0415

        _run(NewsCollector().collect())
        logger.info("collect_news: done")
    except Exception as exc:
        logger.error("collect_news failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


@shared_task(
    name="src.scheduler.tasks.collect_social_sentiment",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    acks_late=True,
)
def collect_social_sentiment(self):
    """Collect Reddit, Stocktwits, Fear & Greed, options PCR sentiment."""
    try:
        from src.collectors.social_collector import SocialCollector  # noqa: PLC0415

        _run(SocialCollector().collect())
        logger.info("collect_social_sentiment: done")
    except Exception as exc:
        logger.error("collect_social_sentiment failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


# ── Macro & central banks ─────────────────────────────────────────────────────

@shared_task(
    name="src.scheduler.tasks.collect_macro",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    acks_late=True,
)
def collect_macro(self):
    """Collect macro indicators: FRED (US), ECB Data Warehouse, BOJ, etc."""
    try:
        from src.collectors.macro_collector import MacroCollector  # noqa: PLC0415

        _run(MacroCollector().collect())
        logger.info("collect_macro: done")
    except Exception as exc:
        logger.error("collect_macro failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


@shared_task(
    name="src.scheduler.tasks.collect_central_bank_rates",
    bind=True,
    max_retries=2,
    default_retry_delay=600,
    acks_late=True,
)
def collect_central_bank_rates(self):
    """Collect current policy rates from FED, ECB, BOJ, BOE, RBA, BOC, SNB, RBNZ."""
    try:
        from src.collectors.central_bank_collector import CentralBankCollector  # noqa: PLC0415

        _run(CentralBankCollector().collect())
        logger.info("collect_central_bank_rates: done")
    except Exception as exc:
        logger.error("collect_central_bank_rates failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


# ── On-chain ──────────────────────────────────────────────────────────────────

@shared_task(
    name="src.scheduler.tasks.collect_onchain",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    acks_late=True,
)
def collect_onchain(self):
    """Collect on-chain metrics from Glassnode and CryptoQuant."""
    try:
        from src.collectors.onchain_collector import OnchainCollector  # noqa: PLC0415

        _run(OnchainCollector().collect())
        logger.info("collect_onchain: done")
    except Exception as exc:
        logger.error("collect_onchain failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


# ── Regime detection ──────────────────────────────────────────────────────────

@shared_task(
    name="src.scheduler.tasks.detect_all_regimes",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    acks_late=True,
)
def detect_all_regimes(self):
    """Run RegimeDetector for all active instruments and persist results."""
    try:
        from src.analysis.regime_detector import RegimeDetector  # noqa: PLC0415

        _run(RegimeDetector().detect_all())
        logger.info("detect_all_regimes: done")
    except Exception as exc:
        logger.error("detect_all_regimes failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


# ── Signal generation ─────────────────────────────────────────────────────────

@shared_task(
    name="src.scheduler.tasks.generate_all_signals",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    acks_late=True,
)
def generate_all_signals(self):
    """Run the full signal pipeline for every active instrument × timeframe."""
    try:
        from src.signals.signal_engine import SignalEngine  # noqa: PLC0415

        engine = SignalEngine()
        _run(engine.run_all())
        logger.info("generate_all_signals: done")
    except Exception as exc:
        logger.error("generate_all_signals failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


# ── Signal tracking ───────────────────────────────────────────────────────────

@shared_task(
    name="src.scheduler.tasks.track_active_signals",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def track_active_signals(self):
    """Check open signals against current price; update status and results."""
    try:
        from src.tracker.signal_tracker import SignalTracker  # noqa: PLC0415

        _run(SignalTracker().check_all())
        logger.info("track_active_signals: done")
    except Exception as exc:
        logger.error("track_active_signals failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


# ── Backtesting ───────────────────────────────────────────────────────────────

@shared_task(
    name="src.scheduler.tasks.run_weekly_backtest",
    bind=True,
    max_retries=1,
    default_retry_delay=600,
    acks_late=True,
    time_limit=7200,   # 2-hour hard limit
    soft_time_limit=6000,
)
def run_weekly_backtest(self):
    """Run walk-forward + Monte Carlo backtest; update optimal weights if validation passes."""
    try:
        from src.analysis.backtest_engine import BacktestEngine  # noqa: PLC0415

        _run(BacktestEngine().run_weekly())
        logger.info("run_weekly_backtest: done")
    except Exception as exc:
        logger.error("run_weekly_backtest failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


# ── Order Flow WebSocket (Phase 3) ────────────────────────────────────────────

@shared_task(
    name="src.scheduler.tasks.start_order_flow_ws",
    bind=True,
    max_retries=0,  # long-running; supervisor/Celery autoscaler handles restarts
    acks_late=False,
    time_limit=None,  # runs indefinitely
)
def start_order_flow_ws(self):
    """Start persistent WebSocket streams for all active crypto instruments.

    This task is intentionally long-running — it connects to Binance aggTrade
    streams and writes CVD snapshots to `order_flow_data` every 60 s.

    Celery should run exactly ONE instance of this task (use ``--queues ws``
    with a dedicated worker).  The beat schedule fires it once on startup;
    subsequent failures trigger a Celery retry via supervisor restart.
    """
    try:
        from sqlalchemy import select  # noqa: PLC0415

        from src.collectors.order_flow_collector import OrderFlowWebSocketCollector  # noqa: PLC0415
        from src.database.engine import async_session_factory  # noqa: PLC0415
        from src.database.models import Instrument  # noqa: PLC0415

        async def _start() -> None:
            async with async_session_factory() as session:
                res = await session.execute(
                    select(Instrument).where(
                        Instrument.is_active.is_(True),
                        Instrument.market_type == "crypto",
                    )
                )
                instruments = res.scalars().all()

            symbols = [
                instr.symbol.replace("/", "").upper() for instr in instruments
            ]
            if not symbols:
                logger.info("start_order_flow_ws: no active crypto instruments")
                return

            logger.info("start_order_flow_ws: streaming %d symbols: %s", len(symbols), symbols)
            collector = OrderFlowWebSocketCollector(symbols)
            await collector.run()

        _run(_start())
    except Exception as exc:
        logger.error("start_order_flow_ws failed: %s", exc, exc_info=True)
        raise


# ── Market context (DXY, VIX, TNX, funding rates) ────────────────────────────

@shared_task(
    name="src.scheduler.tasks.collect_market_context",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    acks_late=True,
)
def collect_market_context(self):
    """Collect DXY, VIX, TNX, crypto funding rates (realtime indicators)."""
    try:
        from src.scheduler.jobs import job_collect_market_context  # noqa: PLC0415

        _run(job_collect_market_context())
        logger.info("collect_market_context: done")
    except Exception as exc:
        logger.error("collect_market_context failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


# ── Economic calendar ─────────────────────────────────────────────────────────

@shared_task(
    name="src.scheduler.tasks.collect_economic_calendar",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    acks_late=True,
)
def collect_economic_calendar(self):
    """Collect upcoming economic events for next 14 days."""
    try:
        from src.scheduler.jobs import job_collect_economic_calendar  # noqa: PLC0415

        _run(job_collect_economic_calendar())
        logger.info("collect_economic_calendar: done")
    except Exception as exc:
        logger.error("collect_economic_calendar failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


# ── RSS news ──────────────────────────────────────────────────────────────────

@shared_task(
    name="src.scheduler.tasks.collect_rss_news",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def collect_rss_news(self):
    """Collect real-time RSS news feeds with smart signal routing."""
    try:
        from src.scheduler.jobs import job_collect_rss_news  # noqa: PLC0415

        _run(job_collect_rss_news())
        logger.info("collect_rss_news: done")
    except Exception as exc:
        logger.error("collect_rss_news failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


# ── COT reports ───────────────────────────────────────────────────────────────

@shared_task(
    name="src.scheduler.tasks.collect_cot",
    bind=True,
    max_retries=2,
    default_retry_delay=600,
    acks_late=True,
)
def collect_cot(self):
    """Collect CFTC Commitments of Traders reports (weekly)."""
    try:
        from src.scheduler.jobs import job_collect_cot  # noqa: PLC0415

        _run(job_collect_cot())
        logger.info("collect_cot: done")
    except Exception as exc:
        logger.error("collect_cot failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


# ── TF-specific signal generation ─────────────────────────────────────────────

@shared_task(
    name="src.scheduler.tasks.generate_signals_h1",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    acks_late=True,
)
def generate_signals_h1(self):
    """Generate signals for H1 timeframe (runs every hour)."""
    try:
        from src.scheduler.jobs import job_generate_signals_h1  # noqa: PLC0415

        _run(job_generate_signals_h1())
        logger.info("generate_signals_h1: done")
    except Exception as exc:
        logger.error("generate_signals_h1 failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


@shared_task(
    name="src.scheduler.tasks.generate_signals_h4",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    acks_late=True,
)
def generate_signals_h4(self):
    """Generate signals for H4 timeframe (runs every 4 hours)."""
    try:
        from src.scheduler.jobs import job_generate_signals_h4  # noqa: PLC0415

        _run(job_generate_signals_h4())
        logger.info("generate_signals_h4: done")
    except Exception as exc:
        logger.error("generate_signals_h4 failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


@shared_task(
    name="src.scheduler.tasks.generate_signals_daily",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    acks_late=True,
)
def generate_signals_daily(self):
    """Generate signals for D1/W1/MN1 timeframes (runs daily)."""
    try:
        from src.scheduler.jobs import job_generate_signals_daily  # noqa: PLC0415

        _run(job_generate_signals_daily())
        logger.info("generate_signals_daily: done")
    except Exception as exc:
        logger.error("generate_signals_daily failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


# ── Trade simulator tick ───────────────────────────────────────────────────────

@shared_task(
    name="src.scheduler.tasks.simulate_trades_tick",
    bind=True,
    max_retries=1,
    default_retry_delay=10,
    acks_late=True,
)
def simulate_trades_tick(self):
    """Simulator tick: check live prices against SL/TP for all open positions."""
    try:
        from src.scheduler.jobs import job_simulate_trades  # noqa: PLC0415

        _run(job_simulate_trades())
        logger.info("simulate_trades_tick: done")
    except Exception as exc:
        logger.error("simulate_trades_tick failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


# ── System events cleanup ─────────────────────────────────────────────────────

@shared_task(
    name="src.scheduler.tasks.cleanup_system_events",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    acks_late=True,
)
def cleanup_system_events(self):
    """Delete system_events older than 3 days."""
    try:
        from src.scheduler.jobs import job_cleanup_system_events  # noqa: PLC0415

        _run(job_cleanup_system_events())
        logger.info("cleanup_system_events: done")
    except Exception as exc:
        logger.error("cleanup_system_events failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


# ── Accuracy stats ────────────────────────────────────────────────────────────

@shared_task(
    name="src.scheduler.tasks.recalculate_accuracy_stats",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    acks_late=True,
)
def recalculate_accuracy_stats(self):
    """Recalculate all accuracy_stats rows from signal_results."""
    try:
        from src.tracker.accuracy import AccuracyCalculator  # noqa: PLC0415

        _run(AccuracyCalculator().recalculate_all())
        logger.info("recalculate_accuracy_stats: done")
    except Exception as exc:
        logger.error("recalculate_accuracy_stats failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)

