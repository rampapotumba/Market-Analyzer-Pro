# Консолидированные задачи v6 -- Финальный список

## Дата: 2026-03-20
## Статус: Активный

---

## Обзор

Все предыдущие задачи v6, v6-cal, v6-cal-r2, v6-cal-r3 собраны в один файл.
Завершённые задачи убраны. Остались только открытые задачи, отсортированные по приоритету.

**Источники (архив):**
- `docs/TASKS_V6.md` -- 13/15 выполнено
- `docs/TASKS_V6_CALIBRATION.md` -- 9/11 выполнено
- `docs/TASKS_V6_CAL_R2.md` -- 8/10 выполнено
- `docs/TASKS_V6_CAL_R3.md` -- 6/8 выполнено

---

## P0 -- Производительность бэктеста (блокирует итерации)

Бэктест ~5 часов (10 инструментов x 21 месяц H1). До добавления DXY -- ~50 минут.
Каждый раунд калибровки требует бэктеста. 5 часов на прогон = невозможно итерировать.

---

### OPT-01: Устранение O(n) list comprehension для D1 фильтра в горячем цикле

- **Приоритет:** P0
- **Проблема:** Строка 1489 `backtest_engine.py`:
  ```python
  d1_rows_for_filter = [r for r in _d1_all if r.timestamp <= candle_ts][-200:]
  ```
  Вызывается на КАЖДОЙ свече (до ~12,000 раз на символ). `_d1_all` содержит ~500 D1 строк.
  12,000 x 500 = 6,000,000 сравнений timestamps на символ. При 10 символах = 60M операций.

- **Решение:**
  1. D1 данные уже отсортированы по timestamp (из БД `ORDER BY timestamp`).
  2. Перед циклом: построить отсортированный массив D1 timestamps.
  3. В цикле: использовать `bisect.bisect_right(d1_timestamps, candle_ts)` для O(log n) поиска индекса.
  4. Вместо list comprehension: `d1_rows_slice = d1_rows_all[max(0, idx-200):idx]`.

- **Файлы:**
  - `src/backtesting/backtest_engine.py` -- `_simulate_symbol()`, строки 1486-1489

- **Точные изменения:**
  ```python
  # ПЕРЕД ЦИКЛОМ (после строки 1380, до `for i in range(...)`):
  import bisect
  _d1_all = d1_rows_all or []
  _d1_timestamps = [r.timestamp for r in _d1_all]  # уже отсортированы

  # В ЦИКЛЕ (заменить строки 1488-1489):
  # БЫЛО:
  # d1_rows_for_filter = [r for r in _d1_all if r.timestamp <= candle_ts][-200:]
  # СТАЛО:
  _d1_idx = bisect.bisect_right(_d1_timestamps, candle_ts)
  d1_rows_for_filter = _d1_all[max(0, _d1_idx - 200):_d1_idx]
  ```

- **Ожидаемый эффект:** ~60M сравнений -> ~120K * log2(500) ~= 1M операций. Улучшение ~60x для D1 lookup.

- **Тесты:**
  - `test_opt_01_d1_bisect_same_result` -- результат bisect идентичен list comprehension
  - `test_opt_01_d1_bisect_empty` -- пустой d1_rows -> пустой результат
  - `test_opt_01_d1_bisect_boundary` -- candle_ts == d1 timestamp -> корректный включающий/исключающий range

- **Оценка:** 1 час

---

### OPT-02: Устранение per-candle _to_utc() для DXY RSI lookup

- **Приоритет:** P0
- **Проблема:** Строка 1508 `backtest_engine.py`:
  ```python
  "dxy_rsi": (dxy_rsi_by_ts or {}).get(_to_utc(price_rows[idx].timestamp)),
  ```
  `_to_utc()` вызывается на КАЖДОЙ свече: ~12,000 раз на символ, 10 символов = 120,000 вызовов.
  Каждый вызов создает новый datetime object через `ts.replace(tzinfo=...)` или `ts.astimezone(...)`.

  Кроме того, точный timestamp-match может промахиваться (DXY и symbol timestamps могут не совпадать
  с точностью до секунды), приводя к `None` -> graceful degradation -> фильтр не работает.

- **Решение:**
  1. Предвычислить DXY RSI lookup-массив, совпадающий с индексами price_rows.
  2. Перед циклом по свечам: для каждого price_rows[i] найти ближайший DXY RSI timestamp
     через bisect (nearest previous, не exact match).
  3. Хранить как `dxy_rsi_at_idx: list[Optional[float]]` длиной n.
  4. В цикле: `dxy_rsi_at_idx[idx]` -- O(1) lookup без создания объектов.

- **Файлы:**
  - `src/backtesting/backtest_engine.py` -- `_simulate_symbol()`, строки ~1300-1310 (pre-loop) и 1508

- **Точные изменения:**
  ```python
  # ПЕРЕД ЦИКЛОМ (после pre-computation блока, ~строка 1379):
  # Pre-map DXY RSI to price_rows indices using bisect for nearest-previous lookup.
  dxy_rsi_at_idx: list[Optional[float]] = [None] * n
  if dxy_rsi_by_ts:
      _dxy_ts_sorted = sorted(dxy_rsi_by_ts.keys())
      _dxy_rsi_sorted = [dxy_rsi_by_ts[ts] for ts in _dxy_ts_sorted]
      for _pi in range(n):
          _pts = _to_utc(price_rows[_pi].timestamp)
          _di = bisect.bisect_right(_dxy_ts_sorted, _pts) - 1
          if _di >= 0:
              dxy_rsi_at_idx[_pi] = _dxy_rsi_sorted[_di]

  # В ЦИКЛЕ (заменить строку 1508):
  # БЫЛО:
  # "dxy_rsi": (dxy_rsi_by_ts or {}).get(_to_utc(price_rows[idx].timestamp)),
  # СТАЛО:
  "dxy_rsi": dxy_rsi_at_idx[idx],
  ```

- **Дополнительное улучшение:** `_to_utc()` теперь вызывается n раз (один раз per price row, а не per candle x per lookup). Можно предвычислить UTC timestamps для price_rows тоже.

- **Ожидаемый эффект:** 120K dict lookups с datetime creation -> 120K array index lookups. Плюс nearest-match вместо exact match (исправляет потенциальные промахи).

- **Тесты:**
  - `test_opt_02_dxy_rsi_mapped_to_idx` -- dxy_rsi_at_idx содержит значения для candles с совпадающими timestamps
  - `test_opt_02_dxy_nearest_previous` -- при несовпадении timestamp берётся ближайший предыдущий
  - `test_opt_02_dxy_empty` -- пустой dxy_rsi_by_ts -> все None

- **Оценка:** 1.5 часа

---

### OPT-03: Устранение S/R пересчёта через TAEngine в горячем цикле

- **Приоритет:** P0
- **Проблема:** Строки 1854-1871 `backtest_engine.py`, метод `_generate_signal_fast()`:
  ```python
  _ta_sr = _TAE(df_slice, timeframe=timeframe)
  ta_inds = _ta_sr.calculate_all_indicators()
  ```
  Создаётся новый TAEngine + вызывается `calculate_all_indicators()` для КАЖДОГО сигнала.
  Даже с O(1) df_slice (pandas view), TAEngine пересчитывает ВСЕ индикаторы (RSI, MACD, BB, MA, ADX, etc.)
  только для того, чтобы извлечь support/resistance. Это ~200-500 раз на символ, каждый вызов
  занимает десятки миллисекунд.

  При 10 символах: ~3,000-5,000 полных TAEngine вызовов = несколько минут чистого CPU.

- **Решение:**
  1. S/R уровни обычно не меняются каждую свечу. Кешировать S/R с обновлением каждые N свечей (50-100).
  2. Альтернатива: предвычислить S/R уровни один раз из full_df через скользящее окно.
  3. Минимальное решение: вызывать `_compute_support_resistance()` напрямую (если TAEngine экспортирует эту функцию) без пересчета RSI/MACD/BB.

- **Файлы:**
  - `src/backtesting/backtest_engine.py` -- `_generate_signal_fast()`, строки 1852-1871
  - `src/analysis/ta_engine.py` -- проверить можно ли вызвать S/R вычисление отдельно

- **Точные изменения (вариант: кеширование с обновлением каждые 50 свечей):**
  ```python
  # В _simulate_symbol(), ПЕРЕД ЦИКЛОМ:
  _sr_cache: dict[str, Any] = {"support": [], "resistance": [], "last_idx": -100}

  # В _generate_signal_fast() (или в месте вызова в _simulate_symbol):
  # Обновлять S/R только если прошло >= 50 свечей с последнего вычисления.
  if i - _sr_cache["last_idx"] >= 50:
      try:
          _ta_sr = _TAE(df_slice, timeframe=timeframe)
          ta_inds = _ta_sr.calculate_all_indicators()
          _sr_cache["support"] = [Decimal(str(v)) for v in (ta_inds.get("support_levels") or []) if v]
          _sr_cache["resistance"] = [Decimal(str(v)) for v in (ta_inds.get("resistance_levels") or []) if v]
          _sr_cache["last_idx"] = i
      except Exception:
          pass
  support_levels = _sr_cache["support"]
  resistance_levels = _sr_cache["resistance"]
  ```

- **Ожидаемый эффект:** ~3,000-5,000 TAEngine вызовов -> ~60-100 (по одному на каждые 50 свечей).
  Для всех 10 символов: с ~5,000 -> ~600 вызовов. Ускорение S/R на порядок.

- **Риск:** S/R уровни, кешированные на 50 свечей, могут быть менее точными.
  Impact ограничен: S/R вес в TA score = 5% (sr_sig weight). Кеш обновляется каждые ~2 дня H1.

- **Тесты:**
  - `test_opt_03_sr_cache_reduces_calls` -- TAEngine вызывается <= n/50 раз (не на каждый сигнал)
  - `test_opt_03_sr_cache_first_call` -- первый сигнал вычисляет S/R
  - `test_opt_03_sr_results_reasonable` -- S/R уровни осмысленные (не пустые и не NaN)

- **Оценка:** 2 часа

---

### OPT-04: Параллельная обработка символов через asyncio.gather

- **Приоритет:** P0
- **Проблема:** Строки 1239-1297 `backtest_engine.py`:
  ```python
  for sym_idx, symbol in enumerate(params.symbols):
      ...
      symbol_trades, symbol_filter_stats = await asyncio.to_thread(
          self._simulate_symbol, ...
      )
  ```
  10 символов обрабатываются ПОСЛЕДОВАТЕЛЬНО. Каждый символ уже запускается через `asyncio.to_thread()`,
  но `await` ждёт завершения перед переходом к следующему. GIL ограничивает CPU-параллелизм,
  но I/O операции (загрузка данных) выигрывают.

  Основная возможность: `_simulate_symbol()` -- CPU-bound (numpy, pandas). GIL мешает параллелизму
  в threads. Однако `ProcessPoolExecutor` обходит GIL.

- **Решение:**
  1. Заменить последовательный цикл на `asyncio.gather()` + `asyncio.to_thread()` для всех символов.
  2. Поскольку `_simulate_symbol` CPU-bound: использовать `concurrent.futures.ProcessPoolExecutor` вместо threads. Но это требует сериализации аргументов (pickle), что может быть сложно с ORM-объектами.
  3. **Практичный компромисс:** оставить `asyncio.to_thread()` (без GIL-параллелизма), но запускать ВСЕ символы параллельно через `asyncio.gather()`. Хотя CPU не ускорится из-за GIL, перемежающиеся I/O-операции (pandas rolling, numpy) часть времени отпускают GIL. Реальное ускорение: ~1.5-2x.
  4. **Лучший вариант (Python 3.13+):** Free-threaded Python (no GIL). Пока недоступен -- оставить как TODO.

- **Файлы:**
  - `src/backtesting/backtest_engine.py` -- `_simulate()`, строки 1239-1297

- **Точные изменения:**
  ```python
  # БЫЛО (строки 1239-1297):
  for sym_idx, symbol in enumerate(params.symbols):
      ...
      symbol_trades, stats = await asyncio.to_thread(self._simulate_symbol, ...)
      trades.extend(symbol_trades)
      ...

  # СТАЛО:
  async def _run_symbol(sym_idx: int, symbol: str):
      instrument = await get_instrument_by_symbol(self.db, symbol)
      if instrument is None:
          return [], {}
      market_type = instrument.market or "forex"
      price_rows = await get_price_data(...)
      if len(price_rows) < _MIN_BARS_HISTORY + 2:
          return [], {}
      return await asyncio.to_thread(
          self._simulate_symbol,
          symbol=symbol,
          market_type=market_type,
          ...
      )

  tasks = [_run_symbol(i, sym) for i, sym in enumerate(params.symbols)]
  results = await asyncio.gather(*tasks, return_exceptions=True)
  for result in results:
      if isinstance(result, Exception):
          logger.error("Symbol simulation failed: %s", result)
          continue
      symbol_trades, symbol_filter_stats = result
      trades.extend(symbol_trades)
      for k, v in symbol_filter_stats.items():
          agg_filter_stats[k] = agg_filter_stats.get(k, 0) + v
  ```

- **Ожидаемый эффект:** ~1.5-2x ускорение (numpy/pandas частично отпускают GIL).
  Реальный тест нужен. Если ускорение < 1.2x, откатить и пометить как TODO для Python 3.13.

- **Риск:** `self.db` (AsyncSession) shared между корутинами. Все DB queries делаются ДО `to_thread()`,
  так что race condition маловероятен. Однако нужно убедиться что не будет concurrent DB access.
  Решение: вынести все `await get_price_data()` вызовы в фазу pre-load.

- **Тесты:**
  - `test_opt_04_parallel_same_results` -- результат gather == результат sequential
  - `test_opt_04_error_handling` -- один символ с ошибкой не ломает остальные

- **Оценка:** 3 часа

---

### OPT-05: Pre-load всех данных (DB) перед вычислительной фазой

- **Приоритет:** P0 (зависит от OPT-04)
- **Проблема:** В `_simulate()` цикл последовательно вызывает `await get_price_data()` для каждого символа,
  перемежая DB I/O с CPU-bound симуляцией. Это блокирует event loop и не позволяет параллельно
  загружать данные.

- **Решение:**
  1. Выделить фазу загрузки: загрузить ВСЕ данные для ВСЕХ символов через `asyncio.gather()`.
  2. Хранить в `dict[str, tuple[instrument, list]]`.
  3. Фаза симуляции работает только с in-memory данными, без DB вызовов.

- **Файлы:**
  - `src/backtesting/backtest_engine.py` -- `_simulate()`

- **Точные изменения:**
  ```python
  # Фаза 1: Параллельная загрузка данных
  async def _load_symbol(symbol: str):
      instrument = await get_instrument_by_symbol(self.db, symbol)
      if instrument is None:
          return symbol, None, []
      price_rows = await get_price_data(
          self.db, instrument.id, params.timeframe,
          from_dt=start_dt, to_dt=end_dt, limit=100_000,
      )
      return symbol, instrument, price_rows

  load_tasks = [_load_symbol(s) for s in params.symbols]
  loaded = await asyncio.gather(*load_tasks)
  symbol_data = {sym: (inst, rows) for sym, inst, rows in loaded}

  # Фаза 2: Симуляция (может быть parallel или sequential)
  for sym_idx, symbol in enumerate(params.symbols):
      inst, price_rows = symbol_data.get(symbol, (None, []))
      if inst is None or len(price_rows) < _MIN_BARS_HISTORY + 2:
          continue
      ...
  ```

- **Ожидаемый эффект:** DB queries для 10 символов параллельно. При средней latency 100ms на query = 1s вместо 10 x 100ms = 1s (незначительно). Но это необходимая подготовка для OPT-04.

- **Оценка:** 1 час

---

## P0 -- Сводная таблица оптимизаций

| Задача | Узкое место | Текущая сложность | После оптимизации | Ожидаемое ускорение |
|--------|-------------|-------------------|-------------------|---------------------|
| OPT-01 | D1 list comprehension | O(n*m) per symbol | O(n*log(m)) per symbol | ~60x для D1 lookup |
| OPT-02 | DXY RSI _to_utc() | O(n) datetime allocs + dict.get miss | O(n) pre-map + O(1) array | ~10x для DXY, + fix misses |
| OPT-03 | S/R via full TAEngine | ~3000-5000 TAEngine calls | ~60-100 TAEngine calls | ~50x для S/R |
| OPT-04 | Sequential symbols | 1 thread x 10 symbols | gather() x 10 threads | ~1.5-2x overall |
| OPT-05 | Sequential DB loads | Sequential awaits | Parallel DB loads | Pre-req for OPT-04 |

**Суммарно:** OPT-01..03 устраняют горячие точки в per-candle цикле. OPT-04 добавляет параллелизм.
Ожидаемое общее ускорение: с ~5 часов до ~30-60 минут (примерно 5-10x).

---

## P0 -- Незавершённые валидационные задачи

---

### VAL-01: Запуск бэктеста после CAL3 bug fixes

- **Приоритет:** P0 (заблокирован OPT-01..03)
- **Источник:** `TASKS_V6_CAL_R3.md`, Phase 1 checklist
- **Проблема:** CAL3-01 (DXY fix), CAL3-02 (Monday block), CAL3-03 (TIME_EXIT 48) реализованы,
  но бэктест после них не запущен. Невозможно оценить суммарный эффект bug fixes.
- **Подзадачи:**
  - [ ] Применить оптимизации OPT-01..03
  - [ ] Запустить полный бэктест (10 инструментов, 2024-01 -- 2025-12, H1)
  - [ ] Задокументировать результаты в `docs/BACKTEST_RESULTS_V6_CAL3_P1.md`
  - [ ] Оценить: PF >= 1.3? DD <= 25%? time_exit < 50%?
- **Acceptance:**
  - [ ] Бэктест завершается за < 2 часов (после оптимизаций)
  - [ ] Результаты задокументированы
- **Оценка:** 1 час (+ время бэктеста)

---

### VAL-02: Calendar A/B тест (CAL3-07)

- **Приоритет:** P1
- **Источник:** `TASKS_V6_CAL_R3.md`, CAL3-07
- **Проблема:** Calendar filter блокирует 135 сигналов (23% торговых часов).
  Неизвестно, улучшает это результаты или ухудшает.
- **Подзадачи:**
  - [x] Shell скрипт для запуска двух бэктестов (создан)
  - [ ] Запустить оба бэктеста: `apply_calendar_filter=True` vs `False`
  - [ ] Задокументировать delta PF/WR/PnL
  - [ ] Рекомендация: keep / reduce window / remove
- **Acceptance:**
  - [ ] Документ с чётким сравнением и рекомендацией
- **Оценка:** 1 час + время бэктестов

---

## P1 -- Функциональные задачи

---

### FUNC-01: Live SignalEngine интеграция (filter pipeline)

- **Приоритет:** P1
- **Источник:** TASKS_V5 Task 8, не реализован
- **Проблема:** `SignalFilterPipeline` работает в бэктесте, но `signal_engine.py` (live path)
  не использует unified pipeline. Live сигналы проходят через свою (устаревшую) фильтрацию.
  Это значит что live и backtest фильтруют по-разному.
- **Подзадачи:**
  - [ ] Интегрировать `SignalFilterPipeline.run_all()` в `signal_engine.py` `generate()` метод
  - [ ] Передавать `available_weight=1.0` (все компоненты доступны в live)
  - [ ] Удалить дублирующую фильтрацию из `signal_engine.py`
  - [ ] Тест: live signal проходит через те же фильтры что и backtest
- **Файлы:**
  - `src/signals/signal_engine.py` -- интеграция pipeline
  - `src/signals/filter_pipeline.py` -- возможно потребуется async-обёртка
- **Acceptance:**
  - [ ] Live и backtest используют один и тот же pipeline
  - [ ] Фильтры с graceful degradation работают в live (D1, DXY, calendar)
- **Оценка:** 3-4 часа

---

### FUNC-02: Multi-Timeframe бэктест (TASK-V6-14)

- **Приоритет:** P1
- **Источник:** `TASKS_V6.md`, TASK-V6-14
- **Проблема:** Бэктест работает только на одном TF. H4 может давать лучшие результаты (меньше шума),
  но нет возможности сравнить H1 vs H4 или запустить оба в одном прогоне.
- **Подзадачи:**
  - [ ] `BacktestParams`: `timeframes: list[str]` с backward compat через `timeframe: str`
  - [ ] `_simulate()`: вложенный цикл по timeframes
  - [ ] Correlation guard: одна позиция per symbol across TFs
  - [ ] Summary: `by_timeframe` breakdown
  - [ ] Тест: H1+H4, backward compat с single TF
- **Файлы:**
  - `src/backtesting/backtest_params.py`
  - `src/backtesting/backtest_engine.py`
- **Acceptance:**
  - [ ] Бэктест запускает H1+H4 в одном прогоне
  - [ ] Нет дублирующих входов для одного price move на разных TF
  - [ ] Результаты разбиты по timeframe
- **Оценка:** 4 часа

---

### FUNC-03: Расширение набора инструментов (TASK-V6-15)

- **Приоритет:** P2
- **Источник:** `TASKS_V6.md`, TASK-V6-15
- **Проблема:** Бэктест работает на 10 символах. Нет USDCAD=X в overrides. GC=F может
  требовать специфические настройки.
- **Подзадачи:**
  - [ ] Добавить `DEFAULT_BACKTEST_SYMBOLS` list в config.py
  - [ ] Проверить исторические данные для USDCAD=X, GC=F
  - [ ] Добавить INSTRUMENT_OVERRIDES для GC=F (stocks market, commodity-specific)
  - [ ] Запустить бэктест с расширенным набором
- **Acceptance:**
  - [ ] >= 3 новых инструмента с >= 12 месяцев H1 данных
  - [ ] Общий PF не падает ниже 1.3
- **Оценка:** 2 часа

---

## P1 -- Решения и документация

---

### DOC-01: Strategic decision document (CAL3-08)

- **Приоритет:** P1
- **Источник:** `TASKS_V6_CAL_R3.md`, CAL3-08
- **Проблема:** 6 раундов калибровки, система не достигает стабильной жизнеспособности.
  Нет зафиксированного decision tree с hard stop criteria.
- **Подзадачи:**
  - [ ] Документировать текущее состояние метрик по всем раундам (таблица)
  - [ ] Зафиксировать decision tree:
    - Gate 1: PF >= 1.3 после bug fixes (CAL3) -> продолжить калибровку
    - Gate 2: Если PF < 1.3 -> переключить на H4
    - Gate 3: Если H4 + 1 раунд < 1.3 -> pivot на single-instrument или смена подхода
  - [ ] Определить hard stop: max 2 дополнительных раунда калибровки
- **Файлы:**
  - `docs/DECISION_TIMEFRAME_ARCHITECTURE.md` -- НОВЫЙ
- **Acceptance:**
  - [ ] Документ с decision tree и hard stop criteria
- **Оценка:** 1 час (только документ)

---

### DOC-02: Walk-forward validation run

- **Приоритет:** P1
- **Источник:** TASK-V6-13 (реализован), но ни разу не запущен
- **Проблема:** Walk-forward механизм реализован (`enable_walk_forward=True`), но ни один бэктест
  с этим флагом не запускался. OOS валидация -- ключевой показатель overfitting.
- **Подзадачи:**
  - [ ] Запустить бэктест с `enable_walk_forward=True`, IS=18 мес, OOS=6 мес
  - [ ] Задокументировать IS vs OOS метрики
  - [ ] Оценить: OOS PF >= 1.0? OOS WR в пределах 10% от IS WR?
- **Acceptance:**
  - [ ] Walk-forward результаты задокументированы
  - [ ] OOS PF >= 1.0
- **Оценка:** 30 минут + время бэктеста

---

## P2 -- Незавершённые калибровочные задачи

---

### CAL-MATRIX: Сравнительный бэктест-матрица (CAL-10)

- **Приоритет:** P2
- **Источник:** `TASKS_V6_CALIBRATION.md`, TASK-V6-CAL-10
- **Проблема:** Неизвестно какая комбинация threshold/regime/SHORT дает оптимальный результат.
  Нужна матрица из >= 5 вариантов.
- **Подзадачи:**
  - [ ] Создать `scripts/run_calibration_backtests.sh`
  - [ ] Запустить >= 5 вариантов (A: все cal, B: LONG-only, C: TIME_EXIT 48, D: higher floor, E: directional bear)
  - [ ] Задокументировать в `docs/BACKTEST_RESULTS_V6_CALIBRATION.md`
  - [ ] Выбрать winner с обоснованием
- **Acceptance:**
  - [ ] >= 5 вариантов запущены
  - [ ] Хотя бы 1 вариант: WR >= 25%, PF >= 1.3, DD <= 30%, trades >= 100
- **Оценка:** 2 часа + время бэктестов

---

### CAL-FINAL: Финальный бэктест + перезапуск (CAL-11)

- **Приоритет:** P2 (последний шаг)
- **Источник:** `TASKS_V6_CALIBRATION.md`, TASK-V6-CAL-11
- **Подзадачи:**
  - [ ] Запустить финальный бэктест с winning конфигурацией
  - [ ] Сгенерировать отчёт через `scripts/generate_backtest_report.py`
  - [ ] Перезапустить сервер
  - [ ] Обновить `claude-progress.md`
- **Acceptance:**
  - [ ] WR >= 25%, PF >= 1.3, DD <= 30%, trades >= 100/24mo
  - [ ] >= 6/10 инструментов прибыльны
- **Оценка:** 1 час + время бэктеста

---

## P2 -- Frontend fixes

---

### FE-01: Проверка chart fixes после рестарта сервера

- **Приоритет:** P2
- **Проблема:** `limit=2000` и `date_from` fixes реализованы в `api/routes_v2.py` и
  `frontend-next/src/lib/api.ts`, но не протестированы на запущенном сервере.
- **Подзадачи:**
  - [ ] Перезапустить backend сервер
  - [ ] Открыть chart для EURUSD=X -- проверить что данные за 2+ лет загружаются
  - [ ] Проверить `date_from` parameter -- нет ли 400 ошибок в логах
- **Acceptance:**
  - [ ] Chart отображает данные за весь запрошенный период
  - [ ] Нет 400/500 ошибок при загрузке chart data
- **Оценка:** 30 минут

---

## Порядок выполнения (рекомендуемый)

### Phase 1: Производительность (P0) -- разблокирует все бэктесты
1. **OPT-01** -- D1 bisect (1 час)
2. **OPT-02** -- DXY RSI pre-map (1.5 часа)
3. **OPT-03** -- S/R кеширование (2 часа)
4. **OPT-05** -- Pre-load DB (1 час)
5. *Опционально:* **OPT-04** -- Parallel gather (3 часа) -- если OPT-01..03 недостаточно

--> Запустить тестовый бэктест, замерить время. Цель: < 1 час.

### Phase 2: Валидация после оптимизации (P0)
6. **VAL-01** -- Бэктест после CAL3 bug fixes
7. **DOC-01** -- Strategic decision document (параллельно с VAL-01)

--> Оценить PF. Если >= 1.3 -> Phase 3. Если < 1.3 -> FUNC-02 (H4).

### Phase 3: Функциональные задачи (P1)
8. **FUNC-01** -- Live pipeline integration
9. **VAL-02** -- Calendar A/B тест
10. **DOC-02** -- Walk-forward validation

### Phase 4: Расширение и финализация (P2)
11. **FUNC-02** -- Multi-timeframe (если H1 PF < 1.3)
12. **CAL-MATRIX** -- Матрица калибровок
13. **CAL-FINAL** -- Финальный бэктест + deploy
14. **FUNC-03** -- Новые инструменты
15. **FE-01** -- Chart fixes verification

---

## Метрики успеха (финальные)

| Метрика | Текущее (est.) | Цель | Критично |
|---------|---------------|------|----------|
| Backtest runtime | ~5 часов | < 1 час | Да |
| Trades/24mo | ~100-200 | >= 100 | Да |
| Win Rate | ~19% | >= 25% | Да |
| Profit Factor | ~1.09 | >= 1.3 | Да |
| Max Drawdown | ~54% | <= 25% | Да |
| OOS PF | N/A | >= 1.0 | Да |
| Live/Backtest parity | Нет | Да | Да |
