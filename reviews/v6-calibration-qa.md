# QA Report: v6 Calibration (CAL-01..CAL-09)
## Date: 2026-03-20
## Tester: qa agent

## Result: PASSED

---

## Acceptance criteria results

| Criterion | Result | Notes |
|-----------|--------|-------|
| AVAILABLE_WEIGHT_FLOOR = 0.65 | PASS | `src/config.py` line 208: `AVAILABLE_WEIGHT_FLOOR: float = 0.65` |
| BLOCKED_REGIMES contains RANGING, TREND_BEAR, STRONG_TREND_BEAR | PASS | `src/config.py` line 151: `["RANGING", "TREND_BEAR", "STRONG_TREND_BEAR"]` |
| SHORT_SCORE_MULTIPLIER = 2.0 | PASS | `src/config.py` line 191: `SHORT_SCORE_MULTIPLIER: float = 2.0` |
| SHORT_RSI_THRESHOLD = 30 | PASS | `src/config.py` line 192: `SHORT_RSI_THRESHOLD: int = 30` |
| BTC/USDT min_composite_score = 25 | PASS | `src/config.py` line 158: `"min_composite_score": 25` |
| SPY min_composite_score = 30 | PASS | `src/config.py` line 183: `"min_composite_score": 30` |
| check_score_threshold uses max(available_weight, AVAILABLE_WEIGHT_FLOOR) | PASS | `filter_pipeline.py` line 324: `effective_weight = max(available_weight, AVAILABLE_WEIGHT_FLOOR)` |
| check_momentum uses SHORT_RSI_THRESHOLD (should be 30) | PASS | `filter_pipeline.py` line 423: `rsi_f < SHORT_RSI_THRESHOLD`, config value = 30 |
| Imports at file level (not inline) | PASS | `WEAK_WEEKDAY_SCORE_MULTIPLIER`, `WEAK_WEEKDAYS` at top-level line 16 |
| _check_exit takes account_size parameter | PASS | `backtest_engine.py` line 1401: `account_size: Decimal = Decimal("1000")` as param |
| _compute_pnl in _check_exit uses account_size parameter (not hardcoded) | PASS | `backtest_engine.py` line 1483: uses `account_size` variable, not `Decimal("1000")` |
| _TIME_EXIT_CANDLES at module level with H1=24 | PASS | `backtest_engine.py` line 102: `_TIME_EXIT_CANDLES: dict[str, int] = {"H1": 24, "H4": 20, "D1": 10}` |
| End-of-data path includes sl_price | PASS | `backtest_engine.py` line 1388: `sl_price=open_trade["stop_loss"]` |
| All 291 unit tests pass | PASS | `pytest tests/test_simulator_v3.py tests/test_simulator_v5.py tests/test_simulator_v6.py` — 291 passed in 1.88s |
| Server health endpoint responds | PASS | `GET /api/v2/health` → `{"status":"ok","version":"2.0","database":"ok"}` |

---

## Critical issues found by code review — verification

### Review Issue #1: check_momentum() docstring says RSI < 40, code uses RSI < 30
**Status: PARTIALLY FIXED**

- The **method docstring** (lines 397-403) was corrected: now reads `SHORT: RSI < SHORT_RSI_THRESHOLD (30) AND ...` and `< 30 instead of < 50`.
- However, the **inline comment** at line 422 was NOT updated: still reads `# V6 TASK-V6-08: SHORT requires RSI < 40 (not just < 50) for stronger bearish conviction`.
- The code logic itself is correct (`rsi_f < SHORT_RSI_THRESHOLD`, config = 30).
- Risk: any engineer reading line 422 in isolation still sees the wrong number 40.

### Review Issue #2: _check_exit() hardcoded account_size=1000
**Status: FIXED**

`_compute_pnl()` at line 1480-1485 uses the `account_size` parameter, not a hardcoded `Decimal("1000")`. The `Decimal("1000")` that appears at line 1401 is only the default value for the optional parameter, which is semantically correct.

### Review Issue #3: end-of-data path missing sl_price
**Status: FIXED**

`BacktestTradeResult` constructed at line 1371-1389 includes `sl_price=open_trade["stop_loss"]`.

---

## Bugs found

### Bug 1: Stale inline comment in filter_pipeline.py line 422
- Severity: Minor
- Steps to reproduce:
  1. Open `src/signals/filter_pipeline.py`, line 422
  2. Read: `# V6 TASK-V6-08: SHORT requires RSI < 40 (not just < 50) for stronger bearish conviction`
- Expected: Comment should say `RSI < 30 (not just < 50)` matching `SHORT_RSI_THRESHOLD = 30`
- Actual: Comment says `RSI < 40` — wrong number, creates confusion for maintainers

### Bug 2: Stale assert message in test_simulator_v5.py line 156
- Severity: Minor
- Steps to reproduce:
  1. Open `tests/test_simulator_v5.py`, line 156
  2. Read assert message: `"Crypto score 6 should be rejected (BTC/USDT threshold=15)"`
- Expected: Message should say `threshold=25` (CAL-05 raised BTC/USDT from 20 to 25)
- Actual: Message says `threshold=15` — wrong number; test logic is correct (not=passed is asserted correctly), only the diagnostic message is misleading

---

## Regression check

- **test_simulator_v3.py**: 30 tests — all PASSED. v3 SL/TP, slippage, ATR, swap, P&L unchanged.
- **test_simulator_v5.py**: 133 tests — all PASSED. v5 filters, signal strength, backtest engine unaffected.
- **test_simulator_v6.py**: 128 tests — all PASSED. All CAL-01..CAL-09 acceptance tests green.
- **Total**: 291 tests, 0 failures, 0 errors.

---

## Summary

The v6 calibration implementation is functionally correct. All config values (AVAILABLE_WEIGHT_FLOOR=0.65, BLOCKED_REGIMES with TREND_BEAR/STRONG_TREND_BEAR, SHORT_SCORE_MULTIPLIER=2.0, SHORT_RSI_THRESHOLD=30, BTC min_score=25, SPY min_score=30) are properly set and enforced in the filter pipeline. The three critical issues identified in the code review (hardcoded account_size, missing sl_price in end-of-data path, and misleading docstring) have all been addressed — two fully fixed, and the docstring partially fixed (method-level docstring corrected but inline comment at line 422 still says "RSI < 40"). Two minor cosmetic bugs remain: the inline comment and a stale assert message, neither of which affects runtime behavior. Feature is ready for release.
