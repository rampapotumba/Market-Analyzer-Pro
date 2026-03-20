# Data Audit Report: Historical Data Coverage and Quality

## Date: 2026-03-20
## Analyst: market-analyst agent

---

## Executive Summary

The system operates on 16 instruments across 3 markets (7 forex, 3 crypto, 6 stocks/commodities) with price data spanning Jan 2024 -- Mar 2026 across 5 timeframes (H1, H4, D1, W1, MN1). Total price candle count is 230,273. However, several critical data gaps severely undermine signal quality and backtest reliability:

1. **Forex volume data is entirely absent** (100% zero-volume across all 7 forex pairs on H1) -- making the SIM-29 volume confirmation filter completely non-functional for forex.
2. **Fear & Greed Index has zero historical records** -- the SIM-39 filter has no data to operate on, even though the collector code exists.
3. **DXY has only 3 days of data** (105 records, Mar 17-20 2026) -- the SIM-38 DXY filter cannot work in backtests and has negligible live coverage.
4. **COT data has only 1 snapshot per symbol** (Mar 10 2026) -- the SIM-41 COT filter lacks historical depth for trend detection (needs week-over-week change).
5. **Economic calendar has only 39 events covering 2 days** (Mar 19-21 2026) -- the SIM-33 calendar filter is completely non-functional in backtests spanning 2024-2025.
6. **Backtest produces only 33 trades over 24 months** (~1.4/month) -- statistically insufficient for reliable performance conclusions.

---

## 1. Data Coverage Matrix

### 1.1 Price Data (price_data table: 230,273 records)

| Symbol | Market | H1 Candles | H1 From | H1 To | H4 | D1 | W1 | MN1 |
|--------|--------|-----------|---------|-------|-----|-----|-----|------|
| EURUSD=X | forex | 12,365 | 2024-03-20 | 2026-03-20 | 3,126 | 575 | 200 | 120 |
| GBPUSD=X | forex | 12,367 | 2024-03-20 | 2026-03-20 | 3,126 | 575 | 200 | 120 |
| USDJPY=X | forex | 12,282 | 2024-03-20 | 2026-03-20 | 3,119 | 575 | 200 | 120 |
| AUDUSD=X | forex | 12,429 | 2024-03-20 | 2026-03-20 | 3,135 | 575 | 200 | 120 |
| USDCHF=X | forex | 12,321 | 2024-03-20 | 2026-03-20 | 3,127 | 575 | 200 | 120 |
| USDCAD=X | forex | 11,575 | 2024-03-21 | 2026-03-20 | 2,991 | 575 | 200 | 120 |
| NZDUSD=X | forex | 11,568 | 2024-03-21 | 2026-03-20 | 2,991 | 575 | 200 | 120 |
| BTC/USDT | crypto | 19,426 | 2024-01-01 | 2026-03-20 | 4,857 | 810 | 200 | 104 |
| ETH/USDT | crypto | 19,426 | 2024-01-01 | 2026-03-20 | 4,857 | 810 | 200 | 104 |
| SOL/USDT | crypto | 19,426 | 2024-01-01 | 2026-03-20 | 4,857 | 810 | 200 | 68 |
| AAPL | stocks | 3,483 | 2024-03-20 | 2026-03-19 | 996 | 555 | 200 | 120 |
| MSFT | stocks | 3,483 | 2024-03-20 | 2026-03-19 | 996 | 555 | 200 | 120 |
| GOOGL | stocks | 3,483 | 2024-03-20 | 2026-03-19 | 996 | 555 | 200 | 120 |
| SPY | stocks | 3,483 | 2024-03-20 | 2026-03-19 | 996 | 555 | 200 | 120 |
| QQQ | stocks | 3,483 | 2024-03-20 | 2026-03-19 | 996 | 555 | 200 | 120 |
| GC=F | stocks | 10,737 | 2024-03-21 | 2026-03-20 | 2,963 | 558 | 200 | 103 |

**D1 candle coverage for MA200 (SIM-27):**
- All instruments have 555-810 D1 candles -- **sufficient** (>200 requirement met).
- W1 data: exactly 200 candles per instrument -- borderline for W1 MA50 filter for D1 signals.

### 1.2 H1 Data Gaps (intervals > 4 hours)

| Symbol | Market | Gap Count | Max Gap |
|--------|--------|-----------|---------|
| AAPL | stocks | 500 | 3d 22h (weekends + overnight) |
| GOOGL | stocks | 500 | 3d 22h |
| MSFT | stocks | 500 | 3d 22h |
| SPY | stocks | 500 | 3d 22h |
| QQQ | stocks | 500 | 3d 22h |
| GC=F | stocks | 115 | **48d 15h** (CRITICAL) |
| NZDUSD=X | forex | 98 | **49d 9h** (CRITICAL) |
| USDCAD=X | forex | 98 | **49d 8h** (CRITICAL) |
| AUDUSD=X | forex | 104 | 2d 3h (weekend normal) |
| EURUSD=X | forex | 110 | 2d 3h (weekend normal) |
| GBPUSD=X | forex | 109 | 2d 3h (weekend normal) |
| USDCHF=X | forex | 109 | 2d 4h (weekend normal) |
| USDJPY=X | forex | 108 | 2d 22h |
| BTC/USDT | crypto | 0 | -- (24/7, no gaps) |
| ETH/USDT | crypto | 0 | -- |
| SOL/USDT | crypto | 0 | -- |

**Critical:** GC=F, NZDUSD=X, USDCAD=X have ~49-day gaps in H1 data. This suggests a period of failed collection or missing historical data fetch. These gaps will cause incorrect ATR calculations, missed signals, and unreliable backtest results for these instruments.

**Normal:** Stock gaps of 3d 22h correspond to weekends + overnight (market closed ~16h/day). Forex gaps of 2d are weekends. Crypto has no gaps (24/7 market).

### 1.3 Volume Data Quality (H1)

| Symbol | Market | Zero-Volume % | Status |
|--------|--------|--------------|--------|
| BTC/USDT | crypto | 0.0% | OK |
| ETH/USDT | crypto | 0.0% | OK |
| SOL/USDT | crypto | 0.0% | OK |
| AAPL | stocks | 0.3% | OK |
| MSFT | stocks | 0.3% | OK |
| GOOGL | stocks | 0.1% | OK |
| SPY | stocks | 0.1% | OK |
| QQQ | stocks | 0.6% | OK |
| GC=F | stocks | 3.6% | OK |
| **EURUSD=X** | **forex** | **100.0%** | **NO VOLUME** |
| **GBPUSD=X** | **forex** | **100.0%** | **NO VOLUME** |
| **USDJPY=X** | **forex** | **100.0%** | **NO VOLUME** |
| **AUDUSD=X** | **forex** | **100.0%** | **NO VOLUME** |
| **USDCHF=X** | **forex** | **100.0%** | **NO VOLUME** |
| **USDCAD=X** | **forex** | **100.0%** | **NO VOLUME** |
| **NZDUSD=X** | **forex** | **100.0%** | **NO VOLUME** |

**Critical:** All forex pairs have 100% zero volume. This is expected -- yfinance does not provide real forex volume data (OTC market). The SIM-29 volume confirmation filter gracefully degrades (returns True when all volume == 0), but this means **the filter provides zero value for 7 out of 16 instruments**. Since backtests primarily run on forex (EURUSD, AUDUSD are the most active), this filter effectively does nothing for the majority of backtest trades.

---

## 2. Non-Price Data Sources

### 2.1 Economic Calendar (economic_events: 39 records)

| Metric | Value |
|--------|-------|
| Total events | 39 |
| Date range | 2026-03-19 to 2026-03-21 (2 days only!) |
| High impact | 15 |
| Medium impact | 3 |
| Low impact | 21 |

**Critical:** The economic calendar contains only 2 days of forward-looking events. There is **zero historical economic event data** for the backtest period (2024-01-01 to 2025-12-31). The SIM-33 economic calendar filter is completely non-functional in backtests -- it gracefully degrades and passes all signals through.

**Impact:** The backtest cannot account for high-impact events like NFP, FOMC, ECB decisions. In live trading these events cause massive volatility that destroys SL placements. The backtest is overly optimistic because it trades through these events without penalty.

### 2.2 DXY Index (macro_data indicator "DXY": 105 records)

| Metric | Value |
|--------|-------|
| Records | 105 |
| Date range | 2026-03-17 to 2026-03-20 (3 days) |
| Value range | 99.06 -- 99.83 |
| Source | realtime_collector (yfinance DX-Y.NYB) |

**Critical:** DXY data covers only the last 3 days. The SIM-38 DXY filter requires RSI(14) calculation, which needs at least 14+ data points with historical depth. There is **no DXY data for the backtest period** (2024-2025). The filter gracefully degrades in backtests but provides no value.

**Note:** DXY is NOT registered as an instrument in the `instruments` table and has no price_data records. It is stored only in macro_data with indicator_name="DXY".

### 2.3 Fear & Greed Index (macro_data: 0 records)

| Metric | Value |
|--------|-------|
| Records | **0** |
| Collector exists | Yes (fear_greed_collector.py) |
| API endpoint | https://api.alternative.me/fng/?limit=1 |

**Critical:** Despite having a fully implemented collector, there are **zero Fear & Greed records** in the database. The collector has never successfully stored data, or the scheduler job was never enabled. The SIM-39 filter is completely non-functional.

### 2.4 Funding Rates (macro_data: 327 records total)

| Indicator | Records | Date Range |
|-----------|---------|------------|
| FUNDING_RATE_BTC | 109 | 2026-03-17 to 2026-03-20 |
| FUNDING_RATE_ETH | 109 | 2026-03-17 to 2026-03-20 |
| FUNDING_RATE_SOL | 109 | 2026-03-17 to 2026-03-20 |

**Issue:** Funding rates cover only 3 days. No historical funding rate data for backtest period. The SIM-40 funding rate filter will gracefully degrade in backtests. Sample values show rates in range -0.00006 to +0.000008 -- well below the +/-0.001 (0.1%) threshold, so even in live mode, this filter would rarely trigger.

### 2.5 COT Data (macro_data: 5 records)

| Indicator | Value | Date |
|-----------|-------|------|
| COT_NET_EURUSD=X | +105,144 | 2026-03-10 |
| COT_NET_GBPUSD=X | -84,197 | 2026-03-10 |
| COT_NET_USDJPY=X | -41,387 | 2026-03-10 |
| COT_NET_BTC/USDT | +1,302 | 2026-03-10 |
| COT_NET_SPY | -134,505 | 2026-03-10 |

**Issue:** Only 1 snapshot per symbol (single date). The SIM-41 COT filter requires `change_week` (week-over-week delta) to determine if positions are growing. With only 1 data point, the delta cannot be calculated. Missing COT for: AUDUSD, USDCHF, USDCAD, NZDUSD, ETH, SOL, all stocks except SPY.

### 2.6 Macro Indicators (FRED data)

| Indicator | Records | Date Range |
|-----------|---------|------------|
| FEDFUNDS | 12 | 2025-03 to 2026-02 |
| CPIAUCSL | 11 | 2025-03 to 2026-02 |
| UNRATE | 11 | 2025-03 to 2026-02 |
| PAYEMS | 12 | 2025-03 to 2026-02 |
| GDPC1 | 12 | 2023-01 to 2025-10 |
| INDPRO | 12 | 2025-03 to 2026-02 |
| RETAILSMNSA | 12 | 2025-01 to 2025-12 |
| HOUST | 12 | 2025-02 to 2026-01 |

Macro indicators have ~12 months of data each. These feed into the FA component of composite_score. Coverage is adequate for live operation but provides no support for backtest period starting Jan 2024.

### 2.7 Market-Regime Data (regime_state: 415 records)

| Symbol | Records | Date Range |
|--------|---------|------------|
| Most instruments | 31 | 2026-03-18 to 2026-03-20 |
| GC=F, NZDUSD=X, USDCAD=X | 4 | 2026-03-20 only |

**Critical:** Regime data covers only 2 days. All backtest trades show regime = "UNKNOWN" because historical regime states were never computed. This means:
- SIM-26 (RANGING block) cannot be verified historically
- SIM-18 (dynamic R:R by regime) uses DEFAULT values in backtests
- SIM-19 (regime-adaptive SL) uses DEFAULT multipliers in backtests

### 2.8 Other Data Sources

| Source | Table | Records | Status |
|--------|-------|---------|--------|
| News events | news_events | 588 | 2 days only (2026-03-18 to 2026-03-20) |
| Social sentiment | social_sentiment | 0 | Empty |
| On-chain data | onchain_data | 0 | Empty |
| Order flow | order_flow_data | 0 | Empty (funding rates stored in macro_data instead) |
| Central bank rates | central_bank_rates | 0 | Empty |
| Company fundamentals | company_fundamentals | 0 | Empty |
| Accuracy stats | accuracy_stats | 0 | Empty |
| Swap rates | config/swap_rates.json | 7 pairs | Updated 2026-03-19, crypto not included |

### 2.9 Swap Rates Configuration

```json
{
    "updated_at": "2026-03-19",
    "rates": {
        "EURUSD=X": {"long": -0.5, "short": 0.3},
        "USDJPY=X": {"long": 1.2, "short": -1.5},
        "GBPUSD=X": {"long": -0.8, "short": 0.5},
        "AUDUSD=X": {"long": 0.2, "short": -0.4},
        "USDCAD=X": {"long": 0.4, "short": -0.7},
        "USDCHF=X": {"long": -0.3, "short": 0.1},
        "NZDUSD=X": {"long": 0.1, "short": -0.3}
    }
}
```

Missing: BTC/USDT, ETH/USDT, SOL/USDT, AAPL, MSFT, GOOGL, SPY, QQQ, GC=F. Crypto perpetual funding rates are handled separately, but stock/commodity swap (carry) costs are not accounted for.

---

## 3. Statistical Significance Assessment

### 3.1 Backtest Trade Count

| Run | Period | Trades | Trades/Month | Assessment |
|-----|--------|--------|-------------|------------|
| Baseline (v4) | 24 mo | 33 | 1.4 | CRITICALLY LOW |
| Phase 1 (P1) | 24 mo | 52 | 2.2 | VERY LOW |
| Phase 2 (P2) | 24 mo | 33 | 1.4 | CRITICALLY LOW |
| Phase 3 (P3) | 24 mo | 33 | 1.4 | CRITICALLY LOW |

**Assessment:** 33 trades over 24 months is **statistically insufficient** for reliable performance conclusions. Industry standards require:

- **Minimum 30 trades per month** for day-trading strategies (spec target was 55-65)
- **Minimum 100+ trades total** for any strategy validation
- **Win rate confidence interval at 33 trades with 45% WR:** 95% CI = [28%, 63%] -- the range is so wide that we cannot distinguish this from a coin flip

The current 33-trade sample gives us:
- Standard error of win rate: sqrt(0.45 * 0.55 / 33) = 8.7%
- 95% confidence interval: 45.45% +/- 17.0% = [28.4%, 62.5%]
- We cannot reject the null hypothesis that true WR = 50% (random)

**Root cause of low trade count:** The system generates very few signals that pass all filters. But more importantly, the **backtest engine appears to only produce trades for 4 out of 16 instruments** (EURUSD, AUDUSD, ETH/USDT, SPY). This suggests 12 instruments generate zero backtest trades.

### 3.2 Backtest vs. Baseline Anomaly

The Baseline (v4, all filters OFF) and Phase 2 (all P1+P2 filters ON) produce **identical results**: 33 trades, 45.45% WR, PF 2.01, +$253.73. This is a **red flag** -- it means the Phase 2 filters (volume, momentum, weekday, strength) are having zero effect. Given that forex has no volume data, the volume filter passes everything. The other filters may also not be triggering.

### 3.3 Is 24 Months Enough?

For a strategy generating 1.4 trades/month: **No.** 24 months produces only 33 trades. To achieve statistical significance (minimum 100 trades), the system would need either:
- **72+ months** of history at current signal rate, OR
- A **significant increase in signal frequency** (lower thresholds, more instruments, lower timeframes)

For comparison, the Phase 1 run (with some filters relaxed differently) produced 52 trades -- still insufficient but closer to meaningful. The original v4 unfiltered baseline referenced in the spec had ~110 trades/month for 239 trades -- but this appears to be from a different configuration or time period than what the current backtest engine produces.

---

## 4. Findings

### Finding 1: Forex Volume Data is Entirely Absent
- **Severity:** Major
- **Description:** All 7 forex pairs have 100% zero volume in H1 candles. yfinance does not provide real forex volume for OTC pairs.
- **Evidence:** Volume quality query shows 12,282-12,429 zero-volume candles per forex pair.
- **Impact:** SIM-29 volume confirmation filter provides zero filtering value for forex. Since forex is the primary trading market (7 of 16 instruments, majority of backtest trades), this filter is effectively disabled for most of the portfolio.

### Finding 2: Economic Calendar is Empty for Backtest Period
- **Severity:** Critical
- **Description:** Only 39 events exist, covering 2026-03-19 to 2026-03-21. No historical events for 2024-2025.
- **Evidence:** Query shows total 39 events with min/max dates in March 2026.
- **Impact:** SIM-33 calendar filter cannot function in backtests. The system backtests through NFP, FOMC, ECB decisions without penalty, producing overly optimistic results. In live trading, these events would trigger the filter and block signals.

### Finding 3: DXY, Fear & Greed, COT Have No Historical Depth
- **Severity:** Critical
- **Description:** DXY has 3 days of data, F&G has zero records, COT has 1 snapshot. None of these can support backtest validation.
- **Evidence:** Macro data queries show DXY from 2026-03-17, COT from 2026-03-10, F&G absent.
- **Impact:** Filters SIM-38, SIM-39, SIM-41 are untestable in backtests. Live operation has only days of data -- no ability to compute RSI(14) on DXY or weekly COT changes.

### Finding 4: Critical H1 Data Gaps in 3 Instruments
- **Severity:** Major
- **Description:** GC=F, NZDUSD=X, USDCAD=X have gaps of ~49 days in H1 data.
- **Evidence:** Gap analysis shows max_gap of 48d 15h for GC=F, 49d 9h for NZDUSD=X, 49d 8h for USDCAD=X.
- **Impact:** ATR(14) calculations will be wrong after gaps, causing incorrect SL/TP levels. Signals during gap periods are lost. Backtest for these instruments is unreliable.

### Finding 5: Backtest Trade Count is Statistically Insufficient
- **Severity:** Critical
- **Description:** 33 trades over 24 months. Only 4 of 16 instruments produce trades.
- **Evidence:** All backtest results show 33 trades. Win rate CI is [28%, 63%].
- **Impact:** Cannot draw reliable conclusions about strategy performance. PF of 2.01 could easily be random variation. The system's SPEC_SIMULATOR_V5 targets (PF >= 1.4, WR 46-52%) cannot be statistically validated with this sample.

### Finding 6: Baseline and Phase 2 Produce Identical Results
- **Severity:** Critical
- **Description:** v4 baseline (all filters OFF) and Phase 2 (volume+momentum+weekday+strength filters ON) produce exactly the same 33 trades with identical PnL.
- **Evidence:** BACKTEST_RESULTS_V1.md and BACKTEST_RESULTS_V5_P2.md show identical metrics.
- **Impact:** This proves that Phase 2 filters have zero effect. Either (a) no trades are being filtered, (b) the filter integration in backtest_engine is broken, or (c) the data conditions never trigger the filters (likely for volume due to zero forex data).

### Finding 7: Regime State Not Available in Backtests
- **Severity:** Major
- **Description:** All backtest trades show regime = "UNKNOWN". Regime data covers only 2 days.
- **Evidence:** All backtest results show "by_regime: UNKNOWN = 33 trades."
- **Impact:** SIM-26 (RANGING block), SIM-18 (dynamic R:R), SIM-19 (regime-adaptive SL) all use DEFAULT parameters in backtests. The backtest does not validate the regime-based strategy at all.

### Finding 8: Order Flow Data Table is Empty
- **Severity:** Minor
- **Description:** order_flow_data table has 0 records. Funding rates are stored in macro_data instead.
- **Evidence:** SELECT COUNT(*) FROM order_flow_data = 0.
- **Impact:** If any code references order_flow_data for funding rates (as mentioned in SIM-40 spec: "Data from order_flow_data (already collected)"), it will find nothing. The actual data is in macro_data with indicator_name LIKE 'FUNDING_RATE_%'.

---

## 5. Recommendations

### Priority 1: Immediate Actions (Required for Reliable Operation)

**REC-01: Load Historical Economic Calendar Data**
- Source: Investing.com historical calendar, FMP calendar API, or manual CSV import
- Required: All HIGH/MEDIUM impact events for Jan 2024 -- Mar 2026
- Expected volume: ~2,000-3,000 events over 24 months
- Without this, SIM-33 is completely non-functional in backtests

**REC-02: Investigate and Fix the 49-Day Data Gaps**
- Instruments affected: GC=F, NZDUSD=X, USDCAD=X
- Action: Run historical data fetch to backfill missing periods
- Use: `scripts/fetch_historical.py` (already exists in project)

**REC-03: Investigate Why Only 4 Instruments Produce Backtest Trades**
- 12 of 16 instruments generate zero trades in backtests
- GBPUSD=X and BTC/USDT (both with specific overrides) produce 0 trades
- This is the primary cause of the statistically insufficient 33-trade sample
- Action: Debug backtest_engine signal generation for all instruments

### Priority 2: Data Enrichment (Required for Filter Validation)

**REC-04: Compute Historical Regime States**
- Run regime_detector against historical price data for all instruments
- Store results in regime_state or compute on-the-fly in backtest
- Without this, all regime-based logic uses DEFAULT parameters

**REC-05: Load Historical DXY Data**
- Source: yfinance (DX-Y.NYB) with `period="2y"`
- Store in price_data as a proper instrument, not just macro_data
- Required for RSI(14) calculation needed by SIM-38

**REC-06: Enable Fear & Greed Collector in Scheduler**
- The collector code exists but never runs (or fails silently)
- For backtests: load historical F&G data from alternative.me API (supports `limit=730`)
- API: `https://api.alternative.me/fng/?limit=730` returns ~2 years of history

**REC-07: Accumulate COT Historical Data**
- Current: 1 snapshot per symbol
- Required: At least 52 weekly snapshots for meaningful COT analysis
- The CFTC ZIP files contain full-year historical data -- parse and store all weeks

### Priority 3: Structural Improvements

**REC-08: Accept That Forex Volume Filter is Non-Functional**
- Document this explicitly in the system
- Consider alternative volume proxies for forex: tick count from Finnhub, or simply disable the filter for forex permanently
- Currently the filter "gracefully degrades" but this masks the fact that it provides zero value

**REC-09: Add Missing Instruments to Swap Rates**
- config/swap_rates.json is missing all crypto, all stocks, and GC=F
- While crypto uses funding rates instead of swaps, stock CFDs do have carry costs
- At minimum: document that swap calculation is forex-only

**REC-10: Increase Backtest Sample Size**
- Option A: Lower composite score threshold to allow more trades (risks accepting bad signals)
- Option B: Add more instruments or timeframes to backtest
- Option C: Extend historical data period beyond 2 years
- Option D: Fix the 12 instruments that produce zero trades (REC-03)
- Target: Minimum 100 trades, ideally 200+ for statistical significance

---

## 6. Data Completeness Summary

| Data Source | Live Coverage | Backtest Coverage | Filter Affected | Status |
|-------------|---------------|-------------------|-----------------|--------|
| H1 Price (crypto) | 26 months, no gaps | Full | All TA | OK |
| H1 Price (forex) | 24 months, weekend gaps | Full (with 49d gaps for 2 pairs) | All TA | PARTIAL |
| H1 Price (stocks) | 24 months, overnight gaps | Full | All TA | OK |
| D1 Price | 555-810 candles | Full (>200 for MA200) | SIM-27 | OK |
| Volume (crypto) | Real data | Real data | SIM-29 | OK |
| Volume (forex) | **Zero** | **Zero** | SIM-29 | FAILED |
| Volume (stocks) | Real data | Real data | SIM-29 | OK |
| Economic Calendar | 2 days | **None** | SIM-33 | FAILED |
| DXY | 3 days | **None** | SIM-38 | FAILED |
| Fear & Greed | **None** | **None** | SIM-39 | FAILED |
| Funding Rates | 3 days | **None** | SIM-40 | FAILED |
| COT | 1 snapshot | **None** | SIM-41 | FAILED |
| Regime State | 2 days | **None** | SIM-26, SIM-18, SIM-19 | FAILED |
| Swap Rates | 7 forex pairs | Static file | SIM-37 | PARTIAL |
| News/Sentiment | 2 days | **None** | Composite score | FAILED |
| On-chain | None | None | N/A (not in v5 scope) | N/A |

**Summary: 7 of 13 data sources are FAILED for backtest purposes. Only price candles and D1 MA200 data are adequate.**
