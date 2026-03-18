"""Celery application with beat schedule.

Start worker:   celery -A src.celery_app worker --loglevel=info --concurrency=4
Start beat:     celery -A src.celery_app beat   --loglevel=info
Flower monitor: celery -A src.celery_app flower
"""

from celery import Celery
from celery.schedules import crontab

from src.config import settings

app = Celery(
    "market_analyzer",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "src.scheduler.tasks",
    ],
)

# ── Serialisation & timezone ──────────────────────────────────────────────────
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Prevent tasks from running in the beat process itself
    task_always_eager=False,
    # Acknowledge task only after it has been executed (safer for idempotent tasks)
    task_acks_late=True,
    # Discard tasks that are not acked after a worker restart (prevent duplicate runs)
    task_reject_on_worker_lost=True,
    # Result TTL — keep results for 1 hour
    result_expires=3600,
    # Worker prefetch: fetch 1 task at a time to avoid starvation for long tasks
    worker_prefetch_multiplier=1,
)

# ── Beat schedule ─────────────────────────────────────────────────────────────
app.conf.beat_schedule = {
    # ── Price collection ──────────────────────────────────────────────────────
    "collect-crypto-prices": {
        "task": "src.scheduler.tasks.collect_crypto_prices",
        "schedule": settings.CRYPTO_PRICE_INTERVAL_SECONDS,  # every N seconds
        "options": {"queue": "collectors"},
    },
    "collect-forex-prices": {
        "task": "src.scheduler.tasks.collect_forex_prices",
        "schedule": settings.FOREX_PRICE_INTERVAL_MINUTES * 60,
        "options": {"queue": "collectors"},
    },
    "collect-stock-prices": {
        "task": "src.scheduler.tasks.collect_stock_prices",
        "schedule": settings.STOCK_PRICE_INTERVAL_MINUTES * 60,
        "options": {"queue": "collectors"},
    },

    # ── Order flow ────────────────────────────────────────────────────────────
    "collect-order-flow": {
        "task": "src.scheduler.tasks.collect_order_flow",
        "schedule": settings.ORDER_FLOW_INTERVAL_MINUTES * 60,
        "options": {"queue": "collectors"},
    },

    # ── News & social ─────────────────────────────────────────────────────────
    "collect-news": {
        "task": "src.scheduler.tasks.collect_news",
        "schedule": settings.NEWS_COLLECT_INTERVAL_MINUTES * 60,
        "options": {"queue": "collectors"},
    },
    "collect-social-sentiment": {
        "task": "src.scheduler.tasks.collect_social_sentiment",
        "schedule": settings.SOCIAL_COLLECT_INTERVAL_MINUTES * 60,
        "options": {"queue": "collectors"},
    },

    # ── Macro & central banks ─────────────────────────────────────────────────
    "collect-macro": {
        "task": "src.scheduler.tasks.collect_macro",
        "schedule": settings.MACRO_COLLECT_INTERVAL_HOURS * 3600,
        "options": {"queue": "collectors"},
    },
    "collect-central-bank-rates": {
        "task": "src.scheduler.tasks.collect_central_bank_rates",
        "schedule": crontab(hour="6", minute="0"),  # daily at 06:00 UTC
        "options": {"queue": "collectors"},
    },

    # ── On-chain ──────────────────────────────────────────────────────────────
    "collect-onchain": {
        "task": "src.scheduler.tasks.collect_onchain",
        "schedule": settings.ONCHAIN_COLLECT_INTERVAL_HOURS * 3600,
        "options": {"queue": "collectors"},
    },

    # ── Market context (DXY, VIX, TNX, funding rates) ────────────────────────
    "collect-market-context": {
        "task": "src.scheduler.tasks.collect_market_context",
        "schedule": 3600,  # every hour
        "options": {"queue": "collectors"},
    },

    # ── Economic calendar ─────────────────────────────────────────────────────
    "collect-economic-calendar": {
        "task": "src.scheduler.tasks.collect_economic_calendar",
        "schedule": 12 * 3600,  # every 12 hours
        "options": {"queue": "collectors"},
    },

    # ── RSS news (real-time feeds) ────────────────────────────────────────────
    "collect-rss-news": {
        "task": "src.scheduler.tasks.collect_rss_news",
        "schedule": 120,  # every 2 minutes
        "options": {"queue": "collectors"},
    },

    # ── COT reports (weekly) ──────────────────────────────────────────────────
    "collect-cot": {
        "task": "src.scheduler.tasks.collect_cot",
        "schedule": crontab(day_of_week="1", hour="8", minute="0"),  # Monday 08:00 UTC
        "options": {"queue": "collectors"},
    },

    # ── Regime detection ──────────────────────────────────────────────────────
    "detect-regimes": {
        "task": "src.scheduler.tasks.detect_all_regimes",
        "schedule": settings.REGIME_DETECT_INTERVAL_MINUTES * 60,
        "options": {"queue": "analysis"},
    },

    # ── Signal generation (per timeframe) ────────────────────────────────────
    "generate-signals-h1": {
        "task": "src.scheduler.tasks.generate_signals_h1",
        "schedule": 3600,  # every hour
        "options": {"queue": "signals"},
    },
    "generate-signals-h4": {
        "task": "src.scheduler.tasks.generate_signals_h4",
        "schedule": 4 * 3600,  # every 4 hours
        "options": {"queue": "signals"},
    },
    "generate-signals-daily": {
        "task": "src.scheduler.tasks.generate_signals_daily",
        "schedule": crontab(hour="0", minute="10"),  # daily at 00:10 UTC
        "options": {"queue": "signals"},
    },

    # ── Signal tracker ────────────────────────────────────────────────────────
    "track-signals": {
        "task": "src.scheduler.tasks.track_active_signals",
        "schedule": settings.TRACKER_CHECK_INTERVAL_MINUTES * 60,
        "options": {"queue": "signals"},
    },

    # ── Trade simulator tick ──────────────────────────────────────────────────
    "simulate-trades": {
        "task": "src.scheduler.tasks.simulate_trades_tick",
        "schedule": 60,  # every minute
        "options": {"queue": "signals"},
    },

    # ── Backtesting (weekly, Sunday 02:00 UTC) ────────────────────────────────
    "weekly-backtest": {
        "task": "src.scheduler.tasks.run_weekly_backtest",
        "schedule": crontab(day_of_week="0", hour="2", minute="0"),
        "options": {"queue": "analysis"},
    },

    # ── Accuracy recalculation ────────────────────────────────────────────────
    "recalculate-accuracy": {
        "task": "src.scheduler.tasks.recalculate_accuracy_stats",
        "schedule": crontab(hour="0", minute="30"),  # daily at 00:30 UTC
        "options": {"queue": "analysis"},
    },

    # ── System events cleanup ─────────────────────────────────────────────────
    "cleanup-system-events": {
        "task": "src.scheduler.tasks.cleanup_system_events",
        "schedule": crontab(hour="1", minute="0"),  # daily at 01:00 UTC
        "options": {"queue": "analysis"},
    },
}

# ── Queue routing ─────────────────────────────────────────────────────────────
app.conf.task_routes = {
    "src.scheduler.tasks.collect_*": {"queue": "collectors"},
    "src.scheduler.tasks.detect_*": {"queue": "analysis"},
    "src.scheduler.tasks.run_*": {"queue": "analysis"},
    "src.scheduler.tasks.recalculate_*": {"queue": "analysis"},
    "src.scheduler.tasks.cleanup_*": {"queue": "analysis"},
    "src.scheduler.tasks.generate_*": {"queue": "signals"},
    "src.scheduler.tasks.track_*": {"queue": "signals"},
    "src.scheduler.tasks.simulate_*": {"queue": "signals"},
}

if __name__ == "__main__":
    app.start()
