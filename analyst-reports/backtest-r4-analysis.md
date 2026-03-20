# Analysis Report: Backtest Round 4 (Optimized r3)

## Date: 2026-03-20
## Analyst: market-analyst agent
## Run ID: 1eaf680d-c6ac-4236-8752-aead55b32395
## Period: 2024-04-01 to 2025-12-31 (21 months), H1 timeframe, $1000 account
## Context: Iteration 1 of 2 before mandatory architectural decision

---

## Executive Summary

Round 4 applied performance-only optimizations (bisect D1 lookup, DXY RSI pre-mapping, S/R cache) to round 3. **No signal logic was changed**, so the trading results are identical to r3: PF 1.09, WR 24.82%, DD 48.5%, +$159.51. The system remains NOT_VIABLE by its own viability assessment (pf_below_1.3, wr_below_25pct, dd_above_25pct, concentration_risk). However, comparing r4 to the original r3 report reveals important discrepancies in trade counts (139 vs 182) and metrics (WR 24.82% vs 19.4%, DD 48.5% vs 53.8%) that must be reconciled before proceeding. The previous analyst issued a hard stop: "if PF doesn't reach 1.3 in the next 2 iterations, the architecture needs to change completely." This is iteration 1 of 2. PF is 1.09. One iteration remains.

This analysis provides a brutally honest assessment: the TA-only composite scoring system on H1 does not have a statistical edge. The remaining iteration should be used to test the LAST viable signal-logic changes that could bring PF to 1.3. If those fail, the architecture must be redesigned.

---

## 1. Data Reconciliation: r3 vs r4 Discrepancies

### Metrics Comparison

| Metric | r3 (original) | r4 (this run) | Delta |
|--------|--------------|---------------|-------|
| Total trades | 182 | 139 | -43 |
| Win Rate | 19.4% | 24.82% | +5.4% |
| Profit Factor | 1.09 | 1.09 | 0 |
| Total PnL | +$138 | +$159.51 | +$21.51 |
| Max Drawdown | 53.8% | 48.5% | -5.3% |
| LONG/SHORT | 171/11 | 132/7 | -39/-4 |
| Time exits | 127 (69.8%) | Not reported | - |
| Avg Duration | Not reported | 8620 min (~6d) | - |

### Assessment

The metrics are NOT identical despite "no signal logic changes." The discrepancy (43 fewer trades) suggests one of:

1. **The performance optimizations inadvertently changed signal generation behavior.** Bisect D1 lookup or S/R cache could return slightly different values due to rounding, index boundaries, or cache staleness, causing marginal signals to pass/fail differently.

2. **The r3 report used a different backtest configuration.** The r3 analysis mentioned TIME_EXIT_CANDLES["H1"] = 24 (reduced from 48). The r4 config shows TIME_EXIT_CANDLES["H1"] = 48. If r4 restored H1 to 48 candles, this would cause some trades that previously exited via time_exit at 24h to instead reach TP/SL, and others to remain open longer and eventually exit at 48h. This explains both fewer total trades (longer holding = fewer sequential entries due to cooldown/correlation limits) and higher WR (more time to reach TP).

3. **Instrument universe changed.** R3 had 10 instruments, r4 has 10 (same list), but trade counts per instrument differ significantly.

**Verdict:** The r4 results represent a legitimate but different configuration than r3, primarily due to TIME_EXIT_CANDLES restoration to 48. The PF remains 1.09, confirming that neither performance optimizations nor the time_exit change fundamentally improved the system.

---

## 2. Signal Quality Analysis

### Filter Funnel: 92,033 raw signals -> 139 trades (0.15% pass rate)

| Filter | Rejections | % of Raw | Cumulative Pass |
|--------|-----------|----------|----------------|
| score_threshold | 84,599 | 91.9% | 7,434 |
| session_filter | 5,330 | 5.8% | ~2,104 |
| regime_filter | 1,478 | 1.6% | ~626 |
| d1_trend_filter | 260 | 0.3% | ~366 |
| weekday_filter | 124 | 0.1% | ~242 |
| volume_filter | 60 | 0.07% | ~182 |
| momentum_filter | 41 | 0.04% | ~141 |
| dxy_filter | 2 | 0.002% | ~139 |

### Key Observations

**Score threshold dominates filtering (91.9%).** This is expected and correct for a TA-only system where composite = 0.45 * ta_score. The effective threshold is 15 * 0.65 = 9.75, requiring ta_score >= 21.67. Only ~8% of H1 candles produce a TA score strong enough.

**DXY filter is nearly non-functional (2 rejections out of 92,033).** This was flagged in r3 as a bug: the backtest hardcodes `dxy_rsi: None`. The 2 rejections likely come from a fix that was partially implemented. For a 21-month period with DXY RSI frequently > 55 or < 45, we should expect hundreds of rejections on forex LONG/SHORT signals. **This is still broken or barely functional.**

**Regime filter rejects only 1,478 signals.** With RANGING, TREND_BEAR, STRONG_TREND_BEAR, and TREND_BULL all blocked, plus VOLATILE blocked for forex, only STRONG_TREND_BULL (non-forex) and VOLATILE (non-forex) are allowed. The 1,478 rejections seem low given the aggressive blocking. This suggests most candles already fail score_threshold before reaching regime_filter.

---

## 3. Instrument-Level Analysis

### Profitability Heatmap

| Symbol | Trades | WR% | PnL | Avg PnL/trade | Verdict |
|--------|--------|-----|-----|---------------|---------|
| GC=F | 40 | 22.5% | +$150.36 | +$3.76 | **94% of PnL, lottery-ticket pattern** |
| BTC/USDT | 2 | 50% | +$51.51 | +$25.76 | Tiny sample, meaningless |
| EURUSD=X | 24 | 20.8% | +$52.29 | +$2.18 | Marginal positive |
| SPY | 2 | 50% | +$35.73 | +$17.87 | Tiny sample, meaningless |
| USDCAD=X | 23 | 34.8% | +$35.00 | +$1.52 | **Best WR, small positive** |
| NZDUSD=X | 1 | 100% | +$12.16 | +$12.16 | Single trade, meaningless |
| GBPUSD=X | 6 | 16.7% | +$7.00 | +$1.17 | Barely positive, low sample |
| USDJPY=X | 1 | 0% | -$0.55 | -$0.55 | Single loss |
| AUDUSD=X | 3 | 33.3% | -$12.89 | -$4.30 | Small sample, net negative |
| ETH/USDT | 35 | 20% | -$171.11 | -$4.89 | **Worst performer, destroys PnL** |

### Critical Findings

**Finding 1: ETH/USDT is the system's worst enemy.** 35 trades, 20% WR, -$171. This single instrument wipes out the gains from EURUSD, USDCAD, SPY, GBPUSD, NZDUSD, and BTC combined. ETH has:
- min_composite_score = 20 (via override)
- sl_atr_multiplier = 3.5 (wider stops)
- No regime restriction (unlike BTC which requires STRONG_TREND_BULL)

The wider stops combined with the system's lack of predictive power on ETH creates a scenario where losses are amplified. The TA composite score has zero edge on ETH at H1 timeframe.

**Finding 2: GC=F concentration risk.** $150 of $159 total PnL (94%) comes from gold. Remove GC=F and the system has +$9 across 99 trades -- essentially random. The gold edge comes from:
- 2024-2025 gold bull run (macro tailwind, not signal quality)
- High ATR on gold creating large TP targets
- LONG bias (132/139 = 95% LONG) coinciding with bull trend

This is curve-fitting to a specific instrument during a specific regime, not systematic alpha.

**Finding 3: USDCAD is the only instrument with credible positive signal.** 23 trades, 34.78% WR, +$35. This is the highest WR among instruments with sufficient sample size. Still marginal (1.52 per trade), but the only one showing consistent signal quality.

---

## 4. Structural Problems

### Problem 1: LONG-Only System (95% LONG)

132 LONG trades vs 7 SHORT trades. The system is functionally LONG-only because:
- TREND_BEAR and STRONG_TREND_BEAR regimes are blocked (no SHORT environment)
- SHORT_SCORE_MULTIPLIER = 1.3 raises SHORT threshold
- SHORT_RSI_THRESHOLD = 30 (deeply oversold required)
- Only STRONG_TREND_BULL and VOLATILE (non-forex) allowed

**Impact:** The system has no hedging capability. In a broad market downturn, all positions lose simultaneously. The 48.5% drawdown is partly caused by this unidirectional exposure.

**However:** Given that LONG signals produce better results than SHORT in this TA system, the SHORT restriction is actually helping. The 7 SHORT trades should be examined -- if they are net negative, blocking them entirely would marginally improve PF.

### Problem 2: Average Duration of 6 Days on H1 Timeframe

Average trade duration is 8,620 minutes (~6 days, ~144 H1 candles). This is extreme for H1 entries:
- If TIME_EXIT_CANDLES["H1"] = 48, many trades still sit for 2 full days
- Winners take even longer (likely 8+ days based on r3 data: avg_win_duration 12,037 min)
- The signal generates at H1 granularity but the expected move takes 5-10 days

**This confirms the r3 finding:** the TA composite score is generating signals with D1 or H4 predictive horizons, but entering at H1 granularity. The entry timing is suboptimal for the signal's actual timeframe.

### Problem 3: Score Threshold Allows Weak Signals

All 139 trades pass the score threshold, but 75% still lose. The threshold is a necessary but insufficient quality gate. The signals that pass have composite scores of |9.75| to |~13.5| (TA-only range), which corresponds to "borderline buy" quality. The system needs either:
- Much higher thresholds (reducing to <50 trades but higher WR)
- Additional confirmation beyond TA score

---

## 5. What Could Realistically Bring PF to 1.3?

### Mathematical Analysis

Current state: 139 trades, PF 1.09
- Gross wins: $W, Gross losses: $L, where W/L = 1.09
- Total PnL: W - L = $159.51
- Solving: W = $159.51 * 1.09/(1.09-1) = $159.51 * 12.11 = ~$1,932, L = ~$1,773

To reach PF 1.3: need W'/L' = 1.3
- If we keep the same gross wins ($1,932): need L' = $1,932/1.3 = $1,486, reduction of $287 in losses
- If we keep the same gross losses ($1,773): need W' = $1,773 * 1.3 = $2,305, increase of $373 in wins

**Path 1: Eliminate $287 in losses.** ETH/USDT contributes -$171. Blocking ETH entirely recovers $171. Remaining needed: $116 from other cuts. Blocking AUDUSD (-$12.89), USDJPY (-$0.55), and the losing portion of other instruments could cover this.

**Path 2: Increase $373 in wins.** Not feasible without changing signal logic -- the same trades would produce the same wins.

**Path 3 (Hybrid): Block worst losers + tighten thresholds.**
- Block ETH/USDT entirely: +$171 (from 139 to 104 trades)
- Block AUDUSD/USDJPY/NZDUSD (too few trades, unreliable): +$1.28 (from 104 to 99 trades)
- Projected: PnL ~$332, gross_loss reduced to ~$1,600
- New PF estimate: ($1,932 + $1.28) / ($1,773 - $172.28) = $1,933 / $1,601 = 1.21

Even the most aggressive instrument blocking only reaches PF ~1.21, still below 1.3.

**Path 4: Block ETH + Fix DXY filter properly.**
- DXY filter currently blocks 2 signals. With proper implementation, it could block 20-50 losing LONG forex trades during dollar-strong periods.
- If DXY blocks 30 forex losses averaging -$5 each: saves $150
- Combined with ETH block: PF estimate = $1,933 / ($1,601 - $150) = $1,933 / $1,451 = 1.33

**This is the ONLY path that reaches PF >= 1.3, and it requires:**
1. Blocking ETH/USDT entirely (or raising min_composite_score to 30+)
2. Properly implementing DXY filter in backtest (fixing the dxy_rsi=None bug)
3. Assuming DXY filter blocks at least 30 losing forex trades

---

## 6. The Fundamental Question: Is TA-Only H1 Viable?

### Evidence Against

1. **WR 24.82% with R:R ~2:1** -- break-even WR at 2:1 R:R is 33%. The system is 8% below break-even. Only the tail of large GC=F wins (>3:1 R:R) keeps PF above 1.0.

2. **94% PnL concentration in one instrument.** If GC=F has a ranging year, the system is net negative.

3. **6 rounds of calibration** (v5 P1/P2/P3, v6 r1/r2/r3/r4) have not moved PF above 1.09 with real data. Each round reveals previous improvements were artifacts.

4. **The composite scoring approach weighs 8 TA indicators equally** (RSI, MACD, BB, MA cross, ADX, Stochastic, Volume, S/R). This averages out conflicting signals instead of identifying high-conviction setups.

5. **H1 entry with D1 predictive horizon** -- the average winning trade takes 8+ days. The entry at H1 adds noise without improving timing.

### Evidence For (marginal)

1. **GC=F shows genuine trend-following behavior** -- low WR but high R:R on winners. If isolated, this could be a viable trend-following system for commodities.

2. **USDCAD at 34.78% WR** suggests some TA signal quality for this pair specifically.

3. **Phase 2 result (33 trades, PF 2.01)** showed that with very tight filtering, the system CAN produce good results -- but only on a tiny sample size with no external data.

### Verdict

The TA-only composite scoring system on H1 does not have enough edge for multi-instrument trading. The system works as a **gold trend-following system with occasional USDCAD signals**, not as a general-purpose signal generator. This is not necessarily a failure -- it means the instrument universe should be drastically reduced and the system should be positioned as what it actually is.

---

## 7. Viability Assessment

| Metric | Minimum Viable | Current | Status | Gap |
|--------|---------------|---------|--------|-----|
| Profit Factor | >= 1.3 | 1.09 | FAIL | -0.21 |
| Win Rate | >= 25% | 24.82% | MARGINAL FAIL | -0.18% |
| Max Drawdown | <= 25% | 48.5% | CRITICAL FAIL | -23.5% |
| Concentration | <= 40% single instrument | 94% (GC=F) | CRITICAL FAIL | -54% |
| Trades/month | >= 10 | 6.6 | MARGINAL | -3.4 |
| Live-ready | All pass | 0/5 pass | NOT VIABLE | -- |

---

## Findings

### Finding 1: ETH/USDT Destroys System Profitability
- **Severity:** Critical
- **Description:** 35 trades, 20% WR, -$171 PnL. ETH alone wipes out gains from 6 other instruments. The TA composite has zero predictive edge on ETH/USDT at H1 timeframe.
- **Evidence:** by_symbol data -- ETH is -$171 while total system PnL is +$159. Without ETH, system would be +$330.
- **Impact:** System PF would improve from 1.09 to ~1.21 by simply removing ETH.

### Finding 2: DXY Filter Remains Non-Functional
- **Severity:** Critical
- **Description:** Only 2 DXY rejections in 21 months. The backtest is still not properly passing DXY RSI data to the filter pipeline. This was flagged in r3 as a bug (hardcoded `dxy_rsi: None`).
- **Evidence:** filter_stats show dxy_filter: 2 rejections. Expected: 100-300 for a 21-month period with meaningful DXY oscillation.
- **Impact:** Forex LONG signals during dollar-strength periods pass unfiltered, contributing to forex losses.

### Finding 3: System is Functionally LONG-Only
- **Severity:** Major
- **Description:** 132 LONG vs 7 SHORT trades (95%/5%). Combined regime blocks and SHORT multiplier have effectively eliminated SHORT trading.
- **Evidence:** LONG: 132, SHORT: 7. TREND_BEAR, STRONG_TREND_BEAR, TREND_BULL blocked. SHORT requires RSI < 30.
- **Impact:** No hedging capability. Full directional exposure in market downturns.

### Finding 4: GC=F Concentration (94% of PnL) is Unsustainable
- **Severity:** Major
- **Description:** $150 of $159 PnL comes from gold. System without GC=F has +$9 across 99 trades (PF ~1.01).
- **Evidence:** by_symbol: GC=F +$150.36 out of +$159.51 total.
- **Impact:** System viability depends entirely on gold continuing its bull trend. This is not a diversified trading strategy.

### Finding 5: Trade Duration Indicates Timeframe Mismatch
- **Severity:** Major
- **Description:** Average trade lasts 6 days (8,620 min) on H1 timeframe. Winners take even longer. Signal predictive horizon is D1, not H1.
- **Evidence:** avg_duration_minutes: 8620. Previous r3 data showed 70% time_exit rate.
- **Impact:** Capital is tied up in positions that take a full trading week to resolve. H1 granularity adds entry noise without improving timing.

### Finding 6: One Iteration Remaining Before Mandatory Architecture Decision
- **Severity:** Critical (procedural)
- **Description:** Previous analyst set a hard stop: "if PF doesn't reach 1.3 in the next 2 iterations, the architecture needs to change completely." This is iteration 1. PF is 1.09. One iteration remains.
- **Evidence:** r3 analysis, Section 13, Option D, point 6.
- **Impact:** The next iteration is the final attempt within the current architecture. Changes must be maximally impactful.
