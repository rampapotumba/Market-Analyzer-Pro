# Code Review: v6 Calibration (CAL-01..CAL-09)
## Date: 2026-03-20
## Reviewer: code-reviewer agent

## Result: CHANGES REQUIRED

---

## Critical issues (must fix before QA)

### 1. `filter_pipeline.py:421` — `check_momentum()` docstring claims RSI < 40, code uses RSI < 30

The docstring says:
```
SHORT: RSI < SHORT_RSI_THRESHOLD (40) AND MACD line < Signal line
V6 (TASK-V6-08): SHORT uses stricter RSI threshold (< 40 instead of < 50)
```
But `SHORT_RSI_THRESHOLD = 30` in `config.py`, and the actual check at line 422 is:
```python
if direction == "SHORT" and not (rsi_f < SHORT_RSI_THRESHOLD and macd_f < sig_f):
```
The docstring says 40, the config says 30, the tests verify 30 (`test_v6_08_short_rsi_35_blocks` expects RSI=35 to be blocked, consistent with threshold=30). The **code is correct** (uses 30), but the **docstring is wrong** — it says "< 40 instead of < 50" when it should say "< 30". This is a correctness hazard: anyone reading the docstring to understand the filter will implement a wrong threshold in live mode.

**Fix**: Update the docstring to say `SHORT_RSI_THRESHOLD (30)`.

---

### 2. `backtest_engine.py:1477` — `_check_exit()` hardcodes account_size=1000 instead of using passed parameter

In `_check_exit()`, `_compute_pnl()` is called with a hardcoded `Decimal("1000")`:
```python
pnl_pips, pnl_usd = _compute_pnl(
    direction, entry, exit_price,
    Decimal(str(open_trade["position_pct"])),
    Decimal("1000"),  # account size for P&L pct calculation
    market_type,
)
```
But `_simulate_symbol()` receives `account_size: Decimal` as a parameter and this value is available throughout the method. If a user runs a backtest with `account_size=Decimal("10000")`, every closed trade inside `_check_exit()` will compute P&L against $1000, producing figures 10x smaller than expected. The end-of-data path at line 1353 correctly uses the passed `account_size`.

This is a pre-existing bug, but since CAL tasks touch the `_check_exit()` path (adding `sl_price` return, time exit) the responsibility to flag it falls here.

**Fix**: Pass `account_size` from `_simulate_symbol()` into `_check_exit()` as a parameter.

---

### 3. `backtest_engine.py:1345-1347` — `open_trade` dict missing `sl_price` field; `_compute_summary` references `t.sl_price` but it is never set on in-progress trades

`_check_exit()` correctly sets `sl_price=sl` on the returned `BacktestTradeResult` (line 1504). However, the `end_of_data` close path (lines 1366-1383) creates a `BacktestTradeResult` **without** `sl_price`:

```python
trades.append(BacktestTradeResult(
    ...
    # sl_price is NOT passed here
))
```

`_compute_summary()` at line 482 reads `t.sl_price` for every trade including end-of-data trades:
```python
sl_field = getattr(t, "sl_price", None)
```
This particular path is safe because `getattr(..., None)` fallback is used. However, the MAE % calculation will silently skip all end-of-data trades, which may be confusing if end-of-data count is large.

More importantly: `open_trade` dict at line 1330 does not include `sl_price`. The `stop_loss` key is there (`open_trade["stop_loss"]`), but `sl_price` is only set on the final `BacktestTradeResult` inside `_check_exit()`. This is fine architecturally, but see issue #2 — the `sl` value used in `_check_exit()` is correct since it comes from `open_trade["stop_loss"]`.

**Fix**: Pass `sl_price=open_trade["stop_loss"]` in the end-of-data `BacktestTradeResult` construction. This makes MAE % available for those trades too and keeps the model consistent.

---

## Minor issues (should fix)

### 4. `config.py:191` — `SHORT_SCORE_MULTIPLIER` named as "multiplier" but the comment says "SHORT WR 12.04%, sell bucket -$1,202. Требуем 2x conviction"

The naming is clear. However, the comment just above says "RSI < 30 (deeply oversold)" for a SHORT signal — but "oversold" traditionally describes a BUY condition (price dropped a lot). For a SHORT signal, RSI < 30 means the asset is deeply **over-sold**; the comment should say "deeply bearish momentum" or clarify the intent to prevent confusion for future maintainers.

### 5. `backtest_engine.py:396-406` — `by_score_scaled` in `_compute_summary()` uses `_TA_WEIGHT` as scale floor, but `_get_signal_strength_scaled()` uses `AVAILABLE_WEIGHT_FLOOR` as floor

`_compute_summary()`:
```python
_bucket_scale = max(_TA_WEIGHT, AVAILABLE_WEIGHT_FLOOR)
```
`_TA_WEIGHT = 0.45`, `AVAILABLE_WEIGHT_FLOOR = 0.65` → `_bucket_scale = 0.65`. This is correct as a one-time calculation. However, if `_TA_WEIGHT` is ever changed to be > 0.65, the floor would silently stop having effect. The intent should be:
```python
_bucket_scale = AVAILABLE_WEIGHT_FLOOR  # the floor is the canonical scale for backtest reporting
```
This removes a hidden dependency on the relative values of two constants.

### 6. `filter_pipeline.py:192-195` — Weekday multiplier imports inside hot loop

```python
from src.config import WEAK_WEEKDAY_SCORE_MULTIPLIER, WEAK_WEEKDAYS
```
This import runs **inside `run_all()`** on every signal evaluation. Python caches module imports, so there is no real I/O, but the attribute lookup on `sys.modules` still happens per call. Move these imports to the top of the file alongside the other `src.config` constants, or cache them at class level. In the backtest loop this runs tens of thousands of times.

### 7. `backtest_engine.py:1442` — `_time_exit_candles` dict is recreated on every candle in every open trade check

```python
_time_exit_candles: dict[str, int] = {"H1": 24, "H4": 20, "D1": 10}
```
This dict literal is inside `_check_exit()`, which is called for every candle while a trade is open. This constant should be a module-level constant (comparable to existing `_COOLDOWN_MINUTES`). The spec (SIM-35) defines these as fixed values; placing them inside the function obscures that they are configuration, not local computation.

### 8. `tests/test_simulator_v5.py:150-157` — `test_sim25_crypto_higher_threshold_rejected` has stale comment

The test comment says:
```
# BTC/USDT override min_composite_score=15 (v6: lowered from 20)
```
But CAL-05 raised BTC/USDT threshold to 25, not lowered it to 15. The test assertion is correct (6 < 25 → rejected), but the comment is factually wrong and will confuse future readers who consult the v5 test file.

### 9. `tests/test_simulator_v5.py:241-248` — `test_sim28_btc_higher_threshold` has stale comment

```
# 6 < 15 (BTC/USDT instrument override, v6: lowered from 20 in TASK-V6-03)
```
Same issue: BTC was raised to 25 by CAL-05, not lowered to 15. The assertion `not passed` is correct but the comment is misleading.

### 10. `backtest_engine.py:880` — Walk-forward `_simulate()` called 3 times: full range + IS + OOS

When `enable_walk_forward=True`, `_simulate()` is called for:
1. The full date range (line 880)
2. In-sample period (line 905)
3. Out-of-sample period (line 906)

This means the backtest runs **three full simulations** instead of two. The IS and OOS results together cover the same data as the full run, so the full run is redundant when walk-forward is enabled. This is a performance issue, not a correctness bug, but for long backtests it triples execution time.

---

## Suggestions (optional)

- `check_score_threshold()` applies multipliers in the order: `base * available_weight_floor * SHORT_multiplier * weekday_multiplier`. The docstring lists priority as instrument_override > constructor > market_default > global_default. Consider adding the multiplier application order to the docstring to prevent future bugs when adding new multipliers.

- The `SignalFilterPipeline.__new__(SignalFilterPipeline)` pattern in the `BacktestEngine` static wrapper methods (lines 1512-1558) bypasses `__init__`, which means `rejection_counts`, `total_signals`, and `passed_signals` are never initialized. Currently safe because these wrappers only call individual `check_*` methods (which don't touch counters), but any future code that calls `get_stats()` on one of these pipeline instances will get an `AttributeError`. Consider using `SignalFilterPipeline()` instead, or adding a `__new__`-safe initialization guard.

- The `BLOCKED_REGIMES` list in `config.py` now includes `TREND_BEAR` and `STRONG_TREND_BEAR`. The v5 test `test_sim26_trend_bull_allowed` tests that `TREND_BULL` is allowed. There is no corresponding test verifying `TREND_BEAR` is now blocked for **non-overridden** instruments. Adding `test_sim26_trend_bear_blocked` would give explicit coverage for CAL-02.

---

## Summary

The calibration logic is structurally sound. `AVAILABLE_WEIGHT_FLOOR`, `SHORT_SCORE_MULTIPLIER`, `WEAK_WEEKDAY_SCORE_MULTIPLIER`, and the expanded `BLOCKED_REGIMES` are all applied correctly in `check_score_threshold()` and `check_signal_strength()`. The priority chain (instrument_override > constructor > market_default > global_default) is preserved. `sl_price` is properly added to `BacktestTradeResult` and used correctly in `_compute_summary()` for MAE % calculation. The live mode path (`available_weight=1.0`) is unaffected by the floor (`max(1.0, 0.65) = 1.0`). No hardcoded secrets or SQL injection risks are present.

Two issues require fixes before QA: the misleading `check_momentum()` docstring (RSI threshold documented as 40 but implemented as 30) creates a live-vs-backtest consistency risk if the live engine is implemented from the docstring, and the hardcoded `Decimal("1000")` in `_check_exit()` causes incorrect P&L for non-default account sizes. The missing `sl_price` in end-of-data trade construction should also be corrected for consistency.
