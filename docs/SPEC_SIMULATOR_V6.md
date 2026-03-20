# Trade Simulator v6 -- Спецификация

## Дата: 2026-03-20
## Статус: В разработке
## Фаза: v6 -- Data Integrity, Score Calibration, Filter Activation

---

## Обзор

Simulator v6 фокусируется на трех направлениях:

1. **Целостность данных** -- устранение расхождений в отчетах бэктеста, автоматизация генерации отчетов
2. **Калибровка скоринга** -- исправление критической проблемы масштабирования composite score в бэктесте (composite = 0.45 * ta_score при нулевых FA/sentiment/geo), разблокировка инструментов
3. **Активация фильтров** -- подключение D1 данных, экономического календаря, time/MAE exit, исправление SHORT-сигналов

### Ключевые метрики (Phase 3 v5, верифицированные из JSON)

| Метрика | v5 Actual | v6 Target |
|---------|-----------|-----------|
| Trades/24mo | 33 | >= 80 |
| Win Rate | 45.45% | >= 45% |
| Profit Factor | 2.01 | >= 1.5 |
| Max Drawdown | < 10% | <= 20% |
| Instruments w/ trades | 4/6 | 6/6 |

---

## V6-SCORE: Пропорциональное масштабирование composite score в бэктесте

### Проблема

В бэктесте `composite = 0.45 * ta_score` (FA=0, sentiment=0, geo=0). Это означает:
- Глобальный порог `MIN_COMPOSITE_SCORE=15` требует `ta_score >= 33.3`
- Порог для crypto `MIN_COMPOSITE_SCORE_CRYPTO=20` требует `ta_score >= 44.4`
- Override GBPUSD `min_composite_score=20` требует `ta_score >= 44.4`

В live-режиме все 4 компонента работают, composite может достигать ~45. Бэктест тестирует принципиально другой режим пороговых значений.

### Выбранный подход: (A) Пропорциональное масштабирование порогов

**Обоснование выбора опции (A) вместо (B) и (C):**

- **(B) Нормализация composite до полного диапазона** (`composite = ta_score` в бэктесте) -- меняет характеристики сигналов бэктеста. TA score имеет другое распределение, чем полный composite. Бэктест перестает быть репрезентативным для live.
- **(C) Отдельные пороги бэктеста** (`BACKTEST_MIN_COMPOSITE_SCORE = 7`) -- создает два набора констант, усложняет поддержку. При добавлении FA/sentiment данных в бэктест придется снова менять пороги.
- **(A) Пропорциональное масштабирование** -- автоматически адаптируется к набору доступных данных. Если в будущем в бэктест добавятся FA данные (weight=0.25), effective_threshold пересчитается автоматически: `15 * (0.45 + 0.25) = 10.5`. Не требует ручной калибровки при изменении состава данных.

### Формула

```python
# Определяем сумму весов доступных компонентов
available_weight = sum_of_nonzero_component_weights
# В бэктесте (только TA): available_weight = 0.45
# В live (все компоненты): available_weight = 0.45 + 0.25 + 0.20 + 0.10 = 1.0

# Эффективный порог
effective_threshold = threshold * available_weight

# Примеры:
# Backtest (only TA): 15 * 0.45 = 6.75
# Backtest (TA + FA): 15 * 0.70 = 10.50
# Live (all):         15 * 1.00 = 15.00
```

### Место применения

`SignalFilterPipeline.check_score_threshold()` -- добавить параметр `available_weight: float = 1.0` в контекст фильтра. `BacktestEngine._simulate_symbol()` передает `available_weight=0.45` через `filter_context`.

### Signal strength buckets

Пороги signal strength (`_get_signal_strength`) также масштабируются:
```python
# Backtest: STRONG_BUY >= 15 * 0.45 = 6.75 (вместо 15)
# Backtest: BUY >= 10 * 0.45 = 4.50 (вместо 10)
```

Это обеспечивается новой функцией `_get_signal_strength_scaled(composite, scale)` или передачей `available_weight` в существующую функцию.

---

## V6-REGIME-FIX: Исправление regime в trade_dicts

### Проблема

`regime` не включается в `trade_dicts` при сериализации (строки 747-764 backtest_engine.py). Поле `BacktestTradeResult.regime` корректно заполняется при создании объекта (строка 1143), но теряется при bulk insert.

### Исправление

Добавить `"regime": t.regime` в trade_dicts serialization (после строки 763).

---

## V6-D1-DATA: Загрузка D1 данных для trend filter

### Проблема

`d1_rows` всегда `[]` (строка 1066). SIM-27 D1 MA200 фильтр никогда не работает.

### Подход

В `_simulate()`, перед циклом по символам, загрузить D1 данные для каждого символа:
```python
d1_price_rows = await get_price_data(db, symbol, "D1", start_dt - timedelta(days=300), end_dt)
```

В `_simulate_symbol()`, для каждого сигнала на timestamp T, найти D1 свечи <= T (последние 200).

---

## V6-CALENDAR: Исправление экономического календаря

### Проблема

Phase 2 (calendar OFF) и Phase 3 (calendar ON) идентичны. Либо таблица economic_events пуста, либо ни одно событие не попадает в 2-часовое окно.

### Подход

1. Проверить содержимое таблицы economic_events
2. Если пуста -- создать скрипт для бэкфилла из ForexFactory/Investing.com
3. Добавить в backtest_engine логирование: "Calendar filter checked N events, blocked M signals"

---

## V6-SHORT: Улучшение качества SHORT-сигналов

### Проблема

SHORT WR 36.84%, PnL -$72.18. LONG WR 57.14%, PnL +$325.90.

### Подход: Комбинация (A) + (B)

- **(A) Асимметричные пороги:** SHORT требует `|composite| >= threshold * 1.2` (на 20% выше LONG)
- **(B) Строже momentum для SHORT:** RSI < 40 (вместо < 50) для SHORT

Реализуется через новые параметры:
```python
SHORT_SCORE_MULTIPLIER = Decimal("1.2")  # SHORT threshold = LONG threshold * 1.2
SHORT_RSI_THRESHOLD = 40  # вместо 50
```

Оба варианта сравниваются через backtest A/B. LONG-only mode (D) реализуется как fallback.

---

## V6-SPY: Параметры для SPY

### Проблема

SPY: 16.7% WR, -$114.36. Стабильно убыточен.

### Подход

Добавить SPY в INSTRUMENT_OVERRIDES:
```python
"SPY": {
    "min_composite_score": 25,
    "allowed_regimes": ["STRONG_TREND_BULL", "STRONG_TREND_BEAR"],
}
```

Если после REQ-V6-002 SPY все равно убыточен -- исключить из default symbols.

---

## V6-EXIT: Time Exit и MAE Exit в бэктесте

### Проблема

`_check_exit()` проверяет только SL/TP. Time exit и MAE exit из v5 спецификации не реализованы в бэктесте.

### Подход

Расширить `_check_exit()`:
1. Проверить количество свечей с момента входа. Если >= TIME_EXIT_CANDLES[timeframe] и unrealized_pnl <= 0 -- exit.
2. Проверить MAE: если MAE >= 60% SL distance и MFE < 20% TP distance за >= 3 свечи -- exit.

Порядок проверки: SL -> TP -> time_exit -> mae_exit.

---

## V6-EOD: Исключение end_of_data из метрик

### Проблема

4 trade в baseline закрываются 2025-12-31, вносят +$844.21 (80% gross wins). Это искусственные закрытия.

### Подход

В `_compute_summary()`:
- Основные метрики (WR, PF, avg_duration) считаются без `exit_reason="end_of_data"`
- Добавить отдельные поля: `end_of_data_count`, `end_of_data_pnl`
- Warning если end_of_data > 20% trades

---

## V6-REPORT: Автоматическая генерация отчетов

### Подход

`scripts/generate_backtest_report.py`:
- Читает из таблицы backtest_runs по run_id
- Генерирует markdown с run_id, параметрами, data hash
- Поддерживает сравнительную таблицу нескольких run_id

---

## V6-FILTER-STATS: Диагностика фильтров

### Подход

Добавить в `SignalFilterPipeline`:
```python
self.rejection_counts: dict[str, int] = defaultdict(int)
self.total_signals: int = 0
```

В `run_all()`: increment counters при каждом rejection. Включить `filter_stats` в backtest summary.

---

## V6-WALKFORWARD: Walk-Forward валидация

### Подход

Расширить `BacktestParams`:
```python
enable_walk_forward: bool = False
in_sample_months: int = 18
out_of_sample_months: int = 6
```

`BacktestEngine` запускает два прохода: IS (2024-01 -- 2025-06) и OOS (2025-07 -- 2025-12). Summary включает IS/OOS сравнение.

---

## Архитектурные решения

### 1. available_weight -- контрактное изменение filter_context

Новый ключ `available_weight` добавляется в filter_context dict. Default = 1.0 (live). Backtest передает 0.45. Это НЕ ломает существующий live код (default = 1.0 означает threshold * 1.0 = threshold).

### 2. Signal strength scaling -- единая точка изменения

Функции `_get_signal_strength()` в обоих файлах (filter_pipeline.py и backtest_engine.py) заменяются на `_get_signal_strength_scaled(composite, scale=1.0)`. При scale=0.45 пороги: STRONG_BUY >= 6.75, BUY >= 4.5.

### 3. D1 данные -- lazy loading per symbol

D1 данные загружаются один раз per symbol в `_simulate()` и передаются в `_simulate_symbol()`. Не кешируются между символами (разные инструменты).

### 4. Backward compatibility

Все новые поля в BacktestParams -- Optional с defaults. Все новые поля в summary -- дополнительные ключи (не удаляем существующие). Старые backtest runs читаются без ошибок.
