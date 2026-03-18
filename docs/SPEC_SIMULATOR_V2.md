# Спецификация: Trade Simulator v2 — Исправление логики виртуальной торговли

**Версия:** 1.0
**Дата:** 2026-03-18
**Статус:** DRAFT — к реализации

---

## 1. Контекст и цель

Текущий симулятор (v1) содержит ряд системных ошибок, которые делают накапливаемые торговые данные **непригодными для достоверного анализа качества сигналов**. Конкретно:

- win rate завышен из-за отсутствия спреда
- MFE/MAE всегда 0 (данные теряются при каждом тике)
- P&L в USD считается некорректно (без учёта размера позиции)
- Длительность сделки считается от создания сигнала, а не от входа
- Истёкшие без входа сигналы засчитываются как win/loss
- `TradeLifecycleManager` написан, но не подключён к трекеру

Цель v2 — исправить все перечисленные проблемы, сохранив обратную совместимость API и не меняя существующие таблицы без необходимости.

---

## 2. Перечень изменений

| # | Приоритет | Компонент | Описание |
|---|-----------|-----------|----------|
| SIM-01 | P0 | `signal_tracker.py` | Персистентность MFE/MAE через поле в `virtual_portfolio` |
| SIM-02 | P0 | `signal_tracker.py` + seed | Модель спреда: рыночно-специфичные константы |
| SIM-03 | P0 | `signal_tracker.py` + schema | Разделение `expired` (был вход) и `cancelled` (входа не было) |
| SIM-04 | P1 | `signal_tracker.py` | Duration: от `entry_filled_at`, не от `signal.created_at` |
| SIM-05 | P1 | `signal_tracker.py` | Подключение `TradeLifecycleManager`: breakeven + trailing stop |
| SIM-06 | P1 | `trade_simulator.py` | P&L USD с учётом `position_size_pct` сигнала |
| SIM-07 | P2 | `signal_tracker.py` + schema | Частичное закрытие по TP1: 50% + переход на трейлинг |
| SIM-08 | P2 | `signal_tracker.py` | Корректная entry tolerance по типу рынка |

---

## 3. Детальные требования

---

### SIM-01: Персистентность MFE/MAE

**Проблема:**
`SignalTracker._mfe` и `._mae` — словари в памяти объекта. `SignalTracker()` создаётся заново каждую минуту в `run_simulator_tick()`. Все накопленные значения обнуляются. В `signal_results.max_favorable_excursion` всегда записывается `Decimal("0")`.

**Требование:**
MFE/MAE должны накапливаться между тиками. Два допустимых подхода:

**Вариант A (рекомендуется):** Добавить поля в таблицу `virtual_portfolio`:
```sql
ALTER TABLE virtual_portfolio
  ADD COLUMN mfe NUMERIC(18, 8) DEFAULT 0,
  ADD COLUMN mae NUMERIC(18, 8) DEFAULT 0;
```
При каждом тике читать из `virtual_portfolio.mfe/mae`, обновлять если текущее значение больше, сохранять обратно. При закрытии позиции копировать в `signal_results`.

**Вариант B:** Сделать `SignalTracker` синглтоном с временем жизни = время жизни воркера (через `functools.lru_cache` или module-level instance в `scheduler/tasks.py`).

**Ограничение:** Вариант B хрупок при перезапуске воркера — после рестарта потеряем накопленное. Вариант A предпочтителен.

**Acceptance criteria:**
- После 10 тиков позиции в `signal_results.max_favorable_excursion` содержат ненулевые значения для сделок, где цена двигалась в нужном направлении
- После рестарта Celery-воркера MFE/MAE не обнуляются

---

### SIM-02: Модель спреда

**Проблема:**
Симулятор открывает позицию ровно по `signal.entry_price` без учёта bid/ask спреда. Реальный вход всегда хуже на величину спреда. Без этого win rate и PF систематически завышены.

**Требование:**
Ввести модуль `src/simulator/spread.py` (или константы в `signal_tracker.py`) с рыночно-специфичными спредами:

```python
# Типовые спреды при входе (в единицах pip_size инструмента)
SPREAD_BY_MARKET: dict[str, Decimal] = {
    "forex":  Decimal("1.5"),   # 1.5 пипа — Pepperstone Razor account
    "stocks": Decimal("2.0"),   # 2 цента / $0.01 пипа = 2 пипа
    "crypto": Decimal("0.0"),   # для крипты — % от цены (см. ниже)
}

# Для крипты: спред как % от цены (тейкер Binance = 0.075%)
CRYPTO_SPREAD_PCT: Decimal = Decimal("0.00075")
```

**Применение:**
В `_open_virtual_position()` при создании `entry_actual_price` применяем ухудшение:
- LONG:  `entry_actual_price = entry_price + spread_in_price_units`
- SHORT: `entry_actual_price = entry_price - spread_in_price_units`

Для forex/stocks: `spread_in_price = SPREAD_BY_MARKET[market] × pip_size`
Для crypto: `spread_in_price = entry_price × CRYPTO_SPREAD_PCT`

**P&L считается от `entry_actual_price`** (с учётом спреда), а не от `signal.entry_price`.

**Acceptance criteria:**
- EURUSD LONG signal: entry_price = 1.08500, entry_actual_price = 1.08515 (1.5 пипа хуже)
- BTC/USDT SHORT: entry_price = 85000, entry_actual_price = 84936.25 (0.075% лучше для SHORT)
- Profit Factor всех исторических сделок снизится на ~5–15% по сравнению с v1

---

### SIM-03: Разделение `expired` и `cancelled`

**Проблема:**
Когда сигнал истекает (`signal.expires_at < now`), код проверяет `signal.status in ("created", "active", "tracking")`. Сигнал в статусе `"created"` никогда не получал входа — это отменённый сигнал, а не завершённая сделка. Но для него создаётся `signal_result` с `result = win/loss/breakeven`, что загрязняет статистику.

**Требование:**

Расширить `exit_reason` в `signal_results`:

| exit_reason | Описание |
|---|---|
| `sl_hit` | Стоп-лосс сработал |
| `tp1_hit` | Первый тейкпрофит |
| `tp2_hit` | Второй тейкпрофит |
| `tp3_hit` | Третий тейкпрофит |
| `trailing_sl_hit` | Трейлинг-стоп сработал |
| `expired` | Позиция была открыта, сигнал истёк по времени |
| `cancelled` | **NEW** Вход так и не состоялся, сигнал истёк |

**Логика в expiry handler:**
```python
if signal.status == "created":
    # Вход никогда не был заполнен
    result_data = {
        "exit_reason": "cancelled",
        "result": "breakeven",  # не считать как win/loss
        "pnl_pips": Decimal("0"),
        "pnl_percent": Decimal("0"),
    }
    await update_signal_status(db, signal.id, "cancelled")  # новый статус
else:
    # Сигнал был активен — считаем реальный P&L
    result_data = {
        "exit_reason": "expired",
        "result": "win" if pips > 0 else "loss" if pips < 0 else "breakeven",
        ...
    }
    await update_signal_status(db, signal.id, "expired")
```

**Изменения схемы БД:**
- Добавить `"cancelled"` в допустимые значения `signal_results.exit_reason`
- Добавить `"cancelled"` в допустимые значения `signals.status`

**Изменения в API и фронтенде:**
- `cancelled` сделки **не включаются** в расчёт win rate, profit factor, avg win/loss
- В симуляторе и accuracy они показываются в отдельном счётчике или фильтруются отдельно
- Миграция Alembic: обновить `CHECK` constraint или добавить документацию

**Acceptance criteria:**
- Сигнал создан в 10:00, цена так и не дошла до entry, expires_at наступил: `exit_reason = "cancelled"`, `pnl_pips = 0`, не влияет на win_rate
- Сигнал создан в 10:00, вошёл в 11:00, не закрылся за 168ч: `exit_reason = "expired"`, P&L считается по текущей цене vs `entry_actual_price`

---

### SIM-04: Duration от entry_filled_at

**Проблема:**
`duration_minutes` считается от `signal.created_at`. Поле `signal_results.entry_filled_at` уже существует в схеме, но не используется для расчёта длительности.

**Требование:**
В `_close_signal()` и в expiry handler считать duration от момента фактического входа:

```python
# Текущий код (неверно):
duration_minutes = int((exit_at - _utc(signal.created_at)).total_seconds() / 60)

# Правильно:
entry_time = _utc(result.entry_filled_at) if result and result.entry_filled_at else _utc(signal.created_at)
duration_minutes = int((exit_at - entry_time).total_seconds() / 60)
```

Момент заполнения входа нужно записывать явно. В `_open_virtual_position()` и `open_position_for_signal()` при создании позиции сохранять `entry_filled_at = datetime.now(UTC)`. Обновлять `signal_results.entry_filled_at` при первом создании записи результата.

Дополнительно: добавить поле `entry_filled_at` в `virtual_portfolio` (если не существует) для хранения времени фактического входа.

**Acceptance criteria:**
- Сигнал создан в T+0, вошёл в T+30min, закрылся в T+90min: `duration_minutes = 60`
- Сигнал создан в T+0, вошёл в T+0 (немедленно), закрылся в T+120min: `duration_minutes = 120`

---

### SIM-05: Интеграция TradeLifecycleManager

**Проблема:**
`TradeLifecycleManager` в `trade_lifecycle.py` реализует breakeven, partial close и trailing stop, но **ни разу не вызывается** в `SignalTracker.check_signal()`. В результате:
- SL никогда не переносится на breakeven
- Трейлинг-стоп не активируется
- Позиция всегда закрывается полностью по TP1 вместо 50%

**Требование:**

#### 5.1 Необходимые поля в `virtual_portfolio`

Добавить поля (Alembic миграция):
```sql
ALTER TABLE virtual_portfolio
  ADD COLUMN breakeven_moved BOOLEAN DEFAULT FALSE,
  ADD COLUMN partial_closed BOOLEAN DEFAULT FALSE,
  ADD COLUMN trailing_stop NUMERIC(18, 8) DEFAULT NULL,
  ADD COLUMN current_stop_loss NUMERIC(18, 8) DEFAULT NULL;  -- актуальный SL (с учётом trail)
```

#### 5.2 Получение ATR для трейлинга

В `check_signal()` перед вызовом `TradeLifecycleManager` нужно получить ATR текущего инструмента:
```python
# Из последнего indicators_snapshot сигнала (уже есть JSON поле):
indicators = json.loads(signal.indicators_snapshot or "{}")
atr = Decimal(str(indicators.get("atr", 0))) or fallback_atr(instrument)
```

Fallback ATR: если `indicators_snapshot` пуст — рассчитать ATR(14) по последним 20 свечам H1 из `price_data`.

#### 5.3 Логика вызова TradeLifecycleManager

Заменить текущий блок в `check_signal()` для статусов `("active", "tracking")`:

```python
elif signal.status in ("active", "tracking"):
    position = await get_virtual_position(db, signal.id)
    await self._update_virtual_unrealized(db, signal, current_price)

    lifecycle = TradeLifecycleManager()
    action = lifecycle.check(
        direction=signal.direction,
        entry=entry_price,
        stop_loss=position.current_stop_loss or signal.stop_loss,
        take_profit_1=signal.take_profit_1,
        take_profit_2=signal.take_profit_2,
        take_profit_3=signal.take_profit_3,
        current_price=current_price,
        atr=atr,
        regime=signal.regime or "RANGING",
        partial_closed=position.partial_closed,
        breakeven_moved=position.breakeven_moved,
        trailing_stop=position.trailing_stop,
    )

    if action["action"].startswith("exit_"):
        exit_reason = action["action"].replace("exit_", "") + "_hit"
        if action["action"] == "exit_sl" and position.trailing_stop:
            exit_reason = "trailing_sl_hit"
        await self._close_signal(db, signal, current_price, now, exit_reason)

    elif action["action"] == "breakeven":
        await update_virtual_position(db, signal.id, {
            "current_stop_loss": action["new_stop_loss"],
            "breakeven_moved": True,
        })

    elif action["action"] == "partial_close":
        await self._partial_close(db, signal, position, current_price, now)
        await update_virtual_position(db, signal.id, {"partial_closed": True})

    elif action["action"] == "trailing_update":
        await update_virtual_position(db, signal.id, {
            "trailing_stop": action["new_stop_loss"],
            "current_stop_loss": action["new_stop_loss"],
        })
```

#### 5.4 Метод `_partial_close()`

```python
async def _partial_close(self, db, signal, position, exit_price, exit_at):
    """Закрыть 50% позиции по TP1, записать частичный результат."""
    # Рассчитать P&L на 50% позиции
    entry = position.entry_price or signal.entry_price
    pnl_pips, pnl_pct = self._calculate_pnl(signal.direction, entry, exit_price, pip_size)

    # Записать partial result (новое поле в signal_results или отдельная запись)
    await update_virtual_position(db, signal.id, {
        "size_remaining_pct": position.size_pct * Decimal("0.5"),
        "partial_closed": True,
        "partial_close_price": exit_price,
        "partial_close_at": exit_at,
        "partial_pnl_pct": pnl_pct * Decimal("0.5"),  # P&L на 50%
    })
    # Переносим SL на breakeven (автоматически после partial close)
    await update_virtual_position(db, signal.id, {
        "current_stop_loss": entry,
        "breakeven_moved": True,
    })
```

**Acceptance criteria:**
- LONG signal: entry=1.1000, SL=1.0925, TP1=1.1150. Когда цена = 1.1075 (RR 1:1), SL переносится на 1.1000
- Когда цена = 1.1143 (95% от TP1), `partial_closed=True`, SL → entry
- Trailing stop активируется только после breakeven

---

### SIM-06: Корректный P&L USD с учётом размера позиции

**Проблема:**
Текущая формула `pnl_usd = pnl_pct / 100 × ACCOUNT_SIZE` предполагает 100% аллокацию счёта на каждую позицию. При `position_size_pct = 2%` и `pnl_pct = 1%` реальный gain = `$1000 × 2% × 1% = $0.20`, а не `$10`.

**Требование:**

#### 6.1 Формула P&L USD

```python
def pnl_usd_for_position(
    pnl_pct: Decimal,       # % движения цены
    position_size_pct: Decimal,  # % от счёта, выделенный на позицию
    account_size: Decimal,
) -> Decimal:
    """
    pnl_usd = account_size × (position_size_pct / 100) × (pnl_pct / 100)

    Пример:
        account = $1000, position = 5% ($50), цена выросла на 2%
        pnl_usd = 1000 × 0.05 × 0.02 = $1.00
    """
    return account_size × (position_size_pct / Decimal("100")) × (pnl_pct / Decimal("100"))
```

#### 6.2 Получение position_size_pct

Источник (в порядке приоритета):
1. `signal.position_size_pct` — рассчитанный RiskManager при генерации сигнала
2. `virtual_portfolio.size_pct` — записанный при открытии позиции
3. Fallback: `settings.MAX_RISK_PER_TRADE_PCT` = 2.0%

#### 6.3 Обновление API

В `routes_v2.py` эндпоинты `/simulator/stats` и `/simulator/trades` должны возвращать `pnl_usd` по новой формуле. Поле `pnl_usd` в ответе пересчитывается на лету из хранимых `pnl_percent + position_size_pct`, либо сохраняется в `signal_results`.

Добавить поле `pnl_usd` в `signal_results`:
```sql
ALTER TABLE signal_results ADD COLUMN pnl_usd NUMERIC(14, 4);
```

**Acceptance criteria:**
- Signal: position_size_pct=2.0, pnl_pct=+1.5%, account=$1000 → pnl_usd = +$0.30
- Signal: position_size_pct=5.0, pnl_pct=-1.0%, account=$1000 → pnl_usd = -$0.50
- Суммарный Total P&L по всем сделкам = правдоподобная цифра (не десятки долларов за несколько сделок)

---

### SIM-07: Частичное закрытие по TP1

**Проблема:**
Текущий код в `check_signal()` при достижении TP1 закрывает 100% позиции. По плану v2/v3 нужно: 50% → TP1, SL → entry, оставшиеся 50% → до TP2/trailing.

**Требование:**

#### 7.1 Изменения схемы `virtual_portfolio`

```sql
ALTER TABLE virtual_portfolio
  ADD COLUMN size_remaining_pct NUMERIC(8, 4) DEFAULT 1.0,
  ADD COLUMN partial_close_price NUMERIC(18, 8),
  ADD COLUMN partial_close_at TIMESTAMP WITH TIME ZONE,
  ADD COLUMN partial_pnl_pct NUMERIC(8, 4);
```

#### 7.2 Изменения схемы `signal_results`

```sql
ALTER TABLE signal_results
  ADD COLUMN pnl_usd NUMERIC(14, 4),
  ADD COLUMN partial_close_pnl_usd NUMERIC(14, 4),  -- P&L от 50% по TP1
  ADD COLUMN full_close_pnl_usd NUMERIC(14, 4);     -- P&L от оставшихся 50%
```

#### 7.3 Логика жизненного цикла позиции

```
Шаг 1 (открытие):  size_remaining = 1.0 (100%)
Шаг 2 (RR 1:1):   breakeven_moved = True, current_stop_loss = entry_price
Шаг 3 (TP1 ≈ 95%): partial_close: закрыть 50%, size_remaining = 0.5,
                    записать partial_pnl_pct, breakeven_moved = True
Шаг 4 (после частичного закрытия): trailing stop на оставшихся 50%
Шаг 5 (TP2 или trailing hit): закрыть оставшиеся 50%, записать full_close_pnl_usd
```

#### 7.4 Расчёт итогового P&L

```python
total_pnl_pct = partial_pnl_pct * 0.5 + final_pnl_pct * 0.5
```

Хранить оба компонента для анализа (насколько выгоден трейлинг относительно простого выхода на TP1).

**Acceptance criteria:**
- Сделка с TP1=R:R 2 и TP2=R:R 3.5: если цена доходит до TP2, итоговый P&L = avg(2×SL, 3.5×SL) = 2.75×SL
- Сделка с TP1=R:R 2 и реверсом после частичного закрытия: итоговый P&L = avg(2×SL, -0.05×SL) = ~1×SL (breakeven за счёт трейлинга)

---

### SIM-08: Entry Tolerance по типу рынка

**Проблема:**
`ENTRY_TOLERANCE_PCT = 0.001` (0.1%) — плоское значение для всех рынков. Для EURUSD 0.1% = 10 пипов (очень широко). Для BTC при цене $85,000 → $85 — нормально. Для AAPL при $200 → $0.20 — разумно.

**Требование:**

Заменить плоский `ENTRY_TOLERANCE_PCT` на рыночно-специфичные значения:

```python
ENTRY_TOLERANCE_BY_MARKET: dict[str, Decimal] = {
    "forex":  Decimal("0.0003"),  # 0.03% = ~3 пипа — реалистичный slippage
    "stocks": Decimal("0.001"),   # 0.1% — нормальный gap для акций
    "crypto": Decimal("0.002"),   # 0.2% — криптовалюта более волатильна
}

def get_entry_tolerance(market: str) -> Decimal:
    return ENTRY_TOLERANCE_BY_MARKET.get(market, Decimal("0.001"))
```

В `_check_entry()` использовать `tolerance = entry_price × get_entry_tolerance(market)` вместо константы.

**Acceptance criteria:**
- EURUSD: entry=1.08500, tolerance ±0.00033 (3.3 пипа)
- BTC/USDT: entry=85000, tolerance ±170 (0.2%)
- AAPL: entry=200.00, tolerance ±0.20 (0.1%)

---

## 4. Изменения схемы БД (сводка)

Все изменения выполняются через **одну Alembic миграцию** `xxxx_simulator_v2.py`:

```python
# virtual_portfolio
op.add_column("virtual_portfolio", sa.Column("mfe", Numeric(18, 8), server_default="0"))
op.add_column("virtual_portfolio", sa.Column("mae", Numeric(18, 8), server_default="0"))
op.add_column("virtual_portfolio", sa.Column("breakeven_moved", Boolean, server_default="false"))
op.add_column("virtual_portfolio", sa.Column("partial_closed", Boolean, server_default="false"))
op.add_column("virtual_portfolio", sa.Column("trailing_stop", Numeric(18, 8), nullable=True))
op.add_column("virtual_portfolio", sa.Column("current_stop_loss", Numeric(18, 8), nullable=True))
op.add_column("virtual_portfolio", sa.Column("size_remaining_pct", Numeric(8, 4), server_default="1.0"))
op.add_column("virtual_portfolio", sa.Column("partial_close_price", Numeric(18, 8), nullable=True))
op.add_column("virtual_portfolio", sa.Column("partial_close_at", DateTime(timezone=True), nullable=True))
op.add_column("virtual_portfolio", sa.Column("partial_pnl_pct", Numeric(8, 4), nullable=True))
op.add_column("virtual_portfolio", sa.Column("entry_filled_at", DateTime(timezone=True), nullable=True))

# signal_results
op.add_column("signal_results", sa.Column("pnl_usd", Numeric(14, 4), nullable=True))
op.add_column("signal_results", sa.Column("partial_close_pnl_usd", Numeric(14, 4), nullable=True))
op.add_column("signal_results", sa.Column("full_close_pnl_usd", Numeric(14, 4), nullable=True))
```

Расширить CHECK constraints (или документацию):
- `signal_results.exit_reason`: добавить `"cancelled"`, `"trailing_sl_hit"`
- `signals.status`: добавить `"cancelled"`

---

## 5. Изменения файлов (сводка)

| Файл | Тип изменения | Описание |
|------|---------------|----------|
| `src/tracker/signal_tracker.py` | Изменение | SIM-01, SIM-02, SIM-03, SIM-04, SIM-05, SIM-07, SIM-08 |
| `src/tracker/trade_simulator.py` | Изменение | SIM-01, SIM-02, SIM-06 |
| `src/signals/trade_lifecycle.py` | Без изменений | Уже реализован корректно, только подключить |
| `src/database/models.py` | Изменение | Новые поля в VirtualPosition, SignalResult |
| `src/database/crud.py` | Изменение | Новые методы: `update_mfe_mae()`, `partial_close_position()` |
| `src/api/routes_v2.py` | Изменение | `/simulator/stats` и `/simulator/trades` с pnl_usd v2 |
| `alembic/versions/` | Новый файл | `xxxx_simulator_v2.py` — одна миграция для всех полей |
| `src/database/seed.py` | Проверка | pip_size корректен ✓ (BTC=1.0, stocks=0.01, forex=0.0001) |
| `tests/test_simulator_v2.py` | Новый файл | Тесты для всех SIM-01 .. SIM-08 |

---

## 6. Тестирование

### 6.1 Unit тесты (новый файл `tests/test_simulator_v2.py`)

| Тест | Проверяет |
|------|-----------|
| `test_spread_long_forex` | Entry LONG EURUSD: actual_price = entry + 1.5 пипа |
| `test_spread_short_crypto` | Entry SHORT BTC: actual_price = entry × (1 - 0.075%) |
| `test_cancelled_vs_expired` | Сигнал без входа → cancelled, 0 P&L; с входом → expired, реальный P&L |
| `test_duration_from_entry` | duration_minutes = exit - entry_filled_at, не created_at |
| `test_mfe_mae_persistence` | После 3 тиков MFE/MAE ненулевые, персистируются между тиками |
| `test_breakeven_trigger` | При RR 1:1 SL переносится на entry |
| `test_partial_close_tp1` | При 95% TP1: size_remaining=0.5, breakeven=True |
| `test_trailing_stop_update` | После breakeven: trailing_stop = price - 0.5×ATR (тренд) |
| `test_pnl_usd_with_position_size` | pnl_usd = account × size_pct × pnl_pct |
| `test_entry_tolerance_by_market` | forex=0.03%, stocks=0.1%, crypto=0.2% |

### 6.2 Регрессионные тесты

- Старые сигналы в БД: `cancelled` не влияют на win_rate
- `total_pnl_usd` в `/simulator/stats` пересчитывается корректно

---

## 7. Backward Compatibility

- API response shapes не меняются (новые поля добавляются, старые остаются)
- `pnl_usd` в существующих `signal_results` = NULL → API возвращает 0
- Frontend в `/accuracy` и `/simulator` уже использует `pnl_usd` напрямую — нужно убедиться что NULL обрабатывается как 0

---

## 8. Последовательность реализации

```
1. Alembic миграция (SIM-01 поля + SIM-06 pnl_usd + SIM-07 lifecycle поля)
2. SIM-03: cancelled/expired разделение (самое критичное для чистоты данных)
3. SIM-01: MFE/MAE через virtual_portfolio
4. SIM-02: Модель спреда
5. SIM-04: Duration от entry_filled_at
6. SIM-06: pnl_usd с position_size_pct
7. SIM-08: Entry tolerance по рынку
8. SIM-05: Интеграция TradeLifecycleManager (breakeven + trailing)
9. SIM-07: Partial close logic
10. Тесты
11. Обновление API эндпоинтов
12. Фронтенд: добавить partial_close_pnl, trailing данные в Simulator/Accuracy
```
