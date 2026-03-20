# Requirements: Backtest v5 Final Analysis -> v6 Planning

## Date: 2026-03-20 (updated)
## From: market-analyst
## To: architect

---

## Background

Completed deep analysis of Trade Simulator v5 backtest results across 4 phases (Baseline, Phase 1, Phase 2, Phase 3) over 24 months (2024-01 to 2025-12), 6 instruments, H1 timeframe. Found critical data integrity issues in reporting, instrument coverage failures, and structural SHORT signal weakness.

**Key actual metrics (from raw JSON, NOT from FINAL.md summary which contains errors):**
- **Baseline** (no filters, no session filter): 38 trades, PF 0.60, -$1054
- **Phase 1** (P1.md, separate run): 52 trades, PF 0.72, -$236
- **Phase 2** (all filters except calendar): 33 trades, PF 2.01, +$253.73
- **Phase 3** (all filters ON): 33 trades, PF 2.01, +$253.73 (IDENTICAL to Phase 2)
- 2 of 6 instruments produce 0 trades in filtered runs (GBPUSD, BTC/USDT)
- Trades/month: 1.4 (target: 55-75)

**Critical discrepancy:** `BACKTEST_RESULTS_FINAL.md` contains a different dataset from the individual phase files and raw JSON. The FINAL table shows Phase 3 with 30 trades / PF 2.33 / +$278.87, but actual Phase 3 JSON shows 33 trades / PF 2.01 / +$253.73. All analysis below uses the verified JSON/phase-file data.

Full analytical report: `analyst-reports/backtest-v5-final-analysis.md`

---

## Requirements

### REQ-V6-001: Fix Backtest Reporting Pipeline (Data Integrity)

- **Priority:** P0
- **Problem:** `BACKTEST_RESULTS_FINAL.md` contains data that does not match the individual phase files or raw JSON. FINAL Phase 3 shows 30 trades / PF 2.33 / +$278.87, but actual is 33 trades / PF 2.01 / +$253.73. Phase 1 in FINAL shows 34 trades / PF 0.37, but P1.md shows 52 trades / PF 0.72. `BACKTEST_RESULTS_V1.md` was overwritten with Phase 2 data. No single source of truth exists.
- **Required behavior:**
  - Implement `scripts/generate_backtest_report.py` that reads from `backtest_runs` table by run_id and produces markdown
  - FINAL comparison table must be generated programmatically from stored run IDs
  - Each report file must include the run_id, exact parameter set, and data hash
  - No manual editing of metric values in report files
- **Expected impact:** Eliminates false conclusions. All v6 decisions will be based on verified metrics.
- **Acceptance criteria:**
  - Script exists and produces correct markdown for any run_id
  - Regenerated FINAL.md matches JSON data for all phases
  - CI/CD or pre-commit hook warns if report files are manually edited
- **Data reference:** `/tmp/bt_results.json`, `/tmp/bt_Baseline.json`, `docs/BACKTEST_RESULTS_FINAL.md`

---

### REQ-V6-002: Fix Composite Score Scaling for Backtest

- **Priority:** P0
- **Problem:** In backtest, `composite = 0.45 * ta_score` because FA, sentiment, and geo components are all 0.0. This means `min_composite_score=15` requires ta_score >= 33.3, and overrides of 18-20 require ta_score >= 40-44.4. These are near-unreachable thresholds. In live mode, all 4 components contribute and composite can reach ~45. The backtest is testing a fundamentally different threshold regime than live.
- **Required behavior:** Architect must choose one approach:
  - **(A) Scale thresholds proportionally:** `effective_threshold = threshold * sum_of_available_weights`. When only TA available: `15 * 0.45 = 6.75`. Preserves live behavior.
  - **(B) Normalize composite to full range:** `composite = ta_score` (not weighted) in backtest. Simplest but changes backtest signal characteristics.
  - **(C) Separate backtest thresholds:** `BACKTEST_MIN_COMPOSITE_SCORE = 7` as explicit config. Most transparent.
  - Must document chosen approach with rationale.
- **Expected impact:** 3-5x increase in trade count. Instruments currently at 0 trades will generate signals.
- **Acceptance criteria:**
  - Total trades >= 80 in 24-month backtest (>= 3.3/month)
  - GBPUSD produces >= 3 trades
  - BTC/USDT produces >= 3 trades
  - PF remains >= 1.3 after adjustment
- **Data reference:** `src/backtesting/backtest_engine.py` line 1309: `composite = _TA_WEIGHT * ta_score`; `src/config.py` line 146: `MIN_COMPOSITE_SCORE = 15`

---

### REQ-V6-003: Fix BTC/USDT Regime Override Blocking

- **Priority:** P0
- **Problem:** BTC/USDT has `allowed_regimes: ["STRONG_TREND_BULL", "STRONG_TREND_BEAR"]` in INSTRUMENT_OVERRIDES, combined with `min_composite_score: 20`. Both conditions together are nearly impossible to satisfy. In baseline (unfiltered), BTC had 1 trade with +$427.59 -- the single most profitable trade across all instruments.
- **Required behavior:**
  - Expand `allowed_regimes` to include `TREND_BULL` and `TREND_BEAR`
  - Reduce `min_composite_score` from 20 to 15 (or implement REQ-V6-002 first)
  - Re-run backtest to verify BTC generates trades
- **Expected impact:** BTC is the most volatile instrument. Adding it to the tradeable set could significantly increase profitability.
- **Acceptance criteria:**
  - BTC/USDT produces >= 3 trades in 24-month backtest
  - BTC trades have PF >= 1.0
- **Data reference:** `src/config.py` lines 152-156, baseline JSON `by_symbol.BTC/USDT: 1 trade, +$427.59`

---

### REQ-V6-004: Fix GBPUSD Zero-Trade Problem

- **Priority:** P1
- **Problem:** GBPUSD has `min_composite_score: 20` override, requiring ta_score >= 44.4. This is never reached. GBPUSD produces 0 trades in ALL runs, including baseline.
- **Required behavior:**
  - Remove the `min_composite_score: 20` override for GBPUSD, or lower it to 15
  - After REQ-V6-002 is implemented, verify default thresholds are sufficient
- **Expected impact:** GBPUSD is the 2nd most liquid forex pair. Should contribute 5-15 trades in 24 months.
- **Acceptance criteria:**
  - GBPUSD produces >= 3 trades
  - GBPUSD trades do not degrade overall PF below 1.3
- **Data reference:** `src/config.py` line 162: `"GBPUSD=X": {"min_composite_score": 20}`

---

### REQ-V6-005: Load D1 Data in Backtest for Trend Filter

- **Priority:** P1
- **Problem:** The D1 MA200 filter (SIM-27) always passes in backtest because `d1_rows` is hardcoded as `[]`. The filter requires 200 D1 candles but receives 0, triggering graceful degradation. SIM-27 provides zero actual filtering despite being "enabled."
- **Required behavior:**
  - Pre-load D1 price data for each symbol at backtest start
  - For each signal at timestamp T, pass last 200 D1 candles ending at or before T
  - Log actual pass/block decisions, not just graceful degradation
- **Expected impact:** Counter-trend trades (LONG in bear market, SHORT in bull market) will be filtered, improving PF.
- **Acceptance criteria:**
  - `d1_rows` contains >= 200 rows when D1 data is available
  - Filter logs show pass/block decisions with close and MA200 values
  - Backtest with `apply_d1_trend_filter=true` produces fewer trades than `false`
- **Data reference:** `src/backtesting/backtest_engine.py` line 1066: `"d1_rows": []`

---

### REQ-V6-006: Fix Calendar Filter (Currently No-Op)

- **Priority:** P1
- **Problem:** Phase 2 (calendar OFF) and Phase 3 (calendar ON) produce byte-identical results: 33 trades, same PnL, same by-symbol breakdown. Calendar filter has zero effect.
- **Required behavior:**
  - Audit `economic_events` table: verify it has HIGH-impact events for 2024-2025
  - If empty: create a backfill script from ForexFactory or Investing.com
  - If populated: debug why no events match the 2-hour window for any of 33 trade timestamps
  - Add per-run logging: "Calendar filter blocked N signals this run"
- **Expected impact:** Calendar filter should block 5-15% of trades near major economic releases, improving PF.
- **Acceptance criteria:**
  - Economic events table has >= 50 HIGH-impact events per year
  - Calendar filter blocks >= 1 trade in 24-month backtest
  - Blocked trades logged with event name and timestamp
- **Data reference:** `src/signals/filter_pipeline.py` lines 330-345, `BACKTEST_RESULTS_V5_P2.md` === `BACKTEST_RESULTS_V5_P3.md`

---

### REQ-V6-007: Improve SHORT Signal Quality

- **Priority:** P1
- **Problem:** SHORT signals (strong_sell) have WR 36.84% and produce -$72.18 total PnL, while LONG signals (strong_buy) have WR 57.14% and +$325.90. SHORT is a net drag on portfolio.
- **Required behavior:** Architect must evaluate three approaches:
  - **(A) Asymmetric thresholds:** SHORT requires |composite| >= 18, LONG >= 15
  - **(B) Stricter SHORT momentum:** Require RSI < 40 (not < 50) for SHORT momentum alignment
  - **(C) Asymmetric R:R:** Lower TP multiplier for SHORT to increase SHORT WR at expense of per-trade profit
  - **(D) LONG-only mode:** Disable SHORT entirely as a backtest comparison baseline
  - Run all four variants in backtest to compare PF and trade count.
- **Expected impact:** Either improve SHORT WR to >= 45% or eliminate SHORT drag.
- **Acceptance criteria:**
  - SHORT PnL >= $0, OR LONG-only mode with documented PF improvement
  - Overall PF >= 1.5
- **Data reference:** JSON `by_score_bucket`: strong_sell 19 trades, 7 wins, -$72.18; strong_buy 14 trades, 8 wins, +$325.90

---

### REQ-V6-008: SPY-Specific Parameter Tuning

- **Priority:** P1
- **Problem:** SPY has 16.7% WR (1 win of 6 trades), contributing -$114 in losses. Consistently unprofitable across all phases.
- **Required behavior:**
  - Add SPY to INSTRUMENT_OVERRIDES with `min_composite_score: 25` or `allowed_regimes: ["STRONG_TREND_BULL", "STRONG_TREND_BEAR"]`
  - OR exclude SPY from tradeable instruments pending further analysis
  - Evaluate whether TA-only scoring is fundamentally unsuitable for broad equity indices
- **Expected impact:** Removing SPY's -$114 loss increases total PnL from +$253 to +$367 (PF from 2.01 to ~2.5).
- **Acceptance criteria:**
  - SPY PnL >= $0 after tuning, OR SPY excluded with documented rationale
  - No regression in other instruments
- **Data reference:** JSON `by_symbol.SPY`: 6 trades, 1 win, -$114.36

---

### REQ-V6-009: Persist Regime in Backtest Trade Results

- **Priority:** P1
- **Problem:** All 33 trades in filtered runs show `regime="UNKNOWN"` despite regime computation working in baseline (which shows proper distribution: RANGING 13, STRONG_TREND_BULL 5, VOLATILE 9, etc.). Regime data is lost between signal generation and trade result storage.
- **Required behavior:**
  - Verify `BacktestTradeResult.regime` field is populated from `open_trade["regime"]`
  - Check if `regime` is included in `trade_dicts` serialization (line 766 area)
  - Fix the regime propagation path
- **Expected impact:** Enables per-regime performance analysis and validates regime filter effectiveness.
- **Acceptance criteria:**
  - No trades with regime="UNKNOWN" in new backtest runs
  - by_regime summary shows real regime names with counts
- **Data reference:** `src/backtesting/backtest_engine.py` line 1105, JSON `by_regime: {"UNKNOWN": {"trades": 33}}`

---

### REQ-V6-010: Implement Rejected Signal Logging (Filter Diagnostics)

- **Priority:** P2
- **Problem:** No visibility into how many signals are generated vs blocked by each filter. Impossible to determine filter selectivity or tune thresholds.
- **Required behavior:**
  - Add counters to `SignalFilterPipeline`: `self.rejection_counts = defaultdict(int)`
  - Increment on each `return False, reason` with the filter name
  - Include in backtest summary:
    ```json
    "filter_stats": {
      "total_raw_signals": 500,
      "rejected_by_session": 142,
      "rejected_by_score": 200,
      "rejected_by_regime": 50,
      "rejected_by_d1_trend": 30,
      "rejected_by_volume": 20,
      "rejected_by_momentum": 15,
      "rejected_by_weekday": 8,
      "rejected_by_calendar": 5,
      "passed_all": 30
    }
    ```
- **Expected impact:** Data-driven filter tuning. If 90% of rejections come from one filter, it may be too aggressive.
- **Acceptance criteria:**
  - Backtest summary includes filter_stats dict
  - Sum of all rejections + passed = total_raw_signals
- **Data reference:** `src/signals/filter_pipeline.py` `run_all()`

---

### REQ-V6-011: Add Multi-Timeframe Backtest

- **Priority:** P2
- **Problem:** Backtest only runs H1. Adding H4 and D1 would increase trade count through different signal profiles.
- **Required behavior:**
  - BacktestParams accepts list of timeframes (currently single string)
  - Engine runs each symbol x timeframe independently
  - Correlation guard prevents same-symbol entries on overlapping timeframes
  - Results include per-timeframe breakdown
- **Expected impact:** 2-3x trade count increase.
- **Acceptance criteria:**
  - Backtest can run H1+H4 in a single run
  - No duplicate entries for same price move on different timeframes
  - Results broken down by timeframe
- **Data reference:** `src/backtesting/backtest_params.py`

---

### REQ-V6-012: Add Instrument Expansion

- **Priority:** P2
- **Problem:** Only 4 of 6 configured instruments produce trades. System needs more instruments for trade frequency target.
- **Required behavior:**
  - Add instruments: USDJPY=X, NZDUSD=X, USDCAD=X (forex), XAUUSD/GC=F (gold)
  - Ensure price collection covers these for backtest period
  - Add them to default backtest symbol list
- **Expected impact:** Doubling instrument count could increase trades by 50-100%.
- **Acceptance criteria:**
  - At least 3 new instruments with >= 12 months H1 data
  - New instruments produce trades in backtest
  - Overall PF does not degrade below 1.3
- **Data reference:** Current: EURUSD, GBPUSD, AUDUSD, BTC/USDT, ETH/USDT, SPY

---

### REQ-V6-013: Validate Time Exit and MAE Exit Mechanisms

- **Priority:** P2
- **Problem:** Time exit and MAE exit have never triggered (both counts = 0 across all runs). Either conditions are unreachable or not implemented in backtest `_check_exit()`.
- **Required behavior:**
  - Verify backtest `_check_exit` checks time exit and MAE exit conditions (currently only checks SL/TP)
  - If not implemented: add time exit (close after N candles if PnL <= 0) and MAE exit
  - If implemented but never triggered: review thresholds
- **Expected impact:** Time exit prevents indefinite drawdown; MAE exit cuts losers early.
- **Acceptance criteria:**
  - At least 1 time exit or MAE exit in 24-month backtest
  - If thresholds adjusted, document rationale
- **Data reference:** JSON: `time_exit_count: 0`, `mae_exit_count: 0`

---

### REQ-V6-014: Exclude End-of-Data Closures from Metrics

- **Priority:** P2
- **Problem:** Baseline shows 4 trades closing on 2025-12-31 contributing +$844.21 (80% of gross wins). These are artificial closures that inflate metrics.
- **Required behavior:**
  - Exclude `exit_reason="end_of_data"` trades from WR, PF, and avg_duration
  - Report them separately: `end_of_data_count`, `end_of_data_pnl`
  - Warn if end_of_data > 20% of trades
- **Acceptance criteria:**
  - Primary metrics exclude end-of-data trades
  - Summary includes separate end-of-data section
- **Data reference:** Baseline equity curve: last 4 entries all dated 2025-12-31

---

### REQ-V6-015: Add Walk-Forward Validation

- **Priority:** P2
- **Problem:** All optimization on full 24-month period. No out-of-sample validation. Susceptible to overfitting.
- **Required behavior:**
  - In-sample: 2024-01 to 2025-06 (18 months)
  - Out-of-sample: 2025-07 to 2025-12 (6 months)
  - Report PF, WR, drawdown separately
  - Config already has `BACKTEST_IN_SAMPLE_MONTHS=18`, `BACKTEST_OUT_OF_SAMPLE_MONTHS=6`
- **Acceptance criteria:**
  - OOS PF >= 1.0 (breakeven out of sample)
  - OOS WR within 10% of in-sample WR
  - Report includes IS/OOS comparison
- **Data reference:** `src/config.py` lines 120-124

---

## Priority Summary

| Priority | REQ IDs | Theme |
|----------|---------|-------|
| **P0** | V6-001, V6-002, V6-003 | Data integrity, scoring fix, BTC unblock |
| **P1** | V6-004, V6-005, V6-006, V6-007, V6-008, V6-009 | Instrument coverage, filter fixes, signal quality |
| **P2** | V6-010, V6-011, V6-012, V6-013, V6-014, V6-015 | Diagnostics, expansion, validation |

## Recommended Execution Order

1. **REQ-V6-001** (reporting pipeline) -- foundation for validating all subsequent changes
2. **REQ-V6-002** (composite scaling) + **REQ-V6-003** (BTC unblock) + **REQ-V6-004** (GBPUSD fix) -- the core trade frequency problem
3. **REQ-V6-009** (regime persistence) + **REQ-V6-010** (filter diagnostics) -- measurement infrastructure
4. **REQ-V6-005** (D1 data) + **REQ-V6-006** (calendar data) -- fix non-functional filters
5. **REQ-V6-007** (SHORT quality) + **REQ-V6-008** (SPY tuning) -- signal quality optimization
6. **REQ-V6-013** (time/MAE exit) + **REQ-V6-014** (end-of-data exclusion) -- metric accuracy
7. **REQ-V6-011** (multi-timeframe) + **REQ-V6-012** (instrument expansion) -- scale up
8. **REQ-V6-015** (walk-forward) -- validation
