# Requirements: Backtest Methodology Standards
## Date: 2026-03-20
## From: market-analyst
## To: architect

## Background

Analysis of 6 backtest rounds (v5 Baseline through r5/v6) revealed that backtest results are
unreliable, non-reproducible across time windows, and insufficient for making strategy decisions.
Key evidence:

- v5 Phase 2 showed PF 2.01 on 33 trades (2024-01 to 2025-12), but r4/v6 on a slightly
  different window (2024-04 to 2025-12) with additional fixes showed PF 1.09 on 139 trades.
  The same codebase produced wildly different results on overlapping periods.
- GC=F went from +$150 (40 trades, 22.5% WR) in r4 to -$7.69 (27 trades, 14.8% WR) in r5
  with the only change being the instrument universe (path dependence through correlation guard).
- All v5 rounds show regime=UNKNOWN for 100% of trades, meaning regime-based filtering was
  never validated.
- 33 trades over 24 months (1.4/month) is statistically meaningless for any conclusion about
  edge existence.
- Baseline, Phase 2, and Phase 3 produced IDENTICAL results (33 trades, PF 2.01, $253.73)
  despite adding 8 new filters, proving filters had zero effect in backtest.

This document defines requirements for a trustworthy backtest methodology.

---

## Requirements

### REQ-BT-001: Minimum Sample Size
- Priority: Critical
- Problem: Current backtests produce 33-139 trades. At 33 trades with 45% WR, the 95% confidence
  interval for true WR is [28%, 63%] (binomial). The system cannot distinguish a 28% WR loser
  from a 63% WR winner. PF of 2.01 on 33 trades has a standard error of ~0.8, meaning the true
  PF could easily be below 1.0.
- Required behavior:
  - Minimum 100 trades per instrument-strategy combination for any statistical claim
  - Minimum 200 trades total per backtest run for aggregate metrics
  - If a backtest produces fewer trades than minimum, the summary must display
    `statistical_confidence: "INSUFFICIENT"` and all metrics should carry a warning
  - Display confidence intervals (95%) for WR and PF in summary output
- Acceptance: Backtest summary includes `sample_adequacy` field with trade count assessment,
  CI bounds for WR and PF, and a pass/fail flag based on minimum thresholds
- Data reference: All 6 backtest rounds have N < 200 total trades; v5 rounds have N=33

### REQ-BT-002: Walk-Forward Validation
- Priority: Critical
- Problem: All backtests run on a single in-sample period. There is no out-of-sample validation.
  Parameters tuned to maximize PF on 2024-01 to 2025-12 may be worthless on 2026+ data.
  The jump from PF 2.01 (v5 P2) to PF 1.09 (r4 v6, different window) is classic overfitting.
- Required behavior:
  - Implement anchored walk-forward validation:
    - Split data into K folds (minimum K=3, recommended K=5)
    - Training window: expanding (anchored to start date)
    - Validation window: fixed size (e.g., 3 months)
    - Example with 24 months of data, 3 folds:
      Fold 1: Train 2024-01 to 2024-06, Validate 2024-07 to 2024-09
      Fold 2: Train 2024-01 to 2024-09, Validate 2024-10 to 2024-12
      Fold 3: Train 2024-01 to 2024-12, Validate 2025-01 to 2025-03
      etc.
    - Report metrics separately for each fold and aggregate
  - The strategy is considered valid only if ALL folds show PF > 1.0 and
    the aggregate out-of-sample PF > 1.2
  - Parameters must NOT be adjusted between folds (use training-window values)
- Acceptance: BacktestEngine supports `mode="walk_forward"` parameter with configurable fold
  count and window sizes. Output includes per-fold metrics table and aggregate OOS stats.
- Data reference: v5 P2 vs r4 results demonstrate failure of single-window testing

### REQ-BT-003: Backtest-Live Parity Verification
- Priority: Critical
- Problem: v5 Phase 2 and Phase 3 produced identical results (33 trades, PF 2.01) despite
  Phase 3 adding calendar filter, breakeven buffer, time exit, S/R snapping, and swap updates.
  This proves that either (a) filters were not actually applied in backtest, or (b) the test
  data never triggered them. Either way, backtest results do not represent live behavior.
  Additionally, regime=UNKNOWN for 100% of backtest trades means regime filters were never tested.
- Required behavior:
  - Every filter must log when it activates and when it passes through
  - Backtest summary must include `filter_activation_stats`:
    ```
    {
      "regime_filter": {"checked": 1500, "blocked": 340, "passed": 1160},
      "d1_trend_filter": {"checked": 1160, "blocked": 0, "note": "no D1 data loaded"},
      "volume_filter": {"checked": 1160, "blocked": 89, "passed": 1071},
      ...
    }
    ```
  - If a filter has 0 blocks, issue a warning: "Filter X never triggered -- verify data availability"
  - regime must never be UNKNOWN in backtest -- the regime detector runs on same price data
  - Add a `parity_check` mode that runs the same 100 signals through both live and backtest
    paths and compares outputs bit-for-bit
- Acceptance: No filter shows "checked: 0" or 100% passthrough without explicit warning.
  Regime is populated for every backtest trade. Parity check produces a report comparing
  live vs backtest signal decisions.
- Data reference: v5 Phase 2 vs Phase 3 identical results; by_regime shows only "UNKNOWN"

### REQ-BT-004: End-of-Data Trade Handling
- Priority: High
- Problem: The current system already excludes end_of_data trades from primary metrics (good),
  but if the backtest period ends during a strong trend, many open positions get force-closed
  and their unrealized P&L distorts perception. In r4, end_of_data trades triggered a warning
  at >5% of total trades. More importantly, the CHOICE of end date itself changes results --
  ending in a drawdown vs ending in a rally produces different aggregate metrics.
- Required behavior:
  - End-of-data trades must be reported separately with full detail
  - Primary metrics (WR, PF, PnL, DD) must ONLY use trades that closed naturally
  - Add a "sensitivity test": re-run the backtest ending 1 week, 2 weeks, 1 month earlier.
    If PF changes by more than 20%, flag the result as "period-sensitive"
  - The backtest UI should show a toggle: "Include/exclude end-of-data trades"
- Acceptance: Summary includes end_of_data section, sensitivity analysis for 3 end dates,
  and a stability flag. PF variance across end dates < 20% for "stable" classification.
- Data reference: CAL2-01 comments in backtest_engine.py; end_of_data inflation noted in code

### REQ-BT-005: Path Dependence Detection
- Priority: High
- Problem: GC=F went from +$150 in r4 (10 instruments) to -$7.69 in r5 (5 instruments).
  The only change was removing 5 instruments from the universe. This should NOT affect GC=F
  trades, but it does -- because correlation guard, position cooldowns, and capital allocation
  depend on which OTHER instruments are trading. This means backtest results for one instrument
  are not independent of the instrument universe.
- Required behavior:
  - Add an "isolation mode" backtest that runs each instrument independently (no correlation
    guard, no portfolio-level constraints) to measure raw per-instrument edge
  - Add a "portfolio mode" that runs all instruments together (current behavior)
  - Compare isolation vs portfolio results for each instrument in the summary
  - If any instrument's PnL changes by more than 30% between modes, flag as "path-dependent"
  - Document which cross-instrument interactions exist (correlation guard, capital limits,
    cooldown interactions)
- Acceptance: BacktestParams supports `isolation_mode: bool`. Summary includes both isolated
  and portfolio results side-by-side. Path dependence flag per instrument.
- Data reference: GC=F r4 vs r5 collapse; correlation guard in portfolio_risk.py

### REQ-BT-006: Statistical Significance Testing
- Priority: High
- Problem: No backtest run includes any statistical test. We cannot say whether PF 2.01 is
  significantly different from PF 1.0 (no edge). With 33 trades, it almost certainly is not.
- Required behavior:
  - For every backtest, compute and report:
    - **t-test on trade returns**: H0 = mean return = 0. Report p-value.
    - **Bootstrap confidence interval for PF**: Resample trades with replacement 10000 times,
      report 5th and 95th percentile PF.
    - **Sharpe ratio** (annualized) with standard error
    - **Sortino ratio** (downside deviation only)
    - **Maximum consecutive losses** and probability under random (binomial)
  - A strategy is "statistically significant" only if:
    - t-test p-value < 0.05
    - Bootstrap 5th percentile PF > 1.0
    - Sharpe > 0.5
  - Display these tests prominently in the summary, not buried in extended metrics
- Acceptance: Summary includes `statistical_tests` section with all tests listed,
  p-values, confidence intervals, and a composite significance verdict.
- Data reference: All rounds lack statistical testing; settings already define
  MIN_OOS_SHARPE=0.8 and MIN_OOS_PROFIT_FACTOR=1.3 but they are not computed

### REQ-BT-007: Data Integrity Verification
- Priority: High
- Problem: The backtest uses H1 candles from yfinance. Known issues:
  - Forex volume is always 0 (volume filter is bypassed for forex)
  - D1 data may not be loaded (d1_trend_filter shows 0 blocks with "no D1 data" notes)
  - Regime is UNKNOWN for all backtest trades, suggesting regime detector input is broken
  - No verification that price data is continuous (gaps on holidays, weekends, after-hours)
- Required behavior:
  - Before running backtest, run a data quality check:
    - Count candles per instrument. Report expected vs actual count for the period.
    - Detect gaps > 2x normal candle interval. Report gap locations.
    - Verify OHLC integrity: high >= max(open, close), low <= min(open, close)
    - Check for duplicate timestamps
    - Report volume availability per instrument (% of candles with volume > 0)
    - Verify D1 data availability for each instrument (required for MA200 filter)
  - If data quality issues are found, display warnings before running the backtest
  - Include a `data_quality` section in backtest summary
- Acceptance: Pre-backtest data check runs automatically. Summary includes data quality
  metrics. Known gaps are documented and their impact on results is estimated.
- Data reference: Volume filter bypassed for forex (filter_pipeline.py line 403);
  regime=UNKNOWN in all backtest results; d1_trend_filter 0 blocks

### REQ-BT-008: Benchmark Comparison
- Priority: Medium
- Problem: PF 2.01 on 33 trades sounds good, but there is no benchmark. What would
  buy-and-hold return? What would a random entry with same SL/TP return? Without benchmarks,
  we cannot know if the strategy adds value over simpler approaches.
- Required behavior:
  - For every backtest, compute benchmark returns:
    - **Buy-and-hold**: For each instrument, compute return over the same period
    - **Random entry**: Generate N random entry points with same SL/TP/position sizing rules,
      run 1000 simulations, report median PF and 95th percentile PF
    - **Inverted signals**: Run the same strategy but flip LONG<->SHORT. If inverted also
      profits, the strategy may be capturing regime rather than direction.
  - Strategy must outperform random entry 95th percentile to claim statistical edge
- Acceptance: Summary includes benchmark section with buy-hold, random, and inverted results.
  Strategy PF compared to random 95th percentile with pass/fail flag.
- Data reference: No benchmarks exist in any current backtest round

### REQ-BT-009: Regime-Aware Metrics
- Priority: Medium
- Problem: Regime is UNKNOWN for 100% of backtest trades. The system has 7 defined regimes
  with different weight sets and SL multipliers, but we have zero data on which regimes
  produce profitable trades. BLOCKED_REGIMES currently blocks 4 out of 7 regimes (RANGING,
  TREND_BEAR, STRONG_TREND_BEAR, TREND_BULL), leaving only STRONG_TREND_BULL, VOLATILE,
  and LOW_VOLATILITY. This was calibrated without regime data in backtest.
- Required behavior:
  - Fix regime detection in backtest (it already calls _detect_regime_from_df but result
    is not persisted to trade records)
  - Report WR, PF, and average R:R per regime in summary
  - Identify which regimes are profitable and which are not
  - Use regime-level metrics to validate BLOCKED_REGIMES list with data
- Acceptance: No trade has regime=UNKNOWN. by_regime in summary shows all encountered
  regimes with >= 10 trades each for statistical relevance.
- Data reference: by_regime = {"UNKNOWN": {...}} in all backtest results;
  _detect_regime_from_df exists in backtest_engine.py but regime field is not stored

### REQ-BT-010: Multi-Timeframe Backtest
- Priority: Medium
- Problem: All backtests run exclusively on H1. The system supports M1, M5, M15, H1, H4, D1,
  W1, MN1, but we have zero data on whether the strategy works on other timeframes. H1 was
  chosen arbitrarily. D1 may produce better results for trend-following.
- Required behavior:
  - BacktestParams should accept a list of timeframes
  - Run the same strategy on H4 and D1 in addition to H1
  - Compare results across timeframes in a summary table
  - Identify which timeframe produces the best risk-adjusted returns
- Acceptance: Backtest can be run on multiple timeframes in a single execution.
  Summary includes cross-timeframe comparison table.
- Data reference: All 6 rounds use H1 only; TF_INDICATOR_PERIODS supports all timeframes;
  TIME_EXIT_CANDLES has entries for H1, H4, D1

### REQ-BT-011: Transaction Cost Sensitivity
- Priority: Low
- Problem: Current backtest uses fixed slippage (1 pip forex, 0.1% crypto) and fixed
  swap rates (from 2023, potentially stale). With PF barely above 1.0 in many rounds,
  small changes in transaction costs could flip the result.
- Required behavior:
  - Run sensitivity analysis with 0x, 1x, 2x, 3x slippage multipliers
  - Report at which slippage level the strategy becomes unprofitable
  - Include spread cost estimate (not currently modeled)
  - Flag if strategy becomes unprofitable at 2x slippage (fragile edge)
- Acceptance: Summary includes slippage sensitivity table showing PF at 4 slippage levels.
  "Fragile" flag if PF < 1.0 at 2x slippage.
- Data reference: _SL_SLIPPAGE in backtest_engine.py; swap rates from 2023 in config/swap_rates.json

---

## Implementation Priority

| Requirement | Priority | Effort | Impact |
|-------------|----------|--------|--------|
| REQ-BT-001 | Critical | Low    | Prevents false conclusions from small samples |
| REQ-BT-002 | Critical | High   | Only reliable way to detect overfitting |
| REQ-BT-003 | Critical | Medium | Ensures backtest represents reality |
| REQ-BT-006 | High     | Medium | Provides mathematical edge confidence |
| REQ-BT-007 | High     | Medium | Fixes known data quality issues |
| REQ-BT-009 | Medium   | Low    | Unlocks regime-level strategy tuning |
| REQ-BT-005 | High     | Medium | Explains GC=F collapse and similar anomalies |
| REQ-BT-004 | High     | Low    | Already partially implemented |
| REQ-BT-008 | Medium   | Medium | Establishes if edge exists at all |
| REQ-BT-010 | Medium   | Medium | May find better timeframe |
| REQ-BT-011 | Low      | Low    | Stress-tests edge robustness |

## Recommended Implementation Order

1. REQ-BT-009 (fix regime in backtest -- prerequisite for everything)
2. REQ-BT-007 (data integrity check -- prerequisite for valid results)
3. REQ-BT-003 (filter parity -- ensure filters actually work)
4. REQ-BT-001 (sample size checks and CIs)
5. REQ-BT-006 (statistical significance)
6. REQ-BT-002 (walk-forward -- the big one)
7. REQ-BT-008 (benchmarks)
8. REQ-BT-005 (path dependence)
9. REQ-BT-004 (end-of-data sensitivity)
10. REQ-BT-010 (multi-timeframe)
11. REQ-BT-011 (transaction cost sensitivity)
