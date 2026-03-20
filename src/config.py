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
    # Calibrated to actual composite score range (≈ ±25 max in real markets)
    STRONG_BUY_THRESHOLD: float = 20.0
    BUY_THRESHOLD: float = 10.0
    SELL_THRESHOLD: float = -10.0
    STRONG_SELL_THRESHOLD: float = -20.0
    MIN_CONFIDENCE: float = 50.0  # signals below this confidence are discarded

    # ── Timeframe-specific composite minimums ─────────────
    # H1 already gated by market_type filter below.
    # Higher TFs require stronger conviction to avoid noise.
    TF_MIN_COMPOSITE: dict = {
        "H1":  10.0,
        "H4":  12.0,
        "D1":  15.0,
        "W1":  20.0,
        "MN1": 20.0,
    }
    # H1 signals only for fast-moving markets (crypto + forex)
    H1_ALLOWED_MARKETS: list = ["crypto", "forex"]

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

# ── v5: Signal filtering constants ───────────────────────────────────────────
MIN_COMPOSITE_SCORE: int = 15          # global threshold (raised from 10)
MIN_COMPOSITE_SCORE_CRYPTO: int = 20   # for market == "crypto"

BLOCKED_REGIMES: list = ["RANGING"]

INSTRUMENT_OVERRIDES: dict = {
    "BTC/USDT": {
        "sl_atr_multiplier": 3.5,
        "min_composite_score": 15,  # v6: lowered from 20 (TASK-V6-03)
        "allowed_regimes": [
            "STRONG_TREND_BULL", "STRONG_TREND_BEAR",
            "TREND_BULL", "TREND_BEAR",  # v6: expanded (TASK-V6-03)
        ],
    },
    "ETH/USDT": {
        "sl_atr_multiplier": 3.5,
        "min_composite_score": 15,  # v6: lowered from 20 (TASK-V6-03)
    },
    "GBPUSD=X": {
        # v6: TASK-V6-04 — override removed; global threshold 15 * 0.45 = 6.75 is sufficient
    },
    "USDCHF=X": {
        "min_composite_score": 18,
    },
}

# ── v6: Score component weights (TASK-V6-02) ─────────────────────────────────
# Used for proportional threshold scaling.
# In backtest (only TA available): available_weight = 0.45
# In live (all components):        available_weight = 0.45 + 0.25 + 0.20 + 0.10 = 1.0
SCORE_COMPONENT_WEIGHTS: dict = {
    "ta": 0.45,
    "fa": 0.25,
    "sentiment": 0.20,
    "geo": 0.10,
}
