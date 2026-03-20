# Code Review: Backtest Calibration Round 5 (TASK-R5-01 through TASK-R5-06)
## Date: 2026-03-20
## Reviewer: code-reviewer agent

## Result: CHANGES REQUIRED

---

## Critical Issues (must fix before QA)

### 1. `src/backtesting/backtest_engine.py:1184` — D1 preload loop uses `params.symbols`, not `symbols_to_run`

The whitelist filtering is defined at line 1247, but the D1 data preload loop at line 1184 iterates over `params.symbols` (the full unfiltered list). This means the engine loads D1 data for instruments that will be skipped by the whitelist, wasting DB queries on every backtest run.

More critically: this ordering inconsistency violates the task spec. TASK-R5-03 spec states:
> "In `_simulate()`, after `params.symbols` is resolved (around line 1245), add whitelist filtering"

The D1 preload runs *before* whitelist resolution. The whitelist block must be moved to **before** the D1 preload and DXY preload loops, or both preload loops must switch to `symbols_to_run`. Currently the code contradicts its own comment at line 1175:
> "total_symbols and _backtest_progress are initialized after whitelist filtering (below), so accurate progress reporting uses the filtered count."

The comment acknowledges the ordering, but only progress tracking is corrected — data loading is not.

**Fix required:** Move the whitelist block (lines 1244-1259) to before the D1 preload loop (before line 1178), so both D1 and DXY preloads operate on the filtered symbol set. The DXY preload is not symbol-specific (it loads once), so that loop is not a problem, but the D1 loop at line 1184 is.

---

### 2. `src/config.py:168,186-188` — BLOCKED_INSTRUMENTS conflicts with INSTRUMENT_OVERRIDES for ETH/USDT

`BLOCKED_INSTRUMENTS` (line 168) blocks ETH/USDT at filter pipeline level. However, `INSTRUMENT_OVERRIDES` (lines 186-188) still contains an active override for ETH/USDT with `min_composite_score: 20`. This creates a dead entry: the override will never be consulted because ETH/USDT is rejected before `check_score_threshold()` runs.

This is not a runtime bug — the block fires first and the override is never reached. But it is a maintenance hazard: a future developer may look at INSTRUMENT_OVERRIDES, see ETH/USDT has a score override, and assume it can occasionally trade. The comment on the override says "V6-CAL-05: ETH/USDT — restore min_score 20" which directly contradicts the R5 block.

**Fix required:** Remove the ETH/USDT entry from `INSTRUMENT_OVERRIDES` or replace it with a comment explaining it is superseded by `BLOCKED_INSTRUMENTS`. Not removing it will cause confusion during future calibration rounds.

---

## Minor Issues (should fix)

### 3. `src/config.py:174` — `BACKTEST_INSTRUMENT_WHITELIST` typed as `list` without element type

```python
BACKTEST_INSTRUMENT_WHITELIST: list = [...]
```

The project uses type hints consistently. The type annotation should be `list[str]` to match the established pattern (cf. `BLOCKED_REGIMES: list = [...]` — same pattern exists there, so this is a systemic issue, but new code should not repeat it).

**Fix:** `BACKTEST_INSTRUMENT_WHITELIST: list[str] = [...]`

### 4. `src/config.py:168` — `BLOCKED_INSTRUMENTS` typed as `set` without element type

Same issue: `BLOCKED_INSTRUMENTS: set = {"ETH/USDT"}` should be `set[str]`.

**Fix:** `BLOCKED_INSTRUMENTS: set[str] = {"ETH/USDT"}`

### 5. `tests/test_simulator_v6.py:3793-3821` — Whitelist tests exercise Python built-ins, not engine code

`test_r5_backtest_whitelist_filters_symbols_logic`, `test_r5_backtest_whitelist_empty_means_all_symbols`, and `test_r5_backtest_whitelist_order_preserved` all test a locally-inlined list comprehension, not the actual backtest engine code. They would pass even if `backtest_engine.py` had a typo in the filtering logic.

The spec requires:
> "New test: `test_r5_backtest_whitelist_filters_symbols`"

A unit test that patches `BACKTEST_INSTRUMENT_WHITELIST` and calls `_simulate()` (or at minimum monkeypatches the config and verifies `symbols_to_run` is computed correctly inside the engine) would provide meaningful coverage.

**Fix:** Add a test that imports and patches `src.config.BACKTEST_INSTRUMENT_WHITELIST` and confirms the engine's filtering logic picks it up. The current tests only verify that Python list comprehensions work as expected.

### 6. `src/signals/filter_pipeline.py:185-189` — `check_blocked_instrument` runs unconditionally (no flag)

All other filters in `run_all()` are guarded by `self.apply_*` flags (e.g., `apply_regime_filter`, `apply_score_filter`). The blocked-instrument check has no corresponding `apply_blocked_instrument_filter` flag. This means there is no way to disable the block in integration tests or special backtest scenarios without mutating `BLOCKED_INSTRUMENTS` itself.

This is a minor design inconsistency rather than a bug — the spec says "O(1) set lookup, runs before all other filters," which implies always-on. But the absence of a flag differs from every other filter in the class. If tests need to exercise ETH/USDT signals directly, they must patch `BLOCKED_INSTRUMENTS` in the config module.

**Recommendation:** Either add an `apply_instrument_block_filter: bool = True` flag for consistency, or add a docstring note explaining this filter is intentionally always-on (unlike the others).

---

## Suggestions (optional)

- **TASK-R5-02 diagnostic logging (`backtest_engine.py:1429-1447`):** The added `[DXY-DIAG]` log is well-structured and useful. However, `_above_55_count` and `_below_45_count` are computed for every symbol but only correlate to filtering on `_USD_LONG_SIDE_PAIRS`. Consider adding a note in the log message that these counts are relevant only for EURUSD/GBPUSD/AUDUSD/NZDUSD to avoid confusion when reviewing logs for GC=F or BTC/USDT.

- **`docs/ARCHITECTURE_DECISION.md`:** The document exceeds the spec template with useful additions (table format for criteria, hard-stop refinement for PF 1.2-1.29, effort estimates). This is an improvement over the spec template. No changes needed.

- **TASK-R5-05 (Appendix: R3-R4 Discrepancy):** The investigation is thorough and the conclusion is sound. The secondary factor (CAL3-04 VOLATILE block for forex) correctly identified. The analysis is consistent with the code.

- **TASK-R5-06 (`SHORT_ENABLED` flag):** The flag exists but is not read anywhere in the codebase — it is purely documentary. This is acceptable per the task spec ("No logic changes, SHORT code preserved for potential D1 pivot"), but the comment should say explicitly that the flag is currently unused to prevent a future developer from assuming it controls something.

---

## Summary

The R5 implementation correctly addresses the primary goal (ETH/USDT block, DXY diagnostics, whitelist, architecture documentation). The blocked-instrument check is properly placed as the first filter in `run_all()`, the DXY diagnostic logging is permanent and informative, and the config constants are properly typed with backward-compatible defaults.

Two issues require fixes before QA: the whitelist block is positioned after the D1 preload loop (inefficiency and ordering inconsistency), and the stale ETH/USDT entry in `INSTRUMENT_OVERRIDES` creates a maintenance hazard. The whitelist tests also do not exercise the actual engine code, reducing their regression-detection value.
