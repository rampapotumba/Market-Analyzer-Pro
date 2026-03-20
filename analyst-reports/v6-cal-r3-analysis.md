# Analysis Report: v6 Calibration Round 3 — First Real Historical Data Backtest

## Date: 2026-03-20
## Analyst: market-analyst agent
## Run ID: 9a95980b-a93d-42b1-98c9-8ce4b538add2
## Period: 2024-04-01 to 2025-12-31 (21 months)
## Data: REAL historical Fear&Greed, DXY, Funding Rates, COT, 453 economic calendar events

---

## Executive Summary

This is the first backtest with real historical external data (Fear&Greed, DXY, Funding Rates, COT, and 453 economic calendar events) and a shortened 21-month window. **The results are sobering.** The system produces PF 1.09, WR 19.4%, and a catastrophic 53.8% max drawdown. With all filters active and real data, the system is indistinguishable from a random entry strategy with slightly positive skew. The entire positive PnL ($138) depends on a single instrument (GC=F at $226, or 163% of total PnL), while 7 out of 10 instruments are net losers. The system lost $462 from its $1000 starting balance before recovering on two large GC=F wins in mid-2025. This is not a viable trading system in its current form.

---

## 1. Regression Analysis: Why PF Dropped from 1.28 to 1.09

### Comparison Table

| Metric | v5 (24mo, no data) | cal-r1 (24mo, no data) | cal-r2 (24mo, no data) | **cal-r3 (21mo, REAL data)** |
|--------|-----|--------|--------|---------|
| Trades | 33 | 250 | 239 | **182** |
| Win Rate | 45.5% | 12.6% | 23.6% | **19.4%** |
| PF | 2.01 | 1.72 | 1.28 | **1.09** |
| Total PnL | +$253 | +$1,843 | +$533 | **+$138** |
| Max DD | 10.3% | 33.7% | 26.6% | **53.8%** |
| LONG/SHORT | 14/19 | — | — | **171/11** |

### Contributing Factors

**Factor 1: Shorter window removed 3 profitable months (Jan-Mar 2024).**
The cal-r2 period was 2024-01-01 to 2025-12-31 (24 months). Cal-r3 starts at 2024-04-01 (21 months). If Q1-2024 was net positive in previous runs, this mechanically reduces total PnL by ~12.5% of the trading window.

**Factor 2: Real calendar data blocks 135 signals that previously passed.**
With 453 HIGH-impact economic events and a +/-4h window, the calendar filter now has teeth. 135 rejections vs 0 when the calendar was empty. Some of these blocked signals were likely profitable entries near volatile news events. This is a double-edged sword: the filter correctly avoids news risk, but news events ARE where the largest moves occur.

**Factor 3: Real data likely changed regime classifications.**
With real DXY data, the regime detector may classify periods differently. The DXY filter itself shows 0 rejections (dxy_rsi passed as None in backtest context — see code line 1355: `"dxy_rsi": None`). This means the DXY filter is NOT actually running despite real DXY data being collected. **This is a bug or oversight.**

**Factor 4: Scoring dynamics with real F&G and Funding Rate adjustments.**
Fear&Greed and Funding Rate modify composite scores for crypto pairs. With real data:
- F&G <= 20 adds +5 to LONG BTC/ETH composite
- F&G >= 80 adds +5 to SHORT BTC/ETH composite
- Funding Rate extremes apply -10 penalty

These adjustments likely caused some crypto signals to fall below thresholds or shift direction at inopportune moments.

**Verdict: The PF drop is primarily caused by the system being fundamentally weak, with the previous "no data" runs being artificially inflated by graceful degradation (filters passing everything through).**

---

## 2. Drawdown Analysis: 53.8% is Catastrophic

### Equity Curve Breakdown

| Period | Balance Range | Event |
|--------|--------------|-------|
| Apr 2024 start | $1,000 | Starting balance |
| May 20, 2024 | $1,148 | Peak 1 (+14.8%) |
| Jun 13, 2024 | $869 | **3 trades closed same bar** ($-106 in single timestamp) |
| Jul 30, 2024 | $703 | Continued decline, -$445 from peak |
| Aug-Dec 2024 | $703 -> $585 | Slow bleed, 0.5-1% per month |
| Jan 4, 2025 | $683 | Brief spike (GC=F win?) then immediate -$117 |
| Mar 31, 2025 | $537 | **Absolute trough: -$611 from peak (-53.8%)** |
| Apr 11, 2025 | $1,022 | **Single trade: +$437 (GC=F?)** - recovery |
| Jul 16, 2025 | $1,543 | **Peak 2 (GC=F rally)** |
| Oct 7, 2025 | $1,186 | Drawdown from peak 2: -23% |
| Dec 31, 2025 | $1,138 (excl eod) | Settling |

### Critical Observations

1. **June 13, 2024 — $106 loss in a single timestamp.** Three positions closed at 15:00 UTC on the same candle. This suggests correlated positions opened simultaneously or correlation guard failure. Balance dropped from $987 to $869 in one bar.

2. **The system was underwater for 11 consecutive months** (Jun 2024 — Apr 2025). No live trader would survive this psychologically or financially.

3. **Two massive GC=F trades in Apr and Jul 2025 are responsible for the entire recovery.** Without these two trades, the system would have ended at approximately $600 — a 40% loss.

4. **127 out of 182 trades exit via time_exit.** This means 70% of positions sit for 24 hours doing nothing, then close at a loss. The system enters, the market does not move in the expected direction, and the time exit cuts the position. This is not a trading strategy — it is a random entry with a time-based stop.

---

## 3. Instrument Concentration: GC=F Dependency

| Instrument | Trades | WR | PnL | % of Total PnL |
|-----------|--------|-----|-----|----------------|
| GC=F | 42 | 11.9% | +$226 | **163%** |
| BTC/USDT | 2 | 50% | +$32 | 23% |
| SPY | 2 | 50% | +$16 | 12% |
| NZDUSD=X | 3 | 33% | +$9 | 7% |
| USDJPY=X | 4 | 25% | -$3 | -2% |
| GBPUSD=X | 8 | 12.5% | -$9 | -7% |
| ETH/USDT | 32 | 18.8% | -$26 | -19% |
| AUDUSD=X | 7 | 28.6% | -$28 | -20% |
| USDCAD=X | 36 | 25% | -$29 | -21% |
| EURUSD=X | 44 | 18.2% | -$50 | -36% |

### Assessment

- GC=F has a **11.9% win rate** but generates 163% of PnL. This means 5 winning trades out of 42 each had massive payoffs relative to losses. The R:R ratio on GC=F winners must be extremely high (likely 10:1+ on some trades). This is a lottery ticket pattern, not systematic alpha.

- **If GC=F is removed, the remaining 140 trades produce -$88 PnL.** The system without GC=F is a net loser.

- GC=F works because gold had a strong bull trend in 2024-2025 and the LONG-biased system (171 LONG vs 11 SHORT) happened to catch some of those moves with large ATR-based TP levels.

- **This is survivorship bias in instrument selection, not genuine signal quality.**

---

## 4. ETH/USDT Collapse: From +$812 to -$26

In cal-r1 (no historical data), ETH produced +$812 from far more trades. In cal-r3 (real data):

### Root Cause Analysis

1. **Fear & Greed filter now has real values.** When F&G is 20-80 (neutral zone), no adjustment is applied. When extreme, it adds/subtracts 5 points. In a 21-month window, extreme readings are rare (maybe 10-20% of days). Most of the time F&G does nothing.

2. **Funding Rate extreme filter (-10 penalty) kills crypto signals.** When FR > +0.1% (common during bull markets), LONG composite gets -10 penalty. Since BTC bull runs often coincide with high funding rates, the filter blocks LONG entries exactly when crypto is trending up. **This filter may be counterproductive for crypto trend-following.**

3. **BTC/USDT went from 102 trades to 2.** The min_composite_score=25 + allowed_regimes=["STRONG_TREND_BULL"] override (config.py line 157-161) is extremely restrictive. Combined with real F&G and FR data that modify scores, almost no signal passes.

4. **ETH/USDT at min_score=20 gets 32 trades** but WR drops to 18.8%. The TA-only scoring system generates noise signals for crypto that barely exceed the threshold but have no real predictive power.

### Conclusion

The previous ETH profitability was an artifact of filter-less operation (graceful degradation passing everything through). With real data, the system has no edge on ETH.

---

## 5. Calendar Filter Impact Assessment

**135 signals blocked** by the calendar filter (0.14% of raw signals). This is a relatively small number because:
- Score threshold already rejects 92.1% (86,556 signals)
- Session filter rejects 5,497
- Calendar runs after both of these, seeing only pre-filtered signals

### Quality Assessment

The calendar filter blocks signals within +/-4h of HIGH-impact events. With 453 events over 21 months, that is roughly 21 events/month, creating blocked windows of 8h * 21 = 168 hours/month out of ~720 total hours (23% of time). For H1 signals on forex pairs, this is significant.

**Problem:** The +/-4h window may be too aggressive for H1 timeframe. An H1 signal 3.5 hours before NFP is blocked, but that signal might have been valid if the trade was entered and hit TP before the news. The current implementation does not distinguish between "signal would have been caught by news volatility" and "signal was independent of news."

**Without a control group (same run without calendar filter), we cannot measure impact.** Recommendation: run a comparison backtest with apply_calendar_filter=false.

---

## 6. Regime Analysis: VOLATILE is Negative

| Regime | Trades | Wins | WR | PnL |
|--------|--------|------|-----|-----|
| STRONG_TREND_BULL | 85 | 17 | 20% | +$171 |
| VOLATILE | 95 | 18 | 19% | -$33 |

### Assessment

- Both regimes have nearly identical win rates (~19-20%).
- STRONG_TREND_BULL is marginally profitable. VOLATILE is marginally negative.
- The difference is likely in average win size, not win rate.
- Blocking VOLATILE would remove 95 trades and save $33 in losses, but:
  - Remaining 85 trades in 21 months = 4 trades/month
  - This further increases concentration risk on fewer signals
  - Some VOLATILE regime periods in gold produced the large wins

**Recommendation:** Do NOT block VOLATILE globally. Instead, consider blocking VOLATILE only for forex pairs (where it reliably produces noise) while keeping it for commodities (GC=F) where volatile periods coincide with trend continuations.

---

## 7. System Viability Assessment

### Minimum Thresholds for Live Trading

| Metric | Minimum Viable | Current | Status |
|--------|---------------|---------|--------|
| Profit Factor | >= 1.5 | 1.09 | FAIL |
| Win Rate | >= 25% (with 2:1 R:R) | 19.4% | FAIL |
| Max Drawdown | <= 20% | 53.8% | CRITICAL FAIL |
| Monthly profitable | >= 60% | 9/21 (43%) | FAIL |
| Instrument diversification | No single instrument > 30% of PnL | GC=F at 163% | CRITICAL FAIL |
| Trades/month | >= 20 | 8.7 | MARGINAL |
| Consecutive losing months | <= 3 | 9 (Jun-Feb) | CRITICAL FAIL |

### Honest Verdict

**The system is not viable for live trading.** With a PF of 1.09, transaction costs in a real trading environment (spread, slippage beyond model, platform fees) would push the system to break-even or negative. A 53.8% drawdown means a live account would be margin-called or manually stopped well before recovery. The 9-month losing streak is psychologically and financially unsustainable.

---

## 8. Score Threshold Analysis

The score_threshold filter rejects 86,556 out of 93,934 raw signals (92.1%). The effective threshold in backtest mode:

```
effective_threshold = MIN_COMPOSITE_SCORE * max(available_weight, AVAILABLE_WEIGHT_FLOOR)
                    = 15 * max(0.45, 0.65)
                    = 15 * 0.65 = 9.75
```

With composite = TA_WEIGHT * ta_score = 0.45 * ta_score:
- To pass threshold: |0.45 * ta_score| >= 9.75
- Required: |ta_score| >= 21.67

This means only signals with ta_score > 21.67 (out of max ~100) pass. This is about the 78th percentile of TA scores. **92.1% rejection rate seems reasonable** — the issue is not that too many signals pass, but that the signals that DO pass have no predictive value (19.4% WR with 2:1 R:R implies random entries would produce ~33% WR at 2:1 R:R... the system is WORSE than random).

---

## 9. Exit Reason Analysis

| Exit Reason | Count | % of Total | Implication |
|-------------|-------|-----------|-------------|
| time_exit | 127 | 69.8% | Market does not move in direction after entry |
| trailing_stop | 16 | 8.8% | Worked correctly — locked profit |
| tp_hit | 15 | 8.2% | Clean wins |
| sl_hit | 11 | 6.0% | Clean losses |
| mae_exit | 11 | 6.0% | Early cut — saved from full SL |
| end_of_data | 2 | 1.1% | Excluded from metrics |

### Critical Finding: 70% Time Exits

**127 out of 182 trades are closed by time_exit** — meaning the position sat for 24 H1 candles (1 day) without profit and was force-closed. This is the single most damning statistic:

- The TA scoring system generates directional signals, but the market does not move in that direction within 24 hours.
- The signal has no timing edge — it may identify the correct direction on a multi-day basis, but the entry timing is poor relative to the H1 timeframe.
- Time exits are all losses or break-evens (by definition: `unrealized_move <= 0`).

**This suggests a fundamental mismatch between signal timeframe (H1 entry) and signal predictive horizon (possibly D1 or longer).**

---

## 10. Weekday and Hour Analysis

### By Weekday (entry day)

| Day | Trades | Wins | PnL | WR |
|-----|--------|------|-----|-----|
| Monday (0) | 25 | 1 | -$399 | 4.0% |
| Tuesday (1) | 10 | 3 | +$460 | 30.0% |
| Wednesday (2) | 53 | 15 | +$426 | 28.3% |
| Thursday (3) | 52 | 11 | -$274 | 21.2% |
| Friday (4) | 40 | 5 | -$75 | 12.5% |

**Monday is catastrophic** — 25 trades, 1 win, -$399. The WEAK_WEEKDAY_SCORE_MULTIPLIER of 1.5x is not enough. Monday signals should be blocked entirely for forex.

Tuesday and Wednesday are the only profitable days. The system could improve substantially by restricting entries to Tuesday-Wednesday only, but this would reduce trades to ~63/21mo (3/month).

### By Hour

Hours with 0% WR: 3, 5, 11, 20, 0, 4 — all off-hours.
Hours with high WR: 6 (50%), 7 (50%), 12 (50%), 22 (40%), 23 (50%) — mostly low sample sizes.
The busiest hours (14-17 UTC, NY session) have 14-25% WR — below average.

---

## 11. MAE Analysis

| Metric | Value |
|--------|-------|
| Avg MAE % of SL (all) | 31.03% |
| Avg MAE % of SL (winners) | 59.13% |
| Avg MAE % of SL (losers) | 23.81% |

**Winners have higher MAE than losers (59% vs 24%).** This is counterintuitive and reveals a problem:
- Winners are reaching 59% of SL before turning profitable — they are barely surviving the drawdown before reversing.
- Losers are exiting via time_exit at only 24% of SL — the market is not moving enough in either direction. These are not SL hits, they are stagnation.

This confirms the "no edge" hypothesis: the system enters, the market does not respond, and time exit forces a close.

---

## 12. DXY Filter Bug

**Code line 1355 in backtest_engine.py:**
```python
"dxy_rsi": None,  # DXY not available in backtest without external data
```

Despite collecting real DXY historical data, the backtest hardcodes dxy_rsi as None. The DXY filter therefore passes all signals via graceful degradation. **The DXY data collected for this backtest is not being used.**

**Impact estimate:** The DXY RSI filter blocks LONG EURUSD/GBPUSD/AUDUSD/NZDUSD when DXY is strong (RSI > 55). In 2024-2025, DXY was predominantly strong (dollar bull trend through Q3-Q4 2024). This filter could have blocked many of the losing LONG forex trades. Potential improvement: 5-15% reduction in losing forex trades.

---

## 13. Fundamental Question: Strategic Direction

### Option A: Continue Calibrating

**Against:** Six rounds of calibration (v5 P1/P2/P3, v6 cal-r1/r2/r3) have produced a system that went from PF 2.01 (33 trades, no external data) to PF 1.09 (182 trades, real data). Each round adds complexity and reveals that previous improvements were artifacts. This is a clear case of **diminishing returns approaching zero.** The TA-only scoring system does not have predictive power at H1 timeframe.

### Option B: Fundamentally Rethink Signal Generation

**Assessment:** The core problem is that the TA scoring system generates directional signals with no timing edge. Adding ML would require labeled training data (which signals were "good") — but with 19.4% WR, there is not enough positive signal to train on. ML on noise produces noise.

**A more productive approach:** Instead of scoring all TA indicators and combining them, focus on a single high-conviction setup (e.g., trend continuation after pullback to key level) and test only that setup.

### Option C: Focus on GC=F + STRONG_TREND_BULL Only

**Assessment:** This is viable as a short-term pragmatic solution but is not a real trading system:
- GC=F had a historic bull run in 2024-2025. This is not repeatable.
- 42 trades in 21 months with 11.9% WR = 5 wins total. This is not statistically significant.
- A single instrument system has no portfolio diversification.
- The "edge" on GC=F is likely: high ATR * high R:R multiplier * strong trend = occasional massive wins that offset many small losses. This works in trending markets and fails completely in ranging ones.

### Option D: Recommended Path Forward

**Reduce scope to what the data supports:**

1. **Acknowledge the TA composite score has no predictive edge at H1 for most instruments.** 19.4% WR with the current setup is below random (which would give ~33% at 2:1 R:R). The system is actively anti-predictive on some instruments.

2. **Shift to D1/H4 timeframe.** The 70% time_exit rate at H1 suggests the signals have multi-day horizons. Testing at D1 with wider SL/TP may reveal edge that exists but is lost to noise at H1.

3. **Reduce instrument universe to 3-4 instruments** where the system shows any hint of edge: GC=F, USDCAD=X (25% WR despite being net negative), and possibly one crypto.

4. **Fix the DXY filter bug** — it was implemented but never actually used in backtest. This alone could improve forex results.

5. **Test a mean-reversion variant** alongside trend-following. The current system is 94% LONG (171/182). If the scoring naturally produces LONG bias, it may work better as a mean-reversion SHORT system during overbought conditions.

6. **Set an acceptance criterion before further development:** If the system cannot achieve PF >= 1.3, WR >= 25%, DD <= 25% on a 24-month out-of-sample test within the next 2 calibration rounds, the project should pivot to a fundamentally different approach.

---

## Findings

### Finding 1: System Has No Statistical Edge
- **Severity:** Critical
- **Description:** 19.4% WR with ~2:1 R:R (PF 1.09) is below the break-even threshold for a live trading system. After accounting for real-world costs (spread, slippage, fees), this system would be net negative.
- **Evidence:** 182 trades, $138 total PnL, 127/182 exit via time_exit (no price movement in expected direction).
- **Impact:** System cannot be deployed live without fundamental changes.

### Finding 2: Catastrophic Drawdown (53.8%)
- **Severity:** Critical
- **Description:** The system draws down over half of its account value. 9 consecutive losing months (Jun 2024 — Feb 2025). No trader or fund would tolerate this.
- **Evidence:** Equity curve drops from $1,148 peak to $537 trough over 10 months.
- **Impact:** Any live deployment would be stopped by risk management well before reaching the eventual recovery point.

### Finding 3: Single-Instrument Dependency (GC=F = 163% of PnL)
- **Severity:** Critical
- **Description:** Remove GC=F and the remaining 140 trades lose $88. The system is a gold momentum strategy disguised as a multi-instrument system.
- **Evidence:** by_symbol data — only GC=F (+$226), BTC/USDT (+$32), and SPY (+$16) are positive.
- **Impact:** System cannot be marketed or relied upon as a multi-instrument signal generator.

### Finding 4: DXY Filter Not Using Real Data in Backtest
- **Severity:** Major
- **Description:** Despite collecting real DXY historical data, backtest_engine.py hardcodes `dxy_rsi: None` (line 1355). The DXY filter is therefore non-functional in backtest.
- **Evidence:** Code line 1355: `"dxy_rsi": None, # DXY not available in backtest without external data` — but real DXY data IS now available.
- **Impact:** Forex LONG signals during dollar strength periods are not being filtered, contributing to forex losses.

### Finding 5: 70% of Trades Exit Via Time Exit
- **Severity:** Major
- **Description:** 127 out of 182 trades are closed because the position spent 24 hours without positive movement. This indicates a fundamental timing problem with entry signals.
- **Evidence:** time_exit_count: 127, avg_loss_duration: 4,411 minutes (~3 days), avg_win_duration: 12,037 minutes (~8 days).
- **Impact:** The majority of capital is deployed in positions that go nowhere. The system incurs swap costs and opportunity cost on these positions.

### Finding 6: Monday Entries are Catastrophic
- **Severity:** Major
- **Description:** 25 Monday entries produced 1 win and -$399 loss. The weekday multiplier (1.5x) is insufficient.
- **Evidence:** by_weekday data: Monday WR 4.0%, PnL -$399.
- **Impact:** Monday entries alone account for 289% of the total PnL in losses.

### Finding 7: SHORT System Non-Functional
- **Severity:** Major
- **Description:** Only 11 SHORT trades out of 182 (6%). SHORT signals are effectively eliminated by the combination of SHORT_SCORE_MULTIPLIER (1.3x), SHORT_RSI_THRESHOLD (< 30), and TREND_BEAR/STRONG_TREND_BEAR regime blocks.
- **Evidence:** short_count: 11, win_rate_short: 18.18%, strong_sell bucket: 4 trades, 0 wins, -$47.
- **Impact:** The system has no hedging capability and is fully directionally exposed to LONG positions.

### Finding 8: Score Threshold Allows Anti-Predictive Signals
- **Severity:** Major
- **Description:** The "buy" bucket (score 10-15, scaled) produces 137 trades at $452 PnL, but the "sell", "weak_buy", and "strong_sell" buckets combined produce -$117. The threshold allows through weak signals that actively lose money.
- **Evidence:** by_score_bucket data — sell: 7 trades -$42, weak_buy: 9 trades -$28, strong_sell: 4 trades -$47.
- **Impact:** Tightening the threshold to allow only "strong_buy" and "buy" (positive) would improve PnL by ~$117 but reduce trades to ~162.
