# Analysis Report: Backtest Round 5 — FINAL Go/NoGo Decision

## Date: 2026-03-20
## Analyst: market-analyst agent
## Run ID: 03988897-cd11-48ad-8011-ccc21af221cc
## Period: 2024-04-01 to 2025-12-31 (21 months), H1 timeframe, $1000 account
## Context: Iteration 2 of 2 (FINAL) before mandatory architectural pivot

---

## Executive Summary

Round 5 applied the maximum viable parameter-level interventions: blocked ETH/USDT (the worst performer across all rounds), whitelisted 5 instruments with demonstrated edge, and added DXY diagnostic logging. The result: PF improved from 1.09 to 1.27, drawdown dropped dramatically from 48.5% to 17.88%, but trade count collapsed from 139 to 47, win rate slipped to 23.91%, and -- critically -- GC=F, the engine behind every previous positive result, collapsed from +$150 (22.5% WR, 40 trades) to -$7.69 (14.81% WR, 27 trades). The entire system PnL of +$131.24 now depends on exactly two trades: one BTC/USDT (+$100.15) and one SPY (+$43.01). This is not a trading system -- it is two lottery tickets that happened to pay out.

**Decision: NoGo.** The system fails 3 of 4 acceptance criteria. PF 1.27 falls short of the 1.3 threshold after the second and final calibration iteration. The hard stop rule is triggered.

---

## 1. Go/NoGo Criteria Assessment

| Criterion | Threshold | r5 Actual | Status |
|-----------|-----------|-----------|--------|
| Profit Factor | >= 1.3 | 1.27 | **FAIL** |
| Win Rate | >= 25% | 23.91% | **FAIL** |
| Max Drawdown | <= 25% | 17.88% | PASS |
| PnL Concentration | No single instrument > 50% of PnL | BTC = 76% on 1 trade | **FAIL** |

**3 of 4 criteria FAIL. The system is not viable.**

### Hard Stop Rule Application

From ARCHITECTURE_DECISION.md:
- "PF < 1.2 after r5 --> Skip directly to pivot."
- "PF 1.2 to 1.29 --> One additional calibration round allowed (r6), but must demonstrate clear path to 1.3+."

PF = 1.27 falls in the 1.2-1.29 range, which technically allows one more round. However, the analyst exercises the override for the following reasons:

1. **There is no clear path to 1.3+.** The improvement from r4 (1.09) to r5 (1.27) was achieved by eliminating instruments, not by improving signal quality. Further instrument elimination leaves us with 1-2 symbols and under 20 trades -- statistically meaningless.

2. **The improvement is illusory.** PF 1.27 is driven by 2 trades out of 47 (BTC +$100, SPY +$43 = $143 of $131 total PnL). Remove either trade and PF drops below 1.0. This is not robust edge -- it is noise.

3. **GC=F collapsed without any signal logic change.** The star performer across 6 previous rounds went from +$150 to -$7.69. If the system's best instrument can swing $158 between runs with no parameter change, the system has no stable edge.

4. **An additional round would optimize on 47 data points.** Any further tuning is pure overfitting. Statistical significance requires N > 100 for meaningful inference on filter parameters.

**Verdict: Hard stop triggered. No further calibration rounds. Architecture must change.**

---

## 2. GC=F Collapse Investigation

### The Numbers

| Metric | r4 (10 instruments) | r5 (5 whitelisted) | Delta |
|--------|---------------------|---------------------|-------|
| Trades | 40 | 27 | -13 |
| Win Rate | 22.5% | 14.81% | -7.7pp |
| PnL | +$150.36 | -$7.69 | **-$158.05** |
| Wins | 9 | 4 | -5 |

### Root Cause Analysis

Code diff between r4 and r5 (commit aca694e) confirms: **no signal logic, filter thresholds, or GC=F-specific parameters were changed.** The only changes were:

1. ETH/USDT added to BLOCKED_INSTRUMENTS
2. BACKTEST_INSTRUMENT_WHITELIST set to 5 symbols
3. DXY diagnostic logging added
4. ETH/USDT override removed from INSTRUMENT_OVERRIDES

None of these directly affect GC=F signal generation or filtering.

### Hypothesis: Indirect Path Dependence

The most likely explanation is **path dependence through correlation guard and cooldown mechanics.** In r4, 10 instruments were active. The order in which instruments are iterated, combined with cooldown timers and the correlation guard (SIM-21: CORRELATED_GROUPS), means that opening a position on one instrument can block entry on another instrument for a cooldown period.

With 10 instruments, certain GC=F entry candles may have been "free" because the correlation guard was occupied blocking a different pair. With only 5 instruments, the guard frees up earlier, allowing a DIFFERENT GC=F candle to be selected -- one that turns out to be a loser.

This is a classic symptom of a **non-ergodic system**: the outcome depends on the exact sequence of events, not just the statistical properties of individual signals. It means the GC=F "edge" in r4 was partially an artifact of the specific multi-instrument interaction pattern, not intrinsic signal quality.

### Alternative Hypothesis: Random Variance

With only 40 trades in r4 and 27 in r5, the difference between 9 wins and 4 wins is well within binomial confidence intervals. At a true 20% win rate, getting 9/40 wins has p=0.14 and getting 4/27 wins has p=0.27. Both are completely consistent with a 20% base rate. The "edge" on GC=F may never have existed -- 9 wins at lottery-style payoffs created the illusion.

### Verdict

GC=F never had a stable, exploitable edge. The +$150 in r4 was a combination of (a) a strong gold bull market in 2024-2025, (b) LONG-only bias happening to align with the trend, (c) high ATR creating large TP targets, and (d) random favorable sequencing of 9 winning trades. The collapse in r5 reveals the fragility.

---

## 3. Instrument-Level Autopsy

### EURUSD=X: 0 Wins in 8 Trades

Complete failure. The TA composite score has no predictive power on EURUSD at H1. Eight consecutive losses suggest the system systematically enters at the wrong time or direction for this pair. EURUSD is the most liquid and efficient FX pair -- expecting a simple TA composite to find edge here is unrealistic.

### USDCAD=X: The Only Credible Performer

9 trades, 55.56% WR, +$23.40. This is the only instrument with a win rate above 50% on a meaningful sample. However, 9 trades in 21 months (0.43/month) is too few for statistical confidence (95% CI for WR: 21% to 86%).

### BTC/USDT and SPY: Statistically Meaningless

1 trade each, both wins. These could be coin flips. The +$143 combined PnL from 2 trades constitutes 109% of total system PnL. A system whose survival depends on 2 trades out of 47 is not a system.

### GC=F: From Star to Liability

27 trades, 14.81% WR, -$7.69. GC=F went from contributing 94% of PnL (r4) to being slightly negative. With 4 wins in 27 trades at presumably high R:R targets, the few winners could not overcome the accumulated losses. This is what a trend-following system looks like when the trend pauses or the entry timing is off.

---

## 4. System-Level Structural Failures

### Failure 1: Trade Frequency is Below Any Useful Threshold

47 trades in 21 months = 2.2 trades/month. Any serious trading system needs at least 10 trades/month for meaningful performance measurement and capital deployment. At 2.2/month, the system is dormant 90% of the time.

### Failure 2: LONG-Only Bias is Now Extreme

44 LONG vs 3 SHORT (93.6% LONG). The system is functionally a LONG-only strategy. During any market downturn, all open positions lose simultaneously with no hedge.

### Failure 3: No Instrument Has Statistical Significance

The highest trade count is GC=F at 27. For binomial testing of win rates, N=27 gives wide confidence intervals. None of the 5 instruments individually provides enough data to distinguish signal from noise.

### Failure 4: Filters Eliminated 99.89% of Signals Without Improving Quality

41,810 raw signals reduced to 47 trades (0.11% pass rate). Despite this extreme filtration, the 47 trades that survived still have only 23.91% WR. The filter cascade is not selecting high-quality signals -- it is randomly sampling from the noise with extra complexity.

### Failure 5: DXY Filter Produced Zero Rejections

Despite being the key hope for r5 improvement (identified in r4 analysis as the "only path to PF >= 1.3"), the DXY filter rejected zero signals. The diagnostic logging was added but the filter itself is functionally irrelevant when only EURUSD=X remains from DXY-affected pairs, and momentum filter already rejects most EURUSD LONG signals before DXY is checked. This confirms the r4 analyst's estimate of "+30 DXY rejections saving $150" was overly optimistic.

---

## 5. The Calibration History: A Story of Diminishing Returns

| Round | Trades | WR | PF | PnL | Key Change |
|-------|--------|-----|-----|-----|------------|
| v5-P2 | 33 | 45.5% | 2.01 | +$253 | Tight filters, no external data |
| v6-cal1 | 700 | 14.5% | 1.12 | +$1,766 | Proportional scoring, 10 instr. |
| v6-cal2 | 239 | 23.6% | 1.28 | +$533 | Threshold tuning, SHORT unlock |
| v6-cal3 (r3) | 182 | 19.4% | 1.09 | +$138 | Real external data |
| r4 | 139 | 24.8% | 1.09 | +$159 | Performance opts (no logic change) |
| **r5** | **47** | **23.9%** | **1.27** | **+$131** | **Block ETH, whitelist 5 instr.** |

Pattern: Each round that added real constraints (real data, more instruments, longer history) pushed PF toward 1.0 (no edge). Each round that removed instruments or tightened filters pushed PF up slightly but destroyed trade count. The "best" result (v5-P2, PF 2.01) came from the smallest sample (33 trades) with no external data -- the definition of overfitting.

The system oscillates between "enough trades but no edge" and "apparent edge but too few trades to validate." This is the signature of a system that does not have a real statistical edge.

---

## 6. Decision: NoGo -- Pivot Required

### Final Verdict

The TA-only composite scoring system on H1 timeframe has been exhaustively tested across 8 calibration rounds. It does not produce a stable, statistically significant edge on any instrument or combination of instruments. Further calibration is futile and would constitute overfitting to historical noise.

### Recommended Pivot: Option A (D1 Timeframe) with Elements of Option B

**Primary recommendation: Option A -- Switch to D1 Timeframe.**

Rationale:
1. **The data supports this.** Average trade duration across all rounds is 2.8-6 days. The signals are already operating on a D1 horizon despite H1 entry. Moving to D1 eliminates the timing noise from H1 entries.
2. **Lowest effort.** Same scoring engine, same filters. Change timeframe parameter, adjust TIME_EXIT_CANDLES and cooldown. Full re-backtest required but no new logic.
3. **Addresses the core failure.** 70% of trades exit via time_exit at H1 because the market does not move in 24-48 H1 candles. On D1, a 10-candle time exit = 2 weeks, which matches the actual predictive horizon.
4. **Risk is manageable.** Fewer signals (estimated 5x reduction), but the current system only produces 2.2 trades/month anyway. Even 0.5 trades/month on D1 with higher quality would be an improvement.

**Secondary element: Narrow instrument universe (Option B influence).**

Keep 3-4 instruments maximum: GC=F (gold -- demonstrated trend-following behavior despite r5 collapse), USDCAD=X (best WR consistency), and optionally BTC/USDT (if D1 signals are less noisy than H1). Drop EURUSD=X (0% WR in r5, marginal in all rounds) and SPY (insufficient data).

### What NOT to Do

- **Option C (Pattern-Based):** High effort, high overfitting risk, no evidence the underlying TA indicators contain exploitable patterns at any frequency.
- **Option D (ML):** Total labeled dataset is approximately 800 signals / 140 trades across all rounds. This is far too small for any ML approach. Gradient boosting on 140 samples with 8+ features will overfit catastrophically.
- **Further H1 calibration:** Exhausted. Eight rounds produced PF range 1.0-1.27 with no stable configuration. The H1 TA-only approach is a dead end.

---

## 7. Implementation Requirements for Architect

### REQ-PIVOT-001: Switch Backtest Timeframe to D1

- Priority: Critical
- Current state: System runs on H1, producing signals with multi-day predictive horizons
- Required: New backtest run on D1 candles with adapted parameters
- Parameters to adjust:
  - `params.timeframe = "D1"`
  - `TIME_EXIT_CANDLES["D1"] = 10` (already defined, 2 weeks)
  - Remove session_filter (not applicable to D1)
  - Remove weekday_filter (D1 candles are daily, not intraday)
  - Adjust cooldown from H1-candle-count to D1-candle-count
  - SL/TP ATR multipliers may need recalibration for D1 ATR values
- Acceptance: Backtest on same period (2024-04-01 to 2025-12-31) produces results. No PF threshold for first D1 run -- this is exploratory.

### REQ-PIVOT-002: Reduce Instrument Universe to 3

- Priority: High
- Current state: 5 whitelisted instruments with 3 producing losses
- Required: BACKTEST_INSTRUMENT_WHITELIST = ["GC=F", "USDCAD=X", "BTC/USDT"]
- Rationale: Only instruments with demonstrated positive signal quality across multiple rounds. SPY has 2 total trades ever (meaningless). EURUSD has 0% WR in r5.
- Acceptance: Backtest runs on 3 instruments only.

### REQ-PIVOT-003: Define New Go/NoGo Criteria for D1 Approach

- Priority: High
- The existing H1 criteria (PF >= 1.3, WR >= 25%, DD <= 25%) should be reviewed for D1 applicability
- D1 will produce far fewer trades -- statistical significance thresholds must be adjusted
- Recommended: minimum 30 trades for any conclusion, PF >= 1.3 maintained, WR >= 30% (D1 should have higher WR with better timing), DD <= 20%
- Maximum 2 calibration rounds before next architectural decision

### REQ-PIVOT-004: Investigate GC=F Path Dependence

- Priority: Medium
- The GC=F collapse between r4 and r5 despite no signal logic changes suggests path dependence through correlation guard / cooldown mechanics
- Required: Run r5 backtest with GC=F as the ONLY instrument (no correlation guard interference) and compare to r4 GC=F results
- This isolates whether the collapse is due to multi-instrument interaction or random variance
- Acceptance: Report showing GC=F standalone performance

---

## 8. Closing Statement

Eight calibration rounds over the course of this project have produced a clear and unambiguous conclusion: the TA-only composite scoring system at H1 timeframe does not generate a tradeable edge across multiple instruments. The system's positive PnL in every round has been driven by one or two instruments during favorable macro conditions (gold bull run 2024-2025), not by signal quality. The PF has never sustainably exceeded 1.3 on a meaningful sample size.

This is not a failure of implementation -- the code is well-structured, filters work correctly, and the backtest engine is reliable. It is a failure of the underlying premise: that averaging 8 TA indicators into a composite score and applying a threshold produces actionable signals at H1 frequency. The signal-to-noise ratio at H1 is too low for this approach.

The recommended pivot to D1 preserves the existing infrastructure while addressing the fundamental timeframe mismatch. If D1 also fails to produce edge within 2 rounds, the project should consider abandoning the composite scoring approach entirely in favor of a pattern-recognition or event-driven architecture.
