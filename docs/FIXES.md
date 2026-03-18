# Trade Logic Fixes — Trader Review

> Основано на анализе логики с точки зрения трейдера (2026-03-18).
> Порядок: P0 (критические баги) → P1 (некорректная логика) → P2 (улучшения).
> После каждой задачи — тесты, отметка `[x]`.

---

## P0 — Критические баги

### FIX-01: Нормировка magnitude в расчёте confidence

**Файл:** `src/signals/signal_engine_v2.py` → `_calculate_confidence()`

**Проблема:** `magnitude = abs(composite) / 100.0` — composite max ≈ 25, поэтому
magnitude всегда ≤ 0.25 (вклад в confidence не более 10 из 40). Величина сигнала
фактически не влияет на confidence. Сигнал с composite=7 и composite=25 имеют
почти одинаковую confidence при высоком alignment.

**Фикс:**
```python
# Было:
magnitude = min(abs(composite) / 100.0, 1.0)
# Стало:
magnitude = min(abs(composite) / 25.0, 1.0)  # 25 = реальный max composite
```

- [ ] Поправить формулу в `_calculate_confidence()`
- [ ] Тест: `composite=7.0` → magnitude=0.28 (не 0.07)
- [ ] Тест: `composite=25.0` → magnitude=1.0 (max)
- [ ] Тест: слабый сигнал (composite=7) имеет заметно меньший confidence чем сильный (composite=20)

---

### FIX-02: TTL сигналов не зависит от таймфрейма

**Файл:** `src/signals/signal_engine.py` → генерация сигнала (строка ~588)
**Конфиг:** `src/config.py` → `SIGNAL_EXPIRY_HOURS = 168` (7 дней плоско)

**Проблема:** H1-сигнал ждёт заполнения 7 дней — setup давно устарел. W1/MN1
с 7-дневным TTL — разумно, но H1/H4 должны истекать за часы, не за неделю.
Результат: 92% сигналов (459 из 495) отменены.

**Фикс:** TTL = N × длина_свечи в часах.
```python
TF_EXPIRY_CANDLES = {
    "M1": 60, "M5": 48, "M15": 32,
    "H1": 24, "H4": 20, "D1": 10,
    "W1": 8,  "MN1": 3,
}
# expires_at = now + timedelta(hours = TF_CANDLE_HOURS[tf] * TF_EXPIRY_CANDLES[tf])
```

- [ ] Добавить `TF_EXPIRY_CANDLES` и `TF_CANDLE_HOURS` в `signal_engine.py`
- [ ] Убрать `SIGNAL_EXPIRY_HOURS` из `config.py` (или оставить как fallback)
- [ ] Применить `_calculate_expiry(timeframe)` при создании сигнала
- [ ] Тест: H1 → TTL = 24 часа
- [ ] Тест: D1 → TTL = 10 дней
- [ ] Тест: W1 → TTL = 56 дней (8 × 7 дней)
- [ ] Тест: неизвестный TF → fallback к SIGNAL_EXPIRY_HOURS

---

### FIX-03: size_pct в virtual_portfolio не берётся из signal.position_size_pct

**Файл:** `src/tracker/signal_tracker.py` → `_open_virtual_position()`
**Файл:** `src/tracker/trade_simulator.py` → `open_position_for_signal()`

**Проблема:** Все 54 позиции в БД имеют `size_pct = 1.0`, при этом
`signals.position_size_pct = 20.0`. P&L считается от 1.0% вместо 20.0% —
занижен в 20 раз. Исторические позиции требуют backfill.

**Диагностика:** Старый код (до v2) хардкодил `size_pct = 1.0`. Текущий код
(`open_position_for_signal`) уже корректен, но исторические записи не обновлены.

**Фикс:**
```sql
UPDATE virtual_portfolio vp
SET size_pct = s.position_size_pct
FROM signals s
WHERE vp.signal_id = s.id
  AND vp.size_pct = 1.0
  AND s.position_size_pct IS NOT NULL
  AND s.position_size_pct != 1.0;
```

- [ ] Проверить текущий код — убедиться что `signal.position_size_pct` корректно сохраняется
- [ ] Написать одноразовый скрипт backfill `scripts/backfill_position_size.py`
- [ ] Тест: новая позиция сохраняет `size_pct = signal.position_size_pct`
- [ ] Тест: backfill меняет только записи с `size_pct = 1.0` и отличным `position_size_pct`

---

## P1 — Некорректная логика

### FIX-04: Partial close срабатывает на 95% TP1, а не на TP1

**Файл:** `src/signals/trade_lifecycle.py` → `_check_partial_close()`

**Проблема:** `threshold_frac = Decimal("0.95")` — закрываем 50% за 5% до TP1.
Трейдер теряет часть прибыли на каждой выигрышной сделке. Стандарт — закрыть
50% ровно на TP1, остаток тянуть трейлингом.

**Фикс:**
```python
threshold_frac = Decimal("1.0")  # ровно на TP1
```

- [ ] Изменить `threshold_frac` с `0.95` на `1.0` в `_check_partial_close()`
- [ ] Тест: LONG, TP1 = 1.11, price = 1.1099 → нет partial close
- [ ] Тест: LONG, TP1 = 1.11, price = 1.11 → partial close срабатывает
- [ ] Тест: SHORT, TP1 = 1.09, price = 1.09 → partial close срабатывает

---

### FIX-05: TP3 нереалистичен для RANGING режима

**Файл:** `src/signals/risk_manager_v2.py` → `calculate_levels_for_regime()`

**Проблема:** В RANGING режиме `TP2_RR = 2.5`, `TP3_RR = 2.5 × 1.5 = 3.75`.
В боковом рынке 3.75R — нереалистичная цель. TP3 должен быть None для RANGING
и HIGH_VOLATILITY режимов.

**Фикс:**
```python
NO_TP3_REGIMES = {"RANGING", "HIGH_VOLATILITY"}

tp3 = None
if regime not in NO_TP3_REGIMES:
    tp3 = entry ± sl_dist × (REGIME_TP2_RR[regime] * Decimal("1.5"))
```

- [ ] Добавить `NO_TP3_REGIMES` в `risk_manager_v2.py`
- [ ] Обновить `calculate_levels_for_regime()` — TP3 = None для этих режимов
- [ ] Тест: RANGING → TP3 = None
- [ ] Тест: HIGH_VOLATILITY → TP3 = None
- [ ] Тест: STRONG_TREND_BULL → TP3 рассчитывается корректно

---

### FIX-06: Trailing stop слишком тугой — мультипликатор ATR занижен

**Файл:** `src/signals/trade_lifecycle.py` → константы `_TRAIL_ATR_TREND` / `_TRAIL_ATR_RANGE`

**Проблема:** В тренде нормальный pullback = 0.5–1.0×ATR. При trailing = 0.5×ATR
позиция закрывается на каждом нормальном откате. Стандарт для тренда — 1.0–1.5×ATR.

**Фикс:**
```python
_TRAIL_ATR_TREND = Decimal("1.0")   # было 0.5
_TRAIL_ATR_RANGE = Decimal("0.5")   # было 0.3
```

- [ ] Обновить константы в `trade_lifecycle.py`
- [ ] Тест: STRONG_TREND_BULL, price = entry + 2×ATR → trail = price - 1.0×ATR
- [ ] Тест: RANGING, price = entry + 1×ATR → trail = price - 0.5×ATR
- [ ] Тест: trailing stop не уменьшается при откате (только двигается в прибыльную сторону)

---

## P2 — Улучшения качества сигналов

### FIX-07: Сессионный фильтр для форекс-сигналов

**Файл:** `src/signals/signal_engine.py` → перед генерацией сигнала

**Проблема:** EUR/GBP/CHF сигналы генерируются во время Asian сессии (00:00–07:00 UTC),
когда спред шире и ATR, рассчитанный на дневных данных, завышен относительно
ликвидности текущей сессии. Это приводит к нереалистичным SL/TP уровням.

**Фикс:** Фильтр только для European/North American пар (не для JPY, AUD, NZD).
```python
FOREX_PAIRS_EU_NA = {"EURUSD=X", "GBPUSD=X", "USDCHF=X", "EURGBP=X"}
ASIAN_SESSION_UTC = (0, 7)  # часы: 00:00–07:00 UTC

def _is_low_liquidity_session(symbol: str, market: str, now_utc: datetime) -> bool:
    if market != "forex":
        return False
    if symbol not in FOREX_PAIRS_EU_NA:
        return False
    return ASIAN_SESSION_UTC[0] <= now_utc.hour < ASIAN_SESSION_UTC[1]
```

- [ ] Добавить `FOREX_PAIRS_EU_NA` и `_is_low_liquidity_session()` в `signal_engine.py`
- [ ] Интегрировать фильтр в `generate()` — ранний выход с логированием
- [ ] Тест: EURUSD, 03:00 UTC → сигнал не генерируется
- [ ] Тест: USDJPY, 03:00 UTC → сигнал генерируется (JPY = Asian pair)
- [ ] Тест: EURUSD, 10:00 UTC → сигнал генерируется

---

## Прогресс

| Задача | Статус | Файл |
|--------|--------|------|
| FIX-01: magnitude нормировка | [x] | signal_engine_v2.py |
| FIX-02: TTL по таймфрейму | [x] | signal_engine.py, config.py |
| FIX-03: size_pct backfill | [x] | signal_tracker.py + скрипт |
| FIX-04: partial close на TP1 | [x] | trade_lifecycle.py |
| FIX-05: TP3 = None для ranging | [x] | risk_manager_v2.py |
| FIX-06: trailing stop ATR ×2 | [x] | trade_lifecycle.py |
| FIX-07: сессионный фильтр | [x] | signal_engine.py |
