# Data Sources Guide: FA, Sentiment, and Geo Engines
## Date: 2026-03-20
## Analyst: market-analyst agent

---

## Executive Summary

The system has three non-TA engines (FA, Sentiment, Geo) contributing 10-90% of composite score weight depending on timeframe. Currently, all three produce near-zero scores due to missing or unreliable data feeds. TA alone drives all signals, which means 55% of the scoring model (on swing/positional timeframes) is dead weight. This guide identifies 23 data sources, evaluates each against 10 criteria, and recommends a phased integration plan.

**Key weight distribution (from `mtf_filter.py`):**

| Timeframe | TA | FA | Sentiment | Geo | Non-TA Total |
|-----------|----|----|-----------|-----|--------------|
| M1/M5/M15 | 90% | 0% | 5% | 5% | 10% |
| H1 | 70% | 10% | 15% | 5% | 30% |
| H4/D1 | 45% | 25% | 20% | 10% | 55% |
| W1 | 20% | 40% | 25% | 15% | 80% |
| MN1 | 10% | 50% | 20% | 20% | 90% |

**Current state of each engine:**

| Engine | Score Range | Current Output | Root Cause |
|--------|-----------|----------------|------------|
| FAEngine (forex/stocks) | [-100, +100] | ~0.0 | FRED data present but only 1 observation per series (no delta possible); no previous_value to compute change |
| CryptoFAEngine | [-100, +100] | ~5-15 | On-chain data partially working (CoinMetrics), but NVT=None, DXY/VIX often missing from macro_data |
| SentimentEngineV2 | [-100, +100] | ~0.0 | News exists (RSS) but no social_data or fear_greed passed to engine constructor in signal_engine.py |
| GeoEngineV2 | [-100, +100] | ~0.0 | GDELT API returns empty articles for most country codes; rate-limited; no fallback |

---

## 1. Summary Table: All Recommended Data Sources

| # | Source | Engine | API Type | Auth | Cost | Rate Limit | Format | Update Freq | Historical Depth | Coverage | Reliability | Integration Effort |
|---|--------|--------|----------|------|------|------------|--------|-------------|-----------------|----------|-------------|-------------------|
| 1 | **FRED API** | FA | REST | API key (free) | Free | 120 req/min | JSON | Daily | 70+ years | US macro | Very High | Easy (already built) |
| 2 | **ECB Data API** | FA | REST | None | Free | Unlimited | JSON/SDMX | Monthly | 20+ years | EU rates | High | Easy (already built) |
| 3 | **Central bank APIs** | FA | REST/CSV | None | Free | Varies | Mixed | Monthly | 10-20 years | G8 rates | Medium | Easy (already built) |
| 4 | **CFTC COT Reports** | FA | File DL | None | Free | N/A | CSV/ZIP | Weekly (Fri) | 20+ years | Forex, indices, BTC futures | High | Easy (already built) |
| 5 | **CoinMetrics Community** | FA | REST | None | Free | 100 req/10min | JSON | Daily | 5+ years | Top 50 crypto | High | Easy (already built) |
| 6 | **CoinGecko Free** | FA/Sent | REST | None | Free | 10-30 req/min | JSON | Real-time | 365 days | All crypto | Medium | Easy |
| 7 | **Glassnode Free** | FA | REST | API key (free) | Free tier | 10 req/min | JSON | Daily | 2 years (free) | BTC, ETH | High | Medium |
| 8 | **Alternative.me F&G** | Sentiment | REST | None | Free | Unlimited | JSON | Daily | 2+ years (daily) | Crypto (BTC) | Medium | Easy (already built) |
| 9 | **Finnhub** | FA/Sent | REST | API key (free) | Free (60 req/min) | 60 req/min | JSON | Real-time | 10+ years (earnings) | US stocks | High | Easy (already built) |
| 10 | **Yahoo Finance (yfinance)** | FA | Python lib | None | Free | Soft limit ~2000/hr | JSON | Real-time | Up to 30 years (D1) | Stocks, forex, indices | Medium | Easy (already built) |
| 11 | **News RSS Feeds** | Sentiment | HTTP/RSS | None | Free | N/A | XML | Real-time | None (live only) | All markets | Medium | Easy (already built) |
| 12 | **GDELT v2** | Geo | REST | None | Free | ~60 req/min | JSON | 15-min | 6 months (free) | Global geopolitics | Low-Medium | Medium (already built, flaky) |
| 13 | **NewsAPI.org** | Sentiment | REST | API key | Free (100 req/day) | 100 req/day free | JSON | Real-time | 30 days (free) | Global news | High | Easy |
| 14 | **Reddit (PRAW)** | Sentiment | REST | OAuth (free) | Free | 60 req/min | JSON | Real-time | ~1 year (search) | All markets | Medium | Easy (already built) |
| 15 | **ACLED** | Geo | REST | API key (free) | Free (academic) | 500 req/day | JSON/CSV | Weekly | 10+ years | Armed conflict | Very High | Medium |
| 16 | **Global Peace Index** | Geo | File DL | None | Free | N/A | CSV/XLSX | Annual | 15+ years | 163 countries | Very High | Easy |
| 17 | **Kaggle Datasets** | All | File DL | Free account | Free | N/A | CSV | Static/annual | Varies | All markets | Varies | Easy (backfill only) |
| 18 | **Quandl/Nasdaq Data Link** | FA | REST | API key (free) | Free tier | 300 req/day | JSON/CSV | Daily | 30+ years | Macro, commodities | High | Medium |
| 19 | **World Bank API** | FA/Geo | REST | None | Free | Unlimited | JSON | Quarterly/Annual | 60+ years | 200+ countries | Very High | Easy |
| 20 | **IMF Data API** | FA | REST | None | Free | Unlimited | JSON | Quarterly | 50+ years | 190 countries | Very High | Easy |
| 21 | **OECD Data API** | FA | REST | None | Free | Unlimited | JSON/CSV | Monthly | 50+ years | OECD countries | Very High | Easy |
| 22 | **Hugging Face / FinBERT** | Sentiment | Python | None | Free (local) | N/A | Tensor | N/A | N/A | NLP model | High | Medium (already partially built) |
| 23 | **Pushshift/Reddit Archive** | Sentiment | File DL | None | Free | N/A | JSON/ZSTD | Monthly dump | 2005-2023 | Reddit full archive | High | Hard (backfill only) |

---

## 2. Per-Engine Breakdown

### 2.1 GeoEngine (weight 5-15%)

**Current state:** GeoEngineV2 uses GDELT v2 API (`api.gdeltproject.org`). Maps country codes to instruments via `_COUNTRY_INSTRUMENTS`. Returns 0.0 because:
- GDELT `sourcecountry:XX` filter uses 2-letter codes but GDELT expects FIPS country codes (not ISO)
- Even when articles return, the `tone` field parsing works but the API returns empty `articles` array frequently
- No fallback when GDELT is down
- **No historical data** for backtesting

**Recommended sources:**

#### Source G1: ACLED (Armed Conflict Location & Event Data)
- **URL:** https://acleddata.com/
- **API:** REST (https://api.acleddata.com/acled/read)
- **Auth:** Free API key (register at acleddata.com, academic/research use)
- **Rate limit:** 500 requests/day, 50,000 records/request
- **Format:** JSON, CSV
- **Update frequency:** Weekly (Friday), with real-time data for paid tier
- **Historical depth:** 1997-present for Africa, 2018-present globally
- **Coverage:** Armed conflict, protests, riots, strategic developments in 190+ countries
- **Reliability:** Very High -- used by UN, World Bank, and major hedge funds
- **Integration effort:** Medium
- **Why critical:** ACLED provides structured event data with geo-coordinates, event types (battles, protests, violence against civilians), and fatality counts. This can be directly mapped to countries and then to forex pairs. Unlike GDELT's tone-based approach, ACLED events are human-coded and verified.
- **Mapping to instruments:**
  - Event count spike in EU countries -> EURUSD risk-off
  - Middle East conflict escalation -> XAUUSD bullish (safe haven), USOIL supply risk
  - US domestic unrest -> VIX spike signal
- **Historical backfill:** Full dataset downloadable as CSV (several GB). Weekly updates thereafter.

#### Source G2: Global Peace Index (GPI) + Fragile States Index
- **URL:** https://www.visionofhumanity.org/maps/ (GPI), https://fragilestatesindex.org/ (FSI)
- **API:** File download (CSV/XLSX from their data portal)
- **Auth:** None
- **Rate limit:** N/A
- **Format:** CSV, XLSX
- **Update frequency:** Annual (June for GPI, April for FSI)
- **Historical depth:** GPI: 2008-present, FSI: 2006-present
- **Coverage:** 163 countries (GPI), 178 countries (FSI)
- **Reliability:** Very High -- academic-quality indices
- **Integration effort:** Easy
- **Why useful:** Provides a baseline risk score per country. When combined with ACLED event data (deviations from baseline), you get a robust geopolitical risk signal. GPI scores 0-5 (5=most peaceful) can be used as a static risk premium per currency.
- **Mapping:** Create a `geo_baseline_risk` table. Pre-populate annually. Use as a multiplier for ACLED event signals.

#### Source G3: GDELT v2 (keep with fixes)
- **Current issue:** Country code format mismatch, API returns empty frequently
- **Fix needed:** Use GDELT's `domain` or `theme` query instead of `sourcecountry`. GDELT themes like `TAX_FNCACT_SANCTIONS`, `MILITARY`, `POLITICAL_TURMOIL` are more reliable than country filters.
- **Historical depth:** GKG (Global Knowledge Graph) has 15-min updates, free, but only last 6 months searchable via API. Full archive available via Google BigQuery (paid).
- **Recommendation:** Keep as a real-time supplement to ACLED, but do not rely on it as the sole geo source. Fix the query format.

#### Source G4: World Bank Governance Indicators
- **URL:** https://api.worldbank.org/v2/
- **API:** REST, no auth
- **Rate limit:** Unlimited
- **Format:** JSON, XML
- **Update frequency:** Annual (September)
- **Historical depth:** 1996-present
- **Coverage:** 200+ countries, 6 governance dimensions (political stability, rule of law, etc.)
- **Reliability:** Very High
- **Integration effort:** Easy
- **Why useful:** "Political Stability and Absence of Violence" indicator provides a 0-100 percentile score per country. Combined with ACLED's event-based approach, this gives both baseline and deviation signals.

---

### 2.2 SentimentEngine (weight 15-25%)

**Current state:** SentimentEngineV2 supports 4 sources (news, social, fear_greed, options) with renormalized weights. However, in `signal_engine.py` line 493-495:
```python
sent_engine = SentimentEngineV2(news_events=news_records)
```
Only `news_events` is passed. `social_data`, `fear_greed_index`, and `put_call_ratio` are never provided. The `SocialCollector` collects this data but it is not wired into the signal engine.

**Bug #1 (code, not data):** Even though `SocialCollector` saves `fear_greed_index`, `reddit_score`, `stocktwits_bullish_pct`, and `put_call_ratio` to the `social_sentiment` table, the `signal_engine.py` never queries this table and never passes this data to `SentimentEngineV2`. Fix: query `social_sentiment` for the current instrument and pass it to the constructor.

**Bug #2 (scoring):** News sentiment via TextBlob produces scores in [-1, +1] range with most articles scoring in [-0.1, +0.1] (very neutral). TextBlob is not designed for financial text. FinBERT is integrated but may not be running (requires GPU or API).

**Recommended sources:**

#### Source S1: Fix the wiring (no new source needed)
- **Priority:** CRITICAL
- **Effort:** 30 minutes of code change
- **Impact:** Immediately enables fear_greed (crypto), reddit, stocktwits, and PCR scores in composite
- **What to do:** In `signal_engine.py`, before creating `SentimentEngineV2`:
  1. Query `social_sentiment` table for the current `instrument.id` (latest row)
  2. Pass `social_data={"reddit_score": ..., "stocktwits_score": ...}`
  3. Pass `fear_greed_index=...` and `put_call_ratio=...`

#### Source S2: NewsAPI.org
- **URL:** https://newsapi.org/
- **API:** REST
- **Auth:** Free API key (100 requests/day), Developer plan $449/mo
- **Rate limit:** 100 req/day (free), 1000/day (developer)
- **Format:** JSON
- **Update frequency:** Real-time (articles indexed within minutes)
- **Historical depth:** 30 days (free), 5 years (paid)
- **Coverage:** 150,000+ news sources worldwide, searchable by keyword/domain
- **Reliability:** High
- **Integration effort:** Easy
- **Why useful:** Much better structured than RSS. Returns title, description, content, source, publishedAt. Can search by keyword (e.g., "EURUSD", "Federal Reserve", "Bitcoin") with relevance ranking. The 30-day history on free tier is enough for live, but useless for backtesting.

#### Source S3: Finnhub News Sentiment (already integrated)
- **URL:** https://finnhub.io/api/v1/news-sentiment
- **Auth:** Free API key (already configured)
- **Rate limit:** 60 req/min
- **Format:** JSON with pre-scored sentiment (buzz, bullish%, bearish%)
- **Historical depth:** Real-time only
- **Coverage:** US stocks
- **Reliability:** High
- **Integration effort:** Easy -- Finnhub already returns sentiment scores per ticker
- **Note:** The `FinnhubNewsCollector` already exists but uses generic `news` endpoint. The `/news-sentiment` endpoint provides pre-calculated bullish/bearish percentages per symbol, which is more useful than running TextBlob.

#### Source S4: Alternative.me Fear & Greed Historical
- **URL:** https://api.alternative.me/fng/?limit=0&format=json
- **Auth:** None
- **Rate limit:** Unlimited
- **Format:** JSON
- **Update frequency:** Daily
- **Historical depth:** Since Feb 2018 (full history with `limit=0`)
- **Coverage:** Crypto market (primarily BTC-driven)
- **Reliability:** Medium (single source, methodology opaque)
- **Integration effort:** Easy
- **Why critical for backtesting:** By passing `limit=0`, you get the entire history (2000+ daily points). This can be backfilled into `macro_data` table and used in backtest. Currently only the latest value is fetched.
- **Backfill command:** `GET https://api.alternative.me/fng/?limit=0&format=json` returns `[{value, timestamp, ...}, ...]`

#### Source S5: Reddit Archive (Pushshift) for backtesting
- **URL:** https://the-eye.eu/redarcs/ or https://academictorrents.com (Pushshift dumps)
- **API:** File download (compressed NDJSON)
- **Auth:** None
- **Rate limit:** N/A (static files)
- **Format:** ZSTD-compressed NDJSON, ~100GB per year
- **Update frequency:** Monthly dumps (through mid-2023, then spotty)
- **Historical depth:** 2005-2023
- **Coverage:** All of Reddit (filter to financial subreddits)
- **Reliability:** High (complete Reddit archive)
- **Integration effort:** Hard (need to decompress, filter, score, store)
- **Why useful:** The only way to backtest social sentiment. Filter for r/wallstreetbets, r/CryptoCurrency, r/investing, r/stocks, r/Forex. Score with FinBERT. Store daily aggregated sentiment per subreddit.
- **Practical approach:** Download only the subreddits of interest using pre-filtered dumps from PushShift. Score submissions and top-level comments. Aggregate to daily sentiment score per instrument.

#### Source S6: CNN Fear & Greed (Stocks)
- **URL:** https://production.dataviz.cnn.io/index/fearandgreed/graphdata
- **API:** REST (undocumented, used by CNN website)
- **Auth:** None
- **Rate limit:** Unknown (light use only)
- **Format:** JSON
- **Update frequency:** Real-time during market hours
- **Historical depth:** ~1 year in the graph data response
- **Coverage:** US stock market (S&P 500 derived)
- **Reliability:** Medium (undocumented endpoint, could break)
- **Integration effort:** Easy
- **Why useful:** Complements Alternative.me's crypto F&G with a stock market equivalent. The CNN F&G combines 7 indicators (put/call ratio, VIX, junk bond demand, stock momentum, stock strength, breadth, safe haven demand). The graphdata endpoint returns historical data points.

---

### 2.3 FAEngine (weight 10-50%)

**Current state:**
- `FAEngine` (forex/stocks): Uses FRED data (FEDFUNDS, CPIAUCSL, UNRATE, GDPC1). Works IF there are 2+ observations per series in `macro_data` to compute deltas. Currently, `FREDCollector` fetches `limit=12` observations but `get_macro_data(db, limit=200)` retrieves mixed records across all indicators. The `_delta()` method needs at least 2 records per indicator name.
- `CryptoFAEngine`: Uses on-chain data from CoinMetrics + cycle analysis. Partially working. NVT is always None (paid metric). MVRV works when CoinMetrics returns data.
- **COT integration** (SIM-41): Built but COT data may have only 1 annual observation (latest), preventing delta calculation.
- **Central bank rates:** Collected but never consumed by FAEngine.

**Bug #3:** `FREDCollector` stores data but `FAEngine._delta()` requires 2 records per indicator sorted desc by `release_date`. The `get_macro_data(db, limit=200)` may return 200 records across all indicators, and the FAEngine constructor loops them correctly -- but if only 1 record per indicator exists, all deltas return None and score = 0.

**Fix needed:** Ensure `FREDCollector` fetches at least `limit=24` (2 years monthly data) on first run, and stores all observations. Then deltas will work.

**Recommended sources:**

#### Source F1: FRED API (fix the collector)
- **Status:** Already integrated
- **Fix:** Change `FREDCollector._fetch_series(limit=12)` to `limit=60` for initial backfill (5 years monthly)
- **Add series:**
  - `DFF` -- Effective Federal Funds Rate (daily, more granular than FEDFUNDS monthly)
  - `T10Y2Y` -- 10Y-2Y Treasury spread (recession indicator)
  - `DTWEXBGS` -- Trade-Weighted Dollar Index (alternative to DXY)
  - `UMCSENT` -- University of Michigan Consumer Sentiment
  - `RSXFS` -- Advance Retail Sales
  - `PCE` or `PCEPI` -- Personal Consumption Expenditures
  - `ICSA` -- Initial Jobless Claims (weekly, leading indicator)
- **Historical depth:** 50-70 years for most series
- **Backfill:** One-time fetch with `limit=600` (50 years monthly) per series. Store all. ~5000 total records.

#### Source F2: FRED Historical Backfill Script
- **Priority:** CRITICAL for backtesting
- **What to build:** A one-time script that fetches full history for all FRED series:
  ```
  GET https://api.stlouisfed.org/fred/series/observations
    ?series_id=FEDFUNDS&api_key=KEY&file_type=json
    &observation_start=2000-01-01&sort_order=asc&limit=10000
  ```
- This gives ~300 monthly observations per series (25 years), enabling delta calculations throughout the backtest period.

#### Source F3: Interest Rate Differentials (derived from Central Bank Rates)
- **Status:** Central bank rates already collected by `CentralBankCollector` (FED, ECB, BOJ, BOE, RBA, BOC, SNB, RBNZ)
- **What to build:** A `rate_differential_calculator` that computes:
  - EURUSD differential = FED rate - ECB rate
  - GBPUSD differential = FED rate - BOE rate
  - USDJPY differential = FED rate - BOJ rate
  - etc.
- **Why:** Rate differentials are the single strongest fundamental driver of forex pairs in the medium term. This data is ALREADY in the database but never used by FAEngine.
- **Integration effort:** Easy -- query `central_bank_rates` table, compute diff, add to FA score.
- **Historical backfill:** Central bank rates are already historically available from FRED (FEDFUNDS) and respective bank APIs.

#### Source F4: Quandl/Nasdaq Data Link
- **URL:** https://data.nasdaq.com/
- **API:** REST
- **Auth:** Free API key (50 req/day), premium ($49-499/mo)
- **Rate limit:** 50/day (free), 2000/day (premium)
- **Format:** JSON, CSV
- **Update frequency:** Daily
- **Historical depth:** 30+ years for many datasets
- **Coverage:** Commodities (oil inventories, gold demand), macro (leading indicators), futures
- **Reliability:** High
- **Integration effort:** Medium
- **Key datasets (free):**
  - `FRED/*` -- mirror of FRED (redundant but useful as fallback)
  - `LBMA/GOLD` -- London gold fix price
  - `ODA/POILBRE_USD` -- Brent crude oil price
  - `MULTPL/SP500_PE_RATIO_MONTH` -- S&P 500 P/E ratio (monthly, 1871-present)

#### Source F5: Glassnode Free Tier (Crypto on-chain)
- **URL:** https://api.glassnode.com/
- **API:** REST
- **Auth:** Free API key (register at glassnode.com)
- **Rate limit:** 10 req/min
- **Format:** JSON
- **Update frequency:** Daily (some hourly on paid tier)
- **Historical depth:** 2 years on free tier (BTC since 2009 on paid)
- **Coverage:** BTC, ETH (free tier); 40+ assets on paid
- **Reliability:** Very High -- industry standard for on-chain analytics
- **Integration effort:** Medium
- **Free metrics:**
  - `market/mvrv` -- MVRV ratio (better quality than CoinMetrics)
  - `indicators/sopr` -- Spent Output Profit Ratio
  - `indicators/nupl` -- Net Unrealized Profit/Loss
  - `addresses/active_count` -- Active addresses
  - `transactions/count` -- Transaction count
  - `mining/hash_rate_mean` -- Network hash rate
- **Why useful:** Glassnode's MVRV and NUPL are more reliable than CoinMetrics free tier. SOPR is a powerful on-chain momentum indicator not available elsewhere free.

#### Source F6: World Bank / IMF / OECD for historical macro
- **URLs:**
  - https://api.worldbank.org/v2/
  - https://dataservices.imf.org/REST/SDMX_JSON.svc/
  - https://sdmx.oecd.org/public/rest/
- **Auth:** None for all three
- **Rate limit:** Generous (no hard limit documented)
- **Format:** JSON, XML
- **Update frequency:** Monthly/Quarterly
- **Historical depth:** 50+ years
- **Coverage:** GDP, CPI, unemployment, trade balance, current account for 200+ countries
- **Reliability:** Very High
- **Integration effort:** Easy-Medium
- **Why useful for backtesting:** These provide macro data for non-US countries that FRED doesn't cover well. For EURUSD analysis, you need Eurozone GDP, CPI, unemployment -- not just US data. World Bank/IMF are the definitive sources.
- **Key series:**
  - World Bank: GDP growth (`NY.GDP.MKTP.KD.ZG`), CPI (`FP.CPI.TOTL.ZG`), unemployment (`SL.UEM.TOTL.ZS`)
  - IMF: Current account balance, reserves, exchange rates
  - OECD: Composite Leading Indicators (CLI) -- very useful as economic cycle gauge

#### Source F7: Investing.com Economic Calendar (scraping fallback)
- **URL:** https://www.investing.com/economic-calendar/
- **API:** None (scraping required)
- **Auth:** None
- **Rate limit:** Be respectful (1 req/5s)
- **Format:** HTML (parse with BeautifulSoup)
- **Update frequency:** Real-time
- **Historical depth:** 5+ years
- **Coverage:** All countries, all impact levels, actual/forecast/previous values
- **Reliability:** High (but fragile due to scraping)
- **Integration effort:** Medium
- **Why useful:** The `calendar_collector.py` and `fmp_calendar_collector.py` exist but FMP's calendar is paid-only. Investing.com's calendar is the most comprehensive free alternative. Each event has impact level (bull icons), actual vs. forecast values, and country.
- **Alternative:** TradingEconomics calendar (already partially used) or ForexFactory calendar.

---

## 3. Recommended "Starter Pack" -- Minimum Free Sources

**Goal:** Get all 3 engines producing non-zero scores with zero cost.

### Phase 0: Fix code wiring (0 new sources, 2-4 hours dev work)

| Fix | Engine | Impact |
|-----|--------|--------|
| Wire `social_sentiment` data into `SentimentEngineV2` constructor in `signal_engine.py` | Sentiment | Enables F&G, Reddit, Stocktwits, PCR scores |
| Increase FRED `limit` from 12 to 60+ observations | FA | Enables delta calculations for all FRED indicators |
| Wire `central_bank_rates` into `FAEngine` for rate differentials | FA | Single strongest forex fundamental signal |
| Fix GDELT country code format (FIPS, not ISO) and add theme-based queries | Geo | Makes existing GDELT integration actually work |

**Expected outcome after Phase 0:** FA produces [-20, +20] range scores for forex/stocks. Sentiment produces [-30, +30] range from news + F&G + social. Geo produces [-50, +50] when GDELT works.

### Phase 1: Backfill historical data (4-8 hours dev work)

| Source | Engine | Action |
|--------|--------|--------|
| FRED full history | FA | Script to fetch 25 years of observations for all 8 series |
| Alternative.me F&G full history | Sentiment | `limit=0` returns entire history since Feb 2018 |
| ACLED download | Geo | Download full CSV dataset, load into `geo_events` table |
| Central bank rate history | FA | FRED has historical rates; ECB API has history since 1999 |

**Expected outcome after Phase 1:** Backtesting has FA, Sentiment (partial), and Geo data for 2018-present.

### Phase 2: Add new free sources (8-16 hours dev work)

| Source | Engine | Priority |
|--------|--------|----------|
| Glassnode free tier (MVRV, SOPR, NUPL) | FA (crypto) | High |
| CNN Fear & Greed (stocks) | Sentiment | Medium |
| Additional FRED series (T10Y2Y, ICSA, UMCSENT) | FA | Medium |
| World Bank governance indicators | Geo | Medium |
| Finnhub `/news-sentiment` endpoint | Sentiment | Medium |

---

## 4. Recommended "Pro Pack" -- 1-2 Paid Sources

If budget allows $50-500/month, these two sources cover the most gaps:

### Option A: Glassnode Professional ($29/mo)
- Unlocks: All on-chain metrics for 40+ crypto assets, full history to genesis block
- Impact: CryptoFAEngine goes from ~5 score to fully operational [-80, +80]
- Covers: NVT (currently None), full MVRV history, exchange flows, SOPR, NUPL, hash rate
- ROI: If the system trades crypto, this is the highest-value single data source

### Option B: Quandl Premium ($49/mo)
- Unlocks: 300+ datasets including commodities fundamentals, extended economic data
- Impact: FAEngine gets commodity inventory data (EIA crude, gold ETF flows), better stock fundamentals
- Covers: Historical P/E ratios, commodity supply/demand, leading indicators

### Option C: Polygon.io Starter ($29/mo)
- Unlocks: Real-time and historical stock/crypto data, news with sentiment
- Impact: Better price data, pre-scored news sentiment, ticker-level news
- Covers: Historical news sentiment (backtestable), options data, market-wide indicators

**Recommendation:** Start with Glassnode Pro ($29/mo) if crypto is a primary focus. Otherwise, keep everything free -- the Starter Pack covers 80% of needs.

---

## 5. Historical Data Strategy for Backtesting

This is the **most critical gap**. Without historical non-TA data, backtests only validate TA signals, making the 55% non-TA weight on swing timeframes a random noise amplifier.

### Backtestable Data Timeline

| Data Source | Available From | Resolution | Backfill Method |
|-------------|---------------|------------|-----------------|
| FRED macro (all series) | 1970+ | Monthly | Single API call per series, `observation_start=2000-01-01` |
| Central bank rates | 1999+ (ECB), 1954+ (FED) | Monthly | FRED for FED/BOJ; respective bank APIs for others |
| CFTC COT | 2004+ | Weekly | Download annual ZIP files for each year |
| Alternative.me F&G | Feb 2018 | Daily | `limit=0` single API call |
| ACLED conflict events | 2018+ (global) | Daily events | Full CSV download from ACLED portal |
| CoinMetrics on-chain | 2010+ (BTC) | Daily | Community API with `start_time` and `end_time` params |
| Glassnode (free) | 2022+ | Daily | API with date range params |
| GPI / FSI indices | 2008+ | Annual | CSV download |
| World Bank governance | 1996+ | Annual | API call with date range |
| News sentiment | NONE (live only) | N/A | **Gap** -- requires Reddit archive or paid NewsAPI |

### Recommended Backfill Script Structure

```
scripts/backfill_historical.py
  --source fred        # Backfill all FRED series 2000-present
  --source fear_greed  # Backfill Alternative.me F&G 2018-present
  --source cot         # Backfill CFTC COT 2018-present (annual ZIPs)
  --source acled       # Load ACLED CSV into geo_events table
  --source rates       # Backfill central bank rates 2000-present
  --source coinmetrics # Backfill on-chain data 2018-present
  --source all         # Run everything
```

### Critical Gap: Historical News Sentiment

For backtesting, there is no free source of historical scored news sentiment. Options:

1. **Reddit Archive + FinBERT** (free but hard): Download subreddit archives from Pushshift, score with FinBERT, aggregate to daily sentiment per instrument. This gives backtestable social sentiment for 2015-2023.

2. **Synthetic sentiment from price action** (free, easy): Use price-based sentiment proxies as backtest substitutes:
   - Fear & Greed index (available since 2018)
   - VIX as sentiment proxy (via FRED `VIXCLS`, available since 1990)
   - Put/Call ratio historical (CBOE data, available via FRED `PCERATIO` -- but discontinued)
   - Implied volatility skew

3. **Accept the gap**: For backtesting, set sentiment weight to 0 and redistribute to TA and FA. Only enable sentiment weight in live trading where real-time data is available.

**Recommendation:** Use option 2 (proxy indicators) for backtesting, supplemented by Alternative.me F&G history for crypto. Use option 1 (Reddit archive) only if backtesting shows sentiment weight materially affects PF.

---

## 6. Integration Priority Order

### Immediate (this week)

| # | Action | Engine | Type | Est. Hours | Expected Impact |
|---|--------|--------|------|-----------|-----------------|
| 1 | Fix: Wire social_sentiment into SentimentEngineV2 | Sentiment | Bug fix | 2h | Sentiment score goes from 0 to [-30,+30] |
| 2 | Fix: Increase FRED fetch limit + add series | FA | Bug fix | 2h | FA score goes from 0 to [-20,+20] for forex |
| 3 | Fix: Wire central_bank_rates as rate differentials | FA | Feature | 4h | Strongest forex fundamental signal enabled |
| 4 | Fix: GDELT query format (theme-based) | Geo | Bug fix | 2h | Geo score starts returning non-zero |

### Short-term (next 2 weeks)

| # | Action | Engine | Type | Est. Hours |
|---|--------|--------|------|-----------|
| 5 | Backfill: FRED 25-year history script | FA | Backfill | 4h |
| 6 | Backfill: Alternative.me F&G full history | Sentiment | Backfill | 2h |
| 7 | Backfill: Central bank rate history | FA | Backfill | 4h |
| 8 | New: ACLED integration + historical load | Geo | New source | 8h |
| 9 | Backfill: CoinMetrics 5-year history | FA | Backfill | 4h |

### Medium-term (next month)

| # | Action | Engine | Type | Est. Hours |
|---|--------|--------|------|-----------|
| 10 | New: Glassnode free tier (MVRV, SOPR, NUPL) | FA | New source | 8h |
| 11 | New: CNN Fear & Greed (stocks) | Sentiment | New source | 4h |
| 12 | New: World Bank governance indicators | Geo | New source | 4h |
| 13 | New: Finnhub news-sentiment endpoint | Sentiment | Enhancement | 4h |
| 14 | Backfill: Reddit archive scoring (if needed) | Sentiment | Backfill | 16h |

### Total estimated effort: ~68 hours

Items 1-4 alone (10 hours) should move the non-TA engines from 0.0 to meaningful scores, which is the highest-ROI investment.

---

## 7. Key Findings and Warnings

### FINDING-1: SentimentEngine data wiring is broken (Severity: Critical)
The SentimentEngineV2 supports 4 data sources but only receives `news_events`. The `social_sentiment` table is populated by `SocialCollector` but never read by the signal engine. Fear & Greed index is fetched by `fear_greed_collector.py` and used for SIM-39 adjustment, but never passed to the sentiment engine for the base sentiment score.

### FINDING-2: FAEngine cannot compute deltas (Severity: Critical)
FAEngine's `_delta()` and `_pct_change()` methods require 2+ observations per indicator. If FRED collector only stores 1 recent observation (or if macro_data has been cleaned), all deltas return None and FA score = 0.

### FINDING-3: Central bank rates collected but unused (Severity: Major)
`CentralBankCollector` fetches rates for 8 major central banks. This data sits in `central_bank_rates` table but is never consumed by any engine. Rate differentials are the single most important forex fundamental factor.

### FINDING-4: No historical data for backtesting (Severity: Critical)
The backtest engine runs with TA data only. FA, Sentiment, and Geo scores are all 0.0 during backtests because there is no historical macro/sentiment/geo data in the database. This means the backtest validates only ~45-70% of the signal model (TA weight), and the reported PF of 1.004 is entirely TA-driven.

### FINDING-5: GDELT integration is unreliable (Severity: Major)
GDELT returns empty results for most queries. The `sourcecountry` filter expects FIPS codes, and even with correct codes, the API frequently returns no articles. There is no fallback source. The geo score is effectively always 0.0.

### FINDING-6: TextBlob is inadequate for financial sentiment (Severity: Minor)
TextBlob's polarity scores cluster around 0.0 for financial text because it was trained on movie reviews. FinBERT is partially integrated but the fallback to TextBlob means most news gets a ~0 score. This is a known limitation -- the FinBERT path works correctly when available.
