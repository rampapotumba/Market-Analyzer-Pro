# Trade Simulator v3 — Архитектура изменений

## Схема изменений БД

```
┌─────────────────────────────────┐
│       virtual_account (NEW)     │
├─────────────────────────────────┤
│ id                  SERIAL PK   │
│ initial_balance     NUM(14,4)   │
│ current_balance     NUM(14,4)   │  ← обновляется при каждом закрытии
│ peak_balance        NUM(14,4)   │  ← max(peak, current) для drawdown
│ total_realized_pnl  NUM(14,4)   │
│ total_trades        INT         │
│ updated_at          TIMESTAMPTZ │
└─────────────────────────────────┘
         │
         │ current_balance (при открытии)
         ▼
┌─────────────────────────────────────┐
│     virtual_portfolio (+ 5 полей)   │
├─────────────────────────────────────┤
│ ... existing fields ...             │
│ + unrealized_pnl_usd   NUM(14,4)   │  ← SIM-12: USD P&L с position sizing
│ + accrued_swap_pips     NUM(14,4)   │  ← SIM-13: накопленный swap
│ + accrued_swap_usd      NUM(14,4)   │
│ + last_swap_date        DATE        │
│ + account_balance_at_entry NUM(14,4)│  ← SIM-16: снимок баланса при входе
└─────────────────────────────────────┘
         │
         │ при закрытии (_close_signal)
         ▼
┌─────────────────────────────────────┐
│     signal_results (+ 6 полей)      │
├─────────────────────────────────────┤
│ ... existing fields ...             │
│ + candle_high_at_exit  NUM(18,8)    │  ← SIM-09: аудит candle data
│ + candle_low_at_exit   NUM(18,8)    │
│ + exit_slippage_pips   NUM(8,4)     │  ← SIM-10: slippage аудит
│ + swap_pips            NUM(14,4)    │  ← SIM-13: итоговый swap
│ + swap_usd             NUM(14,4)    │
│ + composite_score      NUM(8,4)     │  ← SIM-14: денормализация для аналитики
└─────────────────────────────────────┘
```

## Поток данных — тик симулятора

```
Тик (каждая минута)
    │
    ├─ 1. _get_candle_prices(db, instrument_id, timeframe)
    │      → (last_close, candle_high, candle_low)          [SIM-09]
    │
    ├─ 2. Проверка SL/TP по High/Low + worst case          [SIM-09]
    │      sl_hit = current <= SL OR candle_low <= SL (LONG)
    │      tp_hit = current >= TP OR candle_high >= TP (LONG)
    │      both_hit → sl_hit (worst case)
    │
    ├─ 3. Если SL hit → _apply_sl_slippage()               [SIM-10]
    │      exit_price = SL ± slip (ухудшение)
    │
    ├─ 4. _get_live_atr() для trailing stop                 [SIM-11]
    │      fallback: live TF → live H1 → snapshot → 14×pip
    │
    ├─ 5. _update_virtual_unrealized()                      [SIM-12]
    │      effective_size × balance → unrealized_usd
    │
    ├─ 6. _apply_daily_swap() (если rollover time)          [SIM-13]
    │      forex: table | crypto: funding_rate
    │
    └─ 7. Если exit:
           ├─ _close_signal()                               
           │   ├─ total_pnl = price_pnl + swap              [SIM-13]
           │   ├─ composite_score → signal_results           [SIM-14]
           │   └─ candle_high/low_at_exit → signal_results   [SIM-09]
           │
           └─ _update_account_balance(realized_pnl)          [SIM-16]
               ├─ current_balance += pnl
               └─ peak_balance = max(peak, current)
```

## Новые API эндпоинты

```
GET /api/v2/simulator/score-analysis     [SIM-14]
    → score_buckets[] + threshold_recommendations

GET /api/v2/simulator/breakdown?by=...   [SIM-15]
    → dimension + rows[] (по TF, direction, exit_reason, market, month)

GET /api/v2/simulator/stats              [обновлён]
    → + account_* поля (SIM-16)
    → + avg_mfe/mae, total_swap, best_exit_reason (SIM-15)
```

## Зависимости между SIM задачами

```
SIM-16 (баланс) ─────┐
                      ├─→ SIM-12 (unrealized с balance)
                      ├─→ SIM-09 (использует exit_price для pnl)
                      └─→ SIM-10 (slippage влияет на pnl → на balance)

SIM-09 (High/Low) ───→ SIM-10 (slippage после определения exit)

SIM-13 (swap) ────────→ total_pnl_usd при закрытии

SIM-14 (score analysis) ←── SIM-14.4 (composite_score в signal_results)
SIM-15 (breakdown) ←── все закрытые сделки с корректными данными
```
