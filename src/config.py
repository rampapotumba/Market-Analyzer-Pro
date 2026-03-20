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

    # ── ACLED geopolitical event data ────────────────────
    ACLED_API_KEY: str = ""
    ACLED_EMAIL: str = ""

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

# V6-CAL: TREND_BEAR и STRONG_TREND_BEAR добавлены —
# 219 trades при 11% WR, -$922 PnL в v6 backtest.
# V6-CAL2-07: TREND_BULL добавлен — 45 trades, 17.8% WR, -$45.83 в v6-cal-r1 backtest.
BLOCKED_REGIMES: list = ["RANGING", "TREND_BEAR", "STRONG_TREND_BEAR", "TREND_BULL"]

# CAL3-04: Per-market-type regime blocking.
# VOLATILE blocks forex (95 trades, -$33, WR 19%) but NOT crypto/stocks
# (GC=F: VOLATILE = trend continuation; crypto: VOLATILE = normal state).
# Priority: BLOCKED_REGIMES_BY_MARKET checked before BLOCKED_REGIMES global list.
BLOCKED_REGIMES_BY_MARKET: dict = {
    "forex": ["VOLATILE"],
    # "crypto": [],   # crypto and stocks do NOT block VOLATILE
    # "stocks": [],
}

# R5: Instruments with no demonstrated edge — blocked entirely.
# ETH/USDT: -$171 r4, -$245 P1, -$26 r3. 20% WR across 6 rounds.
# Use explicit block rather than raising min_composite_score —
# explicit is better than implicit (a score of 30+ still allows losers).
BLOCKED_INSTRUMENTS: set[str] = {"ETH/USDT"}

# R5: Backtest-only instrument whitelist. When non-empty, only these symbols
# are simulated. Empty list = all instruments (backward compatible).
# Based on r4 results: GC=F (+$150), EURUSD=X (+$52), USDCAD=X (+$35),
# BTC/USDT (+$51, low N), SPY (+$35, low N).
BACKTEST_INSTRUMENT_WHITELIST: list[str] = [
    "GC=F", "EURUSD=X", "USDCAD=X", "BTC/USDT", "SPY",
]

INSTRUMENT_OVERRIDES: dict = {
    # V6-CAL-05: Ужесточение — 102 trades, 9.8% WR, -$135 при relaxed settings.
    # min_score 25 (строже v5), только STRONG_TREND_BULL (bear заблокированы глобально).
    "BTC/USDT": {
        "sl_atr_multiplier": 3.5,
        "min_composite_score": 25,
        "allowed_regimes": ["STRONG_TREND_BULL"],
    },
    # ETH/USDT entry removed — instrument is in BLOCKED_INSTRUMENTS (R5).
    # Override would never be consulted because block fires before score check.
    # Do not restore this entry without first removing ETH/USDT from BLOCKED_INSTRUMENTS.
    # V6-CAL-06: Восстановить GBPUSD override — -$43 при global threshold.
    "GBPUSD=X": {
        "min_composite_score": 20,
    },
    "USDCHF=X": {
        "min_composite_score": 18,
    },
    # V6-CAL-06: Новые overrides для убыточных инструментов.
    # USDJPY: -$75 при global threshold, 12.9% WR.
    "USDJPY=X": {
        "min_composite_score": 22,
    },
    # NZDUSD: -$140, 16.7% WR, worst avg loss.
    "NZDUSD=X": {
        "min_composite_score": 22,
    },
    # V6-CAL2-08: relaxed from 30/STRONG_TREND_BULL — 0 trades was too restrictive.
    "SPY": {
        "min_composite_score": 22,
        "allowed_regimes": ["STRONG_TREND_BULL", "VOLATILE"],
    },
    # V6-CAL2-08: AUDUSD=X — 46 trades, 8.7% WR, -$97.87. Ужесточение порога.
    "AUDUSD=X": {
        "min_composite_score": 22,
    },
}

# ── v6: SHORT signal quality (TASK-V6-08, V6-CAL-04, V6-CAL2-03) ────────────
# V6-CAL-04: SHORT WR 12.04%, "sell" bucket -$1,202. Требуем 2x conviction и RSI < 30.
# V6-CAL2-03: reduced from 2.0 to 1.3 to allow SHORT trades in backtest.
# Effective threshold at 1.3: 15 * 0.65 * 1.3 = 12.675 (достижимо при ta_score >= 28.2).
SHORT_SCORE_MULTIPLIER: float = 1.3   # SHORT effective_threshold *= 1.3
SHORT_RSI_THRESHOLD: int = 30         # SHORT: RSI must be < 30 (deeply oversold)
# R5 Decision: SHORT is effectively disabled at H1 timeframe.
# SHORT_SCORE_MULTIPLIER=1.3 + SHORT_RSI_THRESHOLD=30 + BLOCKED_REGIMES
# result in <5% SHORT trades with negative PnL.
# If pivoting to D1 (see ARCHITECTURE_DECISION.md), re-evaluate SHORT viability.
# To fully disable: set SHORT_ENABLED = False
SHORT_ENABLED: bool = True  # Keep True for now; SHORT is gated by parameters above

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

# V6-CAL-01: Floor для available_weight — предотвращает чрезмерное снижение
# порога в backtest (только TA). Без floor: 15*0.45=6.75 (слишком низко).
# С floor 0.65: effective = 15*0.65 = 9.75.
AVAILABLE_WEIGHT_FLOOR: float = 0.65

# V6-CAL-09: Monday и Tuesday score penalty для forex.
# Mon: -$373, Tue: -$365 в v6 backtest. Требуем 1.5x conviction.
# CAL3-02: Monday убран из WEAK_WEEKDAYS — он теперь блокируется полностью в check_weekday().
# WEAK_WEEKDAYS используется только для score multiplier (1.5x), не для полной блокировки.
WEAK_WEEKDAY_SCORE_MULTIPLIER: float = 1.5
WEAK_WEEKDAYS: list = [1]  # 1=Tuesday (Monday блокируется полностью в check_weekday)
