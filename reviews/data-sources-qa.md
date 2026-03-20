# QA Review: Data Sources & Chart Rendering
## Date: 2026-03-20
## Tester: qa agent

---

## Overall Verdict: FAILED

Three critical issues found. The candlestick chart cannot show historical data from 2024–2025 due to a hard limit of 200 candles in the frontend request, and there is no mechanism to scroll back in time. The database itself contains good data — the bugs are in the API/frontend layer.

---

## 1. Database Coverage

### Summary
The database contains 2 full years of price data for all instruments across all timeframes. Data quality is good; the only gaps in forex H1 are on public holidays (Christmas, New Year) when markets are closed — these are expected market closures, not data collection bugs.

### Coverage Table

| Symbol | Timeframe | Candles | From | To | Span |
|--------|-----------|---------|------|----|------|
| EURUSD=X | H1 | 12,365 | 2024-03-20 | 2026-03-20 | 2 years |
| EURUSD=X | H4 | 3,126 | 2024-03-20 | 2026-03-20 | 2 years |
| EURUSD=X | D1 | 575 | 2024-01-01 | 2026-03-20 | 2+ years |
| BTC/USDT | H1 | 19,426 | 2024-01-01 | 2026-03-20 | 2+ years |
| BTC/USDT | H4 | 4,857 | 2024-01-01 | 2026-03-20 | 2+ years |
| AAPL | H1 | 3,483 | 2024-03-20 | 2026-03-19 | 2 years |
| GBPUSD=X | H1 | 12,367 | 2024-03-20 | 2026-03-20 | 2 years |

**H1 forex data starts at 2024-03-20**, not 2024-01-01. D1 has data from 2024-01-01. The first 79 days of 2024 (Jan–Mar) are absent for H1/H4 forex and stocks. This is a pre-existing collector behavior, not a regression.

### Gap Analysis — EURUSD H1
6 non-weekend gaps detected, all on public holidays:
- 2024-12-25: 13h gap (Christmas)
- 2024-12-31 / 2025-01-01: 7h + 13h gaps (New Year)
- 2025-12-25: 14h gap (Christmas)
- 2026-01-01: two gaps (New Year)

**Conclusion: market holiday gaps are expected and normal. No data collection bugs.**

---

## 2. Bug Analysis

### Bug 1: Chart Only Shows ~10 Days of H1 Data (CRITICAL)

**Component:** `frontend-next/src/app/instruments/page.tsx`, line 193

**Root cause:** The frontend hardcodes `limit=200` with no `date_from` parameter:
```
/api/v2/prices/${symbol}?timeframe=${timeframe}&limit=200
```

The API `GET /api/v2/prices/{symbol}` uses `ORDER BY timestamp DESC, LIMIT N, then reversed to chronological`. With `limit=200` and H1 timeframe, the query returns only the 200 most recent candles — approximately the last 10 calendar days (excluding weekends). The database holds 12,365 candles (2 years) but the chart renders only 200.

**Impact by timeframe with limit=200:**
| Timeframe | What chart shows | What DB has |
|-----------|-----------------|-------------|
| H1 | ~10 days | 2 years |
| H4 | ~45 days | 2 years |
| D1 | ~9 months | 2+ years |
| W1/MN1 | never loaded | not requested |

**Expected:** The chart should show at minimum several months of history to be useful for analysis. A limit of 200 candles for H1 means traders see only 10 days, making trend analysis and the SMA-200 filter meaningless visually.

**Steps to reproduce:**
1. Open the Instruments page
2. Select any forex pair, set timeframe to H1
3. Chart shows candles from approximately 2026-03-10 to 2026-03-20 only
4. No historical context for 2024 or 2025 is visible

---

### Bug 2: `date_from` Parameter Does Not Expand History (MAJOR)

**Component:** `src/database/crud.py`, function `get_price_data`, lines 107–125

**Root cause:** The SQL query applies `ORDER BY timestamp DESC` then `LIMIT N`, then reverses. When `date_from` is provided alongside a `limit`, the limit is applied *after* the WHERE filter but the DESC ordering means it still returns the N most recent candles within the range — not the N oldest starting from `date_from`.

Example:
```
GET /api/v2/prices/EURUSD=X?timeframe=H1&limit=200&date_from=2024-01-01
```
Returns: 200 candles from 2026-03-10 to 2026-03-20 (same result as without date_from).

The `date_from` parameter only has a practical effect when `date_to` is also set and cuts off the most recent candles. When used alone with `limit`, it does not show historical data from that start date.

**Expected:** `date_from=2024-01-01&limit=200` should return the first 200 candles starting from 2024-01-01 (ascending). Currently it returns the last 200 candles before the end of data.

**Fix options:**
- Option A: Change query to `ORDER BY timestamp ASC` when `date_from` is provided, then re-reverse
- Option B: Frontend sends both `date_from` and `date_to` to define a window
- Option C: Add separate `offset` parameter for pagination

---

### Bug 3: Maximum API Limit Insufficient for Full History (MAJOR)

**Component:** `src/api/routes_v2.py`, line 813

**Root cause:** The API enforces `le=2000` (maximum 2000 candles). For H1 data covering 2 years:
- EURUSD H1: 12,365 candles total
- BTC/USDT H1: 19,426 candles total
- Maximum retrievable: 2,000 candles = ~4 months of H1 data

Even if the frontend were to request `limit=2000` (the maximum), it would still only receive data from approximately November 2025 onward — missing all of 2024 and most of 2025.

**Impact:** Users can never see more than ~4 months of H1 data on the chart through the standard API endpoint, regardless of what the database contains.

---

### Bug 4: Gap Detection Logic Cannot Compensate for Limit Constraint (MINOR)

**Component:** `src/api/routes_v2.py`, function `_collect_if_stale`, lines 741–804

The background gap detection logic compares `row_count` against `expected_rows` based on the time span of returned rows. Since `limit=200` consistently returns only ~10 days of H1 data, the gap check sees:
- `row_count = 200`
- `span_hours = 10 * 24 = 240h`
- `expected_rows = (240 / 1.0) * 0.60 = 144`
- `200 > 144` → **no gap detected** → no historical backfill triggered

The gap detection is therefore useless for discovering that the database has 2 years of data not being served to the frontend. It only detects *internal* gaps within the returned window.

---

## 3. What IS Working Correctly

- **Database integrity:** All instruments have continuous data from their respective start dates with no unexpected gaps.
- **API endpoint functionality:** `GET /api/v2/prices/{symbol}` responds correctly, returns proper JSON with OHLCV fields.
- **Timestamp format:** API returns ISO 8601 with `+00:00` timezone offset (e.g., `"2026-03-20T00:00:00+00:00"`). JavaScript `new Date(ts).getTime() / 1000` correctly converts this to a Unix timestamp. **No timezone bug in chart rendering.**
- **Chart rendering logic:** `CandlestickChart.tsx` correctly maps OHLCV data to lightweight-charts format. Precision detection (forex vs stocks/crypto) works correctly.
- **Data collectors:** YFinanceCollector and CcxtCollector correctly save data with UTC timestamps. No duplicate or corrupted records found.
- **Holiday gaps:** Only expected market-closure gaps (Christmas, New Year) present in forex data.
- **Server startup:** Backend starts successfully and all endpoints respond.
- **Auto-collect on request:** The background `_collect_if_stale` task correctly detects stale data and triggers refresh for recent candles.

---

## 4. Root Cause Summary

The issue reported by the user ("gaps in charts, no 2024/2025 history") is caused entirely by the **frontend requesting only 200 candles with no date range**, not by missing data in the database. The database is healthy and contains 2 full years of data. The chart can only show what it requests — 200 candles = 10 days of H1 data.

**The fix requires changes to either:**
1. The frontend: request more candles (e.g., `limit=2000`) or add `date_from` + `date_to` support with pagination
2. The CRUD layer: fix `get_price_data` so `date_from` works as "show data starting from this date" rather than "filter but still take the most recent N"
3. The API limit: raise `le=2000` to allow larger requests (e.g., `le=5000`) for wide chart views

---

## 5. Recommendations (Priority Order)

1. **High:** Frontend should request `limit=1000` for H1/H4, or add a `date_from` that is calculated as `now - N_months` depending on timeframe.
2. **High:** Fix `get_price_data` query: when `date_from` is set and `date_to` is not set, use `ORDER BY timestamp ASC LIMIT N` to return the N candles starting from `date_from`.
3. **Medium:** Raise API max limit from 2000 to at least 5000 for the prices endpoint.
4. **Low:** Add chart time-range selector in the UI (1M / 3M / 6M / 1Y / All) that translates to `date_from`/`date_to` parameters.
