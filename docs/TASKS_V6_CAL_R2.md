# Tasks: v6 Calibration Round 2

## Goal
Устранить структурные проблемы бэктеста: исключить end_of_data из PF/PnL, разблокировать SHORT, снизить time_exit с 75% до <50%, заблокировать выходные, ужесточить убыточные режимы.

## Background
Бэктест v6-cal-r1 показал PF 1.72, но 37% PnL приходится на end_of_data артефакты (реальный PF ~1.35). SHORT невозможен математически (effective threshold 19.5, max composite ~13.5). 75% сделок закрываются по time_exit. 9 выходных сделок дают -$226. STRONG_TREND_BULL и TREND_BULL — убыточные режимы.

## Analyst reports
- `analyst-requirements/v6-calibration-r2-requirements.md`
- `analyst-reports/v6-calibrated-r2-analysis.md`

---

## Phase 1: Honest Metrics (REQ-CAL2-001, REQ-CAL2-006)

### TASK-CAL2-01: Exclude end_of_data from PF and total_pnl_usd
- **REQ**: REQ-CAL2-001
- **Priority**: High
- **File**: `src/backtesting/backtest_engine.py` -> `_compute_summary()`
- **Current problem**: Строка 280: `total_pnl = sum(... for t in trades)` включает end_of_data. Строки 281-282: `gross_win` / `gross_loss` считаются по `metric_trades` (без eod), но `total_pnl` — по всем `trades`. PF считается на basis metric_trades (корректно), но `total_pnl_usd` включает eod (некорректно).
- **Changes**:
  1. Изменить строку 280: `total_pnl = sum(float(t.pnl_usd or 0) for t in metric_trades)` -- исключить eod из headline PnL
  2. Добавить отдельное поле `total_pnl_usd_incl_eod` = sum по всем trades (для полноты)
  3. Добавить поле `eod_warning: bool` = True если `end_of_data_count > total_trades * 0.05`
  4. В equity curve оставить eod (curve отражает реальный баланс), но пометить последние eod-точки флагом `"is_eod": true`
- **Tests** (`tests/test_simulator_v5.py`):
  - `test_cal2_01_pf_excludes_eod`: 5 trades (3 real + 2 eod), проверить что PF и total_pnl считаются только по 3 real
  - `test_cal2_01_eod_warning_flag`: проверить что warning=True если eod > 5%
- **Acceptance**:
  - [x] `profit_factor` и `total_pnl_usd` не включают end_of_data trades
  - [x] `end_of_data_pnl` и `total_pnl_usd_incl_eod` доступны отдельно
  - [x] `eod_warning` = True если end_of_data > 5% от total_trades

### TASK-CAL2-02: Add win_rate_pct to by_symbol and exclude eod
- **REQ**: REQ-CAL2-006
- **Priority**: Medium
- **File**: `src/backtesting/backtest_engine.py` -> `_compute_summary()`
- **Current problem**: Строки 335-343: `by_symbol` считает wins и pnl по всем trades (включая eod). Нет поля `win_rate_pct`.
- **Changes**:
  1. Изменить цикл by_symbol (строки 335-343): считать по `metric_trades` (исключить eod)
  2. Добавить в каждый entry: `"win_rate_pct": round(wins/trades*100, 2) if trades > 0 else 0.0`
  3. Аналогично: by_regime, by_weekday — тоже исключить eod trades из подсчета
- **Tests**:
  - `test_cal2_02_by_symbol_excludes_eod`: проверить что eod trade не попадает в by_symbol wins
  - `test_cal2_02_by_symbol_has_win_rate`: проверить наличие win_rate_pct
- **Acceptance**:
  - [x] by_symbol содержит win_rate_pct
  - [x] by_symbol/by_regime/by_weekday НЕ включают end_of_data trades

---

## Phase 2: SHORT и Weekend (REQ-CAL2-002, REQ-CAL2-004)

### TASK-CAL2-03: Reduce SHORT_SCORE_MULTIPLIER to 1.3
- **REQ**: REQ-CAL2-002 (Option A)
- **Priority**: High
- **File**: `src/config.py`
- **Current problem**: `SHORT_SCORE_MULTIPLIER = 2.0`. Effective threshold = 15 * 0.65 * 2.0 = 19.5. Max composite в backtest ~13.5 (0.45 * 30). SHORT = 0 trades.
- **Design decision**: Option A (разрешить SHORT с 1.3x multiplier). Effective threshold станет 15 * 0.65 * 1.3 = 12.675. Это достижимо для сильных SHORT сигналов (ta_score >= 28.2). Momentum filter (RSI < 30) остается как дополнительный гейт.
- **Changes**:
  1. `src/config.py`: изменить `SHORT_SCORE_MULTIPLIER: float = 1.3`
  2. Обновить комментарий: убрать "V6-CAL-04" reference, добавить "V6-CAL2-03: reduced from 2.0 to 1.3 to allow SHORT trades in backtest"
- **Tests**:
  - `test_cal2_03_short_threshold_reachable`: composite=-13.0, direction=SHORT, available_weight=0.45 -> должен пройти score filter
  - `test_cal2_03_short_threshold_blocks_weak`: composite=-8.0 -> должен быть заблокирован
- **Acceptance**:
  - [x] SHORT_SCORE_MULTIPLIER = 1.3
  - [x] Effective SHORT threshold в backtest = 12.675 (достижимо)
  - [ ] Бэктест производит SHORT trades > 0

### TASK-CAL2-04: Block Saturday and Sunday for all instruments
- **REQ**: REQ-CAL2-004
- **Priority**: Medium
- **File**: `src/signals/filter_pipeline.py` -> `check_weekday()`
- **Current problem**: Строки 430-440: check_weekday блокирует Mon 00-10 (кроме crypto) и Fri 18+. Суббота и воскресенье не блокируются. 9 weekend trades = -$226 с 0 wins.
- **Changes**:
  1. Добавить в начало `check_weekday()`, ДО текущих проверок:
     ```python
     if weekday in (5, 6):  # Saturday, Sunday
         return False, f"weekend_block:day={weekday}"
     ```
  2. Эта проверка применяется ко ВСЕМ market_type (включая crypto). Нет exemptions.
  3. Обновить docstring: "SIM-32 + V6-CAL2-04: blocks Saturday/Sunday for all instruments"
- **Tests**:
  - `test_cal2_04_saturday_blocked`: ts=Saturday 14:00, market_type="crypto" -> blocked
  - `test_cal2_04_sunday_blocked`: ts=Sunday 10:00, market_type="forex" -> blocked
  - `test_cal2_04_friday_still_allowed`: ts=Friday 12:00 -> allowed
- **Acceptance**:
  - [ ] Бэктест показывает 0 trades на Saturday/Sunday
  - [x] Crypto НЕ исключается из weekend block

---

## Phase 3: TP/Time Exit (REQ-CAL2-003)

### TASK-CAL2-05: Reduce R:R ratios by 30% across all regimes
- **REQ**: REQ-CAL2-003 (Option 3a)
- **Priority**: High
- **File**: `src/signals/risk_manager_v2.py` -> `REGIME_RR_MAP`
- **Current problem**: 75% trades exit by time_exit, only 10% reach TP. TP targets слишком далеко (VOLATILE 2.0x, STRONG_TREND 2.5x). Цена не доходит до TP за 24 свечи.
- **Design decision**: Снизить target_rr на 30% для всех режимов. Также снизить min_rr пропорционально (чтобы S/R snap validation не отвергал новые более близкие TP).
- **Changes**: Изменить REGIME_RR_MAP:
  ```python
  REGIME_RR_MAP = {
      "STRONG_TREND_BULL": {"min_rr": 1.4, "target_rr": 1.75},   # was 2.0/2.5
      "STRONG_TREND_BEAR": {"min_rr": 1.4, "target_rr": 1.75},   # was 2.0/2.5
      "TREND_BULL":        {"min_rr": 1.0, "target_rr": 1.4},    # was 1.5/2.0
      "TREND_BEAR":        {"min_rr": 1.0, "target_rr": 1.4},    # was 1.5/2.0
      "RANGING":           {"min_rr": 0.7, "target_rr": 0.9},    # was 1.0/1.3
      "VOLATILE":          {"min_rr": 1.0, "target_rr": 1.4},    # was 1.5/2.0
      "DEFAULT":           {"min_rr": 0.9, "target_rr": 1.05},   # was 1.3/1.5
      # Legacy aliases — same reduction
      "WEAK_TREND_BULL":   {"min_rr": 1.0, "target_rr": 1.4},
      "WEAK_TREND_BEAR":   {"min_rr": 1.0, "target_rr": 1.4},
      "HIGH_VOLATILITY":   {"min_rr": 1.0, "target_rr": 1.4},
      "LOW_VOLATILITY":    {"min_rr": 0.9, "target_rr": 1.05},
  }
  ```
- **Risk**: Снижение R:R уменьшит avg win size. Если WR не вырастет пропорционально, PF может упасть. Нужен бэктест для валидации.
- **Tests**:
  - `test_cal2_05_rr_reduced`: проверить что для regime=VOLATILE target_rr=1.4 (не 2.0)
  - Регрессия: существующие v4/v5 тесты, использующие REGIME_RR_MAP -> обновить expected values
- **Acceptance**:
  - [x] Все target_rr снижены на ~30%
  - [ ] time_exit rate в бэктесте < 50%
  - [ ] TP hit rate > 20%

### TASK-CAL2-06: Implement trailing stop at 50% MFE
- **REQ**: REQ-CAL2-003 (Option 3b) -- дополнение к 3a
- **Priority**: High
- **File**: `src/backtesting/backtest_engine.py` -> `_check_exit()`
- **Current problem**: Breakeven buffer (SIM-34) уже существует, но срабатывает только один раз и двигает SL на entry + 0.5*(tp-entry). Нет механизма trailing stop.
- **Design decision**: Добавить trailing stop. Когда MFE >= 50% от TP distance, двигаем SL на breakeven + 20% от TP distance. Это простой двухступенчатый trailing (1: breakeven @ 50% MFE, уже есть; 2: lock profit @ 50% MFE, новое).
- **Changes**:
  1. В `_check_exit()`, после проверки SL/TP но ПЕРЕД time_exit:
     ```python
     # Trailing stop: when MFE >= 50% of TP distance, move SL to lock some profit
     tp_distance = abs(tp - entry)
     mfe = Decimal(str(open_trade.get("mfe", 0)))
     if tp_distance > 0 and mfe >= tp_distance * Decimal("0.5"):
         # Move SL to breakeven + 20% of TP distance
         if direction == "LONG":
             trailing_sl = entry + tp_distance * Decimal("0.2")
             if candle_low <= trailing_sl:
                 exit_price = trailing_sl
                 exit_reason = "trailing_stop"
         else:
             trailing_sl = entry - tp_distance * Decimal("0.2")
             if candle_high >= trailing_sl:
                 exit_price = trailing_sl
                 exit_reason = "trailing_stop"
     ```
  2. Добавить `trailing_stop_count` в _compute_summary (аналогично sl_hit_count)
  3. Новая константа: `_TRAILING_STOP_MFE_TRIGGER = Decimal("0.5")`, `_TRAILING_STOP_LOCK_RATIO = Decimal("0.2")`
- **Important**: trailing_sl проверяется КАЖДУЮ свечу, не только один раз. Это значит что если MFE уже достигал 50%, SL навсегда перемещается.
- **Implementation detail**: Нужно добавить поле `trailing_sl_active: bool` в open_trade dict, чтобы отслеживать активацию.
  1. В `_simulate_symbol()` при создании open_trade: `"trailing_sl_active": False`
  2. В `_check_exit()`: если `mfe >= 50% * tp_dist` -> `open_trade["trailing_sl_active"] = True`, `open_trade["stop_loss"] = trailing_sl`
  3. На следующих свечах SL уже будет trailing_sl, и стандартная SL-проверка его отработает
- **Tests**:
  - `test_cal2_06_trailing_stop_activates`: MFE=60% of TP, candle drops below trailing SL -> exit "trailing_stop"
  - `test_cal2_06_trailing_not_active_below_50pct`: MFE=40% -> trailing не срабатывает
  - `test_cal2_06_trailing_stop_short`: аналогично для SHORT direction
- **Acceptance**:
  - [x] Trailing stop активируется при MFE >= 50% от TP distance
  - [x] Exit reason = "trailing_stop" с profit lock
  - [x] trailing_stop_count добавлен в summary

---

## Phase 4: Regimes и Instrument Overrides (REQ-CAL2-005, REQ-CAL2-007, REQ-CAL2-008, REQ-CAL2-009)

### TASK-CAL2-07: Block TREND_BULL regime
- **REQ**: REQ-CAL2-005 (Option A for TREND_BULL)
- **Priority**: Medium
- **File**: `src/config.py` -> `BLOCKED_REGIMES`
- **Current problem**: TREND_BULL: 45 trades, 17.8% WR, -$45.83. Убыточный режим.
- **Changes**:
  1. `BLOCKED_REGIMES = ["RANGING", "TREND_BEAR", "STRONG_TREND_BEAR", "TREND_BULL"]`
  2. Комментарий: "V6-CAL2-07: TREND_BULL added -- 45 trades, 17.8% WR, -$45.83 in v6-cal-r1 backtest"
- **Note**: STRONG_TREND_BULL НЕ блокируется (96 trades, некоторые profitable для GC=F). Для STRONG_TREND_BULL требуется per-instrument анализ (out of scope для этого task).
- **Tests**:
  - `test_cal2_07_trend_bull_blocked`: regime="TREND_BULL" -> filter rejects
- **Acceptance**:
  - [x] TREND_BULL в BLOCKED_REGIMES
  - [ ] Бэктест показывает 0 trades в TREND_BULL

### TASK-CAL2-08: Add AUDUSD override and relax SPY
- **REQ**: REQ-CAL2-009, REQ-CAL2-007
- **Priority**: Low
- **File**: `src/config.py` -> `INSTRUMENT_OVERRIDES`
- **Changes**:
  1. Добавить AUDUSD=X override:
     ```python
     "AUDUSD=X": {
         "min_composite_score": 22,
     },
     ```
     Комментарий: "V6-CAL2-08: 46 trades, 8.7% WR, -$97.87. Ужесточение порога."
  2. Изменить SPY override:
     ```python
     "SPY": {
         "min_composite_score": 22,  # was 30
         "allowed_regimes": ["STRONG_TREND_BULL", "VOLATILE"],  # was only STRONG_TREND_BULL
     },
     ```
     Комментарий: "V6-CAL2-08: relaxed from 30/STRONG_TREND_BULL -- 0 trades was too restrictive"
- **Tests**:
  - `test_cal2_08_audusd_higher_threshold`: AUDUSD=X with composite=10 -> blocked (below 22*0.65=14.3)
  - `test_cal2_08_spy_volatile_allowed`: SPY with regime=VOLATILE -> allowed
- **Acceptance**:
  - [x] AUDUSD=X в INSTRUMENT_OVERRIDES с min_composite_score=22
  - [x] SPY min_composite_score снижен до 22, VOLATILE добавлен в allowed_regimes
  - [ ] SPY производит > 0 trades в бэктесте

### TASK-CAL2-09: Add concentration risk warning
- **REQ**: REQ-CAL2-008
- **Priority**: Low
- **File**: `src/backtesting/backtest_engine.py` -> `_compute_summary()`
- **Changes**:
  1. После вычисления by_symbol, добавить:
     ```python
     # Concentration risk warning
     concentration_warning = None
     if by_symbol and total_pnl > 0:
         symbol_pnls = sorted(
             [(s, d["pnl_usd"]) for s, d in by_symbol.items()],
             key=lambda x: x[1], reverse=True,
         )
         top1_pct = symbol_pnls[0][1] / total_pnl * 100 if total_pnl > 0 else 0
         top2_pct = (
             (symbol_pnls[0][1] + symbol_pnls[1][1]) / total_pnl * 100
             if len(symbol_pnls) >= 2 and total_pnl > 0 else top1_pct
         )
         warnings = []
         if top1_pct > 40:
             warnings.append(f"{symbol_pnls[0][0]} contributes {top1_pct:.1f}% of PnL")
         if top2_pct > 70:
             warnings.append(f"Top 2 instruments contribute {top2_pct:.1f}% of PnL")
         if warnings:
             concentration_warning = "; ".join(warnings)
     ```
  2. Добавить `"concentration_warning": concentration_warning` в result dict
- **Tests**:
  - `test_cal2_09_concentration_warning_single`: 1 symbol with 50% PnL -> warning
  - `test_cal2_09_concentration_warning_top2`: top 2 = 80% -> warning
  - `test_cal2_09_no_concentration_warning`: evenly distributed -> None
- **Acceptance**:
  - [x] concentration_warning появляется когда top 1 > 40% или top 2 > 70%
  - [x] concentration_warning = None когда распределение равномерное

---

## Phase 5: Backtest Validation

### TASK-CAL2-10: Run calibration R2 backtest and document results
- **Priority**: High (после всех предыдущих tasks)
- **Files**: бэктест запуск, `docs/BACKTEST_RESULTS_V6_CAL_R2.md`
- **Steps**:
  1. Запустить полный бэктест с текущими параметрами (все инструменты, 2 года)
  2. Зафиксировать результаты в `docs/BACKTEST_RESULTS_V6_CAL_R2.md`
  3. Сравнить с предыдущим бэктестом:
     - PF (excl eod) vs предыдущий PF
     - time_exit rate (target: <50%)
     - TP hit rate (target: >20%)
     - SHORT trade count (target: >0)
     - Weekend trades (target: 0)
     - Max drawdown (target: <25%)
  4. Если time_exit все еще >50% -> рассмотреть увеличение time_exit candles (H1: 24 -> 36) как fallback
- **Acceptance**:
  - [ ] Бэктест запущен и результаты задокументированы
  - [ ] PF (excl eod) >= 1.3
  - [ ] time_exit rate < 50%
  - [ ] SHORT trades > 0
  - [ ] Weekend trades = 0

---

## Summary

| Task | REQ | Priority | Est. hours | File(s) |
|------|-----|----------|------------|---------|
| CAL2-01 | REQ-CAL2-001 | High | 1.5h | backtest_engine.py |
| CAL2-02 | REQ-CAL2-006 | Medium | 1h | backtest_engine.py |
| CAL2-03 | REQ-CAL2-002 | High | 0.5h | config.py |
| CAL2-04 | REQ-CAL2-004 | Medium | 1h | filter_pipeline.py |
| CAL2-05 | REQ-CAL2-003 | High | 1h | risk_manager_v2.py |
| CAL2-06 | REQ-CAL2-003 | High | 2h | backtest_engine.py |
| CAL2-07 | REQ-CAL2-005 | Medium | 0.5h | config.py |
| CAL2-08 | REQ-CAL2-007/009 | Low | 0.5h | config.py |
| CAL2-09 | REQ-CAL2-008 | Low | 1h | backtest_engine.py |
| CAL2-10 | validation | High | 1h | docs/ |
| **Total** | | | **~10h** | |

## Execution Order
1. **CAL2-01** + **CAL2-02** (honest metrics) -- можно параллельно, оба в _compute_summary
2. **CAL2-03** (SHORT unlock) -- trivial config change
3. **CAL2-04** (weekend block) -- trivial filter change
4. **CAL2-07** (TREND_BULL block) -- trivial config change
5. **CAL2-08** (AUDUSD/SPY overrides) -- trivial config change
6. **CAL2-05** (R:R reduction) -- requires careful test regression
7. **CAL2-06** (trailing stop) -- most complex, depends on R:R changes
8. **CAL2-09** (concentration warning) -- independent, low priority
9. **CAL2-10** (backtest validation) -- last, after all changes

## Dependencies
- CAL2-06 зависит от CAL2-05 (trailing stop имеет смысл с новыми R:R ratios)
- CAL2-10 зависит от всех остальных tasks
- Все tasks совместимы с v4/v5 тестами (backward compatible)

## Risks
1. **R:R reduction + trailing stop**: двойное изменение exit logic. Возможно PF упадет из-за меньших средних выигрышей. Бэктест (CAL2-10) покажет.
2. **SHORT trades**: разблокировка SHORT при RSI < 30 + MACD alignment может дать мало trades. Если SHORT WR < 10%, рассмотреть возврат к LONG-only.
3. **TREND_BULL block**: убирает 45 trades. Если оставшиеся STRONG_TREND_BULL trades тоже ухудшатся, может потребоваться блокировка и этого режима (оставив только VOLATILE).
