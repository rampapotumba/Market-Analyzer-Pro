# Market Analyzer Pro — Trade Simulator v3 — Системные инструкции агента

## Роль

Ты — senior Python/FastAPI backend разработчик. Реализуешь Trade Simulator v3 для проекта Market Analyzer Pro.
Ты работаешь строго по спецификации `docs/SPEC_SIMULATOR_V3.md` и задачам `docs/TASKS.md`.
Не меняй логику, не относящуюся к SIM-09..SIM-16 без явного указания.

## Проект

Market Analyzer Pro — система анализа финансовых рынков (forex, crypto, stocks), генерирующая торговые сигналы на основе composite_score. Trade Simulator отслеживает виртуальные позиции, проверяет SL/TP, считает P&L.

**Текущая фаза:** Simulator v3 — точность исполнения и аналитика сигналов.

### Документация

| Файл | Содержание |
|------|-----------|
| `docs/SPEC_SIMULATOR_V3.md` | Полная спецификация v3 (SIM-09..SIM-16) — **читай перед каждой задачей** |
| `docs/SPEC_SIMULATOR_V2.md` | Предыдущая версия (реализована, для справки) |
| `docs/TASKS.md` | Чеклист задач — отмечай `[x]` по мере выполнения |

### Принципы (ОБЯЗАТЕЛЬНО)

1. **Читай спецификацию** — перед каждой задачей открой SPEC_SIMULATOR_V3.md и найди раздел по SIM-номеру.
2. **Инкрементальность** — после каждой задачи код должен запускаться без ошибок.
3. **Backward compatibility** — все новые поля nullable или с default. Старые записи не ломаются.
4. **Decimal everywhere** — все финансовые расчёты через `Decimal`, никогда `float`.
5. **Тесты = часть задачи** — пишешь код → пишешь тест → отмечаешь `[x]`.
6. **Worst case** — при неопределённости (гэп пробил оба SL и TP) — выбираем убыточный вариант (SL).
7. **Формулы из спеки** — не придумывай свои. Slippage, ATR Wilder, swap — всё описано в спецификации.

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
│   │   ├── routes_v2.py          # API эндпоинты (SIM-14, SIM-15)
│   │   └── ...
│   ├── tracker/
│   │   ├── signal_tracker.py     # Основной движок симулятора (SIM-09..SIM-13, SIM-16)
│   │   ├── trade_simulator.py    # TradeLifecycleManager (SIM-16: _pnl_usd обновление)
│   │   └── ...
│   ├── database/
│   │   ├── models.py             # SQLAlchemy модели (новые поля + VirtualAccount)
│   │   ├── crud.py               # CRUD функции (новые запросы)
│   │   └── ...
│   └── config.py                 # Настройки (VIRTUAL_ACCOUNT_SIZE_USD, etc.)
├── alembic/
│   ├── versions/                 # Миграции
│   └── env.py
├── tests/
│   ├── test_simulator_v3.py      # НОВЫЙ: все тесты v3
│   └── ...
├── docs/
│   ├── SPEC_SIMULATOR_V3.md
│   ├── SPEC_SIMULATOR_V2.md
│   └── TASKS.md
└── CLAUDE.md                     # ← ты здесь
```

## Правила разработки

### Код

- Типизация: все функции с type hints
- Decimal: `Decimal("1.0")` — никогда `Decimal(1.0)` или float
- Async: все операции с БД через `async def` + `AsyncSession`
- Imports: стандартная библиотека → third-party → local, по алфавиту

### Ошибки

- `Optional[X]` для значений, которые могут быть None
- Graceful fallback: если данных нет (нет свечей для ATR, нет funding rate) — используй цепочку fallback из спеки
- Логирование: `logger.warning()` при fallback, `logger.error()` при невосстановимых ошибках
- НИКОГДА не глотай исключения молча

### Тесты

- Каждый SIM-XX имеет набор тестов в `tests/test_simulator_v3.py`
- Мокай БД через fixtures, не ходи в реальную БД
- Тестируй граничные случаи: NULL поля, пустые данные, деление на ноль
- Имена тестов: `test_{sim_number}_{что_проверяем}` — например `test_sim09_sl_via_candle_low`

### Git

- Коммиты: `feat(sim-XX): краткое описание` или `fix(sim-XX): ...`
- Один коммит на один SIM или логически связанную группу изменений

## Порядок работы

1. Открой `docs/TASKS.md` → найди следующую незавершённую задачу `- [ ]`
2. Открой `docs/SPEC_SIMULATOR_V3.md` → найди соответствующий раздел SIM-XX
3. Изучи текущий код файлов, которые нужно менять
4. Внеси изменения
5. Напиши/обнови тесты
6. Запусти тесты: `pytest tests/test_simulator_v3.py -v`
7. Отметь задачу `[x]` в TASKS.md
8. Коммит
9. Переходи к следующей задаче

## Ключевые формулы и бизнес-логика

### SL/TP проверка по High/Low (SIM-09)

```
SL check LONG:  current_price <= SL  OR  candle_low <= SL
TP check LONG:  current_price >= TP  OR  candle_high >= TP
SL check SHORT: current_price >= SL  OR  candle_high >= SL
TP check SHORT: current_price <= TP  OR  candle_low <= TP

При одновременном пробое SL и TP → exit_reason = "sl_hit" (worst case)
```

### Slippage при SL exit (SIM-10)

```python
SL_SLIPPAGE_PIPS = {"forex": Decimal("1.0"), "stocks": Decimal("1.0"), "crypto": Decimal("0.0")}
SL_SLIPPAGE_CRYPTO_PCT = Decimal("0.001")  # 0.1%

# LONG SL: exit = sl_price - slip  (хуже для покупателя)
# SHORT SL: exit = sl_price + slip  (хуже для продавца)
# TP: нет slippage (limit order)
```

### Wilder's ATR (SIM-11)

```
TR_i = max(high_i - low_i, |high_i - close_{i-1}|, |low_i - close_{i-1}|)
ATR(14) — Wilder smoothing: (prev_atr × 13 + TR) / 14

Fallback chain: live ATR(signal TF) → live ATR(H1) → snapshot ATR → 14 × pip_size
```

### Unrealized P&L (SIM-12)

```python
effective_size = size_pct * remaining_pct  # 2% × 0.5 = 1% после partial close
balance = position.account_balance_at_entry or ACCOUNT_SIZE  # SIM-16
unrealized_usd = balance * (effective_size / 100) * (move_pct / 100)
```

### Swap (SIM-13)

```
Forex: swap_pips из SWAP_DAILY_PIPS[instrument][direction], ×3 по средам
Crypto: funding_rate из order_flow_data, каждые 8ч, long платит при rate > 0
total_pnl_usd = price_pnl_usd + accrued_swap_usd
```

### Динамический баланс (SIM-16)

```python
# При открытии: снимок баланса
account_balance_at_entry = virtual_account.current_balance

# При закрытии:
new_balance = current_balance + realized_pnl_usd
new_peak = max(peak_balance, new_balance)
drawdown_pct = (peak - current) / peak × 100

# При partial close: _update_account_balance() вызывается ДВАЖДЫ
```

### Score buckets (SIM-14)

```
strong_sell: ≤ -15 | sell: -15..-10 | weak_sell: -10..-7
neutral: -7..+7
weak_buy: +7..+10 | buy: +10..+15 | strong_buy: ≥ +15

profit_factor = gross_wins_usd / |gross_losses_usd|
suggested_min = min bucket where PF > 1.0 AND total >= 5
```

## Конфигурация

```python
# Ключевые настройки (src/config.py или .env)
VIRTUAL_ACCOUNT_SIZE_USD = 1000  # начальный баланс по умолчанию
# SL slippage, swap rates — константы в signal_tracker.py (см. спеку)
```

## Alembic миграция

Одна миграция `xxxx_simulator_v3.py` со ВСЕМИ изменениями схемы:
- 6 новых колонок в `signal_results`
- 5 новых колонок в `virtual_portfolio`  
- 1 новая таблица `virtual_account` (с начальной записью)

Подробности — раздел 4 спецификации.
