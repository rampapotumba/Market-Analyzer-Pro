# Spec: Backtest Engine Performance Optimization

## Date: 2026-03-20
## Related task file: `docs/TASKS_V6_FINAL.md` (OPT-01..OPT-05)

---

## Problem Statement

Backtest engine runtime degraded from ~50 minutes (pre-DXY) to ~5 hours (post-DXY/D1/trailing)
for 10 instruments x 21 months H1. Three hotspots identified in the per-candle loop of
`_simulate_symbol()` in `src/backtesting/backtest_engine.py`.

---

## Hotspot Analysis

### 1. D1 list comprehension (OPT-01)

**Location:** `backtest_engine.py:1489`
```python
d1_rows_for_filter = [r for r in _d1_all if r.timestamp <= candle_ts][-200:]
```

**Profile:**
- Called: ~12,000 times per symbol (every candle)
- `_d1_all` size: ~500 D1 rows (300 warmup + 200 active)
- Per call: 500 timestamp comparisons + list construction + [-200:] slice
- Per symbol: 12,000 * 500 = 6,000,000 comparisons
- Total (10 symbols): 60,000,000 comparisons

**Fix:** Replace with `bisect.bisect_right()` on pre-sorted timestamp array.
Pre-sorted: D1 data loaded with `ORDER BY timestamp` from DB (guaranteed sorted).

### 2. DXY RSI per-candle lookup (OPT-02)

**Location:** `backtest_engine.py:1508`
```python
"dxy_rsi": (dxy_rsi_by_ts or {}).get(_to_utc(price_rows[idx].timestamp)),
```

**Profile:**
- `_to_utc()` creates a new datetime object each call (`ts.replace(tzinfo=...)`)
- dict.get() uses exact timestamp match -- DXY and symbol timestamps may not align
  to the same second, causing silent None returns (graceful degradation = filter disabled)
- Called: ~12,000 * 10 = 120,000 times

**Fix:** Pre-map DXY RSI to price_rows indices using bisect for nearest-previous match.
Result: `dxy_rsi_at_idx[idx]` -- O(1) array index, no datetime allocation, nearest-match semantics.

### 3. S/R via full TAEngine (OPT-03)

**Location:** `backtest_engine.py:1854-1871` (`_generate_signal_fast()`)
```python
_ta_sr = _TAE(df_slice, timeframe=timeframe)
ta_inds = _ta_sr.calculate_all_indicators()
```

**Profile:**
- Creates new TAEngine instance for each signal candidate
- `calculate_all_indicators()` recomputes RSI, MACD, BB, MA, ADX, Stochastic, Volume, S/R
  -- all of which are ALREADY pre-computed in ta_arrays
- Only S/R levels are needed (weight = 5% of TA score)
- Called: ~200-500 times per symbol (whenever raw signal is generated)
- Total: ~3,000-5,000 full TAEngine computations

**Fix:** Cache S/R levels with refresh interval of 50 candles.
S/R levels are inherently slow-moving (support/resistance from recent highs/lows).
50 H1 candles = ~2 trading days -- reasonable refresh rate.

---

## Architecture: Before vs After

### Before (current):
```
for each candle i:
    # O(m) -- scan all D1 rows
    d1_rows = [r for r in d1_all if r.timestamp <= candle_ts][-200:]

    # O(1) dict lookup, but creates datetime + exact match (may miss)
    dxy_rsi = dxy_dict.get(_to_utc(price_rows[idx].timestamp))

    if signal_generated:
        # O(full_TA) -- redundant recomputation
        ta_engine = TAEngine(df_slice)
        ta_engine.calculate_all_indicators()  # RSI, MACD, BB, ...
        support = ta_engine.support_levels
```

### After (optimized):
```
# Pre-loop phase:
d1_timestamps = [r.timestamp for r in d1_all]           # sorted
dxy_rsi_at_idx = pre_map_dxy_to_price_rows(dxy_dict)   # bisect
sr_cache = {"support": [], "resistance": [], "age": -100}

for each candle i:
    # O(log m) -- binary search
    d1_idx = bisect.bisect_right(d1_timestamps, candle_ts)
    d1_rows = d1_all[max(0, d1_idx-200):d1_idx]

    # O(1) -- array index
    dxy_rsi = dxy_rsi_at_idx[idx]

    if signal_generated:
        # O(1) or O(full_TA) every 50 candles
        if i - sr_cache["age"] >= 50:
            sr_cache = recompute_sr(df_slice)
        support = sr_cache["support"]
```

---

## Data Flow: DXY RSI Pre-mapping

```
1. _compute_dxy_rsi(dxy_rows) returns dict[datetime_utc, float]
   Keys: UTC-aware timestamps from DXY H1 candles
   Values: RSI(14) at each candle

2. Pre-mapping phase (once per symbol):
   For each price_row[i]:
     a. Normalize timestamp to UTC
     b. bisect_right(sorted_dxy_timestamps, price_ts) - 1
     c. dxy_rsi_at_idx[i] = dxy_rsi_sorted[bisect_idx] or None

3. In main loop:
   filter_context["dxy_rsi"] = dxy_rsi_at_idx[idx]
```

This also **fixes the timestamp mismatch bug**: current exact-match can return None
when DXY and symbol timestamps differ by seconds/minutes. bisect_right gives the
nearest PREVIOUS DXY RSI value -- correct for causal (no-lookahead) filtering.

---

## Risk Assessment

| Optimization | Risk | Mitigation |
|-------------|------|-----------|
| OPT-01 (bisect D1) | None -- mathematically equivalent | Unit test: compare bisect result vs list comprehension |
| OPT-02 (DXY pre-map) | Nearest-match changes semantics from exact to nearest-previous | This is actually MORE correct (fixes silent None bug) |
| OPT-03 (S/R cache) | Stale S/R levels (up to 50 candles old) | S/R weight = 5% of TA score; levels rarely change within 2 days |
| OPT-04 (parallel) | GIL limits CPU parallelism | Fallback to sequential if no improvement measured |
| OPT-05 (pre-load) | Memory spike (all symbol data in RAM simultaneously) | Already loading full price_rows per symbol; marginal increase |

---

## Acceptance Criteria

1. All existing tests pass unchanged (no behavioral regression)
2. Backtest produces IDENTICAL trades (same data_hash) with and without optimizations
3. Total runtime for 10 instruments x 21 months H1: < 1 hour (target: 30-60 minutes)
4. DXY filter shows `rejected_by_dxy_filter > 0` (confirms fix of silent None bug)

---

## Implementation Order

1. OPT-01 (D1 bisect) -- simplest, biggest impact for D1 filter
2. OPT-02 (DXY pre-map) -- biggest single hotspot elimination + bug fix
3. OPT-03 (S/R cache) -- moderate complexity, significant TAEngine savings
4. OPT-05 (pre-load) -- infrastructure for parallelism
5. OPT-04 (parallel) -- only if OPT-01..03 insufficient

After each step: run backtest, verify data_hash matches, measure elapsed time.
