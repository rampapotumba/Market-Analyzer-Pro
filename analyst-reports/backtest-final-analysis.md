# Analysis Report: Final v5 Backtest Evaluation

## Date: 2026-03-20
## Analyst: market-analyst agent

---

## Executive Summary

The v5 backtest suite reveals **severe data integrity issues** that undermine confidence in all reported metrics. Three separate backtest runs with identical parameters produced wildly different results (PF ranging from 0.40 to 2.33), indicating **non-deterministic behavior** in the backtest engine. The "Baseline" result degraded from PF 2.01 (pre-pipeline-fix) to PF 0.60 (post-pipeline-fix), a 3.3x deterioration that was never explained. The target of 55-75 trades/month is missed by a factor of 46x (actual: ~1.2/month), making the backtest statistically meaningless with only 30 trades over 24 months. GBPUSD and BTC/USDT produce 0 trades across all phases, meaning two of six instruments are effectively dead. The entire v5 optimization may be fitting to noise rather than discovering genuine edge.

---

## 1. Critical Anomaly: Non-Deterministic Backtest Results

### Evidence

Three separate runs with the same parameters (all filters ON, min_score=15) produced different results:

| Source | Run Script | Trades | WR% | PF | PnL |
|--------|-----------|--------|-----|-----|-----|
| BACKTEST_RESULTS_V5_P3.md | run_backtests_v5_fixed.sh | 33 | 45.45% | 2.01 | +$253.73 |
| BACKTEST_RESULTS_FINAL.md "Phase 3" | run_backtests_v5_fixed.sh (Baseline) | 30 | 46.67% | 2.33 | +$278.87 |
| bt_Baseline.json (all OFF) | FINAL script | 38 | 31.58% | 0.60 | -$1054.31 |

The Phase 2 and Phase 3 results from `run_backtests_v5_fixed.sh` are **byte-identical** (33 trades, PF 2.01, every metric matches). This means the calendar filter (SIM-33) had **zero effect** -- either it found no economic events in the database, or it is not functioning.

### Root Cause Hypotheses

1. **Empty economic_events table**: The calendar filter calls `get_economic_events_in_range()`. If this table is empty, the filter passes through silently (graceful degradation). The log line `[SIM-33] Loaded 0 HIGH-impact economic events` would confirm this but was not captured.

2. **Non-deterministic regime detection**: The baseline run (all filters OFF, `apply_session_filter: false`) shows 38 trades with regime breakdown: RANGING=13, VOLATILE=9, TREND_BULL=4, STRONG_TREND_BULL=5, STRONG_TREND_BEAR=5, TREND_BEAR=2. When filters are ON, the RANGING regime block (13 trades) accounts for 34% of baseline trades. But the expected filtered count (38-13=25) does not match any Phase result.

3. **Code changes between runs**: The BACKTEST_RESULTS_V1.md (old baseline, 33 trades, PF 2.01) and bt_Baseline.json (new baseline, 38 trades, PF 0.60) were produced by different code versions. The pipeline fix (`run_backtests_v5_fixed.sh`) changed how `apply_session_filter` defaults work.

### Severity: CRITICAL

The backtest engine is not reproducible. Results cannot be trusted for strategy validation.

---

## 2. Baseline Degradation Analysis

### Old Baseline (BACKTEST_RESULTS_V1.md, pre-pipeline-fix)
- 33 trades, WR 45.45%, PF 2.01, DD 10.25%, PnL +$253.73

### New Baseline (bt_Baseline.json, post-pipeline-fix, all filters OFF)
- 38 trades, WR 31.58%, PF 0.60, DD 159.19%, PnL -$1054.31

### Key Differences

| Metric | Old Baseline | New Baseline | Delta |
|--------|-------------|-------------|-------|
| Trades | 33 | 38 | +5 more trades |
| ETH/USDT trades | 1 | 7 | +6 ETH trades |
| ETH/USDT PnL | +$236.80 | -$1323.12 | -$1559.92 swing |
| EURUSD trades | 17 | 12 | -5 EURUSD trades |
| Avg duration | 4034 min | 80613 min | 20x longer |

**The ETH/USDT catastrophe explains almost everything.** The new baseline has 7 ETH trades (vs 1 in old baseline), losing $1323. The single profitable ETH trade ($236.80) is present in both. The 6 additional ETH trades are all losers opened when session_filter was OFF.

**Why avg duration is 20x longer:** The old baseline had `apply_session_filter` effectively ON (it was the default behavior before the pipeline fix). The new baseline explicitly sets `apply_session_filter: false`. Without the session filter, trades open during low-liquidity Asian hours where spreads are wide and moves are noisy. These trades sit open for weeks (80,613 min average = 56 days) before hitting SL.

### Root Cause
The session filter was implicitly active in early runs. When the FINAL baseline explicitly disabled it (`apply_session_filter: false`), it exposed 5 additional toxic trades. This is actually a **correct behavior** of the baseline -- it shows the system without any filtering. The problem is that the "improvement" shown by Phase 1-3 is partly just re-enabling the session filter that was already active before.

---

## 3. Phase Progression Analysis

### FINAL Report Numbers

| Phase | Trades | WR% | PF | DD% | PnL |
|-------|--------|-----|-----|-----|-----|
| Baseline (all OFF) | 38 | 31.6% | 0.60 | 159% | -$1054 |
| Phase 1 (+ranging, d1, session, score) | 34 | 29.4% | 0.37 | 158% | -$1581 |
| Phase 2 (+volume, weekday, momentum) | 29 | 31.0% | 0.40 | 96% | -$871 |
| Phase 3 (+calendar) | 30 | 46.7% | 2.33 | 7.1% | +$279 |

### Phase 1 Worsening (PF 0.60 -> 0.37)

Phase 1 added 4 filters but **made things worse**. Analysis:
- Trades reduced from 38 to 34 (-4), but PnL dropped by $527.
- The ranging filter should have removed 13 RANGING trades (+$83 PnL from baseline regime data). If these profitable RANGING trades were removed, that explains partial degradation.
- The D1 MA200 filter may be blocking profitable counter-trend trades.
- The session filter now explicitly active should have improved results, but it appears the combination of score>=15 + ranging + d1 trend filtered OUT profitable signals while leaving unprofitable ones.

**Evidence from Baseline regime data**: RANGING regime had 13 trades, 5 wins, +$83 PnL. Blocking this regime REDUCES profitability. This contradicts the hypothesis that RANGING generates random signals.

### Phase 2 -> Phase 3 Jump (PF 0.40 -> 2.33)

This is the most suspicious finding.

**Fact 1**: Phase 3 has 30 trades vs Phase 2's 29 trades. Calendar filter should only REDUCE trades (it blocks signals near economic events). Having MORE trades is a **monotonicity violation**.

**Fact 2**: The jump from PF 0.40 to PF 2.33 (5.8x improvement) by adding a single filter is statistically improbable. Calendar filter blocks HIGH-impact events -- these should be roughly randomly distributed across winning and losing trades.

**Fact 3**: From the BACKTEST_RESULTS_V5_P2.md and V5_P3.md (fixed script runs), Phase 2 and Phase 3 produced IDENTICAL results (33 trades, PF 2.01). This means the calendar filter had zero effect in those runs.

**Conclusion**: The FINAL report's Phase 2 and Phase 3 numbers were produced by DIFFERENT code or different data states. The Phase 3 "improvement" is an artifact of non-deterministic execution, not a genuine filter effect.

---

## 4. Instrument Analysis

### 4.1 GBPUSD=X: 0 Trades Across All Phases

**Root Cause**: INSTRUMENT_OVERRIDES sets `min_composite_score: 20` for GBPUSD. Combined with the TA-only composite calculation in backtest (`composite = 0.45 * ta_score`), the maximum possible composite is `0.45 * 100 = 45`. However, in practice ta_score rarely exceeds 40, making effective max composite ~18. With threshold=20, GBPUSD signals are almost always blocked.

**Code Evidence** (backtest_engine.py line 1309): `composite = _TA_WEIGHT * ta_score` where `_TA_WEIGHT = 0.45`. For GBPUSD to pass the threshold of 20, ta_score must be >= 44.4. This is extremely rare for a mean-reverting forex pair.

### 4.2 BTC/USDT: 0 Trades in Filtered Phases, 1 Trade in Baseline

**Root Cause**: BTC/USDT has triple filtering:
1. `min_composite_score: 20` (requires ta_score >= 44.4)
2. `allowed_regimes: ["STRONG_TREND_BULL", "STRONG_TREND_BEAR"]` (blocks TREND, RANGING, VOLATILE)
3. Volume filter active (crypto has volume data)

This combination is too restrictive. In 24 months, only 1 BTC signal passed the old baseline. With filters, zero pass.

### 4.3 AUDUSD=X: Best Performer (77.8% WR, +$101.69)

AUDUSD consistently performs well across all phases. This instrument has NO overrides (uses default min_composite_score=15). It demonstrates that the base strategy works for some instruments.

### 4.4 SPY: Consistently Poor (16.7-20% WR, -$80 to -$114)

SPY loses money in every phase. The stock market has different characteristics (mean-reversion tendencies, gap behavior) that the trend-following TA signals may not capture well.

### 4.5 ETH/USDT: Single Trade Dominance

The single ETH trade (+$236.80) is a massive outlier. When present (filtered phases), it accounts for 85% of total profit. This makes the entire portfolio dependent on one trade -- a classic overfitting risk.

---

## 5. Signal Quality Assessment

### 5.1 Score Buckets

Phase 3 (FINAL):
- strong_buy: 13 trades, 8 wins (61.5%), +$333.88
- strong_sell: 17 trades, 6 wins (35.3%), -$55.02

**LONG bias is significant**: strong_buy WR 61.5% vs strong_sell WR 35.3%. The SHORT strategy underperforms by 26 percentage points. This matches the historical observation from v4.

### 5.2 Duration Analysis

| Phase | Avg Duration |
|-------|-------------|
| Baseline (all OFF) | 80,613 min (56 days) |
| Phase 1 | 50,779 min (35 days) |
| Phase 2 | 36,708 min (25 days) |
| Phase 3 | 4,024 min (2.8 days) |

The dramatic reduction from 56 days to 2.8 days is suspicious. If the same SL/TP logic is used, the duration should depend on market volatility, not on entry filters. Possible explanations:
1. Session filter prevents opening during low-liquidity hours where trades sit for weeks.
2. The filtered trades happen to be in faster-moving market conditions.
3. End-of-data closing affects average -- trades open near end of backtest period inflate duration.

### 5.3 Weekday Analysis (Baseline)

| Weekday | Trades | Wins | PnL |
|---------|--------|------|-----|
| Mon (0) | 6 | 2 | -$272 |
| Tue (1) | 10 | 2 | -$1171 |
| Wed (2) | 5 | 4 | +$845 |
| Thu (3) | 8 | 2 | -$160 |
| Fri (4) | 7 | 2 | -$187 |
| Sat (6) | 2 | 0 | -$110 |

**Wednesday is the only profitable day** (4/5 wins, +$845). This extreme concentration is concerning -- it suggests the strategy may only work on specific days, likely due to mid-week momentum characteristics.

**Saturday trades (2)**: These should not exist for forex/stocks. Either timestamp handling is incorrect (UTC timezone shifting?), or crypto trades are landing on Saturday.

### 5.4 Hour Analysis (Baseline)

Low win rates at hours 7-9, 14-15, 18-19 (all 0%). Decent at 13 (100%), 4 (100%), 5 (100%), 20 (100%). Small sample sizes make these unreliable.

---

## 6. Target Metrics Assessment

| Metric | Target | Actual (Phase 3 FINAL) | Status |
|--------|--------|----------------------|--------|
| Win rate >= 46% | 46% | 46.67% | MARGINAL PASS (barely above threshold) |
| Profit factor >= 1.4 | 1.4 | 2.33 | PASS (but not trustworthy -- see above) |
| Max drawdown <= 35% | 35% | 7.12% | PASS (but sample too small) |
| Trades/month 55-75 | 55 | 1.2 | CRITICAL FAIL (46x below minimum) |
| LONG/SHORT 30-70% | 30-70% | 43%/57% | PASS |

### The trades/month problem

30 trades over 24 months = 1.25 trades/month. The target is 55-75/month. This is not a minor shortfall -- it is a fundamental architectural problem.

**Root causes of low trade frequency:**
1. **TA-only composite**: Backtest uses only TA weight (0.45 * ta_score). FA, sentiment, geo are all 0. This means the effective signal range is [-45, +45] instead of [-100, +100]. With threshold=15, the bar is proportionally higher.
2. **Restrictive overrides**: GBPUSD (score>=20), BTC (score>=20 + strong_trend only), ETH (score>=20) effectively eliminate 3 of 6 instruments.
3. **Stacking filters**: Session + regime + D1 trend + volume + momentum + weekday + calendar -- each filter independently reasonable, but combined they eliminate nearly everything.
4. **Single position per symbol**: The engine opens at most 1 position per symbol at any time. With avg duration 2.8 days and 4 active instruments, theoretical max is ~4 concurrent positions.

---

## 7. Code Logic Issues

### 7.1 Session Filter Always-On for Score Check

In `filter_pipeline.py` line 166-169, `check_signal_strength()` is called **regardless of any flag** (comment says "always-on quality gate"). This means even when `apply_score_filter=False` is set in baseline, the SIM-31 strength filter still runs. This is by design but affects the "all filters OFF" baseline -- it is not truly "all filters OFF".

### 7.2 S/R Computation Called on Every Signal (Performance + Accuracy)

In `backtest_engine.py` lines 1319-1336, `_generate_signal_fast()` calls `TAEngine(df_slice)` and `calculate_all_indicators()` to get S/R levels. This is called for every potential signal (~17,000 candles per symbol), creating unnecessary computation. The comment says "only called when signal passes" but it is called BEFORE the filter pipeline, not after.

### 7.3 D1 Data Not Available in Backtest

In `backtest_engine.py` line 1066, `d1_rows` is hardcoded to empty list: `"d1_rows": []`. This means the D1 MA200 trend filter (`check_d1_trend`) ALWAYS passes in backtest due to graceful degradation. The filter flag `apply_d1_trend_filter=True` has zero effect.

**This is a critical finding**: The D1 filter was listed as "ON" in Phase 1-3, but it never actually ran. Every time it was called, it received an empty list and returned True (passthrough). The filter is non-functional in backtest mode.

### 7.4 DXY Filter Not Available in Backtest

In `backtest_engine.py` line 1068, `dxy_rsi` is hardcoded to `None`. Same as D1 -- the DXY filter always passes through.

### 7.5 Volume Filter Skipped for Forex

In `filter_pipeline.py` lines 282-283, the volume filter explicitly skips forex pairs (`if market_type == "forex": return True`). Since 3 of 4 active instruments are forex (EURUSD, AUDUSD, SPY has volume data, ETH/BTC have volume), the volume filter only applies to 2 instruments (crypto + SPY). For the crypto instruments, they are already blocked by score>=20 override.

---

## 8. Data Quality

### 8.1 Regime Always UNKNOWN in Filtered Runs

BACKTEST_RESULTS_V5_P1.md, P2.md, P3.md all show `by_regime = {UNKNOWN: all trades}`. This means the regime field is not being propagated to BacktestTradeResult correctly in the filtered runs. The baseline JSON does show proper regime breakdown. This is a data tracking bug.

### 8.2 MAE Values Anomalous

Baseline JSON shows `avg_mae_pct_of_sl: 676.12`. This means the average Maximum Adverse Excursion is 676% of the stop-loss distance. Values > 100% should be impossible if SL is functioning correctly (the trade should have been stopped out). This indicates MAE is being calculated incorrectly, or trades are not checking SL on every candle.

### 8.3 Avg Win Duration > Avg Loss Duration (Baseline)

Baseline shows avg_win_duration=181,735 min (126 days) vs avg_loss_duration=33,942 min (24 days). Winners lasting 126 days in a 730-day backtest period means trades are open for 17% of the entire test period. These are likely end-of-data closures classified as "wins" by virtue of the exit price being above entry.

---

## Findings Summary

### Finding 1: Backtest Non-Determinism
- **Severity:** CRITICAL
- **Description:** Identical parameters produce different results across runs (PF range: 0.40 to 2.33)
- **Evidence:** BACKTEST_RESULTS_V5_P2.md (PF 2.01) vs FINAL Phase 2 (PF 0.40) with same filter configuration
- **Impact:** No v5 result can be trusted. Strategy validation is impossible.

### Finding 2: D1 Trend Filter Non-Functional in Backtest
- **Severity:** CRITICAL
- **Description:** `d1_rows` is hardcoded to `[]` in backtest, causing D1 MA200 filter to always pass
- **Evidence:** backtest_engine.py line 1066: `"d1_rows": []`
- **Impact:** One of the core P1 filters (SIM-27) has never been tested. Reported Phase 1 improvements may be entirely from session filter re-activation.

### Finding 3: Trades/Month 46x Below Target
- **Severity:** CRITICAL
- **Description:** 1.2 trades/month vs target 55-75. Only 30 trades in 24 months across 6 instruments.
- **Evidence:** BACKTEST_RESULTS_FINAL.md: 30 total trades, 24-month period
- **Impact:** Results are statistically meaningless. With 30 trades, confidence interval for WR 46.7% is approximately +/-18% (95% CI: 28.5%-64.9%).

### Finding 4: Calendar Filter Has Zero Effect
- **Severity:** MAJOR
- **Description:** Phase 2 and Phase 3 produce identical results when calendar filter toggled
- **Evidence:** BACKTEST_RESULTS_V5_P2.md and BACKTEST_RESULTS_V5_P3.md are byte-identical
- **Impact:** economic_events table is likely empty. Calendar filter provides no value in backtest.

### Finding 5: 3 of 6 Instruments Produce 0 Trades
- **Severity:** MAJOR
- **Description:** GBPUSD and BTC/USDT have 0 trades; ETH has only 1 trade (outlier)
- **Evidence:** by_symbol data in all phase reports
- **Impact:** Portfolio is effectively a 3-instrument strategy (EURUSD, AUDUSD, SPY) with one crypto outlier

### Finding 6: MAE Values Exceed 100% of SL Distance
- **Severity:** MAJOR
- **Description:** avg_mae_pct_of_sl = 676% in baseline, indicating SL check failure or MAE miscalculation
- **Evidence:** bt_Baseline.json: `"avg_mae_pct_of_sl": 676.1171`
- **Impact:** Risk management metrics are unreliable. Actual risk exposure may be much higher than designed.

### Finding 7: SHORT Strategy Significantly Underperforms
- **Severity:** MAJOR
- **Description:** WR LONG 61.5% vs WR SHORT 35.3% across all phases
- **Evidence:** strong_buy WR 61.5% vs strong_sell WR 35.3% in Phase 3 FINAL
- **Impact:** SHORT signals destroy approximately 50% of LONG profits. Disabling SHORT would improve PF.

### Finding 8: End-of-Data Trade Closures Distort Metrics
- **Severity:** MAJOR
- **Description:** Trades open at end of backtest period are closed at last price and counted as win/loss
- **Evidence:** Baseline avg_win_duration=126 days (17% of backtest period). Equity curve shows 4 trades closing on 2025-12-31 with total +$844.
- **Impact:** Approximately 10-15% of trades are not genuine SL/TP exits, inflating metrics.

### Finding 9: Saturday Trades Exist
- **Severity:** MINOR
- **Description:** Baseline by_weekday shows 2 trades on day 6 (Saturday)
- **Evidence:** bt_Baseline.json: `"6": {"trades": 2, "wins": 0, "pnl_usd": -109.91}`
- **Impact:** Possible timezone handling error or crypto trades being classified with wrong weekday.
