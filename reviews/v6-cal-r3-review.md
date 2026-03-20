# Code Review: Calibration Round 3 — DXY RSI fix + Monday block + per-market VOLATILE
## Date: 2026-03-20
## Reviewer: code-reviewer agent

## Result: CHANGES REQUIRED

---

## Critical issues (must fix before QA)

### 1. `backtest_engine.py:1504` — DXY timestamp lookup is a probable no-op (timezone mismatch)

The `dxy_rsi_by_ts` dict is keyed by raw `r.timestamp` objects from DXY price rows.
The lookup key is `candle_ts = current_candle.timestamp` from the traded symbol's price rows.

Both come from `PriceData` (`DateTime(timezone=True)` column, asyncpg returns timezone-aware datetimes).
However, SQLAlchemy + asyncpg may return timestamps **with** `tzinfo=UTC` from one query and
**without** `tzinfo` from another, depending on the connection configuration and driver version.
Python dict lookup uses `__eq__` on datetime: a naive datetime and an aware datetime are **never equal**,
even if they represent the same instant.

```python
# backtest_engine.py:1504 — key is candle_ts from traded symbol rows
"dxy_rsi": (dxy_rsi_by_ts or {}).get(candle_ts),
```

The dict keys come from `dxy_rows` (line 983), the lookup key from `price_rows` (line 1422).
If one set of rows is tz-aware and the other is tz-naive (or both aware but different zone), every
`.get(candle_ts)` returns `None`, making SIM-38 a permanent no-op with zero log output to diagnose it.

**Impact:** The entire fix is silently bypassed — DXY RSI filter never fires in backtest.

**Fix:** Normalize all timestamps to UTC-aware before inserting into the dict, and strip/normalize
`candle_ts` the same way before lookup:

```python
# In _compute_dxy_rsi:
def _to_utc(ts: datetime.datetime) -> datetime.datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=datetime.timezone.utc)
    return ts.astimezone(datetime.timezone.utc)

result[_to_utc(timestamps[rsi_period])] = _rsi_from_avg(avg_gain, avg_loss)
# ... and in the loop:
result[_to_utc(timestamps[j + 1])] = _rsi_from_avg(avg_gain, avg_loss)
```

```python
# In _simulate_symbol at line 1504:
_candle_ts_utc = candle_ts.replace(tzinfo=datetime.timezone.utc) if candle_ts.tzinfo is None \
    else candle_ts.astimezone(datetime.timezone.utc)
"dxy_rsi": (dxy_rsi_by_ts or {}).get(_candle_ts_utc),
```

**Test coverage gap:** `test_cal3_01_simulate_symbol_accepts_dxy_rsi_by_ts` only checks that the
parameter exists in the signature. There is no integration test verifying that a known DXY RSI value
is actually looked up and passed to the filter when timestamps match. This should be added.

---

### 2. `backtest_engine.py:970-1003` — Wilder RSI seed uses `changes[:rsi_period]` (14 changes) but first RSI is stored at `timestamps[rsi_period]` — off-by-one ambiguity with lookahead risk

The Wilder seed averages `changes[0..13]` (14 price changes = 15 closes).
The first RSI value is stored at `timestamps[14]` (index 14 = the 15th row).
The Wilder loop then processes `changes[14..N-1]` and stores at `timestamps[j+1]` = `timestamps[15..N]`.

This is correct for causal computation — **no lookahead** — the RSI at timestamp T uses only closes
up to and including T. However, the `candle_ts` for the traded symbol at iteration `i` maps
to `price_rows[i].timestamp`, while signals use `idx = i - 1` for indicator values.
The DXY RSI lookup uses `candle_ts` (index `i`), not `idx = i - 1`.

This means the filter is using the RSI value computed from DXY data **including the current candle**,
which is **one candle ahead** of the TA indicators used for signal generation.
For H1 timeframes this is a 1-hour lookahead. Minor in practice, but inconsistent with the
no-lookahead guarantee documented throughout the engine.

**Fix:** Look up DXY RSI at `price_rows[i-1].timestamp` (the same `idx` used for TA indicators),
or document this as an accepted approximation.

---

## Minor issues (should fix)

### 3. `tests/test_simulator_v6.py:2784-2816` — `_make_price_row` helper in `TestCal301DxyRsiFilter` is dead code

The method `_make_price_row` at line 2784 is defined but never called. The test class uses only
`_make_price_rows` (plural). The dead method also contains a redundant loop at lines 2799-2801
that builds `rows` but never returns it (the function ends by building `result` via a second loop
and returning that). This is confusing code that could be misread as a logic bug.

### 4. `tests/test_simulator_v6.py:1167` — Test comment references wrong threshold value

```python
# Candle low below SL AND we've held 24 candles (max for H1 after CAL-03) — SL should win
```

After CAL3-03, H1 max is **48 candles**, not 24. The comment says "CAL-03" but references the
pre-CAL3-03 value. The test itself passes `candles_since_entry=24` and tests SL priority (correct),
but the comment misleads the reader about time exit threshold.

### 5. `src/backtesting/backtest_engine.py:57-61` — `WEEKDAY_FILTER` dict is stale (no longer used)

```python
WEEKDAY_FILTER = {
    "monday_block_until_utc": 10,
    "friday_block_from_utc": 18,
    "crypto_exempt_monday": True,
}
```

CAL3-02 moved weekday logic into `SignalFilterPipeline.check_weekday()`. This module-level dict
is no longer referenced anywhere in `backtest_engine.py`. It documents the old partial-Monday-block
behaviour (block until 10:00 UTC), while the new behaviour is full-day block. Stale constants that
contradict current behaviour are a maintenance hazard.

### 6. `src/config.py:232` — Comment says "Monday блокируется полностью в check_weekday" but `WEAK_WEEKDAYS = [1]` is forex-only by convention, not enforcement

`WEAK_WEEKDAYS` is consumed in `filter_pipeline.py:195` with a `market_type == "forex"` guard.
This means for `market_type="stocks"`, Tuesday also gets no multiplier, which is correct.
But the comment ("WEAK_WEEKDAYS используется только для score multiplier, не для полной блокировки")
does not document the forex-only scoping. A developer adding a new market type will not know that
Tuesday score penalties are forex-specific unless they trace the code.

---

## Suggestions (optional)

- **DXY: log a sample of matched/unmatched timestamps** on first run. Given that the lookup is done
  millions of times per backtest, even a single log line showing "DXY RSI lookup: first match at TS X"
  would confirm the fix works end-to-end without running a full backtest.

- **`viability_assessment` concentration check at line 657-661:** The check `if by_symbol and total_pnl > 0`
  means the concentration gate is skipped entirely when `total_pnl <= 0`. A strategy could have
  80% of its loss concentrated in one instrument and still be marked `concentration_viable=True`.
  Consider checking concentration on absolute PnL magnitude regardless of sign.

- **`_make_price_rows` in tests** (line 2796) generates all rows with the same timestamp base
  (`datetime(2024, 1, 1, 0, 0, ...)`) and adds `timedelta(hours=i)`, which creates H1-spaced rows.
  This is correct for DXY H1 testing. Consider adding a comment to clarify intent.

---

## Summary

The calibration round 3 implementation is structurally sound: Monday full-block for forex is
correctly scoped to non-crypto, per-market VOLATILE blocking works as designed, H1 TIME_EXIT=48
is cleanly restored, and the viability assessment is a useful addition to `_compute_summary`.

However, the DXY RSI fix — the most critical change in this round — has a high probability of
being a silent no-op in production due to Python datetime equality semantics when mixing
timezone-aware and timezone-naive timestamps as dict keys. This must be fixed and validated with
an integration test before QA, otherwise the SIM-38 filter will continue to degrade silently
exactly as it did before the fix.
