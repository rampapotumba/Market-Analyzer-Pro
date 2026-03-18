# Market Analyzer Pro — Техническое задание v3.0

**Дата:** 2026-03-18
**Статус:** Draft
**Основание:** Аудит v2 — системные проблемы в TA/FA движках, нерабочий LLM-блок, некорректный MTF-фильтр, отсутствие инструментальной специфики в sentiment, неадаптивный risk manager.

---

## Содержание

1. [Цель и принципы v3](#1-цель-и-принципы-v3)
2. [Диагностика v2: что сломано и почему](#2-диагностика-v2-что-сломано-и-почему)
3. [Изменение 1: TA Engine — контекстные сигналы и S/R](#3-изменение-1-ta-engine--контекстные-сигналы-и-sr)
4. [Изменение 2: FA Engine — инструментальная специфика](#4-изменение-2-fa-engine--инструментальная-специфика)
5. [Изменение 3: Sentiment — фильтрация по инструменту](#5-изменение-3-sentiment--фильтрация-по-инструменту)
6. [Изменение 4: Composite Score — нормализация и LLM-порог](#6-изменение-4-composite-score--нормализация-и-llm-порог)
7. [Изменение 5: MTF Filter — корректный порог направления](#7-изменение-5-mtf-filter--корректный-порог-направления)
8. [Изменение 6: Risk Manager — режимная адаптация](#8-изменение-6-risk-manager--режимная-адаптация)
9. [Изменение 7: Signal Cooldown — снятие при противоположном направлении](#9-изменение-7-signal-cooldown--снятие-при-противоположном-направлении)
10. [Изменение 8: Position Size — убрать хардкод](#10-изменение-8-position-size--убрать-хардкод)
11. [Изменение 9: Таймфрейм-адаптивные периоды индикаторов](#11-изменение-9-таймфрейм-адаптивные-периоды-индикаторов)
12. [База данных — схема изменений](#12-база-данных--схема-изменений)
13. [Тесты и валидация](#13-тесты-и-валидация)
14. [План реализации (Phases)](#14-план-реализации-phases)

---

## 1. Цель и принципы v3

### 1.1 Цель

Устранить системные аналитические ошибки v2, выявленные при аудите. Не добавлять новые компоненты — исправить и откалибровать существующие, чтобы каждый компонент давал статистически осмысленный вклад в итоговый сигнал.

### 1.2 Принципы v3

1. **Instrument-awareness**: каждый аналитический модуль должен знать, для какого именно инструмента он работает, и использовать специфичные для него данные.
2. **Calibrated ranges**: все промежуточные скоры (TA, FA, Sentiment, Geo) должны реально использовать диапазон [-100, +100], а не концентрироваться в ±10-20.
3. **No silent dead code**: компоненты, которые не могут работать (LLM при пороге выше реального максимума, MTF filter при пороге выше реального score), должны быть исправлены, а не сохранены как декорация.
4. **Backward compatibility**: все изменения — in-place замены без изменения внешнего API (`/api/v2/signals`, модели БД `signals`, форматы Telegram).

### 1.3 Целевые рынки (без изменений относительно v2)

Forex Major/Minor, US/EU Stocks, Crypto Major, Commodities.

### 1.4 Ограничения v3

- Минимальный рабочий таймфрейм — **M15** (не M1, не M5 для production-сигналов)
- Исполнение сигналов — ручное + webhook (без изменений)
- Без платных источников данных

---

## 2. Диагностика v2: что сломано и почему

Этот раздел описывает root causes проблем, зафиксированных при code review.

### 2.1 Реальный диапазон composite score: ≈ ±15-25, не ±100

**Причина:** Каждый суб-скор (TA, FA, Sent, Geo) теоретически в [-100, +100], но:
- **TA**: max реальный ≈ ±35 (все индикаторы никогда не согласны на 100% с strength=1.0)
- **FA**: max реальный ≈ ±10 (дельты ставок 0.25bp × 10 = ±2.5 за один шаг)
- **Sentiment**: max реальный ≈ ±25 (FinBERT на смешанных новостях)
- **Geo**: max реальный ≈ ±15 (GDELT нормализованные метрики)

Итоговый composite при весах (0.45/0.25/0.20/0.10):
```
max_composite ≈ 0.45×35 + 0.25×10 + 0.20×25 + 0.10×15 = 15.75 + 2.5 + 5.0 + 1.5 ≈ 24.75
```

Следствия:
- LLM_SCORE_THRESHOLD = 25.0 → **LLM никогда не вызывается**
- MTF `_get_direction_from_score` порог ±30 → **MTF никогда не видит направление**
- `confidence = score_abs × 1.2` → **максимальная confidence ≈ 30%** (исправлено в предыдущей итерации)

### 2.2 FA Engine: данные не релевантны большинству инструментов

- Использует только US-индикаторы (FEDFUNDS, CPIAUCSL, UNRATE, GDPC1)
- Для EUR/USD, GBP/USD — нет ECB/BOE ставок, только хардкодный коэффициент
- GDPC1 (ВВП) — квартальный. Скор на M15 одинаков 90 дней подряд
- Реальная амплитуда FA score ≈ ±5-10 → вклад в composite ≈ ±1.25-2.5 (шум)

### 2.3 Sentiment: не фильтруется по инструменту

`get_news_events(db, limit=30)` возвращает 30 последних новостей из любых источников.
Новость про инфляцию в Японии влияет на sentiment сигнала BTC/USDT.

### 2.4 TA Engine: ряд специфических ошибок

- **RSI как standalone**: oversold RSI в даунтренде = ложный buy signal
- **S/R = min/max 50 свечей**: не отражает реальные уровни поддержки/сопротивления
- **ADX fallback без TA-Lib**: возвращает константу 25 для всех значений → ADX signal всегда 0
- **Индикаторы не адаптированы к таймфрейму**: EMA(200) на M15 ≠ EMA(200) на D1 по смыслу

### 2.5 MTF Filter: порог направления сломан

```python
def _get_direction_from_score(score: float) -> int:
    if score >= 30:   # НИКОГДА не достигается при max composite ≈ 25
        return 1
    if score <= -30:  # НИКОГДА не достигается
        return -1
    return 0          # ВСЕГДА
```

MTF filter всегда возвращает multiplier = "neutral" (1.0). Фильтр не работает.

### 2.6 Risk Manager: не адаптивен

- SL/TP множители — фиксированные из config, не меняются по режиму
- Position size хардкодит $10,000 (simulator использует $1,000)
- Cooldown не снимается при смене направления сигнала

---

## 3. Изменение 1: TA Engine — контекстные сигналы и S/R

### 3.1 RSI: контекстный сигнал (режимная логика)

**Проблема:** RSI < 30 = bullish в любом контексте. В даунтренде это ловушка.

**Решение:** RSI интерпретируется в контексте ADX (тренд/флэт):

```
Если ADX >= 25 (тренд):
    RSI в диапазоне 45-65 при LONG — откат в тренде (точка входа)
    RSI в диапазоне 35-55 при SHORT — откат в тренде (точка входа)
    RSI < 30 при LONG-тренде → strength × 0.5 (фильтруем классические "перепроданность" в тренде вниз)
    RSI > 70 при SHORT-тренде → strength × 0.5

Если ADX < 25 (флэт/range):
    RSI < 30 → bullish signal (классическая логика oversold/overbought применима)
    RSI > 70 → bearish signal
```

**Реализация:** В `generate_ta_signals()` метод `_rsi_signal(adx_value)`:

```python
def _rsi_signal(self, rsi: float, adx: float) -> dict:
    """RSI signal with ADX trend context."""
    trending = adx is not None and adx >= 25

    if not trending:
        # Range mode: classic oversold/overbought
        if rsi < 30:
            return {"signal": 1, "strength": (30 - rsi) / 30}
        elif rsi > 70:
            return {"signal": -1, "strength": (rsi - 70) / 30}
        else:
            return {"signal": 0, "strength": 0.0}
    else:
        # Trend mode: RSI as pullback indicator
        # Bullish: RSI pulled back to 40-50 zone (not oversold trap)
        if 40 <= rsi <= 55:
            return {"signal": 1, "strength": (55 - rsi) / 15}
        elif 45 <= rsi <= 60:
            return {"signal": -1, "strength": (rsi - 45) / 15}
        # Extreme zones get reduced weight in trend
        elif rsi < 30:
            return {"signal": 1, "strength": (30 - rsi) / 30 * 0.4}
        elif rsi > 70:
            return {"signal": -1, "strength": (rsi - 70) / 30 * 0.4}
        else:
            return {"signal": 0, "strength": 0.0}
```

### 3.2 Support/Resistance: кластеры вместо экстремумов

**Проблема:** `support = recent["low"].min()` — один экстремум за 50 свечей.

**Решение:** Считать S/R как зоны с наибольшим количеством касаний (price clusters):

```python
def _find_support_resistance(self) -> dict[str, float]:
    """
    Find S/R levels by price clustering (significant touch count).

    Algorithm:
    1. Collect all pivot highs and pivot lows (local extremes with N candles on each side)
    2. Group by price proximity (tolerance = ATR × 0.3)
    3. Return levels with most touches as S/R
    """
    if len(self.df) < 20:
        return {"support": 0.0, "resistance": 0.0}

    atr = self._calc_atr(14)
    atr_val = float(atr[-1]) if atr is not None and len(atr) > 0 and not np.isnan(atr[-1]) else 0.0
    tolerance = atr_val * 0.3 if atr_val > 0 else 0.001

    window = 3  # bars on each side to be a pivot
    highs = self.df["high"].values
    lows = self.df["low"].values
    close = float(self.df["close"].iloc[-1])

    pivots_high = []
    pivots_low = []

    for i in range(window, len(self.df) - window):
        if all(highs[i] >= highs[i-j] for j in range(1, window+1)) and \
           all(highs[i] >= highs[i+j] for j in range(1, window+1)):
            pivots_high.append(highs[i])
        if all(lows[i] <= lows[i-j] for j in range(1, window+1)) and \
           all(lows[i] <= lows[i+j] for j in range(1, window+1)):
            pivots_low.append(lows[i])

    def cluster_levels(levels: list[float], tol: float) -> list[tuple[float, int]]:
        """Group nearby levels, return (avg_price, touch_count) sorted by touches."""
        if not levels:
            return []
        clusters = []
        used = [False] * len(levels)
        for i, lvl in enumerate(levels):
            if used[i]:
                continue
            group = [lvl]
            for j, other in enumerate(levels):
                if not used[j] and i != j and abs(lvl - other) <= tol:
                    group.append(other)
                    used[j] = True
            clusters.append((sum(group) / len(group), len(group)))
            used[i] = True
        return sorted(clusters, key=lambda x: x[1], reverse=True)

    resistance_clusters = cluster_levels(
        [p for p in pivots_high if p > close], tolerance
    )
    support_clusters = cluster_levels(
        [p for p in pivots_low if p < close], tolerance
    )

    resistance = resistance_clusters[0][0] if resistance_clusters else float(self.df["high"].tail(50).max())
    support = support_clusters[0][0] if support_clusters else float(self.df["low"].tail(50).min())

    return {"support": support, "resistance": resistance}
```

Также добавить в `indicators`:
```python
indicators["nearest_resistance_touches"] = resistance_clusters[0][1] if resistance_clusters else 0
indicators["nearest_support_touches"] = support_clusters[0][1] if support_clusters else 0
```

S/R signal strength усиливается при количестве касаний ≥ 3:
```python
touch_boost = 1.0 + min(0.5, (touches - 1) * 0.15)  # max 1.5× при 4+ касаниях
```

### 3.3 ADX fallback: нейтральный но не сломанный

**Проблема:** Fallback ADX = константа 25, plus_di = minus_di = 25 → signal = 0 всегда.

**Решение:** Реализовать корректный fallback через price-based trend proxy:

```python
def _calc_adx(self, period: int = 14):
    if TALIB_AVAILABLE:
        adx = talib.ADX(...)
        plus_di = talib.PLUS_DI(...)
        minus_di = talib.MINUS_DI(...)
        return adx, plus_di, minus_di

    # Fallback: price-based trend strength
    # ADX proxy: normalized slope of linear regression
    close = pd.Series(self._close)
    high = pd.Series(self._high)
    low = pd.Series(self._low)

    # True Range for ATR
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()

    # Directional Movement
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    smoothed_plus_dm = plus_dm.ewm(span=period, adjust=False).mean()
    smoothed_minus_dm = minus_dm.ewm(span=period, adjust=False).mean()

    plus_di = 100 * smoothed_plus_dm / atr.replace(0, np.nan)
    minus_di = 100 * smoothed_minus_dm / atr.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(span=period, adjust=False).mean()

    return adx.fillna(25.0).values, plus_di.fillna(25.0).values, minus_di.fillna(25.0).values
```

### 3.4 Volume signal: контекст направления через MA, не MACD

**Проблема:** Volume piggybacks на MACD direction. MACD может быть bullish, а цена падает.

**Решение:** Volume направление = направление цены относительно SMA20:

```python
curr_close = ind["current_price"]
sma20 = ind.get("sma20")
price_direction = 0
if sma20:
    price_direction = 1 if curr_close > sma20 else -1

if vol_ratio > 1.5:
    signals["volume"] = {"signal": price_direction, "strength": min((vol_ratio - 1) / 2, 1.0)}
```

---

## 4. Изменение 2: FA Engine — инструментальная специфика

### 4.1 Стратегия: маршрутизация по инструменту

`SignalEngine.generate_signal()` уже корректно вызывает `CryptoFAEngine` для крипто.
Для forex и stocks нужно заменить `FAEngine` на `ForexFAEngine` / `StockFAEngine` соответственно:

```python
# В signal_engine.py — блок FA (шаг 4)
if instrument.market == "crypto":
    engine = CryptoFAEngine(db)
    result = await engine.analyze(instrument.id, instrument.symbol)
    fa_score = result["score"]
elif instrument.market == "forex":
    engine = ForexFAEngine(db)  # уже существует в v2
    result = await engine.analyze(instrument.symbol)
    fa_score = result.get("score", 0.0)
elif instrument.market == "stocks":
    engine = StockFAEngine(db)  # уже существует в v2
    result = await engine.analyze(instrument.symbol)
    fa_score = result.get("score", 0.0)
else:
    # Commodities: используем FAEngine v1 как fallback
    engine = FAEngine(instrument, macro_records, news_records)
    fa_score = engine.calculate_fa_score()
```

**Важно:** `ForexFAEngine` и `StockFAEngine` уже реализованы в v2 (согласно TASKS.md — отмечены [x]). Нужно только подключить их в signal_engine.py вместо legacy FAEngine.

### 4.2 ForexFAEngine: верификация и дополнение

Проверить, что `ForexFAEngine` использует:
- Данные `central_bank_rates` таблицы (ECB, BOJ, BOE, etc.)
- `InterestRateDifferential.calculate_differential(symbol)` для каждой пары
- Нормализованный выход в диапазон [-100, +100] (а не ±5-10)

**Если диапазон FA score для форекс всё ещё ±10**, применить scale-up:
```python
# В ForexFAEngine.analyze()
raw_score = self._calculate_raw_score()
# Amplify to use full range: ±10 raw → ±60 effective
fa_score = max(-100.0, min(100.0, raw_score * 6.0))
```

Коэффициент амплификации выбирается так, чтобы сильный дифференциал ставок (>200bp) давал score ≈ 70-80.

### 4.3 Commodities FA: привязка к специфичным индикаторам

Для `XAUUSD`, `XAGUSD`, `WTI`, `Brent` добавить отдельную ветку в `FAEngine`:

```python
COMMODITY_INDICATORS = {
    "XAUUSD": ["FEDFUNDS", "CPIAUCSL", "DXY_PROXY"],   # Золото: ставки + инфляция + доллар
    "XAGUSD": ["FEDFUNDS", "CPIAUCSL"],
    "WTI":    ["UNRATE", "GDPC1"],                       # Нефть: экономическая активность
    "BRENT":  ["UNRATE", "GDPC1"],
}

def _analyze_commodity_fundamentals(self) -> float:
    symbol = self.instrument.symbol
    indicators = COMMODITY_INDICATORS.get(symbol, ["FEDFUNDS", "CPIAUCSL"])
    ...
```

---

## 5. Изменение 3: Sentiment — фильтрация по инструменту

### 5.1 Проблема

```python
news_records = await get_news_events(db, limit=30)
sent_engine = SentimentEngineV2(news_events=news_records)
```

Один и тот же sentiment для всех инструментов.

### 5.2 Решение: инструментальная фильтрация новостей

**5.2.1** Добавить в `crud.py` функцию `get_news_events_for_instrument`:

```python
async def get_news_events_for_instrument(
    session: AsyncSession,
    symbol: str,
    market: str,
    limit: int = 30,
    hours_back: int = 48,
) -> list[NewsEvent]:
    """
    Fetch news relevant to a specific instrument.

    Relevance criteria (in order of priority):
    1. symbol match: news.symbol == instrument.symbol (exact match)
    2. currency match: for forex, news mentioning base or quote currency
    3. market match: news.category == market (fallback)
    4. global macro: if <5 results found, add general macro news
    """
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours_back)

    # Build symbol-specific keywords
    keywords = _get_instrument_keywords(symbol, market)  # see below

    stmt = (
        select(NewsEvent)
        .where(
            NewsEvent.published_at >= cutoff,
            or_(
                NewsEvent.symbol == symbol,
                *[func.lower(NewsEvent.headline).contains(kw.lower()) for kw in keywords]
            )
        )
        .order_by(NewsEvent.published_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()

    # If instrument-specific news < 5, supplement with general market news
    if len(rows) < 5:
        general_stmt = (
            select(NewsEvent)
            .where(
                NewsEvent.published_at >= cutoff,
                NewsEvent.category.in_(["macro", "economy", market])
            )
            .order_by(NewsEvent.published_at.desc())
            .limit(limit - len(rows))
        )
        gen_result = await session.execute(general_stmt)
        rows = list(rows) + list(gen_result.scalars().all())

    return rows
```

**5.2.2** Функция маппинга инструмента на ключевые слова:

```python
def _get_instrument_keywords(symbol: str, market: str) -> list[str]:
    """Map instrument to relevant news keywords."""
    KEYWORD_MAP = {
        # Forex: base + quote currency keywords
        "EURUSD": ["EUR", "euro", "ECB", "eurozone", "USD", "dollar", "Fed"],
        "GBPUSD": ["GBP", "pound", "sterling", "BOE", "UK", "USD", "dollar", "Fed"],
        "USDJPY": ["JPY", "yen", "BOJ", "Japan", "USD", "dollar", "Fed"],
        "USDCHF": ["CHF", "franc", "SNB", "Switzerland", "USD"],
        "AUDUSD": ["AUD", "aussie", "RBA", "Australia", "USD"],
        "NZDUSD": ["NZD", "kiwi", "RBNZ", "New Zealand", "USD"],
        "USDCAD": ["CAD", "loonie", "BOC", "Canada", "oil", "USD"],
        # Crypto
        "BTCUSDT": ["bitcoin", "BTC", "crypto", "cryptocurrency", "halving"],
        "ETHUSDT": ["ethereum", "ETH", "crypto", "DeFi", "NFT"],
        "SOLUSDT": ["solana", "SOL", "crypto"],
        # Commodities
        "XAUUSD":  ["gold", "XAU", "precious metals", "inflation", "Fed"],
        "XAGUSD":  ["silver", "XAG", "precious metals"],
        "WTI":     ["oil", "crude", "OPEC", "petroleum", "energy"],
        "BRENT":   ["oil", "crude", "OPEC", "Brent", "energy"],
    }

    # For stocks: use company name and ticker
    if market == "stocks":
        ticker = symbol.split(".")[0]  # AAPL, MSFT etc.
        return [ticker]

    return KEYWORD_MAP.get(symbol, [symbol[:3], symbol[3:6]])  # forex fallback: base+quote
```

**5.2.3** Обновить `signal_engine.py`:

```python
# Было:
news_records = await get_news_events(db, limit=30)

# Стало:
news_records = await get_news_events_for_instrument(
    db,
    symbol=instrument.symbol,
    market=instrument.market,
    limit=30,
    hours_back=48,
)
```

---

## 6. Изменение 4: Composite Score — нормализация и LLM-порог

### 6.1 LLM_SCORE_THRESHOLD: снизить до реального диапазона

**Проблема:** `LLM_SCORE_THRESHOLD = 25.0` при max composite ≈ 24.75 → LLM никогда не вызывается.

**Решение:**

```python
# В signal_engine.py
LLM_SCORE_THRESHOLD = 10.0  # Вызываем LLM когда composite > 40% от реального максимума
```

Значение 10.0 соответствует примерно 40% от реального максимума (25). Это означает LLM будет вызываться для всех BUY/SELL сигналов (порог 7.0) кроме самых слабых.

**Дополнительно:** Добавить Telegram-уведомление если LLM противоречит сигналу (llm_score * composite_score < 0), с указанием на повышенную неопределённость.

### 6.2 Нормализация sub-scores: scale-up FA и Geo

Если после перехода на ForexFAEngine/StockFAEngine реальный FA score всё ещё ниже ±30 в экстремальных ситуациях, применить нормализацию внутри `generate_signal()`:

```python
# Нормализация после получения каждого скора:
def _normalize_score(score: float, expected_max: float, target_max: float = 60.0) -> float:
    """Scale score from [−expected_max, +expected_max] to [−target_max, +target_max]."""
    if expected_max <= 0:
        return score
    return max(-100.0, min(100.0, score / expected_max * target_max))
```

Применять только если эмпирические наблюдения за 2+ неделями работы покажут что sub-скор не выходит за ±30. Это дополнительный шаг — **не обязательный в первой итерации v3**.

---

## 7. Изменение 5: MTF Filter — корректный порог направления

### 7.1 Проблема

```python
def _get_direction_from_score(score: float) -> int:
    if score >= 30:   # Недостижимый порог при реальном max ≈ 25
        return 1
    if score <= -30:  # Недостижимый порог
        return -1
    return 0          # Всегда возвращается → MTF multiplier всегда "neutral"
```

### 7.2 Решение: порог через signal thresholds

Направление в MTF определяется теми же порогами, что и генерация сигналов:

```python
# В mtf_filter.py

def _get_direction_from_score(score: float) -> int:
    """
    Determine directional bias from composite score.
    Uses the same thresholds as signal generation (BUY_THRESHOLD = 7.0).
    Previously used ±30 which was above the real score range — now fixed.
    """
    from src.config import settings
    if score >= settings.BUY_THRESHOLD:      # ≥ 7.0
        return 1
    if score <= settings.SELL_THRESHOLD:     # ≤ -7.0
        return -1
    return 0
```

**Важно:** импорт `settings` внутри функции (чтобы избежать circular imports при инициализации модуля).

**Альтернатива** (избегает import): передавать пороги как параметры в `MTFFilter.__init__`:

```python
class MTFFilter:
    def __init__(self, buy_threshold: float = 7.0, sell_threshold: float = -7.0):
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold

    def _get_direction_from_score(self, score: float) -> int:
        if score >= self.buy_threshold:
            return 1
        if score <= self.sell_threshold:
            return -1
        return 0
```

Инициализация в `SignalEngine`:
```python
self.mtf_filter = MTFFilter(
    buy_threshold=settings.BUY_THRESHOLD,
    sell_threshold=settings.SELL_THRESHOLD,
)
```

---

## 8. Изменение 6: Risk Manager — режимная адаптация

### 8.1 Проблема

`RiskManager` использует фиксированные множители из config.
SPEC_V2.md описывает адаптацию по режиму, но код её не реализует.

### 8.2 Режим-адаптивные SL/TP

Добавить в `RiskManager` метод `calculate_levels_for_regime`:

```python
# Таблица множителей по режимам (из SPEC_V2.md)
REGIME_SL_MULTIPLIERS = {
    "STRONG_TREND":   Decimal("1.5"),
    "WEAK_TREND":     Decimal("1.3"),
    "RANGING":        Decimal("1.2"),
    "HIGH_VOLATILITY": Decimal("2.5"),
    "LOW_VOLATILITY": Decimal("1.0"),
    None:             Decimal("1.5"),   # fallback
}

REGIME_TP1_RR = {
    "STRONG_TREND":   Decimal("2.0"),
    "WEAK_TREND":     Decimal("2.0"),
    "RANGING":        Decimal("1.5"),   # Ближе — в флэте цель не так далеко
    "HIGH_VOLATILITY": Decimal("2.0"),
    "LOW_VOLATILITY": Decimal("1.8"),
    None:             Decimal("2.0"),
}

REGIME_TP2_RR = {
    "STRONG_TREND":   Decimal("3.5"),
    "WEAK_TREND":     Decimal("3.0"),
    "RANGING":        Decimal("2.5"),
    "HIGH_VOLATILITY": Decimal("3.0"),
    "LOW_VOLATILITY": Decimal("2.5"),
    None:             Decimal("3.5"),
}

def calculate_levels_for_regime(
    self,
    entry: Decimal,
    atr: Decimal,
    direction: str,
    regime: Optional[str] = None,
) -> dict[str, Decimal]:
    """
    Calculate SL/TP levels adapted to current market regime.
    Falls back to fixed multipliers if regime is None.
    """
    sl_mult = REGIME_SL_MULTIPLIERS.get(regime, self.sl_atr_mult)
    tp1_rr = REGIME_TP1_RR.get(regime, self.tp1_atr_mult)
    tp2_rr = REGIME_TP2_RR.get(regime, self.tp2_atr_mult)
    sl_distance = atr * sl_mult

    if direction == "LONG":
        stop_loss = entry - sl_distance
        take_profit_1 = entry + sl_distance * tp1_rr
        take_profit_2 = entry + sl_distance * tp2_rr
    elif direction == "SHORT":
        stop_loss = entry + sl_distance
        take_profit_1 = entry - sl_distance * tp1_rr
        take_profit_2 = entry - sl_distance * tp2_rr
    else:
        return {"stop_loss": None, "take_profit_1": None, "take_profit_2": None}

    q = Decimal("0.00000001")
    return {
        "stop_loss": stop_loss.quantize(q, rounding=ROUND_HALF_UP),
        "take_profit_1": take_profit_1.quantize(q, rounding=ROUND_HALF_UP),
        "take_profit_2": take_profit_2.quantize(q, rounding=ROUND_HALF_UP),
    }
```

### 8.3 Подключение в SignalEngine

Получать текущий режим перед расчётом уровней:

```python
# В signal_engine.py — после шага 8 (composite score)
current_regime = None
try:
    from src.analysis.regime_detector import RegimeDetector
    detector = RegimeDetector()
    current_regime = await detector.get_current_regime(instrument.symbol, df)
except Exception:
    pass

# Шаг 11 — использовать режим-адаптивный метод:
levels = self.risk_manager.calculate_levels_for_regime(
    entry_price, atr, direction, regime=current_regime
)
```

---

## 9. Изменение 7: Signal Cooldown — снятие при противоположном направлении

### 9.1 Проблема

Cooldown блокирует все сигналы в течение N минут после последнего. Если рынок резко разворачивается, система пропускает противоположный сигнал.

### 9.2 Решение: bypass cooldown при развороте

В `signal_engine.py`, после проверки cooldown добавить:

```python
if last_signal_time:
    elapsed_minutes = (now - last_signal_time).total_seconds() / 60
    if elapsed_minutes < cooldown_minutes:
        # Check if we have a direction reversal
        # Load last signal's direction from DB to compare
        last_sig = await get_latest_signal_for_instrument(db, instrument.id, timeframe)
        if last_sig and last_sig.direction not in ("HOLD", None):
            # Pre-calculate direction from current data (fast path — only TA)
            try:
                pre_ta = TAEngine(df)
                pre_score = pre_ta.calculate_ta_score()
                pre_direction = _determine_direction(pre_score)
            except Exception:
                pre_direction = None

            direction_reversed = (
                pre_direction is not None
                and pre_direction != "HOLD"
                and pre_direction != last_sig.direction
            )

            if not direction_reversed:
                logger.debug(
                    f"[SignalEngine] Cooldown for {instrument.symbol}/{timeframe}: "
                    f"{elapsed_minutes:.0f}/{cooldown_minutes} min — skipping"
                )
                return None
            else:
                logger.info(
                    f"[SignalEngine] Cooldown bypassed for {instrument.symbol}/{timeframe}: "
                    f"direction reversal {last_sig.direction} → {pre_direction}"
                )
        else:
            logger.debug(f"[SignalEngine] Cooldown — skipping")
            return None
```

**Ограничение:** При развороте всё равно применяется фильтр price-change (ATR × 0.3), предотвращая мусорные сигналы при боковике.

---

## 10. Изменение 8: Position Size — убрать хардкод

### 10.1 Проблема

```python
position_size_pct = self.risk_manager.calculate_position_size(
    Decimal("10000"),  # Default $10k account — хардкод
    ...
)
```

Simulator использует $1,000. Расчётный размер позиции не соответствует реальному счёту.

### 10.2 Решение: размер счёта из config

Добавить в `config.py`:

```python
VIRTUAL_ACCOUNT_SIZE_USD: float = 1000.0   # Размер виртуального счёта симулятора
SIGNAL_ACCOUNT_SIZE_USD: float = 10000.0   # Размер счёта для расчёта position size в сигналах
```

Обновить `signal_engine.py`:

```python
position_size_pct = self.risk_manager.calculate_position_size(
    Decimal(str(settings.SIGNAL_ACCOUNT_SIZE_USD)),
    settings.MAX_RISK_PER_TRADE_PCT,
    sl_distance,
    entry_price,
)
```

Обновить `trade_simulator.py`:

```python
ACCOUNT_SIZE = Decimal(str(settings.VIRTUAL_ACCOUNT_SIZE_USD))
```

---

## 11. Изменение 9: Таймфрейм-адаптивные периоды индикаторов

### 11.1 Проблема

EMA(200) на M15 = 200 × 15 минут = 50 часов. Это краткосрочный индикатор.
EMA(200) на D1 = 200 × 1 день = 200 дней. Это долгосрочный тренд.
Один и тот же сигнал "EMA200 > цена = bearish" применяется в обоих случаях, хотя смысл принципиально разный.

### 11.2 Решение: таймфрейм-адаптивные периоды

Добавить в `ta_engine.py` таблицу адаптивных периодов:

```python
# Таймфрейм-адаптивные периоды.
# Принцип: сохранить смысловую эквивалентность индикаторов.
# EMA_FAST ≈ "несколько часов", EMA_SLOW ≈ "несколько дней", EMA_LONG ≈ "несколько месяцев"

TF_INDICATOR_PERIODS = {
    "M15": {
        "rsi": 14, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
        "bb": 20, "sma_fast": 20, "sma_slow": 50, "sma_long": 200,
        "ema_fast": 12, "ema_slow": 26, "adx": 14, "stoch_k": 14, "stoch_d": 3, "atr": 14,
    },
    "H1": {
        "rsi": 14, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
        "bb": 20, "sma_fast": 20, "sma_slow": 50, "sma_long": 200,
        "ema_fast": 12, "ema_slow": 26, "adx": 14, "stoch_k": 14, "stoch_d": 3, "atr": 14,
    },
    "H4": {
        "rsi": 14, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
        "bb": 20, "sma_fast": 20, "sma_slow": 50, "sma_long": 100,  # SMA200 = 400 days — overkill
        "ema_fast": 12, "ema_slow": 26, "adx": 14, "stoch_k": 14, "stoch_d": 3, "atr": 14,
    },
    "D1": {
        "rsi": 14, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
        "bb": 20, "sma_fast": 50, "sma_slow": 100, "sma_long": 200,
        "ema_fast": 21, "ema_slow": 55, "adx": 14, "stoch_k": 14, "stoch_d": 3, "atr": 14,
    },
    # Default fallback
    "_default": {
        "rsi": 14, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
        "bb": 20, "sma_fast": 20, "sma_slow": 50, "sma_long": 200,
        "ema_fast": 12, "ema_slow": 26, "adx": 14, "stoch_k": 14, "stoch_d": 3, "atr": 14,
    },
}
```

Обновить `TAEngine.__init__`:
```python
def __init__(self, df: pd.DataFrame, timeframe: str = "_default") -> None:
    self.df = df.copy()
    self._normalize_columns()
    self._periods = TF_INDICATOR_PERIODS.get(timeframe, TF_INDICATOR_PERIODS["_default"])
    self._indicators = None
    self._signals = None
```

Использовать `self._periods` вместо хардкода во всех `_calc_*` методах:
```python
# Было:
rsi = self._calc_rsi(14)

# Стало:
rsi = self._calc_rsi(self._periods["rsi"])
```

Обновить вызов в `signal_engine.py`:
```python
ta_engine = TAEngine(df, timeframe=timeframe)
```

---

## 12. База данных — схема изменений

### 12.1 Новые поля

Таблица `signals` — добавить колонку `regime`:

```sql
ALTER TABLE signals ADD COLUMN regime VARCHAR(32) NULL;
```

Alembic migration: `d1e2f3a4b5c6_add_regime_to_signals.py`

```python
def upgrade():
    op.add_column("signals", sa.Column("regime", sa.String(32), nullable=True))
    op.create_index("ix_signals_regime", "signals", ["regime"])

def downgrade():
    op.drop_index("ix_signals_regime", "signals")
    op.drop_column("signals", "regime")
```

### 12.2 Новые поля в config (не меняют схему)

```python
# config.py
VIRTUAL_ACCOUNT_SIZE_USD: float = 1000.0
SIGNAL_ACCOUNT_SIZE_USD: float = 10000.0
LLM_SCORE_THRESHOLD: float = 10.0   # Перенести из хардкода в signal_engine.py
```

---

## 13. Тесты и валидация

### 13.1 Обязательные юнит-тесты

**`tests/test_ta_engine_v3.py`**
- RSI в тренде (ADX > 25): RSI < 30 даёт strength × 0.4 (не 1.0)
- RSI в флэте (ADX < 25): RSI < 30 даёт полный bullish signal
- S/R кластеры: 3+ касания дают touch_boost > 1.0
- ADX fallback без TA-Lib: plus_di ≠ minus_di (не константа)
- `TAEngine(df, timeframe="H4")`: `_periods["sma_long"] == 100`

**`tests/test_fa_routing_v3.py`**
- `market == "forex"` → `ForexFAEngine` вызывается, не `FAEngine`
- `market == "stocks"` → `StockFAEngine` вызывается
- `market == "crypto"` → `CryptoFAEngine` вызывается (уже тестируется в v2)

**`tests/test_sentiment_filtering_v3.py`**
- `get_news_events_for_instrument("EURUSD", "forex")`: возвращает новости с "EUR" или "ECB" в headline
- `get_news_events_for_instrument("BTCUSDT", "crypto")`: возвращает новости с "bitcoin" или "BTC"
- При < 5 результатах добавляет общие macro новости

**`tests/test_mtf_filter_v3.py`**
- `_get_direction_from_score(10.0)` → 1 (было 0)
- `_get_direction_from_score(-8.0)` → -1 (было 0)
- `_get_direction_from_score(3.0)` → 0
- MTF agree_2: composite 12 × 1.2 = 14.4
- MTF disagree_2: composite 12 × 0.4 = 4.8

**`tests/test_risk_manager_v3.py`**
- `calculate_levels_for_regime(entry, atr, "LONG", "RANGING")`: TP1_RR = 1.5 (не 2.0)
- `calculate_levels_for_regime(entry, atr, "SHORT", "HIGH_VOLATILITY")`: SL_mult = 2.5
- `calculate_levels_for_regime(entry, atr, "LONG", None)`: использует дефолтные значения

**`tests/test_signal_cooldown_v3.py`**
- Cooldown активен + direction reversal: сигнал генерируется
- Cooldown активен + то же направление: сигнал блокируется

### 13.2 Интеграционные тесты

**`tests/test_signal_engine_v3_integration.py`**
- Полный прогон `generate_signal` для EURUSD/H1 с mock данными:
  - Убедиться что `ForexFAEngine` вызван
  - Убедиться что `LLMEngine` вызывается при composite > 10
  - Убедиться что `MTFFilter` применяет ненейтральный множитель при composite > 7
- Полный прогон для BTCUSDT/H1: `CryptoFAEngine` вызван

### 13.3 Regression тесты

Сохранить snapshot тестовые случаи с ожидаемыми диапазонами (не точными значениями):

```python
# test_signal_regression_v3.py
def test_eurusd_h1_fa_score_range():
    """ForexFAEngine score must use more of the range than legacy FAEngine."""
    score = run_forex_fa("EURUSD")
    assert abs(score) > 5  # legacy was often <5
    # If rate differential > 100bp, score should be significant
    # (можно верифицировать только при наличии реальных данных)

def test_mtf_direction_not_always_neutral():
    """MTF filter must change score when higher TF has clear direction."""
    mtf = MTFFilter(buy_threshold=7.0, sell_threshold=-7.0)
    adjusted = mtf.apply(12.0, "H1", [{"timeframe": "H4", "score": 15.0}])
    assert adjusted > 12.0  # agree_1 multiplier = 1.0 (нет буста), но не нейтрал в смысле направления
    adjusted2 = mtf.apply(12.0, "H1", [{"timeframe": "H4", "score": -12.0}])
    assert adjusted2 < 12.0  # disagree_1 multiplier = 0.7
```

### 13.4 Coverage требования

- `src/analysis/ta_engine.py`: ≥ 90%
- `src/signals/signal_engine.py` (изменённые блоки): ≥ 85%
- `src/signals/mtf_filter.py`: 100%
- `src/signals/risk_manager.py`: ≥ 90%
- `src/database/crud.py` (`get_news_events_for_instrument`): ≥ 85%

---

## 14. План реализации (Phases)

### Phase 3.1 — Критические исправления (приоритет 1)
*Затрагивают качество всех сигналов. Выполнять первыми.*

- [ ] **3.1.1** MTF Filter: исправить `_get_direction_from_score` порог
- [ ] **3.1.2** Signal Engine: снизить `LLM_SCORE_THRESHOLD` до 10.0
- [ ] **3.1.3** Signal Engine: заменить `FAEngine` на `ForexFAEngine`/`StockFAEngine` по рынку
- [ ] **3.1.4** Sentiment: добавить `get_news_events_for_instrument` в crud.py и подключить в engine
- [ ] **3.1.5** Position Size: убрать хардкод $10k, вынести в config

### Phase 3.2 — TA Engine улучшения (приоритет 2)
*Улучшают качество технического сигнала.*

- [ ] **3.2.1** RSI: реализовать контекстный сигнал через ADX
- [ ] **3.2.2** ADX fallback: корректная реализация без TA-Lib
- [ ] **3.2.3** S/R: кластерная детекция с touch count
- [ ] **3.2.4** Volume signal: направление через SMA20 вместо MACD
- [ ] **3.2.5** Таймфрейм-адаптивные периоды: `TF_INDICATOR_PERIODS` таблица + `timeframe` в TAEngine

### Phase 3.3 — Risk Manager адаптация (приоритет 2)
- [ ] **3.3.1** Добавить `calculate_levels_for_regime` в `RiskManager`
- [ ] **3.3.2** Получать `current_regime` в `generate_signal` и передавать в risk manager
- [ ] **3.3.3** Alembic migration: добавить колонку `regime` в `signals`

### Phase 3.4 — Signal Cooldown (приоритет 3)
- [ ] **3.4.1** Реализовать direction-reversal bypass в cooldown логике
- [ ] **3.4.2** Тест: reversal bypass работает, боковик не проходит

### Phase 3.5 — Тесты и валидация
- [ ] **3.5.1** Написать все юнит-тесты из раздела 13.1
- [ ] **3.5.2** Написать интеграционные тесты (раздел 13.2)
- [ ] **3.5.3** Regression тесты
- [ ] **3.5.4** Проверить coverage ≥ 90% для изменённых модулей

---

## Приложение A: Сводная таблица изменений

| # | Файл | Тип изменения | Влияние |
|---|------|--------------|---------|
| 1 | `src/signals/mtf_filter.py` | Bugfix — порог ±30 → ±BUY_THRESHOLD | Критическое: MTF был нерабочим |
| 2 | `src/signals/signal_engine.py` | LLM_SCORE_THRESHOLD: 25→10 | Критическое: LLM не вызывался |
| 3 | `src/signals/signal_engine.py` | FA routing: FAEngine → ForexFA/StockFA | Критическое: неправильные FA данные |
| 4 | `src/database/crud.py` | Новый метод `get_news_events_for_instrument` | Высокое: sentiment был глобальным |
| 5 | `src/signals/signal_engine.py` | Использовать инструментальный sentiment | Высокое |
| 6 | `src/analysis/ta_engine.py` | RSI контекстный сигнал (ADX-aware) | Высокое: ложные сигналы в тренде |
| 7 | `src/analysis/ta_engine.py` | ADX fallback корректный | Среднее: ADX был мёртв без TA-Lib |
| 8 | `src/analysis/ta_engine.py` | S/R через кластеры (touch count) | Высокое: S/R был некорректен |
| 9 | `src/analysis/ta_engine.py` | Volume signal: SMA20 вместо MACD | Среднее |
| 10 | `src/analysis/ta_engine.py` | TF-адаптивные периоды индикаторов | Среднее |
| 11 | `src/signals/risk_manager.py` | Режим-адаптивные SL/TP | Среднее |
| 12 | `src/signals/signal_engine.py` | Cooldown bypass при развороте | Среднее |
| 13 | `src/config.py` | VIRTUAL_ACCOUNT_SIZE_USD, SIGNAL_ACCOUNT_SIZE_USD | Низкое |
| 14 | `alembic/versions/` | Новая миграция: `regime` в `signals` | Низкое |

---

## Приложение B: Что НЕ меняется в v3

- Схема БД (кроме одной колонки `regime`)
- REST API endpoints (`/api/v2/signals`, `/api/v2/simulator/*`)
- Telegram формат уведомлений
- Docker-compose конфигурация
- Collector логика (price, macro, news, calendar)
- Frontend страницы и компоненты
- Backtesting engine
- Walk-forward validator
- FinBERT микросервис
- Celery beat расписание
- GDELT geo engine
