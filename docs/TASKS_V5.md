# Trade Simulator v5 — Задачи

> Отмечай `[x]` по мере выполнения. Работай сверху вниз, по фазам.
> Перед каждой задачей читай `docs/SPEC_SIMULATOR_V5.md` (раздел SIM-XX).
> После каждого блока — запусти тесты: `pytest tests/test_simulator_v5.py -v`
>
> **Основание:** `docs/REQUIREMENTS.md` — бэктест Oct 2025 – Mar 2026.
> WR 41.1%, PF 1.004, DD 50.2% → цель: WR 46–52%, PF 1.4–2.0, DD 20–35%.

---

## Phase 0 — Подготовка инфраструктуры и baseline

> Завершение незаконченных задач v4 + подготовка к v5.

### 0.1 Baseline бэктест (завершение v4 Phase 2.4)
- [ ] Запустить `POST /api/v2/backtest/run` с параметрами:
  - symbols: ["EURUSD=X", "GBPUSD=X", "AUDUSD=X", "BTC/USDT", "ETH/USDT", "SPY"]
  - timeframe: "H1", start_date: "2024-01-01", end_date: "2025-12-31"
  - account_size: 1000.0, apply_slippage: true, apply_swap: true
- [ ] Зафиксировать baseline: total_trades, win_rate, PF, max_drawdown, LONG/SHORT ratio
- [ ] Создать `docs/BACKTEST_RESULTS_V1.md` с baseline данными
- [ ] Коммит: `docs: baseline backtest results v4`

### 0.2 Подготовка тестовой инфраструктуры v5
- [x] Создай `tests/test_simulator_v5.py` (пустой файл с импортами и базовыми фикстурами)
- [x] Добавь фикстуры: `mock_instrument_forex`, `mock_instrument_crypto`, `mock_d1_candles`, `mock_ta_indicators`
- [x] Проверь: `pytest tests/test_simulator_v5.py -v` → 0 tests, 0 errors
- [x] Коммит: `feat(v5): test infrastructure scaffold`

---

## Phase 1 — Критические фильтры (P1)

> Ожидаемый эффект: +5..11% WR, PF 1.4–1.6.
> Каждый фильтр — отдельный коммит.

### 1.1 SIM-25: Порог composite score ±15 (крипто ±20)

- [x] В `src/config.py`: добавь `MIN_COMPOSITE_SCORE = 15`, `MIN_COMPOSITE_SCORE_CRYPTO = 20`
- [x] В `src/signals/signal_engine.py` → `generate_signal()`: после composite_score — проверка порога
  - Определить `market` инструмента, выбрать threshold
  - `if abs(composite_score) < threshold: return None`
  - `logger.debug(f"[SIM-25] Score {composite_score} below threshold {threshold}")`
- [x] В `src/backtesting/backtest_engine.py` → `_generate_signal()`: аналогичная проверка
- [x] Тест: `test_sim25_score_below_threshold_rejected`
- [x] Тест: `test_sim25_score_above_threshold_accepted`
- [x] Тест: `test_sim25_crypto_higher_threshold`
- [x] Тест: `test_sim25_threshold_from_config`
- [x] Коммит: `feat(sim-25): raise composite score threshold ±15/±20`

### 1.2 SIM-26: Запрет торговли в RANGING

- [x] В `src/signals/signal_engine.py`: добавь `BLOCKED_REGIMES = ["RANGING"]` в начало файла
- [x] В `generate_signal()`: после определения regime → `if regime in BLOCKED_REGIMES: return None`
- [x] `logger.info(f"[SIM-26] Skipping: {regime} regime for {symbol}")`
- [x] В `src/backtesting/backtest_engine.py` → аналогично
- [x] Тест: `test_sim26_ranging_blocked`
- [x] Тест: `test_sim26_trend_allowed`
- [x] Тест: `test_sim26_volatile_allowed`
- [x] Тест: `test_sim26_blocked_regimes_configurable`
- [x] Коммит: `feat(sim-26): block RANGING regime signals`

### 1.3 SIM-28: Instrument overrides

> Делаем ДО SIM-27, т.к. SIM-27 может использовать per-symbol config.

- [x] В `src/config.py`: добавь `INSTRUMENT_OVERRIDES` dict (BTC, ETH, GBPUSD, USDCHF)
- [x] В `src/signals/risk_manager_v2.py` → `calculate_levels_for_regime()`:
  - Принять параметр `overrides: dict = None`
  - Если override.sl_atr_multiplier → использовать вместо ATR_SL_MULTIPLIER_MAP
- [x] В `src/signals/signal_engine.py` → `generate_signal()`:
  - Загрузить overrides для текущего символа
  - Если override.min_composite_score → использовать как threshold (приоритет над глобальным)
  - Если override.allowed_regimes → проверить текущий regime
- [x] В `src/backtesting/backtest_engine.py` → те же overrides
- [x] Тест: `test_sim28_btc_wider_sl`
- [x] Тест: `test_sim28_btc_higher_threshold`
- [x] Тест: `test_sim28_btc_only_strong_trend`
- [x] Тест: `test_sim28_gbpusd_higher_threshold`
- [x] Тест: `test_sim28_no_override_default`
- [x] Коммит: `feat(sim-28): per-instrument parameter overrides`

### 1.4 SIM-27: D1 MA200 trend filter

- [x] В `src/signals/signal_engine.py`: новый метод `_check_d1_trend_alignment(symbol, direction, timeframe) -> bool`
  - Получить D1 candles из БД (последние 200)
  - Рассчитать MA200 = mean(close[-200:])
  - LONG: допустим если close > MA200; SHORT: если close < MA200
  - Для M1/M5/M15: фильтр не применяется
  - Для D1: проверять W1 MA50
  - Нет данных → пропустить с warning
- [x] В `src/backtesting/backtest_engine.py` → тот же фильтр, D1 candles из slice (NO LOOKAHEAD)
- [x] Тест: `test_sim27_long_blocked_below_ma200`
- [x] Тест: `test_sim27_short_blocked_above_ma200`
- [x] Тест: `test_sim27_long_allowed_above_ma200`
- [x] Тест: `test_sim27_no_d1_data_passthrough`
- [x] Тест: `test_sim27_m15_no_filter`
- [x] Коммит: `feat(sim-27): D1 MA200 trend alignment filter`

**Контрольная точка Phase 1:**
- [x] `pytest tests/test_simulator_v5.py -v` → все Phase 1 тесты проходят (26/26)
- [x] `pytest tests/test_simulator_v4.py tests/test_simulator_v3.py -v` → 70/71 passed (1 pre-existing failure unrelated to v5)
- [ ] Запустить бэктест с Phase 1 фильтрами → зафиксировать в `docs/BACKTEST_RESULTS_V5_P1.md`
- [ ] Сравнить с baseline: ожидаем WR +5..11%, PF 1.4–1.6, trades -25..30%
- [ ] Коммит: `docs: backtest results after Phase 1 filters`

---

## Phase 2 — Структурные фильтры (P2)

> Ожидаемый дополнительный эффект: +4..9% WR.

### 2.1 SIM-29: Volume confirmation filter

- [ ] В `src/signals/signal_engine.py`: новый метод `_check_volume_confirmation(df) -> bool`
  - `vol_ma20 = df["volume"].rolling(20).mean().iloc[-1]`
  - `current_vol = df["volume"].iloc[-1]`
  - `if current_vol < vol_ma20 * 1.2: return False`
  - Если все volume == 0 → return True (фильтр не применяется)
  - Если < 20 свечей → return True
- [ ] Вызов в `generate_signal()` ПЕРЕД scoring
- [ ] В `src/backtesting/backtest_engine.py` → аналогично
- [ ] Тест: `test_sim29_volume_above_threshold_passes`
- [ ] Тест: `test_sim29_volume_below_threshold_blocked`
- [ ] Тест: `test_sim29_zero_volume_passthrough`
- [ ] Тест: `test_sim29_insufficient_data_passthrough`
- [ ] Коммит: `feat(sim-29): volume confirmation filter`

### 2.2 SIM-30: Momentum alignment (RSI/MACD)

- [ ] В `src/signals/signal_engine.py`: новый метод `_check_momentum_alignment(ta_indicators, direction) -> bool`
  - LONG: RSI(14) > 50 И MACD line > Signal line
  - SHORT: RSI(14) < 50 И MACD line < Signal line
  - Нет данных → return True (не блокировать)
- [ ] Вызов в `generate_signal()` ПОСЛЕ определения direction
- [ ] В `src/backtesting/backtest_engine.py` → аналогично
- [ ] Тест: `test_sim30_long_momentum_confirmed`
- [ ] Тест: `test_sim30_long_momentum_rejected_rsi`
- [ ] Тест: `test_sim30_long_momentum_rejected_macd`
- [ ] Тест: `test_sim30_missing_data_passthrough`
- [ ] Коммит: `feat(sim-30): momentum alignment filter RSI/MACD`

### 2.3 SIM-31: Min signal strength = BUY

- [ ] В `src/signals/signal_engine.py`:
  - `ALLOWED_SIGNAL_STRENGTHS = {"BUY", "STRONG_BUY", "SELL", "STRONG_SELL"}`
  - После определения signal_strength: `if strength not in ALLOWED_SIGNAL_STRENGTHS: return None`
  - `logger.debug(f"[SIM-31] Filtered weak signal: {strength} for {symbol}")`
- [ ] Тест: `test_sim31_strong_buy_allowed`
- [ ] Тест: `test_sim31_buy_allowed`
- [ ] Тест: `test_sim31_weak_buy_rejected`
- [ ] Тест: `test_sim31_hold_rejected`
- [ ] Коммит: `feat(sim-31): minimum signal strength filter`

### 2.4 SIM-32: Weekday filter

- [ ] В `src/signals/signal_engine.py`: новый метод `_check_weekday_filter(timestamp, market) -> bool`
  - Mon 00:00–10:00 UTC: blocked (forex, stocks)
  - Fri 18:00–23:59 UTC: blocked (forex, stocks)
  - Crypto: exempt from Mon filter
  - `WEEKDAY_FILTER` dict с конфигурируемыми параметрами
- [ ] В `src/backtesting/backtest_engine.py` → аналогично
- [ ] Тест: `test_sim32_monday_morning_blocked`
- [ ] Тест: `test_sim32_monday_afternoon_allowed`
- [ ] Тест: `test_sim32_friday_evening_blocked`
- [ ] Тест: `test_sim32_monday_crypto_allowed`
- [ ] Коммит: `feat(sim-32): weekday filter Mon/Fri`

### 2.5 SIM-33: Economic calendar в бэктесте

- [ ] В `src/database/crud.py`: `async def get_economic_events_in_range(db, start, end, impact="HIGH") -> list`
- [ ] В `src/backtesting/backtest_engine.py` → `_simulate_symbol()`:
  - Перед генерацией сигнала: проверить HIGH events ±2h
  - Нет событий в БД → фильтр пропускается
- [ ] Тест: `test_sim33_high_impact_event_blocks_signal`
- [ ] Тест: `test_sim33_no_event_allows_signal`
- [ ] Тест: `test_sim33_no_historical_events_passthrough`
- [ ] Коммит: `feat(sim-33): economic calendar filter in backtest`

**Контрольная точка Phase 2:**
- [ ] `pytest tests/test_simulator_v5.py -v` → все Phase 1+2 тесты проходят
- [ ] `pytest tests/test_simulator_v4.py tests/test_simulator_v3.py -v` → 0 regressions
- [ ] Запустить бэктест → зафиксировать в `docs/BACKTEST_RESULTS_V5_P2.md`
- [ ] Сравнить с Phase 1: ожидаем WR +4..9%, trades ещё -10..15%
- [ ] Коммит: `docs: backtest results after Phase 2 filters`

---

## Phase 3 — Управление позициями (P3)

> Ожидаемый эффект: +1..5% WR, -10..22% drawdown.

### 3.1 SIM-34: Breakeven на 50% пути к TP1

- [ ] В `src/tracker/signal_tracker.py` → `_partial_close()`:
  - `BREAKEVEN_BUFFER_RATIO = Decimal("0.5")`
  - LONG: `new_sl = entry + BREAKEVEN_BUFFER_RATIO * (tp1 - entry)`
  - SHORT: `new_sl = entry - BREAKEVEN_BUFFER_RATIO * (entry - tp1)`
  - Заменить текущий `new_sl = entry_price`
- [ ] В `src/signals/trade_lifecycle.py` → обновить breakeven логику аналогично
- [ ] Тест: `test_sim34_breakeven_with_buffer_long`
- [ ] Тест: `test_sim34_breakeven_with_buffer_short`
- [ ] Тест: `test_sim34_buffer_configurable`
- [ ] Тест: `test_sim34_remaining_position_survives_normal_pullback`
- [ ] Коммит: `feat(sim-34): breakeven at 50% path to TP1`

### 3.2 SIM-35: Time-based exit

- [ ] В `src/tracker/signal_tracker.py` → `check_signal()`:
  - `TIME_EXIT_CANDLES = {"H1": 48, "H4": 20, "D1": 10}`
  - После MAE early exit check, ДО SL/TP check
  - `candles_elapsed >= TIME_EXIT_CANDLES[tf] AND unrealized_pnl <= 0`
  - `exit_reason = "time_exit"`
- [ ] Тест: `test_sim35_time_exit_h1_48_candles`
- [ ] Тест: `test_sim35_time_exit_no_trigger_profitable`
- [ ] Тест: `test_sim35_time_exit_no_trigger_early`
- [ ] Тест: `test_sim35_time_exit_h4_20_candles`
- [ ] Коммит: `feat(sim-35): time-based exit for stale positions`

### 3.3 SIM-36: S/R snapping в бэктесте

- [ ] В `src/backtesting/backtest_engine.py` → `_recalc_sl_tp()`:
  - Получать `support_levels` и `resistance_levels` из TAEngine
  - Передавать в `RiskManagerV2.calculate_levels_for_regime()`
- [ ] Тест: `test_sim36_backtest_sl_snaps_to_support`
- [ ] Тест: `test_sim36_backtest_no_sr_levels_fallback`
- [ ] Коммит: `feat(sim-36): S/R snapping for SL in backtest`

### 3.4 SIM-37: Обновление swap-ставок

- [ ] Создать `config/swap_rates.json` с актуальными ставками + AUDUSD
- [ ] В `src/tracker/signal_tracker.py`:
  - Загружать ставки из JSON при инициализации
  - Если файл не найден → fallback к хардкоду с warning
  - Проверять дату: если > 90 дней → `logger.warning("[SIM-37] Swap rates are stale")`
- [ ] Тест: `test_sim37_swap_rates_from_json`
- [ ] Тест: `test_sim37_swap_rates_fallback`
- [ ] Тест: `test_sim37_swap_rates_stale_warning`
- [ ] Коммит: `feat(sim-37): externalize swap rates to JSON`

**Контрольная точка Phase 3:**
- [ ] `pytest tests/test_simulator_v5.py -v` → все Phase 1+2+3 тесты проходят
- [ ] 0 regressions в v4/v3 тестах
- [ ] Запустить бэктест → `docs/BACKTEST_RESULTS_V5_P3.md`
- [ ] Коммит: `docs: backtest results after Phase 3`

---

## Phase 4 — Новые источники данных (P4)

> Ожидаемый эффект: +2..5% WR (форекс), +3..5% WR (крипто).
> Каждый SIM создаёт новый collector или расширяет существующий.

### 4.1 SIM-38: DXY real-time фильтр

- [ ] В `src/collectors/realtime_collector.py`: добавить сбор DXY (DX-Y.NYB) через yfinance
  - Интервал: 1 минута
  - Рассчитать RSI(14) в памяти
  - Хранить last_dxy_rsi в cache
- [ ] В `src/signals/signal_engine.py`: новый метод `_check_dxy_alignment(direction, symbol) -> bool`
  - DXY RSI > 55: block LONG для USD long side (EURUSD, GBPUSD, AUDUSD, NZDUSD)
  - DXY RSI < 45: block SHORT для USD long side
  - Нейтральная зона 45–55: не фильтровать
  - Нет данных → не блокировать
- [ ] Тест: `test_sim38_dxy_strong_blocks_usd_long_side`
- [ ] Тест: `test_sim38_dxy_strong_allows_usd_base`
- [ ] Тест: `test_sim38_dxy_neutral_no_filter`
- [ ] Тест: `test_sim38_dxy_no_data_passthrough`
- [ ] Коммит: `feat(sim-38): DXY real-time filter for forex`

### 4.2 SIM-39: Fear & Greed Index для крипто

- [ ] Создать `src/collectors/fear_greed_collector.py`:
  - API: `https://api.alternative.me/fng/?limit=1`
  - Хранить в `macro_data` (indicator="FEAR_GREED", country="GLOBAL")
  - Fallback: если API недоступен → не влиять
- [ ] В `src/scheduler/jobs.py`: добавить job раз в час
- [ ] В `src/signals/signal_engine.py`: при расчёте composite для crypto:
  - value <= 20: +5 к composite для LONG
  - value >= 80: +5 к composite для SHORT
  - 21–79: 0
- [ ] Тест: `test_sim39_extreme_fear_boosts_long`
- [ ] Тест: `test_sim39_extreme_greed_boosts_short`
- [ ] Тест: `test_sim39_neutral_no_effect`
- [ ] Тест: `test_sim39_non_crypto_no_effect`
- [ ] Коммит: `feat(sim-39): Fear & Greed Index for crypto`

### 4.3 SIM-40: Funding Rate extreme filter

- [ ] В `src/signals/signal_engine.py`:
  - Получить funding_rate из `order_flow_data` (уже собирается)
  - FR > +0.1%: LONG composite -10
  - FR < -0.1%: SHORT composite -10
  - Только для crypto
- [ ] Тест: `test_sim40_high_funding_penalizes_long`
- [ ] Тест: `test_sim40_negative_funding_penalizes_short`
- [ ] Тест: `test_sim40_normal_funding_no_effect`
- [ ] Тест: `test_sim40_non_crypto_no_effect`
- [ ] Коммит: `feat(sim-40): funding rate extreme filter for crypto`

### 4.4 SIM-41: COT Data для форекс

- [ ] Создать `src/collectors/cot_collector.py`:
  - Данные из CFTC (публичный, еженедельно)
  - Хранить в `macro_data` (indicator="COT_{symbol}", country="US")
  - Парсинг: net_positions для non-commercials, change_week
- [ ] В `src/scheduler/jobs.py`: weekly job (пятница после 20:00 UTC)
- [ ] В `src/signals/fa_engine.py` → `calculate_fa_score()`:
  - Non-commercials net long + увеличивают → +5
  - Non-commercials net short + увеличивают → -5
  - Нет данных → 0
- [ ] Тест: `test_sim41_cot_net_long_boosts_fa`
- [ ] Тест: `test_sim41_cot_net_short_penalizes_fa`
- [ ] Тест: `test_sim41_cot_no_data_neutral`
- [ ] Коммит: `feat(sim-41): COT data integration for forex`

**Контрольная точка Phase 4:**
- [ ] Все Phase 4 тесты проходят
- [ ] 0 regressions
- [ ] Бэктест → `docs/BACKTEST_RESULTS_V5_P4.md`
- [ ] Коммит: `docs: backtest results after Phase 4`

---

## Phase 5 — Достоверность бэктеста (P5)

> Структурные улучшения бэктеста для точности и гибкости.

### 5.1 SIM-42: Унификация фильтров live/backtest

- [ ] Создать `src/signals/filter_pipeline.py`:
  - Класс `SignalFilterPipeline` со всеми фильтрами:
    - `check_score_threshold()`
    - `check_regime()`
    - `check_d1_trend()`
    - `check_volume()`
    - `check_momentum()`
    - `check_weekday()`
    - `check_calendar()`
  - Метод `run_all(signal_context) -> tuple[bool, str]` — возвращает (passed, reason)
  - Каждый фильтр может быть включен/выключен через параметр
- [ ] В `src/signals/signal_engine.py` → использовать `SignalFilterPipeline`
- [ ] В `src/backtesting/backtest_engine.py` → использовать тот же `SignalFilterPipeline`
- [ ] Тест: `test_sim42_backtest_applies_all_filters`
- [ ] Тест: `test_sim42_live_and_backtest_same_result`
- [ ] Коммит: `refactor(sim-42): unified SignalFilterPipeline`

### 5.2 SIM-43: Параметризация бэктеста

- [ ] В `src/backtesting/backtest_params.py` → расширить `BacktestParams`:
  - `apply_ranging_filter: bool = True`
  - `apply_d1_trend_filter: bool = True`
  - `apply_volume_filter: bool = True`
  - `apply_weekday_filter: bool = True`
  - `apply_momentum_filter: bool = True`
  - `apply_calendar_filter: bool = True`
  - `min_composite_score: Optional[float] = None`
- [ ] В `src/backtesting/backtest_engine.py` → передавать params в SignalFilterPipeline
- [ ] Тест: `test_sim43_backtest_with_all_filters`
- [ ] Тест: `test_sim43_backtest_without_filters`
- [ ] Тест: `test_sim43_custom_score_threshold`
- [ ] Коммит: `feat(sim-43): parameterized backtest filters`

### 5.3 SIM-44: Расширенные метрики бэктеста

- [ ] В `src/backtesting/backtest_engine.py` → расширить `_compute_summary()`:
  - `win_rate_long_pct`, `win_rate_short_pct`
  - `avg_win_duration_minutes`, `avg_loss_duration_minutes`
  - `by_weekday`: {0..4: {trades, wins, pnl_usd}}
  - `by_hour_utc`: {0..23: {trades, win_rate_pct}}
  - `by_regime`: {regime: {trades, wins, pnl_usd}}
  - `sl_hit_count`, `tp_hit_count`, `mae_exit_count`, `time_exit_count`
  - `avg_mae_pct_of_sl`
- [ ] Тест: `test_sim44_extended_metrics_present`
- [ ] Тест: `test_sim44_win_rate_by_direction`
- [ ] Тест: `test_sim44_by_regime_breakdown`
- [ ] Коммит: `feat(sim-44): extended backtest metrics`

---

## Phase 6 — Финальная проверка

### 6.1 Регрессия

- [ ] `pytest tests/test_simulator_v5.py tests/test_simulator_v4.py tests/test_simulator_v3.py -v`
  → Все тесты проходят, 0 regressions
- [ ] Подсчёт: v5 тестов ~70, v4 тестов 41, v3 тестов 30 → ~141 total

### 6.2 Финальный бэктест

- [ ] Запустить бэктест с ВСЕМИ v5 фильтрами активными
- [ ] Создать `docs/BACKTEST_RESULTS_FINAL.md`:
  - **Таблица эволюции:**
    | Метрика | Baseline (v4) | +P1 | +P2 | +P3 | +P4 | Final |
  - LONG/SHORT ratio, trades/month, WR, PF, DD
  - by_symbol: какие инструменты стали прибыльными
  - by_regime: какие режимы дают edge
  - Рекомендации для v6

### 6.3 Оценка целевых метрик

- [ ] Win rate ≥ 46%?
- [ ] Profit factor ≥ 1.4?
- [ ] Max drawdown ≤ 35%?
- [ ] Trades/month в диапазоне 55–75?
- [ ] LONG/SHORT ratio: 30–70% / 30–70%?
- [ ] Если PF < 1.4: анализ by_filter — какой фильтр даёт/не даёт edge
- [ ] Коммит: `docs: final v5 backtest results and analysis`

---

## Итого: 20 SIM задач (SIM-25..SIM-44), ~70 тестов

**Критический путь:** Phase 0 → SIM-25 → SIM-26 → SIM-28 → SIM-27 → бэктест → Phase 2 → ... → Phase 5

**Зависимости:**
- SIM-28 (overrides) расширяет SIM-25 (threshold) → делать после
- SIM-27 (D1 MA200) зависит от D1 данных в price_data
- SIM-42 (unification) зависит от SIM-25..SIM-33 (все фильтры реализованы)
- SIM-43 (parameterization) зависит от SIM-42 (pipeline)
- SIM-44 (metrics) может содержать SIM-35 (time_exit) exit_reason → лучше после Phase 3

**Бэктест-циклы:**
- После Phase 1 → BACKTEST_RESULTS_V5_P1.md
- После Phase 2 → BACKTEST_RESULTS_V5_P2.md
- После Phase 3 → BACKTEST_RESULTS_V5_P3.md
- После Phase 4 → BACKTEST_RESULTS_V5_P4.md
- Финальный → BACKTEST_RESULTS_FINAL.md
