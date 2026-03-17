# Market Analyzer Pro — План задач

> Отмечай задачи как выполненные [x] по мере продвижения.
> Порядок выполнения — сверху вниз. Не перескакивай.

---

## Фаза 1 — MVP

### 1.1. Инициализация проекта
- [x] Создать структуру каталогов (src/, tests/, frontend/, alembic/, docs/, data/)
- [x] Создать `pyproject.toml` с конфигурацией проекта
- [x] Создать `requirements.txt` и `requirements-dev.txt`
- [x] Создать `.env.example` с описанием всех переменных
- [x] Создать `src/config.py` — Pydantic Settings из `.env`
- [x] Создать `Makefile` с командами (install, run, test, lint, migrate)
- [x] Создать `.gitignore`
- [x] Проверить: `pip install -r requirements.txt` проходит без ошибок

### 1.2. База данных
- [x] Создать `src/database/engine.py` — async SQLAlchemy engine + session factory
- [x] Создать `src/database/models.py` — все ORM модели (instruments, price_data, signals, signal_results, accuracy_stats, macro_data, news_events)
- [x] Создать `src/database/crud.py` — CRUD операции для всех таблиц
- [x] Настроить Alembic (alembic init, env.py, initial migration)
- [x] Создать seed-скрипт: заполнить instruments таблицу начальным набором (5 форекс, 5 акций, 3 крипто)
- [x] Написать тест: создание БД, миграция, seed, проверка данных

### 1.3. Data Collectors
- [x] Создать `src/collectors/base.py` — BaseCollector (ABC) с retry, logging, rate limiting
- [x] Создать `src/collectors/price_collector.py`:
  - [x] `YFinanceCollector` — акции, форекс, индексы (yfinance)
  - [x] `CcxtCollector` — крипто через ccxt (Binance public)
  - [x] Метод `collect_historical(symbol, timeframe, start, end)` → сохранение в price_data
  - [x] Метод `collect_latest(symbol, timeframe)` → последние N свечей
- [x] Создать `src/collectors/macro_collector.py`:
  - [x] FRED API интеграция (ставка ФРС, CPI, NFP, PMI, GDP, безработица)
  - [x] Парсинг и нормализация → сохранение в macro_data
- [x] Создать `src/collectors/news_collector.py`:
  - [x] Finnhub News API интеграция
  - [x] Базовый sentiment scoring (TextBlob)
  - [x] Сохранение в news_events
- [x] Создать `src/collectors/calendar_collector.py`:
  - [x] Finnhub Calendar → экономические события
- [x] Написать тесты для всех коллекторов (с моками API)

### 1.4. Technical Analysis Engine
- [x] Создать `src/analysis/ta_engine.py`:
  - [x] Класс `TAEngine` принимает DataFrame с OHLCV
  - [x] Метод `calculate_all_indicators()` → dict с значениями всех индикаторов
  - [x] Метод `generate_ta_signals()` → dict с сигналами каждого индикатора
  - [x] Метод `calculate_ta_score()` → взвешенный TA_Score [-100, +100]
  - [x] Индикаторы через ta-lib: RSI, MACD, BB, SMA(20/50/200), EMA(12/26), ADX, Stochastic, ATR
  - [x] Определение Support/Resistance (pivot points)
  - [x] Свечные паттерны (через ta-lib)
- [x] Написать тесты: известные данные → проверка расчёта RSI, MACD, BB, скор

### 1.5. Signal Engine
- [x] Создать `src/signals/signal_engine.py`:
  - [x] Класс `SignalEngine`
  - [x] Метод `generate_signal(instrument, timeframe)` → Signal (Pydantic model)
  - [x] Вызывает TA Engine (и FA/Sentiment когда будут готовы)
  - [x] Расчёт composite_score с весами из config
  - [x] Определение direction и signal_strength по порогам
- [x] Создать `src/signals/risk_manager.py`:
  - [x] Класс `RiskManager`
  - [x] Метод `calculate_levels(entry, atr, direction)` → SL, TP1, TP2
  - [x] Метод `calculate_position_size(account, risk_pct, sl_distance)` → lot size
  - [x] Correlation filter (если есть другие активные сигналы)
- [x] Создать `src/signals/mtf_filter.py`:
  - [x] Мульти-таймфрейм подтверждение
  - [x] Коэффициенты: ×1.2, ×1.0, ×0.7, ×0.4
- [x] Написать тесты: моковые данные → проверка сигнала, SL/TP, R:R

### 1.6. Сохранение сигналов в БД
- [x] В `signal_engine.py` добавить сохранение сигнала через CRUD
- [x] Сохранять indicators_snapshot как JSON
- [x] Сохранять reasoning как JSON (ключевые факторы, описание)
- [x] Устанавливать expires_at на основе горизонта
- [x] Тест: генерация сигнала → запись в БД → чтение → проверка полей

### 1.7. Signal Tracker (базовый)
- [x] Создать `src/tracker/signal_tracker.py`:
  - [x] Метод `check_active_signals()` — для всех сигналов со status in (created, active, tracking)
  - [x] Логика перехода: created→active (цена достигла entry)
  - [x] Логика перехода: tracking→completed (SL/TP hit)
  - [x] Логика перехода: tracking→expired (время вышло)
  - [x] Запись MFE/MAE при каждой проверке
  - [x] Создание signal_results при закрытии
- [x] Написать тесты: симуляция движения цены → проверка статусов

### 1.8. FastAPI Backend
- [x] Создать `src/main.py` — FastAPI app с lifespan
- [x] Создать `src/api/routes.py`:
  - [x] GET /api/instruments
  - [x] GET /api/prices/{symbol} (query: timeframe, from, to)
  - [x] POST /api/analyze/{symbol} (query: timeframe) — запуск анализа, возврат сигнала
  - [x] GET /api/signals (query: status, market, from, to, limit)
  - [x] GET /api/signals/active
  - [x] GET /api/signals/{id}
  - [x] GET /api/accuracy
  - [x] GET /api/health
  - [x] GET /api/macro/{country}
  - [x] GET /api/news
- [x] Pydantic schemas для всех request/response
- [x] Подключить static files для frontend/
- [x] WebSocket: /ws/prices/{symbol}, /ws/signals

### 1.9. Scheduler
- [x] Создать `src/scheduler/jobs.py`:
  - [x] Job: collect_prices — каждые N мин
  - [x] Job: collect_news — каждые 15 мин
  - [x] Job: check_signals — каждые 5 мин (tracker)
  - [x] Job: collect_macro — 1×/день
- [x] Интеграция APScheduler с FastAPI startup/shutdown

### 1.10. Frontend (обновление)
- [x] Создать `frontend/index.html` — главный дашборд
- [x] fetch('/api/instruments') → заполнение сайдбара
- [x] fetch('/api/analyze/{symbol}') → обновление графиков и сигналов
- [x] fetch('/api/signals/active') → панель активных сигналов
- [x] Добавить Lightweight Charts (TradingView) для candlestick
- [x] Тёмная тема, профессиональный трейдинговый UI

### 1.11. Docker
- [x] Создать `Dockerfile` (python:3.11-slim, pip install, uvicorn)
- [x] Создать `docker-compose.yml` (app + volumes для data/ и .env)

### 1.12. Интеграционный тест Фазы 1
- [x] E2E: запуск → сбор данных → анализ → сигнал → запись в БД → проверка
- [x] Все эндпоинты зарегистрированы и работают
- [x] Дашборд работает с реальными данными из API

---

## Фаза 2 — Полный функционал (после завершения Фазы 1)

### 2.1. FA Engine
- [x] Создать `src/analysis/fa_engine.py` (Форекс, Акции, Крипто факторы)
- [x] Весовая система для каждого рынка
- [x] Тесты

### 2.2. Sentiment Engine
- [x] Создать `src/analysis/sentiment_engine.py` (TextBlob)
- [x] NLP pipeline для новостей
- [x] Тесты

### 2.3. Accuracy Tracker полный
- [x] Создать `src/tracker/accuracy.py`:
  - [x] Win Rate, Profit Factor, Sharpe, Drawdown
  - [x] Разбивка по инструментам, TF, рынкам
  - [x] Запись в accuracy_stats
- [x] Тесты

### 2.4. PostgreSQL миграция
- [ ] docker-compose с PostgreSQL
- [ ] Обновить DATABASE_URL
- [ ] TimescaleDB для price_data
- [ ] Миграция существующих данных

### 2.5. Frontend v2
- [x] Страница истории сигналов (signals.html)
- [x] Дашборд точности (accuracy.html)
- [x] Equity Curve график (Chart.js)
- [ ] Страница настроек

### 2.6. Экономический календарь
- [x] Коллектор создан (FinnhubCalendarCollector)
- [ ] UI компонент с предстоящими событиями

---

## Фаза 3 — Продвинутые функции (после завершения Фазы 2)

- [ ] Авто-калибровка весов (ML на исторических данных)
- [x] Telegram Bot (уведомления о сигналах) — реализован как `src/notifications/telegram.py`
- [x] Геополитический модуль (GDELT integration) — stub в `src/analysis/geo_engine.py`
- [x] Backtesting Engine — stub в `src/backtesting/engine.py`
- [x] Paper Trading (виртуальный счёт) — stub в `src/trading/paper_trading.py`
- [ ] Commodities (XAU, WTI, Brent)
- [ ] Брокерская интеграция (Interactive Brokers API)
