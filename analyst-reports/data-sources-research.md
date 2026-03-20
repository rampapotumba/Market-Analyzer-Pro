# Data Sources Research: Closing 6 Critical Data Gaps

## Date: 2026-03-20
## Analyst: market-analyst agent

---

## Executive Summary

The data audit identified 6 external data sources needed before backtest calibration can proceed reliably. This report evaluates available APIs — both paid (1-2 services covering all needs) and free (combination approach). The recommendation is **Option B (free APIs)** as it covers all 6 needs at zero cost, with sufficient historical depth for 2024-2025 backtesting.

---

## 1. Summary Table: Data Need x Recommended Source

| # | Data Need | Filter | Free Source | Paid Alternative | Historical Depth | Priority |
|---|-----------|--------|-------------|------------------|-----------------|----------|
| 1 | Economic Calendar (FOMC, NFP, CPI) | SIM-33 | **ForexFactory XML** (already integrated) + **Investing.com scraper** or **FMP free tier** | Twelve Data / Trading Economics | 2+ years via FMP | P1 Critical |
| 2 | Fear & Greed Index | SIM-39 | **Alternative.me API** (already coded, never run) | N/A (alt.me is the canonical source) | 730 days (limit=730) | P2 Easy |
| 3 | DXY H1 candles | SIM-38 | **yfinance** (DX-Y.NYB, already used for live) | Twelve Data / Alpha Vantage | 2+ years | P2 Easy |
| 4 | COT (Commitment of Traders) | SIM-41 | **CFTC ZIP files** (already coded, only fetches latest) | Quandl (Nasdaq Data Link) | Full history (2000+) | P2 Medium |
| 5 | Funding Rates (crypto) | SIM-40 | **Binance REST API** (historical endpoint exists) | CoinGlass API | 2+ years | P3 Easy |
| 6 | Forex Volume proxy | SIM-29 | **FXCM tick volume** (free CSV) or **Alpha Vantage** forex intraday | Polygon.io | Varies (6-12 months) | P3 Low value |

---

## 2. Option A: Paid APIs (1-2 Services Covering All Needs)

### Candidate 1: Twelve Data (twelvedata.com)

| Aspect | Details |
|--------|---------|
| **URL** | https://twelvedata.com |
| **Coverage** | Forex OHLCV (real volume from ECN), DXY, economic calendar, technical indicators (RSI, MACD built-in) |
| **Does NOT cover** | Fear & Greed, COT, crypto funding rates |
| **Pricing** | Free: 800 API calls/day, 8 calls/min. Basic: $29/mo (60k calls/day). Pro: $149/mo |
| **Historical depth** | 25+ years for forex/stocks, 5+ years intraday (H1) |
| **Format** | JSON, CSV |
| **Python SDK** | `twelvedata` (official, pip install twelvedata) |
| **Rate limits** | Free tier: 8/min, 800/day. Paid: much higher |
| **Gaps filled** | DXY (1), Forex volume (6), partial calendar (1) |
| **Example** | `GET /time_series?symbol=DXY&interval=1h&outputsize=5000&apikey=KEY` |

**Verdict:** Covers DXY and forex volume well. Does NOT cover F&G, COT, funding rates. Would still need free APIs for the rest. At $29/mo it's reasonable but not sufficient alone.

### Candidate 2: Polygon.io

| Aspect | Details |
|--------|---------|
| **URL** | https://polygon.io |
| **Coverage** | US stocks (excellent), forex (aggregate bars), crypto, economic calendar (via reference data) |
| **Does NOT cover** | Fear & Greed, COT, crypto funding rates |
| **Pricing** | Free: 5 API calls/min. Starter: $29/mo. Developer: $99/mo (full history) |
| **Historical depth** | 15+ years stocks, 2+ years forex intraday |
| **Format** | JSON |
| **Python SDK** | `polygon-api-client` (official) |
| **Rate limits** | Free: 5/min. Paid: unlimited |
| **Gaps filled** | DXY (via forex), forex volume (via aggregate bars), partial calendar |
| **API key status** | **Already configured** in project `.env` as `POLYGON_API_KEY` |

**Verdict:** Already have API key. Good for stocks and forex aggregate bars with volume. Same limitation -- no F&G, COT, funding rates.

### Candidate 3: EODHD (eodhistoricaldata.com)

| Aspect | Details |
|--------|---------|
| **URL** | https://eodhistoricaldata.com |
| **Coverage** | Stocks, forex, crypto, economic calendar (historical!), macro data |
| **Does NOT cover** | Fear & Greed, COT, crypto funding rates |
| **Pricing** | Free: 20 API calls/day. All World: $29.99/mo. Full: $79.99/mo |
| **Historical depth** | 30+ years for stocks, intraday limited to 120 days on basic plans |
| **Format** | JSON, CSV |
| **Python SDK** | `eodhd` (third-party) |
| **Gaps filled** | Economic calendar with history (1!), DXY (3), partial forex volume |

**Verdict:** Strong economic calendar API with historical data. But intraday depth limited on cheaper plans.

### Candidate 4: Trading Economics (tradingeconomics.com)

| Aspect | Details |
|--------|---------|
| **URL** | https://tradingeconomics.com/api |
| **Coverage** | Economic calendar (best historical coverage), macro indicators, forex, crypto |
| **Does NOT cover** | Fear & Greed, COT, crypto funding rates |
| **Pricing** | Free: Web only. Basic: $49/mo. Professional: $149/mo. Business: $995/mo |
| **Historical depth** | 20+ years of economic events |
| **Format** | JSON, CSV |
| **Python SDK** | `tradingeconomics` (official) |
| **Gaps filled** | Economic calendar (1) -- best source for historical events |

**Verdict:** Best historical economic calendar. But very expensive for what we need, and still doesn't cover 3 of 6 gaps.

### Option A Conclusion

**No single paid API covers all 6 needs.** The best paid combination would be:
- **Twelve Data ($29/mo)** for DXY + forex volume + economic calendar
- Still need free APIs for F&G (alternative.me), COT (CFTC), funding rates (Binance)

This makes Option A essentially "Option B + $29/mo for better forex volume and DXY." Given that yfinance already provides DXY (DX-Y.NYB) and volume is low-value for forex anyway, the paid option provides marginal benefit.

---

## 3. Option B: Free API Combination (Recommended)

### Source 1: ForexFactory XML + Financial Modeling Prep (FMP) -- Economic Calendar

**Current state:** ForexFactory XML feed is already integrated (`fmp_calendar_collector.py`). It provides current week events only. No historical data.

**Solution for historical data:** Financial Modeling Prep (FMP) free tier.

| Aspect | Details |
|--------|---------|
| **URL** | https://site.financialmodelingprep.com |
| **API endpoint** | `GET /api/v3/economic_calendar?from=2024-01-01&to=2024-12-31&apikey=KEY` |
| **Free tier** | 250 requests/day (sufficient for one-time historical load) |
| **Historical depth** | 5+ years of economic events with impact level |
| **Data fields** | date, country, event, impact (low/medium/high), actual, previous, estimate, currency |
| **Registration** | Free, email only |
| **Format** | JSON |

**Implementation plan:**
1. Register for free FMP API key
2. Write one-time script to fetch 2024-01-01 to 2025-12-31 in monthly chunks (24 requests)
3. Map events to our `economic_events` table schema (already exists)
4. ForexFactory continues for live/weekly forward-looking events

**Alternative free source:** Investing.com economic calendar scraping (via `investpy` library or direct scraping). Less reliable but no API key needed.

**Example API call:**
```
GET https://financialmodelingprep.com/api/v3/economic_calendar?from=2024-01-01&to=2024-03-31&apikey=YOUR_KEY
```

**Response format:**
```json
[
  {
    "event": "Nonfarm Payrolls",
    "date": "2024-01-05 13:30:00",
    "country": "US",
    "actual": 216,
    "previous": 173,
    "estimate": 175,
    "impact": "High",
    "currency": "USD"
  }
]
```

---

### Source 2: Alternative.me Fear & Greed API -- Already Implemented

| Aspect | Details |
|--------|---------|
| **URL** | https://api.alternative.me/fng/ |
| **Current state** | Collector exists (`fear_greed_collector.py`), never stored data to DB |
| **Historical endpoint** | `GET /fng/?limit=730&format=json` -- returns up to 730 days |
| **Rate limits** | None documented (public API, no key needed) |
| **Data fields** | value (0-100), value_classification (Extreme Fear/Fear/Neutral/Greed/Extreme Greed), timestamp |
| **Format** | JSON |

**Implementation plan:**
1. Write one-time script: `GET /fng/?limit=730` -- fetches ~2 years of daily F&G values
2. Store in `macro_data` table with indicator_name="FEAR_GREED", country="GLOBAL"
3. Enable the existing collector in scheduler for ongoing daily updates

**Example API call:**
```
GET https://api.alternative.me/fng/?limit=730&format=json
```

**Response:**
```json
{
  "data": [
    {"value": "25", "value_classification": "Extreme Fear", "timestamp": "1710806400"},
    {"value": "72", "value_classification": "Greed", "timestamp": "1710720000"}
  ]
}
```

**Effort:** Minimal -- collector code already exists, just need historical fetch script + scheduler activation.

---

### Source 3: yfinance -- DXY Historical H1 Candles

| Aspect | Details |
|--------|---------|
| **URL** | N/A (Python library, Yahoo Finance data) |
| **Current state** | DXY is collected in `realtime_collector.py` as macro_data, but only 3 days of data |
| **Symbol** | `DX-Y.NYB` (ICE US Dollar Index futures) |
| **Historical depth** | yfinance supports `period="2y"` for H1 data (730 trading days) |
| **Rate limits** | Unofficial API, ~2000 requests/hour is safe |
| **Data fields** | Open, High, Low, Close, Volume |

**Implementation plan:**
1. Register DXY as instrument in `instruments` table (market_type="index" or "forex")
2. Run `scripts/fetch_historical.py` for DX-Y.NYB with period="2y", intervals=[1h, 4h, 1d]
3. Store in `price_data` table alongside other instruments
4. Backtest engine can then compute RSI(14) on DXY H1 candles for SIM-38 filter

**Or simpler alternative:**
1. One-time script using yfinance to fetch DXY and store in `macro_data` or `price_data`
2. Continue existing realtime_collector for live updates

**Example Python code:**
```python
import yfinance as yf
dxy = yf.download("DX-Y.NYB", period="2y", interval="1h")
# Returns DataFrame with ~12,000+ H1 candles covering 2024-2026
```

**Effort:** Minimal -- yfinance is already a project dependency, just need historical backfill.

---

### Source 4: CFTC ZIP Files -- COT Historical Data

| Aspect | Details |
|--------|---------|
| **URL** | https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalCompressed/index.htm |
| **Current state** | `cot_collector.py` exists, fetches current year ZIP, stores only latest snapshot per symbol |
| **Historical files** | `deacot{YEAR}.zip` -- one ZIP per year, each contains full year of weekly reports |
| **Data fields** | Market name, date, non-commercial long/short, commercial long/short, total OI |
| **Coverage** | EUR FX, GBP, JPY (CME futures) -- maps to our EURUSD, GBPUSD, USDJPY |
| **Format** | CSV inside ZIP |
| **Rate limits** | Static files, no limits |

**Implementation plan:**
1. Modify `cot_collector.py` to parse ALL rows from ZIP (not just latest per market)
2. Fetch `deacot2024.zip` and `deacot2025.zip`
3. Store each weekly report as separate `macro_data` record with proper release_date
4. This gives ~52 snapshots per year per instrument -- sufficient for week-over-week delta calculation

**Key change needed in collector:**
```python
# Current: stores only LATEST record per market (one per symbol)
# Required: store ALL records per market (52 per year per symbol)
# Change: remove "if our_symbol not in latest:" guard, collect all rows
```

**Markets available in CFTC data:**
- EURO FX (CME) -> EURUSD=X
- BRITISH POUND (CME) -> GBPUSD=X
- JAPANESE YEN (CME) -> USDJPY=X
- BITCOIN (CME) -> BTC/USDT
- E-MINI S&P 500 (CME) -> SPY
- AUSTRALIAN DOLLAR (CME) -> AUDUSD=X (can add mapping)
- CANADIAN DOLLAR (CME) -> USDCAD=X (can add mapping)
- NEW ZEALAND DOLLAR (CME) -> NZDUSD=X (can add mapping)
- SWISS FRANC (CME) -> USDCHF=X (can add mapping)

**Effort:** Medium -- collector exists but needs modification to store full history instead of latest only. Also need to add AUD/CAD/NZD/CHF market mappings.

---

### Source 5: Binance REST API -- Historical Funding Rates

| Aspect | Details |
|--------|---------|
| **URL** | https://fapi.binance.com |
| **Endpoint** | `GET /fapi/v1/fundingRate?symbol=BTCUSDT&limit=1000&startTime=...&endTime=...` |
| **Current state** | `order_flow_collector.py` fetches current funding rate only |
| **Historical depth** | Full history since contract launch (2019 for BTC, 2020 for ETH) |
| **Frequency** | Every 8 hours (3x per day) |
| **Rate limits** | 2400 req/min (weight-based), this endpoint is weight=1 |
| **Data fields** | symbol, fundingTime, fundingRate |
| **No API key needed** | Public endpoint |

**Implementation plan:**
1. Write one-time script to fetch historical funding rates
2. Paginate with startTime/endTime in 1000-record chunks
3. For 2024-2025: ~365 * 3 = 1095 records per symbol, fits in 2 API calls per symbol
4. Store in `macro_data` table with indicator_name="FUNDING_RATE_BTC", etc.
5. Ongoing collection already works via order_flow_collector (but stores in wrong table -- see Finding 8 in audit)

**Example API call:**
```
GET https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&startTime=1704067200000&endTime=1735689600000&limit=1000
```

**Response:**
```json
[
  {"symbol": "BTCUSDT", "fundingRate": "0.00010000", "fundingTime": 1704096000000},
  {"symbol": "BTCUSDT", "fundingRate": "0.00008500", "fundingTime": 1704124800000}
]
```

**Effort:** Easy -- Binance API is already used in project, just need historical fetch script.

---

### Source 6: Forex Volume Proxy

This is the most problematic gap. OTC forex has no centralized volume. Options:

#### Option 6a: Accept graceful degradation (RECOMMENDED)

The SIM-29 volume filter already gracefully degrades when volume=0. For forex instruments, the filter passes through. This is documented behavior and acceptable because:
- Volume confirmation is most valuable for stocks and crypto (where we have real volume)
- Forex volume proxies (tick count, futures volume) introduce their own inaccuracies
- The effort-to-value ratio is very low

**Recommendation:** Document that SIM-29 is forex-exempt. No additional data source needed.

#### Option 6b: Alpha Vantage FX Intraday (already have API key)

| Aspect | Details |
|--------|---------|
| **URL** | https://www.alphavantage.co |
| **Endpoint** | `GET /query?function=FX_INTRADAY&from_symbol=EUR&to_symbol=USD&interval=60min&outputsize=full&apikey=KEY` |
| **Current state** | API key already configured in project |
| **Volume data** | Alpha Vantage provides "volume" for forex intraday -- but this is aggregate tick count, not real volume |
| **Historical depth** | Full output: last 3-6 months of H1 data |
| **Rate limits** | Free: 25 requests/day (very restrictive). Premium: $49.99/mo |
| **Issue** | 25 req/day limit means fetching 7 forex pairs * multiple months = weeks of waiting |

**Verdict:** Impractical at free tier rate limits. Not recommended.

#### Option 6c: Polygon.io Forex Aggregates (already have API key)

| Aspect | Details |
|--------|---------|
| **URL** | https://polygon.io |
| **Endpoint** | `GET /v2/aggs/ticker/C:EURUSD/range/1/hour/2024-01-01/2024-03-31?apiKey=KEY` |
| **Volume data** | Trade count (number of ticks in period) -- not dollar volume |
| **Free tier** | 5 API calls/min, delayed data. Historical requires paid plan. |

**Verdict:** Free tier likely insufficient. Paid tier ($29/mo) provides historical forex bars with tick count volume.

---

## 4. Recommendation

### Recommended approach: Option B (Free APIs)

| # | Data Gap | Source | API Key Needed | Estimated Records | Effort |
|---|----------|--------|---------------|-------------------|--------|
| 1 | Economic Calendar | **FMP free tier** (one-time) + ForexFactory (live) | Yes (free registration) | ~2,500 events | Medium |
| 2 | Fear & Greed | **Alternative.me** (existing collector) | No | ~730 daily values | Easy |
| 3 | DXY candles | **yfinance** (existing dependency) | No | ~12,000 H1 candles | Easy |
| 4 | COT data | **CFTC ZIPs** (existing collector, modify) | No | ~520 weekly reports | Medium |
| 5 | Funding rates | **Binance REST** (existing integration) | No | ~2,200 records | Easy |
| 6 | Forex volume | **Skip** (graceful degradation) | N/A | N/A | None |

**Total cost: $0/month**
**New API keys needed: 1 (FMP, free registration)**
**New Python dependencies: 0 (all libraries already installed)**

### Why not Option A (paid)?

1. **No single paid API covers all 6 needs** -- F&G, COT, and funding rates always require separate free sources
2. **yfinance already provides DXY data** -- paying $29/mo for Twelve Data adds minimal value
3. **Forex volume proxy has low analytical value** -- the main benefit of paid APIs (real forex volume) doesn't justify cost
4. **All free sources provide sufficient historical depth** (2+ years) for our backtest period
5. **We already have Polygon.io API key** -- if we later need forex volume, we can try it at no cost

### Rationale for skipping forex volume (Gap #6)

The volume confirmation filter (SIM-29) was designed to confirm breakout strength. For forex:
- OTC market has no centralized volume -- any proxy is approximate
- Tick count != real volume (a 100-lot institutional order = 1 tick)
- CME forex futures volume is a proxy for spot, but correlation is imperfect
- The filter already gracefully degrades (returns True for zero-volume instruments)
- **Impact assessment:** Even if we had perfect forex volume data, analysis of stock/crypto backtest trades (where volume IS available) shows the volume filter had zero effect (Phase 2 = Baseline). This suggests the filter threshold (1.2x MA20) may need recalibration regardless of data availability.

---

## 5. Implementation Priority Order

### Phase 1: Quick Wins (1-2 hours each)

**Step 1: Fear & Greed Historical Load**
```python
# One API call, ~730 records, existing collector code
GET https://api.alternative.me/fng/?limit=730
# Parse response, store each day in macro_data
```
- Difficulty: Trivial
- Impact: Enables SIM-39 in backtests
- Script: `scripts/fetch_historical_fear_greed.py`

**Step 2: DXY Historical Load**
```python
import yfinance as yf
dxy = yf.download("DX-Y.NYB", period="2y", interval="1h")
# Store in price_data table, register as instrument
```
- Difficulty: Easy
- Impact: Enables SIM-38 in backtests (DXY RSI filter)
- Script: `scripts/fetch_historical_dxy.py`

**Step 3: Binance Historical Funding Rates**
```python
import httpx
# 2 API calls per symbol (BTC, ETH, SOL) = 6 total calls
url = "https://fapi.binance.com/fapi/v1/fundingRate"
params = {"symbol": "BTCUSDT", "startTime": 1704067200000, "limit": 1000}
```
- Difficulty: Easy
- Impact: Enables SIM-40 in backtests
- Script: `scripts/fetch_historical_funding_rates.py`

### Phase 2: Medium Effort (2-4 hours each)

**Step 4: COT Historical Data**
- Modify `cot_collector.py` to parse full year of weekly reports (not just latest)
- Add missing market mappings: AUD, CAD, NZD, CHF
- Fetch `deacot2024.zip` and `deacot2025.zip`
- Expected: ~520 records (5-9 markets * 52 weeks * 2 years)
- Script: `scripts/fetch_historical_cot.py`

**Step 5: Economic Calendar Historical**
- Register for FMP free API key
- Fetch monthly chunks: 24 requests for Jan 2024 - Dec 2025
- Map to existing `economic_events` table schema
- Filter for HIGH and MEDIUM impact events only
- Expected: ~2,500 events
- Script: `scripts/fetch_historical_calendar.py`

### Phase 3: Optional Enhancement

**Step 6: Forex Volume (IF needed after recalibration)**
- Try Polygon.io free tier first (API key already configured)
- If insufficient, accept graceful degradation permanently

---

## 6. Data Format Mapping

### How each source maps to existing DB schema:

| Source | Target Table | Key Fields |
|--------|-------------|------------|
| FMP Calendar | `economic_events` | event_date, country, currency, event_name, impact, actual, previous, estimate |
| Alternative.me F&G | `macro_data` | indicator_name="FEAR_GREED", country="GLOBAL", value, release_date |
| yfinance DXY | `price_data` | instrument_id (new), timestamp, open, high, low, close, volume, timeframe |
| CFTC COT ZIPs | `macro_data` | indicator_name="COT_NET_{symbol}", country="US", value, release_date |
| Binance Funding | `macro_data` | indicator_name="FUNDING_RATE_{symbol}", country="GLOBAL", value, release_date |

**Important:** No schema changes needed. All 5 data sources fit into existing tables.

---

## 7. Risk Assessment

| Risk | Probability | Mitigation |
|------|------------|------------|
| FMP free tier discontinued | Low | Fallback: EODHD free tier, or manual CSV import from Investing.com |
| Alternative.me API changes | Low | Simple API, stable for years. Fallback: manual CSV |
| CFTC ZIP format changes | Very low | Format unchanged since ~2010 |
| Binance API geo-blocking | Medium | Use ccxt as abstraction layer (already a dependency) |
| yfinance rate limiting | Low | One-time bulk fetch, then incremental updates |
| Data quality issues (gaps, nulls) | Medium | Validate after load, log warnings, graceful degradation in filters |

---

## 8. Estimated Timeline

| Step | Effort | Dependency | Cumulative |
|------|--------|-----------|------------|
| Fear & Greed load | 1 hour | None | 1 hour |
| DXY historical load | 1 hour | None | 2 hours |
| Funding rates load | 1 hour | None | 3 hours |
| COT historical modification | 3 hours | None | 6 hours |
| Economic calendar (FMP) | 3 hours | FMP registration | 9 hours |
| Backtest rerun with new data | 1 hour | All above | 10 hours |

**Total estimated effort: ~10 hours of development time.**

After data loading, the backtest should be rerun to validate that:
1. SIM-33 (calendar filter) now blocks signals near high-impact events
2. SIM-38 (DXY filter) now filters forex signals based on USD strength
3. SIM-39 (F&G filter) adjusts crypto composite scores
4. SIM-40 (funding rate filter) penalizes extreme funding conditions
5. SIM-41 (COT filter) provides week-over-week positioning context
6. Trade count changes (expected: some reduction from new active filters)
