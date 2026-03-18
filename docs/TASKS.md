# Trade Simulator v3 — Задачи

> Отмечай `[x]` по мере выполнения. Работай сверху вниз, по фазам.  
> Перед каждой задачей читай `docs/SPEC_SIMULATOR_V3.md` (раздел SIM-XX).  
> После каждого блока — запусти тесты: `pytest tests/test_simulator_v3.py -v`

---

## Phase 1 — Фундамент: схема БД и динамический баланс (P0)

### 1.1 Alembic миграция

- [x] Изучи текущие модели в `src/database/models.py` — найди `SignalResult`, `VirtualPortfolio` и их текущие поля
- [x] Изучи существующие миграции в `alembic/versions/` — определи формат именования и последнюю ревизию
- [x] Создай `alembic/versions/xxxx_simulator_v3.py` — **одна миграция** со всеми изменениями:
  - `signal_results`: добавить `candle_high_at_exit NUMERIC(18,8)`, `candle_low_at_exit NUMERIC(18,8)`, `exit_slippage_pips NUMERIC(8,4)`, `swap_pips NUMERIC(14,4)`, `swap_usd NUMERIC(14,4)`, `composite_score NUMERIC(8,4)` — все nullable
  - `virtual_portfolio`: добавить `unrealized_pnl_usd NUMERIC(14,4)` nullable, `accrued_swap_pips NUMERIC(14,4) DEFAULT 0`, `accrued_swap_usd NUMERIC(14,4) DEFAULT 0`, `last_swap_date DATE` nullable, `account_balance_at_entry NUMERIC(14,4)` nullable
  - Новая таблица `virtual_account`: `id SERIAL PK`, `initial_balance NUMERIC(14,4) NOT NULL DEFAULT 1000.0`, `current_balance NUMERIC(14,4) NOT NULL DEFAULT 1000.0`, `peak_balance NUMERIC(14,4) NOT NULL DEFAULT 1000.0`, `total_realized_pnl NUMERIC(14,4) NOT NULL DEFAULT 0.0`, `total_trades INTEGER NOT NULL DEFAULT 0`, `updated_at TIMESTAMPTZ DEFAULT NOW()`
  - `INSERT INTO virtual_account ...` — начальная запись
  - Включи `downgrade()` для отката (drop table + drop columns)
- [ ] Тест: выполни `alembic upgrade head` без ошибок

### 1.2 SQLAlchemy модели

- [x] В `src/database/models.py`: добавь новые поля в класс `SignalResult` (6 полей)
- [x] В `src/database/models.py`: добавь новые поля в класс `VirtualPortfolio` (5 полей)
- [x] В `src/database/models.py`: создай класс `VirtualAccount` (модель новой таблицы)
- [x] Тест: импорт моделей без ошибок, все поля соответствуют миграции

### 1.3 CRUD-функции для VirtualAccount

- [x] В `src/database/crud.py`: добавь `async def get_virtual_account(db: AsyncSession) -> Optional[VirtualAccount]`
- [x] В `src/database/crud.py`: добавь `async def update_virtual_account(db: AsyncSession, data: dict) -> None`
- [x] В `src/database/crud.py`: добавь `async def create_virtual_account_if_not_exists(db: AsyncSession) -> VirtualAccount`
- [x] Тест: `test_sim16_account_initialized_on_first_run` — создание записи при отсутствии

### 1.4 SIM-16: Динамический баланс — открытие позиции

- [x] Найди в `src/tracker/signal_tracker.py` метод, отвечающий за открытие позиции (создание записи в `virtual_portfolio`)
- [x] Добавь получение `virtual_account.current_balance` перед созданием позиции
- [x] Записывай `account_balance_at_entry = current_balance` в `virtual_portfolio`
- [x] Тест: `test_sim16_position_sizing_from_balance` — при балансе $900, P&L считается от $900

### 1.5 SIM-16: Динамический баланс — закрытие позиции

- [x] Создай функцию `_update_account_balance(db, realized_pnl_usd)` в `signal_tracker.py` (см. спеку §16.5)
- [x] Интегрируй вызов `_update_account_balance()` в `_close_signal()` — после расчёта P&L
- [x] При partial close (SIM-07): вызывай `_update_account_balance()` с `partial_close_pnl_usd`
- [x] Обнови `peak_balance` = max(peak, new_balance) при каждом обновлении
- [x] Тест: `test_sim16_account_balance_updates_on_close` — убыточная сделка уменьшает баланс
- [x] Тест: `test_sim16_account_balance_compounds` — две сделки подряд, баланс накапливается
- [x] Тест: `test_sim16_partial_close_updates_balance_twice` — два обновления при partial close
- [x] Тест: `test_sim16_drawdown_calculation` — peak=$1100, current=$950 → drawdown=13.64%

### 1.6 SIM-16: Обновление `_pnl_usd` с учётом баланса

- [x] Найди `_pnl_usd()` в `trade_simulator.py` (или `signal_tracker.py`)
- [x] Добавь параметр `account_balance: Optional[Decimal] = None`
- [x] Если `account_balance is not None` → использовать его вместо `ACCOUNT_SIZE`
- [x] Во всех вызовах `_pnl_usd()` при закрытии — передавай `position.account_balance_at_entry`
- [x] Тест: `test_sim16_legacy_position_fallback` — `account_balance_at_entry=NULL` → fallback к VIRTUAL_ACCOUNT_SIZE_USD

---

## Phase 2 — Точность исполнения SL/TP (P0)

### 2.1 SIM-09: Получение candle High/Low

- [x] Изучи, как сейчас получается `current_price` в тике симулятора (signal_tracker.py)
- [x] Создай метод `_get_candle_prices(db, instrument_id, timeframe) -> tuple[Decimal, Decimal, Decimal]` — возвращает (last_close, candle_high, candle_low) последней завершённой свечи
- [x] Источник: таблица `price_data` (поля `high`, `low`, `close`), фильтр по `instrument_id` + `timeframe`, ORDER BY timestamp DESC, LIMIT 1 (убедись, что берёшь **завершённую** свечу, а не текущую открытую)
- [x] Тест: mock price_data с 3 свечами → возвращается корректная последняя

### 2.2 SIM-09: Проверка SL/TP по High/Low

- [x] Найди метод проверки SL/TP в signal_tracker.py (обычно в цикле тика)
- [x] Расширь логику: SL check LONG — `current_price <= SL OR candle_low <= SL`
- [x] Расширь логику: TP check LONG — `current_price >= TP OR candle_high >= TP`
- [x] Аналогично для SHORT (инвертированные проверки, см. спеку §SIM-09)
- [x] При одновременном пробое SL и TP → `exit_reason = "sl_hit"` (worst case)
- [x] При SL hit по candle_low: `exit_price = stop_loss` (точно по уровню, до slippage)
- [x] При TP hit по candle_high: `exit_price = take_profit_level` (точно по уровню)
- [x] Сохраняй `candle_high_at_exit` и `candle_low_at_exit` в `signal_results`
- [x] Тест: `test_sim09_sl_via_candle_low` — LONG: last > SL, candle_low < SL → sl_hit
- [x] Тест: `test_sim09_tp_via_candle_high` — LONG: last < TP, candle_high > TP → tp1_hit
- [x] Тест: `test_sim09_both_hit_worst_case` — гэп пробил оба → sl_hit

### 2.3 SIM-10: Slippage при SL exit

- [x] Добавь константы в signal_tracker.py: `SL_SLIPPAGE_PIPS`, `SL_SLIPPAGE_CRYPTO_PCT` (значения из спеки §SIM-10)
- [x] Создай `_apply_sl_slippage(sl_price, direction, market, pip_size) -> Decimal` (формула из спеки)
- [x] Применяй slippage при `exit_reason in ("sl_hit", "trailing_sl_hit")`
- [x] НЕ применяй при `tp1_hit`, `tp2_hit`, `tp3_hit`, `expired`
- [x] Сохраняй `exit_slippage_pips` в `signal_results`
- [x] Тест: `test_sim10_sl_slippage_forex` — LONG EURUSD: exit = SL - 1 pip
- [x] Тест: `test_sim10_sl_slippage_crypto` — LONG BTC: exit = SL × (1 - 0.001)
- [x] Тест: `test_sim10_tp_no_slippage` — TP hit: exit = точно TP

---

## Phase 3 — Unrealized P&L + живой ATR (P1)

### 3.1 SIM-12: Unrealized P&L с position sizing

- [x] Найди `_update_virtual_unrealized()` в signal_tracker.py
- [x] Добавь параметр `position: VirtualPortfolio` (для доступа к `size_pct`, `size_remaining_pct`, `account_balance_at_entry`)
- [x] Рассчитай `effective_size = size_pct * remaining` (учёт partial close)
- [x] Рассчитай `unrealized_usd = balance * (effective_size / 100) * (move_pct / 100)` где `balance = position.account_balance_at_entry or ACCOUNT_SIZE`
- [x] Сохраняй `unrealized_pnl_usd` в `virtual_portfolio` (новое поле)
- [x] `unrealized_pnl_pct` по-прежнему хранит % движения цены (для отображения)
- [x] Тест: `test_sim12_unrealized_usd_with_size` — size=2%, move=+1.5% → +$0.30
- [x] Тест: `test_sim12_unrealized_after_partial` — remaining=0.5, move=+2% → +$0.20

### 3.2 SIM-11: Живой ATR для trailing stop

- [x] Создай `_get_live_atr(db, instrument_id, timeframe, period=14) -> Optional[Decimal]` в signal_tracker.py
- [x] Реализуй Wilder's ATR: `TR = max(H-L, |H-prevC|, |L-prevC|)`, smoothing `(prev×13 + TR) / 14`
- [x] Нужно `period + 1` свечей (15 для ATR(14))
- [x] Реализуй fallback chain: live ATR(signal TF) → live ATR(H1) → snapshot ATR → 14 × pip_size
- [x] Замени текущее чтение ATR из snapshot на вызов `_get_live_atr()` с fallback
- [x] Тест: `test_sim11_live_atr_calculation` — 15 свечей → корректный ATR(14) по Wilder
- [x] Тест: `test_sim11_atr_fallback_chain` — нет данных → snapshot → 14×pip_size

---

## Phase 4 — Аналитика сигналов (P0-P1)

### 4.1 SIM-14: Score → Outcome эндпоинт

- [x] Убедись, что `composite_score` копируется в `signal_results` при `_close_signal()` (из `signal.composite_score`)
- [x] В `src/database/crud.py`: создай запрос для агрегации по score buckets — GROUP BY диапазонам, COUNT, SUM(pnl_usd WHERE win), SUM(pnl_usd WHERE loss), AVG(duration), AVG(mfe), AVG(mae)
- [x] Бакеты: `strong_sell ≤-15`, `sell -15..-10`, `weak_sell -10..-7`, `neutral -7..+7`, `weak_buy +7..+10`, `buy +10..+15`, `strong_buy ≥+15`
- [x] В `src/api/routes_v2.py`: создай `GET /api/v2/simulator/score-analysis`
- [x] Реализуй `threshold_recommendations`: `suggested_min_score_for_positive_edge` (min bucket с PF > 1.0 и total ≥ 5), `score_with_best_win_rate` (max win_rate при total ≥ 5)
- [x] Бакеты с < 3 сделками → `insufficient_data: true`
- [x] Тест: `test_sim14_score_buckets_assignment` — composite=8.5 → бакет "weak_buy"
- [x] Тест: `test_sim14_threshold_recommendation` — бакет с PF > 1.0 определяет suggested_min

### 4.2 SIM-15: Breakdown эндпоинт

- [x] В `src/database/crud.py`: создай универсальный запрос для breakdown по измерению (timeframe, direction, exit_reason, market, month)
- [x] В `src/api/routes_v2.py`: создай `GET /api/v2/simulator/breakdown?by=...`
- [x] Валидация: `by` ∈ {timeframe, direction, exit_reason, market, month}, иначе 400
- [x] Для `by=month`: GROUP BY `date_trunc('month', exit_at)`
- [x] Для `by=market`: JOIN с `instruments` таблицей
- [x] Каждая строка: key, total, wins, losses, breakevens, win_rate_pct, profit_factor, avg_pnl_usd, avg_duration_minutes, avg_composite_score
- [x] `profit_factor = gross_wins / |gross_losses|` (0 если нет losses)
- [x] Тест: mock 10 сделок с разными TF → breakdown возвращает корректные группы

### 4.3 SIM-15: Расширение `/simulator/stats`

- [x] Добавь в ответ `/simulator/stats` новые поля:
  - `cancelled_count` — кол-во cancelled сигналов
  - `avg_duration_minutes` — средняя длительность закрытых сделок
  - `avg_mfe_pips`, `avg_mae_pips` — средние MFE/MAE
  - `total_swap_usd` — сумма swap по закрытым сделкам
  - `best_exit_reason` — exit_reason с лучшим avg_pnl_usd
  - `account_initial_balance`, `account_current_balance`, `account_peak_balance` (из SIM-16)
  - `account_drawdown_pct` = (peak - current) / peak × 100
  - `account_total_return_pct` = (current - initial) / initial × 100
- [x] Тест: stats возвращает все новые поля без ошибок при пустой БД (нули/NULL)

---

## Phase 5 — Overnight Swap (P2)

### 5.1 SIM-13: Swap таблица и логика начисления

- [x] Добавь константы в signal_tracker.py: `SWAP_DAILY_PIPS`, `TRIPLE_SWAP_WEEKDAY = 2`, `ROLLOVER_HOUR_UTC = 22` (значения из спеки §13.1)
- [x] Создай `_apply_daily_swap(db, signal, position, instrument, now)` (спека §13.2)
- [x] Условия начисления: status=open/partial, now >= 22:00 UTC, last_swap_date != today
- [x] Среда: умножить на 3 (тройной своп)
- [x] Для crypto: `get_latest_funding_rate(db, instrument_id)` из `order_flow_data`
- [x] Добавь `get_latest_funding_rate()` в `crud.py`
- [x] Обновляй `accrued_swap_pips`, `accrued_swap_usd`, `last_swap_date` в `virtual_portfolio`
- [x] При закрытии: записывай `swap_pips`, `swap_usd` в `signal_results`; `total_pnl_usd = price_pnl_usd + accrued_swap_usd`
- [x] Тест: `test_sim13_swap_wednesday_triple` — Wed rollover: 3× swap
- [x] Тест: `test_sim13_swap_positive_carry` — USDJPY long: swap_pips > 0
- [x] Тест: `test_sim13_swap_crypto_funding` — BTC long, funding=+0.01% → вычтен

---

## Phase 6 — Регрессионные тесты и финальная проверка

### 6.1 Backward compatibility тесты

- [x] Тест: старые `signal_results` без `candle_high_at_exit` (NULL) → логика не падает
- [x] Тест: `composite_score = NULL` → не включается в score_analysis, без ошибки
- [x] Тест: `unrealized_pnl_usd = NULL` для старых позиций → API возвращает 0
- [x] Тест: `account_balance_at_entry = NULL` → fallback к VIRTUAL_ACCOUNT_SIZE_USD

### 6.2 Интеграционная проверка

- [ ] Запусти `alembic upgrade head` на чистой БД → без ошибок
- [x] Запусти все тесты: `pytest tests/ -v` → 961 passed, 35 pre-existing failures (v3 tests: 28/28 ✓)
- [x] Проверь API: `GET /simulator/stats` → новые поля присутствуют
- [x] Проверь API: `GET /simulator/score-analysis` → корректная структура
- [x] Проверь API: `GET /simulator/breakdown?by=timeframe` → корректная структура

---

## Итого: 8 SIM задач, ~50 подзадач, ~25 тестов
