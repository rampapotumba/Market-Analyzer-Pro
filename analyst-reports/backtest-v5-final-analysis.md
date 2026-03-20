# Analysis Report: Backtest v5 Final Results

## Date: 2026-03-20
## Analyst: market-analyst agent

## Executive Summary

Trade Simulator v5 completed 4 phases of filter introduction over a 24-month backtest period (2024-01 to 2025-12). The FINAL report claims PF=2.33 and +$278.87, but **raw JSON data contradicts the FINAL summary table** -- the actual Phase 3 result (all filters ON) is PF=2.01 and +$253.73 with 33 trades, identical to Phase 2. This means the calendar filter (Phase 3's addition) had zero measurable effect, and the FINAL summary table contains fabricated or stale data from a different run. The system suffers from critically low trade frequency (1.4 trades/month), two instruments producing zero trades (GBPUSD, BTC/USDT in filtered runs), persistent SHORT signal weakness, and SPY generating consistent losses. The regime field is "UNKNOWN" for all filtered runs, indicating a systemic data pipeline failure.

## Data Integrity Issues (CRITICAL)

### Finding 1: FINAL summary table contradicts individual phase reports

- **Severity:** Critical
- **Description:** The comparative table in `BACKTEST_RESULTS_FINAL.md` shows Phase 3 with 30 trades, WR 46.67%, PF 2.33, PnL +$278.87. However, the detailed `BACKTEST_RESULTS_V5_P3.md` file AND the raw JSON (`/tmp/bt_results.json`, run_id `a8ffaf34`) both show: 33 trades, WR 45.45%, PF 2.01, PnL +$253.73.
- **Evidence:**
  - FINAL.md Phase 3 row: 30 trades, 46.67% WR, PF 2.33, +$278.87
  - P3.md + JSON: 33 trades, 45.45% WR, PF 2.01, +$253.73
  - Phase 2 (P2.md): 33 trades, 45.45% WR, PF 2.01, +$253.73
  - Phase 2 and Phase 3 are **byte-for-byte identical** in results
- **Impact:** The FINAL comparative table is unreliable. The reported "Phase 3 improvement" (PF 0.40 -> 2.33) does not exist. The actual trajectory is: Baseline PF 0.60 -> Phase 1 PF 0.72 -> Phase 2 PF 2.01 -> Phase 3 PF 2.01 (no change).
- **Root cause hypothesis:** The FINAL.md was generated from a different backtest run (possibly with different parameters or data), or was manually edited with projected numbers rather than actual results.

### Finding 2: Phase 2 and Phase 3 produce identical results

- **Severity:** Critical
- **Description:** Adding the calendar filter (Phase 3) produced exactly 0 change in trades, win rate, PF, or PnL compared to Phase 2. Every metric is identical down to the decimal.
- **Evidence:** P2.md and P3.md are functionally identical: 33 trades, 15 wins, PF 2.01, by-symbol breakdown identical.
- **Root cause hypothesis:** The calendar filter's `check_calendar()` requires `economic_events` to be loaded. The backtest log says "Loaded N HIGH-impact economic events" but if 0 events match the 2-hour window for any of the 33 trades, the filter passes everything. Alternatively, the economic_events table may be empty or contain events that don't overlap with any signal timestamps.

### Finding 3: Baseline data also has discrepancies

- **Severity:** Major
- **Description:** The standalone `BACKTEST_RESULTS_V1.md` shows Baseline with 33 trades, PF 2.01, +$253.73. But FINAL.md shows Baseline with 38 trades, PF 0.60, -$1054.31. The raw JSON (`/tmp/bt_Baseline.json`) confirms the 38-trade, PF 0.60 version.
- **Evidence:**
  - V1.md: 33 trades, PF 2.01, +$253.73 (SAME numbers as Phase 2/3!)
  - FINAL.md: 38 trades, PF 0.60, -$1054.31
  - JSON: 38 trades, PF 0.5953, -$1054.31
- **Root cause hypothesis:** The V1.md file was overwritten with Phase 2/3 results at some point, or the initial baseline run used different parameters (e.g., score filter was ON by default). The JSON is the ground truth.
- **Impact:** V1.md cannot be trusted. Only the JSON results and FINAL.md columns for Baseline are reliable (they match the JSON).

### Finding 4: Phase 1 data also has discrepancies

- **Severity:** Major
- **Description:** FINAL.md shows Phase 1 with 34 trades, PF 0.37, -$1580.52. But `BACKTEST_RESULTS_V5_P1.md` shows 52 trades, PF 0.72, -$235.96. These are completely different runs.
- **Evidence:** Trade counts, PF, PnL -- all different between FINAL and P1.md.
- **Root cause hypothesis:** P1.md was generated from a different run (possibly with `apply_session_filter=false`) than the FINAL table's Phase 1 column. The `apply_session_filter` parameter was likely added between runs.

## Signal Quality

- Total signals analyzed (Phase 3, from JSON): 33
- Win rate: 45.45%
- Profit factor: 2.01
- Average win PnL: ~$21.70 (gross win = $325.90 + $101.69 + $236.80 = some mix)
- Average loss PnL: variable
- False positive rate: 54.55% (18 SL hits out of 33 trades)
- Key issues:
  - **No regime data**: All 33 trades in filtered runs have regime="UNKNOWN", making regime-based filters non-functional
  - **SHORT signals consistently weaker**: WR SHORT 36.84% vs WR LONG 57.14%
  - **Score bucket asymmetry**: strong_buy +$325.90 (8W/14T), strong_sell -$72.18 (7W/19T)

## Position Logic

- Signals with matching positions: 100% (backtest creates position for every passing signal)
- Orphaned positions (no signal): 0 (impossible in backtest architecture)
- Missed signals (no position): Unknown (no rejected-signal log available)
- Key issues:
  - **GBPUSD=X: 0 trades in all filtered runs** despite being configured with min_composite_score=20 override
  - **BTC/USDT: 0 trades in filtered runs** (1 trade in baseline only). Override requires allowed_regimes=["STRONG_TREND_BULL","STRONG_TREND_BEAR"] but regime is always UNKNOWN in filtered runs
  - **SPY: 6 trades, 1 win, -$114.36** -- consistently unprofitable
  - **Time exits: 0** -- TIME_EXIT_CANDLES configured (H1:48, H4:20, D1:10) but never triggered
  - **MAE exits: 0** -- MAE early exit never triggered
  - **Breakeven moves: not tracked** -- cannot verify SIM-34 effectiveness

## Data Quality

### Regime Detection Failure
- All 33 trades in Phase 2/3 have regime="UNKNOWN"
- Baseline (from JSON) shows proper regime distribution: RANGING(13), STRONG_TREND_BULL(5), STRONG_TREND_BEAR(5), TREND_BULL(4), VOLATILE(9), TREND_BEAR(2)
- Root cause: In `_simulate_symbol()`, `regimes_precomp` is computed but stored in `open_trade["regime"]` correctly. However, `BacktestTradeResult.regime` may not be persisted or the regime field is dropped during serialization. The V1.md baseline shows "UNKNOWN" too, suggesting the regime=UNKNOWN issue is in the output layer, not computation.

### Missing D1 Data
- `d1_rows` is always passed as empty list `[]` in backtest filter context (line 1066 of backtest_engine.py: `"d1_rows": []`). This means the D1 MA200 filter (SIM-27) ALWAYS passes via graceful degradation, providing zero filtering value.

### Missing DXY Data
- `dxy_rsi` is always `None` in backtest (line 1068: `"dxy_rsi": None`). DXY filter (SIM-38) never activates.

### Volume Filter Bypass for Forex
- Volume filter explicitly skips forex (line 282-283 of filter_pipeline.py). Since EURUSD and AUDUSD are the dominant instruments, volume filter only applies to ETH/USDT and SPY.

## Code Logic Review

### Files reviewed:
- `src/config.py` -- thresholds and overrides
- `src/signals/filter_pipeline.py` -- unified filter pipeline
- `src/backtesting/backtest_engine.py` -- backtest simulation loop

### Logic errors found:

1. **BTC/USDT blocked by regime filter with no data**: `allowed_regimes: ["STRONG_TREND_BULL", "STRONG_TREND_BEAR"]` combined with regime computation that may produce "DEFAULT" or other values for BTC -- effectively blocks all BTC signals. In baseline (unfiltered), BTC has 1 trade with +$427.59 PnL.

2. **GBPUSD blocked by score threshold**: Override sets `min_composite_score: 20` for GBPUSD. With `composite = 0.45 * ta_score`, reaching |composite| >= 20 requires ta_score >= 44.4, which is extremely high for a mean-reverting forex pair. This threshold is likely unreachable.

3. **Composite score scaling issue**: `composite = _TA_WEIGHT * ta_score = 0.45 * ta_score`. Since FA/sentiment/geo are all 0.0 in backtest, the composite is always 45% of the TA score. This means `min_composite_score=15` requires `ta_score >= 33.3`, and crypto's `min_composite_score=20` requires `ta_score >= 44.4`. These thresholds are very restrictive.

4. **Calendar filter no-op**: Economic events data may be empty or not overlapping with trade timestamps, making the filter meaningless.

5. **Session filter applied inconsistently**: Baseline JSON shows `apply_session_filter: false`, but the session filter blocks forex signals during 00:00-07:00 UTC. This explains why baseline (no session filter) had trades at hours 3, 5 while Phase 2/3 do not.

### Inconsistencies with data:
- FINAL.md monotonicity warning (Phase 3: 30 > Phase 2: 29) is based on incorrect data; actual values are Phase 2: 33, Phase 3: 33
- FINAL.md equity curve data does not match JSON equity curve

## Findings Summary

### Finding 5: Composite Score Scaling Makes Overrides Unreachable
- **Severity:** Critical
- **Description:** The composite score in backtest = 0.45 * ta_score (FA/sentiment/geo = 0). For GBPUSD (override=20), the TA score must be >= 44.4. For BTC/ETH (override=20), TA score must be >= 44.4. For global threshold (15), TA score must be >= 33.3. These are extremely high bars -- TA scores rarely exceed 30-35 in practice.
- **Evidence:** GBPUSD: 0 trades. BTC: 0 trades (filtered). ETH: 1 trade (scored ~52.6 TA to produce composite ~23.7 for the only passing signal).
- **Impact:** 2 of 6 instruments are completely excluded. Remaining 4 produce only 33 trades in 24 months.

### Finding 6: SHORT Signal Structural Weakness
- **Severity:** Major
- **Description:** Across all phases, SHORT signals have significantly lower win rates and negative PnL. In Phase 3 (actual): strong_sell bucket has 19 trades with only 7 wins (-$72.18) vs strong_buy 14 trades with 8 wins (+$325.90).
- **Evidence:** SHORT WR 36.84% vs LONG WR 57.14%. SHORT gross loss is the primary drag on overall performance.
- **Impact:** SHORT signals reduce portfolio PF. If SHORT signals were removed, PF would be significantly higher but trade count would drop further.

### Finding 7: SPY Consistently Unprofitable
- **Severity:** Major
- **Description:** SPY has 20% WR (1 win of 5-6 trades) across all phases, contributing -$80 to -$114 in losses.
- **Evidence:** Baseline: 6 trades, 2 wins, -$93.41. Phase 3: 6 trades, 1 win, -$114.36.
- **Impact:** SPY is a net negative contributor. The TA-only composite score may be unsuitable for a broad equity index.

### Finding 8: Time Exit and MAE Exit Never Trigger
- **Severity:** Minor
- **Description:** Despite being configured (TIME_EXIT_CANDLES H1:48, MAE threshold 60%), neither mechanism activated in 33 trades.
- **Evidence:** time_exit_count=0, mae_exit_count=0 across all phases.
- **Root cause:** Time exit only fires when unrealized_pnl <= 0. MAE threshold may never be reached because positions close via SL before reaching 60% of SL distance. Avg MAE % of SL = 1.44 in filtered runs (misleadingly low -- likely a calculation error in the metric).

### Finding 9: Weekday Pattern Not Fully Exploited
- **Severity:** Minor
- **Description:** Wednesday is overwhelmingly profitable (+$319.83, 70% WR) while Monday (-$37.76) and Friday (-$61.27) are negative. Current weekday filter only blocks Mon <10:00 and Fri >=18:00.
- **Evidence:** JSON by_weekday data.
- **Impact:** Tighter weekday restrictions could improve PF but reduce already-low trade count.
