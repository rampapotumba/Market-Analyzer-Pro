# Tasks: v6 Calibration Round 3

## Цель
Исправить критические баги и структурные проблемы, обнаруженные в cal-r3 бэктесте (PF 1.09, DD 53.8%, WR 19.4%). Приоритет — bug fixes над новыми фичами.

## Источники
- Анализ: `analyst-reports/v6-cal-r3-analysis.md`
- Требования: `analyst-requirements/v6-calibration-r3-requirements.md`

---

## Phase 1: Critical Bug Fixes (баги, искажающие результаты)

### CAL3-01: Fix DXY RSI filter — real data not passed to backtest pipeline
- **Priority:** P0
- **REQ:** REQ-001
- **Problem:** `backtest_engine.py:1355` hardcodes `"dxy_rsi": None`. DXY historical data есть в БД (symbol "DX-Y.NYB" or "DXY"), но не загружается в `_simulate()`. Фильтр SIM-38 всегда проходит через graceful degradation. Все forex LONG сигналы во время сильного доллара (Q3-Q4 2024) не блокируются.
- **Solution:**
  1. В `_simulate()` (строки 1065-1090): добавить предзагрузку DXY price data аналогично D1 cache. Загрузить DXY H1 candles за весь период бэктеста.
  2. Вычислить RSI(14) из DXY closes один раз (массив), как делается для TA indicators в `_simulate_symbol()`.
  3. Передать DXY RSI массив в `_simulate_symbol()` через новый параметр `dxy_rsi_array`.
  4. В `_simulate_symbol()` (строка 1355): вместо `None` — lookup DXY RSI по timestamp текущей свечи из предвычисленного массива.
- **Files:**
  - `src/backtesting/backtest_engine.py` — `_simulate()`: загрузка DXY data + RSI(14) computation; `_simulate_symbol()`: новый параметр + lookup
- **Subtasks:**
  - [x] В `_simulate()`: найти DXY instrument по symbol ("DX-Y.NYB"), загрузить price_data за период бэктеста через `get_price_data()`
  - [x] Вычислить RSI(14) из DXY closes используя Wilder smoothing (14 периодов), получить dict `{timestamp: rsi_value}`
  - [x] Передать `dxy_rsi_by_ts: dict[datetime, float]` в `_simulate_symbol()` как новый параметр
  - [x] В filter_context (строка 1355): заменить `None` на lookup `dxy_rsi_by_ts.get(candle_ts)` (ближайший timestamp)
  - [x] Тест: mock DXY data с RSI > 55, проверить что LONG EURUSD=X блокируется
  - [x] Тест: mock DXY data с RSI 50 (нейтральный), сигнал проходит
  - [x] Тест: DXY instrument не найден в БД → graceful degradation (dxy_rsi=None, фильтр пропускается)
- **Acceptance:**
  - [ ] Бэктест показывает `rejected_by_dxy_filter > 0` в filter_stats
  - [ ] Forex LONG trades в Q3-Q4 2024 (период сильного доллара) блокируются
  - [ ] При отсутствии DXY data — поведение не меняется (backward compatible)
- **Estimate:** 2-3 часа
- **Note:** DXY symbol в БД может быть "DX-Y.NYB" (Yahoo Finance format). Проверить реальный symbol через `get_instrument_by_symbol()`. Если DXY инструмент не существует — создать fallback список символов: ["DX-Y.NYB", "DXY", "DX=F"].

---

### CAL3-02: Block Monday completely for forex
- **Priority:** P0
- **REQ:** REQ-002
- **Problem:** Monday forex entries: 25 trades, 1 win, -$399 (WR 4.0%). Текущий `WEAK_WEEKDAY_SCORE_MULTIPLIER = 1.5` недостаточен — сигналы все равно проходят score threshold.
- **Solution:** Полная блокировка Monday (weekday=0) для forex в `check_weekday()`. Криптовалюты по-прежнему exempt. Убрать Monday из `WEAK_WEEKDAYS` (оставить только Tuesday), т.к. блокировка полная и multiplier больше не нужен.
- **Files:**
  - `src/signals/filter_pipeline.py` — `check_weekday()` (строки 430-447)
  - `src/config.py` — `WEAK_WEEKDAYS` (строка 220): убрать 0, оставить `[1]` (только Tuesday)
- **Subtasks:**
  - [x] В `check_weekday()` (строка 441): изменить условие Monday с `hour < 10` на полный день. Вместо `if weekday == 0 and hour < 10:` сделать `if weekday == 0:` для non-crypto
  - [x] В `config.py` строка 220: изменить `WEAK_WEEKDAYS: list = [0, 1]` на `WEAK_WEEKDAYS: list = [1]` (Monday блокируется полностью, Tuesday — через multiplier)
  - [x] Тест: forex signal на Monday 14:00 UTC → blocked
  - [x] Тест: crypto signal на Monday 14:00 UTC → passed (exempt)
  - [x] Тест: forex signal на Tuesday → passed (через multiplier, не блокируется)
- **Acceptance:**
  - [ ] Бэктест: 0 forex trades с entry на Monday
  - [ ] Crypto Monday trades сохраняются
- **Estimate:** 1 час

---

### CAL3-03: Increase TIME_EXIT_CANDLES for H1 back to 48
- **Priority:** P0
- **REQ:** REQ-003
- **Problem:** 127 из 182 trades (70%) выходят по time_exit через 24 H1 свечи (1 день). Средняя длительность выигрыша — 8.3 дня (12037 минут), т.е. выигрышные сделки требуют > 24 часов для достижения TP. Текущие 24 свечи убивают потенциальных победителей.
- **Solution:** Вернуть H1 time_exit к 48 свечам (2 дня). Это было оригинальное значение из v5 spec. Уменьшение до 24 в CAL-03 было преждевременной оптимизацией без учёта реальных данных.
- **Files:**
  - `src/backtesting/backtest_engine.py` — `_TIME_EXIT_CANDLES` (строка 102): `"H1": 24` → `"H1": 48`
- **Subtasks:**
  - [x] Изменить `_TIME_EXIT_CANDLES["H1"]` с 24 на 48 (строка 102)
  - [x] Тест: позиция с 47 свечами и unrealized_pnl <= 0 → НЕ закрывается
  - [x] Тест: позиция с 48 свечами и unrealized_pnl <= 0 → закрывается по time_exit
  - [x] Тест: позиция с 48 свечами и unrealized_pnl > 0 → НЕ закрывается (существующая логика)
- **Acceptance:**
  - [ ] time_exit_count в бэктесте значительно ниже 70%
  - [ ] Больше trades достигают TP или SL (реальные выходы)
- **Estimate:** 30 минут

---

## Phase 2: Structural Filters (улучшение качества сигналов)

### CAL3-04: Per-instrument regime blocking (VOLATILE per market type)
- **Priority:** P1
- **REQ:** REQ-005
- **Problem:** VOLATILE regime: 95 trades, -$33, WR 19%. Глобальная блокировка удалит слишком много trades (включая GC=F, где волатильность = trend continuation). Нужна per-instrument или per-market-type настройка.
- **Solution:** Добавить `BLOCKED_REGIMES_BY_MARKET` в config.py. В `check_regime()` проверять market-specific блокировку перед global.
- **Files:**
  - `src/config.py` — добавить `BLOCKED_REGIMES_BY_MARKET` dict
  - `src/signals/filter_pipeline.py` — `check_regime()` (строки 339-350)
  - `src/backtesting/backtest_engine.py` — передать `market_type` в filter_context (уже есть)
- **Subtasks:**
  - [x] В `config.py` добавить:
    ```python
    BLOCKED_REGIMES_BY_MARKET: dict = {
        "forex": ["VOLATILE"],
        # crypto и stocks — не блокируют VOLATILE
    }
    ```
  - [x] В `filter_pipeline.py` `check_regime()`: добавить параметр `market_type: str = ""`, проверять `BLOCKED_REGIMES_BY_MARKET.get(market_type, [])` перед `BLOCKED_REGIMES`
  - [x] В `run_all()`: передать `market_type` в `self.check_regime(regime, symbol, market_type)`
  - [x] Тест: EURUSD=X + VOLATILE → blocked
  - [x] Тест: GC=F + VOLATILE → passed (stocks не блокируют)
  - [x] Тест: ETH/USDT + VOLATILE → passed (crypto не блокируют)
  - [x] Тест: EURUSD=X + STRONG_TREND_BULL → passed (не в blocked list)
- **Acceptance:**
  - [ ] Forex VOLATILE trades блокируются
  - [ ] GC=F и crypto VOLATILE trades сохраняются
- **Estimate:** 1.5 часа

---

### CAL3-05: Add viability assessment to backtest summary
- **Priority:** P2
- **REQ:** REQ-007
- **Problem:** Каждый бэктест требует ручного анализа метрик. Нужна автоматическая оценка жизнеспособности.
- **Solution:** Добавить секцию `viability_assessment` в `_compute_summary()`.
- **Files:**
  - `src/backtesting/backtest_engine.py` — `_compute_summary()` (после строки 611)
- **Subtasks:**
  - [x] Добавить в конец `_compute_summary()` перед `return result`: viability_assessment dict
  - [x] Тест: trades с PF=1.5, WR=30%, DD=15%, diversified → overall="VIABLE"
  - [x] Тест: trades с PF=1.1, WR=19%, DD=54% → overall="NOT_VIABLE", 3 blocking factors
  - [x] Тест: пустой trades list → graceful handling (no division by zero)
- **Acceptance:**
  - [ ] JSON output бэктеста содержит `viability_assessment` с корректными pass/fail
- **Estimate:** 1.5 часа

---

### CAL3-06: F&G / Funding Rate adjustment tracking in BacktestTradeResult
- **Priority:** P2
- **REQ:** REQ-004
- **Problem:** Невозможно изолировать влияние F&G и Funding Rate на crypto trades. ETH перешёл с +$812 (без данных) на -$26 (с реальными данными), но неизвестно, помогли или навредили эти корректировки.
- **Solution:** Добавить `fg_adjustment` и `fr_adjustment` поля в `BacktestTradeResult`. Логировать корректировки при генерации сигнала. Добавить `by_adjustment` секцию в summary.
- **Files:**
  - `src/backtesting/backtest_params.py` — `BacktestTradeResult`: 2 новых Optional поля
  - `src/backtesting/backtest_engine.py` — `_generate_signal_fast()` или `_simulate_symbol()`: записать adjustment values в trade dict
  - `src/backtesting/backtest_engine.py` — `_compute_summary()`: добавить `by_adjustment` breakdown
- **Subtasks:**
  - [x] В `BacktestTradeResult` добавить `fg_adjustment` и `fr_adjustment` Optional[Decimal] поля
  - [x] В `_compute_summary()`: добавить `by_adjustment` секцию, группирующую trades по наличию/типу adjustment
  - [x] Тест: crypto trade с fg_adjustment=+5 корректно сохраняется
  - [x] Тест: forex trade без adjustments → поля None
- **Acceptance:**
  - [ ] Бэктест JSON включает `by_adjustment` с breakdown PnL для trades с/без F&G и FR adjustments
- **Estimate:** 2 часа
- **Note:** Требует понимания, где именно F&G и FR модифицируют composite score в `_generate_signal_fast()`. Если adjustment вычисляется вне generate_signal — нужно вычислить повторно в backtest loop.

---

## Phase 3: Diagnostic & Decision Support

### CAL3-07: Calendar filter A/B comparison backtest
- **Priority:** P2
- **REQ:** REQ-006
- **Problem:** Calendar filter блокирует 135 сигналов, но неизвестно, улучшает это или ухудшает результаты. С 453 HIGH-impact events и +/-4h window, 23% торговых часов заблокированы.
- **Solution:** Это задача запуска, не код. Запустить два бэктеста с идентичными параметрами: `apply_calendar_filter=True` vs `apply_calendar_filter=False`. Задокументировать дельту. Если calendar filter улучшает PF > 0.05 — оставить. Иначе — уменьшить window или убрать.
- **Files:**
  - `scripts/run_calendar_ab_test.sh` — НОВЫЙ скрипт для запуска двух бэктестов
  - `docs/BACKTEST_RESULTS_V6_CAL3.md` — результаты с рекомендацией
- **Subtasks:**
  - [x] Написать shell script, запускающий 2 бэктеста через API: один с `apply_calendar_filter=true`, другой с `false`
  - [ ] Запустить оба, записать PF/WR/PnL/DD для каждого
  - [ ] Документировать delta и рекомендацию
- **Acceptance:**
  - [ ] Документ с чётким сравнением и рекомендацией: keep / reduce window / remove
- **Estimate:** 1 час (скрипт) + время бэктеста

---

### CAL3-08: Strategic decision document — timeframe and architecture
- **Priority:** P1 (decision, not code)
- **REQ:** REQ-008
- **Problem:** 6 раундов калибровки, система не достигает минимальной жизнеспособности. Нужно зафиксированное архитектурное решение с hard stop criteria.
- **Solution:** Архитектор/product owner документирует решение. Предварительная рекомендация (на основе анализа):
  1. Исправить баги (CAL3-01..03) — это "бесплатные" улучшения
  2. Запустить бэктест после bug fixes — если PF >= 1.3, продолжить калибровку
  3. Если PF < 1.3 после bug fixes — переключить на H4 timeframe (данные есть)
  4. Hard stop: если PF < 1.3 после H4 и ещё 1 раунда калибровки — pivot на single-instrument (GC=F) или смена подхода
- **Files:**
  - `docs/DECISION_TIMEFRAME_ARCHITECTURE.md` — НОВЫЙ: зафиксированное решение
- **Subtasks:**
  - [ ] Документировать текущее состояние метрик по всем раундам
  - [ ] Зафиксировать decision tree с hard stop criteria
  - [ ] Определить метрики для каждого gate: PF threshold, min trades, max DD
- **Acceptance:**
  - [ ] Документ с decision tree и hard stop criteria
- **Estimate:** 1 час (только документ, не код)

---

## Порядок выполнения

1. **CAL3-01** (DXY bug fix) — самый большой impact, forex LONG фильтрация
2. **CAL3-02** (Monday block) — простое исправление, -$399 экономии
3. **CAL3-03** (TIME_EXIT 48) — восстановление оригинального значения, больше trades достигают TP
4. **Запуск бэктеста** — оценить суммарный эффект bug fixes
5. **CAL3-04** (per-market regime) — если PF после Phase 1 < 1.3
6. **CAL3-05** (viability assessment) — для автоматизации анализа
7. **CAL3-06** (F&G/FR tracking) — для диагностики crypto
8. **CAL3-07** (calendar A/B) — для informed decision on calendar filter
9. **CAL3-08** (strategic decision) — после всех данных

---

## Checklist

### Phase 1: Critical Bug Fixes
- [x] CAL3-01: DXY RSI filter fix
- [x] CAL3-02: Monday full block for forex
- [x] CAL3-03: TIME_EXIT H1 = 48
- [ ] **Backtest run**: post-Phase-1 results documented

### Phase 2: Structural Filters
- [x] CAL3-04: Per-market regime blocking (VOLATILE for forex)
- [x] CAL3-05: Viability assessment in summary
- [x] CAL3-06: F&G / FR adjustment tracking
- [ ] **Backtest run**: post-Phase-2 results documented

### Phase 3: Diagnostic & Decision
- [x] CAL3-07: Calendar A/B script (execution pending)
- [ ] CAL3-08: Strategic decision document

---

## Expected Impact (оценка)

| Fix | Expected Trades Saved | Expected PnL Impact |
|-----|----------------------|-------------------|
| CAL3-01 (DXY filter) | ~20-40 forex LONG blocked | +$50-150 (avoid losing forex LONGs during strong dollar) |
| CAL3-02 (Monday block) | ~25 Monday trades blocked | +$399 (entire Monday loss eliminated) |
| CAL3-03 (TIME_EXIT 48) | ~30-50 fewer time_exits | +$100-200 (more trades reach TP) |
| **Total Phase 1** | — | **+$500-750 estimated** (PF could reach 1.4-1.8) |
| CAL3-04 (VOLATILE forex) | ~40-50 forex VOLATILE blocked | +$20-50 |

**Caveat:** Оценки приблизительные. Реальный impact будет виден только после бэктеста.
