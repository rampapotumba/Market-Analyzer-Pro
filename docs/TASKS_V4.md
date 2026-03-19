# Trade Simulator v4 — Задачи

> Отмечай `[x]` по мере выполнения. Работай сверху вниз, по фазам.
> Перед каждой задачей читай `docs/SPEC_SIMULATOR_V4.md` (раздел SIM-XX).
> После каждого блока — запусти тесты: `pytest tests/test_simulator_v4.py -v`
>
> **Основание:** Анализ 6 закрытых сделок от 2026-03-19.
> Критический баг: система генерирует только SHORT сигналы (17/17).

---

## Phase 0 — Подготовка инфраструктуры

> Создание файлов и миграции до начала кодинга. Гарантирует что структура на месте.

### 0.1 Подготовка тестовой инфраструктуры
- [x] Создай `tests/test_simulator_v4.py` (пустой файл с импортами и базовыми фикстурами)
- [x] Добавь фикстуры: `mock_db_session`, `mock_position_long`, `mock_position_short`, `mock_candle_data`
- [x] Проверь что `pytest tests/test_simulator_v4.py -v` проходит (0 tests, 0 errors)

### 0.2 Alembic миграция для бэктеста (SIM-22 schema)
- [x] Создай миграцию `alembic revision --autogenerate -m "simulator_v4_backtest_tables"`
- [x] Таблица `backtest_runs`: id UUID PK, params JSONB, status VARCHAR(16) DEFAULT 'pending', started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, summary JSONB
- [x] Таблица `backtest_trades`: id SERIAL PK, run_id UUID FK → backtest_runs(id) ON DELETE CASCADE, symbol VARCHAR(32), timeframe VARCHAR(8), direction VARCHAR(8), entry_price NUMERIC(18,8), exit_price NUMERIC(18,8), exit_reason VARCHAR(32), pnl_pips NUMERIC(14,4), pnl_usd NUMERIC(14,4), result VARCHAR(16), composite_score NUMERIC(8,4), entry_at TIMESTAMPTZ, exit_at TIMESTAMPTZ, duration_minutes INTEGER, mfe NUMERIC(18,8), mae NUMERIC(18,8)
- [x] Добавь индекс: `ix_backtest_trades_run_id` на `run_id`
- [ ] Применить: `alembic upgrade head` → проверить что таблицы создались
- [x] Коммит: `feat(sim-22): add backtest tables migration`

### 0.3 SQLAlchemy модели для бэктеста
- [x] В `src/database/models.py`: добавь `BacktestRun` и `BacktestTrade` модели
- [x] Убедись что relationship `BacktestRun.trades` → `BacktestTrade` настроен (cascade delete)
- [x] Проверь: `from src.database.models import BacktestRun, BacktestTrade` — без ошибок

### 0.4 Создание модуля backtesting
- [x] Создай `src/backtesting/__init__.py`
- [x] Создай `src/backtesting/backtest_params.py` — Pydantic-модели: `BacktestParams`, `BacktestResult`, `BacktestTradeResult`
- [x] Создай `src/backtesting/backtest_engine.py` — заглушка класса `BacktestEngine` с `async def run_backtest(params) -> str`
- [x] Коммит: `feat(sim-22): scaffold backtesting module`

---

## Phase 1 — Диагностика и критические фиксы (P0)

> Эту фазу нельзя пропускать: SIM-17 блокирует достоверность всех последующих данных.

### 1.1 SIM-17: Диагностика SHORT bias в scoring

**Шаг 1 — Аудит fallback-значений (только чтение кода, не меняй):**
- [x] Изучи `src/signals/signal_engine.py` — найди где собирается composite_score (ta/fa/sentiment/geo/of)
- [x] Изучи `src/signals/fa_engine.py` — запиши ВСЕ ветки except/fallback и что они возвращают
- [x] Изучи `src/signals/sentiment_engine.py` — запиши ВСЕ ветки except/fallback
- [x] Изучи `src/signals/geo_engine.py` — запиши ВСЕ ветки except/fallback
- [x] Изучи `src/signals/order_flow.py` — **файл не существует**, OF scoring не реализован
- [x] Проверь `SIGNAL_WEIGHTS` в конфиге: сумма весов == 1.0? ✓ (ta=0.45+fa=0.25+sent=0.20+geo=0.10=1.0)
- [x] Зафиксируй все находки — все fallbacks уже возвращают 0.0, order_flow.py отсутствует

**Шаг 2 — Фикс fallback-значений:**
- [x] В `fa_engine.py`: fallbacks уже 0.0, добавлены [SIM-17] logger.warning
- [x] В `sentiment_engine.py`: fallbacks уже 0.0 (_weighted_average returns 0.0 when all None)
- [x] В `geo_engine.py`: fallbacks уже 0.0 (line 171)
- [x] В `order_flow.py`: файл не существует — OF не используется в composite_score
- [x] SIGNAL_WEIGHTS сходятся к 1.0 — нормализация не нужна
- [x] Добавь `logger.warning(f"[SIM-17] ...")` в каждый fallback в signal_engine.py и fa_engine.py

**Шаг 3 — Диагностический эндпоинт:**
- [x] В `src/api/routes_v2.py`: создан `GET /api/v2/diagnostics/scoring-breakdown`
- [x] Для каждого инструмента: вызываются все компоненты (ta/fa/sentiment/geo)
- [x] Структура ответа: instruments[], summary{avg_composite, pct_negative, suspected_bias_sources}
- [x] Добавлен `bias_flags`: список компонентов которые вернули дефолт при отсутствии данных

**Шаг 4 — Тесты:**
- [x] `test_sim17_neutral_fallback_fa` — FA engine без данных → 0.0
- [x] `test_sim17_neutral_fallback_sentiment` — Sentiment engine без новостей → 0.0
- [x] `test_sim17_neutral_fallback_geo` — Geo engine для неизвестного символа → 0.0
- [x] `test_sim17_neutral_fallback_of` — Order flow не реализован → of_score=None, of_weight=0.0
- [x] `test_sim17_scoring_breakdown_endpoint` — эндпоинт возвращает корректную структуру
- [x] `test_sim17_long_signal_possible` — при нейтральных fa/sentiment/geo и бычьем TA → composite > 7.0

**Шаг 5 — Верификация:**
- [ ] Запусти scoring-breakdown → убедись что появились инструменты с положительным composite_score
- [x] Коммит: `fix(sim-17): fix SHORT bias — neutral fallback for all scoring components`

### 1.2 SIM-19: SL на 2×ATR (режим-адаптивный)

> **Порядок: SIM-19 ДО SIM-18.** SL distance определяет SL, а TP = f(SL, R:R). Сначала фиксим SL, потом R:R.

- [x] Найди в `src/signals/risk_manager_v2.py` метод расчёта SL (ищи умножение ATR)
- [x] Добавь `ATR_SL_MULTIPLIER_MAP` (значения из спеки §SIM-19) в начало файла
- [x] Модифицируй `calculate_levels_for_regime()`: принимает `regime: str`, использует `ATR_SL_MULTIPLIER_MAP.get(regime, ATR_SL_MULTIPLIER_MAP["DEFAULT"])`
- [x] Убедись что `position_size_pct` пересчитывается: `risk_amount / new_sl_distance` (формула уже так работает)
- [ ] Проверь: SIM-13 (swap) работает корректно — более широкий SL → дольше в позиции → больше свопов
- [x] Тест: `test_sim19_sl_wider_volatile` — VOLATILE: SL = entry ± 2.5×ATR
- [x] Тест: `test_sim19_sl_strong_trend` — STRONG_TREND_*: SL = entry ± 1.5×ATR
- [x] Тест: `test_sim19_position_size_decreases_with_wider_sl` — wider SL → smaller position_pct
- [x] Тест: `test_sim19_rr_preserved` — R:R остаётся корректным (TP пересчитан под новый SL)
- [x] Коммит: `feat(sim-19): regime-adaptive SL multiplier`

### 1.3 SIM-18: Динамический R:R по режиму рынка

> Зависит от SIM-19 (SL distance уже режим-адаптивный → TP = SL × R:R)

- [x] В `risk_manager_v2.py`: добавь `REGIME_RR_MAP` (значения из спеки §SIM-18)
- [x] Модифицируй расчёт TP: `tp_distance = sl_distance * REGIME_RR_MAP[regime]["target_rr"]`
- [x] Расчёт TP1, TP2, TP3 через target_rr; TP3 = TP2×1.5 (кроме RANGING/VOLATILE)
- [x] Level snap: если S/R level в диапазоне `[tp1 * 0.8, tp1 * 1.2]` → скорректировать tp1 к уровню
- [x] Проверь что min_rr соблюдается: если после snap R:R < min_rr → вернуть tp1 к расчётному
- [x] Тест: `test_sim18_rr_strong_trend` — STRONG_TREND_BULL → TP на 2.5×SL distance
- [x] Тест: `test_sim18_rr_ranging` — RANGING → TP на 1.3×SL distance
- [x] Тест: `test_sim18_rr_level_snap` — TP корректируется к ближайшему resistance
- [x] Тест: `test_sim18_rr_min_respected` — после snap R:R не ниже min_rr
- [x] Коммит: `feat(sim-18): dynamic R:R by market regime`

### 1.4 SIM-21: Корреляционный guard

- [x] Добавь `CORRELATED_GROUPS` в `src/signals/portfolio_risk.py`
- [x] Создай helper `get_correlation_group(symbol: str) -> Optional[set[str]]` — находит группу символа
- [x] Создай `async def count_open_positions_in_group(db, group: set[str], direction: str) -> int` в `src/database/crud.py`
- [x] Создай `async def is_position_blocked_by_correlation(db, instrument_id, symbol, direction) -> tuple[bool, str]` в `src/database/crud.py`
- [x] В `signal_engine.py`: прямой guard (Rule 1) остался `has_open_position_for_instrument`; добавлен correlation group guard (Rule 2) после определения direction (шаг 11a)
- [x] Тест: `test_sim21_same_instrument_blocked` — тот же инструмент → blocked
- [x] Тест: `test_sim21_correlated_same_direction_blocked` — EURUSD SHORT + GBPUSD SHORT → blocked
- [x] Тест: `test_sim21_correlated_opposite_direction_allowed` — EURUSD SHORT + GBPUSD LONG → allowed
- [x] Тест: `test_sim21_different_group_allowed` — EURUSD SHORT + BTC SHORT → allowed
- [x] Тест: `test_sim21_unknown_symbol_allowed` — символ не в группах → allowed
- [x] Коммит: `feat(sim-21): correlation guard for position blocking`

**Контрольная точка Phase 1:**
- [x] `pytest tests/test_simulator_v4.py -v` → 22 тестов Phase 1 проходят
- [x] `pytest tests/test_simulator_v3.py -v` → 30 passed, 0 новых поломок
- [ ] Запустить scoring-breakdown → подтвердить наличие LONG сигналов

---

## Phase 2 — Бэктест движок (P0)

> Запусти как можно раньше — результаты нужны для валидации всех изменений из Phase 1.

### 2.1 SIM-22: CRUD функции бэктеста

- [x] В `src/database/crud.py`: `async def create_backtest_run(db, params: dict) -> str` (returns run_id UUID)
- [x] `async def update_backtest_run(db, run_id: str, status: str, summary: dict = None)`
- [x] `async def create_backtest_trade(db, run_id: str, trade: dict)`
- [x] `async def create_backtest_trades_bulk(db, run_id: str, trades: list[dict])` — batch insert для производительности
- [x] `async def get_backtest_run(db, run_id: str) -> Optional[BacktestRun]`
- [x] `async def get_backtest_results(db, run_id: str) -> dict` — run + trades + computed stats
- [x] `async def list_backtest_runs(db, limit: int = 20) -> list[BacktestRun]`
- [x] Тест: `test_sim22_crud_create_and_get_run` — создать → получить → совпадает
- [x] Тест: `test_sim22_crud_update_run` — update_backtest_run изменяет статус
- [x] Тест: `test_sim22_crud_bulk_insert_trades` — bulk insert добавляет N объектов
- [x] Тест: `test_sim22_crud_get_results_structure` — get_backtest_results возвращает все поля
- [x] Коммит: `feat(sim-22): backtest CRUD functions`

### 2.2 SIM-22: Бэктест движок (core)

- [x] В `src/backtesting/backtest_engine.py`: реализуй `BacktestEngine` класс
- [x] Метод `async def run_backtest(self, params: BacktestParams) -> str`:
  1. Создать backtest_run (status=running)
  2. Загрузить price_data для каждого символа в заданном диапазоне
  3. Итерация по свечам в хронологическом порядке
  4. На каждой свече: slice данных до текущей (NO LOOKAHEAD!)
  5. `_generate_signal()` на slice (TAEngine + нейтральные fa/geo/sentiment)
  6. Entry fill: на СЛЕДУЮЩЕЙ свече после сигнала (open price)
  7. Для открытых позиций: SL/TP check по high/low (SIM-09 логика, worst case → SL)
  8. Накопить результаты в memory (list of BacktestTradeResult)
  9. Bulk insert в backtest_trades
  10. Рассчитать summary: win_rate, PF, max_drawdown, equity_curve, monthly_returns
  11. Update backtest_run (status=completed, summary=...)
- [x] Error handling: при exception → update status=failed, сохранить error в summary
- [x] Тест: `test_sim22_backtest_no_lookahead` — на свече N доступны только данные [0..N-1]
- [x] Тест: `test_sim22_backtest_sl_tp_check` — SL/TP применяются по high/low свечи
- [x] Тест: `test_sim22_backtest_results_structure` — результат содержит все поля из спеки
- [x] Тест: `test_sim22_backtest_isolated_from_live` — ничего не записывается в signal_results/virtual_portfolio
- [x] Коммит: `feat(sim-22): backtest engine core implementation`

### 2.3 SIM-22: API эндпоинты бэктеста

- [ ] В `src/api/routes_v2.py`:
  - `POST /api/v2/backtest/run` — validate params, create_task(engine.run_backtest), return {run_id, status: "running"}
  - `GET /api/v2/backtest/{run_id}/status` — текущий статус из backtest_runs
  - `GET /api/v2/backtest/{run_id}/results` — полные результаты (summary + trades list)
  - `GET /api/v2/backtest/list` — список всех прогонов (id, status, started_at, summary.total_trades)
- [ ] Валидация params: symbols непустой, start_date < end_date, account_size > 0
- [ ] Тест: `test_sim22_backtest_run_endpoint` — POST возвращает run_id
- [ ] Тест: `test_sim22_backtest_list_endpoint` — GET /list возвращает массив
- [ ] Коммит: `feat(sim-22): backtest API endpoints`

### 2.4 SIM-22: Первый прогон бэктеста

- [ ] Запустить `POST /api/v2/backtest/run` с параметрами:
  - symbols: ["EURUSD=X", "GBPUSD=X", "AUDUSD=X", "BTC/USDT", "ETH/USDT", "SPY"]
  - timeframe: "H1", start_date: "2024-01-01", end_date: "2025-12-31"
  - account_size: 1000.0, apply_slippage: true, apply_swap: true
- [ ] Дождаться завершения. Если < 100 сделок → проверить пороги composite_score, расширить период/символы
- [ ] Создать `docs/BACKTEST_RESULTS_V1.md` с данными:
  - total_trades, win_rate, profit_factor, max_drawdown_pct
  - LONG/SHORT ratio — **ожидается 30-70%/30-70%** (если 0% LONG → SIM-17 фикс не сработал)
  - by_score_bucket: в каком диапазоне composite_score сделки прибыльны
  - by_symbol: какие инструменты лучше/хуже
  - Сравнение с метриками успеха из спеки §5
- [ ] Коммит: `docs(sim-22): first backtest results`

---

## Phase 3 — Улучшение управления позициями (P1)

### 3.1 SIM-20: MAE Early Exit

- [ ] В `src/tracker/signal_tracker.py`: добавь `MAE_EARLY_EXIT_CONFIG` (значения из спеки §SIM-20)
- [ ] Добавь `"mae_early_exit"` в exit_reason константы (если есть enum/set — расширить)
- [ ] В тике симулятора: ПОСЛЕ обновления MAE (но ДО проверки SL/TP):
  ```
  sl_distance = abs(entry_price - current_sl)
  mae_ratio = abs(mae) / sl_distance
  if mae_ratio >= threshold AND candles >= min_candles AND (mfe == 0 or abs(mae)/abs(mfe) >= 1/mfe_max_ratio):
      → close at current_price, exit_reason="mae_early_exit"
  ```
- [ ] При закрытии: записать exit_reason = "mae_early_exit", result = "loss" (это всегда убыточный выход)
- [ ] Тест: `test_sim20_mae_early_exit_triggers` — MAE 65% SL, 4 свечи, MFE=0 → exit
- [ ] Тест: `test_sim20_mae_early_exit_no_trigger_early_candles` — MAE 65%, 2 свечи → НЕ exit
- [ ] Тест: `test_sim20_mae_early_exit_no_trigger_with_mfe` — MAE 65%, MFE=40% MAE → НЕ exit
- [ ] Тест: `test_sim20_mae_exit_reason_stored` — exit_reason="mae_early_exit" в БД
- [ ] Тест: `test_sim20_mae_early_exit_division_by_zero` — mfe=0, mae=0, sl_distance=0 → graceful (нет exit, нет crash)
- [ ] Коммит: `feat(sim-20): MAE early exit mechanism`

### 3.2 SIM-20: Бэктест с MAE early exit

- [ ] Запустить бэктест с теми же параметрами что Phase 2.4 (но MAE early exit enabled)
- [ ] Сравнить результаты: win_rate, PF, avg_loss_usd, max_drawdown
- [ ] Зафиксировать в `docs/BACKTEST_RESULTS_V1.md` (секция "MAE Early Exit Impact")
- [ ] Если PF хуже → пометить MAE early exit как `enabled: False` по умолчанию, оставить настраиваемым

### 3.3 SIM-24: Диагностика и фикс partial close

- [ ] Выполни SQL из спеки §SIM-24: сколько сделок с exit_reason='tp1_hit'? Сколько partial_closed=true?
- [ ] Тест: `test_sim24_partial_close_triggers_at_tp1`:
  - mock позицию с TP1 = текущая цена
  - после обработки: `size_remaining_pct = 0.5`, `partial_closed = true`
  - SL перемещён на breakeven (entry price)
- [ ] Если тест падает → найди баг в `signal_tracker.py` (логика partial close из SIM-07)
- [ ] Тест: `test_sim24_partial_close_sl_moves_to_breakeven` — SL == entry_price после partial
- [ ] Тест: `test_sim24_second_half_closes_at_breakeven` — итоговый result = "win" (partial profit + BE = win)
- [ ] В `routes_v2.py` `/simulator/stats`: добавь `partial_close_count` — кол-во сделок с partial_closed=true
- [ ] Тест: `test_sim24_partial_close_count_in_stats`
- [ ] Коммит: `fix(sim-24): diagnose and fix partial close logic`

---

## Phase 4 — Аналитика и наблюдаемость (P1)

### 4.1 SIM-23: Диагностические API эндпоинты

- [ ] `GET /api/v2/diagnostics/score-components`:
  - Для каждого компонента (ta/fa/sentiment/geo/of): avg значение, min, max, zero_pct (% нулевых)
  - Фильтр: `?days=30` (по умолчанию 30 дней)
  - Red flag: `"bias_warning": true` если avg < -3.0
- [ ] `GET /api/v2/diagnostics/mfe-mae-distribution`:
  - Для закрытых сделок: percentiles [10, 25, 50, 75, 90] MAE и MFE
  - `early_exit_viability`: % сделок где MAE > 60% SL в первых 3 свечах (потенциал MAE early exit)
- [ ] `GET /api/v2/diagnostics/signal-timing`:
  - Распределение по часам UTC: {hour: count}
  - win_rate по часу: {hour: win_rate_pct}
- [ ] `GET /api/v2/diagnostics/partial-close-analysis`:
  - tp1_hit_count, partial_close_count, pct_tp1_hit
  - of_tp1_continued_to_tp2: % от partial close что дошли до TP2
  - of_tp1_returned_to_sl: % от partial close что вернулись к SL/BE
- [ ] Тест: `test_sim23_score_components_endpoint` — all components present, zero_pct in [0,100]
- [ ] Тест: `test_sim23_mfe_mae_distribution` — percentiles в правильном порядке (p10 < p50 < p90)
- [ ] Тест: `test_sim23_signal_bias_detected` — при 100% SHORT → bias_warning = true
- [ ] Тест: `test_sim23_signal_timing_format` — 24 часа, каждый с count и win_rate
- [ ] Коммит: `feat(sim-23): diagnostic API endpoints`

### 4.2 SIM-23: Диагностический UI (frontend)

- [ ] Добавь вкладку "Diagnostics" на страницу `/simulator`
- [ ] **Score Components Bar** — горизонтальный bar chart (ta/fa/sentiment/geo/of средние)
  - Красный бордер если компонент avg < -3.0 (suspected bias)
- [ ] **LONG/SHORT Bias Indicator** — соотношение за последние 30 дней
  - ⚠ Warning если > 80% в одну сторону
- [ ] **MFE vs MAE Scatter** — каждая сделка как точка, x=MAE, y=MFE, цвет = win/loss
- [ ] **Equity Curve** — линейный график баланса по времени (из бэктеста и live)
- [ ] Коммит: `feat(sim-23): diagnostic dashboard UI`

---

## Phase 5 — Финальная проверка

### 5.1 Регрессия и интеграция

- [ ] Полный тест-сьют: `pytest tests/ -v` → 0 новых поломок
- [ ] Backward compatibility: создать тест с записью signal_results где все новые поля = NULL → не падает
- [ ] Проверить: старые эндпоинты v3 (SIM-14, SIM-15) работают без изменений

### 5.2 Финальный бэктест

- [ ] Запустить бэктест с ВСЕМИ изменениями v4 (SIM-17+18+19+20+21)
- [ ] Создать `docs/BACKTEST_RESULTS_V2.md`:
  - Полные метрики: total_trades, win_rate, PF, max_drawdown
  - LONG/SHORT ratio
  - Score bucket analysis: min profitable bucket
  - Сравнение с V1 (Phase 2) и метриками успеха из спеки §5
  - **Таблица: V1 baseline → V2 with all fixes → дельта**

### 5.3 Оценка метрик успеха

- [ ] Сравни с таблицей из `SPEC_SIMULATOR_V4.md` §5:
  - [ ] LONG/SHORT ratio: 30-70% / 30-70%?
  - [ ] Закрытых сделок ≥ 100?
  - [ ] Win rate ≥ 40%?
  - [ ] Profit Factor ≥ 1.0?
  - [ ] Avg MAE / SL distance < 80%?
  - [ ] Partial close > 20% от TP1 hits?
  - [ ] Correlated duplicates = 0?
- [ ] Если win rate < 40% или PF < 1.0:
  - [ ] Анализ by_score_bucket: какой min composite_score даёт PF > 1.0
  - [ ] Зафиксировать рекомендацию по пороговому score для v5
- [ ] Коммит: `docs: final backtest results v4`

---

## Итого: 8 SIM задач, ~70 подзадач, ~35 тестов

**Критический путь:** Phase 0 → SIM-17 → SIM-19 → SIM-18 → SIM-21 → SIM-22 → остальные

**Зависимости:**
- SIM-18 зависит от SIM-19 (SL distance определяет TP distance)
- SIM-22 зависит от SIM-17 (bias фикс нужен до бэктеста)
- SIM-20 бэктест (3.2) зависит от SIM-22 (движок готов)
- Phase 5 зависит от всех предыдущих фаз
