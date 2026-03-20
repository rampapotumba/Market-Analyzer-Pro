# Tasks v6 -- Data Integrity, Score Calibration, Filter Activation

## Дата: 2026-03-20
## Спецификация: `docs/SPEC_SIMULATOR_V6.md`
## Требования: `analyst-requirements/backtest-final-analysis-requirements.md`

---

## Приоритет P0 -- Фундамент

---

### TASK-V6-01: Автоматическая генерация отчетов бэктеста
- [x] Выполнено

**REQ:** REQ-V6-001

**Описание:** Создать скрипт `scripts/generate_backtest_report.py`, который читает результаты из таблицы `backtest_runs` по run_id и генерирует markdown-отчет. Устраняет ручное редактирование метрик и расхождения между файлами отчетов.

**Файлы:**
- `scripts/generate_backtest_report.py` -- СОЗДАТЬ
- `src/backtesting/backtest_engine.py` -- добавить data hash в summary

**Шаги реализации:**
1. Создать `scripts/generate_backtest_report.py`:
   - CLI: `python scripts/generate_backtest_report.py --run-id <id> [--run-id <id2>] [--output docs/REPORT.md]`
   - Подключение к БД через `DATABASE_URL` из `.env`
   - Чтение из таблицы `backtest_runs`: `params_json`, `summary_json`, `created_at`
   - Чтение из `backtest_trades` для детализации по символу/режиму
   - Генерация markdown с секциями: Parameters, Summary, By Symbol, By Regime, Equity Curve
2. Поддержка сравнительной таблицы при передаче нескольких `--run-id`:
   ```python
   # | Metric        | Run A   | Run B   | Delta   |
   # |---------------|---------|---------|---------|
   # | Trades        | 33      | 80      | +47     |
   ```
3. В `backtest_engine.py` `_compute_summary()` -- добавить `data_hash`:
   ```python
   import hashlib
   trade_data = json.dumps(trade_dicts, sort_keys=True, default=str)
   summary["data_hash"] = hashlib.sha256(trade_data.encode()).hexdigest()[:16]
   ```
4. Каждый отчет содержит: run_id, дату запуска, полный набор параметров, data_hash

**Тесты:**
- `test_v6_01_report_single_run` -- генерация отчета для одного run_id (мок БД)
- `test_v6_01_report_comparison` -- сравнительная таблица двух run_id
- `test_v6_01_data_hash_deterministic` -- один и тот же набор trades дает одинаковый hash

**Зависимости:** Нет

**Критерии приемки:**
- [ ] Скрипт существует и запускается без ошибок
- [ ] Сгенерированный markdown содержит run_id, параметры, data_hash
- [ ] Сравнительная таблица корректно вычисляет дельты
- [ ] data_hash детерминистичен

---

### TASK-V6-02: Пропорциональное масштабирование composite score в бэктесте
- [x] Выполнено

**REQ:** REQ-V6-002

**Описание:** Реализовать формулу `effective_threshold = threshold * available_weight`. В бэктесте (только TA, weight=0.45) порог 15 становится 6.75. Это ключевое исправление для увеличения количества сделок с 33 до >= 80.

**Файлы:**
- `src/signals/filter_pipeline.py` -- изменить `check_score_threshold()`, `_get_signal_strength()`
- `src/backtesting/backtest_engine.py` -- передавать `available_weight=0.45` в filter_context; изменить `_get_signal_strength()`
- `src/config.py` -- добавить `SCORE_COMPONENT_WEIGHTS` dict

**Шаги реализации:**
1. В `src/config.py` добавить:
   ```python
   SCORE_COMPONENT_WEIGHTS = {
       "ta": 0.45,
       "fa": 0.25,
       "sentiment": 0.20,
       "geo": 0.10,
   }
   ```
2. В `filter_pipeline.py` -- изменить `_get_signal_strength()` на `_get_signal_strength_scaled()`:
   ```python
   def _get_signal_strength_scaled(composite: float, scale: float = 1.0) -> str:
       """Signal strength с масштабированными порогами."""
       abs_score = abs(composite)
       if abs_score >= 15 * scale:
           return "STRONG_BUY" if composite > 0 else "STRONG_SELL"
       elif abs_score >= 10 * scale:
           return "BUY" if composite > 0 else "SELL"
       elif abs_score >= 7 * scale:
           return "WEAK_BUY" if composite > 0 else "WEAK_SELL"
       return "NEUTRAL"
   ```
3. В `filter_pipeline.py` `SignalFilterPipeline` -- метод `check_score_threshold()`:
   - Читать `available_weight` из `filter_context` (default=1.0)
   - `effective_threshold = threshold * available_weight`
   - Сравнивать `abs(composite) >= effective_threshold`
4. В `backtest_engine.py` `_simulate_symbol()`:
   - В `filter_context` добавить `"available_weight": 0.45`
   - Заменить вызовы `_get_signal_strength()` на `_get_signal_strength_scaled(composite, scale=0.45)`
5. Проверить что INSTRUMENT_OVERRIDES `min_composite_score` тоже масштабируется:
   - `effective_override = override_score * available_weight`

**Тесты:**
- `test_v6_02_scaled_threshold_backtest` -- порог 15 при scale=0.45 дает effective=6.75
- `test_v6_02_scaled_threshold_live` -- порог 15 при scale=1.0 дает effective=15.0
- `test_v6_02_signal_strength_scaled` -- STRONG_BUY при composite=7.0 и scale=0.45
- `test_v6_02_instrument_override_scaled` -- override 20 при scale=0.45 = 9.0
- `test_v6_02_crypto_threshold_scaled` -- crypto override 20 * 0.45 = 9.0

**Зависимости:** Нет

**Критерии приемки:**
- [ ] В бэктесте `effective_threshold = 15 * 0.45 = 6.75`
- [ ] В live `effective_threshold = 15 * 1.0 = 15`
- [ ] Signal strength пороги масштабируются одинаково
- [ ] INSTRUMENT_OVERRIDES масштабируются
- [ ] Бэктест выдает >= 80 trades за 24 месяца

---

### TASK-V6-03: Разблокировка BTC/USDT (расширение allowed_regimes)
- [x] Выполнено

**REQ:** REQ-V6-003

**Описание:** Расширить `allowed_regimes` для BTC/USDT, добавив `TREND_BULL` и `TREND_BEAR`. Снизить `min_composite_score` с 20 до 15 (масштабирование из TASK-V6-02 сделает остальное).

**Файлы:**
- `src/config.py` -- изменить `INSTRUMENT_OVERRIDES["BTC/USDT"]`

**Шаги реализации:**
1. В `src/config.py` изменить:
   ```python
   "BTC/USDT": {
       "sl_atr_multiplier": 3.5,
       "min_composite_score": 15,  # было 20
       "allowed_regimes": [
           "STRONG_TREND_BULL", "STRONG_TREND_BEAR",
           "TREND_BULL", "TREND_BEAR",  # добавлено
       ],
   },
   ```
2. Аналогично для ETH/USDT -- снизить `min_composite_score` с 20 до 15:
   ```python
   "ETH/USDT": {
       "sl_atr_multiplier": 3.5,
       "min_composite_score": 15,  # было 20
   },
   ```

**Тесты:**
- `test_v6_03_btc_allowed_regimes_expanded` -- TREND_BULL разрешен для BTC
- `test_v6_03_btc_threshold_lowered` -- min_composite_score = 15

**Зависимости:** TASK-V6-02 (масштабирование порогов)

**Критерии приемки:**
- [ ] BTC/USDT >= 3 trades в 24-month бэктесте
- [ ] BTC trades имеют PF >= 1.0

---

## Приоритет P1 -- Фильтры и качество сигналов

---

### TASK-V6-04: Исправление GBPUSD (удаление завышенного override)
- [x] Выполнено

**REQ:** REQ-V6-004

**Описание:** Удалить `min_composite_score: 20` override для GBPUSD=X. После TASK-V6-02 глобальный порог 15 * 0.45 = 6.75 будет достаточным.

**Файлы:**
- `src/config.py` -- изменить `INSTRUMENT_OVERRIDES["GBPUSD=X"]`

**Шаги реализации:**
1. Удалить или закомментировать override для GBPUSD=X:
   ```python
   # "GBPUSD=X": {
   #     "min_composite_score": 20,
   # },
   ```
   Или оставить пустой dict если нужны будущие настройки.

**Тесты:**
- `test_v6_04_gbpusd_no_score_override` -- GBPUSD использует глобальный порог
- `test_v6_04_gbpusd_generates_trades` -- интеграционный тест с бэктестом (мок)

**Зависимости:** TASK-V6-02

**Критерии приемки:**
- [ ] GBPUSD >= 3 trades в бэктесте
- [ ] GBPUSD trades не ухудшают общий PF ниже 1.3

---

### TASK-V6-05: Исправление regime в trade_dicts (V6-REGIME-FIX)
- [x] Выполнено

**REQ:** REQ-V6-009

**Описание:** Поле `regime` теряется при сериализации trade_dicts в backtest_engine.py. Нужно добавить `"regime": t.regime` в dict serialization.

**Файлы:**
- `src/backtesting/backtest_engine.py` -- добавить regime в trade_dicts

**Шаги реализации:**
1. Найти serialization trade_dicts (около строки 747-764 в backtest_engine.py)
2. Добавить `"regime": t.regime` в dict:
   ```python
   trade_dict = {
       "symbol": t.symbol,
       # ... existing fields ...
       "regime": t.regime,  # <-- ДОБАВИТЬ
   }
   ```
3. Проверить что `BacktestTradeResult.regime` заполняется при создании (строка ~1143)

**Тесты:**
- `test_v6_05_regime_persisted_in_trade_dict` -- regime не None и не "UNKNOWN"
- `test_v6_05_by_regime_summary_has_real_names` -- by_regime содержит реальные имена

**Зависимости:** Нет

**Критерии приемки:**
- [ ] Нет trades с regime="UNKNOWN" в новых бэктест-прогонах
- [ ] by_regime summary содержит реальные имена режимов

---

### TASK-V6-06: Загрузка D1 данных для trend filter (V6-D1-DATA)
- [x] Выполнено

**REQ:** REQ-V6-005

**Описание:** `d1_rows` всегда `[]` в бэктесте. SIM-27 D1 MA200 фильтр никогда не работает. Нужно загрузить D1 данные для каждого символа перед симуляцией.

**Файлы:**
- `src/backtesting/backtest_engine.py` -- загрузка D1 данных в `_simulate()`, передача в `_simulate_symbol()`

**Шаги реализации:**
1. В `_simulate()`, перед циклом по символам:
   ```python
   from datetime import timedelta

   # Pre-load D1 data per symbol for MA200 filter
   d1_data_cache: dict[str, list] = {}
   if params.apply_d1_trend_filter:
       for symbol in params.symbols:
           d1_rows = await get_price_data(
               db, symbol, "D1",
               start_dt - timedelta(days=300),  # 300 дней для MA200
               end_dt
           )
           d1_data_cache[symbol] = d1_rows
           logger.info(f"D1 data for {symbol}: {len(d1_rows)} rows")
   ```
2. В `_simulate_symbol()` -- передать d1_rows из cache:
   ```python
   # Для каждого сигнала на timestamp T:
   d1_candles = [r for r in d1_data_cache.get(symbol, []) if r.timestamp <= signal_ts][-200:]
   filter_context["d1_rows"] = d1_candles
   ```
3. Добавить логирование в filter_pipeline при pass/block D1 фильтра:
   ```python
   logger.info(f"D1 MA200 filter: close={close}, ma200={ma200}, decision={'pass' if passed else 'block'}")
   ```

**Тесты:**
- `test_v6_06_d1_data_loaded` -- d1_rows содержит >= 200 строк при наличии данных
- `test_v6_06_d1_filter_blocks_counter_trend` -- LONG блокируется при close < MA200
- `test_v6_06_d1_filter_graceful_no_data` -- при отсутствии D1 данных фильтр пропускается

**Зависимости:** Нет

**Критерии приемки:**
- [ ] `d1_rows` содержит >= 200 строк когда D1 данные доступны
- [ ] Фильтр логирует pass/block решения с значениями close и MA200
- [ ] Бэктест с `apply_d1_trend_filter=true` выдает меньше trades чем `false`

---

### TASK-V6-07: Исправление экономического календаря (V6-CALENDAR)
- [x] Выполнено

**REQ:** REQ-V6-006

**Описание:** Phase 2 (calendar OFF) и Phase 3 (calendar ON) идентичны. Фильтр календаря не работает. Нужно провести аудит данных и исправить.

**Файлы:**
- `src/backtesting/backtest_engine.py` -- логирование calendar filter
- `src/signals/filter_pipeline.py` -- диагностика calendar check
- `scripts/load_economic_calendar.py` -- проверить/расширить

**Шаги реализации:**
1. Проверить содержимое таблицы `economic_events`:
   - Сколько HIGH-impact событий за 2024-2025?
   - Какие валютные пары покрыты?
   - Формат timestamp (UTC? timezone-aware?)
2. Добавить диагностическое логирование в `filter_pipeline.py` `_check_calendar()`:
   ```python
   logger.info(f"Calendar check: signal_ts={ts}, events_in_window={len(events)}, "
               f"nearest_event={nearest}, blocked={not passed}")
   ```
3. Если таблица пуста или содержит < 50 событий/год -- расширить скрипт загрузки
4. Проверить окно: текущее 2 часа может быть слишком узким для H1 сигналов. Рассмотреть расширение до 4 часов для high-impact
5. Добавить per-run счетчик: `"calendar_filter_blocked": N` в summary

**Тесты:**
- `test_v6_07_calendar_blocks_near_nfp` -- сигнал за 1 час до NFP блокируется
- `test_v6_07_calendar_passes_no_events` -- нет событий -- фильтр пропускает
- `test_v6_07_calendar_logging` -- проверить что логирование работает

**Зависимости:** Нет

**Критерии приемки:**
- [ ] Таблица economic_events содержит >= 50 HIGH-impact событий в год
- [ ] Calendar filter блокирует >= 1 trade в 24-month бэктесте
- [ ] Заблокированные trades логируются с именем события и timestamp

---

### TASK-V6-08: Улучшение качества SHORT-сигналов
- [x] Выполнено

**REQ:** REQ-V6-007

**Описание:** SHORT WR 36.84%, PnL -$72.18. Реализовать асимметричные пороги (SHORT * 1.2) и строже momentum для SHORT (RSI < 40).

**Файлы:**
- `src/config.py` -- добавить `SHORT_SCORE_MULTIPLIER`, `SHORT_RSI_THRESHOLD`
- `src/signals/filter_pipeline.py` -- изменить `check_score_threshold()` и `_check_momentum()`
- `src/backtesting/backtest_engine.py` -- пробросить параметры

**Шаги реализации:**
1. В `src/config.py` добавить:
   ```python
   SHORT_SCORE_MULTIPLIER = Decimal("1.2")  # SHORT threshold = LONG threshold * 1.2
   SHORT_RSI_THRESHOLD = 40  # SHORT: RSI < 40 (вместо < 50)
   ```
2. В `filter_pipeline.py` `check_score_threshold()`:
   ```python
   effective_threshold = base_threshold * available_weight
   if direction == "SHORT":
       effective_threshold *= SHORT_SCORE_MULTIPLIER
   ```
3. В `filter_pipeline.py` `_check_momentum()`:
   ```python
   # SHORT: RSI < SHORT_RSI_THRESHOLD (40) вместо < 50
   if direction == "SHORT":
       if rsi >= SHORT_RSI_THRESHOLD:
           return False
   ```
4. Добавить `BacktestParams` поля для A/B тестирования:
   ```python
   short_score_multiplier: Optional[float] = None  # None = use config
   short_rsi_threshold: Optional[int] = None        # None = use config
   ```

**Тесты:**
- `test_v6_08_short_threshold_multiplied` -- SHORT с composite=-14 блокируется при threshold=15*1.2=18
- `test_v6_08_short_rsi_40` -- SHORT с RSI=45 блокируется (< 50 но >= 40)
- `test_v6_08_long_unaffected` -- LONG пороги не изменились

**Зависимости:** TASK-V6-02 (масштабирование)

**Критерии приемки:**
- [ ] SHORT PnL >= $0, ИЛИ общий PF >= 1.5 при текущем SHORT
- [ ] LONG пороги не затронуты

---

### TASK-V6-09: SPY -- instrument override
- [x] Выполнено

**REQ:** REQ-V6-008

**Описание:** SPY имеет 16.7% WR и -$114. Добавить строгий override. Если после override все равно убыточен -- исключить из default symbols.

**Файлы:**
- `src/config.py` -- добавить `INSTRUMENT_OVERRIDES["SPY"]`

**Шаги реализации:**
1. В `src/config.py` добавить:
   ```python
   "SPY": {
       "min_composite_score": 25,
       "allowed_regimes": ["STRONG_TREND_BULL", "STRONG_TREND_BEAR"],
   },
   ```
2. Запустить бэктест с новым override
3. Если SPY PnL < 0 -- добавить SPY в `EXCLUDED_SYMBOLS` list (или удалить из default backtest symbols)

**Тесты:**
- `test_v6_09_spy_override_applied` -- SPY использует min_composite_score=25
- `test_v6_09_spy_regime_restricted` -- SPY RANGING блокируется

**Зависимости:** TASK-V6-02

**Критерии приемки:**
- [ ] SPY PnL >= $0 после тюнинга, ИЛИ SPY исключен с документированным обоснованием
- [ ] Нет регрессии в других инструментах

---

## Приоритет P2 -- Диагностика, расширение, валидация

---

### TASK-V6-10: Диагностика фильтров (filter_stats)
- [x] Выполнено

**REQ:** REQ-V6-010

**Описание:** Добавить счетчики отклонений по каждому фильтру в `SignalFilterPipeline`. Включить `filter_stats` в backtest summary.

**Файлы:**
- `src/signals/filter_pipeline.py` -- добавить `rejection_counts`, `total_signals`
- `src/backtesting/backtest_engine.py` -- включить filter_stats в summary

**Шаги реализации:**
1. В `SignalFilterPipeline.__init__()`:
   ```python
   from collections import defaultdict
   self.rejection_counts: dict[str, int] = defaultdict(int)
   self.total_signals: int = 0
   self.passed_signals: int = 0
   ```
2. В `run_all()` -- increment при каждом rejection:
   ```python
   self.total_signals += 1
   for filter_name, check_fn in self._filters:
       if not check_fn(...):
           self.rejection_counts[filter_name] += 1
           return False, filter_name
   self.passed_signals += 1
   return True, None
   ```
3. Добавить метод `get_stats() -> dict`:
   ```python
   def get_stats(self) -> dict:
       return {
           "total_raw_signals": self.total_signals,
           **{f"rejected_by_{k}": v for k, v in self.rejection_counts.items()},
           "passed_all": self.passed_signals,
       }
   ```
4. В `backtest_engine.py` `_compute_summary()` -- добавить:
   ```python
   summary["filter_stats"] = pipeline.get_stats()
   ```

**Тесты:**
- `test_v6_10_filter_stats_counts` -- сумма rejections + passed = total
- `test_v6_10_filter_stats_in_summary` -- summary содержит filter_stats dict

**Зависимости:** Нет

**Критерии приемки:**
- [ ] Backtest summary включает filter_stats dict
- [ ] Сумма всех rejections + passed = total_raw_signals

---

### TASK-V6-11: Time Exit и MAE Exit в бэктесте (V6-EXIT)
- [x] Выполнено

**REQ:** REQ-V6-013

**Описание:** `_check_exit()` проверяет только SL/TP. Добавить time exit и MAE exit.

**Файлы:**
- `src/backtesting/backtest_engine.py` -- расширить `_check_exit()`

**Шаги реализации:**
1. В `_check_exit()` после проверки SL/TP добавить:
   ```python
   # Time exit: close after N candles if PnL <= 0
   TIME_EXIT_CANDLES = {"H1": 48, "H4": 20, "D1": 10}
   candles_since_entry = current_bar_index - entry_bar_index
   max_candles = TIME_EXIT_CANDLES.get(timeframe, 48)
   if candles_since_entry >= max_candles:
       unrealized_pnl = _calc_unrealized_pnl(...)
       if unrealized_pnl <= Decimal("0"):
           return "time_exit", current_price
   ```
2. MAE exit:
   ```python
   # MAE exit: if MAE >= 60% SL distance AND MFE < 20% TP distance AND candles >= 3
   mae_threshold = sl_distance * Decimal("0.60")
   mfe_threshold = tp_distance * Decimal("0.20")
   if (current_mae >= mae_threshold and
       current_mfe < mfe_threshold and
       candles_since_entry >= 3):
       return "mae_exit", current_price
   ```
3. Порядок проверки: SL -> TP -> time_exit -> mae_exit

**Тесты:**
- `test_v6_11_time_exit_triggers` -- позиция закрывается после 48 свечей H1 при PnL <= 0
- `test_v6_11_time_exit_no_trigger_positive_pnl` -- не закрывается при PnL > 0
- `test_v6_11_mae_exit_triggers` -- MAE >= 60% SL, MFE < 20% TP, >= 3 свечи
- `test_v6_11_mae_exit_no_trigger_mfe_high` -- MFE >= 20% предотвращает MAE exit

**Зависимости:** Нет

**Критерии приемки:**
- [ ] >= 1 time exit в 24-month бэктесте
- [ ] >= 1 mae exit в 24-month бэктесте (или документировано почему не срабатывает)
- [ ] Порядок проверки: SL -> TP -> time_exit -> mae_exit

---

### TASK-V6-12: Исключение end_of_data из метрик (V6-EOD)
- [x] Выполнено

**REQ:** REQ-V6-014

**Описание:** 4 trade в baseline закрываются 2025-12-31 с exit_reason="end_of_data", вносят +$844.21. Это искусственные закрытия, искажающие метрики.

**Файлы:**
- `src/backtesting/backtest_engine.py` -- изменить `_compute_summary()`

**Шаги реализации:**
1. В `_compute_summary()`:
   ```python
   # Separate end_of_data trades
   eod_trades = [t for t in trades if t.exit_reason == "end_of_data"]
   real_trades = [t for t in trades if t.exit_reason != "end_of_data"]

   # Compute primary metrics from real_trades only
   win_rate = _calc_win_rate(real_trades)
   profit_factor = _calc_pf(real_trades)
   avg_duration = _calc_avg_duration(real_trades)
   ```
2. Добавить отдельные поля в summary:
   ```python
   summary["end_of_data_count"] = len(eod_trades)
   summary["end_of_data_pnl"] = sum(t.pnl_usd for t in eod_trades)
   summary["total_trades_excl_eod"] = len(real_trades)
   ```
3. Warning если end_of_data > 20% trades:
   ```python
   if len(eod_trades) / max(len(trades), 1) > 0.20:
       logger.warning(f"end_of_data trades: {len(eod_trades)}/{len(trades)} "
                      f"({len(eod_trades)/len(trades)*100:.1f}%) -- metrics may be unreliable")
   ```

**Тесты:**
- `test_v6_12_eod_excluded_from_wr` -- WR считается без end_of_data trades
- `test_v6_12_eod_excluded_from_pf` -- PF считается без end_of_data trades
- `test_v6_12_eod_separate_section` -- summary содержит end_of_data_count и end_of_data_pnl
- `test_v6_12_eod_warning_threshold` -- warning при > 20%

**Зависимости:** Нет

**Критерии приемки:**
- [ ] Primary метрики (WR, PF, avg_duration) исключают end_of_data trades
- [ ] Summary содержит отдельную секцию end_of_data
- [ ] Warning при > 20% end_of_data trades

---

### TASK-V6-13: Walk-Forward валидация (V6-WALKFORWARD)
- [x] Выполнено

**REQ:** REQ-V6-015

**Описание:** Добавить поддержку walk-forward: IS (2024-01 -- 2025-06) и OOS (2025-07 -- 2025-12) с раздельными метриками.

**Файлы:**
- `src/backtesting/backtest_params.py` -- добавить `enable_walk_forward`, `in_sample_months`, `out_of_sample_months`
- `src/backtesting/backtest_engine.py` -- два прохода IS/OOS
- `src/backtesting/backtest_params.py` -- расширить `BacktestResult`

**Шаги реализации:**
1. В `BacktestParams` добавить:
   ```python
   enable_walk_forward: bool = False
   in_sample_months: int = 18
   out_of_sample_months: int = 6
   ```
2. В `BacktestResult` добавить:
   ```python
   walk_forward: Optional[dict] = None  # {"is": {...}, "oos": {...}, "comparison": {...}}
   ```
3. В `backtest_engine.py` -- если `enable_walk_forward`:
   ```python
   # Split period
   is_end = start_dt + relativedelta(months=params.in_sample_months)
   oos_start = is_end
   oos_end = oos_start + relativedelta(months=params.out_of_sample_months)

   # Run IS
   is_trades = await self._simulate(db, params_is)
   is_summary = self._compute_summary(is_trades)

   # Run OOS
   oos_trades = await self._simulate(db, params_oos)
   oos_summary = self._compute_summary(oos_trades)

   # Comparison
   summary["walk_forward"] = {
       "in_sample": is_summary,
       "out_of_sample": oos_summary,
       "wr_delta": oos_wr - is_wr,
       "pf_delta": oos_pf - is_pf,
   }
   ```

**Тесты:**
- `test_v6_13_walk_forward_splits_correctly` -- IS=18 months, OOS=6 months
- `test_v6_13_walk_forward_separate_metrics` -- IS и OOS имеют независимые WR/PF
- `test_v6_13_walk_forward_disabled_by_default` -- без флага walk_forward=None

**Зависимости:** TASK-V6-12 (корректные метрики)

**Критерии приемки:**
- [ ] OOS PF >= 1.0
- [ ] OOS WR в пределах 10% от IS WR
- [ ] Summary включает IS/OOS сравнение

---

### TASK-V6-14: Multi-Timeframe бэктест
- [ ] Выполнено

**REQ:** REQ-V6-011

**Описание:** Бэктест работает только на H1. Добавить поддержку списка таймфреймов с correlation guard.

**Файлы:**
- `src/backtesting/backtest_params.py` -- `timeframe: str` -> `timeframes: list[str]` (с backward compat)
- `src/backtesting/backtest_engine.py` -- цикл по timeframes, correlation guard

**Шаги реализации:**
1. В `BacktestParams`:
   ```python
   timeframes: list[str] = ["H1"]  # новое поле
   timeframe: str = "H1"           # backward compat, deprecated

   @model_validator(mode="after")
   def sync_timeframes(self):
       if len(self.timeframes) == 1 and self.timeframes[0] == "H1":
           self.timeframes = [self.timeframe]
       return self
   ```
2. В `_simulate()` -- вложенный цикл:
   ```python
   for symbol in params.symbols:
       for tf in params.timeframes:
           trades = await self._simulate_symbol(db, symbol, tf, ...)
           all_trades.extend(trades)
   ```
3. Correlation guard: не открывать позицию если уже есть открытая для того же symbol на другом TF
4. В summary добавить `by_timeframe` breakdown

**Тесты:**
- `test_v6_14_multi_tf_runs` -- H1+H4 в одном прогоне
- `test_v6_14_correlation_guard_cross_tf` -- одна позиция per symbol across TFs
- `test_v6_14_by_timeframe_breakdown` -- summary содержит per-TF stats
- `test_v6_14_backward_compat_single_tf` -- старый `timeframe="H1"` работает

**Зависимости:** Нет

**Критерии приемки:**
- [ ] Бэктест может запускать H1+H4 в одном прогоне
- [ ] Нет дублирующих входов для одного price move на разных TF
- [ ] Результаты разбиты по timeframe

---

### TASK-V6-15: Расширение набора инструментов
- [ ] Выполнено

**REQ:** REQ-V6-012

**Описание:** Добавить USDJPY=X, NZDUSD=X, USDCAD=X и золото (GC=F). Обеспечить сбор данных и добавить в default backtest symbols.

**Файлы:**
- `src/config.py` -- добавить `DEFAULT_BACKTEST_SYMBOLS` list
- `scripts/fetch_historical.py` -- добавить новые символы
- `src/backtesting/backtest_engine.py` -- использовать DEFAULT_BACKTEST_SYMBOLS

**Шаги реализации:**
1. В `src/config.py`:
   ```python
   DEFAULT_BACKTEST_SYMBOLS = [
       "EURUSD=X", "GBPUSD=X", "AUDUSD=X", "USDJPY=X", "NZDUSD=X", "USDCAD=X",
       "BTC/USDT", "ETH/USDT",
       "SPY", "GC=F",
   ]
   ```
2. Проверить `scripts/fetch_historical.py` поддерживает новые символы
3. Добавить INSTRUMENT_OVERRIDES для новых символов при необходимости (GC=F -- gold)
4. Запустить сбор исторических данных для новых инструментов

**Тесты:**
- `test_v6_15_new_symbols_in_defaults` -- все 10 символов в DEFAULT_BACKTEST_SYMBOLS
- `test_v6_15_new_symbol_overrides` -- GC=F имеет корректные параметры

**Зависимости:** TASK-V6-02, TASK-V6-03, TASK-V6-04

**Критерии приемки:**
- [ ] >= 3 новых инструмента с >= 12 месяцев H1 данных
- [ ] Новые инструменты генерируют trades в бэктесте
- [ ] Общий PF не падает ниже 1.3

---

## Порядок выполнения (рекомендуемый)

### Phase 1: Фундамент (P0)
1. **TASK-V6-01** -- reporting pipeline (основа для валидации)
2. **TASK-V6-02** -- composite score scaling (ключевое исправление)
3. **TASK-V6-03** -- BTC unblock

   --> Запустить бэктест, зафиксировать результаты через TASK-V6-01

### Phase 2: Instrument Coverage (P1)
4. **TASK-V6-04** -- GBPUSD fix
5. **TASK-V6-05** -- regime persistence
6. **TASK-V6-09** -- SPY override

   --> Бэктест + отчет

### Phase 3: Filter Fixes (P1)
7. **TASK-V6-06** -- D1 data loading
8. **TASK-V6-07** -- calendar fix
9. **TASK-V6-08** -- SHORT quality

   --> Бэктест + отчет

### Phase 4: Diagnostics & Exits (P2)
10. **TASK-V6-10** -- filter diagnostics
11. **TASK-V6-11** -- time/MAE exit
12. **TASK-V6-12** -- end_of_data exclusion

    --> Бэктест + отчет

### Phase 5: Scale & Validate (P2)
13. **TASK-V6-13** -- walk-forward
14. **TASK-V6-14** -- multi-timeframe
15. **TASK-V6-15** -- instrument expansion

    --> Финальный бэктест + walk-forward отчет

---

## Метрики успеха v6

| Метрика | v5 Actual | v6 Target | Критично |
|---------|-----------|-----------|----------|
| Trades/24mo | 33 | >= 80 | Да |
| Win Rate | 45.45% | >= 45% | Да |
| Profit Factor | 2.01 | >= 1.5 | Да |
| Max Drawdown | < 10% | <= 20% | Нет |
| Instruments w/ trades | 4/6 | 6/6 | Да |
| OOS PF | N/A | >= 1.0 | Да |
| SHORT PnL | -$72.18 | >= $0 | Нет |
