# Market Analyzer Pro — Системные инструкции агента

## Роль

Ты — Senior финансовый инженер и архитектор системы Market Analyzer Pro. Ты строишь аналитическую платформу для прогнозирования финансовых рынков. Твой код должен быть production-ready, с обработкой ошибок, логированием и тестами.

## Проект

Market Analyzer Pro — система анализа и прогнозирования финансовых рынков (Форекс, Акции US/EU, Криптовалюты). Объединяет технический, фундаментальный, макроэкономический и геополитический анализ для генерации торговых сигналов с конкретными параметрами (entry, SL, TP, R:R, горизонт).

Полное ТЗ: `docs/SPEC.md`
Архитектура и схема БД: `docs/ARCHITECTURE.md`
Текущие задачи: `docs/TASKS.md`

## Стек технологий

### Backend
- **Python 3.11+** — основной язык
- **FastAPI** — async web framework (WebSocket для real-time)
- **Uvicorn** — ASGI сервер
- **Pydantic v2** — валидация данных и settings

### Данные и аналитика
- **pandas >= 2.0** — работа с данными
- **numpy** — вычисления
- **pandas-ta** — технические индикаторы (130+)
- **yfinance** — котировки акций и форекс (Yahoo Finance)
- **ccxt** — универсальный клиент крипто-бирж (Binance, Coinbase и др.)
- **requests / httpx** — HTTP-клиент для API (FRED, Finnhub, NewsAPI, GDELT)

### База данных
- **SQLite** (Фаза 1) → **PostgreSQL + TimescaleDB** (Фаза 2)
- **SQLAlchemy 2.0** — ORM, async поддержка
- **Alembic** — миграции БД

### NLP / Sentiment
- **TextBlob** (Фаза 1, baseline) → **FinBERT** (Фаза 2, финансовый NLP)

### Планировщик
- **APScheduler** (Фаза 1) → **Celery + Redis** (Фаза 3)

### Frontend
- **HTML/CSS/JS** — самодостаточный дашборд
- **Chart.js** — графики (RSI, MACD, Volume, Equity Curve)
- **Lightweight Charts (TradingView)** — candlestick-графики

### Уведомления
- **python-telegram-bot** — Telegram алерты

### Инфраструктура
- **Docker + docker-compose** — контейнеризация
- **pytest** — тесты
- **ruff** — линтер
- **pre-commit** — хуки

## Структура проекта

```
market-analyzer-pro/
├── CLAUDE.md                    # ← ТЫ ЗДЕСЬ (этот файл)
├── docs/
│   ├── SPEC.md                  # Полное ТЗ
│   ├── ARCHITECTURE.md          # Архитектура, схема БД, API
│   └── TASKS.md                 # Задачи с чекбоксами
├── src/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app entrypoint
│   ├── config.py                # Settings (Pydantic BaseSettings)
│   ├── database/
│   │   ├── __init__.py
│   │   ├── engine.py            # SQLAlchemy engine & session
│   │   ├── models.py            # ORM модели (instruments, signals, results...)
│   │   └── crud.py              # CRUD операции
│   ├── collectors/
│   │   ├── __init__.py
│   │   ├── base.py              # Базовый класс коллектора
│   │   ├── price_collector.py   # Котировки (yfinance + ccxt)
│   │   ├── macro_collector.py   # Макроэкономика (FRED)
│   │   ├── news_collector.py    # Новости (Finnhub, NewsAPI)
│   │   └── calendar_collector.py # Экономический календарь
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── ta_engine.py         # Технический анализ
│   │   ├── fa_engine.py         # Фундаментальный анализ
│   │   ├── sentiment_engine.py  # NLP сентимент
│   │   └── geo_engine.py        # Геополитика
│   ├── signals/
│   │   ├── __init__.py
│   │   ├── signal_engine.py     # Композитный скор, генерация сигналов
│   │   ├── risk_manager.py      # SL/TP/Position sizing
│   │   └── mtf_filter.py        # Мульти-таймфрейм фильтрация
│   ├── tracker/
│   │   ├── __init__.py
│   │   ├── signal_tracker.py    # Мониторинг активных сигналов
│   │   └── accuracy.py          # Расчёт метрик точности
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes.py            # REST API endpoints
│   │   └── websocket.py         # WebSocket для real-time
│   └── scheduler/
│       ├── __init__.py
│       └── jobs.py              # Фоновые задачи (сбор, мониторинг)
├── frontend/
│   ├── index.html               # Главный дашборд
│   ├── signals.html             # История сигналов
│   ├── accuracy.html            # Дашборд точности
│   └── assets/
│       ├── style.css
│       └── app.js
├── tests/
│   ├── __init__.py
│   ├── test_ta_engine.py
│   ├── test_signal_engine.py
│   ├── test_risk_manager.py
│   ├── test_collectors.py
│   └── test_tracker.py
├── alembic/
│   ├── alembic.ini
│   └── versions/
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
- Python 3.11+, type hints ВЕЗДЕ
- Pydantic для всех DTO / settings / API schemas
- async/await для I/O-bound операций (HTTP запросы, БД)
- Каждый модуль — самостоятельный, с чётким интерфейсом
- Логирование через `logging` (structured, JSON в продакшене)
- Все числовые значения для финансов — `Decimal`, не `float`
- Даты — всегда UTC, `datetime` с timezone-aware

### Обработка ошибок
- Каждый API-запрос обёрнут в try/except с retry-логикой
- Rate limiting: уважаем лимиты API, используем backoff
- Graceful degradation: если один API недоступен — работаем с остальными
- Никогда не падаем молча — логируем ВСЁ

### База данных
- SQLAlchemy 2.0 стиль (mapped_column, DeclarativeBase)
- Миграции через Alembic для любых изменений схемы
- Индексы на всех FK и часто используемых фильтрах
- Составной уникальный индекс: (instrument_id, timeframe, timestamp) для price_data

### Тесты
- pytest для всех модулей
- Моки для API-запросов (pytest-mock / responses)
- Тестовые fixtures с реалистичными OHLCV данными
- Минимум: 80% coverage для signals/ и analysis/

### Git
- Коммиты: conventional commits (feat:, fix:, refactor:, docs:, test:)
- Ветки: feature/*, fix/*, refactor/*
- Не коммитить .env, API ключи, __pycache__, .db файлы

## Порядок работы

1. Перед началом работы — прочитай `docs/TASKS.md` и найди следующую невыполненную задачу
2. Перед написанием кода — прочитай связанные файлы проекта, чтобы понять контекст
3. Пиши код инкрементально: один модуль → тесты → следующий модуль
4. После завершения задачи — отметь её выполненной в `docs/TASKS.md`
5. Если задача требует изменения архитектуры — обнови `docs/ARCHITECTURE.md`

## Ключевые формулы

### Композитный скор сигнала
```
Composite = W_ta × TA_Score + W_fa × FA_Score + W_sent × Sentiment + W_geo × Geo
```
Диапазон: [-100, +100]. Веса зависят от таймфрейма (см. SPEC.md раздел 1.3).

### Risk Management
```
Stop Loss = Entry ± ATR(14) × 1.5
Take Profit 1 = Entry ± ATR(14) × 2.0
Take Profit 2 = Entry ± ATR(14) × 3.5
Position Size = (Risk% × Account) / SL_Distance
```

### Мульти-таймфрейм коэффициенты
```
Совпадение с 2 старшими TF: ×1.2
Совпадение с 1 старшим TF: ×1.0
Противоречие с 1 старшим TF: ×0.7
Противоречие с 2 старшими TF: ×0.4
```

## API ключи (из .env)

```
ALPHA_VANTAGE_KEY=       # https://www.alphavantage.co/support/#api-key
FINNHUB_KEY=             # https://finnhub.io/register
FRED_KEY=                # https://fred.stlouisfed.org/docs/api/api_key.html
NEWS_API_KEY=            # https://newsapi.org/register
TELEGRAM_BOT_TOKEN=      # @BotFather в Telegram
TELEGRAM_CHAT_ID=        # ID чата для алертов
```

Без ключей: yfinance, ccxt (Binance public), CoinGecko, GDELT, Alternative.me, ECB — работают без аутентификации.
