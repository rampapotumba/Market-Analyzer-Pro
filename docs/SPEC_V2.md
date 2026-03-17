# Market Analyzer Pro — Техническое задание v2.0

**Дата:** 2026-03-17
**Статус:** Draft
**Основание:** Аудит v1 — критические пробелы в FA-блоке, отсутствие валидации весов, неработающий Geo-блок, отсутствие order flow и режимной детекции.

---

## Содержание

1. [Цель и ограничения](#1-цель-и-ограничения)
2. [Архитектура системы](#2-архитектура-системы)
3. [Сбор данных (Data Layer)](#3-сбор-данных-data-layer)
4. [Аналитические модули](#4-аналитические-модули)
5. [Сигнальный движок](#5-сигнальный-движок)
6. [Risk Management](#6-risk-management)
7. [Backtesting & Validation](#7-backtesting--validation)
8. [База данных](#8-база-данных)
9. [API и уведомления](#9-api-и-уведомления)
10. [Frontend (Dashboard v2)](#10-frontend-dashboard-v2)
11. [Инфраструктура](#11-инфраструктура)
12. [Нефункциональные требования](#12-нефункциональные-требования)
13. [План реализации (Phases)](#13-план-реализации-phases)

---

## 1. Цель и ограничения

### 1.1 Цель v2

Создать профессиональную аналитическую платформу, способную генерировать торговые сигналы со статистически валидированными весами компонентов, полноценным фундаментальным анализом для всех рынков, реальным order flow и автоматическим режимным детектором. Система должна быть пригодна для принятия торговых решений с реальным капиталом при условии ручной финальной верификации оператором.

### 1.2 Целевые рынки

| Рынок | Инструменты | Таймфреймы |
|---|---|---|
| Forex Major | EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, NZDUSD, USDCAD | M15, H1, H4, D1 |
| Forex Minor | EURGBP, EURJPY, GBPJPY, AUDJPY | H1, H4, D1 |
| US Stocks | SPY, QQQ + топ-50 S&P500 по капитализации | H1, H4, D1 |
| EU Stocks | DAX, FTSE100 + топ-20 Eurostoxx | H4, D1 |
| Crypto Major | BTC/USDT, ETH/USDT, SOL/USDT, BNB/USDT | M15, H1, H4, D1 |
| Commodities | XAUUSD (золото), XAGUSD, WTI, Brent | H1, H4, D1 |

### 1.3 Ключевые ограничения

- **Данные:** без платных Bloomberg/Refinitiv терминалов — только открытые и условно-бесплатные API
- **Latency:** система не предназначена для HFT; минимальный таймфрейм M15
- **Исполнение:** сигналы для ручного исполнения + опциональный webhook для Metatrader/3Commas
- **Финальное решение:** всегда за оператором; система — советник, не автопилот

---

## 2. Архитектура системы

### 2.1 Компонентная схема

```
┌─────────────────────────────────────────────────────────────────┐
│                        DATA LAYER                               │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐   │
│  │  Price   │ │  Macro   │ │   News   │ │   Order Flow     │   │
│  │Collector │ │Collector │ │Collector │ │   Collector      │   │
│  │yfinance  │ │FRED+ECB  │ │FinBERT  │ │ (Binance/Bybit)  │   │
│  │CCXT      │ │BOJ+BOE  │ │RSS+X API │ │  CVD, Funding    │   │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────────┬─────────┘   │
└───────┼────────────┼────────────┼─────────────────┼─────────────┘
        │            │            │                 │
┌───────▼────────────▼────────────▼─────────────────▼─────────────┐
│                     PostgreSQL + TimescaleDB                      │
│   price_data │ macro_data │ news_events │ order_flow_data        │
│   signals    │ signal_results │ backtest_runs │ regime_state     │
└─────────────────────────────┬────────────────────────────────────┘
                              │
┌─────────────────────────────▼────────────────────────────────────┐
│                     ANALYSIS LAYER                                │
│  ┌─────────┐ ┌─────────┐ ┌──────────┐ ┌──────┐ ┌────────────┐  │
│  │TA Engine│ │FA Engine │ │Sentiment │ │ Geo  │ │  Regime    │  │
│  │v2 + OF  │ │v2 Multi- │ │ FinBERT  │ │Engine│ │ Detector   │  │
│  │         │ │  CB      │ │ + Social │ │GDELT │ │            │  │
│  └────┬────┘ └────┬─────┘ └────┬─────┘ └──┬───┘ └─────┬──────┘  │
│       │           │            │           │           │          │
│  ┌────▼───────────▼────────────▼───────────▼───────────▼──────┐  │
│  │              SIGNAL ENGINE v2                               │  │
│  │  Regime-aware weights → Composite Score → LLM Validation   │  │
│  └────────────────────────┬────────────────────────────────────┘  │
└───────────────────────────┼──────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────┐
│                   OUTPUT LAYER                                    │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────────┐  │
│  │  Risk Mgr v2 │ │  Backtester  │ │  Notifications           │  │
│  │  (Portfolio- │ │  Walk-Forward│ │  Telegram + Webhook       │  │
│  │   aware)     │ │  Monte Carlo │ │  (MT4/MT5, 3Commas)       │  │
│  └──────────────┘ └──────────────┘ └──────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │  Dashboard v2 (React/Next.js)                              │   │
│  │  TradingView Charts │ Portfolio view │ Backtest results     │   │
│  └────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 Технологический стек v2

| Компонент | v1 | v2 |
|---|---|---|
| БД | SQLite | PostgreSQL 16 + TimescaleDB |
| ORM | SQLAlchemy async | SQLAlchemy async (без изменений) |
| NLP | TextBlob | FinBERT (transformers) |
| Task queue | APScheduler | Celery 5 + Redis |
| Cache | in-memory dict | Redis (TTL-кэш) |
| Frontend | Vanilla JS | Next.js 14 + TypeScript |
| Charts | Chart.js + LW Charts | TradingView Charting Library (free) |
| Backtesting | - (пусто) | vectorbt + кастомный walk-forward |
| Monitoring | logging | Prometheus + Grafana |
| Secrets | .env | Vault / Docker secrets |

---

## 3. Сбор данных (Data Layer)

### 3.1 Ценовые данные

#### 3.1.1 Primary: yfinance + CCXT (без изменений для EOD)

Без изменений для таймфреймов H4, D1, W1.

#### 3.1.2 NEW: Intraday реальный курс (M15, H1)

**Форекс:**
- **Alpha Vantage FX** (500 req/day free) — OHLCV с 1-минутным разрешением, задержка 15 мин
- **ExchangeRate-API** (1500 req/month free) — spot rates для валидации

**Акции (US):**
- **Polygon.io** (бесплатный tier: 5 API calls/min, 2-year history) — приоритет
- yfinance как fallback

**Крипто:**
- Binance WebSocket (`wss://stream.binance.com`) — **real-time OHLCV** без задержки
- Bybit WebSocket — дублирование для BTC/ETH

#### 3.1.3 NEW: Tick / Order Flow данные

Только для крипто (единственный рынок с открытым доступом):

```python
class OrderFlowCollector:
    """Collects Binance aggTrades for CVD calculation."""

    # REST: GET /api/v3/aggTrades — последние 1000 сделок
    # WebSocket: wss://stream.binance.com/ws/<symbol>@aggTrade

    async def calculate_cvd(self, symbol: str, lookback_minutes: int) -> float:
        """Cumulative Volume Delta за период."""
        # buy_volume - sell_volume; taker side определяет направление

    async def get_funding_rate(self, symbol: str) -> dict:
        """Текущий и прогнозируемый funding rate."""
        # Binance: GET /fapi/v1/premiumIndex

    async def get_open_interest(self, symbol: str) -> dict:
        """Open Interest и его изменение за 24ч."""
        # Binance: GET /fapi/v1/openInterest

    async def get_liquidations(self, symbol: str, lookback_hours: int) -> dict:
        """Объём ликвидаций long/short за период."""
        # Binance: GET /fapi/v1/forceOrders
```

**Хранение order flow:**

```sql
CREATE TABLE order_flow_data (
    id BIGSERIAL PRIMARY KEY,
    instrument_id INT REFERENCES instruments(id),
    timestamp TIMESTAMPTZ NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    cvd NUMERIC(18,4),                -- Cumulative Volume Delta
    cvd_change_pct NUMERIC(8,4),      -- изменение CVD за период
    open_interest NUMERIC(18,4),
    oi_change_pct NUMERIC(8,4),
    funding_rate NUMERIC(10,6),
    funding_rate_predicted NUMERIC(10,6),
    long_liquidations NUMERIC(18,4),
    short_liquidations NUMERIC(18,4),
    buy_volume NUMERIC(18,4),
    sell_volume NUMERIC(18,4),
    delta NUMERIC(18,4)               -- buy_volume - sell_volume за бар
);
SELECT create_hypertable('order_flow_data', 'timestamp');
```

### 3.2 Макроэкономические данные

#### 3.2.1 Действующие источники (FRED) — расширить индикаторами

Добавить к существующим FEDFUNDS/CPI/UNRATE/GDPC1:

```python
FRED_INDICATORS_EXTENDED = {
    # Уже есть:
    "FEDFUNDS",   # Ставка ФРС
    "CPIAUCSL",   # CPI США
    "UNRATE",     # Безработица США
    "GDPC1",      # ВВП США

    # Добавить:
    "DEXUSEU",    # EUR/USD официальный курс (для кросс-валидации)
    "T10YIE",     # 10-Year Breakeven Inflation Rate
    "T10Y2Y",     # Yield Curve Spread (рецессионный индикатор)
    "BAMLH0A0HYM2", # High Yield spread (риск-аппетит)
    "VIXCLS",     # VIX (дублирует yfinance, но официальный)
    "DCOILWTICO", # Цена нефти WTI
    "GOLDAMGBD228NLBM", # Цена золота (London Fix)
    "WALCL",      # Баланс ФРС (QE/QT индикатор)
    "M2SL",       # M2 Money Supply
}
```

#### 3.2.2 NEW: ECB Data Warehouse API

```
Base URL: https://data-api.ecb.europa.eu/service/data/
Лицензия: бесплатно, без ключа
```

```python
ECB_SERIES = {
    "FM.B.U2.EUR.RT.MM.EURIBOR3MD_.HSTA": "EURIBOR_3M",  # Ставка денежного рынка
    "ICP.M.U2.N.000000.4.ANR": "EURO_CPI_YOY",            # Инфляция еврозоны
    "STS.M.DE.N.PROD.NS0020.4.000": "DE_INDUSTRIAL_PROD", # Промпроизводство Германии
    "MNA.Q.Y.I8.W2.S1.S1.B.B1GQ._Z._Z._Z.EUR.LR.N": "EA_GDP_QOQ", # ВВП еврозоны
    "EXR.D.USD.EUR.SP00.A": "EURUSD_OFFICIAL",             # Официальный EUR/USD
}
```

#### 3.2.3 NEW: BOJ (Bank of Japan) — для JPY-пар

```
Source: https://www.stat-search.boj.or.jp/index_en.html (JSON API)
```

```python
BOJ_SERIES = {
    "FM01'STBJLPNL'": "BOJ_RATE",         # Overnight Call Rate
    "PR01'SPNFCPIALLITEMS'": "JAPAN_CPI", # CPI Японии
    "OP01'JPNIMVGJDQ'": "JAPAN_GDP",      # ВВП Японии
}
```

#### 3.2.4 NEW: BOE (Bank of England) — для GBP-пар

```
Source: https://www.bankofengland.co.uk/boeapps/database/ (CSV/JSON)
```

```python
BOE_SERIES = {
    "IUMABEDR": "BOE_BASE_RATE",   # База BOE
    "RPIX": "UK_CPI",              # CPI Великобритании
    "ABMI": "UK_GDP",              # ВВП Великобритании
}
```

#### 3.2.5 NEW: Расчёт процентного дифференциала

Критически важный компонент для форекс FA:

```python
class InterestRateDifferential:
    """Calculates interest rate differential between currency pairs."""

    CURRENCY_RATE_MAP = {
        "USD": "FEDFUNDS",
        "EUR": "ECB_RATE",
        "GBP": "BOE_RATE",
        "JPY": "BOJ_RATE",
        "AUD": "RBA_RATE",     # RBA: публичный CSV
        "CAD": "BOC_RATE",     # BOC: публичный JSON
        "CHF": "SNB_RATE",     # SNB: публичный XML
        "NZD": "RBNZ_RATE",    # RBNZ: публичный JSON
    }

    def calculate_differential(self, symbol: str) -> float:
        """
        Для EUR/USD: ECB_RATE - FEDFUNDS
        Положительный → EUR сильнее → bullish EUR/USD
        Отрицательный → USD сильнее → bearish EUR/USD
        """

    def calculate_differential_trend(self, symbol: str, lookback_months: int = 6) -> float:
        """
        Тренд дифференциала важнее абсолютного значения.
        Сужающийся спред в пользу EUR → bullish EUR/USD даже если EUR < USD.
        """
```

### 3.3 Новостные данные и сентимент

#### 3.3.1 Источники (расширить)

| Источник | Тип | Лимит | Инструменты |
|---|---|---|---|
| Finnhub | REST | 60 req/min (free) | Акции, форекс |
| NewsAPI | REST | 100 req/day (free) | Все |
| RSS-агрегатор | RSS | Без лимита | Все |
| Reddit API | REST | 60 req/min | Крипто, акции |
| X (Twitter) API | REST | 500k tweets/month (basic) | Крипто, форекс |
| Stocktwits | REST | Без ключа | Акции |
| Alternative.me | REST | Без ключа | Fear&Greed (крипто) |

#### 3.3.2 NEW: FinBERT интеграция

```python
# requirements.txt дополнения:
# transformers>=4.38.0
# torch>=2.2.0
# sentence-transformers>=2.6.0

class FinBERTEngine:
    """
    ProsusAI/finbert — fine-tuned BERT для финансовых текстов.
    Классификация: positive / negative / neutral
    с вероятностями для каждого класса.
    """

    MODEL_NAME = "ProsusAI/finbert"

    def __init__(self):
        # Загружается один раз при старте
        self.tokenizer = AutoTokenizer.from_pretrained(self.MODEL_NAME)
        self.model = AutoModelForSequenceClassification.from_pretrained(self.MODEL_NAME)
        self.pipeline = pipeline("sentiment-analysis", model=self.model,
                                 tokenizer=self.tokenizer, device=-1)  # CPU

    def score_text(self, text: str) -> float:
        """Returns score in [-1, +1]: positive=+1, negative=-1, neutral=0."""
        result = self.pipeline(text[:512])[0]  # BERT limit
        label = result["label"]
        confidence = result["score"]

        if label == "positive":
            return confidence
        elif label == "negative":
            return -confidence
        return 0.0

    def score_batch(self, texts: list[str]) -> list[float]:
        """Batch processing для эффективности."""
```

**Размещение модели:** при старте Docker-контейнера, кэшируется в volume `/models/finbert/`. Размер: ~440MB.

#### 3.3.3 NEW: Social Sentiment

```python
class SocialSentimentCollector:

    async def get_reddit_sentiment(self, query: str, subreddits: list[str]) -> float:
        """
        Subreddits: r/wallstreetbets (акции), r/Bitcoin, r/CryptoCurrency
        Использует PRAW (Python Reddit API Wrapper).
        Агрегирует upvote_ratio + FinBERT(title+body) последних 50 постов.
        """

    async def get_fear_greed_index(self) -> dict:
        """
        Alternative.me: https://api.alternative.me/fng/?limit=7
        Только для крипто.
        Returns: {"value": 72, "classification": "Greed", "7d_trend": +5}
        """

    async def get_stocktwits_sentiment(self, symbol: str) -> dict:
        """
        https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json
        Bearish/Bullish ratio из последних 30 твитов.
        """

    async def get_options_sentiment(self, symbol: str) -> dict:
        """
        Put/Call Ratio из Yahoo Finance options chain.
        PCR > 1.2 → bearish, PCR < 0.7 → bullish (contrarian).
        Только для US акций и индексов.
        """
```

### 3.4 Экономический календарь

#### 3.4.1 NEW: Investing.com / ForexFactory парсинг

```python
class EconomicCalendarCollector:
    """
    Источник: Finnhub economic calendar (бесплатно)
    Дополнение: Trading Economics API (бесплатный tier)
    """

    HIGH_IMPACT_EVENTS = [
        "NFP",             # Non-Farm Payrolls
        "FOMC",            # Fed Meeting + Minutes
        "CPI",             # Consumer Price Index
        "GDP",             # GDP Release
        "PMI",             # PMI Manufacturing/Services
        "RETAIL_SALES",
        "UNEMPLOYMENT",
        "ECB_DECISION",    # NEW
        "BOJ_DECISION",    # NEW
        "BOE_DECISION",    # NEW
        "EARNINGS",        # Отчётность компаний
    ]

    async def get_upcoming_events(self, hours_ahead: int = 48) -> list[EconomicEvent]:
        """Возвращает события с прогнозом, предыдущим и ожидаемым значением."""

    async def calculate_event_impact(self, event: EconomicEvent) -> float:
        """
        Сравнивает actual vs forecast.
        Сюрприз = (actual - forecast) / abs(forecast) * 100
        Нормализованный impact score в [-1, +1].
        """
```

#### 3.4.2 NEW: Earnings Calendar для акций

```python
class EarningsCalendarCollector:
    """
    Source: Finnhub /calendar/earnings (бесплатно)
    Дополнение: Yahoo Finance earnings dates
    """

    async def get_earnings_risk(self, symbol: str) -> dict:
        """
        Возвращает:
        - days_to_earnings: int
        - expected_move_pct: float (implied move из options)
        - consensus_eps: float
        - consensus_revenue: float
        - surprise_history: list[float]  # последние 4 квартала
        """

    # ПРАВИЛО: за 2 дня до earnings — снизить confidence сигнала на 30%
    # В день earnings — не генерировать сигналы для этого инструмента
```

### 3.5 On-chain данные для крипто

```python
class OnChainCollector:
    """
    Glassnode API (бесплатный tier: daily metrics only)
    Alternative: CryptoQuant (публичные endpoints)
    """

    GLASSNODE_METRICS = {
        "addresses/active_count": "active_addresses",
        "transactions/count": "tx_count",
        "market/nvt": "nvt_ratio",             # NVT ratio (P/E для крипто)
        "mining/hash_rate_mean": "hashrate",   # Только BTC
        "supply/profit_relative": "coins_in_profit_pct",
        "transactions/transfers_volume_sum": "transfer_volume",
    }

    async def get_exchange_flows(self, symbol: str) -> dict:
        """
        Netflow на биржи (inflow > outflow → sell pressure).
        Source: CryptoQuant публичный API.
        """
        return {
            "exchange_inflow": float,
            "exchange_outflow": float,
            "netflow": float,        # negative = outflow (bullish)
            "exchange_reserve": float,
        }
```

---

## 4. Аналитические модули

### 4.1 TA Engine v2

#### 4.1.1 Order Flow интеграция (крипто)

```python
class TAEngineV2(TAEngine):
    """Extends TAEngine with order flow analysis."""

    def __init__(self, df: pd.DataFrame, order_flow_data: dict = None):
        super().__init__(df)
        self.of = order_flow_data or {}

    def calculate_cvd_signal(self) -> float:
        """
        CVD divergence: цена растёт, CVD падает → медвежья дивергенция.
        Возвращает [-1, +1].
        """

    def calculate_oi_signal(self) -> float:
        """
        Price up + OI up → устойчивый тренд.
        Price up + OI down → short covering (слабый сигнал).
        Price down + OI up → новые шорты (сильный нисходящий тренд).
        """

    def calculate_liquidation_signal(self) -> float:
        """
        Большие ликвидации long → потенциальное дно (контрарианный).
        Большие ликвидации short → потенциальная вершина.
        """

    def calculate_funding_signal(self) -> float:
        """
        Улучшенная версия: funding rate + его тренд за 7 дней.
        Persistently positive funding → perp premium → medvezhiy signal.
        """
```

#### 4.1.2 Новые индикаторы

```python
# Добавить в calculate_all_indicators():

def calculate_divergence(self) -> dict:
    """
    RSI divergence: bullish (цена new low, RSI higher low)
                    bearish (цена new high, RSI lower high)
    MACD divergence: аналогично.
    """
    return {
        "rsi_bullish_div": bool,
        "rsi_bearish_div": bool,
        "macd_bullish_div": bool,
        "macd_bearish_div": bool,
    }

def calculate_volume_indicators(self) -> dict:
    """
    OBV (On Balance Volume)
    MFI (Money Flow Index, 14)
    VWAP (Volume Weighted Average Price)
    """

def calculate_ichimoku(self) -> dict:
    """
    Tenkan-sen (9), Kijun-sen (26), Senkou Span A/B, Chikou Span.
    Популярен в форекс и крипто.
    """
    return {
        "tenkan": float,
        "kijun": float,
        "senkou_a": float,
        "senkou_b": float,
        "chikou": float,
        "cloud_bullish": bool,   # цена выше облака
        "tk_cross_bullish": bool, # Tenkan пересёк Kijun снизу вверх
    }

def calculate_pivot_points(self) -> dict:
    """
    Classic Pivot Points (на основе предыдущего дня).
    PP, R1, R2, R3, S1, S2, S3.
    Используются профессионалами на всех рынках.
    """

def calculate_market_structure(self) -> dict:
    """
    Структура рынка: Higher Highs / Higher Lows (uptrend)
                     Lower Highs / Lower Lows (downtrend)
                     Break of Structure (BOS) detection.
    """
    return {
        "trend_structure": str,  # "uptrend", "downtrend", "ranging"
        "last_bos": str,         # "bullish", "bearish", None
        "bos_level": float,
        "last_choch": str,       # Change of Character
    }
```

#### 4.1.3 Улучшение существующих методов

```python
def detect_candle_patterns(self) -> dict:
    """
    ОБЯЗАТЕЛЬНО добавить контекст к паттернам:
    - тренд перед паттерном (bullish/bearish/neutral)
    - объём паттерна vs средний объём
    - расстояние до ближайшего S/R
    Без контекста паттерны бесполезны.
    """

def detect_order_blocks(self) -> list:
    """
    Добавить:
    - "strength" (насколько сильное движение после OB)
    - "tested" (был ли OB уже протестирован)
    - "fresh" (нетронутый OB)
    Торговать только fresh OB.
    """
```

### 4.2 FA Engine v2

#### 4.2.1 Форекс — полный дифференциальный анализ

```python
class ForexFAEngine:
    """
    Полный фундаментальный анализ для форекс:
    анализирует ОБЕ валюты в паре.
    """

    CURRENCY_CENTRAL_BANKS = {
        "USD": "FED", "EUR": "ECB", "GBP": "BOE",
        "JPY": "BOJ", "AUD": "RBA", "CAD": "BOC",
        "CHF": "SNB", "NZD": "RBNZ",
    }

    def analyze(self, symbol: str, macro_data: dict) -> float:
        base_ccy, quote_ccy = self._parse_pair(symbol)  # EUR, USD

        base_score = self._score_currency(base_ccy, macro_data)
        quote_score = self._score_currency(quote_ccy, macro_data)

        # Дифференциал: если EUR сильнее USD → bullish EUR/USD
        raw_score = base_score - quote_score

        # Нормализация + тренд дифференциала
        trend_adjustment = self._differential_trend(symbol, macro_data)

        return max(-100.0, min(100.0, raw_score + trend_adjustment))

    def _score_currency(self, currency: str, macro_data: dict) -> float:
        """
        Для каждой валюты: rate + cpi + gdp + employment + trade_balance.
        Возвращает [-50, +50] — половина диапазона, т.к. это одна сторона пары.
        """

    def _differential_trend(self, symbol: str, macro_data: dict) -> float:
        """
        Тренд дифференциала важнее абсолюта.
        Сближение ставок в пользу base → дополнительный bullish signal.
        """
```

#### 4.2.2 Акции — компанейские метрики

```python
class StockFAEngine:
    """
    Источники: Finnhub /stock/metric (бесплатно),
               Yahoo Finance через yfinance,
               SEC EDGAR (бесплатно).
    """

    async def get_company_metrics(self, symbol: str) -> dict:
        """
        P/E ratio (trailing и forward)
        EPS (TTM и прогноз)
        Revenue growth (YoY)
        Gross/Net margin
        Debt/EBITDA
        Free Cash Flow
        Return on Equity
        """

    async def get_analyst_consensus(self, symbol: str) -> dict:
        """
        Finnhub /stock/recommendation
        Консенсус: Strong Buy / Buy / Hold / Sell / Strong Sell
        + изменение за последние 30 дней (upgrade/downgrade)
        """

    async def get_earnings_surprise(self, symbol: str) -> float:
        """
        Средний EPS-сюрприз за последние 4 квартала.
        Позитивный сюрприз систематически → bullish.
        """

    async def get_insider_activity(self, symbol: str) -> float:
        """
        SEC Form 4 через https://efts.sec.gov/LATEST/search-index?q=
        Соотношение buy/sell инсайдеров за 90 дней.
        Инсайдеры покупают → bullish signal.
        """

    def calculate_stock_fa_score(self, symbol: str) -> float:
        """
        Веса для score:
        - Valuation (P/E vs сектор): 25%
        - Growth momentum (EPS trend): 25%
        - Analyst consensus: 20%
        - Earnings surprise history: 15%
        - Macro backdrop: 15%  ← из текущего FA Engine
        """
```

#### 4.2.3 Крипто — полноценный FA

```python
class CryptoFAEngine:
    """Phase 2 реализация. В v1 всегда возвращала 0."""

    def calculate_crypto_fa_score(self, symbol: str, data: dict) -> float:
        """
        Компоненты:
        1. On-chain: NVT ratio, active addresses trend (25%)
        2. Market structure: dominance, ETF flows (20%)
        3. Funding ecosystem: protocol revenue, TVL (15%)
        4. Cycle analysis: halving proximity, MVRV (25%)
        5. Macro correlation: Risk-on/off environment (15%)
        """

    async def get_mvrv_ratio(self, symbol: str) -> float:
        """
        MVRV = Market Value / Realized Value.
        MVRV > 3.5 → historically overbought.
        MVRV < 1.0 → historically undervalued.
        Source: Glassnode (бесплатный tier).
        """

    async def get_btc_dominance(self) -> float:
        """
        BTC.D растёт → альты слабеют.
        BTC.D падает → alt season.
        Source: CoinGecko бесплатный API.
        """

    async def get_etf_flows(self) -> dict:
        """
        Ежедневные flows в BTC ETF (BlackRock IBIT, Fidelity FBTC и т.д.)
        Source: TheBlock.co данные (публичные).
        Большой inflow → институциональный спрос → bullish.
        """
```

### 4.3 Sentiment Engine v2

```python
class SentimentEngineV2:
    """
    FinBERT-based, multi-source sentiment aggregation.
    """

    SOURCE_WEIGHTS = {
        "news_major": 0.35,      # Reuters, Bloomberg RSS, FT
        "news_crypto": 0.20,     # CoinDesk, Decrypt (для крипто)
        "social_reddit": 0.15,   # r/wallstreetbets, r/CryptoCurrency
        "social_twitter": 0.15,  # X/Twitter (если ключ доступен)
        "fear_greed": 0.10,      # Alternative.me (только крипто)
        "options_flow": 0.05,    # Put/Call Ratio (только акции)
    }

    # Учитывать только источники, доступные для данного инструмента.
    # Нормализовать веса при отсутствии источника.

    def calculate_sentiment_score(
        self,
        news: list,
        social_data: dict,
        fear_greed: dict = None,
        options_pcr: float = None,
        instrument_market: str = "forex",
    ) -> float:
        """Returns float in [-100, +100]."""
```

### 4.4 Regime Detector — НОВЫЙ МОДУЛЬ

Критически важный отсутствующий компонент. Определяет рыночный режим для адаптации весов.

```python
class RegimeDetector:
    """
    Determines current market regime for an instrument.
    Regime affects signal weights and risk parameters.
    """

    REGIMES = {
        "STRONG_TREND_BULL": {"ta_weight": 0.55, "fa_weight": 0.25, "sentiment_weight": 0.15, "geo_weight": 0.05},
        "STRONG_TREND_BEAR": {"ta_weight": 0.55, "fa_weight": 0.25, "sentiment_weight": 0.15, "geo_weight": 0.05},
        "WEAK_TREND_BULL":   {"ta_weight": 0.45, "fa_weight": 0.30, "sentiment_weight": 0.20, "geo_weight": 0.05},
        "WEAK_TREND_BEAR":   {"ta_weight": 0.45, "fa_weight": 0.30, "sentiment_weight": 0.20, "geo_weight": 0.05},
        "RANGING":           {"ta_weight": 0.40, "fa_weight": 0.30, "sentiment_weight": 0.25, "geo_weight": 0.05},
        "HIGH_VOLATILITY":   {"ta_weight": 0.35, "fa_weight": 0.20, "sentiment_weight": 0.15, "geo_weight": 0.30},
        "LOW_VOLATILITY":    {"ta_weight": 0.50, "fa_weight": 0.25, "sentiment_weight": 0.20, "geo_weight": 0.05},
    }

    def detect_regime(self, df: pd.DataFrame, macro_data: dict) -> str:
        """
        Комбинированный детектор:
        1. Trend strength: ADX + MA alignment
        2. Volatility: ATR percentile за 252 дня (1 год)
        3. Macro backdrop: VIX level + trend
        4. Price structure: HH/HL vs LH/LL
        """

    def _detect_trend(self, df: pd.DataFrame) -> str:
        """
        ADX > 25 + все MA в одном направлении → STRONG_TREND
        ADX 15-25 → WEAK_TREND
        ADX < 15 → RANGING
        """

    def _detect_volatility_regime(self, df: pd.DataFrame) -> str:
        """
        ATR percentile за 252 дня:
        > 80th percentile → HIGH_VOLATILITY
        < 20th percentile → LOW_VOLATILITY
        """

    def get_regime_weights(self, regime: str) -> dict:
        """Возвращает веса компонентов для данного режима."""
        return self.REGIMES.get(regime, self.REGIMES["RANGING"])

    def get_atr_multiplier(self, regime: str) -> float:
        """
        SL/TP мультипликаторы адаптируются к режиму:
        HIGH_VOLATILITY → ATR × 2.5 (wider stops)
        LOW_VOLATILITY  → ATR × 1.0 (tighter stops)
        RANGING         → ATR × 1.2 + уровни S/R
        """
        multipliers = {
            "STRONG_TREND_BULL": 1.5,
            "STRONG_TREND_BEAR": 1.5,
            "WEAK_TREND_BULL": 1.3,
            "WEAK_TREND_BEAR": 1.3,
            "RANGING": 1.2,
            "HIGH_VOLATILITY": 2.5,
            "LOW_VOLATILITY": 1.0,
        }
        return multipliers.get(regime, 1.5)
```

**Хранение режима:**

```sql
CREATE TABLE regime_state (
    id SERIAL PRIMARY KEY,
    instrument_id INT REFERENCES instruments(id),
    timeframe VARCHAR(10),
    regime VARCHAR(30) NOT NULL,
    adx NUMERIC(6,2),
    atr_percentile NUMERIC(5,2),
    detected_at TIMESTAMPTZ DEFAULT NOW(),
    valid_until TIMESTAMPTZ,
    UNIQUE (instrument_id, timeframe)
);
```

### 4.5 Geo Engine v2

```python
class GeoEngineV2:
    """
    Рабочая реализация на основе GDELT + Crisis Monitor.
    v1 возвращала 0 — полностью переписать.
    """

    GDELT_API = "https://api.gdeltproject.org/api/v2/doc/doc"

    # Маппинг стран к валютам/активам
    COUNTRY_INSTRUMENT_MAP = {
        "United States": ["USD", "SPY", "QQQ"],
        "European Union": ["EUR"],
        "Germany": ["EUR", "DAX"],
        "United Kingdom": ["GBP", "FTSE"],
        "Japan": ["JPY"],
        "China": ["AUDUSD"],  # Австралия сильно зависит от Китая
        "Russia": ["XAUUSD"],  # Геополитика → золото
        "Middle East": ["WTI", "XAUUSD"],
    }

    async def fetch_gdelt_tone(self, query: str, days: int = 3) -> float:
        """
        GDELT Tone Score: среднее эмоциональное окрашивание новостей.
        Диапазон: обычно [-10, +10].
        """
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": 100,
            "timespan": f"{days}d",
        }

    async def calculate_geopolitical_risk(self) -> dict:
        """
        Для каждого инструмента — агрегированный риск-индекс.
        Учитывает: санкции, конфликты, выборы, центробанковские заседания.
        """

    async def detect_risk_events(self) -> list[str]:
        """
        Ключевые события с высоким geo-импактом:
        - FOMC / ECB / BOJ заседания (из календаря)
        - Геополитические обострения (GDELT spike)
        - Природные катастрофы (для commodities)
        """
```

---

## 5. Сигнальный движок

### 5.1 Composite Score v2 — Regime-Aware

```python
class SignalEngineV2:

    async def generate_signal(self, instrument, timeframe, db):

        # --- Существующая логика (cooldown, price-change) без изменений ---

        # 1. Определить режим
        regime = await self.regime_detector.detect_regime(df, macro_data)
        weights = self.regime_detector.get_regime_weights(regime)

        # 2. Проверить earnings risk (только акции)
        if instrument.market == "stocks":
            earnings_risk = await self.earnings_calendar.get_earnings_risk(instrument.symbol)
            if earnings_risk["days_to_earnings"] <= 2:
                logger.info(f"Skipping {instrument.symbol}: earnings in {earnings_risk['days_to_earnings']}d")
                return None

        # 3. Запустить все движки
        ta_score = await self._run_ta(df, instrument)
        fa_score = await self._run_fa(instrument, macro_data)
        sentiment_score = await self._run_sentiment(news_data, social_data, instrument)
        geo_score = await self._run_geo(instrument)
        correlation_score = await self._run_correlation(instrument, macro_data)
        of_score = await self._run_order_flow(instrument, timeframe)  # NEW

        # 4. Composite с режимными весами
        composite = (
            weights["ta_weight"] * ta_score +
            weights["fa_weight"] * fa_score +
            weights["sentiment_weight"] * sentiment_score +
            weights["geo_weight"] * geo_score
        )

        # 5. Корреляционный модификатор
        composite += correlation_score * 0.05

        # 6. Order Flow модификатор (только крипто, может усиливать/ослаблять)
        if of_score is not None:
            composite *= (1 + of_score * 0.15)

        # 7. Earnings risk дисконт
        if instrument.market == "stocks" and earnings_risk["days_to_earnings"] <= 5:
            composite *= 0.70

        # 8. MTF Filter (без изменений)
        composite = self.mtf_filter.apply(composite, timeframe, higher_tf_signals)

        # 9. Confidence calculation
        confidence = self._calculate_confidence(
            composite, regime, ta_score, fa_score, sentiment_score
        )

        # 10. LLM validation (без изменений, только при score > threshold)

        # 11. Финальное решение
```

### 5.2 Confidence Calculation v2

```python
def _calculate_confidence(
    self,
    composite: float,
    regime: str,
    ta_score: float,
    fa_score: float,
    sentiment_score: float,
    of_score: float = None,
) -> float:
    """
    Confidence = f(agreement between components, signal strength, regime fit).

    Высокий confidence когда:
    - Все компоненты согласны по направлению
    - Сильный composite score
    - Режим соответствует типу сигнала (trending режим для trend-follow)
    - Order flow подтверждает (для крипто)

    Низкий confidence когда:
    - Компоненты расходятся
    - Ranging режим + слабый сигнал
    - Upcoming earnings / event risk
    """

    direction = "bull" if composite > 0 else "bear"

    # Agreement score: сколько компонентов согласны
    components = [ta_score, fa_score, sentiment_score]
    if of_score is not None:
        components.append(of_score * 100)

    agreements = sum(1 for s in components if (s > 0) == (composite > 0))
    agreement_ratio = agreements / len(components)

    # Base confidence from score magnitude
    base = min(abs(composite), 100.0) / 100.0

    # Regime fit
    regime_fit = 1.0
    if direction == "bull" and "BEAR" in regime:
        regime_fit = 0.6
    elif direction == "bear" and "BULL" in regime:
        regime_fit = 0.6
    elif "RANGING" in regime:
        regime_fit = 0.8

    confidence = base * agreement_ratio * regime_fit * 100.0
    return round(min(confidence, 100.0), 1)
```

---

## 6. Risk Management v2

### 6.1 Режимно-адаптивные SL/TP

```python
class RiskManagerV2(RiskManager):

    def calculate_levels(
        self,
        direction: str,
        entry: Decimal,
        atr: Decimal,
        regime: str,
        support_resistance: dict = None,
    ) -> dict:
        """
        ATR мультипликатор зависит от режима (см. RegimeDetector).
        SL дополнительно выравнивается по ближайшей структуре.
        """
        mult = self.regime_detector.get_atr_multiplier(regime)

        sl_distance = atr * Decimal(str(mult))

        # Выровнять SL по ближайшему S/R (если в пределах +20% от ATR-расчётного)
        if support_resistance and regime != "HIGH_VOLATILITY":
            sl_distance = self._align_to_structure(
                direction, entry, sl_distance, support_resistance, atr
            )

        # TP1: RR 1.5:1 (в ranging режиме — консервативнее)
        # TP2: RR 2.5:1
        # TP3: RR 4:1 (только в STRONG_TREND)
        tp1_rr = Decimal("1.5") if regime == "RANGING" else Decimal("2.0")
        tp2_rr = Decimal("2.5") if regime == "RANGING" else Decimal("3.5")

        ...
```

### 6.2 Portfolio-Level Risk — НОВОЕ

```python
class PortfolioRiskManager:
    """
    Контролирует совокупный риск по всем открытым позициям.
    """

    CORRELATED_PAIRS = {
        # Высокая корреляция → не открывать одновременно с полным риском
        frozenset(["EURUSD", "GBPUSD"]): 0.85,
        frozenset(["EURUSD", "AUDUSD"]): 0.75,
        frozenset(["USDJPY", "USDCHF"]): 0.80,
        frozenset(["BTCUSDT", "ETHUSDT"]): 0.90,
        frozenset(["SPY", "QQQ"]): 0.95,
    }

    def get_position_size_adjustment(
        self,
        new_instrument: str,
        open_positions: list[str],
        base_risk_pct: float,
    ) -> float:
        """
        Если уже открыт коррелированный инструмент — снизить риск.
        EURUSD + GBPUSD = фактически 1.85x экспозиция на USD.
        Уменьшить risk% нового сигнала на (correlation * 50%).
        """

    def get_max_open_signals(self, market: str) -> int:
        """
        Forex: максимум 3 сигнала одновременно
        Crypto: максимум 2 сигнала
        Stocks: максимум 5 сигналов (диверсификация по секторам)
        """

    def calculate_portfolio_heat(self, open_signals: list) -> float:
        """
        Суммарный риск по всем открытым позициям в % от депозита.
        Если > 6% → новые сигналы не генерировать.
        """
```

### 6.3 Trailing Stop & Breakeven логика

```python
class TradeLifecycleManager:
    """
    Управление открытой позицией после входа.
    """

    async def check_breakeven(self, signal: Signal, current_price: float) -> Optional[Signal]:
        """
        Перевести SL в безубыток после достижения TP1 (RR 1:1).
        """

    async def check_trailing_stop(
        self,
        signal: Signal,
        current_price: float,
        atr: float,
        regime: str,
    ) -> Optional[Decimal]:
        """
        Trailing stop активируется после RR 1:1.
        Trail distance = 0.5 × ATR в trending режиме, 0.3 × ATR в ranging.
        """

    async def check_partial_close(self, signal: Signal, current_price: float) -> bool:
        """
        Рекомендация закрыть 50% позиции на TP1.
        Остаток вести с trailing stop до TP2.
        """
```

---

## 7. Backtesting & Validation

### 7.1 Backtesting Engine — полная реализация

```python
# requirements.txt:
# vectorbt>=0.26.0

class BacktestEngine:
    """
    Walk-Forward backtesting для валидации весов и параметров.
    """

    def run_walk_forward(
        self,
        instrument: str,
        timeframe: str,
        start_date: str,
        end_date: str,
        in_sample_months: int = 18,
        out_of_sample_months: int = 6,
    ) -> BacktestReport:
        """
        Walk-Forward:
        1. Обучение на IS периоде → оптимизация весов
        2. Тест на OOS периоде → валидация
        3. Шаг вперёд на OOS период
        4. Повторить 5+ раз
        Результат: OOS метрики — честная оценка системы.
        """

    def optimize_weights(
        self,
        instrument: str,
        timeframe: str,
        historical_data: pd.DataFrame,
    ) -> dict:
        """
        Перебор весов компонентов + порогов сигналов.
        Оптимизация по: Sharpe Ratio (не по Win Rate — во избежание overfitting).

        Сетка параметров:
        - ta_weight: [0.3, 0.4, 0.5, 0.6]
        - fa_weight: [0.1, 0.2, 0.3]
        - sentiment_weight: [0.1, 0.15, 0.2]
        - buy_threshold: [20, 25, 30, 35]
        - sell_threshold: [-20, -25, -30, -35]

        ВАЖНО: оптимизировать на IS, финальные веса брать из OOS.
        """

    def run_monte_carlo(
        self,
        trade_results: list[float],
        simulations: int = 10000,
    ) -> dict:
        """
        Monte Carlo analysis:
        - Распределение конечного капитала
        - 95% confidence interval для drawdown
        - Вероятность рuin (drawdown > 30%)
        """

    def calculate_report(self, trades: list[Trade]) -> BacktestReport:
        return BacktestReport(
            total_trades=len(trades),
            win_rate=float,
            profit_factor=float,
            sharpe_ratio=float,
            max_drawdown=float,
            max_drawdown_duration_days=int,
            avg_trade_duration_hours=float,
            expectancy_pips=float,
            calmar_ratio=float,        # Годовая доходность / Max Drawdown
            recovery_factor=float,     # Net Profit / Max Drawdown
            consecutive_losses_max=int,
            monthly_returns=list[float],
        )
```

### 7.2 Модели данных для backtesting

```sql
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
    is_champion BOOLEAN DEFAULT FALSE  -- лучший набор параметров
);

CREATE TABLE backtest_trades (
    id BIGSERIAL PRIMARY KEY,
    backtest_run_id INT REFERENCES backtest_runs(id),
    direction VARCHAR(5),
    entry_price NUMERIC(18,8),
    exit_price NUMERIC(18,8),
    sl NUMERIC(18,8),
    tp NUMERIC(18,8),
    pnl_pips NUMERIC(10,2),
    pnl_pct NUMERIC(8,4),
    entry_time TIMESTAMPTZ,
    exit_time TIMESTAMPTZ,
    exit_reason VARCHAR(20),   -- "TP1", "TP2", "SL", "TRAILING", "TIMEOUT"
    regime VARCHAR(30),
    composite_score NUMERIC(6,2)
);
```

### 7.3 Автоматическая валидация весов

```python
class WeightValidator:
    """
    Периодически (еженедельно) перезапускает walk-forward
    и сравнивает с текущими champion-весами.
    Если новые параметры значительно лучше — предлагает обновление.
    """

    def should_update_weights(
        self,
        current_champion: BacktestRun,
        new_candidate: BacktestRun,
    ) -> bool:
        """
        Обновлять только если:
        - Новый OOS Sharpe >= текущего на 10%+
        - Новый OOS max drawdown не хуже на 5%+
        - Новый OOS profit factor >= 1.3
        - Количество OOS трейдов >= 30 (статистическая значимость)
        """
```

---

## 8. База данных

### 8.1 Новые и изменённые таблицы

```sql
-- ОБНОВИТЬ: instruments — добавить поля
ALTER TABLE instruments ADD COLUMN sector VARCHAR(50);           -- для акций
ALTER TABLE instruments ADD COLUMN base_currency VARCHAR(3);    -- для форекс (EUR в EURUSD)
ALTER TABLE instruments ADD COLUMN quote_currency VARCHAR(3);   -- для форекс (USD)
ALTER TABLE instruments ADD COLUMN central_bank VARCHAR(10);    -- BOJ, ECB и т.д.

-- НОВАЯ: центральные банки
CREATE TABLE central_bank_rates (
    id SERIAL PRIMARY KEY,
    bank VARCHAR(10) NOT NULL,    -- FED, ECB, BOJ, BOE, RBA, BOC, SNB, RBNZ
    rate NUMERIC(6,4) NOT NULL,
    decision_date DATE,
    next_meeting_date DATE,
    bias VARCHAR(20),             -- hawkish, dovish, neutral
    statement_sentiment NUMERIC(4,2),  -- FinBERT score пресс-конференции
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- НОВАЯ: company fundamentals (для акций)
CREATE TABLE company_fundamentals (
    id SERIAL PRIMARY KEY,
    instrument_id INT REFERENCES instruments(id),
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

-- НОВАЯ: on-chain данные (крипто)
CREATE TABLE onchain_data (
    id SERIAL PRIMARY KEY,
    instrument_id INT REFERENCES instruments(id),
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

-- ОБНОВИТЬ: signals — добавить поля
ALTER TABLE signals ADD COLUMN regime VARCHAR(30);
ALTER TABLE signals ADD COLUMN of_score NUMERIC(6,2);          -- order flow score
ALTER TABLE signals ADD COLUMN correlation_score NUMERIC(6,2);
ALTER TABLE signals ADD COLUMN earnings_days_ahead INT;
ALTER TABLE signals ADD COLUMN portfolio_heat NUMERIC(5,2);    -- риск портфеля в момент сигнала
ALTER TABLE signals ADD COLUMN tp3 NUMERIC(18,8);              -- добавить TP3

-- НОВАЯ: portfolio positions (виртуальный портфель)
CREATE TABLE virtual_portfolio (
    id SERIAL PRIMARY KEY,
    signal_id INT REFERENCES signals(id),
    status VARCHAR(20) DEFAULT 'open',  -- open, closed_tp1, closed_tp2, closed_sl, closed_trailing
    entry_filled_at TIMESTAMPTZ,
    partial_close_at TIMESTAMPTZ,       -- закрытие 50% на TP1
    breakeven_moved_at TIMESTAMPTZ,
    trailing_stop NUMERIC(18,8),
    current_pnl_pct NUMERIC(8,4),
    closed_at TIMESTAMPTZ
);

-- НОВАЯ: social sentiment
CREATE TABLE social_sentiment (
    id SERIAL PRIMARY KEY,
    instrument_id INT REFERENCES instruments(id),
    source VARCHAR(30),               -- "reddit", "twitter", "stocktwits"
    score NUMERIC(4,2),               -- [-1, +1]
    post_count INT,
    bullish_ratio NUMERIC(5,2),
    collected_at TIMESTAMPTZ DEFAULT NOW()
);

-- НОВАЯ: backtesting (см. раздел 7)
-- backtest_runs, backtest_trades (см. выше)

-- НОВАЯ: order flow (см. раздел 3.1.3)
-- order_flow_data (см. выше)
```

---

## 9. API и уведомления

### 9.1 REST API v2 (FastAPI)

```
GET  /api/v2/signals                    — список сигналов с фильтрами
GET  /api/v2/signals/{id}               — детальный сигнал + компоненты
GET  /api/v2/instruments                — список инструментов
GET  /api/v2/instruments/{id}/analysis  — текущий анализ по инструменту
GET  /api/v2/instruments/{id}/regime    — текущий режим
GET  /api/v2/portfolio                  — виртуальный портфель
GET  /api/v2/accuracy                   — метрики точности (live)
GET  /api/v2/backtests                  — результаты backtesting
GET  /api/v2/backtests/{id}/trades      — трейды конкретного backtest
GET  /api/v2/macroeconomics             — текущие макро-данные всех ЦБ
GET  /api/v2/health                     — статус всех коллекторов

POST /api/v2/signals/{id}/feedback      — ручная обратная связь оператора
POST /api/v2/backtest/run               — запустить backtest (async task)
POST /api/v2/instruments/{id}/analyze   — принудительный пересчёт
```

### 9.2 WebSocket v2

```
ws://host/ws/signals          — новые сигналы в реальном времени
ws://host/ws/prices/{symbol}  — цена инструмента (форвардинг из Binance WS)
ws://host/ws/portfolio        — обновления позиций
```

### 9.3 Webhook интеграции

```python
class WebhookDispatcher:
    """
    Отправка сигналов во внешние системы.
    """

    async def send_metatrader_webhook(self, signal: Signal) -> bool:
        """
        MT4/MT5 через Expert Advisor на стороне клиента.
        JSON: {action, symbol, direction, entry, sl, tp1, tp2, lot_size}
        """

    async def send_3commas_webhook(self, signal: Signal) -> bool:
        """
        3Commas Bot API для автоматического исполнения крипто.
        Требует: bot_id, bot_secret из настроек.
        """

    async def send_tradingview_alert(self, signal: Signal) -> bool:
        """
        TradingView webhooks для алертов на графике.
        """
```

### 9.4 Telegram v2

```python
# Улучшенный формат уведомлений:
"""
🟢 LONG EURUSD | H4 | {datetime}

📊 Composite: +67.3 | Confidence: 82%
📈 Режим: STRONG_TREND_BULL

🎯 Entry:  1.08450 (Fib 0.618 retracement)
🛑 SL:     1.08120 (-33 pips | ATR×1.5)
🥇 TP1:    1.08890 (+44 pips | RR 1.3:1)
🥈 TP2:    1.09340 (+89 pips | RR 2.7:1)
🥉 TP3:    1.09890 (+144 pips | RR 4.4:1) — trailing

📝 Компоненты:
  TA:  +74 (MACD cross, RSI 58, структура HH/HL)
  FA:  +61 (ставка ФРС vs ЕЦБ: дифференциал сужается в пользу EUR)
  SENT: +52 (FinBERT: позитивный тон по EUR, 15 новостей)
  GEO:  +20 (стабильная обстановка в еврозоне)
  OF:   N/A (форекс)

🔗 MTF: H1 +45 ✅ | D1 +38 ✅ (2/2 согласны)
🤖 LLM: Подтверждает BULLISH (уверенность 78%)
📊 Портфель: тепло 2.1% / 6% лимит
"""
```

---

## 10. Frontend (Dashboard v2)

### 10.1 Технологии

- **Framework:** Next.js 14 + TypeScript
- **Charts:** TradingView Lightweight Charts v4 (бесплатная лицензия)
- **State:** Zustand
- **Styling:** Tailwind CSS
- **Real-time:** native WebSocket hook

### 10.2 Страницы и компоненты

```
/                       — Dashboard: активные сигналы + portfolio heat
/signals                — История сигналов с фильтрами
/signals/{id}           — Детальный разбор: компоненты + график
/instruments/{id}       — Анализ инструмента: все TF + режим + макро
/portfolio              — Виртуальный портфель: P&L, open positions
/backtests              — Результаты backtesting: equity curves, метрики
/macroeconomics         — Дашборд: все ЦБ + ставки + дифференциалы
/accuracy               — Live метрики: win rate по рынкам/таймфреймам
/settings               — API ключи, размер депозита, риск-параметры
```

### 10.3 Ключевые UI компоненты

**RegimeWidget** — показывает текущий режим каждого инструмента с цветовой индикацией.

**PortfolioHeatBar** — визуализация совокупного риска: зелёный (0-3%), жёлтый (3-5%), красный (5%+).

**DifferentialChart** — график процентных дифференциалов ЦБ для форекс.

**BacktestEquityCurve** — интерактивный график с IS/OOS разбивкой и Monte Carlo коридором.

**ComponentBreakdown** — radar chart с вкладом каждого компонента в итоговый скор.

---

## 11. Инфраструктура

### 11.1 Docker Compose v2

```yaml
services:
  postgres:
    image: timescale/timescaledb:latest-pg16
    volumes: [postgres_data:/var/lib/postgresql/data]

  redis:
    image: redis:7-alpine
    # Используется для: Celery broker, TTL-кэш коллекторов, LLM кэш

  app:
    build: .
    depends_on: [postgres, redis]
    environment:
      - WORKERS=4

  celery_worker:
    build: .
    command: celery -A src.scheduler.celery_app worker --loglevel=info
    depends_on: [redis, postgres]

  celery_beat:
    build: .
    command: celery -A src.scheduler.celery_app beat --loglevel=info

  finbert:
    # FinBERT как отдельный микросервис (CPU-интенсивный)
    build: ./services/finbert
    # FastAPI wrapper: POST /score {"text": "..."} → {"score": float}

  prometheus:
    image: prom/prometheus:latest

  grafana:
    image: grafana/grafana:latest

  nginx:
    image: nginx:alpine
    # Reverse proxy: / → Next.js, /api → FastAPI, /ws → FastAPI WS
```

### 11.2 Celery задачи (расписание)

```python
CELERY_BEAT_SCHEDULE = {
    # Цены
    "collect-crypto-prices": {
        "task": "collectors.price.collect_crypto_websocket",
        "schedule": crontab(minute="*/1"),   # каждую минуту (обновление из WS буфера)
    },
    "collect-forex-prices": {
        "task": "collectors.price.collect_forex",
        "schedule": crontab(minute="*/15"),  # каждые 15 минут
    },
    "collect-stock-prices": {
        "task": "collectors.price.collect_stocks",
        "schedule": crontab(minute="*/15"),  # каждые 15 минут
    },

    # Order Flow (крипто)
    "collect-order-flow": {
        "task": "collectors.order_flow.collect",
        "schedule": crontab(minute="*/5"),
    },

    # Макро и ЦБ
    "collect-macro-fred": {
        "task": "collectors.macro.collect_fred",
        "schedule": crontab(hour="0", minute="30"),  # раз в сутки
    },
    "collect-central-bank-rates": {
        "task": "collectors.macro.collect_cb_rates",
        "schedule": crontab(hour="1", minute="0"),   # раз в сутки
    },

    # Новости и сентимент
    "collect-news": {
        "task": "collectors.news.collect_all",
        "schedule": crontab(minute="*/10"),
    },
    "collect-social-sentiment": {
        "task": "collectors.social.collect_all",
        "schedule": crontab(minute="*/30"),
    },

    # Фундаментал акций (еженедельно достаточно)
    "collect-company-fundamentals": {
        "task": "collectors.fundamentals.collect_all",
        "schedule": crontab(day_of_week="1", hour="7", minute="0"),
    },

    # On-chain
    "collect-onchain": {
        "task": "collectors.onchain.collect",
        "schedule": crontab(hour="*/6", minute="0"),
    },

    # Сигналы
    "generate-signals-m15": {
        "task": "signals.generate_for_timeframe",
        "schedule": crontab(minute="*/15"),
        "kwargs": {"timeframe": "M15"},
    },
    "generate-signals-h1": {
        "task": "signals.generate_for_timeframe",
        "schedule": crontab(minute="0"),
        "kwargs": {"timeframe": "H1"},
    },
    "generate-signals-h4": {
        "task": "signals.generate_for_timeframe",
        "schedule": crontab(hour="*/4", minute="5"),
        "kwargs": {"timeframe": "H4"},
    },
    "generate-signals-d1": {
        "task": "signals.generate_for_timeframe",
        "schedule": crontab(hour="6", minute="0"),
        "kwargs": {"timeframe": "D1"},
    },

    # Детектор режима
    "detect-regimes": {
        "task": "analysis.regime.detect_all",
        "schedule": crontab(hour="*/1", minute="10"),
    },

    # Backtesting (еженедельно)
    "weekly-backtest": {
        "task": "backtesting.run_weekly_validation",
        "schedule": crontab(day_of_week="0", hour="2", minute="0"),
    },
}
```

---

## 12. Нефункциональные требования

| Требование | v1 | v2 Target |
|---|---|---|
| Latency генерации сигнала | ≤5 сек | ≤10 сек (FinBERT добавляет ~3 сек) |
| Uptime | ≥99% | ≥99.5% |
| Coverage тестов analysis/signals | 80% | 90% |
| Backtesting OOS Sharpe | нет | ≥0.8 на 3+ инструментах |
| Profit Factor (OOS) | нет | ≥1.3 |
| Max Drawdown (OOS) | нет | ≤15% |
| Fake signal rate | не измеряется | ≤30% (precision ≥70%) |
| Data freshness форекс | 15 мин задержка | ≤15 мин (Alpha Vantage) |
| Data freshness крипто | WS реал-тайм | WS реал-тайм (без изменений) |
| FinBERT inference time | N/A | ≤1 сек на текст (CPU) |

---

## 13. План реализации (Phases)

### Phase 2.1 — Фундамент (6-8 недель)

**Приоритет: исправить самые критичные дыры**

- [ ] Мигрировать на PostgreSQL + TimescaleDB
- [ ] Интегрировать Redis как кэш (заменить in-memory dict)
- [ ] Перейти на Celery вместо APScheduler
- [ ] Реализовать `InterestRateDifferential` — ECB, BOJ, BOE коллекторы
- [ ] Реализовать `ForexFAEngine` с дифференциальным анализом
- [ ] Реализовать `StockFAEngine` с company metrics (Finnhub + yfinance)
- [ ] Реализовать `CryptoFAEngine` (Fear&Greed, dominance, MVRV)
- [ ] Реализовать `RegimeDetector`
- [ ] Реализовать `BacktestEngine` с walk-forward
- [ ] Обновить базовые веса на основе первого backtesting
- [ ] Покрыть тестами все новые модули (≥90% coverage)

### Phase 2.2 — Сентимент и Order Flow (4-5 недель)

- [ ] Развернуть FinBERT как микросервис
- [ ] Реализовать `SentimentEngineV2` с FinBERT
- [ ] Реализовать `SocialSentimentCollector` (Reddit, Stocktwits)
- [ ] Реализовать `OrderFlowCollector` (CVD, OI, funding, liquidations)
- [ ] Реализовать `TAEngineV2` с OF-интеграцией
- [ ] Реализовать `GeoEngineV2` с GDELT (рабочая версия)
- [ ] Реализовать `EarningsCalendarCollector`

### Phase 2.3 — Risk & Portfolio (3-4 недели)

- [ ] Реализовать `RiskManagerV2` с режимной адаптацией
- [ ] Реализовать `PortfolioRiskManager` с корреляцией
- [ ] Реализовать `TradeLifecycleManager` (trailing, breakeven)
- [ ] Реализовать virtual portfolio tracking
- [ ] Monte Carlo для оценки рисков
- [ ] Webhook интеграции (MT5, 3Commas)

### Phase 2.4 — Dashboard & API (3-4 недели)

- [ ] Мигрировать Frontend на Next.js + TypeScript
- [ ] Реализовать все страницы (Backtests, Portfolio, Macroeconomics)
- [ ] WebSocket для real-time обновлений
- [ ] Prometheus + Grafana мониторинг
- [ ] Telegram v2 с улучшенным форматом

### Phase 2.5 — Hardening (2-3 недели)

- [ ] Нагрузочное тестирование (все коллекторы одновременно)
- [ ] Обработка rate limits всех API с exponential backoff
- [ ] Автоматическое обнаружение и алерт при деградации данных
- [ ] Финальный walk-forward по всем инструментам
- [ ] Документация API (auto-generated OpenAPI)

---

## Приложение A: Матрица API ключей

| Сервис | Tier | Лимит | Обязателен | Применение |
|---|---|---|---|---|
| Alpha Vantage | Free | 500 req/day | Да | Forex M15/H1 |
| Polygon.io | Free | 5 req/min | Рекомендуется | US Stocks |
| FRED | Free | Unlimited | Да | Макро США |
| Finnhub | Free | 60 req/min | Да | Новости, earnings, FA |
| Reddit (PRAW) | Free | 60 req/min | Нет | Social sentiment |
| Glassnode | Free | Daily only | Нет | On-chain крипто |
| Anthropic Claude | Pay-per-use | - | Нет | LLM validation |
| NewsAPI | Free | 100 req/day | Нет | Новости (дополнение) |
| Telegram Bot | Free | - | Да | Алерты |

ECB, BOJ, BOE, CoinGecko, Alternative.me, Stocktwits, GDELT — **без ключа, полностью бесплатные**.

---

## Приложение B: Ограничения и риски

| Риск | Вероятность | Митигация |
|---|---|---|
| Деградация yfinance API | Высокая | Polygon.io как первичный для акций |
| FinBERT CPU latency | Средняя | Батч-обработка + кэш результатов |
| GDELT нестабильность | Высокая | Graceful degradation, geo=0 как fallback |
| Overfitting при оптимизации весов | Высокая | Walk-Forward OOS validation, min 30 трейдов |
| Высокая корреляция крипто-сигналов | Средняя | PortfolioRiskManager + лимит 2 крипто-позиции |
| Reddit / X API изменения | Средняя | Stocktwits и RSS как fallback |

---

*Документ является основой для реализации v2. Все изменения относительно этого ТЗ должны фиксироваться в CHANGELOG раздела.*
