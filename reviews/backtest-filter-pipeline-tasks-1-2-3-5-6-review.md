# Code Review: Backtest Filter Pipeline — Tasks 1, 2, 3, 5, 6
## Date: 2026-03-19
## Reviewer: code-reviewer agent

## Result: CHANGES REQUIRED

---

## Critical issues (must fix before proceeding to Task 4)

### 1. `check_score_threshold` — wrong priority order for `min_composite_score`

**File:** `src/signals/filter_pipeline.py:160–175`

```python
threshold = MIN_COMPOSITE_SCORE_CRYPTO if market_type == "crypto" else MIN_COMPOSITE_SCORE
overrides = INSTRUMENT_OVERRIDES.get(symbol, {})
if "min_composite_score" in overrides:
    threshold = overrides["min_composite_score"]
if self.min_composite_score is not None:   # ← constructor arg overrides instrument override
    threshold = self.min_composite_score
```

The spec states (CLAUDE.md and `claude-tasks.md`):
```
Priority: instrument_override > global_config > global_default
```

But the current code allows `self.min_composite_score` (the constructor arg representing `params.min_composite_score`) to **override the per-instrument override**, reversing the stated priority. If a caller passes `min_composite_score=12.0` via `BacktestParams`, this will bypass BTC/USDT's hardcoded `min_composite_score: 20`, which is a security/correctness issue.

The correct order must be:
```
instrument_override > constructor_min_composite_score > market_default > global_default
```

This is already implemented correctly in `BacktestEngine._generate_signal()` (lines 1019–1028), where the instrument override is applied last (highest priority). The `filter_pipeline.py` implementation has the priorities inverted.

**Impact:** BTC/USDT and GBPUSD=X instrument overrides can be bypassed by `BacktestParams.min_composite_score`.

The test `test_sim42_check_score_threshold_custom_min_score` at line 1361 actually **validates the wrong behavior**: it tests that a `min_composite_score=25.0` constructor arg overrides the default, but doesn't verify that it cannot override a per-symbol instrument override. This test needs a companion negative test.

---

### 2. `apply_session_filter` from `BacktestParams` is **not forwarded** to `_simulate_symbol()`

**File:** `src/backtesting/backtest_engine.py:560–570`

```python
filter_flags={
    "ranging": params.apply_ranging_filter,
    "d1_trend": params.apply_d1_trend_filter,
    "volume": params.apply_volume_filter,
    "weekday": params.apply_weekday_filter,
    "momentum": params.apply_momentum_filter,
    "calendar": params.apply_calendar_filter,
    "min_composite_score": params.min_composite_score,
    # ← "session" key is MISSING
},
```

`BacktestParams.apply_session_filter` (Task 3 requirement, `backtest_params.py:28`) is stored in the params object, but it is never passed into `filter_flags` and therefore never checked in `_simulate_symbol()`. The inline session filter at line 649–652 always runs unconditionally regardless of `params.apply_session_filter`.

**Result:** `apply_session_filter=False` in `BacktestParams` has zero effect on the backtest. The `test_backtest_params_has_session_filter` test (line 1904) only verifies the field exists on the model; it does not verify that the flag actually controls filter behavior in the engine. This gap means the test passes while the bug exists.

---

## Minor issues (should fix)

### 3. Inline session filter in `_simulate_symbol()` duplicates `SignalFilterPipeline.check_session_liquidity()`

**File:** `src/backtesting/backtest_engine.py:649–652`

```python
if market_type == "forex" and symbol in _FOREX_PAIRS_EU_NA:
    if _is_asian_session(candle_ts):
        continue
```

This logic is now also in `filter_pipeline.py:284–300`. Task 3 explicitly states the goal is to move this logic into the pipeline, yet the old inline version remains and the pipeline version is not called. This creates the divergence that the entire task set is designed to eliminate. The inline version should be removed in Task 4, but if left until then it must at least respect the `apply_session_filter` flag (Critical Issue 2 above).

### 4. `_get_signal_strength()` bucket ordering has a dead branch

**File:** `src/backtesting/backtest_engine.py:113–128`

```python
def _get_signal_strength(composite: float) -> str:
    if composite >= 20.0:
        return "STRONG_BUY"
    elif composite >= 10.0:
        return "BUY"
    elif composite <= -20.0:
        return "STRONG_SELL"
    elif composite <= -10.0:
        return "SELL"
    elif composite >= 7.0:       # ← can never be reached
        return "WEAK_BUY"
    elif composite <= -7.0:
        return "WEAK_SELL"
    else:
        return "HOLD"
```

The `WEAK_BUY` branch (`composite >= 7.0`) is unreachable: to reach it, the code must have already passed the `composite >= 10.0` check. A value like `8.0` would fall into `BUY`, not `WEAK_BUY`. The spec defines `weak_buy: +7..+10`, but the current logic maps `+7..+10` to `"BUY"`.

This does not affect behavior in practice, because `ALLOWED_SIGNAL_STRENGTHS` only contains `BUY/STRONG_BUY/SELL/STRONG_SELL` and the score threshold is 15+, so values in the 7–10 range never reach this function from actual signals. However, the test at line 672 (`test_sim31_weak_buy_rejected`) calls `_get_signal_strength(8.0)` and asserts it returns `"WEAK_BUY"` — this test **will fail** with the current implementation, as `8.0` returns `"BUY"`.

This is a pre-existing bug carried from v4, but the new test exposes it.

### 5. `_simulate_symbol()` has a broken `i += 1` skip

**File:** `src/backtesting/backtest_engine.py:706`

```python
open_trade = { ... }
# Skip to next candle
i += 1
```

Inside a `for i in range(...)` loop, `i += 1` has no effect — Python reassigns `i` at the start of the next iteration. This means the "next candle" used as the entry candle is immediately rechecked as a `current_candle` in the exit path. This is a pre-existing bug (not introduced in this PR), but since it affects correctness of all backtest results it is worth noting.

### 6. `load_economic_calendar.py` — missing idempotency guard

**File:** `scripts/load_economic_calendar.py:310–330`

The script calls `upsert_economic_event` in a loop but rolls back the entire transaction if any single event fails (due to `async with session.begin()`). If the function is rerun after a partial failure, some events may be inserted twice (depending on the upsert implementation). The script also does not print a summary of which events already existed vs. were newly inserted.

This is a script (not production code), so the severity is low, but the `--dry-run` flag does not help distinguish already-loaded from new events on a re-run.

### 7. Task 3: `check_session_liquidity()` is added to `SignalFilterPipeline.run_all()` but the backtest engine does not use it

As described in Critical Issue 2, `apply_session_filter` is never passed to `_simulate_symbol()`. This means `run_all()` correctly contains the session filter, but since `SignalFilterPipeline` is not called from `BacktestEngine._simulate_symbol()` at all (Task 4 is not yet done), there is currently no end-to-end path where the pipeline's session filter actually runs during a backtest.

---

## Suggestions (optional)

- The `mock_ta_indicators` fixture (line 67–75) uses `"rsi"` key (TAEngine native), but several tests call the `BacktestEngine._check_momentum_alignment()` directly with `"rsi_14"` key. Both paths work because of the fallback, but fixture inconsistency can confuse future test authors.

- `_build_event_list()` in the calendar loader returns events unsorted. If insertion order matters for the upsert, consider returning `sorted(events, key=lambda e: e["event_date"])`.

- The Task 3 spec says the session filter should be "the first filter checked since it's the cheapest." This is correctly implemented in `run_all()` — confirmed, no action needed.

- The Task 2 debug logging (`logger.debug("[SIM-30] Momentum check...")`) is added to both `BacktestEngine._check_momentum_alignment()` and `SignalFilterPipeline.check_momentum()`. This is correct per the task spec. No issue.

---

## Summary

Tasks 1 (regime field), 2 (momentum debug logging), 5 (volume skip for forex), and 6 (calendar loader script) are implemented correctly and meet their acceptance criteria. Task 3 (session filter) has a structural gap: the filter was correctly added to `SignalFilterPipeline` and `BacktestParams`, but `BacktestParams.apply_session_filter` is never forwarded to `_simulate_symbol()`, making the toggle a no-op. Additionally, `filter_pipeline.py` has an inverted priority order for `min_composite_score` (constructor arg overrides per-instrument overrides, violating the spec). Both issues must be fixed before Task 4, as Task 4 depends on the pipeline behaving correctly.
