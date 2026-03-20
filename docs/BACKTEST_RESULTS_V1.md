# Backtest Results — Baseline (v4, все v5-фильтры выключены)

**Дата запуска:** 2026-03-19
**Run ID:** `0957a6ec-ffe1-44bc-ac15-248611a5b93f`
**Период:** 2024-01-01 — 2025-12-31
**Инструменты:** EURUSD=X, GBPUSD=X, AUDUSD=X, BTC/USDT, ETH/USDT, SPY
**Таймфрейм:** H1 | **Счёт:** $1 000 | **Slippage:** ✓ | **Swap:** ✓
**v5-фильтры:** все выключены (apply_ranging_filter=false, apply_d1_trend_filter=false, apply_volume_filter=false, apply_weekday_filter=false, apply_momentum_filter=false, apply_calendar_filter=false)

---

## Ключевые метрики

| Метрика | Значение |
|---------|---------|
| Total trades | **33** |
| Trades/month | ~1.4 |
| Win rate | **45.45%** |
| Profit factor | **2.0118** |
| Total PnL | **+$253.73 (+25.4%)** |
| Max drawdown | **10.25%** |
| Avg duration | 4 034 мин (~2.8 дня) |
| LONG count | 14 (42.4%) |
| SHORT count | 19 (57.6%) |
| SL hits | 18 |
| TP hits | 15 |
| MAE exits | 0 |
| Time exits | 0 |

## По направлению

| Направление | Win rate |
|-------------|---------|
| LONG | 57.14% |
| SHORT | 36.84% |

## По инструментам

| Символ | Trades | Wins | PnL USD |
|--------|--------|------|---------|
| EURUSD=X | 17 | 6 (35%) | +$29.60 |
| AUDUSD=X | 9 | 7 (78%) | +$101.69 |
| ETH/USDT | 1 | 1 (100%) | +$236.80 |
| SPY | 6 | 1 (17%) | -$114.36 |
| GBPUSD=X | 0 | — | — |
| BTC/USDT | 0 | — | — |

## По score bucket

| Bucket | Trades | Wins | PnL USD |
|--------|--------|------|---------|
| strong_buy (≥+15) | 14 | 8 (57%) | +$325.90 |
| strong_sell (≤-15) | 19 | 7 (37%) | -$72.18 |

## По дням недели

| День | Trades | Wins | PnL USD |
|------|--------|------|---------|
| Вт (2) | 10 | 7 (70%) | +$319.83 |
| Чт (3) | 7 | 3 (43%) | +$23.36 |
| Вт (1) | 2 | 1 (50%) | +$9.57 |
| Пн (0) | 7 | 2 (29%) | -$37.76 |
| Пт (4) | 7 | 2 (29%) | -$61.27 |

## По win/loss duration

| | Avg duration |
|---|---|
| Winning trades | 6 132 мин (~4.3 дня) |
| Losing trades | 2 287 мин (~1.6 дня) |

## Наблюдения

- **Очень мало сделок**: 33 за 2 года (~1.4/мес) — сигнальный движок уже очень избирателен на уровне v4
- **SHORT bias проблема**: SHORT WR 36.84% vs LONG WR 57.14% — крупный дисбаланс
- **SPY убыточен**: 6 сделок, 1 победа, -$114
- **Пятница и понедельник убыточны** — weekday filter должен помочь
- **by_regime = UNKNOWN**: режим не записывается в backtest trades (техдолг)
- **avg_mae_pct_of_sl = 1.44** — MAE early exit не срабатывает (нет сделок с 60%+ пути к SL)
