# Спецификация: Trade Simulator v3 — Точность исполнения и аналитика сигналов

**Версия:** 1.0
**Дата:** 2026-03-18
**Статус:** DRAFT — к реализации
**Предыдущая версия:** `docs/SPEC_SIMULATOR_V2.md` (реализована, все SIM-01..SIM-08 закрыты)

---

## 1. Контекст и цель

После реализации v2 симулятор корректно учитывает спред, разделяет cancelled/expired, хранит MFE/MAE, применяет TradeLifecycleManager. Это сделало данные пригодными для **базового анализа**.

Для перехода к **достоверному анализу качества сигналов** (ответ на вопрос "какой composite_score реально прибылен?") остаются структурные проблемы:

1. SL/TP проверяется по цене последней сделки — пропускает внутрисвечные пробои
2. Нет slippage при выходе — P&L систематически завышен на убыточных сделках
3. Trailing stop строится на ATR из снимка сигнала, а не на текущем
4. Unrealized P&L не учитывает размер позиции и частичное закрытие
5. Нет учёта overnight swap — многодневные сделки имеют искажённый P&L
6. Нет аналитики корреляции `composite_score → результат` — а это и есть главная цель всей системы
7. **Баланс виртуального счёта статичен** — каждая сделка рассчитывается от начального $1000 независимо от истории, что делает невозможным корректный drawdown и path-dependent position sizing

Цель v3 — устранить перечисленные проблемы и добавить аналитический слой, который позволит принимать решения по калибровке порогов.

---

## 2. Перечень изменений

| # | Приоритет | Компонент | Описание |
|---|-----------|-----------|----------|
| SIM-09 | P0 | `signal_tracker.py` | SL/TP проверка по High/Low свечи, а не только по last price |
| SIM-10 | P0 | `signal_tracker.py` | Slippage при выходе по SL (рыночное исполнение хуже цены) |
| SIM-11 | P1 | `signal_tracker.py` | ATR для trailing stop: живой расчёт из последних свечей |
| SIM-12 | P1 | `signal_tracker.py` | Unrealized P&L с учётом position_size_pct и partial close |
| SIM-13 | P2 | `signal_tracker.py` + schema | Overnight swap: начисление по рыночно-специфичным ставкам |
| SIM-14 | P0 | `routes_v2.py` + schema | Score → Outcome: аналитика по диапазонам composite_score |
| SIM-15 | P1 | `routes_v2.py` | Расширенная аналитика: breakdown по TF, направлению, exit_reason |
| SIM-16 | P0 | `signal_tracker.py` + `trade_simulator.py` + schema | Динамический баланс счёта: каждая сделка меняет текущий баланс, position sizing path-dependent |

---

## 3. Детальные требования

---

### SIM-09: SL/TP по High/Low свечи

**Проблема:**

Текущий код проверяет SL/TP исключительно по `current_price` — цене последней сделки (last price), получаемой от yfinance/ccxt. Если между двумя тиками симулятора (1 минута) цена уходила ниже SL и возвращалась, симулятор этого не увидит. Для M15/H1 сигналов это создаёт систематическую ошибку: позиции живут дольше, чем должны, искусственно улучшая результаты.

**Пример:**
```
Тик N:   last = 1.10050 (выше SL 1.09900) → SL не проверяется
Реально: low свечи M15 = 1.09880 → SL должен был сработать
Тик N+1: last = 1.10100 → SL по-прежнему "не достигнут"
```

**Требование:**

При каждом тике симулятора наряду с `current_price` (last) получать `candle_high` и `candle_low` из последней завершённой свечи соответствующего таймфрейма. Использовать для проверок:

```
SL check LONG:  current_price <= SL  ИЛИ  candle_low <= SL
TP check LONG:  current_price >= TP  ИЛИ  candle_high >= TP
SL check SHORT: current_price >= SL  ИЛИ  candle_high >= SL
TP check SHORT: current_price <= TP  ИЛИ  candle_low <= TP
```

**Источник данных `candle_high`/`candle_low`:**

Уже хранятся в `price_data` (поля `high`, `low`). Нужно читать последнюю завершённую свечу таймфрейма сигнала (не текущую открытую).

```python
# Новая сигнатура метода
async def _get_candle_prices(
    self,
    db: AsyncSession,
    instrument_id: int,
    timeframe: str,
) -> tuple[Decimal, Decimal, Decimal]:
    """Return (last_close, candle_high, candle_low) of last completed candle."""
```

**Важный нюанс — приоритет исполнения при одновременном пробое SL и TP:**

Если за одну свечу пробиты оба уровня (бывает при гэпах):
- Использовать правило `worst case`: считать, что сначала сработал SL (консервативная оценка)
- Это стандартная практика в backtesting: когда неизвестен порядок — предполагаем худшее

```python
# Логика приоритета
if sl_hit and tp_hit:
    # Оба пробиты — не можем знать порядок, берём worst case
    exit_reason = "sl_hit"
    exit_price  = stop_loss  # а не current_price
elif tp_hit:
    exit_reason = "tp1_hit" / "tp2_hit"
    exit_price  = take_profit_level  # точно по уровню
elif sl_hit:
    exit_reason = "sl_hit"
    exit_price  = stop_loss  # точно по уровню (до применения slippage)
```

**Изменения схемы БД:**

Добавить поля в `signal_results` для аудита:
```sql
ALTER TABLE signal_results
  ADD COLUMN candle_high_at_exit NUMERIC(18, 8),  -- high свечи на момент выхода
  ADD COLUMN candle_low_at_exit  NUMERIC(18, 8);  -- low свечи на момент выхода
```

**Acceptance criteria:**
- LONG сделка: SL = 1.09900, last = 1.10020, candle_low = 1.09880 → `sl_hit`, exit = 1.09900 (до slippage)
- LONG сделка: TP1 = 1.11500, last = 1.11400, candle_high = 1.11520 → `tp1_hit`, exit = 1.11500
- Гэп пробил оба: выбирается `sl_hit`

---

### SIM-10: Slippage при выходе по SL

**Проблема:**

При срабатывании SL в реальности:
- Если это **stop-loss order** (limit stop) — исполняется как market order после пробоя уровня. Цена исполнения обычно хуже SL на 1–5 пипов в нормальных условиях и на 10–50 пипов во время новостей.
- Если TP — **limit order** — исполняется точно по уровню (или лучше, если рынок перепрыгнул).

Сейчас при `sl_hit` exit_price = current_price ≈ SL. Это дарит каждой проигрышной сделке 1–3 пипа. Накопленный эффект — систематически завышенный profit factor.

**Требование:**

Ввести константы slippage при выходе (отдельно от спреда при входе):

```python
# Slippage applied to SL exit (in pip units) — market order worst case
SL_SLIPPAGE_PIPS: dict[str, Decimal] = {
    "forex":  Decimal("1.0"),   # 1 пип — нормальные условия ECN
    "stocks": Decimal("1.0"),   # 1 цент / 0.01 pip_size
    "crypto": Decimal("0.0"),   # для крипто — % от цены
}
SL_SLIPPAGE_CRYPTO_PCT: Decimal = Decimal("0.001")  # 0.1% — taker при пробое

# TP исполняется как limit — нет дополнительного slippage
# (при гэпе через TP — exit_price = TP, не better fill, консервативно)
```

**Применение:**

```python
def _apply_sl_slippage(
    sl_price: Decimal,
    direction: str,
    market: str,
    pip_size: Decimal,
) -> Decimal:
    """Ухудшить цену выхода по SL на величину проскальзывания.

    LONG SL: цена исполнения ниже SL (хуже для покупателя)
    SHORT SL: цена исполнения выше SL (хуже для продавца)
    """
    if market == "crypto":
        slip = sl_price * SL_SLIPPAGE_CRYPTO_PCT
    else:
        slip = SL_SLIPPAGE_PIPS.get(market, Decimal("1.0")) * pip_size

    return sl_price - slip if direction == "LONG" else sl_price + slip
```

**Когда применяется:**

| exit_reason | Slippage | Комментарий |
|---|---|---|
| `sl_hit` | ✅ применяется | Market order после пробоя |
| `trailing_sl_hit` | ✅ применяется | Тот же механизм |
| `tp1_hit` | ❌ не применяется | Limit order |
| `tp2_hit` | ❌ не применяется | Limit order |
| `tp3_hit` | ❌ не применяется | Limit order |
| `expired` | ❌ не применяется | Market close по текущей цене |

**Добавить поле в `signal_results`:**
```sql
ALTER TABLE signal_results
  ADD COLUMN exit_slippage_pips NUMERIC(8, 4);  -- фактическое проскальзывание в пипах
```

**Acceptance criteria:**
- LONG EURUSD SL hit: SL = 1.09900, exit_actual = 1.09890 (1 пип хуже), `exit_slippage_pips = -1.0`
- LONG BTC SL hit: SL = 85000, exit_actual = 84915 (0.1% хуже), slippage = -85 USD / pip_size

---

### SIM-11: Живой ATR для trailing stop

**Проблема:**

`TradeLifecycleManager` получает ATR из `signal.indicators_snapshot` — снимка на момент **генерации сигнала**. Сделка может жить 2–5 дней. За это время:
- ATR H4 может вырасти с 50 до 120 пипов (перед FOMC), сделав trailing слишком тесным
- ATR может сжаться с 80 до 30 пипов (после публикации), оставив trailing чрезмерно широким

Следствие: trailing stop не адаптируется к текущей волатильности, что делает MFE/MAE недостоверными.

**Требование:**

При каждом тике в `_get_atr()` вычислять ATR(14) из последних 15 свечей таймфрейма сигнала, хранящихся в `price_data`. Использовать стандартную формулу Wilder's ATR:

```
True Range_i = max(high_i - low_i, |high_i - close_{i-1}|, |low_i - close_{i-1}|)
ATR(14)      = EMA(True Range, period=14)  # Wilder's smoothing: (prev × 13 + TR) / 14
```

```python
async def _get_live_atr(
    self,
    db: AsyncSession,
    instrument_id: int,
    timeframe: str,
    period: int = 14,
) -> Optional[Decimal]:
    """Compute ATR(14) from recent price_data candles."""
    candles = await get_price_data(db, instrument_id, timeframe, limit=period + 1)
    if len(candles) < period + 1:
        return None

    trs: list[Decimal] = []
    for i in range(1, len(candles)):
        h, l, prev_c = candles[i].high, candles[i].low, candles[i-1].close
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)

    # Wilder's smoothing
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr
```

**Fallback-цепочка (в порядке приоритета):**
1. Живой ATR из `price_data` для таймфрейма сигнала
2. Живой ATR из `price_data` для H1 (если данных таймфрейма нет)
3. ATR из `signal.indicators_snapshot` (старый, стейл)
4. `14 × pip_size` (крайний fallback)

**Таймфрейм для ATR:**
- Использовать `signal.timeframe` — тот же TF, на котором генерировался сигнал
- Это обеспечивает согласованность: H4 сигнал управляется H4 ATR

**Acceptance criteria:**
- При наличии 15+ свечей в `price_data` — используется живой ATR
- Если волатильность выросла в 2× за время жизни сделки, trailing stop расширяется соответственно
- При отсутствии данных — graceful fallback без ошибки

---

### SIM-12: Unrealized P&L с учётом позиции и partial close

**Проблема A — Position sizing:**

```python
# Текущий код:
pnl_pct = (current_price - entry_price) / entry_price * Decimal("100")
await update_virtual_position(db, signal.id, {"unrealized_pnl_pct": pnl_pct})
```

`unrealized_pnl_pct` хранит % ценового движения (например, +1.5%). В API `/simulator/open` это конвертируется через `pnl_usd(unrealized_pct)` с `position_size_pct=None` → fallback 100% → $15 вместо реальных $0.30 при позиции 2%.

**Проблема B — Partial close:**

После частичного закрытия 50% (SIM-07) в `virtual_portfolio.size_remaining_pct = 0.5`, но `_update_virtual_unrealized` продолжает считать P&L как для полной позиции. Unrealized P&L для оставшейся половины завышен в 2×.

**Требование:**

```python
async def _update_virtual_unrealized(
    self,
    db: AsyncSession,
    signal: Signal,
    current_price: Decimal,
    entry_price: Decimal,
    position: VirtualPortfolio,  # передавать позицию, не только сигнал
) -> None:
    if signal.direction == "LONG":
        move_pct = (current_price - entry_price) / entry_price * Decimal("100")
    else:
        move_pct = (entry_price - current_price) / entry_price * Decimal("100")

    # Учитываем реальный размер оставшейся позиции
    size_pct = position.size_pct or Decimal("2.0")
    remaining = position.size_remaining_pct or Decimal("1.0")  # 0.5 после partial close
    effective_size = size_pct * remaining  # например: 2.0% × 0.5 = 1.0%

    unrealized_usd = ACCOUNT_SIZE * (effective_size / Decimal("100")) * (move_pct / Decimal("100"))

    await update_virtual_position(db, signal.id, {
        "current_price":       current_price,
        "unrealized_pnl_pct":  move_pct.quantize(Decimal("0.0001")),
        "unrealized_pnl_usd":  unrealized_usd.quantize(Decimal("0.01")),  # НОВОЕ поле
    })
```

**Новое поле в `virtual_portfolio`:**
```sql
ALTER TABLE virtual_portfolio
  ADD COLUMN unrealized_pnl_usd NUMERIC(14, 4);
```

**Изменения в API `/simulator/open`:**

Возвращать `unrealized_pnl_usd` напрямую из поля, а не пересчитывать через `pnl_usd()`.

**Изменения в `/simulator/stats`:**

`unrealized_pnl_usd` в stats суммировать из нового поля, а не вычислять из pnl_pct.

**Acceptance criteria:**
- Позиция: size_pct=2%, move=+1.5%, remaining=100% → unrealized_usd = +$0.30
- После partial close: remaining=50%, move=+2.0% → unrealized_usd = +$0.20 (только на половину)
- `unrealized_pnl_pct` остаётся как % движения цены (для отображения), `unrealized_pnl_usd` — для суммирования

---

### SIM-13: Overnight swap (financing cost)

**Проблема:**

Forex и CFD позиции, открытые через rollover time (обычно 22:00 UTC), облагаются swap-платежом — разницей процентных ставок стран валютной пары. Для USDJPY long (высокая USD ставка vs низкая JPY) это **положительный** carry (~+$5/лот/день). Для EURUSD long — небольшой **отрицательный** (-$2/лот/день при дифференциале 1–2%).

Для сделок длительностью >1 дня игнорирование swap создаёт смещение, особенно критичное для H4/D1 сигналов.

**Требование:**

#### 13.1 Таблица ставок swap

Вместо реального получения ставок от брокера (нереализуемо для симулятора) — использовать приближённую таблицу на основе текущих ставок центральных банков. Таблица обновляется при изменении ставок (через `central_bank_rates`).

```python
# Типовые дневные swap-ставки (в пипах за 1 лот, ориентировочно)
# Обновляются вручную или из central_bank_rates при изменении >0.25%
# Положительный = начисляется держателю позиции
SWAP_DAILY_PIPS: dict[str, dict[str, Decimal]] = {
    "EURUSD=X": {"long": Decimal("-0.5"), "short": Decimal("0.3")},
    "USDJPY=X": {"long": Decimal("1.2"),  "short": Decimal("-1.5")},
    "GBPUSD=X": {"long": Decimal("-0.8"), "short": Decimal("0.5")},
    "AUDUSD=X": {"long": Decimal("0.2"),  "short": Decimal("-0.4")},
    "USDCAD=X": {"long": Decimal("0.4"),  "short": Decimal("-0.7")},
    "USDCHF=X": {"long": Decimal("-0.3"), "short": Decimal("0.1")},
    "NZDUSD=X": {"long": Decimal("0.1"),  "short": Decimal("-0.3")},
    # Криптовалюты: funding rate из order_flow_data (8h)
    # Акции: дивиденды не учитываются (вне скоупа симулятора)
}

# Среда (rollover): в среду тройной своп (3 дня: среда + выходные)
TRIPLE_SWAP_WEEKDAY: int = 2  # 0=Mon, 2=Wed

# Rollover time (UTC)
ROLLOVER_HOUR_UTC: int = 22
```

#### 13.2 Логика начисления

Начисляем swap **один раз в сутки** при прохождении rollover time (22:00 UTC):

```python
async def _apply_daily_swap(
    self,
    db: AsyncSession,
    signal: Signal,
    position: VirtualPortfolio,
    instrument: Instrument,
    now: datetime.datetime,
) -> None:
    """
    Начисляем swap если:
    1. Сделка открыта (status = "open" или "partial")
    2. Текущее время >= 22:00 UTC
    3. Сегодня swap ещё не начислялся (last_swap_date != today)
    """
```

#### 13.3 Источник ставки для крипто

Для криптовалюты используется funding rate из `order_flow_data` (начисляется каждые 8 часов):

```python
if instrument.market == "crypto":
    funding_rate = await get_latest_funding_rate(db, instrument.id)  # из order_flow_data
    swap_pct = funding_rate × (-1 if signal.direction == "LONG" else 1)
    # При long: платим funding (если rate > 0 — рынок бычий, лонги платят шортам)
```

#### 13.4 Новые поля в БД

```sql
-- virtual_portfolio: накопленный своп за всё время позиции
ALTER TABLE virtual_portfolio
  ADD COLUMN accrued_swap_pips  NUMERIC(14, 4) DEFAULT 0,
  ADD COLUMN accrued_swap_usd   NUMERIC(14, 4) DEFAULT 0,
  ADD COLUMN last_swap_date     DATE;             -- дата последнего начисления

-- signal_results: итоговый своп в результате сделки
ALTER TABLE signal_results
  ADD COLUMN swap_pips  NUMERIC(14, 4),
  ADD COLUMN swap_usd   NUMERIC(14, 4);
```

#### 13.5 Учёт swap в итоговом P&L

При закрытии сделки:
```python
total_pnl_usd = price_pnl_usd + accrued_swap_usd
```

`price_pnl_usd` и `swap_usd` хранятся отдельно — для анализа: сколько дала цена и сколько carry.

**Acceptance criteria:**
- USDJPY long, открыт в пн 10:00, закрыт в пт 10:00: начислено 4 свопа (вт,ср×3,чт,пт = 6 дневных эквивалентов, если среда = тройной)
- EURUSD short, 1 день: `swap_pips = +0.3` (позитивный для SHORT)
- BTC long при funding_rate = +0.01%: вычтено 3× 0.01% от позиции за 24ч
- Криптовалюта с нулевым funding rate: `swap_pips = 0`

**Скоуп:**
- Форекс: своп из таблицы SWAP_DAILY_PIPS
- Крипто: funding rate из `order_flow_data`
- Акции: вне скоупа (дивиденды не реализуются в симуляторе)

---

### SIM-14: Score → Outcome аналитика

**Проблема:**

Это центральная задача всей системы Market Analyzer Pro: **при каком composite_score сигналы реально прибыльны?** Без этих данных невозможно обоснованно калибровать пороги BUY_THRESHOLD / STRONG_BUY_THRESHOLD.

Пример вопроса: "При score 7–10 (BUY) win rate = 45%, при score 15+ (STRONG_BUY) win rate = 62% — значит, торговать надо только от 15". Без аналитики по диапазонам этот вопрос нельзя ответить.

**Требование:**

#### 14.1 Новый API эндпоинт `/api/v2/simulator/score-analysis`

```python
GET /api/v2/simulator/score-analysis

Response:
{
  "score_buckets": [
    {
      "range_label": "7–10 (BUY)",
      "range_min": 7.0,
      "range_max": 10.0,
      "total": 45,
      "wins": 21,
      "losses": 19,
      "breakevens": 5,
      "win_rate_pct": 46.7,
      "profit_factor": 1.12,
      "avg_pnl_usd": 0.18,
      "avg_pnl_pips": 4.2,
      "avg_duration_minutes": 280,
      "avg_mfe_pips": 18.5,  -- насколько уходила в плюс перед разворотом
      "avg_mae_pips": 12.3   -- насколько уходила в минус перед разворотом
    },
    {
      "range_label": "10–15",
      ...
    },
    {
      "range_label": "15+ (STRONG_BUY)",
      ...
    },
    {
      "range_label": "−10–−7 (SELL)",
      ...
    },
    ...
  ],
  "threshold_recommendations": {
    "current_buy_threshold": 7.0,
    "suggested_min_score_for_positive_edge": 11.5,  -- минимальный score с profit_factor > 1.0
    "score_with_best_win_rate": {"score_min": 15.0, "win_rate": 62.3}
  }
}
```

#### 14.2 Диапазоны score (бакеты)

Фиксированные бакеты, совпадающие с порогами системы:

| Бакет | Диапазон | Интерпретация |
|---|---|---|
| `strong_sell` | ≤ −15 | STRONG_SELL |
| `sell` | −15 .. −10 | Средний SELL |
| `weak_sell` | −10 .. −7 | BUY порог (SELL сторона) |
| `neutral` | −7 .. +7 | HOLD / нет сигнала |
| `weak_buy` | +7 .. +10 | Минимальный BUY |
| `buy` | +10 .. +15 | Средний BUY |
| `strong_buy` | ≥ +15 | STRONG_BUY |

Нейтральная зона (+7 .. +7 и симметрично) в реальности даёт мало сигналов — но если они есть, важно видеть их качество.

#### 14.3 Threshold Recommendations

Система автоматически вычисляет:

```python
# Минимальный score, при котором profit_factor > 1.0
suggested_min = min(
    bucket.range_min
    for bucket in score_buckets
    if bucket.profit_factor and bucket.profit_factor > 1.0
    and bucket.total >= 5  # минимальная выборка
)

# Бакет с лучшим win rate при выборке >= 5 сделок
best_bucket = max(
    (b for b in score_buckets if b.total >= 5),
    key=lambda b: b.win_rate_pct
)
```

#### 14.4 Добавить поле `composite_score` в `signal_results`

Сейчас `composite_score` хранится только в `signals`. При JOIN это доступно, но неудобно. Для эффективных агрегаций добавить денормализованное поле:

```sql
ALTER TABLE signal_results
  ADD COLUMN composite_score NUMERIC(8, 4);
```

Заполняется при `_close_signal()` из `signal.composite_score`.

**Acceptance criteria:**
- После 10+ закрытых сделок эндпоинт возвращает заполненные бакеты
- `threshold_recommendations` пересчитываются при каждом запросе (не кешируются в БД)
- Бакеты с < 3 сделками помечаются `"insufficient_data": true`

---

### SIM-15: Расширенная breakdown-аналитика

**Проблема:**

Текущий `/simulator/stats` возвращает только глобальные метрики (total win rate, PF). Невозможно ответить на вопросы:
- "Какой таймфрейм работает лучше — M15 или H4?"
- "Больше ли profit factor у LONG сигналов, чем у SHORT?"
- "Сколько сделок закрывается по TP2 vs trailing stop?"
- "Как изменялась win rate помесячно?"

**Требование:**

#### 15.1 Новый эндпоинт `/api/v2/simulator/breakdown`

```python
GET /api/v2/simulator/breakdown?by=timeframe|direction|exit_reason|market|month

Response (by=timeframe):
{
  "dimension": "timeframe",
  "rows": [
    {
      "key": "M15",
      "total": 34,
      "wins": 18,
      "losses": 13,
      "breakevens": 3,
      "win_rate_pct": 52.9,
      "profit_factor": 1.38,
      "avg_pnl_usd": 0.42,
      "avg_duration_minutes": 95,
      "avg_composite_score": 11.2
    },
    {
      "key": "H1",
      ...
    },
    {
      "key": "H4",
      ...
    }
  ]
}
```

#### 15.2 Поддерживаемые измерения (параметр `by`)

| `by` | GROUP BY | Ключ строки |
|---|---|---|
| `timeframe` | `signals.timeframe` | "M15", "H1", "H4", "D1" |
| `direction` | `signals.direction` | "LONG", "SHORT" |
| `exit_reason` | `signal_results.exit_reason` | "sl_hit", "tp1_hit", "tp2_hit", "trailing_sl_hit", "expired" |
| `market` | `instruments.market` | "forex", "crypto", "stocks" |
| `month` | `date_trunc('month', exit_at)` | "2026-01", "2026-02" |

#### 15.3 Расчёт profit factor по группе

```python
profit_factor = gross_wins_usd / abs(gross_losses_usd)
# gross_wins_usd = SUM(pnl_usd) WHERE result='win' AND exit_reason != 'cancelled'
# gross_losses_usd = SUM(pnl_usd) WHERE result='loss'
```

#### 15.4 Глобальный `/simulator/stats` — добавить поля

К существующему ответу добавить:

```python
{
  # существующие поля...

  # Новые поля
  "cancelled_count":      12,    # сигналов без входа
  "avg_duration_minutes": 185,   # средняя длительность реальных сделок
  "avg_mfe_pips":         22.4,  # средний MFE (насколько позиция "ходила в плюс")
  "avg_mae_pips":         11.8,  # средний MAE
  "total_swap_usd":       -4.32, # накопленный своп по всем закрытым сделкам
  "best_exit_reason":     "tp2_hit",  # exit_reason с лучшим avg_pnl_usd
}
```

**Acceptance criteria:**
- `by=direction`: видно, что LONG win_rate = 55%, SHORT = 42% → сигнал о bias системы
- `by=exit_reason`: видно, что `trailing_sl_hit` avg_pnl_usd = +$0.15 (оправдывает trailing)
- `by=month`: позволяет отследить деградацию модели со временем

---

---

### SIM-16: Динамический баланс виртуального счёта

**Проблема:**

Сейчас `ACCOUNT_SIZE = Decimal(str(settings.VIRTUAL_ACCOUNT_SIZE_USD))` — константа ($1000). Каждая сделка рассчитывается от одного и того же начального баланса, независимо от предыдущих результатов:

```python
# Текущий код (упрощённо):
pnl_usd = ACCOUNT_SIZE * (size_pct / 100) * (move_pct / 100)
# → всегда 1000 * 0.02 * x, даже если счёт уже вырос до $1150 или упал до $850
```

Следствия:
1. **Position sizing неверный** — при счёте $850 (после серии убытков) система продолжает рисковать той же абсолютной суммой, что нереалистично.
2. **Drawdown невозможно вычислить** — без пикового баланса `drawdown = (peak - current) / peak` не считается.
3. **P&L несопоставим** — результат сделки #50 нельзя сравнить с результатом сделки #1: они считаются с одинакового стартового баланса, а не с того, что был на счёте.
4. **Compounding отсутствует** — долгосрочная кривая доходности не отражает реального роста/падения счёта.

**Требование:**

#### 16.1 Новая таблица `virtual_account`

Хранит текущий баланс и историю изменений (одна строка per account):

```sql
CREATE TABLE virtual_account (
    id                   SERIAL PRIMARY KEY,
    initial_balance      NUMERIC(14, 4) NOT NULL DEFAULT 1000.0,
    current_balance      NUMERIC(14, 4) NOT NULL DEFAULT 1000.0,
    peak_balance         NUMERIC(14, 4) NOT NULL DEFAULT 1000.0,   -- для drawdown
    total_realized_pnl   NUMERIC(14, 4) NOT NULL DEFAULT 0.0,      -- накопленный реализованный P&L
    total_trades         INTEGER        NOT NULL DEFAULT 0,
    updated_at           TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Одна строка — один виртуальный счёт. Инициализируется при первом запуске.
INSERT INTO virtual_account (initial_balance, current_balance, peak_balance)
VALUES (1000.0, 1000.0, 1000.0);
```

#### 16.2 Новое поле `account_balance_at_entry` в `virtual_portfolio`

При открытии позиции снимается текущий баланс счёта. Этот снимок используется для расчёта P&L данной конкретной сделки:

```sql
ALTER TABLE virtual_portfolio
  ADD COLUMN account_balance_at_entry NUMERIC(14, 4);
  -- NULL = позиция открыта до SIM-16, использовать fallback VIRTUAL_ACCOUNT_SIZE_USD
```

#### 16.3 Логика открытия позиции

```python
async def open_position_for_signal(signal, db) -> bool:
    ...
    # Получить текущий баланс счёта
    account = await get_virtual_account(db)
    current_balance = account.current_balance if account else ACCOUNT_SIZE

    await create_virtual_position(db, data={
        ...
        "account_balance_at_entry": current_balance,  # НОВОЕ
    })
```

#### 16.4 Логика расчёта P&L (обновлённая)

```python
def _pnl_usd(
    self,
    pnl_pct: Decimal,
    size_pct: Decimal,
    account_balance: Optional[Decimal] = None,
) -> Decimal:
    """P&L в USD с учётом текущего баланса счёта.

    v3: использует account_balance_at_entry (снимок на момент входа)
    v2 compat: если account_balance=None → ACCOUNT_SIZE ($1000)
    """
    balance = account_balance if account_balance is not None else ACCOUNT_SIZE
    return (balance * size_pct / Decimal("100") * pnl_pct / Decimal("100")).quantize(
        Decimal("0.01")
    )
```

#### 16.5 Обновление баланса при закрытии сделки

При каждом вызове `_close_signal()` — обновлять `virtual_account`:

```python
async def _update_account_balance(db: AsyncSession, realized_pnl_usd: Decimal) -> None:
    """Обновить баланс счёта после закрытия позиции."""
    account = await get_virtual_account(db)
    if account is None:
        return

    new_balance = account.current_balance + realized_pnl_usd
    new_peak    = max(account.peak_balance, new_balance)

    await update_virtual_account(db, {
        "current_balance":    new_balance,
        "peak_balance":       new_peak,
        "total_realized_pnl": account.total_realized_pnl + realized_pnl_usd,
        "total_trades":       account.total_trades + 1,
        "updated_at":         datetime.now(timezone.utc),
    })
```

> **Важно**: при частичном закрытии (SIM-07) `_update_account_balance()` вызывается дважды:
> - первый раз при `partial_close` с `partial_close_pnl_usd` (50% позиции)
> - второй раз при финальном закрытии с оставшимся P&L

#### 16.6 Обновление unrealized P&L (SIM-12) с учётом баланса

```python
# В _update_virtual_unrealized() — использовать account_balance_at_entry позиции
balance = position.account_balance_at_entry or ACCOUNT_SIZE
unrealized_usd = balance * (effective_size / Decimal("100")) * (move_pct / Decimal("100"))
```

#### 16.7 Обновления API

**`/simulator/stats` — добавить поля:**

```python
{
    # ... существующие поля ...

    # SIM-16: баланс счёта
    "account_initial_balance": 1000.00,
    "account_current_balance": 1143.75,   # initial + total_realized_pnl
    "account_peak_balance":    1198.20,
    "account_drawdown_pct":    4.54,       # (peak - current) / peak × 100
    "account_total_return_pct": 14.38,     # (current - initial) / initial × 100
}
```

**`/simulator/open` — обновить `unrealized_pnl_usd`:**

Уже будет корректным после SIM-12 + SIM-16: берётся из `virtual_portfolio.unrealized_pnl_usd`, которое теперь считается от `account_balance_at_entry`.

#### 16.8 CRUD-функции

```python
# src/database/crud.py

async def get_virtual_account(db: AsyncSession) -> Optional[VirtualAccount]:
    """Получить единственную запись виртуального счёта."""

async def update_virtual_account(db: AsyncSession, data: dict) -> None:
    """Обновить поля виртуального счёта."""

async def create_virtual_account_if_not_exists(db: AsyncSession) -> VirtualAccount:
    """Создать счёт с initial_balance = settings.VIRTUAL_ACCOUNT_SIZE_USD если нет."""
```

**Acceptance criteria:**
- Сделка #1: account = $1000, size=2%, SL = -$4 → account после = $996
- Сделка #2: account = $996, size=2%, TP1 = +$10 → account после = $1006
- `drawdown_pct` = (1006 - 996) / 1006 → 0% (текущий баланс выше предыдущего минимума — нет drawdown); если бы упал до $920 при пике $1006 → drawdown = (1006-920)/1006 = 8.55%
- Старые позиции (`account_balance_at_entry = NULL`) → fallback к `VIRTUAL_ACCOUNT_SIZE_USD` без ошибок
- При пересчёте через `/simulator/stats` — `total_realized_pnl` совпадает с суммой `signal_results.pnl_usd`

---

## 4. Изменения схемы БД (сводка)

Все изменения — **одна Alembic-миграция** `xxxx_simulator_v3.py`:

```python
# signal_results
op.add_column("signal_results", sa.Column("candle_high_at_exit",  Numeric(18, 8), nullable=True))
op.add_column("signal_results", sa.Column("candle_low_at_exit",   Numeric(18, 8), nullable=True))
op.add_column("signal_results", sa.Column("exit_slippage_pips",   Numeric(8, 4),  nullable=True))
op.add_column("signal_results", sa.Column("swap_pips",            Numeric(14, 4), nullable=True))
op.add_column("signal_results", sa.Column("swap_usd",             Numeric(14, 4), nullable=True))
op.add_column("signal_results", sa.Column("composite_score",      Numeric(8, 4),  nullable=True))

# virtual_portfolio
op.add_column("virtual_portfolio", sa.Column("unrealized_pnl_usd", Numeric(14, 4), nullable=True))
op.add_column("virtual_portfolio", sa.Column("accrued_swap_pips",  Numeric(14, 4), server_default="0"))
op.add_column("virtual_portfolio", sa.Column("accrued_swap_usd",   Numeric(14, 4), server_default="0"))
op.add_column("virtual_portfolio", sa.Column("last_swap_date",         sa.Date,        nullable=True))
op.add_column("virtual_portfolio", sa.Column("account_balance_at_entry", Numeric(14, 4), nullable=True))

# virtual_account (новая таблица)
op.create_table(
    "virtual_account",
    sa.Column("id",                 sa.Integer,     primary_key=True),
    sa.Column("initial_balance",    Numeric(14, 4), nullable=False, server_default="1000.0"),
    sa.Column("current_balance",    Numeric(14, 4), nullable=False, server_default="1000.0"),
    sa.Column("peak_balance",       Numeric(14, 4), nullable=False, server_default="1000.0"),
    sa.Column("total_realized_pnl", Numeric(14, 4), nullable=False, server_default="0.0"),
    sa.Column("total_trades",       sa.Integer,     nullable=False, server_default="0"),
    sa.Column("updated_at",         sa.DateTime(timezone=True), server_default=sa.func.now()),
)
# Инициализировать начальную запись счёта
op.execute("INSERT INTO virtual_account (initial_balance, current_balance, peak_balance) VALUES (1000.0, 1000.0, 1000.0)")
```

Итого: 6 новых колонок в `signal_results`, 5 в `virtual_portfolio`, 1 новая таблица `virtual_account`.

---

## 5. Изменения файлов (сводка)

| Файл | Тип | Описание |
|------|-----|----------|
| `src/tracker/signal_tracker.py` | Изменение | SIM-09, SIM-10, SIM-11, SIM-12, SIM-13 |
| `src/api/routes_v2.py` | Изменение | SIM-14, SIM-15 + обновление stats |
| `src/database/models.py` | Изменение | Новые поля VirtualPortfolio, SignalResult |
| `src/database/crud.py` | Изменение | `get_latest_funding_rate()`, `get_virtual_account()`, `update_virtual_account()`, вспомогательные запросы для аналитики |
| `alembic/versions/` | Новый файл | `xxxx_simulator_v3.py` |
| `tests/test_simulator_v3.py` | Новый файл | Тесты для всех SIM-09..SIM-16 |

---

## 6. Тестирование

### 6.1 Unit тесты (`tests/test_simulator_v3.py`)

| Тест | Проверяет |
|------|-----------|
| `test_sl_via_candle_low` | LONG SL: last > SL, но candle_low < SL → sl_hit |
| `test_tp_via_candle_high` | LONG TP1: last < TP1, но candle_high > TP1 → tp1_hit |
| `test_both_pips_worst_case` | Гэп пробил SL и TP → sl_hit (worst case) |
| `test_sl_slippage_forex` | SL LONG EURUSD: exit = sl - 1 пип |
| `test_sl_slippage_crypto` | SL LONG BTC: exit = sl × (1 - 0.001) |
| `test_tp_no_slippage` | TP hit: exit = точно tp, без slippage |
| `test_live_atr_calculation` | 15 свечей → ATR(14) по формуле Wilder |
| `test_atr_fallback_chain` | Нет данных → snapshot → 14×pip_size |
| `test_unrealized_usd_with_size` | size=2%, move=+1.5% → usd=+$0.30 |
| `test_unrealized_after_partial` | size=2%, remaining=0.5, move=+2% → usd=+$0.20 |
| `test_swap_wednesday_triple` | Rollover Wed: начислено 3× |
| `test_swap_positive_carry` | USDJPY long: swap_pips > 0 |
| `test_swap_crypto_funding` | BTC long, funding=+0.01% → вычтен |
| `test_score_buckets_assignment` | composite=8.5 → бакет "7–10 (BUY)" |
| `test_threshold_recommendation` | Бакет с PF>1.0 определяет suggested_min |
| `test_account_balance_updates_on_close` | Закрытие убыточной сделки уменьшает `current_balance` |
| `test_account_balance_compounds` | Две последовательные сделки — баланс накапливается корректно |
| `test_position_sizing_from_balance` | Сделка при $900 счёте: pnl_usd считается от $900, не $1000 |
| `test_drawdown_calculation` | peak=$1100, current=$950 → drawdown=13.64% |
| `test_partial_close_updates_balance_twice` | При TP1 и финальном закрытии — два обновления баланса |
| `test_legacy_position_fallback` | `account_balance_at_entry=NULL` → fallback к VIRTUAL_ACCOUNT_SIZE_USD |
| `test_account_initialized_on_first_run` | `create_virtual_account_if_not_exists()` создаёт запись если нет |

### 6.2 Регрессионные тесты

- Старые записи без `candle_high_at_exit` → graceful NULL, логика не падает
- `composite_score = NULL` в signal_results → не включается в score_analysis, не вызывает ошибку

---

## 7. Backward Compatibility

- Все новые поля nullable или со значением по умолчанию — миграция не сломает существующие записи
- `unrealized_pnl_usd = NULL` для старых открытых позиций → API возвращает 0, вычисление продолжается через pnl_pct
- `composite_score = NULL` в signal_results → бакет "unknown" в score_analysis или пропускается
- Новые эндпоинты (`/score-analysis`, `/breakdown`) — аддитивные, старые не меняются
- Slippage применяется только к новым сделкам, старые `signal_results` не пересчитываются

---

## 8. Приоритеты и последовательность реализации

```
Фаза 1 — Точность данных (P0):
  1. Alembic миграция (все новые поля + таблица virtual_account)
  2. SIM-16: Динамический баланс — основа корректного position sizing (должен быть первым,
             т.к. влияет на все последующие расчёты pnl_usd)
  3. SIM-09: High/Low SL/TP (candle prices) — исправляет пропущенные выходы
  4. SIM-10: Slippage при SL exit — исправляет завышенный P&L
  5. SIM-12: Unrealized P&L с position sizing + account_balance_at_entry

Фаза 2 — Качество модели (P1):
  6. SIM-11: Живой ATR для trailing — улучшает trailing stop
  7. SIM-14: Score → Outcome эндпоинт — ключевая аналитика
  8. SIM-15: Breakdown эндпоинт + расширение stats (включая drawdown/return из SIM-16)

Фаза 3 — Полнота модели (P2):
  9. SIM-13: Overnight swap
  10. Тесты
```

---

## 9. Открытые вопросы к обсуждению

1. **SIM-09, worst-case при гэпе**: использовать `sl_hit` при одновременном пробое SL и TP — это консервативный подход. Альтернатива: `tp_hit` (оптимистичный). Какой подход предпочесть для оценки системы?

2. **SIM-10, slippage в новостях**: текущие константы (`1 пип`) отражают нормальные условия. Добавить ли умножающий коэффициент при наличии HIGH-impact события в экономическом календаре в момент выхода? Это потребует JOIN с `economic_events` при закрытии.

3. **SIM-13, скоуп свопа**: только форекс и крипто, или добавить CFD на акции (синтетический дивиденд/financing)? Для текущего состояния системы акции в симуляторе, вероятно, второстепенны.

4. **SIM-14, минимальная выборка**: рекомендовать threshold только при ≥ 5 сделках в бакете. Нужно ли это число настраиваемым через query parameter?

5. **Фронтенд**: визуализация score-analysis и breakdown — добавить ли новые страницы/секции, или вывести в существующий `/accuracy`?

6. **SIM-16, сброс счёта**: предусмотреть ли API-эндпоинт `POST /simulator/account/reset` для сброса `current_balance` к `initial_balance` (полезно при переходе на новую версию системы или при ручном тестировании)? Или достаточно прямого обновления в БД?

7. **SIM-16, конкурентный доступ**: при параллельном закрытии нескольких позиций (в рамках одного тика симулятора) необходима ли оптимистичная блокировка (`SELECT ... FOR UPDATE`) при обновлении `virtual_account.current_balance`? Без неё возможны race conditions в будущем при горизонтальном масштабировании.
