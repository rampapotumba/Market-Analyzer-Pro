# Requirements: Backtest Engine O(n^2) Performance Fix

## Date: 2026-03-19
## From: market-analyst
## To: architect / developer

## Background

The backtest engine (`src/backtesting/backtest_engine.py`) has a critical O(n^2) performance bug in `_simulate_symbol()`. For 6 symbols x 2 years of H1 data, backtests take 9+ hours instead of minutes. The root cause is that on every candle iteration, the engine:

1. Copies the entire price history up to index i (O(i) list slice)
2. Converts that copy to a new DataFrame (O(i) allocation + construction)
3. Recalculates ALL technical indicators from scratch on i rows (O(i) TA-Lib calls)
4. Recalculates regime detection (ADX, ATR, SMA200) redundantly

For n=12,000 H1 candles (forex, 2 years): sum(i, i=50..12000) ~ 72 million rows processed.
For BTC (24/7 market, ~17,500 candles): ~153 million rows processed.

All indicators used (RSI, MACD, ATR, ADX, SMA, EMA, Bollinger, Stochastic) are **causal filters** -- the value at index i depends only on data [0..i]. Pre-computing them on the full dataset and reading values at index [i-1] is mathematically equivalent to computing on df[:i], with zero lookahead risk.

## Requirements

### REQ-PERF-001: Pre-compute DataFrame once before the loop

- **Priority:** Critical
- **Problem:** Lines 629-630 of `backtest_engine.py` create an O(i) list slice and then build a new DataFrame on every iteration of the main loop (line 623: `for i in range(_MIN_BARS_HISTORY, n - 1)`).
  ```python
  # Line 629-630 (current, inside loop)
  history = price_rows[:i]       # O(i) list copy
  df = _to_ohlcv_df(history)     # O(i) DataFrame construction
  ```
- **Required behavior:**
  - Call `_to_ohlcv_df(price_rows)` ONCE before the loop to create the full DataFrame.
  - Inside the loop, if a DataFrame slice is still needed (e.g., for filter_context), use `df.iloc[:i]` which returns a pandas view (O(1)), NOT a copy.
  - Ideally, eliminate the need to pass a DataFrame slice into the loop body entirely (see REQ-PERF-002).
- **Files:**
  - `src/backtesting/backtest_engine.py` -- `_simulate_symbol()`, lines 629-630
  - `src/backtesting/backtest_engine.py` -- `_to_ohlcv_df()`, lines 167-184
- **Acceptance:**
  - `_to_ohlcv_df()` is called exactly once per symbol, before the main loop.
  - No list slicing (`price_rows[:i]`) occurs inside the loop.

---

### REQ-PERF-002: Pre-compute all TA indicators once on full DataFrame

- **Priority:** Critical
- **Problem:** On every loop iteration, `_generate_signal()` (line 675) creates a new `TAEngine(df, timeframe)` instance (line 922), which:
  1. Copies the DataFrame (`self.df = df.copy()` -- `ta_engine.py` line 70)
  2. Calls `calculate_ta_score()` (line 923) which calls `generate_ta_signals()` which calls `calculate_all_indicators()` -- computing RSI(14), MACD, Bollinger, SMA(20/50/200), EMA(12/26), ADX(14), Stochastic, ATR(14) from scratch on i rows.
  3. Calls `calculate_all_indicators()` again at line 951 (for the filter pipeline context).

  Each TA-Lib call is O(i). Done n times = O(n^2).

- **Required behavior:**
  - Create ONE `TAEngine` instance on the full DataFrame before the loop.
  - Call `calculate_all_indicators()` once -- this computes full-length arrays for every indicator.
  - Store the full indicator arrays (not just last values). TAEngine currently uses `_safe_last()` to extract only the last value; for backtest, we need the full arrays.
  - In the loop, read indicator values at index `[i-1]` (the last visible candle before current). This is equivalent to computing on `df[:i]` because RSI/MACD/ATR/ADX/SMA/EMA/Bollinger/Stochastic are all causal (use only past data).
  - Compute `ta_score` at each index from pre-computed indicator values at that index.
  - Option A (preferred): Add a method to TAEngine like `calculate_all_indicators_array()` that returns full numpy arrays instead of scalar last values.
  - Option B: Refactor `_generate_signal()` to accept pre-computed indicator values at a given index instead of a DataFrame.

- **Files:**
  - `src/backtesting/backtest_engine.py` -- `_generate_signal()`, lines 900-959
  - `src/backtesting/backtest_engine.py` -- `_simulate_symbol()`, line 675
  - `src/analysis/ta_engine.py` -- `__init__()` line 70, `calculate_all_indicators()` lines 343-440, `calculate_ta_score()` lines 669-684, `generate_ta_signals()` (called by ta_score)
- **Acceptance:**
  - `TAEngine` is instantiated at most once per symbol per backtest.
  - No TA indicator computation occurs inside the main candle loop.
  - `ta_score` values at each index match the current implementation (within floating-point tolerance of 1e-6).

---

### REQ-PERF-003: Pre-compute regime detection

- **Priority:** Critical
- **Problem:** `_detect_regime_from_df(df)` is called at line 942 inside `_generate_signal()`, which is called on every loop iteration. It creates a new `RegimeDetector()` instance and calls `_detect_regime(df)`, which internally computes ADX, ATR percentage, and SMA200 -- all of which are already computed by TAEngine (duplicated work), and all O(i).
- **Required behavior:**
  - Pre-compute ADX, ATR, SMA200 arrays once on the full DataFrame (can reuse arrays from REQ-PERF-002).
  - Determine regime at each candle index using pre-computed values: `regime[i] = classify(adx[i], atr_pct[i], sma200[i], close[i])`.
  - Store results in a list/array: `regimes: list[str]` indexed by candle position.
  - In the loop, read `regimes[i-1]` directly.
  - Extract the regime classification logic from `RegimeDetector._detect_regime()` into a pure function that takes scalar ADX, ATR%, SMA200, close values (no DataFrame needed).
- **Files:**
  - `src/backtesting/backtest_engine.py` -- `_detect_regime_from_df()`, lines 141-164
  - `src/backtesting/backtest_engine.py` -- line 942 (call site inside `_generate_signal`)
  - `src/analysis/regime_detector.py` -- `_detect_regime()`, line 128
- **Acceptance:**
  - `_detect_regime_from_df()` is NOT called inside the main loop.
  - `RegimeDetector` is instantiated at most once per symbol.
  - Regime values at each index match the current implementation exactly (string comparison).

---

### REQ-PERF-004: Eliminate redundant DataFrame copies

- **Priority:** High
- **Problem:** Multiple unnecessary DataFrame copies occur per iteration:
  1. `TAEngine.__init__()` line 70: `self.df = df.copy()` -- defensive copy that is unnecessary in backtest context where the caller does not mutate the DataFrame.
  2. `_to_ohlcv_df()` at line 630 constructs a new DataFrame every iteration (addressed in REQ-PERF-001).
  3. The `filter_context` dict at line 687 passes `"df": df` -- if this is still needed post-optimization, it should be a view, not a copy.

- **Required behavior:**
  - Option A: Add a `copy=True` parameter to `TAEngine.__init__()`, defaulting to `True` for backward compatibility. Backtest passes `copy=False`.
  - Option B: Since REQ-PERF-002 means TAEngine is called once before the loop, the single copy is acceptable. Focus on ensuring no additional copies inside the loop.
  - Ensure `filter_context["df"]` uses a view (`df.iloc[:i]`) if a DataFrame is still required, or replace it with pre-computed indicator values.
- **Files:**
  - `src/analysis/ta_engine.py` -- `__init__()`, line 70
  - `src/backtesting/backtest_engine.py` -- `_simulate_symbol()`, lines 680-693 (filter_context)
- **Acceptance:**
  - Zero DataFrame copy or construction operations inside the main candle loop.
  - `TAEngine` public API remains backward compatible (live signal generation unaffected).

---

### REQ-PERF-005: Maintain no-lookahead guarantee with verification

- **Priority:** Critical
- **Problem:** The current O(n^2) approach guarantees no lookahead by only passing `price_rows[:i]` to signal generation. Switching to pre-computed full arrays requires explicit verification that no future data leaks into signal decisions.
- **Required behavior:**
  - All pre-computed indicator values at index `[i]` must depend only on data `[0..i]`. This is naturally satisfied by all indicators currently used:
    - RSI(14): exponential moving average of gains/losses -- causal
    - MACD: difference of two EMAs -- causal
    - Bollinger Bands: SMA + stddev of past N values -- causal
    - SMA(N): mean of past N values -- causal
    - EMA(N): exponential weighted average -- causal
    - ADX(14): smoothed directional movement -- causal
    - Stochastic: based on past N highs/lows -- causal
    - ATR(14): Wilder smoothing of true range -- causal
  - **D1 MA200 filter (SIM-27)**: This uses a DIFFERENT timeframe's data (D1 candles for H1 signals). It must still use `d1_rows[:i]` logic or equivalent time-based filtering. This is NOT covered by pre-computing H1 indicators.
  - Add a regression test: run backtest on a small dataset (e.g., 500 candles, 1 symbol) with both old O(n^2) and new O(n) approach. Assert that:
    - Same number of trades generated
    - Same entry/exit prices
    - Same PnL within tolerance (< 0.01 difference per trade due to floating point)
  - Consider adding an assertion in development mode: for a random sample of candle indices, verify that `indicators_full[i]` matches `TAEngine(df[:i+1]).calculate_all_indicators()` within tolerance.
- **Files:**
  - `src/backtesting/backtest_engine.py` -- `_simulate_symbol()`, entire method
  - `tests/test_simulator_v5.py` or new `tests/test_backtest_performance.py`
- **Acceptance:**
  - All 182 existing tests pass unchanged.
  - New regression test confirms identical trade output between O(n) and O(n^2) approaches.
  - D1 MA200 / W1 MA50 cross-timeframe filters are explicitly handled (not broken by optimization).

---

## Expected Performance Impact

| Metric | Current (O(n^2)) | Target (O(n)) |
|--------|------------------|---------------|
| 6 symbols x 2yr H1 forex | 9+ hours | 5-15 minutes |
| BTC 2yr H1 (24/7) | ~15+ hours | 5-10 minutes |
| Rows processed (forex) | ~72 million | ~72 thousand |
| TAEngine instantiations per symbol | ~12,000 | 1 |
| DataFrame constructions per symbol | ~12,000 | 1 |

## Implementation Notes

1. **Phased approach recommended:** Implement REQ-PERF-001 + REQ-PERF-004 first (quick wins, lower risk), then REQ-PERF-002 + REQ-PERF-003 (biggest impact, requires TAEngine changes), then REQ-PERF-005 (verification).

2. **TAEngine changes must be backward compatible.** Live signal generation uses TAEngine with single-candle-at-a-time semantics. The backtest optimization should not change TAEngine's public interface for non-backtest callers.

3. **filter_context adaptation:** The `SignalFilterPipeline.run_all()` at line 694 receives `filter_context["df"]` and `filter_context["ta_indicators"]`. Post-optimization, `ta_indicators` should contain pre-computed values at the current index, and `df` can be removed or replaced with a lightweight view if any filter still needs raw price data.

4. **Memory consideration:** Pre-computing full indicator arrays for 17,500 candles uses ~1-2 MB per symbol (negligible). This is not a concern.
