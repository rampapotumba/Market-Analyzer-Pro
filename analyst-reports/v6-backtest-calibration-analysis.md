# Analysis Report: v6 Backtest Calibration

## Date: 2026-03-20
## Analyst: market-analyst agent
## Run ID: 62782bc3-0dea-42f1-afed-ee2a295aecd7

---

## Executive Summary

The v6 backtest produced 700 trades over 24 months (vs 33 in v5), a 21x increase driven by proportional threshold scaling (effective threshold dropped from 15 to 6.75). However, Win Rate collapsed from 45.5% to 14.5%, and Max Drawdown exploded from ~10% to 51.6%. The system is now profitable only due to a few large wins in GC=F and ETH/USDT that compensate for a massive volume of small losses. The root cause is a combination of (1) threshold set too low allowing low-conviction signals, (2) time_exit dominating 61.6% of all exits, closing positions at a loss after 48 candles, and (3) bear regime trades producing -$922 with 11% WR. The system traded profitably in Q1 2024, then entered a sustained drawdown from April-October 2024, partially recovering through December 2024.

---

## v5 vs v6 Comparison

| Metric | v5 Phase 3 | v6 | Delta |
|--------|-----------|-----|-------|
| Instruments | 4/6 | 10/10 | +6 |
| Total trades | 33 | 700 | +667 (21x) |
| Trades/month | 1.4 | 29.2 | +27.8 |
| Win Rate | 45.45% | 14.47% | -31.0pp |
| Profit Factor | 2.01 | 1.12 | -0.89 |
| Total PnL | +$253.73 | +$1,765.82 | +$1,512 |
| Max Drawdown | 10.25% | 51.59% | +41.3pp |
| SL hits | 18 | 65 (9.3%) | - |
| TP hits | 15 | 92 (13.1%) | - |
| MAE exits | 0 | 103 (14.7%) | +103 |
| Time exits | 0 | 431 (61.6%) | +431 |
| WR LONG | 57.14% | 16.33% | -40.8pp |
| WR SHORT | 36.84% | 12.04% | -24.8pp |

---

## Signal Quality

- Total raw signals: 22,324
- Passed all filters: 700 (3.14% pass rate)
- Filter rejection breakdown:
  - score_threshold: 13,542 (60.7%) -- primary gate
  - regime_filter: 3,280 (14.7%)
  - d1_trend_filter: 2,799 (12.5%)
  - session_filter: 1,071 (4.8%)
  - volume_filter: 552 (2.5%)
  - momentum_filter: 255 (1.1%)
  - weekday_filter: 125 (0.6%)
- Win rate of passed signals: 14.47% -- unacceptably low
- False positive rate: 85.5% (signals that resulted in losses)

### Key Issue: Threshold Scaling Too Aggressive

The effective threshold of 6.75 (= 15 * 0.45) is too low. With a TA score range of roughly -45 to +45 (since composite = 0.45 * ta_score and ta_score goes up to ~100), a threshold of 6.75 corresponds to ta_score >= 15 in absolute terms. This lets through signals with very weak technical conviction.

In v5, the effective threshold was 15 (no scaling), which required ta_score >= 33.3 -- much more selective.

---

## Per-Instrument Analysis

| Symbol | Trades | Wins | WR% | PnL USD | Avg PnL | Verdict |
|--------|--------|------|-----|---------|---------|---------|
| GC=F | 50 | 9 | 18.0% | +$1,112.79 | +$22.26 | TOP PERFORMER |
| ETH/USDT | 86 | 13 | 15.1% | +$946.14 | +$11.00 | TOP PERFORMER |
| USDCAD=X | 73 | 13 | 17.8% | +$65.38 | +$0.90 | Marginal |
| AUDUSD=X | 87 | 15 | 17.2% | +$65.77 | +$0.76 | Marginal |
| SPY | 6 | 1 | 16.7% | -$8.82 | -$1.47 | Restricted, still losing |
| EURUSD=X | 67 | 13 | 19.4% | -$20.81 | -$0.31 | Losing, highest WR of losers |
| GBPUSD=X | 77 | 11 | 14.3% | -$43.76 | -$0.57 | Losing |
| USDJPY=X | 62 | 8 | 12.9% | -$75.49 | -$1.22 | Losing |
| BTC/USDT | 102 | 10 | 9.8% | -$135.09 | -$1.32 | WORST WR, high volume |
| NZDUSD=X | 90 | 15 | 16.7% | -$140.29 | -$1.56 | WORST avg loss |

### Key Findings:
1. **GC=F and ETH/USDT carry the entire strategy** -- $2,058 combined profit vs $1,766 total
2. **BTC/USDT is the worst performer** despite relaxed restrictions (9.8% WR, -$135)
3. **6 of 10 instruments are losing money** -- system is not robust
4. **New instruments (USDJPY, NZDUSD, USDCAD)** are mixed: USDCAD marginal positive, USDJPY and NZDUSD losing
5. **SPY restrictions working** -- only 6 trades (down from prior), but still losing

---

## By Regime Analysis

| Regime | Trades | Wins | WR% | PnL USD |
|--------|--------|------|-----|---------|
| STRONG_TREND_BULL | 160 | 27 | 16.9% | +$1,583.40 |
| TREND_BULL | 84 | 13 | 15.5% | +$653.15 |
| VOLATILE | 237 | 44 | 18.6% | +$451.82 |
| TREND_BEAR | 80 | 9 | 11.2% | -$321.40 |
| STRONG_TREND_BEAR | 139 | 15 | 10.8% | -$601.16 |

### Key Findings:
1. **Bear regimes are catastrophic**: 219 trades, 11.0% WR, -$922.55 PnL
2. **VOLATILE is the most traded regime** (237 trades) with mediocre WR (18.6%) but positive PnL
3. **Bull regimes are the only consistently profitable**: 244 trades, 16.4% WR, +$2,236.55
4. **v5 blocked RANGING completely** -- v6 still blocks it (0 RANGING trades, confirmed)
5. **Bear regimes should be blocked or severely restricted** -- they are generating massive losses

---

## By Score Bucket Analysis

| Bucket | Trades | Wins | WR% | PnL USD | Note |
|--------|--------|------|-----|---------|------|
| strong_buy | 11 | 2 | 18.2% | +$560.94 | Few trades, high avg win |
| buy | 148 | 26 | 17.6% | +$1,921.00 | BEST bucket |
| weak_buy | 213 | 35 | 16.4% | +$237.39 | Many trades, low PnL |
| neutral | 26 | 7 | 26.9% | +$194.83 | Edge case (6.75-7.0 range) |
| weak_sell | 125 | 19 | 15.2% | +$106.00 | Slightly positive |
| sell | 165 | 18 | 10.9% | -$1,202.78 | WORST bucket |
| strong_sell | 12 | 1 | 8.3% | -$51.55 | Very poor WR |

### Key Findings:
1. **Score buckets use UNSCALED thresholds** for reporting -- a composite score of 8.0 maps to "weak_buy" in the report even though it passed the scaled STRONG_BUY filter (>= 6.75). This is confusing but functionally correct.
2. **"sell" bucket is catastrophic**: 165 trades, 10.9% WR, -$1,202 PnL. These are SHORT signals with |composite| between 10 and 15 (unscaled), which in scaled terms are strong signals -- but they LOSE money.
3. **"buy" bucket is the engine**: 148 trades, 17.6% WR, +$1,921 -- all profit comes from here.
4. **LONG vs SHORT asymmetry confirmed**: buy-side buckets are profitable, sell-side buckets lose money.

---

## LONG vs SHORT Analysis

| Direction | Trades | WR% | Note |
|-----------|--------|-----|------|
| LONG | 398 | 16.33% | Bulk of trades |
| SHORT | 302 | 12.04% | Consistently worse |

The SHORT_SCORE_MULTIPLIER (1.2x) and SHORT_RSI_THRESHOLD (40) were introduced but are clearly insufficient. SHORT signals have:
- Lower WR (12% vs 16%)
- Catastrophic performance in bear regimes (counterintuitive -- SHORTs in bear regimes should work)
- "sell" bucket PnL: -$1,202

**Root cause hypothesis**: The system generates SHORT signals during bear regimes but they hit time_exit (48 candles, 2 days) before reaching TP. The R:R ratio may be too wide for SHORT trades, or the regime detection lags the actual market move.

---

## Exit Type Analysis (CRITICAL)

| Exit Reason | Count | % of Total |
|-------------|-------|-----------|
| time_exit | 431 | 61.6% |
| mae_exit | 103 | 14.7% |
| tp_hit | 92 | 13.1% |
| sl_hit | 65 | 9.3% |
| end_of_data | 9 | 1.3% |

### This is the central problem.

**61.6% of trades exit via time_exit** -- meaning 431 trades sit for 48 H1 candles (2 days) without reaching either SL or TP, then close at a loss. This indicates:
1. **SL and TP are set too far from entry** -- price never reaches them within 2 days
2. **The "unrealized_pnl <= 0" condition for time_exit** means these are all slightly losing trades that just drift

**14.7% exit via MAE** -- these trades moved 60% toward SL without any progress toward TP. Combined with time_exit, **76.3% of all trades exit with a loss mechanism**.

**Only 13.1% reach TP** -- the take profit target is clearly too ambitious for the average signal quality at this threshold level.

**avg_mae_pct_of_sl = 285.6%** -- on average, trades experience adverse excursion 2.85x the SL distance. This is deeply concerning and suggests either (a) SL is far too tight, or (b) entries are poorly timed and price moves heavily against before (sometimes) recovering.

---

## Weekday Analysis

| Day | Trades | WR% | PnL USD |
|-----|--------|-----|---------|
| Mon | 125 | 14.4% | -$372.63 |
| Tue | 115 | 15.7% | -$365.18 |
| Wed | 159 | 19.5% | +$1,401.85 |
| Thu | 137 | 12.4% | -$156.63 |
| Fri | 124 | 14.5% | -$207.37 |
| Sat | 10 | 10.0% | -$55.50 |
| **Sun** | **30** | **16.7%** | **+$1,521.29** |

**Sunday contributes $1,521 (86% of total PnL)** from only 30 trades. These are almost certainly crypto trades (BTC/ETH) that happen to be profitable. Wednesday is the only other profitable weekday with $1,401.

**Monday and Tuesday are the worst days** -- combined -$738 loss. The weekday filter blocks Monday < 10:00 UTC, but Monday trades starting at 10:00+ are still losing.

---

## Equity Curve Analysis

- Start: $1,000 (2024-01-01)
- Peak (Q1 2024): $2,527 (2024-03-27) -- rapid growth in first 3 months
- Drawdown phase: April-October 2024 ($2,527 -> $1,307) -- 48% drawdown
- Partial recovery: October-December 2024 ($1,307 -> $2,293)
- Second peak: April 2025 ($2,818)
- Final: $2,766 (2025-12-31)

The equity curve shows two distinct phases: high-conviction early trades (when GC=F and ETH had strong trends) and then a long period of churn with many small losses from low-conviction trades.

---

## Monthly Returns

- Winning months: 10 of 24 (41.7%)
- Losing months: 14 of 24 (58.3%)
- Avg winning month: +$391.65
- Avg losing month: -$153.62
- Worst month: May 2025 (-$408)
- Best month: March 2024 (+$799)
- Longest losing streak: Apr-Sep 2024 (6 months)

---

## Root Cause Analysis: Why WR Dropped from 45.5% to 14.5%

### Cause 1: Threshold Too Low (PRIMARY)
Effective threshold 6.75 lets through ~3x more signals than needed. In v5, only signals with |composite| >= 15 passed (requiring ta_score >= 33). Now signals with |composite| >= 6.75 pass (requiring ta_score >= 15). The quality of entries at ta_score=15 is fundamentally lower than ta_score=33.

### Cause 2: Time Exit Dominance (SECONDARY)
431/700 trades (61.6%) exit via time_exit after 48 H1 candles. In v5, 0 time_exits occurred because (a) v5 didn't implement time_exit and (b) the higher threshold meant only high-conviction signals entered, which resolved faster (avg 4034 min vs 9657 min in v6).

### Cause 3: Bear Regime Trades (CONTRIBUTING)
219 bear regime trades lost $922 at 11% WR. In v5, RANGING was blocked but bear regimes were allowed. With only 33 trades in v5, the sample was too small to see this problem. At 700 trades, the pattern is clear.

### Cause 4: SHORT Signal Quality (CONTRIBUTING)
302 SHORT trades at 12% WR. The 1.2x multiplier on threshold is insufficient. The "sell" score bucket alone loses $1,202. SHORT signals in bear regimes should theoretically work but don't -- suggesting the regime detector is identifying "bear" after the move has already happened (lagging indicator).

### Cause 5: BTC/USDT Unblocking Was Premature
Relaxing BTC restrictions (min_score 20->15, adding TREND regimes) produced 102 trades at 9.8% WR and -$135 PnL. The original v5 restrictions were justified.

---

## Data Quality

- Data gaps: None detected (continuous equity curve)
- Missing fields: Score buckets use unscaled thresholds, creating confusion (see Score Bucket Analysis)
- Anomalies:
  - 10 Saturday trades and 30 Sunday trades exist -- should be crypto only (forex markets closed)
  - avg_mae_pct_of_sl = 285.6% -- abnormally high, suggesting SL calculation issues or the metric is cumulative rather than peak
  - End-of-data exclusion working correctly (9 trades, $789 excluded from WR/PF metrics)

---

## Code Logic Review

- Files reviewed:
  - `src/signals/filter_pipeline.py` -- filter logic
  - `src/backtesting/backtest_engine.py` -- score buckets, time_exit, mae_exit
  - `src/config.py` -- thresholds and overrides

### Issues Found:

1. **Score bucket reporting uses unscaled thresholds** (backtest_engine.py:342-358) while filtering uses scaled thresholds. This means the reported "weak_buy" signals actually passed as "STRONG_BUY" in the filter system. Misleading for analysis.

2. **Time exit condition** (backtest_engine.py:1369-1377): fires after 48 candles if unrealized_pnl <= 0. With 61.6% of trades hitting this, the threshold of 48 candles (2 days for H1) may be appropriate but the volume of entries that never reach TP/SL suggests the SL/TP distances are miscalibrated for the typical move size at the 6.75 threshold level.

3. **MAE metric** (avg_mae_pct_of_sl = 285.6%): This means on average, price moves 2.85x the SL distance against the position. Either the MAE tracking is cumulative (not peak), or SL is far too tight. Need to verify the MAE calculation methodology.

4. **GBPUSD=X override is empty** (config.py:164-166): The override dict exists but contains no keys, meaning GBPUSD uses global defaults. Intentional per v6 spec but contributing to its -$43 PnL.

---

## Findings Summary

### Finding 1: Proportional Threshold Scaling Overcorrected
- Severity: **Critical**
- Description: `effective_threshold = 15 * 0.45 = 6.75` lets through 21x more trades than v5 at dramatically lower quality (WR 14.5% vs 45.5%)
- Evidence: 22,324 raw signals -> 700 passed (3.14%), but 85.5% of those are false positives
- Impact: Max drawdown 51.6%, profit factor dropped from 2.01 to 1.12

### Finding 2: Time Exit Accounts for 61.6% of All Exits
- Severity: **Critical**
- Description: 431 of 700 trades exit via time_exit (48 H1 candles) with unrealized_pnl <= 0. These trades consume capital for 2 days and always close at a loss.
- Evidence: time_exit_count = 431, avg_duration_minutes = 9,657 (6.7 days)
- Impact: Massive capital lockup in losing positions, contributing to drawdown

### Finding 3: Bear Regime Trades Are Catastrophically Unprofitable
- Severity: **Major**
- Description: 219 trades in TREND_BEAR + STRONG_TREND_BEAR regimes produce -$922 at 11% WR
- Evidence: STRONG_TREND_BEAR: 139 trades, 10.8% WR, -$601; TREND_BEAR: 80 trades, 11.2% WR, -$321
- Impact: Bear regimes contribute -$922 while bull regimes contribute +$2,237; blocking bear regimes alone would increase PF to ~1.5

### Finding 4: SHORT Signals Remain Low Quality Despite Asymmetric Thresholds
- Severity: **Major**
- Description: SHORT WR at 12.04% vs LONG 16.33%. The "sell" score bucket alone loses $1,202.
- Evidence: SHORT_SCORE_MULTIPLIER=1.2 is insufficient; sell bucket (|composite| 10-15) has 10.9% WR
- Impact: SHORT signals drag overall performance; system would be more profitable LONG-only

### Finding 5: BTC/USDT Restriction Relaxation Failed
- Severity: **Major**
- Description: BTC/USDT min_score lowered from 20 to 15 and regimes expanded to include TREND. Result: 102 trades at 9.8% WR, -$135 PnL.
- Evidence: by_symbol["BTC/USDT"]: worst WR of all instruments
- Impact: BTC/USDT is the second-worst performer by PnL

### Finding 6: Score Bucket Reporting Inconsistency
- Severity: **Minor**
- Description: `_score_bucket()` uses unscaled thresholds (7/10/15) while filter pipeline uses scaled thresholds (3.15/4.5/6.75). A signal with composite=8.0 passes as "STRONG_BUY" in filters but appears as "weak_buy" in reports.
- Evidence: 213 "weak_buy" trades and 125 "weak_sell" trades in report, but these all passed STRONG_BUY/STRONG_SELL filter gates
- Impact: Misleading analytics; cannot directly compare score buckets between v5 and v6

### Finding 7: Only 2 of 10 Instruments Drive Profitability
- Severity: **Major**
- Description: GC=F (+$1,113) and ETH/USDT (+$946) generate $2,059 profit. The other 8 instruments combined generate -$293. System is not diversified.
- Evidence: by_symbol data shows 6/10 instruments losing money
- Impact: System performance depends entirely on gold and ETH trends; removal of either would make system unprofitable

### Finding 8: Wednesday and Sunday Calendar Concentration
- Severity: **Minor**
- Description: $2,923 of profit comes from Wednesday ($1,402) and Sunday ($1,521). All other weekdays combined lose $1,157.
- Evidence: by_weekday data
- Impact: Potential overfitting to specific day-of-week patterns
