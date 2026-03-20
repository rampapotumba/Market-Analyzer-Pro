# Requirements: v6 Backtest Calibration

## Date: 2026-03-20
## From: market-analyst
## To: architect

---

## Background

v6 backtest (run_id: `62782bc3-0dea-42f1-afed-ee2a295aecd7`) was analyzed across 10 instruments over 24 months (2024-01 to 2025-12). The proportional threshold scaling (15 * 0.45 = 6.75) increased trade count from 33 to 700 but caused Win Rate to collapse from 45.5% to 14.5% and Max Drawdown to spike to 51.6%. The system remains profitable (+$1,766) only due to outsized gains in GC=F and ETH/USDT. The analysis identified 8 findings, of which 2 are critical and 4 are major.

Full analysis: `analyst-reports/v6-backtest-calibration-analysis.md`

---

## Requirements

### REQ-V6-CAL-001: Raise Effective Score Threshold

- **Priority:** Critical
- **Problem:** Effective threshold 6.75 (= 15 * 0.45) lets through too many low-conviction signals. 85.5% of entries are false positives (WR 14.5%). In v5 with threshold 15 (no scaling), WR was 45.5%.
- **Required behavior:** Increase the effective threshold to produce WR >= 30% and PF >= 1.3. Two approaches to evaluate:
  - **(A) Raise global MIN_COMPOSITE_SCORE to 22** so that effective_threshold = 22 * 0.45 = 9.9. This should reduce trade count by ~40-50% while filtering the weakest signals.
  - **(B) Raise the scaling factor from 0.45 to 0.65** so that effective_threshold = 15 * 0.65 = 9.75. This acknowledges that TA alone is more reliable than 45% weight implies.
  - **(C) Use a non-linear scaling** -- `effective_threshold = threshold * max(available_weight, 0.65)` -- floor the scaling at 0.65 to prevent over-dilution.
- **Recommended values:** Start with approach (C): `effective_threshold = threshold * max(available_weight, 0.65)`, producing threshold 9.75 for backtest. Run comparative backtest with thresholds 8.0, 9.0, 9.75, 11.0 to find optimal point.
- **Acceptance criteria:**
  - WR >= 25%
  - PF >= 1.3
  - Max Drawdown <= 30%
  - Trades >= 100 over 24 months (>= 4/month)
- **Data reference:** `filter_stats.rejected_by_score_threshold = 13,542` (60.7% of raw signals already rejected at 6.75)

---

### REQ-V6-CAL-002: Block or Restrict Bear Regime Trades

- **Priority:** Critical
- **Problem:** 219 trades in TREND_BEAR + STRONG_TREND_BEAR produce -$922 PnL at 11.0% WR. Bear regime trades destroy 52% of the system's gross profit.
- **Required behavior:** Add TREND_BEAR and STRONG_TREND_BEAR to `BLOCKED_REGIMES` or implement directional filtering:
  - **(A) Full block:** `BLOCKED_REGIMES = ["RANGING", "TREND_BEAR", "STRONG_TREND_BEAR"]` -- simplest, removes 219 trades and -$922
  - **(B) Directional filter:** In bear regimes, allow only SHORT (not LONG). In bull regimes, allow only LONG (not SHORT). This is logically sounder -- bearish regime + SHORT = aligned. Problem is that current SHORT signals are also poor in bear regimes.
  - **(C) Restrict bear to STRONG signals only:** In bear regimes, require |composite| >= 2x threshold (i.e., only very strong conviction)
- **Recommended approach:** Start with (A) as baseline. If trade count drops too much, try (C).
- **Acceptance criteria:**
  - Bear regime PnL >= $0 (breakeven or better)
  - Total trades remain >= 100/24mo
  - Overall PF improvement >= 0.2 (from 1.12 to >= 1.32)
- **Data reference:** `by_regime`: STRONG_TREND_BEAR 139 trades, 10.8% WR, -$601; TREND_BEAR 80 trades, 11.2% WR, -$321

---

### REQ-V6-CAL-003: Reduce Time Exit Dominance

- **Priority:** High
- **Problem:** 431 of 700 trades (61.6%) exit via time_exit (48 H1 candles = 2 days). These trades sit for 2 days, never reach SL or TP, and close at a loss. Average trade duration is 9,657 minutes (6.7 days overall, implying TP-hit trades take much longer).
- **Required behavior:** Two complementary changes:
  - **(A) Shorten time_exit window:** Reduce from 48 to 24 H1 candles (1 day). If a signal hasn't moved in its direction within 1 day, it's unlikely to reach TP. This will convert some time_exits to earlier exits, reducing capital lockup.
  - **(B) Tighten TP targets:** Reduce R:R ratio for H1 timeframe. Current dynamic R:R (REGIME_RR_MAP) may produce TPs too far from entry for 2-day horizon. Consider R:R max 1.5 for H1 signals.
  - **(C) Allow time_exit for positive PnL too:** Current condition is `unrealized_pnl <= 0`. Consider changing to `unrealized_pnl < 0.5 * tp_distance` -- exit if after N candles, price hasn't reached halfway to TP.
- **Recommended approach:** Implement (A) first (24 candles). Backtest. If insufficient, add (B).
- **Acceptance criteria:**
  - time_exit percentage < 40% of all exits
  - Average trade duration < 5,000 minutes
  - TP hit rate >= 20% of total exits
- **Data reference:** `time_exit_count = 431 (61.6%)`, `avg_duration_minutes = 9,657`

---

### REQ-V6-CAL-004: Restrict or Disable SHORT Signals

- **Priority:** High
- **Problem:** SHORT WR 12.04%, "sell" score bucket loses -$1,202 (68% of total gross losses). SHORT_SCORE_MULTIPLIER=1.2 and SHORT_RSI_THRESHOLD=40 are insufficient.
- **Required behavior:** Evaluate three options:
  - **(A) LONG-only mode:** Completely disable SHORT signals. This removes 302 trades but eliminates the -$1,202 "sell" bucket loss.
  - **(B) Aggressive SHORT multiplier:** Increase SHORT_SCORE_MULTIPLIER from 1.2 to 2.0 and SHORT_RSI_THRESHOLD from 40 to 30. This means SHORT signals need 2x the score and RSI < 30 (deeply oversold conditions).
  - **(C) SHORT only in STRONG_TREND_BEAR + VOLATILE:** Allow SHORT only when regime indicates strong bearish momentum or high volatility. Block SHORT in bull/neutral regimes.
- **Recommended approach:** (B) first -- aggressive filtering. If WR still < 20%, fall back to (A).
- **Acceptance criteria:**
  - SHORT WR >= 20% OR SHORT disabled
  - "sell" bucket PnL >= -$200
  - Overall PF >= 1.2
- **Data reference:** `win_rate_short_pct = 12.04%`, `by_score_bucket["sell"] = -$1,202.78`

---

### REQ-V6-CAL-005: Restore BTC/USDT Restrictions

- **Priority:** High
- **Problem:** BTC/USDT min_score lowered from 20 to 15 and regimes expanded from [STRONG_TREND] to [STRONG_TREND, TREND]. Result: 102 trades at 9.8% WR, -$135 PnL -- worst WR of all instruments.
- **Required behavior:** Revert BTC/USDT to stricter settings:
  ```python
  "BTC/USDT": {
      "sl_atr_multiplier": 3.5,
      "min_composite_score": 25,  # raised from 15 back to 25 (even stricter than v5's 20)
      "allowed_regimes": ["STRONG_TREND_BULL", "STRONG_TREND_BEAR"],  # remove TREND
  }
  ```
- **Acceptance criteria:**
  - BTC/USDT WR >= 20%
  - BTC/USDT PnL >= $0
  - BTC/USDT trades >= 10 over 24 months
- **Data reference:** `by_symbol["BTC/USDT"]: 102 trades, 9.8% WR, -$135.09`

---

### REQ-V6-CAL-006: Score Bucket Reporting Consistency

- **Priority:** Medium
- **Problem:** `_score_bucket()` in backtest_engine.py uses unscaled thresholds (7/10/15) while filter pipeline uses scaled thresholds (3.15/4.5/6.75 at scale=0.45). A composite=8 signal passes as STRONG_BUY but reports as weak_buy. This makes bucket analysis misleading.
- **Required behavior:** Add a second set of score buckets to the summary using scaled thresholds:
  ```python
  "by_score_bucket_scaled": { ... }  # uses thresholds * available_weight
  ```
  Keep `by_score_bucket` as-is for backward compatibility, but add `by_score_bucket_scaled` for accurate v6+ analysis.
- **Acceptance criteria:**
  - Summary includes both `by_score_bucket` and `by_score_bucket_scaled`
  - Scaled buckets should NOT contain "weak_buy", "weak_sell", or "neutral" (since those are filtered out by the pipeline)
- **Data reference:** `_score_bucket()` at backtest_engine.py:342-358, filter pipeline `_get_signal_strength_scaled()` at filter_pipeline.py:62-84

---

### REQ-V6-CAL-007: Investigate avg_mae_pct_of_sl Anomaly

- **Priority:** Medium
- **Problem:** `avg_mae_pct_of_sl = 285.6%` means average adverse excursion is 2.85x the SL distance. In v5 this was 1.44%. Either the metric calculation changed, MAE is cumulative rather than peak, or SL placement is far too tight at the lower threshold level.
- **Required behavior:**
  1. Verify MAE calculation in backtest_engine.py: is it tracking peak adverse excursion or cumulative?
  2. If metric is correct, SL placement needs review -- 285% of SL distance means price regularly blows through SL level. But only 65 SL hits (9.3%) means SL is somehow NOT being triggered when MAE exceeds 100%. This is contradictory unless MAE is being computed differently.
  3. Add `avg_mae_pct_of_sl_tp_trades` and `avg_mae_pct_of_sl_sl_trades` to distinguish MAE for winners vs losers.
- **Acceptance criteria:**
  - MAE metric methodology documented
  - If SL is too tight: adjust SL multiplier for low-threshold signals
  - If metric is wrong: fix the calculation
- **Data reference:** `avg_mae_pct_of_sl = 285.6` (v6) vs `1.44` (v5)

---

### REQ-V6-CAL-008: Per-Instrument Min Score Calibration

- **Priority:** Medium
- **Problem:** 6 of 10 instruments are losing money. Global threshold is a blunt instrument -- different instruments have different signal quality distributions.
- **Required behavior:** Add/update INSTRUMENT_OVERRIDES for losing instruments:
  ```python
  INSTRUMENT_OVERRIDES = {
      "BTC/USDT": {"min_composite_score": 25, "sl_atr_multiplier": 3.5, "allowed_regimes": ["STRONG_TREND_BULL", "STRONG_TREND_BEAR"]},
      "ETH/USDT": {"sl_atr_multiplier": 3.5, "min_composite_score": 20},
      "USDJPY=X": {"min_composite_score": 22},
      "NZDUSD=X": {"min_composite_score": 22},
      "GBPUSD=X": {"min_composite_score": 20},
      "SPY": {"min_composite_score": 30, "allowed_regimes": ["STRONG_TREND_BULL"]},  # restrict further or remove
  }
  ```
  Values above are starting points. Each should be validated via individual instrument backtests.
- **Acceptance criteria:**
  - No instrument has PnL < -$50 over 24 months
  - At least 6/10 instruments profitable
- **Data reference:** by_symbol data in full analysis report

---

### REQ-V6-CAL-009: Weekday Restrictions for Monday and Tuesday

- **Priority:** Low
- **Problem:** Monday (-$373) and Tuesday (-$365) are consistently the worst trading days. Combined -$738 loss. Current weekday filter blocks Mon < 10:00 UTC and Fri >= 18:00 UTC only.
- **Required behavior:** Evaluate extending weekday filter:
  - Block Monday entirely (not just < 10:00 UTC) for forex
  - Block Tuesday before 14:00 UTC (late Asian / early European liquidity)
  - OR increase score threshold by 1.5x on Monday/Tuesday
- **Acceptance criteria:**
  - Monday + Tuesday combined PnL >= -$100
  - Total trades >= 100/24mo
- **Data reference:** `by_weekday`: Mon 125 trades -$373, Tue 115 trades -$365

---

### REQ-V6-CAL-010: Comparative Backtest Matrix

- **Priority:** High
- **Problem:** It is unclear which combination of threshold, regime filter, SHORT restrictions produces optimal results. Individual changes may interact non-linearly.
- **Required behavior:** Run a matrix of backtests with the following parameter variations:
  | Test | Threshold | Bear Regime | SHORT | Time Exit |
  |------|-----------|-------------|-------|-----------|
  | A | 9.75 (scale floor 0.65) | Blocked | Enabled | 48 candles |
  | B | 9.75 | Blocked | Disabled | 48 candles |
  | C | 9.75 | Blocked | Enabled | 24 candles |
  | D | 11.0 (scale floor 0.73) | Allowed | Enabled | 48 candles |
  | E | 9.75 | Directional only | 2.0x multiplier | 24 candles |

  Each test should report: trades, WR, PF, max DD, PnL, by_symbol breakdown.
- **Acceptance criteria:**
  - At least one configuration achieves WR >= 25%, PF >= 1.3, DD <= 30%, trades >= 100
  - Results documented in `docs/BACKTEST_RESULTS_V6_CALIBRATION.md`
- **Data reference:** All findings from this analysis

---

## Recommended Threshold Values (Summary)

| Parameter | Current v6 | Recommended | Rationale |
|-----------|-----------|-------------|-----------|
| MIN_COMPOSITE_SCORE | 15 | 15 (unchanged) | Keep global constant; change scaling instead |
| available_weight floor | 0.45 | 0.65 | Prevents threshold from dropping below 9.75 |
| Effective threshold (backtest) | 6.75 | 9.75 | 44% higher; should cut ~200+ weak trades |
| BLOCKED_REGIMES | ["RANGING"] | ["RANGING", "TREND_BEAR", "STRONG_TREND_BEAR"] | Remove -$922 bear regime loss |
| SHORT_SCORE_MULTIPLIER | 1.2 | 2.0 | Require 2x conviction for SHORT |
| SHORT_RSI_THRESHOLD | 40 | 30 | Only deeply oversold for SHORT |
| BTC/USDT min_score | 15 | 25 | Revert to strict; 9.8% WR unacceptable |
| TIME_EXIT_CANDLES["H1"] | 48 | 24 | Reduce capital lockup in losing positions |

## Implementation Priority Order

1. REQ-V6-CAL-001 (threshold) + REQ-V6-CAL-002 (bear regime) -- Critical, maximum impact
2. REQ-V6-CAL-010 (backtest matrix) -- Validate combinations before deploying
3. REQ-V6-CAL-003 (time exit) + REQ-V6-CAL-004 (SHORT) -- High, significant impact
4. REQ-V6-CAL-005 (BTC restrictions) -- High, quick win
5. REQ-V6-CAL-006..009 -- Medium/Low, refinements
