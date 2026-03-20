# Requirements: Backtest Filter Pipeline Fixes

## Date: 2026-03-19
## From: market-analyst
## To: architect

## Background

Analysis of v5 backtest results revealed that the filter pipeline is fundamentally broken:
- Phase 2/3 results are byte-identical to baseline (all filters OFF), proving filters have zero effect
- Phase 1 produced MORE trades than baseline (52 vs 33), opposite of expected behavior
- Regime is always UNKNOWN in all backtest results
- SignalFilterPipeline (SIM-42) exists but is never called by either engine

Full analysis: `docs/ANALYST_REPORT_BACKTEST_ANOMALIES.md`

## Requirements

### REQ-001: Integrate SignalFilterPipeline into BacktestEngine
- **Priority:** High
- **Problem:** `BacktestEngine._generate_signal()` and `_simulate_symbol()` have inline filter implementations that duplicate `SignalFilterPipeline`. The pipeline class (SIM-42) is never imported or used. Filter flags are inconsistently applied -- some hardcoded always-on, some flag-controlled.
- **Required behavior:** `BacktestEngine` must use `SignalFilterPipeline.run_all()` for all signal filtering. All filter checks currently inline in `_generate_signal()` (lines 994, 1019, 1027, 1038, 1043, 1066) and `_simulate_symbol()` (lines 650-660) must be removed and replaced with a single `pipeline.run_all(context)` call. The Asian session filter (lines 650-652) should be added to SignalFilterPipeline as a new filter method.
- **Acceptance:** (1) `grep -r "SignalFilterPipeline" src/backtesting/` returns import and usage. (2) Running baseline with all flags OFF produces different results than Phase 2 with volume+momentum+weekday ON. (3) No filter logic remains duplicated in backtest_engine.py.
- **Data reference:** `src/backtesting/backtest_engine.py` lines 650-660, 994-1066; `src/signals/filter_pipeline.py`

### REQ-002: Populate regime field in BacktestTradeResult
- **Priority:** High
- **Problem:** The `regime` detected by `_detect_regime_from_df()` is stored in the signal dict (line 1074: `"regime": regime`) but never copied to `open_trade` dict (lines 691-703). When BacktestTradeResult is constructed, regime is always None.
- **Required behavior:** (1) Add `"regime": signal["regime"]` to the `open_trade` dict at line ~703. (2) Pass `regime=open_trade.get("regime")` to `BacktestTradeResult()` in both exit paths: `_check_exit` result (line ~810) and end-of-data close (line ~724). (3) `_compute_summary()` `by_regime` section should show actual regime names instead of UNKNOWN.
- **Acceptance:** After fix, re-run any backtest and verify `by_regime` contains keys like TREND_BULL, RANGING, DEFAULT instead of only UNKNOWN.
- **Data reference:** `src/backtesting/backtest_engine.py` lines 691-703, 799-815, 724-741; `src/backtesting/backtest_params.py` line 71

### REQ-003: Fix volume filter for forex instruments
- **Priority:** High
- **Problem:** yfinance returns volume=0 for forex instruments on H1 timeframe. The volume filter (`_check_volume_confirmation`) correctly handles this via graceful degradation (`if df["volume"].sum() == 0: return True`), but this means the filter is completely inert for 79%+ of signals (forex dominates the signal set: 26/33 trades).
- **Required behavior:** One of: (a) Source forex volume data from an alternative provider (OANDA, FXCM tick volume), or (b) Explicitly skip volume filter for `market_type == "forex"` and document this as a known limitation, or (c) Use tick count or spread as a proxy for volume in forex.
- **Acceptance:** After fix, running a backtest with `apply_volume_filter=true` produces a measurably different trade count than `apply_volume_filter=false`, at least for non-forex instruments.
- **Data reference:** `src/backtesting/backtest_engine.py` lines 861-874

### REQ-004: Verify and fix momentum filter indicator key mapping
- **Priority:** High
- **Problem:** `_check_momentum_alignment()` looks for keys `rsi_14`, `rsi`, `macd_line`, `macd`, `macd_signal`, `macd_signal_line` in the ta_indicators dict. If `TAEngine.calculate_all_indicators()` returns keys with different names (e.g., uppercase, different naming convention), all lookups return None and the filter passes via graceful degradation.
- **Required behavior:** (1) Add debug logging inside `_check_momentum_alignment()` that prints actual keys received: `logger.debug("[SIM-30] Available TA keys: %s", list(ta_indicators.keys()))`. (2) Verify key names match between TAEngine output and filter expectations. (3) Add a mapping layer if keys differ. (4) Add unit test that constructs a real TAEngine from sample data and passes its output to the momentum filter.
- **Acceptance:** After fix, running a backtest with `apply_momentum_filter=true` produces fewer trades than with `apply_momentum_filter=false`.
- **Data reference:** `src/backtesting/backtest_engine.py` lines 877-908; `src/analysis/ta_engine.py` (TAEngine.calculate_all_indicators return keys)

### REQ-005: Load economic calendar data for backtest period
- **Priority:** Medium
- **Problem:** The economic_events table appears to be empty for the 2024-2025 period. The calendar filter (SIM-33) always passes because `if not economic_events: return True`.
- **Required behavior:** (1) Create a script or migration to load HIGH-impact economic events for 2024-2025 (FOMC dates, NFP releases, ECB decisions, BOE decisions, Australian employment, etc.). (2) Source: investing.com historical calendar, forexfactory, or similar. (3) Minimum fields: event_date (datetime UTC), currency, impact (HIGH), event_name.
- **Acceptance:** After loading data, `SELECT COUNT(*) FROM economic_events WHERE impact = 'HIGH' AND event_date BETWEEN '2024-01-01' AND '2025-12-31'` returns > 50.
- **Data reference:** `src/backtesting/backtest_engine.py` lines 503-509

### REQ-006: Add Asian session filter to SignalFilterPipeline
- **Priority:** Medium
- **Problem:** The Asian session filter (block EU/NA forex pairs during 00:00-06:59 UTC) is hardcoded in `_simulate_symbol()` and is always active regardless of filter flags. It is not part of SignalFilterPipeline, creating inconsistency.
- **Required behavior:** (1) Add `check_session_liquidity()` method to SignalFilterPipeline. (2) Add `apply_session_filter: bool = True` to BacktestParams. (3) Remove hardcoded session filter from `_simulate_symbol()`. (4) The filter should be on by default (current behavior).
- **Acceptance:** Session filter appears in pipeline run_all() output. Can be toggled via backtest params.
- **Data reference:** `src/backtesting/backtest_engine.py` lines 650-652; `src/signals/filter_pipeline.py`

### REQ-007: Re-run all backtests after fixes and document results
- **Priority:** Medium (after REQ-001 through REQ-005)
- **Problem:** Current backtest results are unreliable due to the issues above. No valid baseline for filter comparison exists.
- **Required behavior:** (1) After all fixes applied, re-run: Baseline (all filters OFF), Phase 1 (score+ranging+d1), Phase 2 (+volume+momentum+weekday), Phase 3 (+calendar). (2) Each phase must produce <= trades vs previous phase (monotonically decreasing or equal). (3) Record git commit hash in results file. (4) Include by_regime breakdown with actual regime names.
- **Acceptance:** Phase N trades <= Phase N-1 trades for all N. by_regime shows actual regime distribution. Results are reproducible from the recorded commit hash.
- **Data reference:** `docs/BACKTEST_RESULTS_*.md`

### REQ-008: Integrate SignalFilterPipeline into live SignalEngine
- **Priority:** Medium
- **Problem:** `signal_engine.py` also has its own inline filter implementations (SIM-25/26/31/38/39/40) separate from both backtest_engine and filter_pipeline. SIM-42 goal was unification.
- **Required behavior:** SignalEngine.generate_signal() should use SignalFilterPipeline for all signal-level filtering (score threshold, regime block, signal strength, DXY, F&G, funding rate). Engine-specific guards (cooldown, open position check, correlation guard) remain inline as they depend on DB state.
- **Acceptance:** Filter logic exists in exactly one place (SignalFilterPipeline). Both engines import and use it.
- **Data reference:** `src/signals/signal_engine.py` lines 610-620, 771-781, 817-820
