# Спецификация: Trade Simulator v4 — Диагностика, Калибровка и Качество сигналов

**Версия:** 1.0
**Дата:** 2026-03-19
**Статус:** DRAFT — к реализации
**Предыдущая версия:** `docs/SPEC_SIMULATOR_V3.md` (реализована, SIM-09..SIM-16 закрыты)
**Основание:** Анализ торговой системы от 2026-03-19 (6 закрытых сделок, 7 открытых)

---

## 1. Контекст и мотивация

После реализации v3 симулятор корректно считает P&L с учётом слипиджа, свопа, динамического
баланса и candle-based SL/TP. Первый реальный анализ закрытых сделок выявил критические
системные проблемы, которые делают текущие торговые результаты ненадёжными:

### Критические находки первого анализа

| # | Проблема | Критичность | Влияние |
|---|----------|-------------|---------|
| 1 | 17 сигналов — 17 SHORT, 0 LONG | Критическая | Система торгует только в одном направлении |
| 2 | Фиксированный R:R = 1.33 для всех инструментов и TF | Высокая | Неоптимальное соотношение риск/доходность |
| 3 | SL на 1–1.5×ATR: выбивается нормальным шумом | Высокая | Win rate 33% < breakeven 43% |
| 4 | 3 одновременные SHORT по AUDUSD (H1 + H4) | Высокая | Корреляционная концентрация риска |
| 5 | Partial close ни разу не сработал | Средняя | Нет данных о работоспособности SIM-07 |
| 6 | MAE проигрышных сделок = 200–330% от SL | Средняя | Позиции открываются против тренда |
| 7 | Выборка 6 закрытых сделок | Критическая | Невозможно делать статистически значимые выводы |
| 8 | Profit Factor = 0.79 | Средняя | Отрицательное ожидание, но выборка слишком мала |

### Цель v4

Перевести систему из состояния "работает технически" в состояние "можно оценивать
качество сигналов". Для этого нужно: (1) устранить системные баги в генерации сигналов,
(2) улучшить управление рисками, (3) накопить достаточную выборку через бэктест,
(4) добавить аналитические инструменты для диагностики.

---

## 2. Перечень изменений

| # | Приоритет | Компонент | Описание |
|---|-----------|-----------|----------|
| SIM-17 | P0 | `signal_engine.py` | Диагностика и фикс bias на SHORT: анализ scoring-компонентов |
| SIM-18 | P0 | `risk_manager.py` | Динамический R:R: адаптация к режиму рынка (тренд vs консолидация) |
| SIM-19 | P0 | `risk_manager.py` | SL на 2×ATR вместо 1.5×ATR: уменьшить преждевременные выбивания |
| SIM-20 | P1 | `signal_tracker.py` | MAE Early Exit: закрывать позиции с MAE > порога на ранних свечах |
| SIM-21 | P1 | `signal_engine.py` | Корреляционный guard по открытым позициям: блокировать не только тот же инструмент, но и высококоррелированные |
| SIM-22 | P0 | `backtesting/` | Бэктест-движок: прогон по историческим данным для накопления 100+ сделок |
| SIM-23 | P1 | `api/routes_v2.py` + frontend | Диагностический дашборд: score-компоненты, MAE/MFE distributions, signal bias |
| SIM-24 | P2 | `signal_tracker.py` | Проверка и фикс partial close: убедиться что SIM-07 отрабатывает корректно |

---

## 3. Детальные требования

---

### SIM-17: Диагностика и фикс SHORT bias

**Проблема:**

За всю историю система сгенерировала 17 сигналов — все SHORT. Это аномалия:
даже на устойчивом медвежьем рынке ни один инструмент из 3 классов активов
(forex, crypto, stocks) не должен давать 100% SHORT сигналы.

**Диагностика (выполнить первым делом):**

1. Запустить `GET /api/v2/signals/analyze/{symbol}?timeframe=H1` для нескольких инструментов.
   Записать breakdown по компонентам: ta_score, fa_score, sentiment_score, geo_score.
2. Проверить дефолтные значения каждого компонента при отсутствии данных:
   - Если `fa_score` при ошибке возвращает `-10` вместо `0` → это источник bias
   - Если `sentiment_score` при отсутствии новостей возвращает `-5` → аналогично
   - Если `geo_score` при отсутствии данных возвращает отрицательное → аналогично
3. Проверить веса `SIGNAL_WEIGHTS` в конфиге: если сумма не равна 1.0 — нормализовать.

**Требования к фиксу:**

```python
# Правило: при отсутствии данных компонент должен возвращать НЕЙТРАЛЬНОЕ значение
# Не: return -10.0  (это вносит системный медвежий bias)
# Да: return 0.0   (нейтрально, не влияет на направление)
```

- Для каждого скорингового компонента (`fa_engine.py`, `sentiment_engine.py`, `geo_engine.py`,
  `order_flow.py`): проверить все ветки `except` и fallback-возвраты.
- Все fallback при ошибке данных → `0.0`, не отрицательные.
- Исключение: если данные есть и реально негативные — возвращать реальное значение.

**Новый эндпоинт диагностики:**

```
GET /api/v2/diagnostics/scoring-breakdown
Response: {
  "instruments": [
    {
      "symbol": "EURUSD=X",
      "timeframe": "H1",
      "composite_score": -18.5,
      "components": {
        "ta_score": -12.0, "ta_weight": 0.4,
        "fa_score": -5.0,  "fa_weight": 0.2,
        "sentiment_score": -3.0, "sentiment_weight": 0.2,
        "geo_score": -2.0, "geo_weight": 0.1,
        "of_score": null,  "of_weight": 0.1
      },
      "bias_flags": [
        "fa_score returned default negative (no data)",
        "sentiment_score returned default negative (no data)"
      ]
    }
  ],
  "summary": {
    "avg_composite": -15.3,
    "pct_negative": 100.0,
    "suspected_bias_sources": ["fa_score", "sentiment_score"]
  }
}
```

**Тесты:**
- `test_sim17_neutral_fallback_fa` — FA engine при отсутствии данных возвращает `0.0`
- `test_sim17_neutral_fallback_sentiment` — Sentiment engine при ошибке возвращает `0.0`
- `test_sim17_scoring_breakdown_endpoint` — эндпоинт возвращает корректную структуру
- `test_sim17_long_signal_possible` — при нейтральных fa/sentiment и бычьем TA → генерируется LONG

---

### SIM-18: Динамический R:R на основе режима рынка

**Проблема:**

Все сделки имеют R:R = 1.33 (константа). Это компромисс, который не оптимален ни при тренде,
ни при консолидации. При тренде упускается потенциал (нужен R:R ≥ 2.0); при консолидации
R:R = 1.33 завышен и TP часто недостижим.

**Текущее состояние** (`risk_manager_v2.py`):

```python
levels = self._rm.calculate_levels_for_regime(
    entry, atr, direction, regime, support_levels, resistance_levels
)
# Внутри используется фиксированный множитель ATR для TP
```

**Требование — таблица R:R по режиму:**

```python
REGIME_RR_MAP: dict[str, dict] = {
    "STRONG_TREND_BULL": {"min_rr": 2.0, "target_rr": 2.5},
    "STRONG_TREND_BEAR": {"min_rr": 2.0, "target_rr": 2.5},
    "TREND_BULL":        {"min_rr": 1.5, "target_rr": 2.0},
    "TREND_BEAR":        {"min_rr": 1.5, "target_rr": 2.0},
    "RANGING":           {"min_rr": 1.0, "target_rr": 1.3},
    "VOLATILE":          {"min_rr": 1.5, "target_rr": 2.0},  # компенсация волатильности
    "DEFAULT":           {"min_rr": 1.3, "target_rr": 1.5},
}
```

**Логика расчёта TP при динамическом R:R:**

```
sl_distance = |entry - stop_loss|
tp_distance = sl_distance × target_rr
tp1 = entry + tp_distance  (LONG)
tp1 = entry - tp_distance  (SHORT)

Если рядом есть уровень поддержки/сопротивления в диапазоне [tp1 × 0.8, tp1 × 1.2]:
  → скорректировать tp1 к уровню (не выходить за ключевой уровень)
```

**Тесты:**
- `test_sim18_rr_strong_trend` — STRONG_TREND_BULL → TP на 2.5×SL расстоянии
- `test_sim18_rr_ranging` — RANGING → TP на 1.3×SL расстоянии
- `test_sim18_rr_level_snap` — TP корректируется к ближайшему resistance уровню

---

### SIM-19: SL на 2×ATR

**Проблема:**

Анализ MAE показал: все 4 проигрышные сделки имели MAE в 110–330% от расстояния до SL.
Цена уходила далеко за SL — это признак не просто невезения, а слишком узких стопов,
которые выбиваются нормальным рыночным шумом.

Текущий SL: `sl = entry - atr × 1.5` (примерно, точный множитель в `risk_manager_v2.py`).

**Требование:**

```python
ATR_SL_MULTIPLIER_MAP: dict[str, float] = {
    "STRONG_TREND_BULL": 1.5,  # тренд чёткий — можно ставить ближе
    "STRONG_TREND_BEAR": 1.5,
    "TREND_BULL":        2.0,  # стандарт
    "TREND_BEAR":        2.0,
    "RANGING":           1.5,  # консолидация — SL за границей диапазона, не нужен широкий
    "VOLATILE":          2.5,  # высокая волатильность — нужен запас
    "DEFAULT":           2.0,
}
```

**Важно:** при увеличении SL автоматически уменьшается `position_size_pct`, т.к. формула:
```
position_size = risk_amount / sl_distance
```
т.е. более широкий SL → меньше позиция → P&L в $ не изменится, но вероятность дожить до TP вырастет.

**Тесты:**
- `test_sim19_sl_wider_volatile` — VOLATILE режим: SL = 2.5×ATR
- `test_sim19_position_size_decreases_with_wider_sl` — wider SL → smaller position_pct
- `test_sim19_rr_preserved` — R:R остаётся корректным после изменения SL

---

### SIM-20: MAE Early Exit

**Проблема:**

Все 4 проигрышные сделки показали MAE >> MFE с первых свечей. Профессиональная практика:
если позиция сразу идёт против тебя и MAE уже достигает значительной части SL расстояния —
это признак "неправильного" входа, нет смысла ждать полного SL.

**Требование:**

Добавить опциональный механизм ранней остановки убытков на основе MAE:

```python
MAE_EARLY_EXIT_CONFIG = {
    "enabled": True,
    "threshold_pct_of_sl": 0.60,  # если MAE >= 60% от SL расстояния...
    "min_candles": 3,              # ...И прошло минимум 3 свечи...
    "mfe_max_ratio": 0.20,         # ...И MFE < 20% от MAE (нет положительного движения)
    # → закрыть позицию с exit_reason = "mae_early_exit"
}
```

**Формула проверки (в тике симулятора):**

```python
sl_distance = abs(position.entry_price - current_sl)
mae_ratio = abs(mae) / sl_distance  # mae хранится в price units

if (mae_ratio >= threshold
    and candles_elapsed >= min_candles
    and (mfe == 0 or abs(mae) / abs(mfe) >= 1/mfe_max_ratio)):
    → exit at current_price, exit_reason = "mae_early_exit"
```

**Логика:** это не стоп — это интеллектуальный выход. Если рынок явно против нас
(MAE нарастает, MFE минимален), ранний выход уменьшает убыток относительно полного SL.

**Новый `exit_reason`:** `"mae_early_exit"` — отдельный от `"sl_hit"`, чтобы можно было
анализировать эффективность отдельно.

**Тесты:**
- `test_sim20_mae_early_exit_triggers` — MAE 65% SL, 4 свечи, MFE=0 → early exit
- `test_sim20_mae_early_exit_no_trigger_early_candles` — MAE 65% SL, только 2 свечи → не срабатывает
- `test_sim20_mae_early_exit_no_trigger_with_mfe` — MAE 65% SL, но MFE=40% MAE → не срабатывает (есть надежда)
- `test_sim20_mae_exit_reason_stored` — exit_reason сохраняется корректно

---

### SIM-21: Корреляционный guard по открытым позициям

**Проблема:**

Текущий guard (`has_open_position_for_instrument`) блокирует только точно тот же инструмент.
Но 3 одновременных позиции по AUDUSD (2×H1 + 1×H4) накопились из-за разных таймфреймов.

Более глубокая проблема: AUDUSD H1 и AUDUSD H4 — это одна и та же ставка на курс доллара.
Открывать обе — удваивать риск без диверсификации.

**Требование:**

Расширить `has_open_position_for_instrument` → `is_position_blocked_by_correlation`:

```python
# Таблица коррелированных групп (статическая конфигурация)
CORRELATED_GROUPS: list[set[str]] = [
    # Форекс: доллар США
    {"EURUSD=X", "GBPUSD=X", "AUDUSD=X", "NZDUSD=X"},   # USD long side (все растут с ослаблением USD)
    {"USDJPY=X", "USDCAD=X", "USDCHF=X"},                 # USD short side
    # Крипто: Bitcoin-коррелированные
    {"BTC/USDT", "ETH/USDT"},                             # тесная корреляция
    # Акции: индексы
    {"SPY", "QQQ", "IWM"},                                 # US equity
]

SAME_DIRECTION_BLOCK = True  # блокировать только если направление СОВПАДАЕТ
CROSS_GROUP_MAX = 1          # максимум 1 открытая позиция из одной группы в одном направлении
```

**Логика:**

```python
async def is_position_blocked_by_correlation(
    db, instrument_id, symbol, direction, timeframe
) -> tuple[bool, str]:
    # 1. Прямой guard: тот же инструмент — любой TF
    if await has_open_position_for_instrument(db, instrument_id):
        return True, f"open position exists for {symbol}"

    # 2. Корреляционный guard: та же группа + то же направление
    group = get_correlation_group(symbol)
    if group:
        open_in_group = await count_open_positions_in_group(db, group, direction)
        if open_in_group >= CROSS_GROUP_MAX:
            return True, f"correlation group limit reached ({open_in_group} positions in group)"

    return False, "OK"
```

**Интеграция:** заменить вызов `has_open_position_for_instrument` в `signal_engine.py`
на новый `is_position_blocked_by_correlation`.

**Тесты:**
- `test_sim21_same_instrument_blocked` — тот же инструмент → blocked
- `test_sim21_correlated_same_direction_blocked` — EURUSD SHORT открыт, GBPUSD SHORT → blocked
- `test_sim21_correlated_opposite_direction_allowed` — EURUSD SHORT открыт, GBPUSD LONG → allowed (хедж)
- `test_sim21_different_group_allowed` — EURUSD SHORT открыт, BTC SHORT → allowed

---

### SIM-22: Бэктест-движок для накопления статистики

**Проблема:**

6 закрытых сделок — статистически бессмысленная выборка. Профессиональные трейдеры
оценивают систему минимум на 50–100 сделках, оптимально 300+.
Ждать накопления в реальном времени при текущей частоте сигналов заняло бы месяцы.

**Требование — бэктест-движок:**

```
POST /api/v2/backtest/run
Body: {
  "symbols": ["EURUSD=X", "GBPUSD=X", "AUDUSD=X", "BTC/USDT", "ETH/USDT", "SPY"],
  "timeframe": "H1",
  "start_date": "2024-01-01",
  "end_date":   "2025-12-31",
  "account_size": 1000.0,
  "apply_slippage": true,
  "apply_swap":     true
}

Response: {
  "run_id": "uuid",
  "status": "running"  // async
}

GET /api/v2/backtest/{run_id}/status
GET /api/v2/backtest/{run_id}/results
```

**Архитектура:**

Бэктест работает на исторических данных из таблицы `price_data` (уже есть).
Для каждой свечи в хронологическом порядке:
1. Запустить `SignalEngineV2.generate()` на срезе данных до этой свечи (no lookahead)
2. Если сигнал сгенерирован → проверить entry fill на следующей свече
3. Для каждой открытой позиции → проверить SL/TP по high/low свечи (SIM-09 логика)
4. Собрать `BacktestResult` в памяти (не писать в основные таблицы)

**Хранение результатов:**

Отдельные таблицы: `backtest_runs` и `backtest_trades` — изолировано от live симулятора.

```sql
CREATE TABLE backtest_runs (
    id UUID PRIMARY KEY,
    params JSONB,
    status VARCHAR(16),   -- running/completed/failed
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    summary JSONB         -- итоговая статистика
);

CREATE TABLE backtest_trades (
    id SERIAL PRIMARY KEY,
    run_id UUID REFERENCES backtest_runs(id),
    symbol VARCHAR(32),
    timeframe VARCHAR(8),
    direction VARCHAR(8),
    entry_price NUMERIC(18,8),
    exit_price NUMERIC(18,8),
    exit_reason VARCHAR(32),
    pnl_pips NUMERIC(14,4),
    pnl_usd NUMERIC(14,4),
    result VARCHAR(16),
    composite_score NUMERIC(8,4),
    entry_at TIMESTAMPTZ,
    exit_at TIMESTAMPTZ,
    duration_minutes INTEGER,
    mfe NUMERIC(18,8),
    mae NUMERIC(18,8)
);
```

**Результаты бэктеста включают:**

```json
{
  "total_trades": 312,
  "win_rate_pct": 41.0,
  "profit_factor": 1.23,
  "total_pnl_usd": 84.50,
  "max_drawdown_pct": 12.3,
  "avg_duration_minutes": 94,
  "by_symbol": {...},
  "by_score_bucket": {...},
  "equity_curve": [{"date": "...", "balance": ...}],
  "monthly_returns": [{"month": "2024-01", "pnl_usd": 12.3, "trades": 18}]
}
```

**Тесты:**
- `test_sim22_backtest_no_lookahead` — данные после свечи не используются при генерации сигнала
- `test_sim22_backtest_sl_tp_check` — SL/TP применяются корректно по high/low
- `test_sim22_backtest_results_structure` — результат содержит все обязательные поля
- `test_sim22_backtest_isolated_from_live` — backtest не пишет в основные таблицы

---

### SIM-23: Диагностический дашборд (frontend + API)

**Проблема:**

Текущий UI показывает только P&L статистику. Для диагностики системы нужно видеть:
распределение компонентов scoring, MFE/MAE профили, как сигналы ведут себя по времени.

**Новые API эндпоинты:**

```
GET /api/v2/diagnostics/score-components
→ Среднее значение каждого компонента для всех сгенерированных сигналов
   + доля нулевых значений (признак missing data fallback)

GET /api/v2/diagnostics/mfe-mae-distribution
→ Для закрытых сделок: percentile distribution MFE и MAE
   + "early exit viability": сколько сделок можно было закрыть с меньшим убытком

GET /api/v2/diagnostics/signal-timing
→ Распределение сигналов по часам UTC (есть ли clustering в определённое время)
   + win rate по времени дня

GET /api/v2/diagnostics/partial-close-analysis
→ Сколько сделок достигли TP1 (могли бы сделать partial close)
   vs сколько затем вернулись к SL
```

**Frontend: новая вкладка "Diagnostics" на `/simulator`:**

- **Score Components Bar** — bar chart с разбивкой ta/fa/sentiment/geo/of по средним значениям.
  Red flag: если любой компонент в среднем < -3.0 (подозрение на systematic fallback).
- **MFE vs MAE Scatter** — scatter plot каждой сделки: x=MAE, y=MFE, colour=result.
  Хороший сигнал: выигрышные сделки вверху-слева (большой MFE, малый MAE).
- **Signal Bias Indicator** — текущее соотношение LONG/SHORT за последние 30 дней.
  Warning если > 80% в одну сторону.
- **Partial Close Readiness** — % открытых позиций, которые были в прибыли более TP1.

**Тесты:**
- `test_sim23_score_components_endpoint` — возвращает все компоненты с avg и zero_pct
- `test_sim23_mfe_mae_distribution` — корректные перцентили на mock данных
- `test_sim23_signal_bias_detected` — при 100% SHORT → bias flag = true

---

### SIM-24: Диагностика и фикс partial close (SIM-07 regression)

**Проблема:**

`partial_close_pnl_usd = NULL` для всех 6 закрытых сделок. Это означает, что partial close
никогда не срабатывал. Возможные причины:
1. Ни одна сделка не достигла TP2 (все закрылись по TP1 или SL)
2. Логика partial close содержит баг

**Диагностика:**

```sql
-- Проверить: были ли сделки с TP1 hit, которые продолжали жить дальше
SELECT sr.exit_reason, COUNT(*), AVG(sr.pnl_pips)
FROM signal_results sr
WHERE sr.exit_reason IN ('tp1_hit', 'tp2_hit', 'tp3_hit')
GROUP BY sr.exit_reason;

-- Проверить: есть ли позиции с partial_closed=true в virtual_portfolio
SELECT COUNT(*) FROM virtual_portfolio WHERE partial_closed = true;
```

**Ожидаемое поведение partial close (SIM-07):**

При достижении TP1:
1. Закрыть 50% позиции по TP1 цене
2. Обновить `size_remaining_pct = 0.5`
3. Переместить SL на breakeven (entry price)
4. Продолжить трекинг оставшейся половины до TP2 или SL@breakeven
5. При закрытии второй половины: `result` = win (даже если вторая часть закрылась по BE)

**Требование:**

- Написать тест `test_sim24_partial_close_triggers_at_tp1` — подтвердить что механизм работает
- Если баг найден — исправить, задокументировать
- Добавить `partial_close_count` в `/simulator/stats`

---

## 4. Порядок реализации

```
Фаза 1 (критические фиксы, P0):
  SIM-17 → SIM-19 → SIM-21

Фаза 2 (накопление данных):
  SIM-22 (бэктест) — запустить как можно раньше,
  результаты используются для валидации всех остальных изменений

Фаза 3 (улучшение управления позициями):
  SIM-20 → SIM-24

Фаза 4 (аналитика и наблюдаемость):
  SIM-23
```

---

## 5. Метрики успеха v4

После реализации всех задач система должна демонстрировать:

| Метрика | Текущее | Цель v4 |
|---------|---------|---------|
| LONG/SHORT ratio | 0% / 100% | 30–70% / 30–70% |
| Закрытых сделок (бэктест) | 6 | ≥ 100 |
| Win rate | 33% | ≥ 40% (breakeven для R:R 1.5) |
| Profit Factor | 0.79 | ≥ 1.0 |
| Avg MAE / SL distance | >200% | < 80% (SL не выбивается шумом) |
| Partial close triggered | 0% | > 20% от TP1 hits |
| Correlated position duplicates | 4 AUDUSD | 0 |

---

## 6. Зависимости и риски

**SIM-17 блокирует всё остальное.** Если bias на SHORT не устранён — все новые сделки
будут односторонними и результаты не будут репрезентативными.

**SIM-22 (бэктест) меняет оценочную базу.** После его реализации все метрики должны
пересчитываться на бэктест-данных (100+ сделок), а не на 6 реальных.

**SIM-19 (более широкий SL) увеличивает время в позиции.** При медленном движении
позиция может жить несколько дней → важно что SIM-13 (swap) работает корректно.

**SIM-20 (MAE early exit) требует калибровки.** Порог 60% должен быть подтверждён
на бэктест-данных, а не принят как догма. Первая версия — с настраиваемым параметром.
