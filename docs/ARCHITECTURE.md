# Market Analyzer Pro v2 — Архитектура

## 1. Компонентная схема

```
┌─────────────────────────────────────────────────────────────────────┐
│                          DATA LAYER                                  │
│  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌──────────────────┐    │
│  │  Price     │ │  Macro    │ │  News     │ │  Order Flow      │    │
│  │ Collector  │ │ Collector │ │ Collector │ │  Collector       │    │
│  │ yfinance   │ │ FRED+ECB  │ │ FinBERT   │ │ Binance/Bybit    │    │
│  │ CCXT+Poly  │ │ BOJ+BOE   │ │ RSS+Reddit│ │ CVD,Funding,OI   │    │
│  └─────┬──────┘ └─────┬─────┘ └─────┬─────┘ └────────┬─────────┘    │
│        │              │              │                │              │
│  ┌─────┴──────┐ ┌─────┴──────┐ ┌────┴──────┐ ┌──────┴──────────┐   │
│  │ Earnings   │ │ Central    │ │ Social    │ │ On-Chain         │   │
│  │ Calendar   │ │ Bank Rates │ │ Sentiment │ │ Collector        │   │
│  │ Finnhub    │ │ 8 ЦБ       │ │ Reddit/ST │ │ Glassnode/CQ     │   │
│  └─────┬──────┘ └─────┬──────┘ └────┬──────┘ └──────┬──────────┘   │
└────────┼──────────────┼─────────────┼────────────────┼──────────────┘
         │              │             │                │
┌────────▼──────────────▼─────────────▼────────────────▼──────────────┐
│                PostgreSQL 16 + TimescaleDB                           │
│  ┌────────────┐ ┌─────────────┐ ┌──────────────┐ ┌──────────────┐  │
│  │ price_data │ │ macro_data  │ │ news_events  │ │order_flow_data│  │
│  │(hypertable)│ │             │ │              │ │ (hypertable)  │  │
│  ├────────────┤ ├─────────────┤ ├──────────────┤ ├──────────────┤  │
│  │instruments │ │central_bank │ │social_sentim.│ │ onchain_data │  │
│  │            │ │  _rates     │ │              │ │              │  │
│  ├────────────┤ ├─────────────┤ ├──────────────┤ ├──────────────┤  │
│  │  signals   │ │company_fund.│ │ regime_state │ │virtual_portf.│  │
│  ├────────────┤ ├─────────────┤ ├──────────────┤ ├──────────────┤  │
│  │signal_res. │ │backtest_runs│ │backtest_trad.│ │accuracy_stats│  │
│  └────────────┘ └─────────────┘ └──────────────┘ └──────────────┘  │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │             Redis 7 (Cache + Broker)                          │   │
│  │  TTL-кэш API │ Celery broker │ LLM кэш                       │   │
│  └──────────────────────────────────────────────────────────────┘   │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────────┐
│                       ANALYSIS LAYER                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ ┌────────────┐  │
│  │TA Engine │ │FA Engine │ │Sentiment │ │  Geo   │ │  Regime    │  │
│  │ v2 + OF  │ │v2: Forex │ │ FinBERT  │ │Engine  │ │ Detector   │  │
│  │Ichimoku  │ │  Stock   │ │+ Social  │ │ GDELT  │ │ADX+ATR+VIX│  │
│  │Divergence│ │  Crypto  │ │+ F&G     │ │        │ │            │  │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └───┬────┘ └─────┬──────┘  │
│       │            │            │            │            │          │
│  ┌────▼────────────▼────────────▼────────────▼────────────▼──────┐  │
│  │              SIGNAL ENGINE v2 (Regime-Aware)                   │  │
│  │  RegimeWeights → Composite → OF modifier → Earnings discount  │  │
│  │  → MTF Filter → Confidence → LLM Validation                   │  │
│  └──────────────────────────┬────────────────────────────────────┘  │
└─────────────────────────────┼──────────────────────────────────────┘
                              │
┌─────────────────────────────▼──────────────────────────────────────┐
│                        OUTPUT LAYER                                  │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────────────┐ │
│  │Risk Manager  │ │ Backtester   │ │    Notifications              │ │
│  │v2 (Portfolio │ │ Walk-Forward │ │  Telegram v2 + Webhook        │ │
│  │ + Lifecycle) │ │ Monte Carlo  │ │  (MT5, 3Commas, TradingView)  │ │
│  └──────────────┘ └──────────────┘ └──────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Dashboard v2 (Next.js 14 + TypeScript)                       │   │
│  │  TradingView Charts │ Portfolio view │ Backtest results        │   │
│  │  RegimeWidget │ PortfolioHeatBar │ DifferentialChart           │   │
│  └──────────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Monitoring: Prometheus + Grafana                             │   │
│  └──────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────┘
```

## 2. Docker Compose Services

```
┌──────────┐    ┌──────────┐    ┌──────────────┐
│  Nginx   │───▶│   App    │◀──▶│  PostgreSQL  │
│  :80/443 │    │  :8000   │    │  +TimescaleDB│
└──────────┘    └──────┬───┘    │  :5432       │
     │              │         └──────────────┘
     │    ┌─────────┴────────┐        ▲
     │    │                  │        │
     ▼    ▼                  ▼        │
┌────────┐ ┌──────────────┐ ┌────────┴───┐
│Next.js │ │Celery Worker │ │   Redis    │
│ :3000  │ │  (4 workers) │ │   :6379    │
└────────┘ └──────────────┘ └────────────┘
                │                  ▲
           ┌────┴────┐             │
           ▼         ▼             │
    ┌──────────┐ ┌──────────┐     │
    │  FinBERT │ │Celery Beat│────┘
    │  :8001   │ │ (scheduler│
    └──────────┘ └──────────┘

    ┌──────────────┐ ┌──────────────┐
    │  Prometheus   │ │   Grafana    │
    │  :9090        │ │   :3001      │
    └──────────────┘ └──────────────┘
```

## 3. Схема базы данных v2

### 3.1 Существующие таблицы (обновлённые)

```sql
-- instruments: добавлены поля для v2
CREATE TABLE instruments (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL UNIQUE,
    name VARCHAR(100),
    market VARCHAR(20) NOT NULL,          -- 'forex', 'stocks', 'crypto', 'commodities'
    exchange VARCHAR(30),
    pip_size NUMERIC(18,10),
    min_lot NUMERIC(10,4),
    sector VARCHAR(50),                   -- NEW: для акций (Technology, Healthcare...)
    base_currency VARCHAR(3),             -- NEW: для форекс (EUR в EURUSD)
    quote_currency VARCHAR(3),            -- NEW: для форекс (USD в EURUSD)
    central_bank VARCHAR(10),             -- NEW: BOJ, ECB и т.д.
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- price_data: TimescaleDB hypertable
CREATE TABLE price_data (
    id BIGSERIAL,
    instrument_id INT NOT NULL REFERENCES instruments(id),
    timeframe VARCHAR(10) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    open NUMERIC(18,8) NOT NULL,
    high NUMERIC(18,8) NOT NULL,
    low NUMERIC(18,8) NOT NULL,
    close NUMERIC(18,8) NOT NULL,
    volume NUMERIC(20,4),
    UNIQUE (instrument_id, timeframe, timestamp)
);
SELECT create_hypertable('price_data', 'timestamp');
CREATE INDEX idx_price_instrument_tf ON price_data (instrument_id, timeframe, timestamp DESC);

-- signals: расширенные для v2
CREATE TABLE signals (
    id SERIAL PRIMARY KEY,
    instrument_id INT NOT NULL REFERENCES instruments(id),
    timeframe VARCHAR(10) NOT NULL,
    direction VARCHAR(10) NOT NULL,       -- 'LONG', 'SHORT'
    signal_type VARCHAR(20) NOT NULL,     -- 'STRONG_BUY', 'BUY', 'SELL', 'STRONG_SELL'
    composite_score NUMERIC(6,2) NOT NULL,
    confidence NUMERIC(5,2),
    ta_score NUMERIC(6,2),
    fa_score NUMERIC(6,2),
    sentiment_score NUMERIC(6,2),
    geo_score NUMERIC(6,2),
    entry_price NUMERIC(18,8) NOT NULL,
    stop_loss NUMERIC(18,8) NOT NULL,
    take_profit_1 NUMERIC(18,8) NOT NULL,
    take_profit_2 NUMERIC(18,8),
    take_profit_3 NUMERIC(18,8),          -- NEW: TP3 для STRONG_TREND
    risk_reward_ratio NUMERIC(4,2),
    position_size_pct NUMERIC(5,2),
    horizon VARCHAR(20),
    status VARCHAR(20) DEFAULT 'CREATED', -- CREATED → ACTIVE → TRACKING → COMPLETED → ANALYZED
    regime VARCHAR(30),                   -- NEW: market regime при генерации
    of_score NUMERIC(6,2),               -- NEW: order flow score
    correlation_score NUMERIC(6,2),      -- NEW: correlation modifier
    earnings_days_ahead INT,             -- NEW: дней до earnings
    portfolio_heat NUMERIC(5,2),         -- NEW: portfolio heat при генерации
    created_at TIMESTAMPTZ DEFAULT NOW(),
    activated_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ
);
CREATE INDEX idx_signals_status ON signals (status);
CREATE INDEX idx_signals_instrument_tf ON signals (instrument_id, timeframe, created_at DESC);
CREATE INDEX idx_signals_regime ON signals (regime);

-- signal_results
CREATE TABLE signal_results (
    id SERIAL PRIMARY KEY,
    signal_id INT NOT NULL REFERENCES signals(id),
    exit_price NUMERIC(18,8),
    exit_reason VARCHAR(30),             -- 'TP1', 'TP2', 'TP3', 'SL', 'TRAILING', 'TIMEOUT', 'MANUAL'
    pnl_pips NUMERIC(10,2),
    pnl_pct NUMERIC(8,4),
    max_favorable_excursion NUMERIC(18,8),
    max_adverse_excursion NUMERIC(18,8),
    duration_hours NUMERIC(10,2),
    closed_at TIMESTAMPTZ DEFAULT NOW()
);

-- accuracy_stats
CREATE TABLE accuracy_stats (
    id SERIAL PRIMARY KEY,
    instrument_id INT REFERENCES instruments(id),
    timeframe VARCHAR(10),
    market VARCHAR(20),
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    total_signals INT,
    winning_signals INT,
    win_rate NUMERIC(5,2),
    profit_factor NUMERIC(6,3),
    sharpe_ratio NUMERIC(6,3),
    max_drawdown NUMERIC(5,2),
    expectancy NUMERIC(10,2),
    avg_pnl_pips NUMERIC(10,2),
    calculated_at TIMESTAMPTZ DEFAULT NOW()
);

-- macro_data
CREATE TABLE macro_data (
    id SERIAL PRIMARY KEY,
    indicator VARCHAR(50) NOT NULL,
    value NUMERIC(18,6) NOT NULL,
    date DATE NOT NULL,
    source VARCHAR(20),                  -- 'FRED', 'ECB', 'BOJ', 'BOE'
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (indicator, date)
);

-- news_events
CREATE TABLE news_events (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT,
    source VARCHAR(50),
    url TEXT,
    sentiment_score NUMERIC(4,2),
    relevance_instruments TEXT[],
    published_at TIMESTAMPTZ,
    collected_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 3.2 Новые таблицы v2

```sql
-- order_flow_data: TimescaleDB hypertable (крипто only)
CREATE TABLE order_flow_data (
    id BIGSERIAL,
    instrument_id INT NOT NULL REFERENCES instruments(id),
    timestamp TIMESTAMPTZ NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    cvd NUMERIC(18,4),                    -- Cumulative Volume Delta
    cvd_change_pct NUMERIC(8,4),
    open_interest NUMERIC(18,4),
    oi_change_pct NUMERIC(8,4),
    funding_rate NUMERIC(10,6),
    funding_rate_predicted NUMERIC(10,6),
    long_liquidations NUMERIC(18,4),
    short_liquidations NUMERIC(18,4),
    buy_volume NUMERIC(18,4),
    sell_volume NUMERIC(18,4),
    delta NUMERIC(18,4),                  -- buy_volume - sell_volume за бар
    UNIQUE (instrument_id, timeframe, timestamp)
);
SELECT create_hypertable('order_flow_data', 'timestamp');
CREATE INDEX idx_of_instrument_tf ON order_flow_data (instrument_id, timeframe, timestamp DESC);

-- regime_state: текущий режим для каждого инструмента
CREATE TABLE regime_state (
    id SERIAL PRIMARY KEY,
    instrument_id INT NOT NULL REFERENCES instruments(id),
    timeframe VARCHAR(10) NOT NULL,
    regime VARCHAR(30) NOT NULL,          -- STRONG_TREND_BULL, RANGING, HIGH_VOLATILITY и т.д.
    adx NUMERIC(6,2),
    atr_percentile NUMERIC(5,2),
    detected_at TIMESTAMPTZ DEFAULT NOW(),
    valid_until TIMESTAMPTZ,
    UNIQUE (instrument_id, timeframe)
);

-- central_bank_rates: ставки всех ЦБ
CREATE TABLE central_bank_rates (
    id SERIAL PRIMARY KEY,
    bank VARCHAR(10) NOT NULL,            -- FED, ECB, BOJ, BOE, RBA, BOC, SNB, RBNZ
    rate NUMERIC(6,4) NOT NULL,
    decision_date DATE,
    next_meeting_date DATE,
    bias VARCHAR(20),                     -- hawkish, dovish, neutral
    statement_sentiment NUMERIC(4,2),     -- FinBERT score
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_cb_bank ON central_bank_rates (bank, created_at DESC);

-- company_fundamentals: FA для акций
CREATE TABLE company_fundamentals (
    id SERIAL PRIMARY KEY,
    instrument_id INT NOT NULL REFERENCES instruments(id),
    period_date DATE NOT NULL,
    pe_ratio NUMERIC(10,2),
    forward_pe NUMERIC(10,2),
    eps_ttm NUMERIC(10,4),
    eps_next_q NUMERIC(10,4),
    revenue_growth_yoy NUMERIC(8,4),
    gross_margin NUMERIC(6,4),
    net_margin NUMERIC(6,4),
    debt_to_ebitda NUMERIC(8,2),
    free_cash_flow NUMERIC(18,2),
    return_on_equity NUMERIC(8,4),
    analyst_buy INT,
    analyst_hold INT,
    analyst_sell INT,
    eps_surprise_avg_4q NUMERIC(8,4),
    insider_buy_sell_ratio NUMERIC(6,2),
    next_earnings_date DATE,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (instrument_id, period_date)
);

-- onchain_data: on-chain метрики (крипто)
CREATE TABLE onchain_data (
    id SERIAL PRIMARY KEY,
    instrument_id INT NOT NULL REFERENCES instruments(id),
    date DATE NOT NULL,
    active_addresses BIGINT,
    tx_count BIGINT,
    nvt_ratio NUMERIC(10,4),
    mvrv_ratio NUMERIC(8,4),
    hashrate NUMERIC(18,4),
    exchange_netflow NUMERIC(18,4),
    coins_in_profit_pct NUMERIC(6,2),
    btc_dominance NUMERIC(6,2),
    fear_greed_index INT,
    etf_daily_flow_usd NUMERIC(18,2),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (instrument_id, date)
);

-- social_sentiment: sentiment из соцсетей
CREATE TABLE social_sentiment (
    id SERIAL PRIMARY KEY,
    instrument_id INT NOT NULL REFERENCES instruments(id),
    source VARCHAR(30) NOT NULL,          -- 'reddit', 'twitter', 'stocktwits'
    score NUMERIC(4,2),                   -- [-1, +1]
    post_count INT,
    bullish_ratio NUMERIC(5,2),
    collected_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_social_instrument ON social_sentiment (instrument_id, collected_at DESC);

-- virtual_portfolio: виртуальные позиции
CREATE TABLE virtual_portfolio (
    id SERIAL PRIMARY KEY,
    signal_id INT NOT NULL REFERENCES signals(id),
    status VARCHAR(20) DEFAULT 'open',    -- open, closed_tp1, closed_tp2, closed_tp3, closed_sl, closed_trailing
    entry_filled_at TIMESTAMPTZ,
    partial_close_at TIMESTAMPTZ,
    breakeven_moved_at TIMESTAMPTZ,
    trailing_stop NUMERIC(18,8),
    current_pnl_pct NUMERIC(8,4),
    closed_at TIMESTAMPTZ
);

-- backtest_runs: результаты walk-forward тестов
CREATE TABLE backtest_runs (
    id SERIAL PRIMARY KEY,
    instrument_id INT REFERENCES instruments(id),
    timeframe VARCHAR(10),
    start_date DATE,
    end_date DATE,
    in_sample_months INT,
    out_of_sample_months INT,
    ta_weight NUMERIC(4,2),
    fa_weight NUMERIC(4,2),
    sentiment_weight NUMERIC(4,2),
    geo_weight NUMERIC(4,2),
    buy_threshold NUMERIC(5,1),
    sell_threshold NUMERIC(5,1),
    is_sharpe NUMERIC(6,3),
    oos_sharpe NUMERIC(6,3),
    oos_win_rate NUMERIC(5,2),
    oos_profit_factor NUMERIC(6,3),
    oos_max_drawdown NUMERIC(5,2),
    oos_total_trades INT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    is_champion BOOLEAN DEFAULT FALSE
);

-- backtest_trades: трейды backtest
CREATE TABLE backtest_trades (
    id BIGSERIAL PRIMARY KEY,
    backtest_run_id INT NOT NULL REFERENCES backtest_runs(id),
    direction VARCHAR(5),
    entry_price NUMERIC(18,8),
    exit_price NUMERIC(18,8),
    sl NUMERIC(18,8),
    tp NUMERIC(18,8),
    pnl_pips NUMERIC(10,2),
    pnl_pct NUMERIC(8,4),
    entry_time TIMESTAMPTZ,
    exit_time TIMESTAMPTZ,
    exit_reason VARCHAR(20),              -- 'TP1', 'TP2', 'SL', 'TRAILING', 'TIMEOUT'
    regime VARCHAR(30),
    composite_score NUMERIC(6,2)
);
CREATE INDEX idx_bt_trades_run ON backtest_trades (backtest_run_id);
```

## 4. REST API v2

### 4.1 Endpoints

```
# ── Signals ─────────────────────────────────────────
GET  /api/v2/signals                        # список (filters: market, tf, status, regime, date_from/to)
GET  /api/v2/signals/{id}                   # детальный + компоненты
POST /api/v2/signals/{id}/feedback          # обратная связь оператора

# ── Instruments ─────────────────────────────────────
GET  /api/v2/instruments                    # список с текущим режимом
GET  /api/v2/instruments/{id}/analysis      # TA+FA+Sent+Geo+OF+Regime
GET  /api/v2/instruments/{id}/regime        # текущий режим + история
POST /api/v2/instruments/{id}/analyze       # принудительный пересчёт (async)

# ── Portfolio ───────────────────────────────────────
GET  /api/v2/portfolio                      # open positions, PnL
GET  /api/v2/portfolio/heat                 # текущий portfolio heat

# ── Accuracy ────────────────────────────────────────
GET  /api/v2/accuracy                       # метрики (агрегат + breakdowns)
GET  /api/v2/accuracy/{market}              # по рынку
GET  /api/v2/accuracy/{market}/{timeframe}  # по рынку и TF

# ── Backtesting ─────────────────────────────────────
GET  /api/v2/backtests                      # список runs
GET  /api/v2/backtests/{id}                 # метрики, equity curve
GET  /api/v2/backtests/{id}/trades          # трейды
POST /api/v2/backtest/run                   # запустить (Celery async)

# ── Macroeconomics ──────────────────────────────────
GET  /api/v2/macroeconomics                 # макро-данные всех ЦБ
GET  /api/v2/macroeconomics/rates           # ставки + дифференциалы
GET  /api/v2/macroeconomics/calendar        # календарь (48h ahead)

# ── Prices ──────────────────────────────────────────
GET  /api/v2/prices/{symbol}               # OHLCV (tf, from, to)
GET  /api/v2/prices/{symbol}/orderflow     # order flow (крипто)

# ── System ──────────────────────────────────────────
GET  /api/v2/health                        # статус коллекторов
GET  /api/v2/health/collectors             # детальный статус
GET  /metrics                              # Prometheus metrics
```

### 4.2 WebSocket

```
ws://host/ws/signals          # новые сигналы
ws://host/ws/prices/{symbol}  # цена (Binance WS passthrough)
ws://host/ws/portfolio        # обновления позиций
```

## 5. Конфигурация (src/config.py)

```python
from pydantic_settings import BaseSettings
from decimal import Decimal

class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://analyzer:changeme@localhost:5432/market_analyzer"
    REDIS_URL: str = "redis://localhost:6379/0"

    # API Keys (обязательные)
    ALPHA_VANTAGE_KEY: str = ""
    FINNHUB_KEY: str = ""
    FRED_KEY: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # API Keys (рекомендуемые)
    POLYGON_API_KEY: str = ""
    REDDIT_CLIENT_ID: str = ""
    REDDIT_CLIENT_SECRET: str = ""
    REDDIT_USER_AGENT: str = "MarketAnalyzerPro/2.0"
    GLASSNODE_API_KEY: str = ""
    NEWS_API_KEY: str = ""

    # API Keys (опциональные)
    ANTHROPIC_API_KEY: str = ""
    WEBHOOK_MT5_URL: str = ""
    WEBHOOK_3COMMAS_URL: str = ""
    WEBHOOK_TRADINGVIEW_URL: str = ""

    # FinBERT
    FINBERT_SERVICE_URL: str = "http://finbert:8001"

    # Collector intervals
    CRYPTO_PRICE_INTERVAL_SECONDS: int = 60
    FOREX_PRICE_INTERVAL_MINUTES: int = 15
    STOCK_PRICE_INTERVAL_MINUTES: int = 15
    ORDER_FLOW_INTERVAL_MINUTES: int = 5
    NEWS_COLLECT_INTERVAL_MINUTES: int = 10
    SOCIAL_COLLECT_INTERVAL_MINUTES: int = 30
    MACRO_COLLECT_INTERVAL_HOURS: int = 24
    ONCHAIN_COLLECT_INTERVAL_HOURS: int = 6
    REGIME_DETECT_INTERVAL_MINUTES: int = 60

    # Signal Engine
    DEFAULT_TA_WEIGHT: Decimal = Decimal("0.45")
    DEFAULT_FA_WEIGHT: Decimal = Decimal("0.25")
    DEFAULT_SENTIMENT_WEIGHT: Decimal = Decimal("0.20")
    DEFAULT_GEO_WEIGHT: Decimal = Decimal("0.10")
    STRONG_BUY_THRESHOLD: float = 60.0
    BUY_THRESHOLD: float = 30.0
    SELL_THRESHOLD: float = -30.0
    STRONG_SELL_THRESHOLD: float = -60.0
    MIN_CONFIDENCE: float = 40.0

    # Risk Management
    SL_ATR_MULTIPLIER: Decimal = Decimal("1.5")
    TP1_RR: Decimal = Decimal("2.0")
    TP2_RR: Decimal = Decimal("3.5")
    TP3_RR: Decimal = Decimal("4.0")
    MAX_RISK_PER_TRADE_PCT: Decimal = Decimal("2.0")
    CORRELATION_THRESHOLD: float = 0.7
    MAX_PORTFOLIO_HEAT: float = 6.0
    MAX_OPEN_FOREX: int = 3
    MAX_OPEN_CRYPTO: int = 2
    MAX_OPEN_STOCKS: int = 5

    # Backtesting
    BACKTEST_IN_SAMPLE_MONTHS: int = 18
    BACKTEST_OUT_OF_SAMPLE_MONTHS: int = 6
    MONTE_CARLO_SIMULATIONS: int = 10000
    MIN_OOS_TRADES: int = 30
    MIN_OOS_SHARPE: float = 0.8
    MIN_OOS_PROFIT_FACTOR: float = 1.3

    # Tracker
    TRACKER_CHECK_INTERVAL_MINUTES: int = 5
    SIGNAL_EXPIRY_HOURS: int = 168
    EARNINGS_SKIP_DAYS: int = 2
    EARNINGS_DISCOUNT_DAYS: int = 5

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    WORKERS: int = 4
    LOG_LEVEL: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
```

## 6. Celery Beat Schedule

```python
CELERY_BEAT_SCHEDULE = {
    # Цены
    "collect-crypto-prices":       {"task": "collectors.price.collect_crypto",    "schedule": "*/1 min"},
    "collect-forex-prices":        {"task": "collectors.price.collect_forex",     "schedule": "*/15 min"},
    "collect-stock-prices":        {"task": "collectors.price.collect_stocks",    "schedule": "*/15 min"},

    # Order Flow (крипто)
    "collect-order-flow":          {"task": "collectors.order_flow.collect",      "schedule": "*/5 min"},

    # Макро и ЦБ
    "collect-macro-fred":          {"task": "collectors.macro.collect_fred",      "schedule": "daily 00:30"},
    "collect-central-bank-rates":  {"task": "collectors.macro.collect_cb_rates",  "schedule": "daily 01:00"},

    # Новости и сентимент
    "collect-news":                {"task": "collectors.news.collect_all",        "schedule": "*/10 min"},
    "collect-social-sentiment":    {"task": "collectors.social.collect_all",      "schedule": "*/30 min"},

    # Фундаментал акций
    "collect-company-fundamentals": {"task": "collectors.fundamentals.collect",   "schedule": "weekly Mon 07:00"},

    # On-chain
    "collect-onchain":             {"task": "collectors.onchain.collect",         "schedule": "*/6 hours"},

    # Режим детекция
    "detect-regimes":              {"task": "analysis.regime.detect_all",         "schedule": "*/1 hour"},

    # Сигналы
    "generate-signals-m15":        {"task": "signals.generate", "schedule": "*/15 min",  "kwargs": {"timeframe": "M15"}},
    "generate-signals-h1":         {"task": "signals.generate", "schedule": "hourly",    "kwargs": {"timeframe": "H1"}},
    "generate-signals-h4":         {"task": "signals.generate", "schedule": "*/4 hours", "kwargs": {"timeframe": "H4"}},
    "generate-signals-d1":         {"task": "signals.generate", "schedule": "daily 06:00", "kwargs": {"timeframe": "D1"}},

    # Трекинг и backtest
    "track-signals":               {"task": "tracker.check_all",                 "schedule": "*/5 min"},
    "weekly-backtest":             {"task": "backtesting.run_weekly",             "schedule": "weekly Sun 02:00"},
}
```

## 7. Data Flow

### 7.1 Основной цикл анализа

```
1. Celery Beat триггерит "generate-signals-{timeframe}"
2. Для каждого активного инструмента (параллельно):
   a. Загрузить OHLCV из PostgreSQL
   b. RegimeDetector → определить режим → получить weights
   c. TAEngineV2(df, order_flow) → ta_score
   d. ForexFA / StockFA / CryptoFA → fa_score
   e. SentimentV2(news, social, F&G, FinBERT) → sentiment_score
   f. GeoEngineV2(GDELT) → geo_score
   g. SignalEngineV2.composite(weights, scores, OF, earnings) → composite
   h. Если composite > threshold И confidence > min_confidence:
      i.   PortfolioRiskManager.check_heat() → можно ли?
      ii.  RiskManagerV2.calculate_levels(regime, ATR, S/R) → SL, TP1-3
      iii. Сохранить Signal (status=CREATED) + virtual_portfolio
      iv.  Telegram v2 + Webhook (MT5/3Commas)
3. Обновить accuracy_stats
```

### 7.2 Цикл трекинга (5 мин)

```
1. Загрузить сигналы status IN ('ACTIVE', 'TRACKING')
2. Для каждого:
   a. Получить текущую цену
   b. TradeLifecycleManager:
      - check_breakeven() → SL → entry?
      - check_partial_close() → TP1 hit?
      - check_trailing_stop() → обновить trail?
   c. Exit conditions: SL/TP/trailing/timeout
   d. Обновить signal_results, virtual_portfolio
3. Пересчитать portfolio heat
```

### 7.3 Walk-Forward Backtesting (еженедельно)

```
1. Для каждого instrument + timeframe:
   a. Загрузить исторические данные
   b. run_walk_forward(IS=18m, OOS=6m, steps=5+)
   c. WeightValidator: сравнить с champion
   d. Если новые веса лучше на 10%+ → новый champion
   e. Monte Carlo → probability of ruin, CI drawdown
   f. Сохранить в backtest_runs / backtest_trades
```
