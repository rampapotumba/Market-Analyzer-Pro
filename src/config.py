"""Application configuration using Pydantic BaseSettings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/market_analyzer.db"

    # API Keys
    ALPHA_VANTAGE_KEY: str = ""
    FINNHUB_KEY: str = ""
    FRED_KEY: str = ""
    NEWS_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

    # Telegram
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # Collector intervals
    PRICE_COLLECT_INTERVAL_MINUTES: int = 5
    NEWS_COLLECT_INTERVAL_MINUTES: int = 15
    MACRO_COLLECT_INTERVAL_HOURS: int = 24

    # Signal engine weights (Swing / H4 default)
    TA_WEIGHT: float = 0.45
    FA_WEIGHT: float = 0.25
    SENTIMENT_WEIGHT: float = 0.20
    GEO_WEIGHT: float = 0.10

    # Risk management
    SL_ATR_MULTIPLIER: float = 1.5
    TP1_ATR_MULTIPLIER: float = 2.0
    TP2_ATR_MULTIPLIER: float = 3.5
    MAX_RISK_PER_TRADE_PCT: float = 2.0
    CORRELATION_THRESHOLD: float = 0.7

    # Signal thresholds
    STRONG_BUY_THRESHOLD: float = 60.0
    BUY_THRESHOLD: float = 30.0
    SELL_THRESHOLD: float = -30.0
    STRONG_SELL_THRESHOLD: float = -60.0

    # Tracker
    TRACKER_CHECK_INTERVAL_MINUTES: int = 5
    SIGNAL_EXPIRY_HOURS: int = 168

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"


settings = Settings()
