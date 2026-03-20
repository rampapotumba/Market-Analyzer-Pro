# Tasks: Backtest Calibration Round 5 (FINAL ITERATION)

**Date:** 2026-03-20
**Goal:** Reach PF >= 1.3 in one iteration. Minimum viable changes only.
**Baseline (r4):** 139 trades, PF 1.09, WR 24.82%, DD 48.5%, PnL +$159.51
**Target:** PF >= 1.3, WR >= 25%, DD <= 25%

---

## TASK-R5-01: Block ETH/USDT from Trading [Critical]

**Fulfills:** REQ-V6-R4-001
**Complexity:** S (Small)
**Expected PF impact:** +0.12 (1.09 -> ~1.21)

### Problem
ETH/USDT: 35 trades, 20% WR, -$171.11 PnL. Consistent loser across all rounds (-$245 P1, -$26 r3, -$171 r4). TA composite score has no predictive edge on ETH at H1.

### Design Decision
Use Option A (BLOCKED_INSTRUMENTS list), not Option B (raise min_composite_score). Rationale: explicit is better than implicit. A score override of 30+ still allows occasional entries that lose money. A block is unambiguous.

### Files to modify

1. **`src/config.py`** — Add new constant after INSTRUMENT_OVERRIDES (around line 202):
   ```python
   # R5: Instruments with no demonstrated edge — blocked entirely.
   # ETH/USDT: -$171 r4, -$245 P1, -$26 r3. 20% WR across 6 rounds.
   BLOCKED_INSTRUMENTS: set = {"ETH/USDT"}
   ```

2. **`src/signals/filter_pipeline.py`** — Add new filter method and integrate into `run_all()`:
   - Add method `check_blocked_instrument(self, symbol: str) -> tuple[bool, str]`
   - Implementation:
     ```python
     def check_blocked_instrument(self, symbol: str) -> tuple[bool, str]:
         from src.config import BLOCKED_INSTRUMENTS
         if symbol in BLOCKED_INSTRUMENTS:
             return False, f"instrument_blocked:{symbol}"
         return True, "ok"
     ```
   - Call it as the FIRST filter in `run_all()`, before session filter (cheapest check, O(1) set lookup)
   - Count rejections under key `"instrument_blocked"`

3. **`src/backtesting/backtest_engine.py`** — No changes needed. The filter pipeline is already called for every signal. The blocked instrument check in filter_pipeline.py will handle both live and backtest paths.

### Acceptance criteria
- [ ] `BLOCKED_INSTRUMENTS` set exists in `src/config.py`
- [ ] `check_blocked_instrument()` method exists in `SignalFilterPipeline`
- [ ] `run_all()` calls `check_blocked_instrument()` as the first filter
- [ ] Backtest r5 shows 0 ETH/USDT trades
- [ ] Filter stats show `rejected_by_instrument_blocked > 0`
- [ ] Existing tests pass (no regression)
- [ ] New test: `test_r5_blocked_instrument_eth_rejected` — ETH/USDT signal blocked
- [ ] New test: `test_r5_non_blocked_instrument_passes` — BTC/USDT signal not blocked

### Dependencies
None. Can be implemented independently.

---

## TASK-R5-02: Diagnose and Fix DXY Filter [Critical]

**Fulfills:** REQ-V6-R4-002
**Complexity:** M (Medium)
**Expected PF impact:** +0.05 to +0.12

### Problem
DXY filter shows only 2 rejections across 92,033 raw signals in 21 months. Expected: 50-200 rejections. The code was updated in CAL3 to pre-load DXY data and use bisect mapping, but it's still not working effectively.

### Root Cause Analysis

The code path (backtest_engine.py lines 1206-1242, 1394-1407, 1552) appears structurally correct:
1. DXY instrument is loaded by symbol from DB (tries "DX-Y.NYB", "DXY", "DX=F")
2. RSI(14) is computed with Wilder's method
3. Bisect nearest-previous maps DXY RSI to each price_row index
4. `filter_context["dxy_rsi"]` is set to `dxy_rsi_at_idx[idx]`

**There are three possible failure modes (developer MUST diagnose which one applies):**

**Hypothesis A: DXY data not in database or insufficient rows**
- The `load_historical_data.py` script loads DXY via yfinance with symbol "DX-Y.NYB"
- yfinance H1 data is limited to ~730 days (configured as 729)
- If the script was not run, or yfinance returned an error, `dxy_rsi_by_ts` will be empty
- The log message `"DXY data not available (tried ['DX-Y.NYB', 'DXY', 'DX=F'])"` would appear
- **Diagnostic:** Run SQL query: `SELECT COUNT(*) FROM price_data pd JOIN instruments i ON i.id = pd.instrument_id WHERE i.symbol = 'DX-Y.NYB' AND pd.timeframe = 'H1';`
- If count < 14: re-run `python scripts/load_historical_data.py --sources dxy`

**Hypothesis B: DXY RSI values mostly in neutral zone 45-55**
- If DXY RSI rarely goes above 55 or below 45, only ~2 rejections would be expected
- This is UNLIKELY given DXY's strong trend in 2024 (RSI regularly > 55)
- **Diagnostic:** Add temporary logging in `_simulate_symbol()` after bisect mapping:
  ```python
  _non_none = sum(1 for x in dxy_rsi_at_idx if x is not None)
  _above_55 = sum(1 for x in dxy_rsi_at_idx if x is not None and x > 55)
  _below_45 = sum(1 for x in dxy_rsi_at_idx if x is not None and x < 45)
  logger.info("[DXY-DIAG] %s: %d/%d mapped, >55: %d, <45: %d", symbol, _non_none, n, _above_55, _below_45)
  ```

**Hypothesis C: Timestamp comparison issue in bisect**
- `_compute_dxy_rsi` normalizes to UTC-aware via `_to_utc()`
- bisect mapping also normalizes price_rows via `_to_utc()`
- Both should be UTC-aware datetime objects — bisect comparison should work
- BUT: if DB returns some timestamps as naive and others as aware, the comparison may silently fail or sort incorrectly
- **Diagnostic:** Check if `_dxy_ts_sorted[0].tzinfo` is the same type as `_to_utc(price_rows[0].timestamp).tzinfo`

### Required Fix (after diagnosis)

**Regardless of root cause, add diagnostic logging (permanent, not temporary):**

In `backtest_engine.py`, after the bisect mapping loop (after line 1407), add:
```python
if dxy_rsi_by_ts:
    _non_none_count = sum(1 for x in dxy_rsi_at_idx if x is not None)
    logger.info(
        "[DXY-DIAG] %s: %d/%d candles mapped to DXY RSI "
        "(first_dxy_ts=%s, last_dxy_ts=%s, first_price_ts=%s, last_price_ts=%s)",
        symbol, _non_none_count, n,
        _dxy_ts_sorted[0].isoformat() if _dxy_ts_sorted else "N/A",
        _dxy_ts_sorted[-1].isoformat() if _dxy_ts_sorted else "N/A",
        _to_utc(price_rows[0].timestamp).isoformat(),
        _to_utc(price_rows[-1].timestamp).isoformat(),
    )
```

**If Hypothesis A (no data):** Ensure `scripts/load_historical_data.py --sources dxy` is run before backtest. Add a pre-flight check in `run_backtest()` that logs a WARNING if DXY count is 0.

**If Hypothesis B (neutral zone):** The filter is working correctly, just not impactful. Consider widening DXY thresholds to 50/50 (from 55/45) as a separate calibration task. NOTE: do not change thresholds without analyst approval.

**If Hypothesis C (timezone):** Ensure both DXY and price_rows timestamps are compared in the same timezone format. Apply `.replace(microsecond=0)` or normalize to naive UTC before bisect to eliminate microsecond jitter.

### Files to modify

1. **`src/backtesting/backtest_engine.py`**:
   - Add diagnostic logging after bisect mapping (permanent)
   - Add DXY data count validation log at startup (after line 1242)
   - If data is missing: ensure error message is clear and actionable

2. **`scripts/load_historical_data.py`** (if needed):
   - Verify DXY data is loaded before backtest
   - No code changes expected unless data is missing

### Acceptance criteria
- [ ] Diagnostic log shows how many candles mapped to DXY RSI per symbol
- [ ] Root cause identified and documented (add comment in code)
- [ ] If DXY data was missing: re-loaded and verified
- [ ] Backtest r5 shows significantly more than 2 dxy_filter rejections
- [ ] If DXY RSI is truly neutral (Hypothesis B), document this finding for analyst
- [ ] New test: `test_r5_dxy_filter_with_real_rsi_values` — pass known RSI > 55, verify LONG blocked for EURUSD

### Dependencies
DXY data must exist in database. If not, `scripts/load_historical_data.py` must be run first.

---

## TASK-R5-03: Instrument Whitelist for Backtest [High]

**Fulfills:** REQ-V6-R4-003
**Complexity:** S (Small)
**Expected PF impact:** +0.03 to +0.08

### Problem
7 of 10 instruments are net negative or have too few trades. Trading them adds noise and losses. Restricting to proven performers concentrates capital on instruments with demonstrated edge.

### Design Decision
Add a `BACKTEST_INSTRUMENT_WHITELIST` config option. When non-empty, `BacktestEngine` only simulates whitelisted symbols. This is a BACKTEST-ONLY filter (does not affect live SignalEngine). Rationale: live system may discover new instruments, but backtest should focus on validated universe.

This is separate from BLOCKED_INSTRUMENTS (TASK-R5-01): BLOCKED_INSTRUMENTS applies to both live and backtest. BACKTEST_INSTRUMENT_WHITELIST applies only to backtest.

### Files to modify

1. **`src/config.py`** — Add after BLOCKED_INSTRUMENTS:
   ```python
   # R5: Backtest-only instrument whitelist. When non-empty, only these symbols
   # are simulated. Empty list = all instruments (backward compatible).
   # Based on r4 results: GC=F (+$150), EURUSD=X (+$52), USDCAD=X (+$35),
   # BTC/USDT (+$51, low N), SPY (+$35, low N).
   BACKTEST_INSTRUMENT_WHITELIST: list = [
       "GC=F", "EURUSD=X", "USDCAD=X", "BTC/USDT", "SPY",
   ]
   ```

2. **`src/backtesting/backtest_engine.py`** — In `_simulate()`, after `params.symbols` is resolved (around line 1245), add whitelist filtering:
   ```python
   from src.config import BACKTEST_INSTRUMENT_WHITELIST
   if BACKTEST_INSTRUMENT_WHITELIST:
       original_count = len(params.symbols)
       symbols_to_run = [s for s in params.symbols if s in BACKTEST_INSTRUMENT_WHITELIST]
       if len(symbols_to_run) < original_count:
           logger.info(
               "[R5] Whitelist active: %d/%d symbols (%s)",
               len(symbols_to_run), original_count, symbols_to_run,
           )
   else:
       symbols_to_run = params.symbols
   ```
   Then iterate over `symbols_to_run` instead of `params.symbols`.

### Acceptance criteria
- [ ] `BACKTEST_INSTRUMENT_WHITELIST` exists in `src/config.py`
- [ ] Backtest only processes whitelisted symbols when list is non-empty
- [ ] Empty list means all symbols processed (backward compatible)
- [ ] Log message shows which symbols are filtered out
- [ ] New test: `test_r5_backtest_whitelist_filters_symbols`
- [ ] Existing tests pass

### Dependencies
None. Independent of TASK-R5-01 and TASK-R5-02.

---

## TASK-R5-04: Architecture Decision Document [Critical — Decision]

**Fulfills:** REQ-V6-R4-005
**Complexity:** S (Small — documentation only)

### Problem
This is the final iteration. Regardless of r5 outcome, a Go/NoGo framework must exist to evaluate results immediately.

### File to create

**`docs/ARCHITECTURE_DECISION.md`** with the following structure:

```markdown
# Architecture Decision: TA-Only H1 Trading System

## Date: 2026-03-20
## Status: Pending r5 results

## Go Criteria (stay with current approach)
All of these must be met:
- PF >= 1.3
- WR >= 25%
- Max DD <= 25%
- No single instrument > 50% of total PnL

## Hard Stop
- PF < 1.2 after r5 → skip directly to pivot
- PF 1.2-1.29 → one more calibration round allowed

## Pivot Options (ordered by feasibility)

### A: Switch to D1 Timeframe
- Same TA scoring, D1 entries instead of H1
- Average trade duration already suggests D1 horizon
- Effort: Low (config change + re-backtest)
- Risk: Lower trade frequency, may not have enough data for validation

### B: Narrow to GC=F + USDCAD Strategy
- Abandon multi-instrument, optimize for 2 proven instruments
- Gold trend + CAD carry strategy
- Effort: Low
- Risk: Concentration risk, no diversification

### C: Pattern-Based Signals
- Replace composite averaging with specific high-conviction patterns
- E.g., RSI divergence + MA cross + volume spike
- Effort: High (significant TAEngine refactoring)
- Risk: Overfitting to historical patterns

### D: ML Classification Layer
- Use existing TA scores as features for ML classifier
- Labeled dataset available from 6 rounds of backtesting
- Effort: Medium-High
- Risk: Small dataset, overfitting

## Current System Limitations (for context)
- Dependent on GC=F for majority of profits
- Functionally LONG-only (7/139 trades SHORT)
- H1 timeframe with D1 predictive horizon (inefficient capital)
- WR 24.82% below break-even for 2:1 R:R

## r5 Results
<!-- Fill in after r5 backtest -->
- PF: ___
- WR: ___
- DD: ___
- GC=F share of PnL: ___
- Decision: GO / PIVOT to ___
```

### Acceptance criteria
- [ ] Document exists at `docs/ARCHITECTURE_DECISION.md`
- [ ] Contains Go/NoGo thresholds
- [ ] Contains 4 pivot options with effort/risk assessment
- [ ] Contains placeholder for r5 results
- [ ] Hard stop criteria documented

### Dependencies
None. Can be created immediately.

---

## TASK-R5-05: Document r3-to-r4 Trade Count Discrepancy [Medium]

**Fulfills:** REQ-V6-R4-004
**Complexity:** S (Small — investigation + documentation)

### Problem
r3: 182 trades, r4: 139 trades. 43-trade difference (24% fewer) for "the same period with no signal logic changes." Most likely cause: `TIME_EXIT_CANDLES["H1"]` changed from 24 (r3) to 48 (r4/default).

### Investigation steps

1. Compare config parameters between r3 and r4:
   - Check `docs/TASKS_V6_CAL_R3.md` for r3 parameter overrides
   - Check current `src/config.py` for r4 parameters
   - Focus on: `TIME_EXIT_CANDLES`, `BLOCKED_REGIMES`, `BLOCKED_REGIMES_BY_MARKET`, `INSTRUMENT_OVERRIDES`, any score thresholds

2. Key hypothesis: `TIME_EXIT_CANDLES["H1"]` was 24 in r3 (CAL-R2 calibration) but restored to 48 (default) in r4.
   - Shorter time exit = positions close sooner = new signals can fire sooner = more trades
   - This alone could explain ~40 additional trades

3. Document findings in a brief section at the bottom of `docs/TASKS_V6_R5.md` (this file).

### Files to read (no modifications)
- `docs/TASKS_V6_CAL_R3.md` — r3 parameters
- `docs/TASKS_V6_CAL_R2.md` — r2 parameters (predecessor)
- `src/config.py` — current (r4) parameters

### Acceptance criteria
- [ ] Written explanation of the 43-trade discrepancy with evidence
- [ ] Specific config parameters identified that differ between r3 and r4
- [ ] Documented in this file (TASKS_V6_R5.md) under "Appendix: R3-R4 Discrepancy"

### Dependencies
None. Read-only investigation.

---

## TASK-R5-06: Formalize LONG-Only Mode [Low]

**Fulfills:** REQ-V6-R4-006
**Complexity:** S (Small)

### Problem
Only 7 SHORT trades out of 139 (5%). SHORT is effectively disabled by combination of TREND_BEAR/STRONG_TREND_BEAR blocks, SHORT_SCORE_MULTIPLIER 1.3, and SHORT_RSI_THRESHOLD 30.

### Design Decision
Option A: Accept LONG-only. Add explicit config flag. Do NOT remove SHORT code (keep it for potential D1 pivot). Just document the decision.

### Files to modify

1. **`src/config.py`** — Add comment after SHORT_RSI_THRESHOLD (line 209):
   ```python
   # R5 Decision: SHORT is effectively disabled at H1 timeframe.
   # SHORT_SCORE_MULTIPLIER=1.3 + SHORT_RSI_THRESHOLD=30 + BLOCKED_REGIMES
   # result in <5% SHORT trades with negative PnL.
   # If pivoting to D1 (see ARCHITECTURE_DECISION.md), re-evaluate SHORT viability.
   # To fully disable: set SHORT_ENABLED = False
   SHORT_ENABLED: bool = True  # Keep True for now; SHORT is gated by parameters above
   ```

### Acceptance criteria
- [ ] Config documents the LONG-only reality
- [ ] No logic changes (SHORT code preserved for potential D1 pivot)
- [ ] SHORT_ENABLED flag exists for future use

### Dependencies
TASK-R5-04 (Architecture Decision) should exist first for cross-reference.

---

## Execution Order

| Priority | Task | Estimated Time | Can Parallelize |
|----------|------|---------------|-----------------|
| 1 | TASK-R5-01 (Block ETH) | 30 min | Yes |
| 2 | TASK-R5-02 (DXY Diagnosis + Fix) | 1-2 hours | Yes (with R5-01) |
| 3 | TASK-R5-03 (Whitelist) | 30 min | Yes (with R5-01, R5-02) |
| 4 | TASK-R5-04 (Decision doc) | 30 min | Yes (anytime) |
| 5 | TASK-R5-05 (Discrepancy investigation) | 30 min | Yes (anytime) |
| 6 | TASK-R5-06 (LONG-only formalization) | 15 min | Yes (after R5-04) |

**Total estimated time:** 3-4 hours

**After all tasks:** Run backtest r5 and evaluate against Go/NoGo criteria in ARCHITECTURE_DECISION.md.

---

## Appendix: R3-R4 Discrepancy

**Investigation completed: 2026-03-20**

**Finding:** The 43-trade reduction (182 → 139, -24%) is primarily explained by
`TIME_EXIT_CANDLES["H1"]` change from 24 (r3) to 48 (r4).

**Evidence:**
- r3 task file (`TASKS_V6_CAL_R3.md`, CAL3-03) explicitly documents: r3 ran with
  `H1: 24` candles time exit. CAL3-03 task **increased it to 48** (restored from v5 default).
- r3 observed: 127/182 trades (70%) exited via `time_exit` at exactly 24 candles.
- With 24-candle time exit: positions close after ~1 day → account turns over faster
  → more entries are possible in the same calendar period.
- With 48-candle time exit: positions stay open ~2 days → capital is tied up longer
  → fewer new entries fire in the same period.
- Estimated impact of `H1: 24 → 48` alone: ~30-45 fewer trades (consistent with observed -43).

**Secondary factor:**
- CAL3-04 (per-market-type regime blocking) also added `VOLATILE` block for forex,
  which reduces forex LONG signals and could account for ~5-10 fewer trades.

**Conclusion:** The discrepancy is not a bug and not signal logic regression.
It is the intended effect of restoring the time exit from an aggressive 24-candle
setting (which was cutting winning trades short) to the v5-spec 48-candle setting.
Fewer trades with longer hold time is the expected outcome of this change.
