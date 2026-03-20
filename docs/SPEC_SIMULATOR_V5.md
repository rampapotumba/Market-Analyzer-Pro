# Спецификация: Trade Simulator v5 — Фильтрация, Качество входов и Достоверность бэктеста

**Версия:** 1.0
**Дата:** 2026-03-19
**Статус:** DRAFT — к реализации
**Предыдущая версия:** `docs/SPEC_SIMULATOR_V4.md` (реализована, SIM-17..SIM-24 закрыты)
**Основание:** `docs/REQUIREMENTS.md` — анализ бэктеста Oct 2025 – Mar 2026, 8 символов, H1

---

## 1. Контекст и мотивация

### Текущее состояние после v4

v4 решил системные проблемы: SHORT bias устранён (SIM-17), SL адаптируется к режиму (SIM-19),
R:R динамический (SIM-18), корреляционный guard работает (SIM-21), бэктест-движок запущен (SIM-22).

### Проблема

Первый полноценный бэктест (Oct 2025 – Mar 2026, H1, 8 символов) показал:

| Метрика | Значение | Оценка |
|---------|----------|--------|
| Win rate | 41.1% | На грани безубытка (breakeven при RR 1.47 = 40.5%) |
| Profit factor | 1.004 | Практически нулевое edge |
| Max drawdown | 50.2% | Неприемлемо высокий |
| Avg win / Avg loss | $18.49 / $12.60 | Actual RR = 1.47 |
| Trades/month | ~110 | Слишком много — много мусорных входов |

### Корневые причины

1. **Слабые сигналы проходят** — порог ±10 пропускает noise-сигналы
2. **Контртрендовые входы** — LONG в даунтренде, SHORT в аптренде (нет D1 фильтра)
3. **Торговля в RANGING** — трендовые сигналы в боковике = случайное направление
4. **BTC/ETH убыточны** — стандартные параметры не подходят для крипто-волатильности
5. **Нет volume/momentum confirmation** — вход без подтверждения объёмом и импульсом
6. **Breakeven в entry** — нормальный откат от TP1 выбивает оставшиеся 50%
7. **Застрявшие позиции** — dead money 48+ часов без движения

### Цель v5

Поднять систему с breakeven (PF 1.004) до стабильно прибыльной (PF ≥ 1.4):
- Отфильтровать мусорные входы (P1 + P2 фильтры)
- Улучшить управление позициями (P3)
- Добавить новые источники данных (P4)
- Обеспечить достоверность бэктеста (P5)

### Целевые метрики

| Метрика | Текущее | Цель (P1+P2) | Цель (P1+P2+P3) |
|---------|---------|--------------|-----------------|
| Win rate | 41.1% | 46–48% | 49–52% |
| Profit factor | 1.004 | 1.4–1.6 | 1.6–2.0 |
| Max drawdown | 50.2% | 30–35% | 20–28% |
| Avg trades/month | ~110 | ~65–75 | ~55–65 |

---

## 2. Перечень изменений

| # | Приоритет | Компонент | REQ | Описание |
|---|-----------|-----------|-----|----------|
| SIM-25 | P1 | `signal_engine.py`, `config.py` | REQ-01 | Порог composite score ±15 (крипто ±20) |
| SIM-26 | P1 | `signal_engine.py` | REQ-02 | Запрет торговли в RANGING режиме |
| SIM-27 | P1 | `signal_engine.py` | REQ-03 | D1 MA200 alignment filter |
| SIM-28 | P1 | `config.py`, `risk_manager_v2.py` | REQ-04, REQ-05 | Instrument overrides (per-symbol параметры) |
| SIM-29 | P2 | `signal_engine.py` | REQ-06 | Volume confirmation filter |
| SIM-30 | P2 | `signal_engine.py` | REQ-07 | Momentum alignment (RSI/MACD) |
| SIM-31 | P2 | `signal_engine.py` | REQ-08 | Min signal strength = BUY |
| SIM-32 | P2 | `signal_engine.py` | REQ-09 | Weekday filter (Mon open / Fri close) |
| SIM-33 | P2 | `backtest_engine.py` | REQ-10 | Economic calendar filter в бэктесте |
| SIM-34 | P3 | `signal_tracker.py` | REQ-11 | Breakeven на 50% пути к TP1 |
| SIM-35 | P3 | `signal_tracker.py` | REQ-12 | Time-based exit (закрытие застрявших позиций) |
| SIM-36 | P3 | `backtest_engine.py` | REQ-13 | S/R snapping для SL в бэктесте |
| SIM-37 | P3 | `signal_tracker.py`, config | REQ-14 | Обновление swap-ставок |
| SIM-38 | P4 | `realtime_collector.py`, `signal_engine.py` | REQ-15 | DXY real-time фильтр |
| SIM-39 | P4 | новый collector, `signal_engine.py` | REQ-16 | Fear & Greed Index для крипто |
| SIM-40 | P4 | `signal_engine.py` | REQ-17 | Funding Rate extreme filter |
| SIM-41 | P4 | новый collector, `fa_engine.py` | REQ-18 | COT Data для форекс |
| SIM-42 | P5 | `backtest_engine.py` | REQ-19 | Унификация фильтров live/backtest |
| SIM-43 | P5 | `backtest_params.py`, `backtest_engine.py` | REQ-20 | Параметризация бэктеста |
| SIM-44 | P5 | `backtest_engine.py` | REQ-21 | Расширенные метрики бэктеста |

---

## 3. Детальные требования

---

### SIM-25: Порог composite score ±15 (крипто ±20)

**Проблема:** порог ±10 из ±100 пропускает слишком много слабых сигналов.

**Требование:**

```python
# В config.py — новые конфигурируемые константы
MIN_COMPOSITE_SCORE = 15          # глобальный порог (было 10)
MIN_COMPOSITE_SCORE_CRYPTO = 20   # для market == "crypto"
```

**Изменения:**
- `src/signals/signal_engine.py` → в `generate_signal()`: после расчёта composite_score проверять порог перед генерацией сигнала
- `src/backtesting/backtest_engine.py` → в `_generate_signal()`: применять тот же порог
- Константы в `src/config.py`, не хардкод

**Логика:**
```python
market = instrument.market  # "forex", "crypto", "stocks"
threshold = MIN_COMPOSITE_SCORE_CRYPTO if market == "crypto" else MIN_COMPOSITE_SCORE
if abs(composite_score) < threshold:
    logger.debug(f"[SIM-25] Score {composite_score} below threshold {threshold} for {symbol}")
    return None
```

**Тесты:**
- `test_sim25_score_below_threshold_rejected` — score=12 → сигнал не генерируется
- `test_sim25_score_above_threshold_accepted` — score=16 → сигнал генерируется
- `test_sim25_crypto_higher_threshold` — crypto score=17 → rejected, score=21 → accepted
- `test_sim25_threshold_from_config` — порог берётся из конфигурации

---

### SIM-26: Запрет торговли в RANGING режиме

**Проблема:** в RANGING режиме трендовые сигналы имеют случайное направление.

**Требование:**

```python
# В signal_engine.py — конфигурируемый список блокируемых режимов
BLOCKED_REGIMES = ["RANGING"]  # расширяемый список
```

**Изменения:**
- `src/signals/signal_engine.py` → в `generate_signal()`: после определения regime — проверить
- `src/backtesting/backtest_engine.py` → аналогично
- Логировать: `logger.info(f"[SIM-26] Skipping: {regime} regime for {symbol}")`

**Тесты:**
- `test_sim26_ranging_blocked` — regime="RANGING" → return None
- `test_sim26_trend_allowed` — regime="TREND_BULL" → сигнал генерируется
- `test_sim26_volatile_allowed` — regime="VOLATILE" → сигнал генерируется
- `test_sim26_blocked_regimes_configurable` — BLOCKED_REGIMES расширяем

---

### SIM-27: D1 MA200 alignment filter

**Проблема:** система открывает LONG в D1 даунтренде и SHORT в D1 аптренде. Контртрендовые входы на H1 имеют статистически худшую вероятность.

**Требование:**

```python
# Перед генерацией сигнала на H1/H4:
# LONG: допустим только если close(D1) > MA200(D1)
# SHORT: допустим только если close(D1) < MA200(D1)
# Для D1: проверять W1 MA50
# Для M1/M5/M15: фильтр не применяется (скальпинг)
```

**Изменения:**
- `src/signals/signal_engine.py` → новый метод `_check_d1_trend_alignment(symbol, direction, timeframe) -> bool`
- Данные D1 берутся из `price_data` таблицы (timeframe="D1")
- MA200 считается из последних 200 D1 candles
- Если D1 данных нет → фильтр пропускается с `logger.warning("[SIM-27] No D1 data for {symbol}, skipping trend filter")`
- `src/backtesting/backtest_engine.py` → тот же фильтр, используя исторические D1 candles БЕЗ lookahead

**Формула:**
```python
d1_candles = get_d1_candles(symbol, count=200)
if len(d1_candles) < 200:
    logger.warning(f"[SIM-27] Insufficient D1 data ({len(d1_candles)}/200) for {symbol}")
    return True  # пропустить фильтр, не блокировать

ma200 = sum(c.close for c in d1_candles) / len(d1_candles)
current_close = d1_candles[-1].close

if direction == "LONG" and current_close < ma200:
    logger.info(f"[SIM-27] Blocked LONG: D1 close {current_close} < MA200 {ma200}")
    return False
if direction == "SHORT" and current_close > ma200:
    logger.info(f"[SIM-27] Blocked SHORT: D1 close {current_close} > MA200 {ma200}")
    return False
return True
```

**Тесты:**
- `test_sim27_long_blocked_below_ma200` — D1 close < MA200 → LONG blocked
- `test_sim27_short_blocked_above_ma200` — D1 close > MA200 → SHORT blocked
- `test_sim27_long_allowed_above_ma200` — D1 close > MA200 → LONG allowed
- `test_sim27_no_d1_data_passthrough` — нет D1 данных → фильтр пропущен
- `test_sim27_m15_no_filter` — M15 таймфрейм → фильтр не применяется

---

### SIM-28: Instrument overrides (per-symbol параметры)

**Проблема:** BTC/USDT — самый убыточный символ (-$269, WR 40.9%). Стандартные ATR-мультипликаторы слишком малы. GBPUSD (WR 36.9%) и USDCHF (WR 40.4%) тоже проблемные.

**Требование:**

```python
# В config.py или в начале risk_manager_v2.py
INSTRUMENT_OVERRIDES: dict[str, dict] = {
    "BTC/USDT": {
        "sl_atr_multiplier": 3.5,        # было 2.5 (VOLATILE)
        "min_composite_score": 20,        # выше глобального 15
        "allowed_regimes": ["STRONG_TREND_BULL", "STRONG_TREND_BEAR"],
    },
    "ETH/USDT": {
        "sl_atr_multiplier": 3.5,
        "min_composite_score": 20,
    },
    "GBPUSD=X": {
        "min_composite_score": 20,
    },
    "USDCHF=X": {
        "min_composite_score": 18,
    },
}
```

**Изменения:**
- `src/config.py` → добавить `INSTRUMENT_OVERRIDES`
- `src/signals/risk_manager_v2.py` → `calculate_levels_for_regime()` принимает и применяет overrides
- `src/signals/signal_engine.py` → при проверке порога score использовать per-symbol override
- `src/backtesting/backtest_engine.py` → те же overrides

**Приоритет применения:**
```
instrument_override > global_config > hardcoded_default
```

**Тесты:**
- `test_sim28_btc_wider_sl` — BTC/USDT: SL = 3.5×ATR (не 2.5)
- `test_sim28_btc_higher_threshold` — BTC/USDT: score=17 → rejected
- `test_sim28_btc_only_strong_trend` — BTC/USDT в TREND_BULL → blocked (only STRONG_TREND allowed)
- `test_sim28_gbpusd_higher_threshold` — GBPUSD: score=17 → rejected, score=21 → accepted
- `test_sim28_no_override_default` — EURUSD: стандартные параметры (нет override)

---

### SIM-29: Volume confirmation filter

**Проблема:** сигналы без подтверждения объёмом статистически ложные.

**Требование:**

```python
def _check_volume_confirmation(self, df: pd.DataFrame) -> bool:
    """Проверяет что текущий объём >= 120% от MA20 объёма."""
    if df["volume"].sum() == 0:  # брокер не передаёт объём
        return True  # пропустить фильтр

    vol_ma20 = df["volume"].rolling(20).mean().iloc[-1]
    current_vol = df["volume"].iloc[-1]

    if current_vol < vol_ma20 * Decimal("1.2"):
        logger.debug(f"[SIM-29] Volume {current_vol} < 120% of MA20 {vol_ma20}")
        return False
    return True
```

**Изменения:**
- `src/signals/signal_engine.py` → новый метод `_check_volume_confirmation(df) -> bool`
- Вызывается ПЕРЕД генерацией сигнала
- `src/backtesting/backtest_engine.py` → аналогично

**Тесты:**
- `test_sim29_volume_above_threshold_passes` — vol=150% MA20 → True
- `test_sim29_volume_below_threshold_blocked` — vol=80% MA20 → False
- `test_sim29_zero_volume_passthrough` — все vol=0 → True (фильтр пропущен)
- `test_sim29_insufficient_data_passthrough` — менее 20 свечей → True

---

### SIM-30: Momentum alignment (RSI/MACD)

**Проблема:** система может генерировать LONG когда MACD падает, или SHORT когда RSI растёт.

**Требование:**

```python
def _check_momentum_alignment(self, ta_indicators: dict, direction: str) -> bool:
    """
    LONG: RSI(14) > 50 И MACD line > Signal line
    SHORT: RSI(14) < 50 И MACD line < Signal line
    """
    rsi = ta_indicators.get("rsi_14")
    macd_line = ta_indicators.get("macd_line")
    macd_signal = ta_indicators.get("macd_signal")

    if rsi is None or macd_line is None or macd_signal is None:
        logger.warning("[SIM-30] Missing RSI/MACD data, skipping momentum filter")
        return True  # не блокировать при отсутствии данных

    if direction == "LONG":
        return rsi > 50 and macd_line > macd_signal
    elif direction == "SHORT":
        return rsi < 50 and macd_line < macd_signal
    return True
```

**Изменения:**
- `src/signals/signal_engine.py` → новый метод
- Значения RSI и MACD из `ta_engine.calculate_all_indicators()`
- `src/backtesting/backtest_engine.py` → аналогично

**Тесты:**
- `test_sim30_long_momentum_confirmed` — RSI=55, MACD > Signal → True
- `test_sim30_long_momentum_rejected_rsi` — RSI=45 → False
- `test_sim30_long_momentum_rejected_macd` — MACD < Signal → False
- `test_sim30_missing_data_passthrough` — нет RSI → True

---

### SIM-31: Min signal strength = BUY

**Проблема:** пограничные weak_buy/weak_sell дают плохое качество.

**Требование:**

```python
# Допустимые signal strength для открытия позиции
ALLOWED_SIGNAL_STRENGTHS = {"BUY", "STRONG_BUY", "SELL", "STRONG_SELL"}
# Запрещены: HOLD, WEAK_BUY, WEAK_SELL

MIN_SIGNAL_STRENGTH = "BUY"  # конфигурируемый
```

**Изменения:**
- `src/signals/signal_engine.py` → после определения signal_strength (score bucket) проверить
- Логировать на DEBUG: `[SIM-31] Filtered weak signal: {strength} for {symbol}`

**Тесты:**
- `test_sim31_strong_buy_allowed` — STRONG_BUY → allowed
- `test_sim31_buy_allowed` — BUY → allowed
- `test_sim31_weak_buy_rejected` — WEAK_BUY → rejected
- `test_sim31_hold_rejected` — HOLD → rejected

---

### SIM-32: Weekday filter (Mon open / Fri close)

**Проблема:** понедельники — гэпы открытия, пятницы — институциональное закрытие.

**Требование:**

```python
WEEKDAY_FILTER = {
    "monday_block_until_utc": 10,    # Mon 00:00–10:00 UTC
    "friday_block_from_utc": 18,     # Fri 18:00–23:59 UTC
    "crypto_exempt_monday": True,    # крипто 24/7, гэп-период не применяется
}
```

**Изменения:**
- `src/signals/signal_engine.py` → новый метод `_check_weekday_filter(timestamp, market) -> bool`
- `src/backtesting/backtest_engine.py` → аналогично

**Тесты:**
- `test_sim32_monday_morning_blocked` — Mon 06:00 UTC, forex → blocked
- `test_sim32_monday_afternoon_allowed` — Mon 14:00 UTC → allowed
- `test_sim32_friday_evening_blocked` — Fri 20:00 UTC, forex → blocked
- `test_sim32_monday_crypto_allowed` — Mon 06:00 UTC, crypto → allowed

---

### SIM-33: Economic calendar filter в бэктесте

**Проблема:** в live есть блок HIGH-impact событий, в бэктесте нет → результаты расходятся.

**Требование:**
- В `BacktestEngine._simulate_symbol()`: перед генерацией сигнала проверять HIGH-impact events ±2 часа
- Использовать таблицу `economic_events` (если данные есть)
- Если исторических событий нет в БД → фильтр пропускается

**Изменения:**
- `src/backtesting/backtest_engine.py` → проверка economic events
- `src/database/crud.py` → `get_economic_events_in_range(db, start, end) -> list`

**Тесты:**
- `test_sim33_high_impact_event_blocks_signal` — NFP в ±2h → signal blocked
- `test_sim33_no_event_allows_signal` — нет событий → allowed
- `test_sim33_no_historical_events_passthrough` — пустая таблица → allowed

---

### SIM-34: Breakeven на 50% пути к TP1

**Проблема:** после partial close SL в entry — нормальный откат от TP1 до entry выбивает оставшуюся позицию.

**Требование:**

```python
BREAKEVEN_BUFFER_RATIO = Decimal("0.5")  # 50% пути от entry к TP1

# LONG: new_sl = entry + BREAKEVEN_BUFFER_RATIO * (tp1 - entry)
# SHORT: new_sl = entry - BREAKEVEN_BUFFER_RATIO * (entry - tp1)
```

**Изменения:**
- `src/tracker/signal_tracker.py` → в `_partial_close()`: использовать буфер вместо entry
- `src/signals/trade_lifecycle.py` → обновить breakeven логику

**Тесты:**
- `test_sim34_breakeven_with_buffer_long` — LONG: entry=1.1000, TP1=1.1100 → new_sl=1.1050 (не 1.1000)
- `test_sim34_breakeven_with_buffer_short` — SHORT: entry=1.1000, TP1=1.0900 → new_sl=1.0950
- `test_sim34_buffer_configurable` — изменение BREAKEVEN_BUFFER_RATIO влияет на результат
- `test_sim34_remaining_position_survives_normal_pullback` — цена откатилась на 30% от TP1 → позиция жива

---

### SIM-35: Time-based exit (закрытие застрявших позиций)

**Проблема:** позиция может висеть 48+ часов без движения — dead money.

**Требование:**

```python
TIME_EXIT_CANDLES = {
    "H1": 48,   # 48 часов
    "H4": 20,   # ~3.3 дня
    "D1": 10,   # 2 недели
}
# exit_reason = "time_exit"
```

**Изменения:**
- `src/tracker/signal_tracker.py` → в `check_signal()`: после MAE early exit, ДО SL/TP check
- Проверять `candles_elapsed >= TIME_EXIT_CANDLES[timeframe]` И позиция НЕ в прибыли (unrealized < 0 или breakeven)
- `exit_reason = "time_exit"`

**Тесты:**
- `test_sim35_time_exit_h1_48_candles` — H1, 50 свечей, unrealized < 0 → exit
- `test_sim35_time_exit_no_trigger_profitable` — H1, 50 свечей, unrealized > 0 → no exit (позиция растёт)
- `test_sim35_time_exit_no_trigger_early` — H1, 20 свечей → no exit
- `test_sim35_time_exit_h4_20_candles` — H4, 22 свечи → exit

---

### SIM-36: S/R snapping для SL в бэктесте

**Проблема:** в live SL выравнивается по S/R уровням, в бэктесте — нет.

**Требование:**
- В `BacktestEngine._recalc_sl_tp()`: передавать S/R уровни из TAEngine
- Использовать `RiskManagerV2.calculate_levels_for_regime()` с уровнями

**Изменения:**
- `src/backtesting/backtest_engine.py` → получать S/R уровни из ta_indicators
- TAEngine должен возвращать `support_levels` и `resistance_levels`

**Тесты:**
- `test_sim36_backtest_sl_snaps_to_support` — SL выравнивается к ближайшему support
- `test_sim36_backtest_no_sr_levels_fallback` — нет S/R → SL по ATR (как раньше)

---

### SIM-37: Обновление swap-ставок

**Проблема:** SWAP_DAILY_PIPS содержит данные 2023 года, нет AUDUSD.

**Требование:**
- Вынести `SWAP_DAILY_PIPS` в `config/swap_rates.json`
- Добавить дату последнего обновления и предупреждение если > 90 дней
- Добавить AUDUSD ставки
- Fallback: если файл не найден → использовать хардкод (текущие значения) с warning

**Изменения:**
- Создать `config/swap_rates.json`
- `src/tracker/signal_tracker.py` → загружать ставки из JSON, fallback к хардкоду

**Тесты:**
- `test_sim37_swap_rates_from_json` — ставки загружаются из файла
- `test_sim37_swap_rates_fallback` — файл не найден → хардкод с warning
- `test_sim37_swap_rates_stale_warning` — данным > 90 дней → warning

---

### SIM-38: DXY real-time фильтр

**Проблема:** DXY не используется в real-time для фильтрации форекс-сигналов.

**Требование:**

```python
# DXY RSI(14) > 55 → USD растёт:
#   Block LONG: EURUSD, GBPUSD, AUDUSD, NZDUSD (USD long side)
#   Allow LONG: USDJPY, USDCAD, USDCHF (USD base pairs)
# DXY RSI(14) < 45 → USD падает:
#   Block SHORT для USD long side pairs
#   Allow SHORT для USD base pairs
# DXY RSI в 45–55 → нейтрально, не фильтровать
```

**Изменения:**
- `src/collectors/realtime_collector.py` → собирать DXY (DX-Y.NYB) через yfinance каждую минуту
- `src/signals/signal_engine.py` → новый метод `_check_dxy_alignment(direction, symbol) -> bool`
- Хранить DXY RSI в cache/memory (не нужна таблица — только last value)

**Тесты:**
- `test_sim38_dxy_strong_blocks_usd_long_side` — DXY RSI=60 → EURUSD LONG blocked
- `test_sim38_dxy_strong_allows_usd_base` — DXY RSI=60 → USDJPY LONG allowed
- `test_sim38_dxy_neutral_no_filter` — DXY RSI=50 → не фильтрует
- `test_sim38_dxy_no_data_passthrough` — нет данных DXY → не блокировать

---

### SIM-39: Fear & Greed Index для крипто

**Проблема:** ключевой сентимент-индикатор для крипто не используется.

**Требование:**

```python
# API: https://api.alternative.me/fng/?limit=1
# value <= 20 (Extreme Fear) → +5 к composite для LONG BTC/ETH
# value >= 80 (Extreme Greed) → +5 к composite для SHORT BTC/ETH
# 21–79 → не влиять
```

**Изменения:**
- Создать `src/collectors/fear_greed_collector.py` — сбор из API, хранение в `macro_data`
- `src/scheduler/jobs.py` → добавить job раз в час
- `src/signals/signal_engine.py` → учитывать F&G при расчёте composite для crypto

**Тесты:**
- `test_sim39_extreme_fear_boosts_long` — FG=15 → +5 к LONG composite для BTC
- `test_sim39_extreme_greed_boosts_short` — FG=85 → +5 к SHORT composite для BTC
- `test_sim39_neutral_no_effect` — FG=50 → 0 adjustment
- `test_sim39_non_crypto_no_effect` — FG=15 → 0 adjustment для EURUSD

---

### SIM-40: Funding Rate extreme filter

**Проблема:** funding rate собирается, но не используется как фильтр.

**Требование:**

```python
# funding_rate > +0.1% (8h) → рынок перегрет лонгами → LONG penalty -10 к composite
# funding_rate < -0.1% (8h) → рынок перегрет шортами → SHORT penalty -10 к composite
# Применяется только для crypto
```

**Изменения:**
- `src/signals/signal_engine.py` → в scoring pipeline для crypto: проверять funding rate
- Данные из `order_flow_data` (уже собираются)

**Тесты:**
- `test_sim40_high_funding_penalizes_long` — FR=+0.15% → LONG composite -10
- `test_sim40_negative_funding_penalizes_short` — FR=-0.15% → SHORT composite -10
- `test_sim40_normal_funding_no_effect` — FR=+0.03% → 0 penalty
- `test_sim40_non_crypto_no_effect` — forex → не применяется

---

### SIM-41: COT Data для форекс

**Проблема:** позиционирование крупных участников не учитывается.

**Требование:**
- Собирать COT из CFTC (публичный API, еженедельно)
- Хранить в `macro_data` с `indicator = "COT_{symbol}"`
- Non-commercials net long + увеличивают → +5 к FA score
- Non-commercials net short + увеличивают → -5 к FA score

**Изменения:**
- Создать `src/collectors/cot_collector.py`
- `src/scheduler/jobs.py` → weekly job (пятница)
- `src/signals/fa_engine.py` → использовать COT данные

**Тесты:**
- `test_sim41_cot_net_long_boosts_fa` — net long growing → +5 FA
- `test_sim41_cot_net_short_penalizes_fa` — net short growing → -5 FA
- `test_sim41_cot_no_data_neutral` — нет данных → 0

---

### SIM-42: Унификация фильтров live/backtest

**Проблема:** фильтры работают в live, но отсутствуют в бэктесте → несопоставимые результаты.

**Требование — все следующие фильтры должны работать одинаково в live и backtest:**
- Session filter (Asian session block) ✅ уже
- Cooldown per timeframe ✅ уже
- SIM-25: Composite score threshold
- SIM-26: RANGING regime block
- SIM-27: D1 MA200 trend filter
- SIM-29: Volume confirmation
- SIM-30: Momentum alignment
- SIM-32: Weekday filter
- SIM-33: Economic calendar

**Подход:** Вынести все фильтры в общий `SignalFilterPipeline` класс, используемый и live и backtest.

**Изменения:**
- Создать `src/signals/filter_pipeline.py` → класс `SignalFilterPipeline` со всеми фильтрами
- `src/signals/signal_engine.py` → использовать pipeline
- `src/backtesting/backtest_engine.py` → использовать тот же pipeline

**Тесты:**
- `test_sim42_backtest_applies_all_filters` — бэктест применяет все фильтры
- `test_sim42_live_and_backtest_same_result` — одинаковый input → одинаковый output

---

### SIM-43: Параметризация бэктеста

**Требование — расширить BacktestParams:**

```python
class BacktestParams(BaseModel):
    # существующие поля...
    apply_slippage: bool = True
    apply_swap: bool = True
    # новые:
    apply_ranging_filter: bool = True
    apply_d1_trend_filter: bool = True
    apply_volume_filter: bool = True
    apply_weekday_filter: bool = True
    apply_momentum_filter: bool = True
    apply_calendar_filter: bool = True
    min_composite_score: Optional[float] = None  # None = использовать глобальный
```

**Изменения:**
- `src/backtesting/backtest_params.py` → расширить модель
- `src/backtesting/backtest_engine.py` → передавать параметры в SignalFilterPipeline
- Frontend: отображать активные параметры на странице бэктестов

**Тесты:**
- `test_sim43_backtest_with_all_filters` — все фильтры включены → меньше сделок
- `test_sim43_backtest_without_filters` — все фильтры выключены → больше сделок
- `test_sim43_custom_score_threshold` — custom min_composite_score применяется

---

### SIM-44: Расширенные метрики бэктеста

**Требование — добавить в summary:**

```python
# Новые поля в backtest summary:
{
    "win_rate_long_pct": float,
    "win_rate_short_pct": float,
    "avg_win_duration_minutes": float,
    "avg_loss_duration_minutes": float,
    "by_weekday": {  # Mon=0..Fri=4
        "0": {"trades": int, "wins": int, "pnl_usd": float},
        ...
    },
    "by_hour_utc": {  # 0..23
        "0": {"trades": int, "win_rate_pct": float},
        ...
    },
    "by_regime": {
        "TREND_BULL": {"trades": int, "wins": int, "pnl_usd": float},
        ...
    },
    "sl_hit_count": int,
    "tp_hit_count": int,
    "mae_exit_count": int,
    "time_exit_count": int,
    "avg_mae_pct_of_sl": float,
}
```

**Изменения:**
- `src/backtesting/backtest_engine.py` → расширить `_compute_summary()`
- `src/backtesting/backtest_params.py` → обновить BacktestResult

**Тесты:**
- `test_sim44_extended_metrics_present` — summary содержит все новые поля
- `test_sim44_win_rate_by_direction` — win_rate_long/short корректны
- `test_sim44_by_regime_breakdown` — by_regime содержит все встреченные режимы

---

## 4. Порядок реализации

```
Phase 0 — Валидация v4 (завершение):
  Запустить backtest v4, создать BACKTEST_RESULTS_V1.md (baseline)

Phase 1 — Критические фильтры (P1):
  SIM-25 (score threshold) → SIM-26 (RANGING block) → SIM-28 (overrides) → SIM-27 (D1 MA200)
  → Бэктест: сравнить с baseline

Phase 2 — Структурные фильтры (P2):
  SIM-29 (volume) → SIM-30 (momentum) → SIM-31 (min strength) → SIM-32 (weekday)
  → SIM-33 (calendar в бэктесте)
  → Бэктест: сравнить с Phase 1

Phase 3 — Управление позициями (P3):
  SIM-34 (breakeven buffer) → SIM-35 (time exit) → SIM-36 (S/R в бэктесте) → SIM-37 (swap rates)
  → Бэктест: сравнить с Phase 2

Phase 4 — Новые данные (P4):
  SIM-38 (DXY) → SIM-39 (F&G) → SIM-40 (funding rate filter) → SIM-41 (COT)
  → Бэктест: сравнить с Phase 3

Phase 5 — Достоверность бэктеста (P5):
  SIM-42 (unification) → SIM-43 (parameterization) → SIM-44 (metrics)
  → Финальный бэктест: BACKTEST_RESULTS_FINAL.md
```

---

## 5. Зависимости

| Задача | Зависит от |
|--------|-----------|
| SIM-25 | — (независимая) |
| SIM-26 | — (независимая) |
| SIM-27 | Доступ к D1 price_data |
| SIM-28 | SIM-25 (использует threshold infrastructure) |
| SIM-29 | — (независимая) |
| SIM-30 | TAEngine (RSI/MACD уже есть) |
| SIM-31 | SIM-25 (score bucket logic) |
| SIM-32 | — (независимая) |
| SIM-33 | economic_events table |
| SIM-34 | — (независимая) |
| SIM-35 | — (независимая) |
| SIM-36 | TAEngine S/R levels |
| SIM-37 | — (независимая) |
| SIM-38 | DXY data collection |
| SIM-39 | Fear & Greed API |
| SIM-40 | order_flow_data (уже есть) |
| SIM-41 | CFTC API |
| SIM-42 | SIM-25..SIM-33 (все фильтры) |
| SIM-43 | SIM-42 |
| SIM-44 | SIM-35 (time_exit), SIM-42 |

---

## 6. Риски

1. **SIM-27 зависит от D1 данных.** Если D1 candles не собираются для всех инструментов → фильтр будет пропущен. Проверить coverage перед реализацией.
2. **SIM-42 (унификация) — рефакторинг.** Вынос фильтров в pipeline требует аккуратности, чтобы не сломать существующую логику. Покрыть тестами ДО рефакторинга.
3. **SIM-38/39/41 — внешние API.** Зависимость от alternative.me, CFTC, yfinance DXY. Graceful fallback обязателен.
4. **Снижение количества сделок.** Каждый фильтр уменьшает trades/month. Если после всех фильтров < 30 trades/month — пересмотреть пороги.
5. **SIM-37 (swap rates) — ручное обновление.** Если JSON не обновляется > 90 дней, swap расчёты будут неточными. Warning обязателен.
