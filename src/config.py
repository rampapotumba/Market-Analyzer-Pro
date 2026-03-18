"""Application configuration using Pydantic BaseSettings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────
    DATABASE_URL: str = (
        "postgresql+asyncpg://analyzer:changeme@localhost:5432/market_analyzer"
    )
    POSTGRES_PASSWORD: str = "changeme"

    # ── Redis ─────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── API Keys (required, free) ─────────────────────────
    ALPHA_VANTAGE_KEY: str = ""
    FINNHUB_KEY: str = ""
    FRED_KEY: str = ""

    # ── API Keys (recommended, free) ─────────────────────
    POLYGON_API_KEY: str = ""
    REDDIT_CLIENT_ID: str = ""
    REDDIT_CLIENT_SECRET: str = ""
    REDDIT_USER_AGENT: str = "MarketAnalyzerPro/2.0"
    GLASSNODE_API_KEY: str = ""       # Deprecated — Glassnode went fully paid
    NEWS_API_KEY: str = ""

    # ── API Keys (optional) ───────────────────────────────
    ANTHROPIC_API_KEY: str = ""
    LLM_VALIDATION_ENABLED: bool = True
    LLM_SCORE_THRESHOLD: float = 10.0  # v3: was hardcoded 25 (never triggered)

    # ── Telegram ──────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # ── Webhooks ──────────────────────────────────────────
    WEBHOOK_MT5_URL: str = ""
    WEBHOOK_3COMMAS_URL: str = ""
    WEBHOOK_TRADINGVIEW_URL: str = ""

    # ── FinBERT microservice ──────────────────────────────
    FINBERT_SERVICE_URL: str = "http://finbert:8001"

    # ── Collector intervals ───────────────────────────────
    CRYPTO_PRICE_INTERVAL_SECONDS: int = 60
    FOREX_PRICE_INTERVAL_MINUTES: int = 15
    STOCK_PRICE_INTERVAL_MINUTES: int = 15
    ORDER_FLOW_INTERVAL_MINUTES: int = 5
    NEWS_COLLECT_INTERVAL_MINUTES: int = 10
    SOCIAL_COLLECT_INTERVAL_MINUTES: int = 30
    MACRO_COLLECT_INTERVAL_HOURS: int = 24
    ONCHAIN_COLLECT_INTERVAL_HOURS: int = 6
    REGIME_DETECT_INTERVAL_MINUTES: int = 60

    # Legacy alias kept for compatibility
    PRICE_COLLECT_INTERVAL_MINUTES: int = 15

    # ── Signal engine weights (overridden by RegimeDetector) ──
    DEFAULT_TA_WEIGHT: float = 0.45
    DEFAULT_FA_WEIGHT: float = 0.25
    DEFAULT_SENTIMENT_WEIGHT: float = 0.20
    DEFAULT_GEO_WEIGHT: float = 0.10

    # Legacy aliases
    TA_WEIGHT: float = 0.45
    FA_WEIGHT: float = 0.25
    SENTIMENT_WEIGHT: float = 0.20
    GEO_WEIGHT: float = 0.10

    # ── Signal thresholds ─────────────────────────────────
    # Calibrated to actual composite score range (≈ ±20 max in real markets)
    STRONG_BUY_THRESHOLD: float = 15.0
    BUY_THRESHOLD: float = 7.0
    SELL_THRESHOLD: float = -7.0
    STRONG_SELL_THRESHOLD: float = -15.0
    MIN_CONFIDENCE: float = 10.0

    # ── Account Size (v3) ────────────────────────────────
    SIGNAL_ACCOUNT_SIZE_USD: float = 10000.0    # v3: was hardcoded $10k in signal_engine
    VIRTUAL_ACCOUNT_SIZE_USD: float = 1000.0    # v3: was hardcoded $1k in simulator

    # ── Risk management ───────────────────────────────────
    SL_ATR_MULTIPLIER: float = 1.5
    TP1_RR: float = 2.0
    TP2_RR: float = 3.5
    TP3_RR: float = 4.0
    MAX_RISK_PER_TRADE_PCT: float = 2.0
    CORRELATION_THRESHOLD: float = 0.7
    MAX_PORTFOLIO_HEAT: float = 6.0
    MAX_OPEN_FOREX: int = 3
    MAX_OPEN_CRYPTO: int = 2
    MAX_OPEN_STOCKS: int = 5

    # Legacy aliases
    TP1_ATR_MULTIPLIER: float = 2.0
    TP2_ATR_MULTIPLIER: float = 3.5

    # ── Backtesting ───────────────────────────────────────
    BACKTEST_IN_SAMPLE_MONTHS: int = 18
    BACKTEST_OUT_OF_SAMPLE_MONTHS: int = 6
    MONTE_CARLO_SIMULATIONS: int = 10000
    MIN_OOS_TRADES: int = 30
    MIN_OOS_SHARPE: float = 0.8
    MIN_OOS_PROFIT_FACTOR: float = 1.3

    # ── Tracker ───────────────────────────────────────────
    TRACKER_CHECK_INTERVAL_MINUTES: int = 5
    SIGNAL_EXPIRY_HOURS: int = 168
    EARNINGS_SKIP_DAYS: int = 2
    EARNINGS_DISCOUNT_DAYS: int = 5

    # ── Server ────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    WORKERS: int = 4
    LOG_LEVEL: str = "INFO"

    # ── Grafana ───────────────────────────────────────────
    GRAFANA_PASSWORD: str = "admin"


settings = Settings()
