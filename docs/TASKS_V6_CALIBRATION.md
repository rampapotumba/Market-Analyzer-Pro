# Tasks v6 Calibration -- Калибровка порогов после бэктеста v6

## Дата: 2026-03-20
## Источник: `analyst-requirements/v6-calibration-requirements.md`
## Анализ: `analyst-reports/v6-backtest-calibration-analysis.md`
## Предыдущий run_id: `62782bc3-0dea-42f1-afed-ee2a295aecd7`

---

## Контекст

Бэктест v6 показал 700 trades за 24 месяца (vs 33 в v5), но WR упал с 45.5% до 14.5%,
Max Drawdown вырос до 51.6%. Эффективный порог 6.75 (= 15 * 0.45) слишком низок.
Bear regime trades потеряли $922, SHORT сигналы -- $1,202 в "sell" bucket.

Цель калибровки: WR >= 25%, PF >= 1.3, DD <= 30%, trades >= 100/24mo.

---

## Приоритет P0 -- Критические изменения (максимальный импакт)

---

### TASK-V6-CAL-01: Floor для available_weight (порог не ниже 9.75)
- [x] Выполнено

**REQ:** REQ-V6-CAL-001

**Проблема:** `effective_threshold = 15 * 0.45 = 6.75` пропускает 85.5% ложных сигналов. WR 14.5%.

**Решение:** Добавить floor для available_weight: `effective_weight = max(available_weight, 0.65)`.
Результат: `effective_threshold = 15 * 0.65 = 9.75` -- на 44% выше текущего.

**Файлы:**
- `src/config.py` -- добавить константу `AVAILABLE_WEIGHT_FLOOR`
- `src/signals/filter_pipeline.py` -- применить floor в `check_score_threshold()`
- `src/backtesting/backtest_engine.py` -- применить floor при передаче `available_weight`

**Точные изменения:**

1. В `src/config.py` после строки `SCORE_COMPONENT_WEIGHTS` (строка ~193) добавить:
   ```python
   # V6-CAL: Floor для available_weight -- предотвращает чрезмерное
   # снижение порога в backtest (только TA). Без floor: 15*0.45=6.75 (слишком низко).
   # С floor 0.65: effective = 15*0.65 = 9.75.
   AVAILABLE_WEIGHT_FLOOR: float = 0.65
   ```

2. В `src/signals/filter_pipeline.py` метод `check_score_threshold()` (строка ~311):
   ```python
   # БЫЛО:
   effective_threshold = threshold * available_weight

   # СТАЛО:
   from src.config import AVAILABLE_WEIGHT_FLOOR
   effective_weight = max(available_weight, AVAILABLE_WEIGHT_FLOOR)
   effective_threshold = threshold * effective_weight
   ```

3. В `src/signals/filter_pipeline.py` метод `check_signal_strength()` (строка ~511):
   ```python
   # БЫЛО:
   strength = _get_signal_strength_scaled(composite, scale=available_weight)

   # СТАЛО:
   from src.config import AVAILABLE_WEIGHT_FLOOR
   effective_weight = max(available_weight, AVAILABLE_WEIGHT_FLOOR)
   strength = _get_signal_strength_scaled(composite, scale=effective_weight)
   ```

**Тесты:**
- `test_v6_cal_01_weight_floor_applied` -- available_weight=0.45 c floor=0.65 дает effective=0.65
- `test_v6_cal_01_threshold_with_floor` -- threshold 15 * max(0.45, 0.65) = 9.75
- `test_v6_cal_01_weight_above_floor_unchanged` -- available_weight=1.0 не меняется
- `test_v6_cal_01_signal_strength_uses_floor` -- signal strength тоже масштабируется с floor

**Критерии приемки:**
- [ ] `effective_threshold` для backtest = 9.75 (при MIN_COMPOSITE_SCORE=15)
- [ ] Live mode (available_weight=1.0) не затронут
- [ ] Instrument overrides тоже масштабируются через floor

---

### TASK-V6-CAL-02: Блокировка bear regimes
- [x] Выполнено

**REQ:** REQ-V6-CAL-002

**Проблема:** 219 trades в TREND_BEAR + STRONG_TREND_BEAR дают -$922 при 11% WR.

**Решение:** Добавить `TREND_BEAR` и `STRONG_TREND_BEAR` в `BLOCKED_REGIMES`.

**Файлы:**
- `src/config.py` -- изменить `BLOCKED_REGIMES`

**Точные изменения:**

1. В `src/config.py` строка ~149:
   ```python
   # БЫЛО:
   BLOCKED_REGIMES: list = ["RANGING"]

   # СТАЛО:
   # V6-CAL: TREND_BEAR и STRONG_TREND_BEAR добавлены --
   # 219 trades при 11% WR, -$922 PnL в v6 backtest.
   BLOCKED_REGIMES: list = ["RANGING", "TREND_BEAR", "STRONG_TREND_BEAR"]
   ```

2. В `src/config.py` обновить `INSTRUMENT_OVERRIDES["BTC/USDT"]` -- убрать `TREND_BEAR`
   из `allowed_regimes` (иначе instrument override перекроет глобальный блок):
   ```python
   "BTC/USDT": {
       "sl_atr_multiplier": 3.5,
       "min_composite_score": 25,   # CAL-05: ужесточение
       "allowed_regimes": ["STRONG_TREND_BULL"],  # CAL-02+CAL-05: только bull
   },
   ```
   ВНИМАНИЕ: Это пересекается с TASK-V6-CAL-05. Если выполняется раньше CAL-05,
   оставить `allowed_regimes` без bear:
   ```python
   "allowed_regimes": ["STRONG_TREND_BULL", "TREND_BULL"],
   ```

**Тесты:**
- `test_v6_cal_02_trend_bear_blocked` -- сигнал с regime=TREND_BEAR отклоняется
- `test_v6_cal_02_strong_trend_bear_blocked` -- STRONG_TREND_BEAR тоже
- `test_v6_cal_02_bull_regimes_pass` -- TREND_BULL и STRONG_TREND_BULL проходят
- `test_v6_cal_02_volatile_passes` -- VOLATILE не заблокирован

**Критерии приемки:**
- [ ] `BLOCKED_REGIMES` содержит 3 элемента: RANGING, TREND_BEAR, STRONG_TREND_BEAR
- [ ] Bear regime PnL = $0 (0 trades)
- [ ] VOLATILE и bull regimes не затронуты

---

## Приоритет P1 -- Высокий импакт

---

### TASK-V6-CAL-03: Сокращение TIME_EXIT до 24 H1 свечей
- [x] Выполнено

**REQ:** REQ-V6-CAL-003

**Проблема:** 431/700 trades (61.6%) выходят по time_exit через 48 свечей H1 (2 дня).
Эти позиции сидят 2 дня и закрываются в минус.

**Решение:** Уменьшить TIME_EXIT_CANDLES["H1"] с 48 до 24 (1 день).

**Файлы:**
- `src/backtesting/backtest_engine.py` -- изменить `_time_exit_candles` dict (строка ~1367)
- `src/tracker/signal_tracker.py` -- если есть аналогичная константа для live

**Точные изменения:**

1. В `src/backtesting/backtest_engine.py` строка ~1367:
   ```python
   # БЫЛО:
   _time_exit_candles: dict[str, int] = {"H1": 48, "H4": 20, "D1": 10}

   # СТАЛО:
   # V6-CAL: H1 сокращен с 48 до 24 -- 61.6% trades выходили по time_exit,
   # сидя 2 дня без прогресса. 1 день достаточен для H1 сигналов.
   _time_exit_candles: dict[str, int] = {"H1": 24, "H4": 20, "D1": 10}
   ```

2. Проверить `src/tracker/signal_tracker.py` -- если есть `TIME_EXIT_CANDLES`, обновить аналогично.

3. Рассмотреть вынос в `src/config.py` для единообразия (опционально):
   ```python
   TIME_EXIT_CANDLES: dict = {"H1": 24, "H4": 20, "D1": 10}
   ```

**Тесты:**
- `test_v6_cal_03_time_exit_24_candles_h1` -- позиция закрывается после 24 свечей H1 (не 48)
- `test_v6_cal_03_time_exit_h4_unchanged` -- H4 по-прежнему 20 свечей
- `test_v6_cal_03_time_exit_d1_unchanged` -- D1 по-прежнему 10 свечей

**Критерии приемки:**
- [ ] time_exit срабатывает после 24 H1 свечей (1 день)
- [ ] time_exit percentage < 50% всех exits (цель < 40%)
- [ ] H4 и D1 time_exit не изменены

---

### TASK-V6-CAL-04: Ужесточение SHORT фильтров
- [x] Выполнено

**REQ:** REQ-V6-CAL-004

**Проблема:** SHORT WR 12.04%, "sell" bucket -$1,202. SHORT_SCORE_MULTIPLIER=1.2 и
SHORT_RSI_THRESHOLD=40 недостаточны.

**Решение:** Увеличить SHORT_SCORE_MULTIPLIER с 1.2 до 2.0 и SHORT_RSI_THRESHOLD с 40 до 30.

**Файлы:**
- `src/config.py` -- изменить `SHORT_SCORE_MULTIPLIER` и `SHORT_RSI_THRESHOLD`

**Точные изменения:**

1. В `src/config.py` строки ~181-182:
   ```python
   # БЫЛО:
   SHORT_SCORE_MULTIPLIER: float = 1.2   # SHORT effective_threshold *= 1.2
   SHORT_RSI_THRESHOLD: int = 40         # SHORT: RSI must be < 40 (not just < 50)

   # СТАЛО:
   # V6-CAL: Ужесточено -- SHORT WR 12.04%, "sell" bucket -$1,202.
   # SHORT требует 2x conviction и RSI < 30 (глубоко перепроданный).
   SHORT_SCORE_MULTIPLIER: float = 2.0   # SHORT effective_threshold *= 2.0
   SHORT_RSI_THRESHOLD: int = 30         # SHORT: RSI must be < 30
   ```

   Эффект: SHORT effective_threshold = 9.75 * 2.0 = 19.5 (при floor 0.65).
   SHORT RSI < 30 допускает только глубоко перепроданные условия.

**Тесты:**
- `test_v6_cal_04_short_multiplier_2x` -- SHORT с composite=-15 блокируется (15 < 19.5)
- `test_v6_cal_04_short_rsi_30` -- SHORT с RSI=35 блокируется (>= 30)
- `test_v6_cal_04_short_passes_strong` -- SHORT с composite=-25, RSI=20 проходит
- `test_v6_cal_04_long_unaffected` -- LONG threshold и RSI не изменились

**Критерии приемки:**
- [ ] SHORT_SCORE_MULTIPLIER = 2.0
- [ ] SHORT_RSI_THRESHOLD = 30
- [ ] SHORT WR >= 20% или количество SHORT trades < 20 (фильтрация работает)
- [ ] LONG пороги не затронуты

---

### TASK-V6-CAL-05: Восстановление ограничений BTC/USDT
- [x] Выполнено

**REQ:** REQ-V6-CAL-005

**Проблема:** BTC/USDT min_score снижен с 20 до 15, regimes расширены. Результат: 102 trades, 9.8% WR, -$135.

**Решение:** Вернуть строгие настройки: min_composite_score=25, только STRONG_TREND_BULL.

**Файлы:**
- `src/config.py` -- изменить `INSTRUMENT_OVERRIDES["BTC/USDT"]`

**Точные изменения:**

1. В `src/config.py` строки ~152-159:
   ```python
   # БЫЛО:
   "BTC/USDT": {
       "sl_atr_multiplier": 3.5,
       "min_composite_score": 15,
       "allowed_regimes": [
           "STRONG_TREND_BULL", "STRONG_TREND_BEAR",
           "TREND_BULL", "TREND_BEAR",
       ],
   },

   # СТАЛО:
   # V6-CAL: Ужесточение -- 102 trades, 9.8% WR, -$135 при relaxed settings.
   # min_score 25 (строже v5), только STRONG_TREND_BULL (bear заблокированы в CAL-02).
   "BTC/USDT": {
       "sl_atr_multiplier": 3.5,
       "min_composite_score": 25,
       "allowed_regimes": ["STRONG_TREND_BULL"],
   },
   ```

2. Также восстановить ETH/USDT min_composite_score:
   ```python
   # БЫЛО:
   "ETH/USDT": {
       "sl_atr_multiplier": 3.5,
       "min_composite_score": 15,
   },

   # СТАЛО:
   # V6-CAL: ETH/USDT -- восстановить min_score 20 (как в v5).
   "ETH/USDT": {
       "sl_atr_multiplier": 3.5,
       "min_composite_score": 20,
   },
   ```

**Тесты:**
- `test_v6_cal_05_btc_min_score_25` -- BTC требует |composite| >= 25 * 0.65 = 16.25
- `test_v6_cal_05_btc_only_strong_bull` -- BTC в TREND_BULL заблокирован
- `test_v6_cal_05_eth_min_score_20` -- ETH требует |composite| >= 20 * 0.65 = 13.0

**Критерии приемки:**
- [ ] BTC/USDT min_composite_score = 25
- [ ] BTC/USDT allowed_regimes = ["STRONG_TREND_BULL"] только
- [ ] ETH/USDT min_composite_score = 20
- [ ] BTC/USDT WR >= 20% или trades < 10 (жесткая фильтрация ожидаема)

---

## Приоритет P2 -- Средний импакт (уточнения)

---

### TASK-V6-CAL-06: Per-instrument overrides для убыточных символов
- [x] Выполнено

**REQ:** REQ-V6-CAL-008

**Проблема:** 6/10 инструментов убыточны. Глобальный порог -- грубый инструмент.

**Решение:** Добавить overrides для USDJPY, NZDUSD, SPY.

**Файлы:**
- `src/config.py` -- расширить `INSTRUMENT_OVERRIDES`

**Точные изменения:**

1. В `src/config.py` добавить/обновить overrides:
   ```python
   INSTRUMENT_OVERRIDES: dict = {
       "BTC/USDT": {
           "sl_atr_multiplier": 3.5,
           "min_composite_score": 25,
           "allowed_regimes": ["STRONG_TREND_BULL"],
       },
       "ETH/USDT": {
           "sl_atr_multiplier": 3.5,
           "min_composite_score": 20,
       },
       "GBPUSD=X": {
           "min_composite_score": 20,  # V6-CAL: восстановить -- -$43 при global threshold
       },
       "USDCHF=X": {
           "min_composite_score": 18,
       },
       # V6-CAL-06: Новые overrides для убыточных инструментов
       "USDJPY=X": {
           "min_composite_score": 22,  # -$75 при global, 12.9% WR
       },
       "NZDUSD=X": {
           "min_composite_score": 22,  # -$140, 16.7% WR, worst avg loss
       },
       "SPY": {
           "min_composite_score": 30,  # -$8 при 25, ужесточить или исключить
           "allowed_regimes": ["STRONG_TREND_BULL"],
       },
   }
   ```

**Тесты:**
- `test_v6_cal_06_usdjpy_override` -- USDJPY использует min_composite_score=22
- `test_v6_cal_06_nzdusd_override` -- NZDUSD использует min_composite_score=22
- `test_v6_cal_06_spy_strict_override` -- SPY использует 30 и только STRONG_TREND_BULL

**Критерии приемки:**
- [ ] Все 7 инструментов имеют overrides
- [ ] Ни один инструмент не теряет > $50 за 24 месяца
- [ ] >= 6/10 инструментов прибыльны

---

### TASK-V6-CAL-07: Исправление метрики avg_mae_pct_of_sl
- [x] Выполнено

**REQ:** REQ-V6-CAL-007

**Проблема:** `avg_mae_pct_of_sl = 285.6%` -- аномально высокое значение. Причина: метрика
использует raw MAE (в ценовых единицах), но называется "pct_of_sl" и не делит на SL distance.

**Решение:** Исправить расчет: `mae_pct = (mae / sl_distance) * 100`. Добавить разбивку
по winners/losers.

**Файлы:**
- `src/backtesting/backtest_engine.py` -- исправить `avg_mae_pct_of_sl` расчет (строка ~433-435)

**Точные изменения:**

1. В `backtest_engine.py` строки ~433-435:
   ```python
   # БЫЛО:
   mae_values = [float(t.mae or 0) for t in trades if t.mae is not None and t.mae > 0]
   avg_mae_pct_of_sl = sum(mae_values) / len(mae_values) if mae_values else 0.0

   # СТАЛО:
   # V6-CAL: MAE как процент от SL distance (а не raw price units).
   # mae / sl_distance * 100 дает реальный % -- сколько от SL пройдено против позиции.
   mae_pct_values = []
   mae_pct_values_winners = []
   mae_pct_values_losers = []
   for t in trades:
       if t.mae is None or t.mae <= 0:
           continue
       sl_dist = abs(float(t.entry_price) - float(t.sl_price)) if t.sl_price else None
       if sl_dist is None or sl_dist == 0:
           continue
       pct = float(t.mae) / sl_dist * 100
       mae_pct_values.append(pct)
       if t.result == "win":
           mae_pct_values_winners.append(pct)
       else:
           mae_pct_values_losers.append(pct)

   avg_mae_pct_of_sl = sum(mae_pct_values) / len(mae_pct_values) if mae_pct_values else 0.0
   avg_mae_pct_of_sl_winners = (
       sum(mae_pct_values_winners) / len(mae_pct_values_winners)
       if mae_pct_values_winners else 0.0
   )
   avg_mae_pct_of_sl_losers = (
       sum(mae_pct_values_losers) / len(mae_pct_values_losers)
       if mae_pct_values_losers else 0.0
   )
   ```

2. В summary dict (строка ~481) добавить новые поля:
   ```python
   "avg_mae_pct_of_sl": round(avg_mae_pct_of_sl, 2),
   "avg_mae_pct_of_sl_winners": round(avg_mae_pct_of_sl_winners, 2),  # V6-CAL
   "avg_mae_pct_of_sl_losers": round(avg_mae_pct_of_sl_losers, 2),    # V6-CAL
   ```

3. Необходимо убедиться что `BacktestTradeResult` содержит `sl_price` (или `stop_loss`).
   Если нет -- добавить передачу SL в trade result. Проверить структуру `BacktestTradeResult`.

**Тесты:**
- `test_v6_cal_07_mae_pct_correct` -- MAE=0.0050, SL_distance=0.0100 -> mae_pct=50.0%
- `test_v6_cal_07_mae_pct_winners_vs_losers` -- разбивка по winners и losers
- `test_v6_cal_07_mae_zero_sl_skipped` -- SL distance = 0 не вызывает деление на ноль

**Критерии приемки:**
- [ ] `avg_mae_pct_of_sl` показывает реальные проценты (ожидаемо 30-80%, не 285%)
- [ ] Summary содержит `avg_mae_pct_of_sl_winners` и `avg_mae_pct_of_sl_losers`
- [ ] Нет деления на ноль при отсутствии SL

---

### TASK-V6-CAL-08: Scaled score buckets в summary
- [x] Выполнено

**REQ:** REQ-V6-CAL-006

**Проблема:** `_score_bucket()` использует unscaled пороги (7/10/15), а filter pipeline --
scaled (3.15/4.5/6.75 при scale=0.45). Composite=8 проходит как STRONG_BUY, но отображается
как "weak_buy" в отчете.

**Решение:** Добавить `by_score_bucket_scaled` с масштабированными порогами.

**Файлы:**
- `src/backtesting/backtest_engine.py` -- добавить `_score_bucket_scaled()` и
  `by_score_bucket_scaled` в summary

**Точные изменения:**

1. После `_score_bucket()` (строка ~358) добавить:
   ```python
   def _score_bucket_scaled(score: Optional[Decimal], scale: float) -> str:
       """Score bucket с масштабированными порогами (V6-CAL).

       При scale=0.65 (с floor): strong >= 9.75, buy >= 6.5, weak >= 4.55
       """
       if score is None:
           return "unknown"
       s = float(score)
       strong = 15.0 * scale
       buy = 10.0 * scale
       weak = 7.0 * scale
       if s >= strong:
           return "strong_buy"
       if s >= buy:
           return "buy"
       if s >= weak:
           return "weak_buy"
       if s <= -strong:
           return "strong_sell"
       if s <= -buy:
           return "sell"
       if s <= -weak:
           return "weak_sell"
       return "neutral"
   ```

2. После `by_score` dict (строка ~368) добавить:
   ```python
   # V6-CAL: Scaled score buckets for accurate v6+ reporting
   from src.config import AVAILABLE_WEIGHT_FLOOR
   _bucket_scale = max(_TA_WEIGHT, AVAILABLE_WEIGHT_FLOOR)
   by_score_scaled: dict[str, dict] = {}
   for t in trades:
       bucket = _score_bucket_scaled(t.composite_score, _bucket_scale)
       if bucket not in by_score_scaled:
           by_score_scaled[bucket] = {"trades": 0, "wins": 0, "pnl_usd": 0.0}
       by_score_scaled[bucket]["trades"] += 1
       if t.result == "win":
           by_score_scaled[bucket]["wins"] += 1
       by_score_scaled[bucket]["pnl_usd"] += float(t.pnl_usd or 0)
   ```

3. В summary dict добавить:
   ```python
   "by_score_bucket_scaled": by_score_scaled,
   ```

**Тесты:**
- `test_v6_cal_08_scaled_bucket_strong_buy` -- composite=10 при scale=0.65 -> "strong_buy" (10 >= 9.75)
- `test_v6_cal_08_scaled_bucket_buy` -- composite=7 при scale=0.65 -> "buy" (7 >= 6.5)
- `test_v6_cal_08_both_buckets_in_summary` -- summary содержит оба: by_score_bucket и by_score_bucket_scaled

**Критерии приемки:**
- [ ] Summary содержит `by_score_bucket_scaled`
- [ ] Scaled buckets НЕ содержат "neutral" (фильтрованы pipeline)
- [ ] `by_score_bucket` (unscaled) сохранен для backward compatibility

---

## Приоритет P3 -- Низкий импакт (уточнения)

---

### TASK-V6-CAL-09: Расширение weekday фильтра (Monday/Tuesday)
- [x] Выполнено

**REQ:** REQ-V6-CAL-009

**Проблема:** Monday (-$373) и Tuesday (-$365) -- худшие дни. Текущий фильтр блокирует
только Mon < 10:00 UTC и Fri >= 18:00 UTC.

**Решение:** Увеличить score threshold на 1.5x для Monday и Tuesday (forex).

**Файлы:**
- `src/config.py` -- добавить `WEEKDAY_SCORE_MULTIPLIER`
- `src/signals/filter_pipeline.py` -- применить в `check_score_threshold()` или добавить
  новый check в `check_weekday()`

**Точные изменения:**

1. В `src/config.py` добавить:
   ```python
   # V6-CAL: Monday и Tuesday score penalty для forex.
   # Mon: -$373, Tue: -$365 в v6 backtest. Требуем 1.5x conviction.
   WEAK_WEEKDAY_SCORE_MULTIPLIER: float = 1.5
   WEAK_WEEKDAYS: list = [0, 1]  # 0=Monday, 1=Tuesday
   ```

2. В `src/signals/filter_pipeline.py` метод `check_score_threshold()`,
   после вычисления `effective_threshold` (перед SHORT multiplier):
   ```python
   # V6-CAL: Monday/Tuesday penalty
   from src.config import WEAK_WEEKDAY_SCORE_MULTIPLIER, WEAK_WEEKDAYS
   candle_ts = context.get("candle_ts") if hasattr(self, '_current_context') else None
   # Примечание: candle_ts не передается в check_score_threshold напрямую.
   # Альтернатива: добавить weekday_multiplier в run_all() перед score check.
   ```

   Лучший подход -- применить множитель в `run_all()` перед вызовом `check_score_threshold()`:
   ```python
   # В run_all(), перед score filter:
   weekday_multiplier = 1.0
   if candle_ts is not None and market_type == "forex":
       from src.config import WEAK_WEEKDAY_SCORE_MULTIPLIER, WEAK_WEEKDAYS
       if candle_ts.weekday() in WEAK_WEEKDAYS:
           weekday_multiplier = WEAK_WEEKDAY_SCORE_MULTIPLIER

   # Передать в check_score_threshold:
   passed, reason = self.check_score_threshold(
       composite, market_type, symbol,
       available_weight=available_weight,
       direction=direction,
       weekday_multiplier=weekday_multiplier,
   )
   ```

   В `check_score_threshold()` добавить параметр `weekday_multiplier: float = 1.0`:
   ```python
   effective_threshold *= weekday_multiplier
   ```

**Тесты:**
- `test_v6_cal_09_monday_forex_higher_threshold` -- Monday forex threshold *= 1.5
- `test_v6_cal_09_tuesday_forex_higher_threshold` -- Tuesday тоже
- `test_v6_cal_09_wednesday_unaffected` -- Wednesday без множителя
- `test_v6_cal_09_monday_crypto_unaffected` -- crypto не затронуто

**Критерии приемки:**
- [ ] Monday + Tuesday effective threshold для forex = base * 1.5
- [ ] crypto и другие рынки не затронуты
- [ ] Monday + Tuesday combined PnL >= -$100

---

## Приоритет P4 -- Валидация

---

### TASK-V6-CAL-10: Сравнительный бэктест-матрица
- [ ] Выполнено

**REQ:** REQ-V6-CAL-010

**Проблема:** Неизвестно какая комбинация threshold/regime/SHORT дает оптимальный результат.

**Решение:** Запустить матрицу из 5 бэктестов и задокументировать результаты.

**Файлы:**
- `scripts/run_calibration_backtests.sh` -- СОЗДАТЬ
- `docs/BACKTEST_RESULTS_V6_CALIBRATION.md` -- СОЗДАТЬ

**Матрица тестов:**

| Test | Floor | Bear Regime | SHORT mult | Time Exit H1 | Описание |
|------|-------|-------------|------------|--------------|----------|
| A | 0.65 | Blocked | 2.0 | 24 | Все калибровки (CAL-01..09) |
| B | 0.65 | Blocked | disabled* | 24 | LONG-only |
| C | 0.65 | Blocked | 2.0 | 48 | Без изменения time exit |
| D | 0.73 | Blocked | 2.0 | 24 | Более высокий floor (11.0) |
| E | 0.65 | Directional** | 2.0 | 24 | Bear = SHORT only |

*disabled = SHORT_SCORE_MULTIPLIER=100 (фактически блокирует все SHORT)
**Directional = отдельная логика, не просто config change

**Шаги реализации:**

1. Создать `scripts/run_calibration_backtests.sh`:
   ```bash
   #!/bin/bash
   # Запуск 5 вариантов калибровки
   # Каждый меняет env vars перед запуском backtest
   # Результаты сохраняются с разными run tags
   ```

2. Для каждого теста:
   - Изменить config (через env vars или параметры BacktestParams)
   - Запустить бэктест
   - Сгенерировать отчет через `scripts/generate_backtest_report.py`

3. Создать `docs/BACKTEST_RESULTS_V6_CALIBRATION.md`:
   ```markdown
   # Calibration Backtest Results

   | Test | Trades | WR% | PF | Max DD% | PnL USD |
   |------|--------|-----|-----|---------|---------|
   | A    |        |     |     |         |         |
   | ...  |        |     |     |         |         |

   ## Winner: Test X
   ## Rationale: ...
   ```

**Критерии приемки:**
- [ ] >= 5 вариантов запущены и задокументированы
- [ ] Хотя бы 1 вариант достигает: WR >= 25%, PF >= 1.3, DD <= 30%, trades >= 100
- [ ] Результаты в `docs/BACKTEST_RESULTS_V6_CALIBRATION.md`
- [ ] Победитель выбран с обоснованием

---

### TASK-V6-CAL-11: Финальный бэктест и перезапуск сервера
- [ ] Выполнено

**REQ:** Финальная валидация

**Проблема:** После всех калибровок необходимо запустить финальный бэктест и обновить
продакшен конфигурацию.

**Файлы:**
- `src/config.py` -- финальные значения
- `docs/BACKTEST_RESULTS_V6_CALIBRATION.md` -- обновить финальные результаты

**Шаги реализации:**

1. Убедиться что все TASK-V6-CAL-01..09 выполнены и отмечены [x]
2. Запустить финальный бэктест с winning конфигурацией из CAL-10:
   ```bash
   python -m src.backtesting.backtest_engine \
     --symbols "EURUSD=X,GBPUSD=X,AUDUSD=X,USDJPY=X,NZDUSD=X,USDCAD=X,BTC/USDT,ETH/USDT,SPY,GC=F" \
     --start 2024-01-01 \
     --end 2025-12-31 \
     --timeframe H1
   ```
3. Сгенерировать финальный отчет:
   ```bash
   python scripts/generate_backtest_report.py --run-id <new_run_id> \
     --output docs/BACKTEST_RESULTS_V6_CALIBRATION_FINAL.md
   ```
4. Перезапустить сервер:
   ```bash
   sudo systemctl restart market-analyzer
   ```
5. Обновить `claude-progress.md` с финальными результатами

**Критерии приемки:**
- [ ] Финальный бэктест: WR >= 25%, PF >= 1.3, DD <= 30%, trades >= 100/24mo
- [ ] Ни один инструмент не теряет > $50
- [ ] >= 6/10 инструментов прибыльны
- [ ] Сервер перезапущен без ошибок
- [ ] `claude-progress.md` обновлен

---

## Порядок выполнения

### Phase 1: Critical Config (CAL-01 + CAL-02)
1. **TASK-V6-CAL-01** -- floor для available_weight (9.75 threshold)
2. **TASK-V6-CAL-02** -- блокировка bear regimes

   --> Быстрый бэктест для валидации направления

### Phase 2: High Impact (CAL-03 + CAL-04 + CAL-05)
3. **TASK-V6-CAL-03** -- time exit 24 свечей
4. **TASK-V6-CAL-04** -- SHORT multiplier 2.0
5. **TASK-V6-CAL-05** -- BTC/USDT ужесточение

   --> Бэктест для валидации

### Phase 3: Per-Instrument + Metrics (CAL-06 + CAL-07 + CAL-08)
6. **TASK-V6-CAL-06** -- per-instrument overrides
7. **TASK-V6-CAL-07** -- исправление MAE метрики
8. **TASK-V6-CAL-08** -- scaled score buckets

   --> Бэктест для валидации

### Phase 4: Refinement + Validation (CAL-09 + CAL-10 + CAL-11)
9. **TASK-V6-CAL-09** -- weekday multiplier
10. **TASK-V6-CAL-10** -- сравнительная матрица
11. **TASK-V6-CAL-11** -- финальный бэктест + restart

---

## Сводная таблица изменений config.py

| Параметр | Текущее v6 | После калибровки | Задача |
|----------|-----------|------------------|--------|
| AVAILABLE_WEIGHT_FLOOR | (нет) | 0.65 | CAL-01 |
| BLOCKED_REGIMES | ["RANGING"] | ["RANGING", "TREND_BEAR", "STRONG_TREND_BEAR"] | CAL-02 |
| TIME_EXIT H1 | 48 | 24 | CAL-03 |
| SHORT_SCORE_MULTIPLIER | 1.2 | 2.0 | CAL-04 |
| SHORT_RSI_THRESHOLD | 40 | 30 | CAL-04 |
| BTC/USDT min_score | 15 | 25 | CAL-05 |
| BTC/USDT allowed_regimes | [4 regimes] | ["STRONG_TREND_BULL"] | CAL-05 |
| ETH/USDT min_score | 15 | 20 | CAL-05 |
| GBPUSD=X min_score | (empty) | 20 | CAL-06 |
| USDJPY=X min_score | (нет) | 22 | CAL-06 |
| NZDUSD=X min_score | (нет) | 22 | CAL-06 |
| SPY min_score | 25 | 30 | CAL-06 |
| WEAK_WEEKDAY_SCORE_MULTIPLIER | (нет) | 1.5 | CAL-09 |
| WEAK_WEEKDAYS | (нет) | [0, 1] | CAL-09 |

## Целевые метрики после калибровки

| Метрика | v6 Actual | Target | Критично |
|---------|-----------|--------|----------|
| Trades/24mo | 700 | >= 100 | Да |
| Win Rate | 14.47% | >= 25% | Да |
| Profit Factor | 1.12 | >= 1.3 | Да |
| Max Drawdown | 51.59% | <= 30% | Да |
| Bear regime PnL | -$922 | >= $0 | Да |
| SHORT WR | 12.04% | >= 20% или disabled | Нет |
| time_exit % | 61.6% | < 40% | Нет |
| Profitable instruments | 4/10 | >= 6/10 | Да |
