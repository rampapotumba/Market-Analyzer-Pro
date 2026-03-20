# Requirements: Backtest Round 4 Post-Analysis (FINAL ITERATION)

## Date: 2026-03-20
## From: market-analyst
## To: architect
## Context: This is the LAST iteration before mandatory architecture decision. PF must reach >= 1.3 or the TA-only H1 approach is abandoned.

---

## Background

Analysis of r4 backtest (run_id: 1eaf680d-c6ac-4236-8752-aead55b32395). Results: 139 trades, PF 1.09, WR 24.82%, DD 48.5%, +$159.51. System is NOT_VIABLE. Mathematical analysis shows that reaching PF 1.3 requires: (a) blocking ETH/USDT (-$171 in losses), AND (b) properly implementing DXY filter to block ~30 losing forex trades. Both changes together yield estimated PF 1.33. Either change alone is insufficient.

**This is the final round.** Only changes with high confidence of measurable impact should be implemented. No speculative experiments.

---

## Requirements

### REQ-V6-R4-001: Block ETH/USDT from Trading
- **Priority:** Critical
- **Problem:** ETH/USDT produces 35 trades at 20% WR, -$171 PnL. This single instrument erases the combined gains of 6 other instruments. The TA composite score has no predictive edge on ETH at H1 timeframe. ETH has been a consistent loser across multiple rounds: -$245 in P1, -$26 in r3, -$171 in r4.
- **Required behavior:** Two options:
  - **Option A (recommended):** Add `"ETH/USDT"` to a new `BLOCKED_INSTRUMENTS` list in config.py. Both live SignalEngine and BacktestEngine must skip this instrument entirely. This is the cleanest approach -- explicitly acknowledging the system has no edge on ETH.
  - **Option B:** Raise `INSTRUMENT_OVERRIDES["ETH/USDT"]["min_composite_score"]` to 30+ and add `"allowed_regimes": ["STRONG_TREND_BULL"]`. This effectively blocks most ETH signals while keeping the override architecture. Less explicit than Option A.
- **Acceptance:** Backtest r5 shows 0 ETH/USDT trades. Total PnL improves by approximately +$171. PF rises from 1.09 to ~1.21+.
- **Data reference:** by_symbol: ETH/USDT 35 trades, 20% WR, -$171.11. Config: `INSTRUMENT_OVERRIDES["ETH/USDT"]` at lines 163-166 of config.py.
- **Estimated PF impact:** +0.12 (from 1.09 to ~1.21)

### REQ-V6-R4-002: Fix DXY Filter -- Pass Real RSI Data to Backtest Pipeline
- **Priority:** Critical
- **Problem:** The DXY filter shows only 2 rejections across 92,033 raw signals in 21 months. This was identified as a bug in r3 (backtest_engine.py hardcodes `dxy_rsi: None`). Despite real DXY data being collected and available, it is not being passed to the filter pipeline. In a 21-month period where DXY RSI oscillates regularly above 55 and below 45, the expected rejection count is 100-300 for USD-correlated forex pairs.
- **Required behavior:**
  1. Load DXY (DX-Y.NYB) H1 price_data at backtest start, same as D1 data is loaded for d1_data_cache.
  2. Pre-compute DXY RSI(14) for each candle timestamp using Wilder's method.
  3. During the candle loop, look up the nearest DXY RSI value for the current candle_ts.
  4. Pass the DXY RSI value as `filter_context["dxy_rsi"]` instead of `None`.
  5. DXY filter should block LONG for EURUSD/GBPUSD/AUDUSD/NZDUSD when DXY RSI > 55, block SHORT when DXY RSI < 45.
- **Acceptance:** Backtest r5 shows significantly more than 2 dxy_filter rejections (expected: 50-200). Forex LONG PnL improves due to blocking entries during dollar-strength periods.
- **Data reference:** filter_stats: dxy_filter = 2 rejections. Previous r3 finding #4. Code: backtest_engine.py context dictionary where dxy_rsi is set.
- **Estimated PF impact:** +0.05 to +0.12 depending on how many losing forex trades are blocked

### REQ-V6-R4-003: Reduce Instrument Universe to Proven Performers
- **Priority:** High
- **Problem:** 7 of 10 instruments are either net negative or have too few trades to be statistically meaningful. Trading these instruments adds noise and losses without contributing edge.
- **Required behavior:** Restrict the backtest instrument universe to ONLY instruments with demonstrated positive PnL or credible signal quality:
  - **KEEP:** GC=F (+$150, 40 trades), EURUSD=X (+$52, 24 trades), USDCAD=X (+$35, 23 trades)
  - **PROBATIONARY (keep with higher threshold):** BTC/USDT (+$51, but only 2 trades -- too few to validate), SPY (+$35, but only 2 trades)
  - **BLOCK:** ETH/USDT (-$171), AUDUSD=X (-$12, 3 trades), USDJPY=X (-$0.55, 1 trade), NZDUSD=X (+$12, 1 trade -- insufficient sample), GBPUSD=X (+$7, 6 trades at 16.7% WR)
- **Implementation:** Add a `BACKTEST_INSTRUMENT_WHITELIST` config option. When set, only whitelisted instruments are simulated. Default: `["GC=F", "EURUSD=X", "USDCAD=X", "BTC/USDT", "SPY"]`.
- **Acceptance:** Backtest r5 runs only the whitelisted instruments. Total trades decrease but PnL concentration warning should improve as GC=F share decreases relative to remaining instruments.
- **Data reference:** Full by_symbol table in analysis report Section 3.
- **Estimated PF impact:** +0.03 to +0.08 (removing small losers)

### REQ-V6-R4-004: Investigate and Document r3-to-r4 Trade Count Discrepancy
- **Priority:** Medium
- **Problem:** r3 reported 182 trades; r4 reports 139 trades for the same period and "no signal logic changes." A 43-trade difference (24% fewer) cannot be explained by performance optimizations alone. The most likely cause is TIME_EXIT_CANDLES["H1"] changing from 24 (r3 calibration) to 48 (r4/default), but this needs explicit confirmation.
- **Required behavior:**
  1. Document which configuration parameters differ between r3 and r4 runs.
  2. If TIME_EXIT_CANDLES is the cause, explicitly note this as a parameter change, not just a "performance optimization."
  3. Run a diagnostic: r4 with TIME_EXIT_CANDLES["H1"] = 24 to verify trade count matches r3.
- **Acceptance:** Written explanation of the 43-trade discrepancy with evidence.
- **Data reference:** r3: 182 trades, r4: 139 trades, config.py line 97: `_TIME_EXIT_CANDLES = {"H1": 48, ...}`

### REQ-V6-R4-005: Prepare Architecture Decision Document
- **Priority:** Critical (Decision)
- **Problem:** This is the final iteration within the current TA-only H1 architecture. If r5 does not achieve PF >= 1.3, WR >= 25%, DD <= 25%, the system must pivot. The architect needs to prepare the decision framework NOW so that r5 results can be immediately evaluated against predetermined criteria.
- **Required behavior:** Create `docs/ARCHITECTURE_DECISION.md` with:
  1. **Go criteria (stay with current approach):** PF >= 1.3, WR >= 25%, DD <= 25%, no single instrument > 50% of PnL
  2. **Pivot options (ordered by feasibility):**
     - **A: Switch to D1 timeframe** -- same TA scoring but D1 entries. Average trade duration already suggests D1 horizon. Requires minimal code changes but full re-backtest.
     - **B: Narrow to single strategy on GC=F + USDCAD** -- abandon multi-instrument and optimize for 2 proven instruments. Effectively becomes a gold trend + CAD carry strategy.
     - **C: Replace composite scoring with pattern-based signals** -- instead of averaging 8 indicators, identify specific high-conviction patterns (e.g., RSI divergence + MA cross + volume spike). Requires significant refactoring of TAEngine.
     - **D: Add ML layer** -- use the existing TA scores as features for an ML classifier trained on historical outcomes. Requires labeled dataset (which we now have from 6 rounds of backtesting).
  3. **Hard stop:** If r5 PF < 1.2, skip directly to pivot. If PF 1.2-1.29, one more calibration round allowed.
- **Acceptance:** Document exists, reviewed by product owner, with agreed Go/NoGo thresholds.
- **Data reference:** 6 rounds of backtest results, all in analyst-reports/

### REQ-V6-R4-006: SHORT System -- Accept LONG-Only or Fix
- **Priority:** Low
- **Problem:** Only 7 SHORT trades out of 139 (5%). The combination of TREND_BEAR/STRONG_TREND_BEAR regime blocks, SHORT_SCORE_MULTIPLIER 1.3, and SHORT_RSI_THRESHOLD 30 has killed SHORT trading. This was intentional (SHORT was net negative), but it means the system has zero hedging capability.
- **Required behavior:** Formal decision:
  - **Option A (recommended for r5):** Accept LONG-only. Remove SHORT logic complexity. Set `SHORT_ENABLED = False` in config. This simplifies the system and makes the LONG-only nature explicit.
  - **Option B:** If pivot to D1 (REQ-V6-R4-005 Option A), re-evaluate SHORT at D1 timeframe where trend signals may be more reliable for both directions.
- **Acceptance:** Config explicitly declares LONG-only mode, or SHORT parameters are documented as "effectively disabled -- pending D1 evaluation."
- **Data reference:** LONG: 132, SHORT: 7 trades. SHORT config: SHORT_SCORE_MULTIPLIER=1.3, SHORT_RSI_THRESHOLD=30

---

## Priority Matrix for Final Iteration (r5)

| REQ | Priority | Effort | Expected PF Impact | Must-Have for 1.3? |
|-----|----------|--------|--------------------|--------------------|
| REQ-V6-R4-001 (Block ETH) | Critical | Low | +0.12 | YES |
| REQ-V6-R4-002 (Fix DXY) | Critical | Medium | +0.05 to +0.12 | YES |
| REQ-V6-R4-003 (Whitelist) | High | Low | +0.03 to +0.08 | Helpful |
| REQ-V6-R4-004 (Discrepancy) | Medium | Low | 0 (diagnostic) | No |
| REQ-V6-R4-005 (Decision doc) | Critical | Low | 0 (governance) | YES |
| REQ-V6-R4-006 (SHORT) | Low | Low | 0 (cleanup) | No |

**Minimum viable set for r5:** REQ-V6-R4-001 + REQ-V6-R4-002 + REQ-V6-R4-005

**Combined estimated PF with REQ-001 + REQ-002:** 1.09 + 0.12 + 0.08 = ~1.29 to 1.33

**Margin of error:** Tight. If DXY filter blocks fewer than ~25 losing forex trades, PF will remain below 1.3. The architect should consider REQ-V6-R4-003 (instrument whitelist) as insurance.

---

## Honest Assessment

The system is being optimized within an architecture that has a low ceiling. Even if all requirements are implemented perfectly and PF reaches 1.3, the system remains:
- Dependent on GC=F (gold) for majority of profits
- Functionally LONG-only
- Operating at H1 with D1 predictive horizon (inefficient capital usage)
- At 24.82% WR (below the break-even threshold for 2:1 R:R)

Reaching PF 1.3 would be a "minimum viable" achievement, not a sign of genuine alpha. The architect should treat r5 as both a calibration attempt AND a data collection exercise for the pivot decision. Regardless of r5 outcome, the architecture decision document (REQ-V6-R4-005) should be prepared.
