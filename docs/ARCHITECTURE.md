# Market Analyzer Pro — Архитектура

## 1. Общая схема

```
┌─────────────────────────────────────────────────────────────────────┐
│                        MARKET ANALYZER PRO                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │  yfinance    │  │  ccxt        │  │  FRED API    │   ...APIs    │
│  │  (stocks/fx) │  │  (crypto)    │  │  (macro)     │              │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘              │
│         └──────────────────┼─────────────────┘                      │
│                            ▼                                        │
│  ┌─────────────────────────────────────────────────────────┐       │
│  │          COLLECTORS (src/collectors/)                    │       │
│  │  price_collector · macro_collector · news_collector      │       │
│  │  calendar_collector                                     │       │
│  └────────────────────────┬────────────────────────────────┘       │
│                            ▼                                        │
│                    ┌──────────────┐                                  │
│                    │   DATABASE   │                                  │
│                    │   (SQLite/   │                                  │
│                    │  PostgreSQL) │                                  │
│                    └──────┬───────┘                                  │
│                            ▼                                        │
│  ┌──────────┐  ┌───────────┐  ┌────────────┐  ┌───────────┐       │
│  │TA Engine │  │ FA Engine │  │  Sentiment │  │Geo Engine │       │
│  │ RSI MACD │  │ Rates CPI │  │  NLP News  │  │ GDELT     │       │
│  │ BB ADX   │  │ EPS P/E   │  │  Fear&Greed│  │ Events    │       │
│  └────┬─────┘  └─────┬─────┘  └─────┬──────┘  └─────┬─────┘       │
│       └───────────────┼──────────────┼───────────────┘              │
│                       ▼                                             │
│  ┌─────────────────────────────────────────────────────────┐       │
│  │          SIGNAL ENGINE (src/signals/)                    │       │
│  │  signal_engine · risk_manager · mtf_filter              │       │
│  └────────────────────────┬────────────────────────────────┘       │
│                            ▼                                        │
│         ┌──────────────────┼──────────────────┐                     │
│         ▼                  ▼                  ▼                     │
│  ┌────────────┐  ┌─────────────────┐  ┌────────────────┐          │
│  │  SAVE TO   │  │   FASTAPI       │  │   TRACKER      │          │
│  │  DATABASE  │  │  REST + WS      │  │  (src/tracker/) │          │
│  │  signals   │  │  → Frontend     │  │  monitor +     │          │
│  │  table     │  │  Dashboard      │  │  accuracy      │          │
│  └────────────┘  └─────────────────┘  └────────────────┘          │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## 2. Схема базы данных

### 2.1. instruments

Справочник торговых инструментов.

```sql
CREATE TABLE instruments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      VARCHAR(20) NOT NULL UNIQUE,      -- 'EUR/USD', 'AAPL', 'BTC/USD'
    market      VARCHAR(20) NOT NULL,             -- 'forex', 'stocks', 'crypto', 'commodities'
    name        VARCHAR(100),                     -- 'Euro / US Dollar'
    pip_size    DECIMAL(18,10) NOT NULL,          -- 0.0001 для forex, 0.01 для акций
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_instruments_market ON instruments(market);
CREATE INDEX idx_instruments_active ON instruments(is_active);
```

### 2.2. price_data

Исторические OHLCV данные (hypertable в TimescaleDB).

```sql
CREATE TABLE price_data (
    id              BIGINT PRIMARY KEY AUTOINCREMENT,
    instrument_id   INTEGER NOT NULL REFERENCES instruments(id),
    timeframe       VARCHAR(5) NOT NULL,          -- 'M1','M5','M15','H1','H4','D1','W1'
    timestamp       TIMESTAMP NOT NULL,           -- UTC, время открытия свечи
    open            DECIMAL(18,8) NOT NULL,
    high            DECIMAL(18,8) NOT NULL,
    low             DECIMAL(18,8) NOT NULL,
    close           DECIMAL(18,8) NOT NULL,
    volume          DECIMAL(18,2) NOT NULL DEFAULT 0,

    UNIQUE(instrument_id, timeframe, timestamp)
);

CREATE INDEX idx_price_instrument_tf ON price_data(instrument_id, timeframe);
CREATE INDEX idx_price_timestamp ON price_data(timestamp);
```

### 2.3. signals

Все сгенерированные торговые сигналы.

```sql
CREATE TABLE signals (
    id                  BIGINT PRIMARY KEY AUTOINCREMENT,
    instrument_id       INTEGER NOT NULL REFERENCES instruments(id),
    timeframe           VARCHAR(5) NOT NULL,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Сигнал
    direction           VARCHAR(5) NOT NULL,      -- 'LONG', 'SHORT', 'HOLD'
    signal_strength     VARCHAR(20) NOT NULL,     -- 'STRONG_BUY','BUY','HOLD','SELL','STRONG_SELL'

    -- Параметры позиции
    entry_price         DECIMAL(18,8) NOT NULL,
    stop_loss           DECIMAL(18,8),
    take_profit_1       DECIMAL(18,8),
    take_profit_2       DECIMAL(18,8),
    risk_reward         DECIMAL(5,2),
    position_size_pct   DECIMAL(5,2),             -- % от депозита

    -- Скоры
    composite_score     DECIMAL(6,2) NOT NULL,    -- [-100, +100]
    ta_score            DECIMAL(6,2),
    fa_score            DECIMAL(6,2),
    sentiment_score     DECIMAL(6,2),
    geo_score           DECIMAL(6,2),
    confidence          DECIMAL(5,2),             -- 0-100%

    -- Мета
    horizon             VARCHAR(50),              -- '3-10 дней'
    reasoning           TEXT,                     -- JSON: ключевые факторы
    indicators_snapshot TEXT,                     -- JSON: все индикаторы на момент сигнала

    -- Статус
    status              VARCHAR(20) NOT NULL DEFAULT 'created',
                        -- 'created','active','tracking','completed','expired','cancelled'
    expires_at          TIMESTAMP                 -- когда сигнал истекает
);

CREATE INDEX idx_signals_instrument ON signals(instrument_id);
CREATE INDEX idx_signals_status ON signals(status);
CREATE INDEX idx_signals_created ON signals(created_at);
CREATE INDEX idx_signals_direction ON signals(direction);
```

### 2.4. signal_results

Фактический результат каждого сигнала (1:1 с signals).

```sql
CREATE TABLE signal_results (
    id                      BIGINT PRIMARY KEY AUTOINCREMENT,
    signal_id               BIGINT NOT NULL UNIQUE REFERENCES signals(id),

    -- Вход
    entry_filled_at         TIMESTAMP,            -- когда цена достигла entry
    entry_actual_price      DECIMAL(18,8),        -- фактическая цена входа

    -- Выход
    exit_at                 TIMESTAMP,
    exit_price              DECIMAL(18,8),
    exit_reason             VARCHAR(20),          -- 'sl_hit','tp1_hit','tp2_hit','expired','manual'

    -- P&L
    pnl_pips                DECIMAL(10,2),
    pnl_percent             DECIMAL(8,4),
    result                  VARCHAR(10),          -- 'win','loss','breakeven'

    -- Экскурсии (для анализа качества SL/TP)
    max_favorable_excursion DECIMAL(18,8),        -- MFE: макс. прибыль в процессе
    max_adverse_excursion   DECIMAL(18,8),        -- MAE: макс. убыток в процессе
    price_at_expiry         DECIMAL(18,8),        -- цена в момент истечения горизонта

    -- Время
    duration_minutes        INTEGER
);

CREATE INDEX idx_results_signal ON signal_results(signal_id);
CREATE INDEX idx_results_result ON signal_results(result);
```

### 2.5. accuracy_stats

Агрегированная статистика точности (обновляется периодически).

```sql
CREATE TABLE accuracy_stats (
    id              BIGINT PRIMARY KEY AUTOINCREMENT,
    period          VARCHAR(20) NOT NULL,         -- 'daily','weekly','monthly','all_time'
    period_start    DATE NOT NULL,
    instrument_id   INTEGER REFERENCES instruments(id), -- NULL = все
    market          VARCHAR(20),                  -- NULL = все рынки
    timeframe       VARCHAR(5),                   -- NULL = все TF

    -- Счётчики
    total_signals   INTEGER NOT NULL DEFAULT 0,
    wins            INTEGER NOT NULL DEFAULT 0,
    losses          INTEGER NOT NULL DEFAULT 0,
    breakevens      INTEGER NOT NULL DEFAULT 0,

    -- Метрики
    win_rate        DECIMAL(5,2),                 -- %
    profit_factor   DECIMAL(8,2),                 -- Gross Profit / Gross Loss
    avg_win_pips    DECIMAL(10,2),
    avg_loss_pips   DECIMAL(10,2),
    sharpe_ratio    DECIMAL(6,3),
    max_drawdown_pct DECIMAL(6,2),
    expectancy      DECIMAL(10,2),                -- мат. ожидание в пипсах

    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(period, period_start, instrument_id, market, timeframe)
);
```

### 2.6. macro_data

Макроэкономические показатели.

```sql
CREATE TABLE macro_data (
    id              BIGINT PRIMARY KEY AUTOINCREMENT,
    indicator_name  VARCHAR(100) NOT NULL,        -- 'CPI','NFP','FedRate','PMI'...
    country         VARCHAR(3) NOT NULL,          -- 'US','EU','GB','JP'...
    value           DECIMAL(18,6) NOT NULL,
    previous_value  DECIMAL(18,6),
    forecast_value  DECIMAL(18,6),                -- consensus прогноз
    release_date    DATE NOT NULL,
    source          VARCHAR(50) NOT NULL,         -- 'FRED','ECB','Eurostat'

    UNIQUE(indicator_name, country, release_date)
);

CREATE INDEX idx_macro_indicator ON macro_data(indicator_name, country);
CREATE INDEX idx_macro_date ON macro_data(release_date);
```

### 2.7. news_events

Новости и события.

```sql
CREATE TABLE news_events (
    id                  BIGINT PRIMARY KEY AUTOINCREMENT,
    headline            TEXT NOT NULL,
    summary             TEXT,
    source              VARCHAR(100) NOT NULL,
    url                 VARCHAR(500),
    published_at        TIMESTAMP NOT NULL,
    sentiment_score     DECIMAL(4,3),             -- [-1.000, +1.000]
    importance          VARCHAR(10) NOT NULL,      -- 'low','medium','high','critical'
    related_instruments TEXT,                      -- JSON: ['EUR/USD','AAPL']
    category            VARCHAR(50)               -- 'macro','earnings','geopolitics','regulation'
);

CREATE INDEX idx_news_published ON news_events(published_at);
CREATE INDEX idx_news_importance ON news_events(importance);
```

### 2.8. ER-диаграмма

```
instruments ─────────┐
  │                   │
  │ 1:N               │ 1:N
  ▼                   ▼
price_data          signals ──── signal_results
                      │    1:1
                      │
                      ▼ (via instrument_id + timeframe + period)
                accuracy_stats

macro_data       (standalone, linked by country/date)
news_events      (standalone, linked via related_instruments JSON)
```

---

## 3. REST API Endpoints

### 3.1. Инструменты

```
GET    /api/instruments                    — список всех инструментов
GET    /api/instruments/{id}               — детали инструмента
POST   /api/instruments                    — добавить инструмент
PATCH  /api/instruments/{id}               — обновить (вкл/выкл)
```

### 3.2. Данные

```
GET    /api/prices/{symbol}                — котировки (query: timeframe, from, to)
GET    /api/macro/{country}                — макроданные по стране
GET    /api/news                           — новости (query: importance, from, to)
GET    /api/calendar                       — экономический календарь
```

### 3.3. Анализ и сигналы

```
POST   /api/analyze/{symbol}               — запустить полный анализ
GET    /api/analyze/{symbol}/ta            — только тех. анализ
GET    /api/analyze/{symbol}/fa            — только фундаментальный

GET    /api/signals                        — история сигналов (query: status, market, from, to)
GET    /api/signals/{id}                   — детали сигнала
GET    /api/signals/active                 — активные сигналы
PATCH  /api/signals/{id}/cancel            — отменить сигнал
```

### 3.4. Статистика точности

```
GET    /api/accuracy                       — общая статистика
GET    /api/accuracy/by-instrument         — по инструментам
GET    /api/accuracy/by-timeframe          — по таймфреймам
GET    /api/accuracy/by-market             — по рынкам
GET    /api/accuracy/equity-curve          — кривая equity
```

### 3.5. Система

```
GET    /api/health                         — healthcheck
GET    /api/config                         — текущие настройки весов
PATCH  /api/config                         — обновить веса/параметры
POST   /api/collect/run                    — принудительный запуск сбора данных
```

### 3.6. WebSocket

```
WS     /ws/prices/{symbol}                — real-time котировки
WS     /ws/signals                        — real-time новые сигналы
```

---

## 4. Конфигурация (Pydantic Settings)

```python
class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/market_analyzer.db"

    # API Keys
    ALPHA_VANTAGE_KEY: str = ""
    FINNHUB_KEY: str = ""
    FRED_KEY: str = ""
    NEWS_API_KEY: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # Collector Settings
    PRICE_COLLECT_INTERVAL_MINUTES: int = 5
    NEWS_COLLECT_INTERVAL_MINUTES: int = 15
    MACRO_COLLECT_INTERVAL_HOURS: int = 24

    # Signal Engine Weights (default: Swing H4)
    TA_WEIGHT: float = 0.45
    FA_WEIGHT: float = 0.25
    SENTIMENT_WEIGHT: float = 0.20
    GEO_WEIGHT: float = 0.10

    # Risk Management
    SL_ATR_MULTIPLIER: float = 1.5
    TP1_ATR_MULTIPLIER: float = 2.0
    TP2_ATR_MULTIPLIER: float = 3.5
    MAX_RISK_PER_TRADE_PCT: float = 2.0
    CORRELATION_THRESHOLD: float = 0.7

    # Signal Thresholds
    STRONG_BUY_THRESHOLD: float = 60.0
    BUY_THRESHOLD: float = 30.0
    SELL_THRESHOLD: float = -30.0
    STRONG_SELL_THRESHOLD: float = -60.0

    # Tracker
    TRACKER_CHECK_INTERVAL_MINUTES: int = 5
    SIGNAL_EXPIRY_HOURS: int = 168  # 7 дней для swing

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
```

---

## 5. Потоки данных

### 5.1. Основной цикл

```
1. [Scheduler] → запускает коллекторы по расписанию
2. [Collectors] → собирают данные из API → записывают в БД (price_data, macro_data, news_events)
3. [Scheduler / User] → запускает анализ для инструмента
4. [TA Engine] → читает price_data → рассчитывает индикаторы → возвращает TA_Score
5. [FA Engine] → читает macro_data → оценивает факторы → возвращает FA_Score
6. [Sentiment] → читает news_events → NLP scoring → возвращает Sentiment_Score
7. [Geo Engine] → GDELT данные → Geo_Score
8. [Signal Engine] → принимает все скоры → композитный скор → Risk Management → СИГНАЛ
9. [CRUD] → записывает сигнал в signals таблицу (status='created')
10. [API/WS] → отправляет сигнал на фронтенд + Telegram
```

### 5.2. Цикл трекинга

```
1. [Scheduler] → каждые N минут запускает Signal Tracker
2. [Tracker] → читает signals WHERE status IN ('created','active','tracking')
3. Для каждого сигнала:
   a. Получает текущую цену инструмента
   b. Проверяет: достигнута ли точка входа? → status='active'→'tracking'
   c. Проверяет: достигнут ли SL? → фиксируем loss
   d. Проверяет: достигнут ли TP1/TP2? → фиксируем win
   e. Проверяет: истёк ли горизонт? → status='expired'
   f. Записывает MFE/MAE
4. При закрытии → записывает в signal_results
5. [Accuracy] → обновляет accuracy_stats
```

---

## 6. Структура FastAPI приложения

```python
# src/main.py
app = FastAPI(title="Market Analyzer Pro", version="2.0")

# Роутеры
app.include_router(instruments_router, prefix="/api")
app.include_router(prices_router, prefix="/api")
app.include_router(analysis_router, prefix="/api")
app.include_router(signals_router, prefix="/api")
app.include_router(accuracy_router, prefix="/api")
app.include_router(system_router, prefix="/api")

# WebSocket
app.include_router(ws_router, prefix="/ws")

# Static (frontend)
app.mount("/", StaticFiles(directory="frontend", html=True))

# Startup
@app.on_event("startup")
async def startup():
    await init_db()
    start_scheduler()

# Shutdown
@app.on_event("shutdown")
async def shutdown():
    stop_scheduler()
    await close_db()
```
