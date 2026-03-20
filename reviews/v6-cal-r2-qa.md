# QA Report: Calibration Round 2 + Trailing Stop Fixes
## Date: 2026-03-20
## Tester: qa agent

## Result: PASSED

---

## Acceptance criteria results

| Criterion | Result | Notes |
|-----------|--------|-------|
| All simulator tests pass (v3 + v5 + v6) | PASSED | 327/327 passed in 2.78s |
| `open_trade` includes `"original_stop_loss"` key | PASSED | Line 1400 in backtest_engine.py: `"original_stop_loss": sl` |
| `_check_exit` checks original SL when trailing is active (worst-case) | PASSED | Lines 1496-1513: `original_sl_hit` checked before `sl_hit`, exits at `original_sl` with `sl_hit` reason |
| MAE calculation uses `original_stop_loss` for sl_distance | PASSED | Line 1554: `mae_sl = open_trade.get("original_stop_loss", sl)` |
| `_compute_summary` excludes `end_of_data` from PF and `total_pnl_usd` | PASSED | Lines 268-291: `metric_trades` filters out `end_of_data`; PF/PnL computed on `metric_trades` only |
| `SHORT_SCORE_MULTIPLIER = 1.3` | PASSED | `config.py` line 198: `SHORT_SCORE_MULTIPLIER: float = 1.3` |
| `BLOCKED_REGIMES` includes RANGING, TREND_BEAR, STRONG_TREND_BEAR, TREND_BULL | PASSED | `config.py` line 152: `["RANGING", "TREND_BEAR", "STRONG_TREND_BEAR", "TREND_BULL"]` |
| `AUDUSD=X` has `min_composite_score=22` | PASSED | `config.py` lines 189-191: `"AUDUSD=X": {"min_composite_score": 22}` |
| `SPY` has `min_composite_score=22` with `STRONG_TREND_BULL + VOLATILE` | PASSED | `config.py` lines 184-187: `min_composite_score=22`, `allowed_regimes=["STRONG_TREND_BULL", "VOLATILE"]` |
| Gap through both SL test exists (worst case LONG) | PASSED | `test_cal2_06_gap_through_both_sl_worst_case_long` at line 2568 — passes |
| Gap through both SL test exists (worst case SHORT) | PASSED | `test_cal2_06_gap_through_both_sl_worst_case_short` at line 2596 — passes |
| MAE uses original `sl_distance` after trailing | PASSED | `test_cal2_06_mae_uses_original_sl_distance_after_trailing` at line 2624 — passes |
| Server health endpoint responds | PASSED | `{"status":"ok","version":"2.0","database":"ok"}` |

---

## Test run summary

```
327 passed in 2.78s
```

Breakdown:
- `test_simulator_v3.py`: 27 tests — all PASSED
- `test_simulator_v5.py`: 133 tests — all PASSED
- `test_simulator_v6.py`: 167 tests — all PASSED

---

## Code review findings

### `open_trade["original_stop_loss"]` presence
Confirmed at `_simulate_symbol()` line 1400:
```python
"original_stop_loss": sl,
```
The key is set at trade creation and is never overwritten during trailing stop activation — only `stop_loss` is mutated. Correct.

### `_check_exit` worst-case logic
Trailing stop activation block (lines 1476-1492) runs first, updating `open_trade["stop_loss"]` to trailing level and local `sl` variable. Then `original_sl` is fetched via `open_trade.get("original_stop_loss", sl)`. The check order is:
1. `original_sl_hit` (gap below original SL) → `exit_reason = "sl_hit"`, exits at `original_sl`
2. `sl_hit` (trailing SL breached) → `exit_reason = "trailing_stop"`
3. `tp_hit` → `exit_reason = "tp_hit"`

This correctly enforces worst-case principle (CLAUDE.md §6).

### MAE calculation
Line 1554:
```python
mae_sl = open_trade.get("original_stop_loss", sl)
sl_distance = abs(entry - mae_sl)
```
Uses original SL (pre-trailing) for threshold computation. Prevents false MAE exits when trailing SL is tight (20% of TP distance).

### `_compute_summary` end_of_data exclusion
Lines 268-291 correctly split trades into `eod_trades` / `real_trades`. PF uses only `real_trades` (variable `metric_trades`). Additionally, `total_pnl_usd` excludes end_of_data (only `metric_trades`), while `total_pnl_usd_incl_eod` provides the full figure for reference. End_of_data trades appear in `end_of_data_count` / `end_of_data_pnl` fields.

---

## Bugs found

None.

---

## Regression check

All 27 v3 tests and 133 v5 tests pass without modification. No regression detected. The trailing stop logic and `original_stop_loss` field are additive changes that do not affect existing SL/TP paths when `trailing_sl_active` is False (default).

---

## Summary

All 327 simulator tests pass (v3 + v5 + v6). All four code verification criteria (`original_stop_loss` in `open_trade`, worst-case gap logic in `_check_exit`, original SL distance in MAE calculation, end_of_data exclusion from PF/PnL) are correctly implemented. Config values for calibration round 2 (`SHORT_SCORE_MULTIPLIER=1.3`, `BLOCKED_REGIMES`, per-instrument overrides for AUDUSD=X and SPY) match the expected values. Server health endpoint is responsive.
