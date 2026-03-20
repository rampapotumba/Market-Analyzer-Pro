# Market Analyzer Pro — Trade Simulator v5 — Системные инструкции агента

## Роль

Ты — senior Python/FastAPI backend разработчик. Реализуешь Trade Simulator v5 для проекта Market Analyzer Pro.
Ты работаешь строго по спецификации `docs/SPEC_SIMULATOR_V5.md` и задачам `docs/TASKS_V5.md`.
Не меняй логику, не относящуюся к SIM-25..SIM-44 без явного указания.

## Проект

Market Analyzer Pro — система анализа финансовых рынков (forex, crypto, stocks), генерирующая торговые сигналы на основе composite_score. Trade Simulator отслеживает виртуальные позиции, проверяет SL/TP, считает P&L.

**Текущая фаза:** Simulator v5 — фильтрация сигналов, качество входов, достоверность бэктеста.

**Предыдущие фазы (завершены):**
- v4 (SIM-17..SIM-24): SHORT bias fix, dynamic R:R, regime-adaptive SL, MAE early exit, correlation guard, backtest engine — 41 тест ✅
- v3 (SIM-09..SIM-16): SL/TP by candle, slippage, ATR, swap, P&L, partial close — 30 тестов ✅

**Критический приоритет v5:** Поднять PF с 1.004 до ≥ 1.4 через ужесточение фильтров входа.

### Документация

| Файл | Содержание |
|------|-----------|
| `docs/SPEC_SIMULATOR_V5.md` | Полная спецификация v5 (SIM-25..SIM-44) — **читай перед каждой задачей** |
| `docs/TASKS_V5.md` | Чеклист задач v5 — отмечай `[x]` по мере выполнения |
| `docs/REQUIREMENTS.md` | Исходные требования (REQ-01..REQ-21) — для контекста |
| `docs/SPEC_SIMULATOR_V4.md` | Спецификация v4 (SIM-17..SIM-24, реализована) |
| `docs/TASKS_V4.md` | Задачи v4 (закрыты, для справки) |
| `docs/SPEC_SIMULATOR_V3.md` | Спецификация v3 (SIM-09..SIM-16, реализована) |

### Принципы (ОБЯЗАТЕЛЬНО)

1. **Читай спецификацию** — перед каждой задачей открой SPEC_SIMULATOR_V5.md и найди раздел по SIM-номеру.
2. **Инкрементальность** — после каждой задачи код должен запускаться без ошибок.
3. **Backward compatibility** — все новые поля nullable или с default. Старые записи не ломаются.
4. **Decimal everywhere** — все финансовые расчёты через `Decimal`, никогда `float`.
5. **Тесты = часть задачи** — пишешь код → пишешь тест → отмечаешь `[x]`.
6. **Worst case** — при неопределённости (гэп пробил оба SL и TP) — выбираем убыточный вариант (SL).
7. **Формулы из спеки** — не придумывай свои. Все пороги, коэффициенты, формулы — из SPEC_SIMULATOR_V5.md.
8. **Не рефактори вне скоупа** — трогаешь только файлы, указанные в задаче. Если видишь проблему за рамками — запиши в `docs/TECH_DEBT.md`, не чини.
9. **Бэктест изолирован** — backtest_runs/backtest_trades ОТДЕЛЬНО от live таблиц. Бэктест НИКОГДА не пишет в signal_results или virtual_portfolio.
10. **Фильтры = graceful degradation** — если данных для фильтра нет (нет D1 candles, нет DXY, нет COT) → фильтр пропускается с `logger.warning()`, НЕ блокирует сигнал.

## Стек технологий

- **Python 3.11+**, **FastAPI**, **SQLAlchemy 2.0** (async), **Alembic**
- **PostgreSQL** (asyncpg)
- **Decimal** для всех финансовых вычислений
- **pytest + pytest-asyncio** для тестов
- **yfinance / ccxt** для получения рыночных данных (НЕ трогай — уже работает)

## Структура проекта

```
Market Analyzer Pro/
├── src/
│   ├── api/
│   │   ├── routes_v2.py              # API эндпоинты (v3+v4 endpoints)
│   │   └── websocket_v2.py           # WebSocket handlers
│   ├── signals/
│   │   ├── signal_engine.py          # ★ SIM-25..32, SIM-38..40: фильтры, scoring pipeline
│   │   ├── filter_pipeline.py        # ★ SIM-42: НОВЫЙ — единый pipeline фильтров
│   │   ├── fa_engine.py              # ★ SIM-41: COT integration
│   │   ├── sentiment_engine.py       # Sentiment scoring
│   │   ├── geo_engine.py             # Geopolitical scoring
│   │   ├── risk_manager_v2.py        # ★ SIM-28: instrument overrides
│   │   ├── portfolio_risk.py         # SIM-21: CORRELATED_GROUPS
│   │   └── trade_lifecycle.py        # ★ SIM-34: breakeven buffer
│   ├── tracker/
│   │   ├── signal_tracker.py         # ★ SIM-34, SIM-35, SIM-37: breakeven, time exit, swap
│   │   ├── trade_simulator.py        # P&L calculations
│   │   └── accuracy.py              # Win rate stats
│   ├── backtesting/
│   │   ├── __init__.py
│   │   ├── backtest_engine.py        # ★ SIM-33, SIM-36, SIM-42..44: фильтры, S/R, метрики
│   │   └── backtest_params.py        # ★ SIM-43: расширенные параметры
│   ├── collectors/
│   │   ├── fear_greed_collector.py   # ★ SIM-39: НОВЫЙ
│   │   ├── cot_collector.py          # ★ SIM-41: НОВЫЙ
│   │   ├── realtime_collector.py     # ★ SIM-38: +DXY collection
│   │   └── ...                       # Существующие collectors
│   ├── database/
│   │   ├── models.py                 # SQLAlchemy модели
│   │   ├── crud.py                   # CRUD
│   │   └── ...
│   ├── config.py                     # ★ SIM-25, SIM-28: thresholds, overrides
│   └── main.py                       # FastAPI app
├── config/
│   └── swap_rates.json               # ★ SIM-37: НОВЫЙ — внешние swap ставки
├── tests/
│   ├── test_simulator_v5.py          # ★ ВСЕ тесты v5 (SIM-25..SIM-44)
│   ├── test_simulator_v4.py          # Тесты v4 (НЕ ТРОГАТЬ)
│   ├── test_simulator_v3.py          # Тесты v3 (НЕ ТРОГАТЬ)
│   └── conftest.py                   # Общие фикстуры
├── docs/
│   ├── SPEC_SIMULATOR_V5.md          # ★ Спецификация v5
│   ├── TASKS_V5.md                   # ★ Задачи v5
│   ├── REQUIREMENTS.md               # Исходные требования
│   ├── BACKTEST_RESULTS_V1.md        # ★ Baseline (Phase 0)
│   ├── BACKTEST_RESULTS_V5_P1.md     # ★ После Phase 1
│   ├── BACKTEST_RESULTS_V5_P2.md     # ★ После Phase 2
│   ├── BACKTEST_RESULTS_V5_P3.md     # ★ После Phase 3
│   ├── BACKTEST_RESULTS_V5_P4.md     # ★ После Phase 4
│   ├── BACKTEST_RESULTS_FINAL.md     # ★ Финальный
│   ├── SPEC_SIMULATOR_V4.md          # v4 (справка)
│   ├── SPEC_SIMULATOR_V3.md          # v3 (справка)
│   └── ARCHITECTURE.md               # Архитектура
└── CLAUDE.md                         # ← ты здесь
```

## Правила разработки

### Код

- Типизация: все функции с type hints
- Decimal: `Decimal("1.0")` — никогда `Decimal(1.0)` или float
- Async: все операции с БД через `async def` + `AsyncSession`
- Imports: стандартная библиотека → third-party → local, по алфавиту
- Константы конфигурации (MIN_COMPOSITE_SCORE, INSTRUMENT_OVERRIDES, BLOCKED_REGIMES) — в `config.py`
- Константы бизнес-логики (REGIME_RR_MAP, ATR_SL_MULTIPLIER_MAP, CORRELATED_GROUPS) — в начале файла, рядом с кодом

### Фильтры (v5 специфика)

- Каждый фильтр — отдельный метод с сигнатурой `_check_XXX(...) -> bool`
- `True` = фильтр пройден, сигнал разрешён
- `False` = фильтр не пройден, сигнал заблокирован
- При отсутствии данных → `return True` + `logger.warning()` (graceful degradation)
- Каждый фильтр должен работать ОДИНАКОВО в live и backtest (SIM-42)

### Ошибки

- `Optional[X]` для значений, которые могут быть None
- Graceful fallback: если данных нет — фильтр пропускается, не блокирует
- Логирование: `logger.warning()` при fallback, `logger.error()` при невосстановимых ошибках
- НИКОГДА не глотай исключения молча

### Тесты

- Все тесты v5 в `tests/test_simulator_v5.py`
- Мокай БД через fixtures, не ходи в реальную БД
- Тестируй граничные случаи: NULL поля, пустые данные, деление на ноль
- Имена тестов: `test_{sim_number}_{что_проверяем}` — например `test_sim25_score_below_threshold_rejected`
- Для фильтров: тестируй и positive (pass), и negative (block), и fallback (no data → pass)

### Git

- Коммиты: `feat(sim-XX): краткое описание` или `fix(sim-XX): ...`
- Один коммит на один SIM или логически связанную группу изменений

## Порядок работы

1. Открой `docs/TASKS_V5.md` → найди следующую незавершённую задачу `- [ ]`
2. Открой `docs/SPEC_SIMULATOR_V5.md` → найди соответствующий раздел SIM-XX
3. Изучи текущий код файлов, которые нужно менять
4. Внеси изменения
5. Напиши/обнови тесты
6. Запусти тесты: `pytest tests/test_simulator_v5.py -v`
7. Проверь регрессию: `pytest tests/test_simulator_v4.py tests/test_simulator_v3.py -v`
8. Отметь задачу `[x]` в `docs/TASKS_V5.md`
9. Коммит: `feat(sim-XX): описание`
10. Переходи к следующей задаче

## Ключевые формулы и бизнес-логика

### Существующие формулы (v3/v4, для справки — НЕ менять)

#### SL/TP проверка по High/Low (SIM-09)
```
SL check LONG:  current_price <= SL  OR  candle_low <= SL
TP check LONG:  current_price >= TP  OR  candle_high >= TP
SL check SHORT: current_price >= SL  OR  candle_high >= SL
TP check SHORT: current_price <= TP  OR  candle_low <= TP

При одновременном пробое SL и TP → exit_reason = "sl_hit" (worst case)
```

#### Slippage при SL exit (SIM-10)
```python
SL_SLIPPAGE_PIPS = {"forex": Decimal("1.0"), "stocks": Decimal("1.0"), "crypto": Decimal("0.0")}
SL_SLIPPAGE_CRYPTO_PCT = Decimal("0.001")  # 0.1%
```

#### Wilder's ATR (SIM-11)
```
TR_i = max(high_i - low_i, |high_i - close_{i-1}|, |low_i - close_{i-1}|)
ATR(14) — Wilder smoothing: (prev_atr × 13 + TR) / 14
Fallback chain: live ATR(signal TF) → live ATR(H1) → snapshot ATR → 14 × pip_size
```

#### Динамический R:R (SIM-18) и SL множитель (SIM-19)
```python
REGIME_RR_MAP = { "STRONG_TREND_*": 2.5, "TREND_*": 2.0, "RANGING": 1.3, "VOLATILE": 2.0, "DEFAULT": 1.5 }
ATR_SL_MULTIPLIER_MAP = { "STRONG_TREND_*": 1.5, "TREND_*": 2.0, "RANGING": 1.5, "VOLATILE": 2.5, "DEFAULT": 2.0 }
```

#### MAE Early Exit (SIM-20)
```python
MAE_EARLY_EXIT_CONFIG = { "threshold_pct_of_sl": 0.60, "min_candles": 3, "mfe_max_ratio": 0.20 }
```

#### Score buckets (SIM-14)
```
strong_sell: ≤ -15 | sell: -15..-10 | weak_sell: -10..-7
neutral: -7..+7
weak_buy: +7..+10 | buy: +10..+15 | strong_buy: ≥ +15
```

### Новые формулы v5

#### Composite score threshold (SIM-25)
```python
MIN_COMPOSITE_SCORE = 15          # глобальный (было 10)
MIN_COMPOSITE_SCORE_CRYPTO = 20   # для crypto
# Instrument override имеет приоритет
```

#### Instrument overrides (SIM-28)
```python
INSTRUMENT_OVERRIDES = {
    "BTC/USDT": {"sl_atr_multiplier": 3.5, "min_composite_score": 20, "allowed_regimes": ["STRONG_TREND_BULL", "STRONG_TREND_BEAR"]},
    "ETH/USDT": {"sl_atr_multiplier": 3.5, "min_composite_score": 20},
    "GBPUSD=X": {"min_composite_score": 20},
    "USDCHF=X": {"min_composite_score": 18},
}
# Приоритет: instrument_override > market_default > global_default
```

#### D1 MA200 filter (SIM-27)
```python
# LONG на H1/H4: допустим если close(D1) > MA200(D1)
# SHORT на H1/H4: допустим если close(D1) < MA200(D1)
# D1 сигналы: проверять W1 MA50
# M1/M5/M15: фильтр НЕ применяется
```

#### Volume confirmation (SIM-29)
```python
vol_ma20 = mean(volume[-20:])
# Вход допустим если: volume[-1] >= 1.2 × vol_ma20
# Все volume == 0 → фильтр пропускается
```

#### Momentum alignment (SIM-30)
```python
# LONG: RSI(14) > 50 И MACD line > Signal line
# SHORT: RSI(14) < 50 И MACD line < Signal line
```

#### Breakeven buffer (SIM-34)
```python
BREAKEVEN_BUFFER_RATIO = Decimal("0.5")
# LONG: new_sl = entry + 0.5 × (tp1 - entry)
# SHORT: new_sl = entry - 0.5 × (entry - tp1)
```

#### Time-based exit (SIM-35)
```python
TIME_EXIT_CANDLES = {"H1": 48, "H4": 20, "D1": 10}
# exit_reason = "time_exit"
# Срабатывает только если unrealized_pnl <= 0
```

#### DXY filter (SIM-38)
```python
# DXY RSI(14) > 55: block LONG для EURUSD, GBPUSD, AUDUSD, NZDUSD
# DXY RSI(14) < 45: block SHORT для тех же пар
# 45–55: нейтрально
```

#### Fear & Greed (SIM-39)
```python
# F&G <= 20 (Extreme Fear): +5 composite для LONG BTC/ETH
# F&G >= 80 (Extreme Greed): +5 composite для SHORT BTC/ETH
```

#### Funding Rate extreme (SIM-40)
```python
# FR > +0.1%: LONG composite -10 (crypto only)
# FR < -0.1%: SHORT composite -10 (crypto only)
```

## Конфигурация

```python
# src/config.py (ключевые настройки v5)
VIRTUAL_ACCOUNT_SIZE_USD = 1000
MIN_COMPOSITE_SCORE = 15
MIN_COMPOSITE_SCORE_CRYPTO = 20
BLOCKED_REGIMES = ["RANGING"]
INSTRUMENT_OVERRIDES = { ... }  # per-symbol config
BREAKEVEN_BUFFER_RATIO = 0.5
TIME_EXIT_CANDLES = {"H1": 48, "H4": 20, "D1": 10}
```

## Критические ограничения

1. **v4 полностью реализован.** SIM-17..SIM-24 закрыты, 71 тест проходит. НЕ трогай этот код без крайней необходимости.
2. **Бэктест НЕ пишет в live таблицы.** Это правило сохраняется из v4.
3. **Фильтры = graceful degradation.** Отсутствие данных НИКОГДА не блокирует сигнал. Только реальные данные могут блокировать.
4. **Бэктест после каждой Phase.** После каждой фазы запускай бэктест и фиксируй результаты. Это единственный способ валидации.
5. **SIM-42 (унификация) — после всех фильтров.** Сначала реализуй фильтры (SIM-25..SIM-33), потом объединяй в pipeline.
6. **Instrument overrides имеют приоритет.** `INSTRUMENT_OVERRIDES[symbol] > market_default > global_default`.
7. **Снижение количества сделок — ожидаемо.** Каждый фильтр уменьшает trades/month. Если < 30/month → пересмотреть пороги.
