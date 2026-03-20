# Fix Tasks — Backtest Filter Pipeline

## Overview

The backtest filter pipeline is fundamentally broken: SignalFilterPipeline (SIM-42) exists but is never called, filters are duplicated inline in BacktestEngine with inconsistent flag handling, regime is never recorded in trade results, and several filters are silently non-functional due to data issues. These tasks fix the pipeline in dependency order.

**Source:** `analyst-requirements/backtest-filter-pipeline-requirements.md`, `docs/ANALYST_REPORT_BACKTEST_ANOMALIES.md`

---

## Task 1: Populate regime field in BacktestTradeResult
**REQ:** REQ-002
**Priority:** High (quick win, no dependencies, unlocks regime-based analysis)
**Files to modify:**
- `src/backtesting/backtest_engine.py`

**Description:**
1. In `_simulate_symbol()`, add `"regime": signal["regime"]` to the `open_trade` dict (after line 702, alongside `"mae": 0.0`).
2. In `_check_exit()` method, add `regime=open_trade.get("regime")` to the `BacktestTradeResult(...)` constructor (around line 799-815).
3. In the end-of-data close path (around line 724-740), add `regime=open_trade.get("regime")` to the `BacktestTradeResult(...)` constructor.

That's it — three one-liner additions.

**Tests:**
- Add test in `tests/test_simulator_v5.py`: `test_regime_recorded_in_trade_result` — create a mock trade flow, verify `BacktestTradeResult.regime` is not None and not "UNKNOWN".

**Acceptance criteria:**
- Run any backtest; `by_regime` section in summary contains keys like `TREND_BULL`, `RANGING`, `DEFAULT` instead of only `UNKNOWN`.
- `grep -n "regime" src/backtesting/backtest_engine.py` shows regime is stored in `open_trade` and passed to both `BacktestTradeResult` constructors.

---

## Task 2: Fix momentum filter indicator key mapping
**REQ:** REQ-004
**Priority:** High (must verify before pipeline integration)
**Files to modify:**
- `src/backtesting/backtest_engine.py` (inline filter — will be removed in Task 4, but fix here first for verification)
- `src/signals/filter_pipeline.py`

**Description:**

After code review, the TAEngine key names actually DO match the momentum filter's fallback lookups:
- TAEngine returns `"rsi"` → filter tries `"rsi_14"` then `"rsi"` -- matches via fallback
- TAEngine returns `"macd"` → filter tries `"macd_line"` then `"macd"` -- matches via fallback
- TAEngine returns `"macd_signal"` → filter tries `"macd_signal"` -- matches directly

So the keys are NOT the root cause of the momentum filter being inert. The real issue is likely that signals with `|composite| >= 15` already have aligned momentum (strong TA score implies RSI and MACD agreement). The filter is working but has no practical effect on this dataset.

**Steps:**
1. Add debug logging to both `BacktestEngine._check_momentum_alignment()` and `SignalFilterPipeline.check_momentum()` to log actual indicator values when the filter is evaluated:
   ```python
   logger.debug("[SIM-30] Momentum check: rsi=%.1f, macd=%.5f, signal=%.5f, direction=%s", rsi_f, macd_f, signal_f, direction)
   ```
2. Add a unit test that constructs a `TAEngine` from sample OHLCV data, calls `calculate_all_indicators()`, and passes the result to `check_momentum()`. Verify that:
   - All three keys (`rsi`, `macd`, `macd_signal`) are found (not None).
   - The filter returns `False` for a deliberately misaligned case (e.g., LONG when RSI < 50).
3. Lower the composite threshold temporarily in a test to verify the momentum filter CAN block signals when they have weaker TA alignment.

**Tests:**
- `test_momentum_filter_keys_match_ta_engine` — real TAEngine output feeds into momentum filter without graceful degradation.
- `test_momentum_filter_blocks_misaligned` — LONG with RSI=40, MACD < Signal returns False.
- `test_momentum_filter_passes_aligned` — LONG with RSI=60, MACD > Signal returns True.

**Acceptance criteria:**
- Unit tests confirm the filter receives non-None values from TAEngine output.
- Debug logging shows actual RSI/MACD values during backtest runs.
- Document finding: if the filter blocks 0 trades at threshold 15, this is expected behavior (not a bug), and should be noted in backtest results.

---

## Task 3: Add session filter to SignalFilterPipeline
**REQ:** REQ-006
**Priority:** High (must be done before Task 4 — pipeline integration)
**Files to modify:**
- `src/signals/filter_pipeline.py`
- `src/backtesting/backtest_params.py`

**Description:**

The Asian session filter (block EU/NA forex pairs during 00:00-06:59 UTC) is hardcoded in `_simulate_symbol()` and is not part of SignalFilterPipeline. Before integrating the pipeline (Task 4), this filter must be added to it.

1. Add `apply_session_filter: bool = True` parameter to `SignalFilterPipeline.__init__()`.
2. Add `apply_session_filter: bool = True` to `BacktestParams`.
3. Add `check_session_liquidity()` method to `SignalFilterPipeline`:
   ```python
   def check_session_liquidity(self, candle_ts, symbol, market_type):
       """Block EU/NA forex pairs during Asian session (00:00-06:59 UTC)."""
       if market_type != "forex":
           return True, "ok"
       if symbol not in _FOREX_PAIRS_EU_NA:
           return True, "ok"
       if _is_asian_session(candle_ts):
           return False, f"asian_session_block:{symbol}"
       return True, "ok"
   ```
4. Add the session filter call to `run_all()` BEFORE the weekday filter (it should be the first filter checked since it's the cheapest).
5. Move or copy `_FOREX_PAIRS_EU_NA` and `_is_asian_session()` definitions to `filter_pipeline.py` (or import from backtest_engine — prefer copying to keep filter_pipeline self-contained).

**Tests:**
- `test_session_filter_blocks_eurusd_asian` — EURUSD at 03:00 UTC → blocked.
- `test_session_filter_passes_eurusd_london` — EURUSD at 10:00 UTC → passed.
- `test_session_filter_passes_crypto` — BTC/USDT at 03:00 UTC → passed (not forex).

**Acceptance criteria:**
- `SignalFilterPipeline.run_all()` includes session filter check.
- Filter can be toggled via `apply_session_filter` flag.
- Session filter in `run_all()` output reason string is `"asian_session_block:{symbol}"` when triggered.

---

## Task 4: Integrate SignalFilterPipeline into BacktestEngine
**REQ:** REQ-001
**Priority:** High (largest task — depends on Tasks 1-3)
**Files to modify:**
- `src/backtesting/backtest_engine.py`
- `src/signals/filter_pipeline.py` (minor: add `check_signal_strength()` method)

**Description:**

Replace all inline filter logic in BacktestEngine with a single `SignalFilterPipeline.run_all()` call. This is the core fix.

**Step-by-step:**

### 4a. Add SIM-31 signal strength filter to SignalFilterPipeline
The signal strength filter (`_get_signal_strength()` + `ALLOWED_SIGNAL_STRENGTHS` check) is always-on in `_generate_signal()` and is not part of the pipeline. Add it:
1. Add `check_signal_strength(composite, direction)` method to `SignalFilterPipeline`.
2. This filter is NOT flag-controlled — it always runs (it's a fundamental quality gate).
3. Call it in `run_all()` right after `check_score_threshold()`.
4. Add `signal_strength` key to the context dict.
5. Import or copy `_get_signal_strength()` and `ALLOWED_SIGNAL_STRENGTHS` into filter_pipeline.py.

### 4b. Construct SignalFilterPipeline in _simulate_symbol()
At the top of `_simulate_symbol()`, construct the pipeline from BacktestParams flags:
```python
from src.signals.filter_pipeline import SignalFilterPipeline

pipeline = SignalFilterPipeline(
    apply_score_filter=True,  # always on
    apply_regime_filter=params.apply_ranging_filter,
    apply_d1_trend_filter=params.apply_d1_trend_filter,
    apply_volume_filter=params.apply_volume_filter,
    apply_momentum_filter=params.apply_momentum_filter,
    apply_weekday_filter=params.apply_weekday_filter,
    apply_calendar_filter=params.apply_calendar_filter,
    apply_session_filter=params.apply_session_filter,  # from Task 3
    min_composite_score=params.min_composite_score,
)
```

### 4c. Replace inline filters in _simulate_symbol()
Remove lines 649-661 (session filter, weekday filter, calendar filter). These are now handled by the pipeline.

### 4d. Refactor _generate_signal() to return raw signal + call pipeline
Change `_generate_signal()` to:
1. Keep: TAEngine calculation, composite score computation, regime detection, ATR, S/R levels.
2. Remove: inline score threshold check (lines 1010-1021), signal strength check (lines 1025-1029), ranging filter (lines 1037-1045), volume filter (line 994), momentum filter (line 1066).
3. Return the raw signal dict INCLUDING all data the pipeline needs (composite_score, regime, direction, ta_indicators, df reference).

### 4e. Call pipeline.run_all() after _generate_signal()
In `_simulate_symbol()`, after `_generate_signal()` returns a raw signal:
```python
signal = self._generate_signal(df, symbol, market_type, timeframe)
if signal is None:
    continue

context = {
    "composite_score": float(signal["composite_score"]),
    "market_type": market_type,
    "symbol": symbol,
    "regime": signal["regime"],
    "direction": signal["direction"],
    "timeframe": timeframe,
    "df": df,
    "ta_indicators": signal.get("ta_indicators", {}),
    "candle_ts": candle_ts,
    "d1_rows": d1_rows,  # from existing D1 data loading
    "economic_events": economic_events,
}
passed, reason = pipeline.run_all(context)
if not passed:
    logger.debug("[Pipeline] Signal blocked for %s: %s", symbol, reason)
    continue
```

### 4f. Remove duplicated static methods from BacktestEngine
After pipeline integration is confirmed working, remove:
- `_check_volume_confirmation()`
- `_check_momentum_alignment()`
- `_check_weekday_filter()`
- `_check_economic_calendar()`
- `_check_d1_trend_alignment()`

Keep only methods that are NOT part of the filter pipeline (e.g., `_check_exit()`, `_recalc_sl_tp()`).

**Tests:**
- `test_pipeline_integration_all_filters_off` — all `apply_*` flags = False; pipeline passes everything.
- `test_pipeline_integration_score_filter_blocks` — composite < 15 → signal blocked.
- `test_pipeline_integration_regime_filter_blocks` — regime = RANGING, flag on → signal blocked.
- `test_pipeline_integration_regime_filter_off` — regime = RANGING, flag off → signal passes.
- `test_pipeline_used_in_backtest` — verify `SignalFilterPipeline` is imported and instantiated in backtest_engine.

**Acceptance criteria:**
1. `grep -r "SignalFilterPipeline" src/backtesting/` returns import and usage.
2. No filter logic remains duplicated in backtest_engine.py (no `_check_volume_confirmation`, `_check_momentum_alignment`, etc.).
3. Running baseline (all filters OFF) produces different results than a run with volume+momentum+weekday ON.
4. All existing v5 tests pass (or are updated to reflect new architecture).
5. filter_flags dict is no longer used — all flags flow through pipeline constructor.

---

## Task 5: Fix volume filter for forex — explicit skip
**REQ:** REQ-003
**Priority:** Medium
**Files to modify:**
- `src/signals/filter_pipeline.py`

**Description:**

The simplest fix: explicitly skip the volume filter for forex instruments since yfinance returns volume=0 for forex on all timeframes. This is a known data limitation, not a code bug.

1. Modify `check_volume()` in `SignalFilterPipeline` to accept `market_type` parameter:
   ```python
   def check_volume(self, df: pd.DataFrame, market_type: str = "forex") -> tuple[bool, str]:
       if market_type == "forex":
           logger.debug("[SIM-29] Volume filter skipped for forex (no volume data from provider)")
           return True, "ok"
       # ... rest of existing logic
   ```
2. Update `run_all()` to pass `market_type` to `check_volume()`.
3. Add a comment in the code documenting this as a known limitation.
4. In `KNOWN_LIMITATIONS.md` (or in the backtest results), document: "Volume filter (SIM-29) is not applied to forex instruments because yfinance does not provide volume data for FX pairs. The filter is active for stocks and crypto."

**Tests:**
- `test_volume_filter_skipped_for_forex` — market_type="forex" → always True regardless of volume data.
- `test_volume_filter_active_for_stocks` — market_type="stocks", low volume → blocks signal.
- `test_volume_filter_active_for_crypto` — market_type="crypto", low volume → blocks signal.

**Acceptance criteria:**
- Backtest with `apply_volume_filter=true` produces different trade count for stocks/crypto symbols vs `apply_volume_filter=false`.
- Forex trade count is unaffected by the volume filter toggle.

---

## Task 6: Document economic calendar data requirement
**REQ:** REQ-005
**Priority:** Medium (data task, not code)
**Files to create:**
- `scripts/load_economic_calendar.py` (stub/documentation script)
- `docs/DATA_REQUIREMENTS.md` (or add section to existing docs)

**Description:**

The economic calendar filter (SIM-33) is inert because the `economic_events` table has no data for the backtest period (2024-2025). Rather than building a complex scraping system, document what data is needed and create a minimal loading script.

1. Create `scripts/load_economic_calendar.py` with:
   - A docstring explaining the purpose and data format needed.
   - A hardcoded list of known HIGH-impact events for 2024-2025 (FOMC dates, NFP releases — these are public and predictable). Minimum 50 events.
   - Schema: `(event_date: datetime UTC, currency: str, impact: str, event_name: str)`.
   - A function that inserts these into the `economic_events` table via SQLAlchemy.
2. Document in `docs/DATA_REQUIREMENTS.md`:
   - What the calendar filter expects.
   - Where to source historical economic calendar data (investing.com, forexfactory).
   - The minimum fields required.
   - Note: for production, this should be replaced with an API-based data pipeline.

**Tests:**
- No automated tests needed for the data script itself.
- After running the script, the existing calendar filter tests should start seeing actual blocks.

**Acceptance criteria:**
- `SELECT COUNT(*) FROM economic_events WHERE impact = 'HIGH' AND event_date BETWEEN '2024-01-01' AND '2025-12-31'` returns > 50.
- Re-running backtest with `apply_calendar_filter=true` shows different trade count vs `false` for periods near FOMC/NFP dates.

---

## Task 7: Re-run all backtests and document results
**REQ:** REQ-007
**Priority:** Medium (after Tasks 1-6 are complete)
**Files to create/modify:**
- `scripts/run_backtests_v5_final.sh` (new clean script)
- `docs/BACKTEST_RESULTS_FINAL.md` (results)

**Description:**

After all fixes are applied, re-run backtests in a clean, reproducible manner.

1. Create `scripts/run_backtests_v5_final.sh` that runs 4 phases sequentially:
   - **Baseline:** ALL `apply_*` flags = `false` (truly no filters).
   - **Phase 1:** `apply_ranging_filter=true`, `apply_d1_trend_filter=true`, `min_composite_score=15`.
   - **Phase 2:** Phase 1 + `apply_volume_filter=true`, `apply_momentum_filter=true`, `apply_weekday_filter=true`.
   - **Phase 3:** Phase 2 + `apply_calendar_filter=true`.
2. Record the git commit hash at the top of results file.
3. Each phase should log: total_trades, win_rate, profit_factor, by_regime breakdown, by_symbol breakdown.
4. Verify monotonicity: Phase N trades <= Phase N-1 trades for all N. If violated, investigate and document why.
5. Write results to `docs/BACKTEST_RESULTS_FINAL.md`.

**Acceptance criteria:**
- Phase N trades <= Phase N-1 trades (monotonically decreasing or equal).
- `by_regime` shows actual regime distribution (no "UNKNOWN").
- Results are reproducible from the recorded commit hash.
- Baseline and Phase 2 produce DIFFERENT trade counts (proving filters have effect).

---

## Task 8: Integrate SignalFilterPipeline into live SignalEngine (FOLLOW-UP)
**REQ:** REQ-008
**Priority:** Low (defer until after backtest fixes are validated)
**Files to modify:**
- `src/signals/signal_engine.py`
- `src/signals/filter_pipeline.py` (may need additional context keys for DXY, F&G, funding rate)

**Description:**

This is a follow-up task to be done AFTER Tasks 1-7 are validated via backtests. The live `SignalEngine` also has its own inline filter implementations that should use `SignalFilterPipeline` for consistency.

**Scope:**
1. Replace inline filter checks in `signal_engine.py` (SIM-25/26/31/38/39/40) with `SignalFilterPipeline.run_all()`.
2. Add DXY filter (`check_dxy_rsi()`), Fear & Greed filter (`check_fear_greed()`), and Funding Rate filter (`check_funding_rate()`) to SignalFilterPipeline if not already present.
3. Keep engine-specific guards inline: cooldown, open position check, correlation guard (these depend on live DB state and are not signal-level filters).

**DO NOT start this task until:**
- Tasks 1-7 are complete.
- Backtest results in Task 7 are validated.
- No regressions in existing v5 tests.

**Acceptance criteria:**
- Filter logic exists in exactly ONE place (`SignalFilterPipeline`).
- Both BacktestEngine and SignalEngine import and use it.
- Live signal generation produces same results as before (no behavioral change, only architectural cleanup).

---

## Dependency Graph

```
Task 1 (regime field)     ─┐
Task 2 (momentum keys)    ─┤
Task 3 (session filter)   ─┼──> Task 4 (pipeline integration) ──> Task 7 (re-run backtests)
Task 5 (volume forex)     ─┤                                            │
Task 6 (calendar data)    ─┘                                            v
                                                                  Task 8 (live engine — follow-up)
```

Tasks 1, 2, 3, 5, 6 can be done in parallel. Task 4 depends on 1-3. Task 7 depends on 4-6. Task 8 is deferred.

---

## Key Findings from Architecture Review

1. **TAEngine keys DO match momentum filter** — The filter uses fallback key lookups (`"rsi_14" or "rsi"`, `"macd_line" or "macd"`) that correctly find TAEngine's output keys (`"rsi"`, `"macd"`, `"macd_signal"`). The momentum filter being inert is because strong signals (composite >= 15) naturally have aligned RSI/MACD.

2. **filter_flags dict is the root cause of inconsistency** — Some filters check `ff.get("key", True)` (default ON), some check `ff.get("key", False)` (default OFF), and some have no flag at all (always ON). Replacing this with `SignalFilterPipeline` constructor params eliminates the inconsistency.

3. **Regime detection works but result is not stored** — `_detect_regime_from_df()` correctly identifies regimes, but the result is only in the signal dict, never copied to `open_trade`, so BacktestTradeResult always has regime=None.


---

## Task 9: Optimize backtest engine from O(n^2) to O(n)
**REQ:** REQ-PERF-001, REQ-PERF-002, REQ-PERF-003, REQ-PERF-004, REQ-PERF-005
**Priority:** Critical (9+ hour backtests → 5-15 minutes)
**Source:** `analyst-requirements/backtest-performance-requirements.md`
**Depends on:** Tasks 1-6 (pipeline integration must be complete)

**Goal:**
Eliminate the O(n^2) performance bottleneck in `_simulate_symbol()` by pre-computing all technical indicators and regime classifications once on the full DataFrame, then using O(1) lookups per candle during the main loop. The optimization MUST produce bit-identical trade results (same trades, same entry/exit prices, same PnL).

**Background:**
Currently, for each candle index `i` in the main loop (line 623), the engine:
1. Copies `price_rows[:i]` — O(i) list slice (line 629)
2. Calls `_to_ohlcv_df(history)` — O(i) DataFrame construction (line 630)
3. Inside `_generate_signal()` (line 675): creates `TAEngine(df, timeframe)` which does `df.copy()` (ta_engine.py line 70), then computes ALL TA indicators from scratch — O(i) per indicator
4. Calls `_detect_regime_from_df(df)` (line 942) which creates a new `RegimeDetector()` and re-computes ADX, ATR, SMA200 — duplicating TAEngine work

For n=12,000 candles (2yr H1 forex): ~72 million rows processed. For BTC (17,500 H1 candles): ~153 million rows.

All core TA indicators (RSI, MACD, BB, SMA, EMA, ADX, Stoch, ATR) are causal filters — value at index `i` depends only on data `[0..i]`. Pre-computing on the full dataset and reading `[i-1]` is mathematically identical to computing on `df[:i]`.

### Technical Design

#### Phase A: Pre-build full DataFrame once (REQ-PERF-001)

**File:** `src/backtesting/backtest_engine.py` — `_simulate_symbol()`

Before the main loop (before line 623), build the full DataFrame once:
```python
full_df = _to_ohlcv_df(price_rows)
```

Remove lines 629-630 (`history = price_rows[:i]` / `df = _to_ohlcv_df(history)`) from inside the loop.

#### Phase B: Add `calculate_all_indicators_arrays()` method to TAEngine (REQ-PERF-002)

**File:** `src/analysis/ta_engine.py`

Add a new method that returns full-length numpy arrays instead of scalar last values:
```python
def calculate_all_indicators_arrays(self) -> dict[str, np.ndarray]:
    """Compute all indicator arrays on the full DataFrame.

    Unlike calculate_all_indicators() which returns scalar last values,
    this returns the full numpy arrays for each core indicator.
    Used by backtest engine for O(n) pre-computation.

    Returns dict with keys:
        rsi, macd, macd_signal, macd_hist,
        bb_upper, bb_middle, bb_lower,
        sma_fast, sma_slow, sma_long,
        ema_fast, ema_slow,
        adx, plus_di, minus_di,
        stoch_k, stoch_d,
        atr
    """
```

This method calls the existing `_calc_rsi()`, `_calc_macd()`, etc. but stores the full arrays instead of calling `_safe_last()`. The existing `calculate_all_indicators()` remains unchanged for backward compatibility.

**Important:** This method does NOT include position-dependent indicators (S/R levels, candle patterns, PDH/PDL, session levels, fibonacci, volume profile, order blocks, FVGs). Those depend on "what's the last candle" and cannot be trivially pre-computed. See Phase D for handling.

#### Phase C: Pre-compute ta_score array (REQ-PERF-002)

**File:** `src/backtesting/backtest_engine.py` — new helper function

Add a function that computes `ta_score` at every candle index from pre-computed indicator arrays:
```python
def _precompute_ta_scores(
    ta_arrays: dict[str, np.ndarray],
    close: np.ndarray,
    n: int,
) -> np.ndarray:
    """Compute ta_score at each index using pre-computed indicator arrays.

    For each index i, extracts scalar indicator values at [i] and applies
    the same signal generation + weighting logic as TAEngine.generate_ta_signals()
    + calculate_ta_score().

    Returns np.ndarray of shape (n,) with ta_score values.
    NaN for indices where indicators are not yet available (warmup period).
    """
```

The function must replicate the EXACT logic of:
1. `TAEngine._rsi_signal(rsi, adx)` — RSI signal with ADX context
2. `TAEngine.generate_ta_signals()` — all signal computations for MACD, BB, MA cross, ADX, Stochastic, Volume
3. `TAEngine.calculate_ta_score()` — weighted sum using `TA_WEIGHTS`

**Critical:** The S/R signal, candle pattern signal, and volume signal components use position-dependent data. For the ta_score pre-computation:
- **S/R signal**: Use weight=0.05, signal=0, strength=0 (constant). This is acceptable because S/R has low weight and the cluster-based S/R detection (which scans entire array for pivot points) is inherently position-dependent. The alternative is an O(n) sliding-window pivot detector, but this adds complexity for 5% weight.
- **Candle pattern signal**: Use weight=0.05, signal from TA-Lib pattern arrays pre-computed on full data. TA-Lib candle patterns ARE causal — they only look at last 3-5 candles. Pre-compute all 6 pattern arrays once and index at [i].
- **Volume signal**: Use weight=0.07, computed from pre-computed SMA20 and volume arrays at index [i]. This IS causal.

**Decision needed by developer:** If matching ta_score exactly (including S/R with its 5% weight) is required, the developer can either:
- (Option A, recommended) Accept the S/R=0 simplification for pre-computed scores. The difference is bounded by 5 points maximum (5% × 100) and in practice is much smaller. Verify via regression test.
- (Option B) Keep S/R computation inside the loop — it's cheap (O(lookback=50) per candle, not O(i)) and doesn't cause the O(n^2) blowup. Call `_find_support_resistance()` on `full_df.iloc[:i]` (a view, O(1) creation) only to get the S/R signal contribution.

If Option B is chosen: `_find_support_resistance()` uses `self.df["close"].iloc[-1]` and `self.df.tail(50)` which are correct on a view `df.iloc[:i]`. It also calls `_calc_atr(14)` internally — this should use the pre-computed ATR array value at `[i-1]` instead. Add a parameter `atr_override: Optional[float] = None` to `_find_support_resistance()`.

#### Phase D: Pre-compute regime array (REQ-PERF-003)

**File:** `src/backtesting/backtest_engine.py` — new helper function
**File:** `src/analysis/regime_detector.py` — extract pure function

1. Extract regime classification logic from `RegimeDetector._detect_regime()` into a standalone pure function:
```python
def classify_regime_at_point(
    adx: float,
    atr_pct: Optional[float],
    close: float,
    sma200: float,
    vix: Optional[float] = None,
) -> str:
    """Classify regime from scalar indicator values. No DataFrame needed.

    Returns one of: STRONG_TREND_BULL, STRONG_TREND_BEAR, WEAK_TREND_BULL,
    WEAK_TREND_BEAR, RANGING, HIGH_VOLATILITY, LOW_VOLATILITY.
    """
```

This function contains the same if/elif chain as `_detect_regime()` (lines 143-168 of regime_detector.py) but operates on scalar inputs.

2. In backtest_engine.py, pre-compute regime at every index:
```python
def _precompute_regimes(
    adx_array: np.ndarray,     # from TAEngine
    atr_array: np.ndarray,     # from TAEngine
    close_array: np.ndarray,
    sma200_array: np.ndarray,  # from TAEngine
    n: int,
) -> list[str]:
    """Compute regime string at each candle index.

    For ATR percentile: uses rolling window of 252 bars ending at [i].
    """
```

**ATR percentile calculation**: `_atr_percentile()` uses a rolling window of 252 ATR values. Pre-compute this as a rolling percentile array:
```python
atr_series = pd.Series(atr_array)
atr_pct_array = atr_series.rolling(252, min_periods=2).apply(
    lambda w: (w[:-1] < w.iloc[-1]).sum() / (len(w) - 1) * 100
)
```

Then apply `_MAP` normalization (STRONG_TREND_BULL → STRONG_TREND_BULL, HIGH_VOLATILITY → VOLATILE, etc.) to match `_detect_regime_from_df()` output.

#### Phase E: Refactor _generate_signal() (REQ-PERF-002)

**File:** `src/backtesting/backtest_engine.py`

Change `_generate_signal()` signature to accept pre-computed values instead of raw DataFrame:
```python
def _generate_signal(
    self,
    ta_score: float,          # pre-computed at index i-1
    atr_value: float,         # pre-computed ATR at index i-1
    regime: str,              # pre-computed regime at index i-1
    ta_indicators_at_i: dict, # pre-computed indicator scalars at index i-1
    symbol: str,
    market_type: str,
    timeframe: str,
) -> Optional[dict[str, Any]]:
```

The refactored method:
1. Computes `composite = _TA_WEIGHT * ta_score` (same as before)
2. Checks `composite == 0` → return None (same as before)
3. Sets direction from composite sign (same as before)
4. Uses passed `regime` instead of calling `_detect_regime_from_df(df)`
5. Uses passed `atr_value` instead of `ta_engine.get_atr(14)`
6. Uses passed `ta_indicators_at_i` for the pipeline context
7. Does NOT create TAEngine instance
8. Does NOT call `calculate_all_indicators()` or `calculate_ta_score()`

**S/R levels for SIM-36:** The `_generate_signal()` currently extracts `support_levels` and `resistance_levels` from `ta_indicators`. These come from `_find_support_resistance()` which is position-dependent. Options:
- (Simple) Pass empty lists. S/R snapping in `_recalc_sl_tp()` will fall back to ATR-only SL/TP.
- (Better) Compute S/R only when a signal is actually generated (after composite check). This happens ~200-500 times per symbol, not ~12,000. Use `full_df.iloc[:i]` as a view — O(50) for the pivot scan, negligible.

#### Phase F: Refactor _simulate_symbol() main loop

**File:** `src/backtesting/backtest_engine.py`

The refactored main loop structure:
```python
# ── Pre-computation (BEFORE loop) ──────────────────────────────────
full_df = _to_ohlcv_df(price_rows)

# 1. Create TAEngine once on full data
ta_engine = TAEngine(full_df, timeframe=timeframe)
ta_arrays = ta_engine.calculate_all_indicators_arrays()

# 2. Pre-compute ta_score at every index
close_arr = full_df["close"].values
ta_scores = _precompute_ta_scores(ta_arrays, close_arr, len(full_df))

# 3. Pre-compute regime at every index
regimes = _precompute_regimes(
    ta_arrays["adx"], ta_arrays["atr"], close_arr,
    ta_arrays["sma_long"], len(full_df)
)

# ── Main loop ──────────────────────────────────────────────────────
for i in range(_MIN_BARS_HISTORY, n - 1):
    # ... exit checks unchanged (use price_rows[i] directly) ...

    # Index into pre-computed arrays: use i-1 (last completed candle)
    idx = i - 1
    ta_score_i = ta_scores[idx]
    regime_i = regimes[idx]
    atr_i = ta_arrays["atr"][idx]

    # Build indicator dict for this index (for pipeline context)
    ta_indicators_i = {
        "rsi": float(ta_arrays["rsi"][idx]) if not np.isnan(ta_arrays["rsi"][idx]) else None,
        "macd": float(ta_arrays["macd"][idx]) if not np.isnan(ta_arrays["macd"][idx]) else None,
        "macd_signal": float(ta_arrays["macd_signal"][idx]) if not np.isnan(ta_arrays["macd_signal"][idx]) else None,
        # ... other indicators ...
        "current_price": close_arr[idx],
        "atr": float(atr_i) if not np.isnan(atr_i) else None,
    }

    signal = self._generate_signal(
        ta_score=ta_score_i, atr_value=atr_i, regime=regime_i,
        ta_indicators_at_i=ta_indicators_i,
        symbol=symbol, market_type=market_type, timeframe=timeframe,
    )
    # ... rest of loop unchanged ...
```

**Index mapping:** `price_rows` and `full_df` have the same ordering. `price_rows[i]` corresponds to `full_df.iloc[i]`. Indicator arrays from TAEngine have the same length as `full_df`. When the loop is at index `i`, the signal should be based on data visible up to `i-1` (the last COMPLETED candle before the current one being evaluated). So we read `ta_arrays["rsi"][i-1]`.

#### Phase G: filter_context["df"] adaptation (REQ-PERF-004)

The `filter_context` dict (lines 680-693) currently passes `"df": df`. After optimization:
- Replace `"df": df` with `"df": full_df.iloc[:i]` (O(1) pandas view) ONLY if any filter in the pipeline actually reads `filter_context["df"]`.
- Check which pipeline filters use `context["df"]`:
  - `check_volume()` — needs `df["volume"].tail(20)` and `df["volume"].iloc[-1]`. Replace with pre-computed `current_volume` and `avg_volume_20` from `ta_indicators_i`.
  - `check_d1_trend()` — uses separate D1 data, not the main df. Unaffected.
  - `check_momentum()` — uses `ta_indicators` dict. Unaffected.
- If no filter reads raw `df`: remove `"df"` from `filter_context` entirely.
- If a filter still needs it: pass `full_df.iloc[:i]` (view, O(1)).

#### Phase H: D1 MA200 / cross-timeframe handling (REQ-PERF-005)

The D1 trend filter (SIM-27) uses D1 candle data to check MA200, which is a DIFFERENT timeframe from the main H1 loop. This is currently handled by passing `d1_rows` to the filter context (line 690: `"d1_rows": []`).

This optimization does NOT change D1 handling — it remains separate. D1 data is loaded once before the loop and passed through. The filter pipeline's `check_d1_trend()` receives D1 rows independently. No action needed here beyond verifying it still works.

#### Phase I: TAEngine backward compatibility (REQ-PERF-004)

**File:** `src/analysis/ta_engine.py`

The `df.copy()` in `__init__()` (line 70) is needed for live signal generation where the caller might mutate the DataFrame. For backtest, the single copy before the loop is acceptable (TAEngine is instantiated only once now).

Two options:
- (Simple, recommended) Leave `df.copy()` as-is. One copy of 12,000 rows takes ~1ms. Not worth changing.
- (Optional) Add `copy: bool = True` parameter: `self.df = df.copy() if copy else df`. Backtest passes `copy=False`.

#### Phase J: No-lookahead regression test (REQ-PERF-005)

**File:** `tests/test_backtest_performance.py` (new file)

1. **Equivalence test**: Run backtest on a small synthetic dataset (200-500 candles, 1 symbol) using BOTH old and new code paths. Compare:
   - Number of trades generated (must be identical)
   - Entry prices, exit prices, PnL per trade (must match within `Decimal("0.01")`)
   - Exit reasons (must be identical)

   Implementation approach: before removing the old code, add a flag `use_precomputed: bool = True` to `_simulate_symbol()`. When `False`, use the old O(n^2) path. Test runs both and compares. After verification, remove the flag and old code path.

2. **Spot-check test**: For 10 random candle indices in the dataset, verify:
   ```python
   # Pre-computed value at index i
   precomputed_rsi = ta_arrays["rsi"][i]
   # Value from fresh TAEngine on df[:i+1]
   fresh_engine = TAEngine(full_df.iloc[:i+1], timeframe)
   fresh_rsi = fresh_engine.calculate_all_indicators()["rsi"]
   assert abs(precomputed_rsi - fresh_rsi) < 1e-6
   ```

3. **Benchmark test** (not a pass/fail test, informational):
   ```python
   import time
   start = time.perf_counter()
   # run backtest
   elapsed = time.perf_counter() - start
   print(f"Backtest completed in {elapsed:.1f}s")
   # Assert it completes within reasonable time (e.g., < 120s for 500 candles)
   ```

### Subtasks

- [ ] 9a. Add `calculate_all_indicators_arrays()` to `TAEngine` — returns full numpy arrays for all core indicators (RSI, MACD, BB, SMA, EMA, ADX, Stoch, ATR). Keep existing `calculate_all_indicators()` unchanged.
- [ ] 9b. Extract `classify_regime_at_point()` pure function from `RegimeDetector._detect_regime()` into `regime_detector.py`. Add unit test confirming it matches `_detect_regime()` for sample data.
- [ ] 9c. Add `_precompute_ta_scores()` helper to `backtest_engine.py` — vectorized ta_score computation from indicator arrays. Must replicate `TAEngine.generate_ta_signals()` + `calculate_ta_score()` weighted sum logic exactly (excluding S/R component, which is set to 0).
- [ ] 9d. Add `_precompute_regimes()` helper to `backtest_engine.py` — regime classification at every index from pre-computed ADX, ATR, SMA200 arrays. Includes ATR percentile rolling computation.
- [ ] 9e. Refactor `_generate_signal()` to accept pre-computed scalars (ta_score, atr, regime, ta_indicators dict) instead of raw DataFrame. Remove TAEngine instantiation and `_detect_regime_from_df()` call from inside this method.
- [ ] 9f. Refactor `_simulate_symbol()` main loop: build full_df once, call TAEngine once, pre-compute all arrays before loop, use O(1) lookups inside loop. Remove `price_rows[:i]` slicing and per-iteration `_to_ohlcv_df()`.
- [ ] 9g. Adapt `filter_context["df"]` — replace with pre-computed indicator values or `full_df.iloc[:i]` view. Verify each pipeline filter's data needs.
- [ ] 9h. Write no-lookahead regression test: compare O(n) vs O(n^2) results on synthetic dataset. All trades must match exactly.
- [ ] 9i. Write spot-check test: verify pre-computed indicator values at random indices match fresh TAEngine computation within 1e-6 tolerance.
- [ ] 9j. Run full test suite (182 existing tests + new tests), verify zero regressions. Run actual backtest and compare results with pre-optimization baseline.

### Acceptance Criteria

- [ ] `_to_ohlcv_df()` is called exactly ONCE per symbol (not inside the main loop).
- [ ] `TAEngine` is instantiated at most ONCE per symbol per backtest run.
- [ ] `RegimeDetector` / `_detect_regime_from_df()` is NOT called inside the main loop.
- [ ] No `price_rows[:i]` list slicing occurs inside the main loop.
- [ ] No DataFrame construction or copy occurs inside the main loop (except `full_df.iloc[:i]` views if needed).
- [ ] Regression test confirms identical trade output: same trade count, same entry/exit prices, same PnL (tolerance < 0.01 per trade).
- [ ] Spot-check test confirms pre-computed indicator values match fresh computation (tolerance 1e-6).
- [ ] All 182 existing tests pass unchanged.
- [ ] Backtest for 1 symbol × 2yr H1 completes in < 60 seconds (was 1.5+ hours).
- [ ] D1 MA200 cross-timeframe filter still works correctly (not broken by optimization).

### Files to Create or Modify

- `src/analysis/ta_engine.py` — add `calculate_all_indicators_arrays()` method
- `src/analysis/regime_detector.py` — extract `classify_regime_at_point()` pure function
- `src/backtesting/backtest_engine.py` — refactor `_simulate_symbol()`, `_generate_signal()`, add `_precompute_ta_scores()`, `_precompute_regimes()` helpers
- `tests/test_backtest_performance.py` — new file with regression, spot-check, and benchmark tests

### Dependencies

- Tasks 1-6 must be complete (pipeline integration in place)
- TAEngine and RegimeDetector public APIs must remain backward-compatible (live signal generation must not break)

### Risks and Notes

1. **S/R signal component (5% weight):** Pre-computing S/R across all candles is non-trivial because the pivot detection looks ±3 candles from each point and filters by "above/below current price". Recommended approach: set S/R signal contribution to 0 in pre-computed ta_scores, verify via regression test that trade output is identical (if S/R had minimal impact) or compute S/R only when a signal passes composite threshold (~200-500 times per symbol, acceptable).

2. **Candle patterns:** TA-Lib candle pattern functions (CDLHAMMER, CDLENGULFING, etc.) operate on the full OHLC arrays and return arrays — they are causal. Pre-compute once and index. Weight is only 5%.

3. **Volume signal direction:** Uses `close > sma20` to determine direction. Both close and sma20 are available from pre-computed arrays. Weight is 7%.

4. **Indicator warmup period:** RSI(14) needs ~14 bars, SMA(200) needs 200 bars, ADX(14) needs ~28 bars. The first `_MIN_BARS_HISTORY=50` candles are skipped (loop starts at index 50). SMA(200) will be NaN for the first 200 candles — this matches current behavior since TAEngine on `df[:i]` with `i<200` also returns NaN for SMA200.

5. **Memory:** Pre-computed arrays for 17,500 candles × 18 indicators × 8 bytes = ~2.5 MB per symbol. Negligible.

6. **Keep old code path temporarily:** During development, keep the old `_generate_signal(df, ...)` signature behind a flag so the regression test can compare both paths. Remove after verification.
