# Market Analyzer Pro — Задачи (v2 + v3)

> Отмечай задачи как выполненные [x] по мере продвижения.
> Порядок — по фазам. Внутри фазы — сверху вниз. Не перескакивай между фазами.
> **v2 (Phase 2.x) — завершено. v3 (Phase 3.x) — текущая работа.**

---

## Phase 2.1 — Фундамент (6-8 недель)

### 2.1.1 Инфраструктура

- [x] Мигрировать БД на PostgreSQL 16 + TimescaleDB
  - [x] Обновить `DATABASE_URL` в config.py (asyncpg)
  - [x] Создать Alembic миграцию: все v2 таблицы
  - [x] Настроить hypertables: `price_data`, `order_flow_data`
  - [x] Обновить `engine.py` для asyncpg
- [x] Интегрировать Redis как кэш
  - [x] Создать `src/cache.py` — Redis TTL-кэш обёртка
  - [x] Заменить in-memory dict кэши на Redis
  - [x] Кэширование API-ответов (FRED, Finnhub, GDELT)
- [x] Перейти на Celery
  - [x] Создать `src/celery_app.py` с beat schedule
  - [x] Перенести задачи из APScheduler в Celery tasks
  - [x] Celery worker + beat в docker-compose
  - [x] Мониторинг задач (flower или custom)

### 2.1.2 Центральные банки и процентные дифференциалы

- [x] Реализовать `central_bank_collector.py`
  - [x] ECB Data Warehouse API
  - [x] BOJ API
  - [x] BOE API
  - [x] RBA, BOC, SNB, RBNZ
- [x] Реализовать `interest_rate_diff.py`
  - [x] `InterestRateDifferential` класс
  - [x] `calculate_differential(symbol)` для всех пар
  - [x] `calculate_differential_trend(symbol, lookback=6m)`
- [x] Alembic миграция: `central_bank_rates`

### 2.1.3 Фундаментальный анализ v2

- [x] `forex_fa_engine.py`
  - [x] `ForexFAEngine._score_currency()` — скор для одной валюты
  - [x] `ForexFAEngine.analyze()` — дифференциальный FA
  - [x] `_differential_trend()` — тренд дифференциала
- [x] `stock_fa_engine.py`
  - [x] `get_company_metrics()` — P/E, EPS, margins
  - [x] `get_analyst_consensus()` — Finnhub recommendations
  - [x] `get_earnings_surprise()` — средний surprise 4Q
  - [x] `get_insider_activity()` — SEC Form 4
  - [x] `calculate_stock_fa_score()` — weighted (25/25/20/15/15)
- [x] `crypto_fa_engine.py`
  - [x] On-chain (NVT, addresses): 25%
  - [x] Market structure (dominance, ETF): 20%
  - [x] Funding ecosystem (revenue, TVL): 15%
  - [x] Cycle analysis (halving, MVRV): 25%
  - [x] Macro correlation: 15%
- [x] `fundamentals_collector.py` — Finnhub + yfinance
- [x] Alembic миграция: `company_fundamentals`

### 2.1.4 Regime Detector

- [x] `regime_detector.py`
  - [x] `detect_regime()` — ADX + MA + ATR percentile + VIX
  - [x] `_detect_trend()` — ADX thresholds
  - [x] `_detect_volatility_regime()` — ATR 252d percentile
  - [x] `get_regime_weights()` — 7 режимов
  - [x] `get_atr_multiplier()` — SL/TP множители
- [x] Alembic миграция: `regime_state`
- [x] Celery task: detect_all (каждый час)

### 2.1.5 Backtesting Engine

- [x] `backtest_engine.py`
  - [x] `run_walk_forward()` — IS(18m) → OOS(6m) × 5+
  - [x] `optimize_weights()` — grid search, Sharpe оптимизация
  - [x] `calculate_report()` — все метрики
- [x] `monte_carlo.py` — 10000 симуляций, CI drawdown (в backtest_engine.py)
- [x] `weight_validator.py` — auto revalidation (в backtest_engine.py)
- [x] Alembic миграции: `backtest_runs`, `backtest_trades`
- [x] Celery task: weekly backtest

### 2.1.6 Тесты Phase 2.1

- [x] `test_regime_detector.py`
- [x] `test_forex_fa.py`
- [x] `test_stock_fa.py`
- [x] `test_crypto_fa.py`
- [x] `test_backtest_engine.py`
- [x] `test_interest_rate_diff.py`
- [x] Coverage ≥ 90% для analysis/ и signals/

---

## Phase 2.2 — Сентимент и Order Flow (4-5 недель)

### 2.2.1 FinBERT микросервис

- [x] `services/finbert/Dockerfile`
- [x] `services/finbert/main.py` — POST /score, POST /batch, GET /health
- [x] `services/finbert/requirements.txt`
- [x] Добавить в docker-compose
- [x] `FinBERTClient` в src/analysis/

### 2.2.2 Sentiment Engine v2

- [x] `sentiment_engine_v2.py`
  - [x] Multi-source weighted scoring
  - [x] FinBERT вместо TextBlob
  - [x] Нормализация весов при отсутствии источника

### 2.2.3 Social Sentiment

- [x] `social_collector.py`
  - [x] Reddit (PRAW)
  - [x] Fear & Greed (Alternative.me)
  - [x] Stocktwits
  - [x] Options PCR (Yahoo)
- [x] Alembic миграция: `social_sentiment` (в v2 схеме)

### 2.2.4 Order Flow

- [x] `order_flow_collector.py`
  - [x] CVD (aggTrades)
  - [x] Funding Rate
  - [x] Open Interest
  - [x] Liquidations
  - [x] WebSocket буфер → БД (OrderFlowWebSocketCollector + Celery task)
- [x] Alembic миграция: `order_flow_data` (hypertable, в v2 схеме)

### 2.2.5 TA Engine v2

- [x] `ta_engine_v2.py`
  - [x] OF signals: CVD, OI, liquidations, funding
  - [x] RSI/MACD divergence
  - [x] OBV, MFI, VWAP
  - [x] Ichimoku cloud
  - [x] Pivot Points
  - [x] Market structure: HH/HL, BOS, CHoCH

### 2.2.6 Geo Engine v2

- [x] `geo_engine_v2.py`
  - [x] GDELT API
  - [x] Country → instrument mapping
  - [x] `fetch_gdelt_tone()`
  - [x] `calculate_geopolitical_risk()`
  - [x] `detect_risk_events()`
  - [x] Circuit breaker (fallback → 0)

### 2.2.7 Earnings Calendar

- [x] `earnings_collector.py`
  - [x] Finnhub + Yahoo earnings dates
  - [x] `get_earnings_risk()` — days, expected_move, consensus
  - [x] Правило: 2d → skip, 5d → -30%

### 2.2.8 On-Chain

- [x] `onchain_collector.py`
  - [x] Glassnode API
  - [x] CryptoQuant exchange flows
  - [x] MVRV, dominance, ETF flows
- [x] Alembic миграция: `onchain_data` (в v2 схеме)

### 2.2.9 Тесты Phase 2.2

- [x] `test_ta_engine_v2.py`
- [x] `test_sentiment_v2.py`
- [x] `test_social_collector.py`
- [x] `test_order_flow.py`
- [x] `test_geo_engine_v2.py`
- [x] `test_onchain.py`

---

## Phase 2.3 — Risk & Portfolio (3-4 недели)

### 2.3.1 Risk Manager v2

- [x] `risk_manager_v2.py`
  - [x] Regime-adaptive ATR multiplier
  - [x] SL alignment to S/R
  - [x] TP1/TP2/TP3 по режиму

### 2.3.2 Portfolio Risk

- [x] `portfolio_risk.py`
  - [x] Correlation matrix
  - [x] Position size adjustment
  - [x] Max open per market (3/2/5)
  - [x] Portfolio heat ≤ 6%

### 2.3.3 Trade Lifecycle

- [x] `trade_lifecycle.py`
  - [x] Breakeven after RR 1:1
  - [x] Partial close 50% at TP1
  - [x] Trailing: 0.5×ATR (trend), 0.3×ATR (range)

### 2.3.4 Virtual Portfolio

- [x] CRUD для `virtual_portfolio`
- [x] Интеграция с signal_tracker
- [x] PnL: open/closed, realized/unrealized
- [x] Alembic миграция: `virtual_portfolio`

### 2.3.5 Signal Engine v2

- [x] `signal_engine_v2.py`
  - [x] Regime-aware composite
  - [x] OF modifier (крипто)
  - [x] Earnings discount (акции)
  - [x] Confidence v2
  - [x] Portfolio heat check
  - [x] LLM validation

### 2.3.6 Webhooks

- [x] `notifications/webhook.py`
  - [x] MT5 webhook
  - [x] 3Commas webhook
  - [x] TradingView webhook

### 2.3.7 Тесты Phase 2.3

- [x] `test_risk_manager_v2.py`
- [x] `test_portfolio_risk.py`
- [x] `test_trade_lifecycle.py`
- [x] `test_signal_engine_v2.py`
- [x] `test_webhook.py`

---

## Phase 2.4 — Dashboard & API (3-4 недели)

### 2.4.1 REST API v2

- [x] `api/routes_v2.py` — все endpoints из ARCHITECTURE.md

### 2.4.2 WebSocket v2

- [x] signals, prices, portfolio streams

### 2.4.3 Next.js Frontend

- [x] Init Next.js 14 + TypeScript + Tailwind
- [x] Страницы: Dashboard, Signals, Instruments, Portfolio, Backtests, Macro, Accuracy, Settings
- [x] Компоненты: RegimeWidget, PortfolioHeatBar, DifferentialChart, EquityCurve, ComponentBreakdown

### 2.4.4 Telegram v2

- [x] Улучшенный формат уведомлений

### 2.4.5 Мониторинг

- [x] Prometheus + prometheus-fastapi-instrumentator
- [x] Grafana dashboard
- [x] Алерты: collector down, high latency

### 2.4.6 Тесты Phase 2.4

- [x] API integration tests
- [x] WebSocket tests
- [x] Frontend E2E (Playwright)

---

## Phase 2.5 — Hardening (2-3 недели)

- [x] Нагрузочное тестирование
- [x] Rate limit обработка с backoff для всех API
- [x] Circuit breaker для нестабильных API (`src/utils/circuit_breaker.py`)
- [x] Data quality мониторинг (`src/utils/data_quality.py`)
- [x] Финальный walk-forward по всем инструментам
- [x] OpenAPI документация
- [x] Deployment guide (`docs/DEPLOYMENT.md`)
- [x] Nginx: rate limiting, CORS, security headers
- [x] Docker secrets для production

---

## Phase 3.1 — Критические исправления (приоритет 1)

> v3 — калибровка, не расширение. Исправляем системные баги v2.
> Первоисточник: `docs/SPEC_V3.md`

### 3.1.1 MTF Filter: исправить порог направления

- [x] В `src/signals/mtf_filter.py`: `_get_direction_from_score` порог ±30 → ±BUY_THRESHOLD (7.0)
- [x] Вариант: передавать `buy_threshold` / `sell_threshold` через `__init__` (избежать circular import)
- [x] Тест: `_get_direction_from_score(10.0)` → 1, `(-8.0)` → -1, `(3.0)` → 0

### 3.1.2 Signal Engine: LLM_SCORE_THRESHOLD

- [x] Добавить `LLM_SCORE_THRESHOLD: float = 10.0` в `src/config.py`
- [x] В `signal_engine_v2.py`: заменить хардкод LLM порога на `settings.LLM_SCORE_THRESHOLD`
- [x] Тест: LLM вызывается при composite > 10.0

### 3.1.3 Signal Engine: FA маршрутизация по рынку

- [x] В `signal_engine_v2.py` блок FA: `market == "forex"` → `ForexFAEngine`, `"stocks"` → `StockFAEngine`
- [x] Commodities fallback → legacy `FAEngine`
- [x] Проверить что `ForexFAEngine.analyze()` возвращает нормализованный score (если ±10, применить scale-up ×6)
- [x] Тест: для EURUSD вызывается `ForexFAEngine`, для AAPL — `StockFAEngine`

### 3.1.4 Sentiment: инструментальная фильтрация

- [x] В `src/database/crud.py`: добавить `get_news_events_for_instrument(session, symbol, market, limit, hours_back)`
- [x] Добавить `_get_instrument_keywords(symbol, market)` — маппинг инструмент → ключевые слова
- [x] В `signal_engine_v2.py`: заменить `get_news_events(db, limit=30)` на `get_news_events_for_instrument(db, symbol, market)`
- [x] Тест: EURUSD получает новости с "EUR"/"ECB", BTCUSDT — с "bitcoin"/"BTC"
- [x] Тест: при < 5 результатах добавляются общие macro новости

### 3.1.5 Position Size: убрать хардкод

- [x] В `src/config.py`: добавить `SIGNAL_ACCOUNT_SIZE_USD: float = 10000.0`, `VIRTUAL_ACCOUNT_SIZE_USD: float = 1000.0`
- [x] В `signal_engine_v2.py`: заменить `Decimal("10000")` на `Decimal(str(settings.SIGNAL_ACCOUNT_SIZE_USD))`
- [x] В `trade_simulator` (если есть): заменить хардкод $1000 на `settings.VIRTUAL_ACCOUNT_SIZE_USD`
- [x] Обновить `.env.example`

---

## Phase 3.2 — TA Engine улучшения (приоритет 2)

### 3.2.1 RSI: контекстный сигнал через ADX

- [x] В `ta_engine.py`: реализовать `_rsi_signal(rsi, adx)` с ADX-контекстом
  - [x] ADX ≥ 25 (тренд): RSI 40-55 = pullback buy (strength), RSI < 30 = strength × 0.4
  - [x] ADX < 25 (флэт): RSI < 30 = classic bullish, RSI > 70 = classic bearish
- [x] Интегрировать `_rsi_signal()` в `generate_ta_signals()`
- [x] Тест: RSI=25 при ADX=30 → strength × 0.4 (не полный buy)

### 3.2.2 ADX fallback: корректная реализация без TA-Lib

- [x] В `ta_engine.py`: заменить fallback `return 25, 25, 25` на Directional Movement calculation
  - [x] True Range → ATR
  - [x] +DM / -DM → smoothed → +DI / -DI
  - [x] DX → smoothed → ADX
- [x] Тест: ADX fallback с реальными данными — plus_di ≠ minus_di

### 3.2.3 S/R: кластерная детекция

- [x] В `ta_engine.py`: заменить `min()/max()` на `_find_support_resistance()` с pivot clustering
  - [x] Pivot highs/lows (window=3)
  - [x] Кластеризация по ATR × 0.3
  - [x] Touch count → touch_boost = 1.0 + min(0.5, (touches-1) × 0.15)
- [x] Добавить `nearest_resistance_touches`, `nearest_support_touches` в indicators
- [x] Тест: 4+ касания дают touch_boost > 1.0

### 3.2.4 Volume signal: направление через SMA20

- [x] В `ta_engine.py`: volume direction = close vs SMA20 (вместо MACD direction)
- [x] Тест: volume > 1.5× avg при цене > SMA20 → bullish signal

### 3.2.5 Таймфрейм-адаптивные периоды индикаторов

- [x] В `ta_engine.py`: добавить `TF_INDICATOR_PERIODS` таблицу (M15, H1, H4, D1, _default)
- [x] `TAEngine.__init__`: принимать `timeframe` → `self._periods`
- [x] Заменить все хардкоды периодов на `self._periods["rsi"]`, `self._periods["sma_long"]` и т.д.
- [x] В `signal_engine_v2.py`: передавать `timeframe` при создании `TAEngine`
- [x] Тест: `TAEngine(df, "H4")._periods["sma_long"] == 100`

---

## Phase 3.3 — Risk Manager адаптация (приоритет 2)

### 3.3.1 Режим-адаптивные SL/TP

- [x] В `risk_manager_v2.py`: добавить таблицы `REGIME_SL_MULTIPLIERS`, `REGIME_TP1_RR`, `REGIME_TP2_RR`
- [x] Реализовать `calculate_levels_for_regime(entry, atr, direction, regime)`
- [x] Тест: RANGING → TP1_RR = 1.5 (не 2.0), HIGH_VOLATILITY → SL_mult = 2.5

### 3.3.2 Подключение режима в Signal Engine

- [x] В `signal_engine_v2.py`: получать `current_regime` через `RegimeDetector`
- [x] Передавать `regime` в `risk_manager.calculate_levels_for_regime()`
- [x] Сохранять `regime` в сигнале (для новой колонки БД)

### 3.3.3 Alembic migration: колонка regime

- [x] Создать миграцию: `ALTER TABLE signals ADD COLUMN regime VARCHAR(32) NULL` (уже в v2 схеме)
- [x] Индекс: `ix_signals_regime` (уже в v2 схеме)
- [x] Тест: миграция up/down

---

## Phase 3.4 — Signal Cooldown (приоритет 3)

### 3.4.1 Direction-reversal bypass

- [x] В `signal_engine_v2.py`: при активном cooldown проверять направление
  - [x] Загрузить последний сигнал из БД
  - [x] Проверить направление последнего сигнала vs текущего composite
  - [x] Если направление развернулось — bypass cooldown
- [x] Логирование: "Cooldown bypassed: direction reversal LONG → SHORT"

### 3.4.2 Тесты cooldown

- [x] Тест: cooldown + reversal → сигнал генерируется
- [x] Тест: cooldown + то же направление → сигнал блокируется

---

## Phase 3.5 — Тесты и валидация

### 3.5.1 Юнит-тесты

- [x] `tests/test_ta_engine_v3.py` — RSI context, S/R clusters, ADX fallback, TF periods
- [x] `tests/test_fa_routing_v3.py` — FA маршрутизация по рынку
- [x] `tests/test_sentiment_filtering_v3.py` — инструментальная фильтрация новостей
- [x] `tests/test_mtf_filter_v3.py` — исправленные пороги
- [x] `tests/test_risk_manager_v3.py` — режим-адаптивные SL/TP
- [x] `tests/test_signal_cooldown_v3.py` — direction reversal bypass

### 3.5.2 Интеграционные тесты

- [ ] `tests/test_signal_engine_v3_integration.py` — полный прогон generate_signal:
  - [ ] EURUSD/H1: ForexFAEngine вызван, LLM при composite > 10, MTF ненейтральный
  - [ ] BTCUSDT/H1: CryptoFAEngine вызван

### 3.5.3 Regression тесты

- [ ] `tests/test_signal_regression_v3.py` — snapshot expected ranges
  - [ ] ForexFA score > 5 (legacy был < 5)
  - [ ] MTF direction не всегда neutral

### 3.5.4 Coverage

- [ ] `src/analysis/ta_engine.py` ≥ 90%
- [ ] `src/signals/signal_engine_v2.py` (изменённые блоки) ≥ 85%
- [ ] `src/signals/mtf_filter.py` = 100%
- [ ] `src/signals/risk_manager_v2.py` ≥ 90%
- [ ] `src/database/crud.py` (`get_news_events_for_instrument`) ≥ 85%
