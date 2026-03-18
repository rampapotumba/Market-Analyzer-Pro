# Market Analyzer Pro v3 — Системные инструкции агента

## Роль

Ты — Senior финансовый инженер и архитектор системы Market Analyzer Pro v3. Ты калибруешь и исправляешь системные ошибки v2: мёртвый код (LLM, MTF), неинструментальный FA/Sentiment, контекст-фри TA сигналы, неадаптивный Risk Manager. **v3 — это калибровка, не расширение.** Код — production-ready: обработка ошибок, логирование, тесты ≥90% coverage для analysis/signals.

## Проект

Market Analyzer Pro v3 — фаза калибровки платформы прогнозирования Forex, US/EU Stocks, Crypto и Commodities. v2 построил инфраструктуру; v3 делает её работоспособной:

- **TA Engine** → контекстные RSI (ADX-aware), кластерные S/R, корректный ADX fallback, TF-адаптивные периоды
- **FA Engine** → маршрутизация по инструменту (ForexFAEngine / StockFAEngine / CryptoFAEngine)
- **Sentiment** → фильтрация новостей по инструменту (не глобальные 30 последних)
- **MTF Filter** → исправлен порог с ±30 (недостижимый) на ±BUY_THRESHOLD (7.0)
- **LLM validation** → порог снижен с 25 до 10 (LLM фактически вызывается)
- **Risk Manager** → режим-адаптивные SL/TP таблицы
- **Signal Cooldown** → bypass при развороте направления
- **Position Size** → конфигурируемый размер счёта

### Документация

| Файл | Содержание |
|---|---|
| `docs/SPEC_V3.md` | Полное ТЗ v3 (1039 строк) — **текущий первоисточник** |
| `docs/SPEC_V2.md` | Полное ТЗ v2 (1762 строки) — первоисточник архитектуры |
| `docs/SPEC.md` | ТЗ v1 (для справки по legacy) |
| `docs/ARCHITECTURE.md` | Архитектура, схема БД, API, Docker |
| `docs/TASKS.md` | Задачи v2 (все выполнены) + v3 по фазам |

### Принципы v3 (ОБЯЗАТЕЛЬНО СОБЛЮДАТЬ)

1. **Instrument-awareness**: каждый аналитический модуль знает для какого инструмента он работает и использует специфичные данные.
2. **Calibrated ranges**: все промежуточные скоры (TA, FA, Sentiment, Geo) реально используют диапазон [-100, +100], а не ±10-20.
3. **No silent dead code**: компоненты с недостижимыми порогами (LLM при 25 > max 24.75, MTF при ±30) — исправлены.
4. **Backward compatibility**: все изменения — in-place без изменения API, моделей БД (кроме колонки `regime`), форматов Telegram.

### Реальный диапазон composite score (КРИТИЧЕСКИ ВАЖНО)

```
max_composite ≈ 0.45×35 + 0.25×10 + 0.20×25 + 0.10×15 ≈ 24.75
```

Все пороги должны быть в этом диапазоне:
- BUY_THRESHOLD = 7.0 (не 30)
- SELL_THRESHOLD = -7.0 (не -30)
- LLM_SCORE_THRESHOLD = 10.0 (не 25)
- MTF direction threshold = ±BUY_THRESHOLD (не ±30)

## Стек технологий v2 (без изменений в v3)

### Backend Core
- **Python 3.11+** — основной язык
- **FastAPI** — async web framework + WebSocket
- **Uvicorn** — ASGI сервер (4 workers в production)
- **Pydantic v2** — валидация данных и Settings

### Данные и аналитика
- **pandas >= 2.0** — работа с данными
- **numpy** — вычисления
- **pandas-ta** — технические индикаторы (130+)
- **yfinance** — котировки акций и форекс (fallback)
- **ccxt** — крипто-биржи (Binance, Bybit)
- **httpx** — async HTTP-клиент (FRED, ECB, BOJ, BOE, Finnhub, NewsAPI, GDELT)
- **vectorbt** — backtesting framework

### База данных
- **PostgreSQL 16 + TimescaleDB** — основная БД с hypertables для time-series
- **SQLAlchemy 2.0** — ORM, async (asyncpg)
- **asyncpg** — async PostgreSQL драйвер
- **Alembic** — миграции БД
- **Redis 7** — кэш (TTL), Celery broker, LLM-кэш

### NLP / Sentiment
- **FinBERT** (ProsusAI/finbert) — финансовый NLP, отдельный микросервис
- **transformers** — Hugging Face библиотека
- **torch** — PyTorch (CPU inference)
- **PRAW** — Reddit API (social sentiment)

### Task Queue
- **Celery 5** — распределённые задачи
- **celery[redis]** — Redis как broker
- **celery-beat** — периодические задачи (замена APScheduler)

### Frontend
- **Next.js 14 + TypeScript** — React SSR framework
- **TradingView Lightweight Charts v4** — candlestick-графики
- **Zustand** — state management
- **Tailwind CSS** — стилизация

### Мониторинг
- **Prometheus** — метрики
- **Grafana** — визуализация
- **prometheus-fastapi-instrumentator** — авто-экспорт метрик FastAPI

### Инфраструктура
- **Docker + docker-compose** — 8 сервисов (app, postgres, redis, celery_worker, celery_beat, finbert, prometheus, grafana, nginx)
- **Nginx** — reverse proxy (/api → FastAPI, / → Next.js, /ws → WebSocket)
- **pytest + pytest-asyncio** — тесты
- **ruff** — линтер + форматтер
- **mypy** — статическая типизация

## Структура проекта v2 (без изменений каталогов в v3)

```
market-analyzer-pro/
├── CLAUDE.md                          # ← ТЫ ЗДЕСЬ
├── docs/
│   ├── SPEC_V3.md                     # Полное ТЗ v3 (текущий первоисточник)
│   ├── SPEC_V2.md                     # Полное ТЗ v2 (первоисточник архитектуры)
│   ├── SPEC.md                        # ТЗ v1 (legacy)
│   ├── ARCHITECTURE.md                # Архитектура, схема БД, API
│   └── TASKS.md                       # Задачи v2 + v3 по фазам
├── src/
│   ├── __init__.py
│   ├── main.py                        # FastAPI app entrypoint
│   ├── config.py                      # Settings (Pydantic BaseSettings)
│   ├── celery_app.py                  # Celery app + beat schedule
│   ├── database/
│   │   ├── __init__.py
│   │   ├── engine.py                  # SQLAlchemy async engine (asyncpg)
│   │   ├── models.py                  # ORM модели (все таблицы v2)
│   │   └── crud.py                    # CRUD операции + get_news_events_for_instrument (v3)
│   ├── collectors/                    # БЕЗ ИЗМЕНЕНИЙ в v3
│   │   ├── __init__.py
│   │   ├── base.py                    # Базовый класс коллектора (retry, rate-limit)
│   │   ├── price_collector.py         # Котировки (yfinance + ccxt + Polygon + WS)
│   │   ├── macro_collector.py         # FRED расширенный (17 индикаторов)
│   │   ├── central_bank_collector.py  # ECB, BOJ, BOE, RBA, BOC, SNB, RBNZ
│   │   ├── news_collector.py          # Новости (Finnhub, NewsAPI, RSS)
│   │   ├── calendar_collector.py      # Экономический календарь
│   │   ├── earnings_collector.py      # Earnings calendar (Finnhub + Yahoo)
│   │   ├── order_flow_collector.py    # CVD, OI, Funding, Liquidations (Binance)
│   │   ├── social_collector.py        # Reddit, Stocktwits, Fear&Greed
│   │   ├── onchain_collector.py       # CryptoQuant, CoinGecko (Glassnode deprecated)
│   │   └── fundamentals_collector.py  # Company metrics (Finnhub + yfinance)
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── ta_engine.py               # TA — v3 ИЗМЕНЕНИЯ: RSI context, S/R clusters, ADX fallback, TF periods
│   │   ├── ta_engine_v2.py            # TA v2 + OF, Ichimoku, divergence, market structure
│   │   ├── fa_engine.py               # FA v1 (legacy, commodities fallback)
│   │   ├── forex_fa_engine.py         # Differential FA для форекс (подключить в signal_engine v3)
│   │   ├── stock_fa_engine.py         # Company FA (подключить в signal_engine v3)
│   │   ├── crypto_fa_engine.py        # On-chain FA (уже подключен в v2)
│   │   ├── sentiment_engine.py        # Sentiment v1 (legacy TextBlob)
│   │   ├── sentiment_engine_v2.py     # FinBERT multi-source
│   │   ├── geo_engine.py              # Geo v1 (legacy)
│   │   ├── geo_engine_v2.py           # GDELT-based (без изменений в v3)
│   │   ├── regime_detector.py         # Market regime detection (без изменений в v3)
│   │   └── interest_rate_diff.py      # Interest rate differential calculator
│   ├── signals/
│   │   ├── __init__.py
│   │   ├── signal_engine.py           # Signal Engine v1 (legacy)
│   │   ├── signal_engine_v2.py        # v3 ИЗМЕНЕНИЯ: FA routing, sentiment filter, LLM threshold, cooldown bypass, regime→risk
│   │   ├── risk_manager.py            # Risk v1
│   │   ├── risk_manager_v2.py         # v3 ИЗМЕНЕНИЯ: calculate_levels_for_regime()
│   │   ├── portfolio_risk.py          # Portfolio-level risk (без изменений в v3)
│   │   ├── trade_lifecycle.py         # Trailing stop, breakeven, partial close (без изменений)
│   │   └── mtf_filter.py              # v3 ИЗМЕНЕНИЯ: порог ±30 → ±BUY_THRESHOLD
│   ├── backtesting/                   # БЕЗ ИЗМЕНЕНИЙ в v3
│   │   ├── __init__.py
│   │   ├── backtest_engine.py
│   │   ├── monte_carlo.py
│   │   └── weight_validator.py
│   ├── tracker/                       # БЕЗ ИЗМЕНЕНИЙ в v3
│   │   ├── __init__.py
│   │   ├── signal_tracker.py
│   │   └── accuracy.py
│   ├── notifications/                 # БЕЗ ИЗМЕНЕНИЙ в v3
│   │   ├── __init__.py
│   │   ├── telegram.py
│   │   └── webhook.py
│   ├── api/                           # БЕЗ ИЗМЕНЕНИЙ в v3
│   │   ├── __init__.py
│   │   ├── routes_v2.py
│   │   └── websocket.py
│   └── scheduler/                     # БЕЗ ИЗМЕНЕНИЙ в v3
│       ├── __init__.py
│       └── tasks.py
├── services/                          # БЕЗ ИЗМЕНЕНИЙ в v3
│   └── finbert/
├── frontend/                          # БЕЗ ИЗМЕНЕНИЙ в v3
├── infra/                             # БЕЗ ИЗМЕНЕНИЙ в v3
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── ... (v2 тесты)
│   ├── test_ta_engine_v3.py           # NEW: RSI context, S/R clusters, ADX fallback, TF periods
│   ├── test_fa_routing_v3.py          # NEW: FA маршрутизация по рынку
│   ├── test_sentiment_filtering_v3.py # NEW: инструментальная фильтрация новостей
│   ├── test_mtf_filter_v3.py          # NEW: исправленные пороги направления
│   ├── test_risk_manager_v3.py        # NEW: режим-адаптивные SL/TP
│   ├── test_signal_cooldown_v3.py     # NEW: direction reversal bypass
│   └── test_signal_regression_v3.py   # NEW: regression тесты
├── alembic/
│   ├── alembic.ini
│   └── versions/
│       └── xxxx_add_regime_to_signals.py  # NEW: колонка regime
├── requirements.txt
├── requirements-dev.txt
├── .env.example
├── docker-compose.yml
├── Dockerfile
├── Makefile
└── pyproject.toml
```

## Правила разработки

### Код
- Python 3.11+, type hints **ВЕЗДЕ** (строгий mypy)
- Pydantic v2 для всех DTO / Settings / API schemas
- async/await для **всех** I/O-bound операций
- Каждый модуль — самостоятельный, с чётким интерфейсом (Protocol или ABC)
- Логирование через `structlog` (structured JSON в production)
- **Все числовые значения для финансов — `Decimal`, не `float`**
- Даты — всегда UTC, `datetime` с timezone-aware
- Redis кэш для всех внешних API с TTL

### Обработка ошибок
- Каждый API-запрос: try/except + retry с exponential backoff (tenacity)
- Rate limiting: track usage, sleep перед лимитом, не после 429
- Graceful degradation: если один API недоступен → работаем с остальными, скор = 0 для недоступного компонента
- Circuit breaker pattern для нестабильных API (GDELT)
- Никогда не падаем молча — логируем **ВСЁ**

### База данных
- SQLAlchemy 2.0 стиль (`mapped_column`, `DeclarativeBase`)
- **asyncpg** для PostgreSQL
- Alembic для **любых** изменений схемы
- TimescaleDB hypertables: `price_data`, `order_flow_data`
- Индексы на всех FK и часто используемых фильтрах
- Составной уникальный индекс: `(instrument_id, timeframe, timestamp)` для price_data/order_flow

### Тесты
- pytest + pytest-asyncio для всех модулей
- Моки для API-запросов (pytest-httpx, responses)
- Тестовые fixtures с реалистичными OHLCV и macro данными
- **Минимум: 90% coverage для signals/ и analysis/**
- Интеграционные тесты с тестовой PostgreSQL (testcontainers)
- Тесты backtest engine с известным результатом (golden tests)
- **v3: regression тесты — snapshot expected ranges, не точные значения**

### Git
- Conventional commits: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `perf:`, `ci:`
- Ветки: `feature/*`, `fix/*`, `refactor/*`
- **НЕ коммитить:** `.env`, API ключи, `__pycache__`, `.db` файлы, `models/finbert/`

## Порядок работы

1. Прочитай `docs/TASKS.md` — найди следующую невыполненную задачу текущей фазы (v3: Phase 3.x)
2. Прочитай `docs/SPEC_V3.md` — найди раздел с детальным описанием задачи
3. Если задача затрагивает v2 архитектуру — сверься с `docs/SPEC_V2.md`
4. Прочитай связанные файлы проекта для понимания контекста
5. Пиши код инкрементально: один модуль → тесты → следующий модуль
6. После завершения — отметь задачу в `docs/TASKS.md` как `[x]`
7. При изменении архитектуры — обнови `docs/ARCHITECTURE.md`

## Ключевые формулы v3

### Composite Score (Regime-Aware) — без изменений формулы
```
weights = RegimeDetector.get_regime_weights(current_regime)
Composite = W_ta × TA + W_fa × FA + W_sent × Sentiment + W_geo × Geo
Composite += correlation_score × 0.05
if crypto: Composite *= (1 + OF_score × 0.15)
if stock & earnings_in_5d: Composite *= 0.70
Composite = MTF_Filter.apply(Composite, timeframe, higher_tf_signals)
```

**v3 отличие:** FA теперь приходит от ForexFAEngine / StockFAEngine / CryptoFAEngine (не от legacy FAEngine для всех). Sentiment фильтруется по инструменту.

### Пороги сигналов (откалиброванные в v2, сохранены в v3)
```
STRONG_BUY:   composite ≥ 15.0
BUY:          composite ≥  7.0
SELL:         composite ≤ -7.0
STRONG_SELL:  composite ≤ -15.0
LLM_THRESHOLD: |composite| ≥ 10.0   # v3: было 25, стало 10
MTF_DIRECTION: ±BUY_THRESHOLD (7.0)  # v3: было ±30 (нерабочее)
```

### Regime Weights — без изменений
```
STRONG_TREND:    TA=0.55, FA=0.25, Sent=0.15, Geo=0.05
WEAK_TREND:      TA=0.45, FA=0.30, Sent=0.20, Geo=0.05
RANGING:         TA=0.40, FA=0.30, Sent=0.25, Geo=0.05
HIGH_VOLATILITY: TA=0.35, FA=0.20, Sent=0.15, Geo=0.30
LOW_VOLATILITY:  TA=0.50, FA=0.25, Sent=0.20, Geo=0.05
```

### Risk Management v3 (Режим-Адаптивные SL/TP)

```
# SL multipliers (ATR × mult)
STRONG_TREND: 1.5, WEAK_TREND: 1.3, RANGING: 1.2
HIGH_VOLATILITY: 2.5, LOW_VOLATILITY: 1.0, default: 1.5

# TP1 Risk:Reward
STRONG_TREND: 2.0, WEAK_TREND: 2.0, RANGING: 1.5
HIGH_VOLATILITY: 2.0, LOW_VOLATILITY: 1.8, default: 2.0

# TP2 Risk:Reward
STRONG_TREND: 3.5, WEAK_TREND: 3.0, RANGING: 2.5
HIGH_VOLATILITY: 3.0, LOW_VOLATILITY: 2.5, default: 3.5

Position Size = (Risk% × SIGNAL_ACCOUNT_SIZE_USD) / SL_Distance  # v3: не хардкод $10k
Portfolio Heat = Σ risk_per_position ≤ 6%
Max Open: Forex=3, Crypto=2, Stocks=5
```

### Trade Lifecycle — без изменений
```
1. Signal → Entry
2. Price hits RR 1:1 → Move SL to breakeven
3. Price hits TP1 → Close 50%, trailing stop on rest
4. Trailing: 0.5×ATR (trending), 0.3×ATR (ranging)
5. Price hits TP2/TP3 or trailing stop → Close rest
```

### MTF коэффициенты — без изменений формулы
```
2 старших TF совпадают: ×1.2
1 старший TF совпадает: ×1.0
1 старший TF противоречит: ×0.7
2 старших TF противоречат: ×0.4
```
**v3 отличие:** пороги определения направления в MTF = BUY_THRESHOLD/SELL_THRESHOLD (7.0/-7.0), не 30/-30.

### Confidence v2 — без изменений
```
agreement_ratio = count(components agreeing) / total_components
regime_fit = 1.0 (match), 0.6 (contra), 0.8 (ranging)
Confidence = (|composite|/100) × agreement_ratio × regime_fit × 100
```

### TF-Адаптивные Периоды Индикаторов (v3 NEW)
```
M15: sma_fast=20, sma_slow=50, sma_long=200, ema_fast=12, ema_slow=26
H1:  sma_fast=20, sma_slow=50, sma_long=200, ema_fast=12, ema_slow=26
H4:  sma_fast=20, sma_slow=50, sma_long=100, ema_fast=12, ema_slow=26  # 200 = 400 дней — overkill
D1:  sma_fast=50, sma_slow=100, sma_long=200, ema_fast=21, ema_slow=55
```

### RSI Контекстный Сигнал (v3 NEW)
```
ADX ≥ 25 (тренд): RSI 40-55 = pullback buy, RSI <30 = strength×0.4 (trap filter)
ADX < 25 (флэт):  RSI <30 = classic bullish, RSI >70 = classic bearish
```

## Сводка v3 изменений по файлам

| Файл | Изменение |
|---|---|
| `src/signals/mtf_filter.py` | Порог ±30 → ±BUY_THRESHOLD |
| `src/signals/signal_engine_v2.py` | FA routing, sentiment filter, LLM threshold 10, cooldown bypass, regime→risk |
| `src/signals/risk_manager_v2.py` | `calculate_levels_for_regime()` + таблицы REGIME_SL/TP |
| `src/analysis/ta_engine.py` | RSI context, S/R clusters, ADX fallback, Volume→SMA20, TF periods |
| `src/database/crud.py` | `get_news_events_for_instrument()` + `_get_instrument_keywords()` |
| `src/config.py` | +VIRTUAL_ACCOUNT_SIZE_USD, +SIGNAL_ACCOUNT_SIZE_USD, +LLM_SCORE_THRESHOLD |
| `alembic/versions/` | +колонка `regime` в `signals` |

## API ключи (из .env)

### Обязательные (бесплатные)
```
ALPHA_VANTAGE_KEY=     # https://www.alphavantage.co/support/#api-key  (Forex M15/H1)
FINNHUB_KEY=           # https://finnhub.io/register  (Новости, earnings, FA)
FRED_KEY=              # https://fred.stlouisfed.org/docs/api/api_key.html  (Макро)
TELEGRAM_BOT_TOKEN=    # @BotFather в Telegram
TELEGRAM_CHAT_ID=      # ID чата для алертов
```

### Рекомендуемые (бесплатные)
```
POLYGON_API_KEY=       # https://polygon.io  (US Stocks)
REDDIT_CLIENT_ID=      # https://www.reddit.com/prefs/apps  (Social Sentiment)
REDDIT_CLIENT_SECRET=
NEWS_API_KEY=          # https://newsapi.org/register  (Новости дополнение)
```

### Опциональные
```
ANTHROPIC_API_KEY=     # Claude LLM validation (v3: теперь реально вызывается при composite > 10)
WEBHOOK_MT5_URL=       # MetaTrader 5 EA webhook
WEBHOOK_3COMMAS_URL=   # 3Commas Bot
```

### Без ключа (полностью бесплатные)
yfinance, ccxt (Binance public), CoinGecko, GDELT, Alternative.me, ECB, BOJ, BOE, RBA, BOC, SNB, RBNZ, Stocktwits, SEC EDGAR.

### Deprecated
```
GLASSNODE_API_KEY=     # Glassnode полностью платный ($999/mo) — использовать CryptoQuant/CoinGecko
```
