# Code Review: v6 Calibration Round 2
## Date: 2026-03-20
## Reviewer: code-reviewer agent

## Result: CHANGES REQUIRED

---

## Critical issues (must fix before QA)

### 1. `backtest_engine.py`:596 — Trailing stop label incorrect for existing SL when trailing is active

When `trailing_sl_active` is True and the SL is hit, the code at line 1509 labels the exit
as `"trailing_stop"`. This is correct intent. However, the check at lines 1478–1490 sets
`trailing_sl_active = True` on the **same candle** as activation and immediately updates `sl`
for the current candle's SL check. This means on the activation candle, if the price also
crosses the trailing SL level on the same bar, the exit reason is `"trailing_stop"`.

The actual problem is subtler: `open_trade["stop_loss"]` is permanently overwritten to
`trailing_sl_new` at line 1488. On subsequent candles, the **regular SL check** at lines
1496–1500 uses this overwritten value. But the label assignment at line 1509 only triggers
`"trailing_stop"` if `open_trade.get("trailing_sl_active")` is True at the point of exit.
Since `trailing_sl_active` is checked at line 1509 **after** `sl` has already been updated,
this path works. However, the test `test_cal2_06_trailing_stop_exits_on_next_candle` sets
`trailing_sl_active = True` and `stop_loss = Decimal("1.1020")` directly, and the engine
calls the standard SL path. The standard path correctly detects the exit as `"trailing_stop"`.
**This is a correctness gap**: if the position reaches its original SL (below the trailing SL)
in a large adverse move, the standard SL path fires with `sl = trailing_sl_new`, not the
original SL. This means the exit price is the trailing SL level, not the original SL level.
The trade is labeled `"trailing_stop"` even though it may be a genuine adverse stop.

Specifically: once `trailing_sl_active = True`, the original SL is **lost**. If price gaps
through both `trailing_sl_new` and the original SL in a single candle, the exit is at
`trailing_sl_new` with reason `"trailing_stop"` (a profit-lock exit), not `"sl_hit"`.
This overestimates PnL in adverse scenarios. **Violates the "worst case" principle** in
`CLAUDE.md` §6.

**Fix**: store `original_stop_loss` in `open_trade` when creating the position. After
`trailing_sl_active` is set, keep checking `original_stop_loss` for the worst-case path.
If `candle_low <= original_stop_loss` (LONG), exit at `original_stop_loss` with
`"sl_hit"` (worst case), not at trailing SL.

---

### 2. `backtest_engine.py`:1533 — MAE exit uses potentially updated (trailing) SL distance

At line 1533: `sl_distance = abs(entry - sl)`. After trailing stop activation, `sl` is
`open_trade["stop_loss"]` which has been rewritten to the trailing SL. The MAE exit
threshold `mae_threshold = sl_distance * Decimal("0.60")` is now computed against the
**trailing** SL distance (which is much smaller — only 20% of TP dist), making the MAE
threshold effectively near zero and causing immediate MAE exits on the next candle. This is
an incorrect interaction between trailing stop logic and MAE exit logic.

**Fix**: use the original SL distance (before trailing) for MAE threshold computation, or
disable MAE exit once trailing stop is active.

---

## Minor issues (should fix)

### 3. `backtest_engine.py`:1402 — `i += 1` inside a `for` loop has no effect

Line 1402 (`i += 1`) in the `for i in range(...)` loop is a no-op in Python. The `for`
loop iterator ignores mutations of the loop variable. The comment says "Skip to next
candle", but this does not actually skip. On the next iteration, `i` resumes from `i+1`
naturally (from the loop counter), but any intent to skip candle processing for the entry
candle is not achieved. This is pre-existing code, but the trailing stop logic added in
this PR relies on the same loop. Worth documenting that this skip is cosmetic.

### 4. `tests/test_simulator_v6.py`:2579 — `check_regime` call missing required positional argument

At line 2579: `pipeline.check_regime("TREND_BULL")` — the method signature is
`check_regime(self, regime: str, symbol: str = "")`. The call omits `symbol`, which is fine
since it defaults to `""`. However the test name implies it's testing the TREND_BULL block
specifically (not the empty-symbol path), which works correctly. This is not a bug but a
potential source of confusion.

### 5. `backtest_engine.py`:506–511 — Exit reason counts use `all trades` (including eod)

`sl_hit_count`, `tp_hit_count`, `mae_exit_count`, `time_exit_count`, `trailing_stop_count`
are counted over `trades` (all trades), not `metric_trades`. This is inconsistent with the
CAL2-01/02 policy of excluding eod from all metrics. An eod trade will never have these exit
reasons in practice, but the asymmetry is a maintenance hazard. The task spec (CAL2-01 and
CAL2-02) does not explicitly require this fix, but it is worth noting.

### 6. `backtest_engine.py`:299 — `long_count` / `short_count` use all trades (including eod)

Lines 299–300 count `long_count` and `short_count` over `trades` (all, including eod).
This is inconsistent with the eod-exclusion policy for primary metrics. Again not a spec
violation, but worth noting as a maintenance concern.

### 7. `tests/test_simulator_v6.py`:2421 — `_make_check_exit_trade` missing `"trailing_sl_active"` key
in `_make_open_trade` helper used in earlier tests (`TestV611TimeAndMaeExit._make_open_trade`)

The helper at lines 1067–1092 does not include `"trailing_sl_active": False`. The
`_check_exit` code at line 1481 calls `open_trade.get("trailing_sl_active")`, which will
return `None` for trades made with the old helper. `None` is falsy, so the check still
works, but the test for trailing stop not activating (`test_cal2_06_trailing_not_active_below_50pct`)
verifies `trade["trailing_sl_active"] is False`, which would fail if the dict lacks the key.
In practice the CAL2 tests use `_make_check_exit_trade` which does include the key. The
v6-11 tests use `_make_open_trade` which does not. If v6-11 tests interact with trailing
stop logic, they may fail or behave unexpectedly. The `_make_open_trade` helper in
`TestV611TimeAndMaeExit` should be updated to include `"trailing_sl_active": False`.

---

## Suggestions (optional)

- **RANGING min_rr = 0.7**: The new RANGING `target_rr = 0.9` with `min_rr = 0.7` creates
  R:R configurations below 1.0. The `validate()` method in `RiskManagerV2` uses the old
  `_REGIME_MIN_RR` dict (which has `RANGING: 1.0`), not `REGIME_RR_MAP`. After the CAL2-05
  change, `validate()` still enforces `min_rr = 1.0` for RANGING, which is inconsistent.
  Backtest uses `calculate_levels_for_regime` (which uses `REGIME_RR_MAP`), so trades can
  be placed at 0.9 R:R, but `validate()` would reject them. If `validate()` is called in
  any live path, RANGING trades will fail validation. This inconsistency should be resolved.

- **Concentration warning uses `total_pnl` (excl eod)** as denominator (correct), but
  `by_symbol` PnL is also computed from `metric_trades` (excl eod). The two are consistent.
  No issue.

- **Test `test_cal2_09_concentration_warning_top2`**: the assertion only checks for
  `"Top 2"` in the warning string. The actual string is `"Top 2 instruments contribute 75.0% of PnL"`.
  The check is sufficient but fragile to string formatting changes.

- **`_make_trade_cal2` helper** sets `t.sl_price = Decimal(entry_price) - Decimal("0.0050")`.
  For trades where direction is SHORT or for CAL2-09 tests where `sl_price` is irrelevant,
  this is fine. However it creates a non-zero MAE percentage by default, which could skew
  MAE-related tests. Not a current bug but worth noting.

---

## Summary

The implementation correctly addresses CAL2-01 through CAL2-09: eod exclusion from all
primary metrics is consistent, weekend block is properly unconditional, R:R values match the
spec, config changes for SHORT_SCORE_MULTIPLIER, BLOCKED_REGIMES, and INSTRUMENT_OVERRIDES
are correct, and the concentration warning logic is sound.

The **critical concern** is the trailing stop implementation (CAL2-06): once activated, the
original SL is permanently overwritten, violating the project's "worst case" principle. In
an adverse gap scenario, the trade exits at a profitable trailing level rather than the true
SL, overstating PnL. Additionally, the MAE exit threshold is distorted after trailing
activation because it uses the now-reduced SL distance. Both issues must be fixed before QA.
The 38 new tests cover the happy paths well, but lack adversarial tests (gap through both
trailing SL and original SL, MAE exit interaction after trailing activation).
