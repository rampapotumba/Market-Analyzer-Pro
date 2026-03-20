# TASKS V7: Data Wiring, Historical Backfill, Backtest Methodology, and Strategy Implementation

## Overview

V7 addresses the root cause of low backtest reliability: non-TA engines (FA, Sentiment, Geo) produce near-zero scores due to broken data wiring and missing historical data. The backtest validates only ~45% of the signal model (TA weight on H4/D1). This plan fixes data wiring, backfills historical data, upgrades backtest methodology, and implements 5 specialized strategies.

**Current state:**
- FA score: ~0.0 (FRED has data but delta computation fails due to single observation per series)
- Sentiment score: ~0.0 (social_data, fear_greed, put_call never passed to SentimentEngineV2)
- Geo score: ~0.0 (GDELT uses wrong country code format, returns empty)
- Central bank rates: collected but never consumed by FAEngine
- Backtest: uses TA only, no walk-forward validation, no statistical significance tests

**Target:** All engines produce meaningful scores in both live and backtest. Walk-forward validated strategies with statistical significance testing.

---

## Phase 1: Data Wiring Fixes (fix what's broken)

Priority: CRITICAL. Estimated total: 16-20 hours.

### TASK-V7-01: Wire social_sentiment data into SentimentEngineV2
- **Phase:** 1
- **Problem:** In `signal_engine.py` line 493, `SentimentEngineV2` is constructed with only `news_events`. The `social_data`, `fear_greed_index`, and `put_call_ratio` parameters are never passed, even though `SocialCollector` populates the `social_sentiment` table with all four data points.
- **Goal:** SentimentEngineV2 receives all 4 data sources in live signal generation.
- **Files to modify:**
  - `src/signals/signal_engine.py` -- query `social_sentiment` table before creating SentimentEngineV2
  - `src/database/crud.py` -- add `get_latest_social_sentiment(db, instrument_id)` function
- **Implementation details:**
  1. Add CRUD function `get_latest_social_sentiment(db: AsyncSession, instrument_id: int)` that queries `social_sentiment` table for the latest row for this instrument (ordered by timestamp DESC, limit 1).
  2. In `SignalEngine.generate_signal()`, after fetching `news_records` (line 449), call `get_latest_social_sentiment(db, instrument.id)`.
  3. Extract from the result: `fear_greed_index`, `reddit_score`, `stocktwits_bullish_pct`, `put_call_ratio`.
  4. Build `social_data` dict: `{"reddit_score": ..., "stocktwits_score": ...}` (map `stocktwits_bullish_pct` to score via `(pct - 50) * 2`).
  5. Pass all to SentimentEngineV2 constructor:
     ```python
     sent_engine = SentimentEngineV2(
         news_events=news_records,
         social_data=social_data,
         fear_greed_index=fg_value,
         put_call_ratio=pcr_value,
     )
     ```
  6. Handle None gracefully -- if no social_sentiment row exists, pass None for each (SentimentEngineV2 already handles None via weight renormalization).
- **Acceptance criteria:**
  - [ ] `SentimentEngineV2` receives `social_data`, `fear_greed_index`, `put_call_ratio` when data exists in DB
  - [ ] When `social_sentiment` table is empty, engine still works (returns news-only score)
  - [ ] Unit test: mock social_sentiment data, verify SentimentEngineV2 produces non-zero score from social+fg sources
  - [ ] Sentiment score in live signals goes from ~0 to [-30, +30] range when social data is collected
- **Dependencies:** None
- **Complexity:** S

---

### TASK-V7-02: Fix FRED collector fetch limit for delta computation
- **Phase:** 1
- **Problem:** `FREDCollector._fetch_series()` defaults to `limit=12`. The `FAEngine._delta()` method needs 2+ observations per indicator sorted by `release_date` DESC. If only 1 record per indicator exists in `macro_data`, all deltas return None and FA score = 0. Additionally, `get_macro_data(db, limit=200)` returns 200 records across ALL indicators mixed together, so with 8 series x 12 observations = 96 records, this works -- but in practice the DB may have fewer due to dedup/upsert.
- **Goal:** FRED collector fetches enough history for reliable delta computation; FAEngine produces non-zero scores.
- **Files to modify:**
  - `src/collectors/macro_collector.py` -- change default `limit` from 12 to 60 (5 years monthly)
  - `src/signals/signal_engine.py` -- increase `get_macro_data(db, limit=200)` to `limit=500` to cover all series with history
- **Implementation details:**
  1. In `FREDCollector._fetch_series()`, change default `limit=12` to `limit=60`.
  2. In `FREDCollector.collect_series()`, the `_with_retry` call already uses `_fetch_series` with default args -- no change needed.
  3. In `signal_engine.py` line 448, change `get_macro_data(db, limit=200)` to `get_macro_data(db, limit=500)`.
  4. Add FRED series recommended by analyst: `DFF` (daily Fed Funds), `T10Y2Y` (yield curve), `DTWEXBGS` (trade-weighted dollar), `UMCSENT` (Michigan Consumer Sentiment). Add to `FRED_SERIES` dict in `macro_collector.py`.
- **Acceptance criteria:**
  - [ ] `FREDCollector._fetch_series()` fetches 60 observations by default
  - [ ] `FAEngine._delta("FEDFUNDS")` returns non-None when 2+ FRED observations exist
  - [ ] FA score for forex instruments goes from ~0 to [-20, +20] range
  - [ ] New FRED series (DFF, T10Y2Y, DTWEXBGS, UMCSENT) are collected
  - [ ] Unit test: construct FAEngine with 2+ observations per indicator, verify non-zero score
- **Dependencies:** None
- **Complexity:** S

---

### TASK-V7-03: Wire central_bank_rates into FAEngine for rate differentials
- **Phase:** 1
- **Problem:** `CentralBankCollector` fetches rates for 8 major banks (FED, ECB, BOJ, BOE, RBA, BOC, SNB, RBNZ) and stores in `central_bank_rates` table. This data is never consumed by `FAEngine`. Interest rate differentials are the single strongest fundamental driver of forex pairs.
- **Goal:** FAEngine uses central bank rate differentials as a scoring component for forex instruments.
- **Files to modify:**
  - `src/analysis/fa_engine.py` -- add `_analyze_rate_differential()` method, call it from `calculate_fa_score()`
  - `src/database/crud.py` -- add `get_central_bank_rates(db)` function
  - `src/signals/signal_engine.py` -- fetch and pass central bank rates to FAEngine
- **Implementation details:**
  1. Add CRUD function `get_central_bank_rates(db: AsyncSession) -> list[CentralBankRate]` that returns latest rate per bank.
  2. Define currency-pair-to-banks mapping in FAEngine:
     ```python
     _PAIR_BANK_MAP = {
         "EURUSD=X": ("FED", "ECB"),   # USD rate - EUR rate
         "GBPUSD=X": ("FED", "BOE"),
         "USDJPY=X": ("FED", "BOJ"),
         "AUDUSD=X": ("FED", "RBA"),
         "USDCAD=X": ("FED", "BOC"),
         "USDCHF=X": ("FED", "SNB"),
         "NZDUSD=X": ("FED", "RBNZ"),
     }
     ```
  3. Add FAEngine constructor parameter `central_bank_rates: Optional[dict[str, float]] = None` (bank -> rate mapping).
  4. Implement `_analyze_rate_differential()`:
     - Look up the two banks for the instrument's symbol
     - Compute differential: `base_rate - quote_rate`
     - Positive differential -> quote currency (USD for EURUSD) is stronger -> score impact (direction depends on pair)
     - Scale: differential * 10 as score contribution (e.g., 1% diff -> 10 score points)
  5. In `calculate_fa_score()`, add rate differential as 30% of final forex FA score:
     `final_score = base_score * 0.6 + rate_diff_score * 0.3 + news_adj * 0.1`
  6. In `signal_engine.py`, fetch rates before creating FAEngine, pass as dict.
- **Acceptance criteria:**
  - [ ] FAEngine receives and uses central bank rate data for forex pairs
  - [ ] Rate differential computed for all major forex pairs
  - [ ] FA score for forex includes rate differential component
  - [ ] Graceful degradation: if no rate data, component = 0 (not blocking)
  - [ ] Unit test: mock rates {FED: 5.25, ECB: 4.50}, verify EURUSD FA score reflects differential
- **Dependencies:** None
- **Complexity:** M

---

### TASK-V7-04: Fix GDELT queries in GeoEngineV2
- **Phase:** 1
- **Problem:** GeoEngineV2 uses `sourcecountry:XX` GDELT filter with 2-letter ISO country codes, but GDELT expects FIPS country codes (not ISO). Even with correct codes, the API frequently returns empty `articles` array. The country-to-instrument mapping uses bare symbols (e.g., "EURUSD") but our system uses "EURUSD=X".
- **Goal:** GeoEngineV2 returns non-zero scores for major instruments using theme-based GDELT queries.
- **Files to modify:**
  - `src/analysis/geo_engine_v2.py` -- fix query format, update country-instrument mapping
- **Implementation details:**
  1. Replace `sourcecountry:XX` with theme-based queries. GDELT themes are more reliable:
     - US: `theme:TAX_FNCACT OR theme:ECON_BANKRUPTCY OR theme:POLITICAL_TURMOIL sourcecountry:US`
     - EU: `theme:EUROZONE OR domain:ecb.europa.eu`
     - Use `domain:` filter as supplement (e.g., `domain:reuters.com`)
  2. Fix `_COUNTRY_INSTRUMENTS` mapping to match our symbol format:
     ```python
     _COUNTRY_INSTRUMENTS = {
         "US": ["EURUSD=X", "USDJPY=X", "GBPUSD=X", "GC=F", "SPY", "BTC/USDT"],
         "EU": ["EURUSD=X"],
         "UK": ["GBPUSD=X"],
         ...
     }
     ```
  3. Add fallback: if GDELT returns empty, try a broader query (just the theme, no country filter).
  4. Add circuit breaker: if 3 consecutive failures for a country, skip for 1 hour.
  5. Consider using `mode=tonechart` instead of `artlist` -- it returns aggregate tone without individual articles, which is more reliable and cheaper.
- **Acceptance criteria:**
  - [ ] GDELT queries return non-empty results for at least US and EU country codes
  - [ ] Symbol mapping matches our format (EURUSD=X not EURUSD)
  - [ ] Fallback query activates when primary query returns empty
  - [ ] Circuit breaker prevents repeated failed requests
  - [ ] Geo score for major forex pairs goes from 0 to [-50, +50] range when GDELT is responding
  - [ ] Unit test: mock GDELT response, verify score calculation
- **Dependencies:** None
- **Complexity:** M

---

## Phase 2: Historical Data Backfill (load data for backtesting)

Priority: HIGH. Estimated total: 24-32 hours.

### TASK-V7-05: Create unified historical backfill script
- **Phase:** 2
- **Problem:** No historical non-TA data exists for backtesting. The backtest validates only TA (45% weight on swing TFs). Need a script to populate historical FA, Sentiment, and Geo data.
- **Goal:** Create `scripts/backfill_historical.py` that can backfill data from multiple sources.
- **Files to create:**
  - `scripts/backfill_historical.py` -- CLI script with `--source` flag
- **Implementation details:**
  1. Create CLI script using `argparse`:
     ```
     python scripts/backfill_historical.py --source fred         # FRED 25-year history
     python scripts/backfill_historical.py --source fear_greed   # Alternative.me F&G since 2018
     python scripts/backfill_historical.py --source rates        # Central bank rates history
     python scripts/backfill_historical.py --source coinmetrics  # On-chain data since 2018
     python scripts/backfill_historical.py --source all          # Everything
     ```
  2. Each source is a separate async function.
  3. Use existing DB models and upsert functions.
  4. Add progress logging (every 100 records).
  5. Idempotent: re-running should not create duplicates (use upsert).
- **Acceptance criteria:**
  - [ ] Script runs without errors for each `--source`
  - [ ] `--source all` runs all sources sequentially
  - [ ] Progress logged to console
  - [ ] Idempotent (safe to re-run)
- **Dependencies:** TASK-V7-02 (FRED series list)
- **Complexity:** S (scaffold only, actual sources in subsequent tasks)

---

### TASK-V7-06: FRED macro data backfill (25 years)
- **Phase:** 2
- **Problem:** Need historical FRED data for backtest FA scoring. Current DB has only recent observations.
- **Goal:** Backfill all FRED series from 2000-01-01 to present into `macro_data` table.
- **Files to modify:**
  - `scripts/backfill_historical.py` -- implement `backfill_fred()` function
- **Implementation details:**
  1. For each series in `FRED_SERIES` (including new ones from TASK-V7-02):
     ```
     GET https://api.stlouisfed.org/fred/series/observations
       ?series_id=FEDFUNDS&api_key=KEY&file_type=json
       &observation_start=2000-01-01&sort_order=asc&limit=10000
     ```
  2. Parse all observations, skip "." values.
  3. Upsert into `macro_data` table using existing `upsert_macro_data()`.
  4. Rate limit: 1 request per second (FRED allows 120/min but be conservative).
  5. Expected volume: ~300 observations x 12 series = ~3600 records.
- **Acceptance criteria:**
  - [ ] All FRED series have data from 2000-01-01 to present in `macro_data`
  - [ ] At least 200+ observations per monthly series
  - [ ] Script completes in under 5 minutes
  - [ ] FAEngine can compute deltas for any date in the backtest range
- **Dependencies:** TASK-V7-05, TASK-V7-02
- **Complexity:** S

---

### TASK-V7-07: Fear & Greed full history backfill
- **Phase:** 2
- **Problem:** Need historical Fear & Greed index for backtest sentiment scoring and Crypto Extreme strategy. Currently only latest value is fetched.
- **Goal:** Backfill complete F&G history (since Feb 2018) into a queryable table.
- **Files to modify:**
  - `scripts/backfill_historical.py` -- implement `backfill_fear_greed()` function
  - `src/database/models.py` -- add `FearGreedHistory` model (or reuse `macro_data` with indicator_name="FEAR_GREED")
- **Implementation details:**
  1. Single API call: `GET https://api.alternative.me/fng/?limit=0&format=json`
  2. Returns ~3000 daily data points since Feb 2018.
  3. Store in `macro_data` table with `indicator_name="FEAR_GREED"`, `country="GLOBAL"`, `source="alternative.me"`.
  4. Parse each entry: `{"value": "25", "value_classification": "Extreme Fear", "timestamp": "1609459200"}`.
  5. Convert Unix timestamp to datetime.
- **Acceptance criteria:**
  - [ ] F&G data from Feb 2018 to present in `macro_data` table
  - [ ] ~2800+ daily records
  - [ ] Queryable by date range for backtest use
  - [ ] Unit test: verify data integrity (all values 0-100, no gaps > 3 days)
- **Dependencies:** TASK-V7-05
- **Complexity:** S

---

### TASK-V7-08: Central bank rate history backfill
- **Phase:** 2
- **Problem:** Need historical rate differentials for backtesting forex FA. Current DB has only latest rates.
- **Goal:** Backfill central bank rate history from 2000-present.
- **Files to modify:**
  - `scripts/backfill_historical.py` -- implement `backfill_central_bank_rates()` function
- **Implementation details:**
  1. FED rate history: use FRED series `FEDFUNDS` (already covered in TASK-V7-06, read from `macro_data`).
  2. ECB rate history: ECB API with date range:
     `https://data-api.ecb.europa.eu/service/data/FM/B.U2.EUR.4F.KR.MRR_FR.LEV?format=jsondata&startPeriod=2000-01`
  3. For other banks: use FRED mirror series where available:
     - BOE: FRED `INTDSRGBM193N` (Bank Rate, monthly since 1694)
     - BOJ: FRED `INTDSRJPM193N` (Discount Rate)
     - BOC: FRED `IR3TIB01CAM156N` (3-Month, or use BOC Valet with date range)
     - RBA: FRED `RBATCTR` (Cash Rate Target)
  4. Store in `central_bank_rates` table with historical effective_dates.
  5. For banks without easy API history, use monthly snapshots from FRED.
- **Acceptance criteria:**
  - [ ] FED, ECB, BOE, BOJ rates available from 2000-present
  - [ ] At least monthly resolution for all major banks
  - [ ] Rate differential can be computed for any backtest date
- **Dependencies:** TASK-V7-05, TASK-V7-03
- **Complexity:** M

---

### TASK-V7-09: ACLED geopolitical event data integration
- **Phase:** 2
- **Problem:** GeoEngine has no historical data source for backtesting. GDELT only has 6 months of searchable history. ACLED provides structured conflict/protest data since 2018 globally.
- **Goal:** Download and load ACLED data for geo scoring in backtests.
- **Files to create:**
  - `src/collectors/acled_collector.py` -- ACLED API collector
  - `scripts/backfill_historical.py` -- implement `backfill_acled()` function
- **Files to modify:**
  - `src/database/models.py` -- add `GeoEvent` model
  - `alembic/versions/` -- new migration for `geo_events` table
- **Implementation details:**
  1. Create `geo_events` table:
     ```sql
     CREATE TABLE geo_events (
       id SERIAL PRIMARY KEY,
       source VARCHAR(20) NOT NULL,           -- 'ACLED', 'GDELT'
       event_date TIMESTAMPTZ NOT NULL,
       country VARCHAR(100) NOT NULL,
       event_type VARCHAR(100),               -- 'Battles', 'Protests', 'Violence against civilians', etc.
       fatalities INTEGER DEFAULT 0,
       severity_score DECIMAL(5,2),           -- Computed score [-100, +100]
       raw_data JSONB,
       created_at TIMESTAMPTZ DEFAULT NOW()
     );
     CREATE INDEX ix_geo_events_country_date ON geo_events(country, event_date);
     ```
  2. ACLED API: `https://api.acleddata.com/acled/read?key=KEY&email=EMAIL&limit=0&...`
     - Filter: `event_date >= 2018-01-01`
     - Fields: event_date, country, event_type, fatalities, sub_event_type
  3. Map ACLED event types to severity scores:
     - Battles: -80
     - Violence against civilians: -90
     - Explosions/Remote violence: -70
     - Protests: -30 (peaceful) to -50 (violent)
     - Strategic developments: -20 to +20
  4. Aggregate daily per country: sum severity scores, cap at [-100, +100].
  5. **Note:** ACLED requires free API key registration (academic/research use). Add `ACLED_API_KEY` and `ACLED_EMAIL` to Settings.
- **Acceptance criteria:**
  - [ ] `geo_events` table created with migration
  - [ ] ACLED data from 2018-present loaded for major countries (US, EU nations, UK, JP, CN, AU, CA, CH, NZ, Middle East)
  - [ ] Daily aggregated severity score computable per country
  - [ ] ACLED collector can fetch weekly updates
- **Dependencies:** TASK-V7-05
- **Complexity:** L

---

### TASK-V7-10: CoinMetrics on-chain data backfill
- **Phase:** 2
- **Problem:** CryptoFAEngine uses CoinMetrics but current data is only recent. Need 5+ years for BTC/ETH backtest.
- **Goal:** Backfill CoinMetrics community metrics (MVRV, active addresses, tx count) from 2018-present.
- **Files to modify:**
  - `scripts/backfill_historical.py` -- implement `backfill_coinmetrics()` function
- **Implementation details:**
  1. CoinMetrics Community API (free, no auth):
     ```
     GET https://community-api.coinmetrics.io/v4/timeseries/asset-metrics
       ?assets=btc,eth&metrics=CapMVRVCur,AdrActCnt,TxCnt
       &start_time=2018-01-01&end_time=2026-03-20&frequency=1d
     ```
  2. Store in appropriate table (likely `macro_data` with indicator_name prefix "COINMETRICS_BTC_MVRV" etc.).
  3. Expected volume: ~3000 days x 3 metrics x 2 assets = ~18000 records.
  4. Rate limit: 100 requests per 10 minutes. Batch by metric and asset.
- **Acceptance criteria:**
  - [ ] BTC and ETH on-chain metrics from 2018-present in DB
  - [ ] MVRV, AdrActCnt, TxCnt available for both assets
  - [ ] CryptoFAEngine can use historical on-chain data for any backtest date
- **Dependencies:** TASK-V7-05
- **Complexity:** M

---

## Phase 3: Backtest Engine Enhancement

Priority: HIGH. Estimated total: 40-50 hours.

### TASK-V7-11: Extend backtest to load and use FA/Sentiment/Geo data
- **Phase:** 3
- **Problem:** BacktestEngine line 4 says `fa/sentiment/geo = 0.0 (neutral per SIM-17)`. This means the backtest tests a fundamentally DIFFERENT strategy than live. The composite score in backtest is `0.45 * ta_score`, while live uses all 4 components. Backtest results cannot validate the live system.
- **Goal:** BacktestEngine loads historical FA, Sentiment, and Geo data and uses them for composite score computation during backtest.
- **Files to modify:**
  - `src/backtesting/backtest_engine.py` -- major changes to signal generation loop
  - `src/backtesting/backtest_params.py` -- add `use_fundamental_data: bool` flag
  - `src/database/crud.py` -- add functions to query historical macro/sentiment/geo data by date range
- **Implementation details:**
  1. Add new BacktestParams fields:
     ```python
     use_fundamental_data: bool = False    # When True, load FA/Sentiment/Geo for backtest
     available_weight_override: Optional[float] = None  # Override available_weight (None = auto-detect)
     ```
  2. At backtest start, if `use_fundamental_data=True`:
     - Load all `macro_data` for the backtest date range (pre-fetch once, index by date)
     - Load all `geo_events` for the date range (pre-fetch once, index by country+date)
     - Load `FEAR_GREED` indicator from `macro_data`
     - Load `central_bank_rates` with effective dates
  3. During candle-by-candle iteration, for each signal:
     - Find macro records with `release_date <= candle_timestamp` (no lookahead!)
     - Compute FA score using FAEngine with historical macro data
     - Compute Sentiment score: use F&G index for the date, news = None (no historical news)
     - Compute Geo score: use ACLED daily aggregate for relevant countries
  4. Composite score becomes: `ta_weight * ta + fa_weight * fa + sent_weight * sent + geo_weight * geo`
  5. When `use_fundamental_data=False`, keep current behavior (`0.45 * ta_score` with scaled thresholds).
  6. Set `available_weight` properly: if all components have data, `available_weight=1.0`; if only TA+FA, sum their weights; etc.
  7. **Critical: no lookahead.** Only use data with dates strictly before the current candle timestamp.
- **Acceptance criteria:**
  - [ ] Backtest with `use_fundamental_data=True` produces different results than TA-only
  - [ ] FA scores in backtest are non-zero when historical FRED data exists
  - [ ] Sentiment scores reflect historical F&G index
  - [ ] Geo scores reflect historical ACLED events
  - [ ] No lookahead bias: all data queries filtered by `date <= candle_ts`
  - [ ] Backward compatible: `use_fundamental_data=False` produces identical results to current
  - [ ] Unit test: mock historical data, verify composite score uses all components
- **Dependencies:** TASK-V7-06, TASK-V7-07, TASK-V7-08, TASK-V7-09
- **Complexity:** XL

---

### TASK-V7-12: Fix regime detection in backtest (REQ-BT-009)
- **Phase:** 3
- **Problem:** Regime is UNKNOWN for 100% of backtest trades. `_detect_regime_from_df()` exists in backtest_engine.py but the result is not persisted to trade records. BLOCKED_REGIMES includes 4 regimes but this was calibrated without any regime data.
- **Goal:** Every backtest trade has a valid regime. Summary includes per-regime metrics.
- **Files to modify:**
  - `src/backtesting/backtest_engine.py` -- ensure regime from `_detect_regime_from_df()` is stored in `BacktestTradeResult.regime`
- **Implementation details:**
  1. In the backtest signal generation loop, `_detect_regime_from_df(df_slice)` is likely already called (for regime filtering via `SignalFilterPipeline`). Verify the regime value is passed to the `BacktestTradeResult` constructor.
  2. Check the `context` dict passed to `SignalFilterPipeline.run_all()` -- it should have `"regime": detected_regime`. If regime is set to "DEFAULT" or empty string, trace back to `_detect_regime_from_df()` to ensure it gets real data.
  3. Verify `_detect_regime_from_df()` receives enough candles (needs 200+ for SMA200 in RegimeDetector). If fewer candles available early in backtest, use "DEFAULT" with a warning.
  4. Add per-regime breakdown to `_compute_summary()`: WR, PF, avg R:R per regime.
  5. If any regime has 0 trades, note it in summary (e.g., "STRONG_TREND_BULL: 0 trades -- all blocked or never detected").
- **Acceptance criteria:**
  - [ ] No backtest trade has `regime=None` or `regime="UNKNOWN"`
  - [ ] Summary includes `by_regime` section with WR and PF per regime
  - [ ] Blocked regimes show 0 trades (confirming filter works)
  - [ ] Unblocked regimes show trade counts
- **Dependencies:** None
- **Complexity:** M

---

### TASK-V7-13: Add statistical significance tests (REQ-BT-006)
- **Phase:** 3
- **Problem:** No backtest includes statistical tests. Cannot determine if PF 2.01 on 33 trades is significant.
- **Goal:** Every backtest summary includes t-test, bootstrap CI for PF, Sharpe, Sortino.
- **Files to modify:**
  - `src/backtesting/backtest_engine.py` -- add `_compute_statistical_tests()` function, call from `_compute_summary()`
- **Implementation details:**
  1. Add function `_compute_statistical_tests(trades: list[BacktestTradeResult]) -> dict`:
     ```python
     import numpy as np
     from scipy import stats

     returns = [float(t.pnl_usd) for t in trades if t.exit_reason != "end_of_data"]

     # t-test: H0 = mean return = 0
     t_stat, p_value = stats.ttest_1samp(returns, 0.0)

     # Bootstrap PF confidence interval (10000 resamples)
     bootstrap_pfs = []
     for _ in range(10000):
         sample = np.random.choice(returns, size=len(returns), replace=True)
         wins = sum(r for r in sample if r > 0)
         losses = abs(sum(r for r in sample if r < 0))
         bootstrap_pfs.append(wins / losses if losses > 0 else float('inf'))
     pf_5th = np.percentile(bootstrap_pfs, 5)
     pf_95th = np.percentile(bootstrap_pfs, 95)

     # Sharpe ratio (annualized, assuming 252 trading days)
     mean_ret = np.mean(returns)
     std_ret = np.std(returns, ddof=1)
     sharpe = (mean_ret / std_ret) * np.sqrt(252) if std_ret > 0 else 0

     # Sortino ratio (downside deviation only)
     downside_returns = [r for r in returns if r < 0]
     downside_std = np.std(downside_returns, ddof=1) if len(downside_returns) > 1 else std_ret
     sortino = (mean_ret / downside_std) * np.sqrt(252) if downside_std > 0 else 0

     # Max consecutive losses
     max_consec = _max_consecutive_losses(returns)

     # Composite significance verdict
     is_significant = (
         p_value < 0.05
         and pf_5th > 1.0
         and sharpe > 0.5
     )
     ```
  2. Add to summary under key `"statistical_tests"`.
  3. Add `scipy` to requirements if not already present.
  4. Handle edge case: if < 10 trades, return `{"verdict": "INSUFFICIENT_DATA", "min_trades": 10}`.
- **Acceptance criteria:**
  - [ ] Summary includes `statistical_tests` section with t-test p-value, bootstrap PF CI, Sharpe, Sortino
  - [ ] Composite verdict: "SIGNIFICANT", "NOT_SIGNIFICANT", or "INSUFFICIENT_DATA"
  - [ ] Bootstrap uses 10000 resamples
  - [ ] Edge cases handled: 0 trades, all wins, all losses
  - [ ] Unit test: known trade set produces expected statistical values
- **Dependencies:** None
- **Complexity:** M

---

### TASK-V7-14: Add sample size adequacy check (REQ-BT-001)
- **Phase:** 3
- **Problem:** Backtests with 33 trades make statistical claims. Need clear indicators of sample adequacy.
- **Goal:** Backtest summary includes sample adequacy assessment and confidence intervals for WR and PF.
- **Files to modify:**
  - `src/backtesting/backtest_engine.py` -- add to `_compute_summary()`
- **Implementation details:**
  1. Add `sample_adequacy` section to summary:
     ```python
     n = len(metric_trades)
     # Binomial CI for win rate
     from scipy.stats import binom
     wr = len(wins) / n if n > 0 else 0
     wr_ci_low = binom.ppf(0.025, n, wr) / n if n > 0 else 0
     wr_ci_high = binom.ppf(0.975, n, wr) / n if n > 0 else 0

     adequacy = "SUFFICIENT" if n >= 100 else "MARGINAL" if n >= 50 else "INSUFFICIENT"
     ```
  2. Display in summary:
     ```json
     "sample_adequacy": {
       "total_trades": 33,
       "verdict": "INSUFFICIENT",
       "min_recommended": 100,
       "win_rate_ci_95": [28.0, 63.0],
       "note": "Results are not statistically reliable with fewer than 100 trades"
     }
     ```
- **Acceptance criteria:**
  - [ ] Summary includes `sample_adequacy` with verdict and CI
  - [ ] Trades < 50: "INSUFFICIENT", 50-99: "MARGINAL", 100+: "SUFFICIENT"
  - [ ] Win rate 95% CI computed using binomial distribution
- **Dependencies:** None
- **Complexity:** S

---

### TASK-V7-15: Filter activation statistics (REQ-BT-003)
- **Phase:** 3
- **Problem:** v5 Phase 2 and Phase 3 produced identical results despite adding 8 filters. We cannot tell if filters actually activate in backtest.
- **Goal:** Backtest summary includes per-filter activation counts.
- **Files to modify:**
  - `src/backtesting/backtest_engine.py` -- integrate `SignalFilterPipeline.get_stats()` into summary
- **Implementation details:**
  1. `SignalFilterPipeline` already tracks `rejection_counts` via `get_stats()` (added in TASK-V6-10).
  2. Verify that the pipeline's stats are included in the backtest summary.
  3. After the backtest loop completes, call `pipeline.get_stats()` and merge into summary under `"filter_activation_stats"`.
  4. Add warnings for filters with 0 rejections:
     ```python
     for filter_name, count in stats.items():
         if filter_name.startswith("rejected_by_") and count == 0:
             warnings.append(f"Filter '{filter_name}' never triggered -- verify data availability")
     ```
  5. Add warning if regime is always "DEFAULT" or always the same regime.
- **Acceptance criteria:**
  - [ ] Summary includes `filter_activation_stats` showing checked/blocked counts per filter
  - [ ] Filters with 0 blocks produce a warning in summary
  - [ ] After TASK-V7-12, regime filter shows actual blocking counts
- **Dependencies:** TASK-V7-12
- **Complexity:** S

---

### TASK-V7-16: Implement walk-forward validation (REQ-BT-002)
- **Phase:** 3
- **Problem:** All backtests run on a single in-sample period. No out-of-sample validation. PF 2.01 on v5 P2 vs PF 1.09 on r4 (different window) = classic overfitting.
- **Goal:** BacktestEngine supports `mode="walk_forward"` with configurable folds.
- **Files to modify:**
  - `src/backtesting/backtest_engine.py` -- add `_run_walk_forward()` method
  - `src/backtesting/backtest_params.py` -- already has `enable_walk_forward`, `in_sample_months`, `out_of_sample_months`
- **Implementation details:**
  1. When `params.enable_walk_forward=True`, instead of running a single backtest:
     - Compute fold boundaries: anchored expanding window.
     - Example with start=2020-01, end=2025-12, IS=18mo, OOS=6mo:
       ```
       Fold 1: IS 2020-01 to 2021-06, OOS 2021-07 to 2021-12
       Fold 2: IS 2020-01 to 2021-12, OOS 2022-01 to 2022-06
       Fold 3: IS 2020-01 to 2022-06, OOS 2022-07 to 2022-12
       ... etc
       ```
  2. For each fold:
     - Run IS backtest (for metrics comparison, not parameter tuning -- parameters are fixed)
     - Run OOS backtest
     - Record OOS trades and metrics
  3. Aggregate all OOS trades for final metrics.
  4. Report per-fold OOS metrics table:
     ```json
     "walk_forward": {
       "folds": [
         {"fold": 1, "oos_start": "2021-07", "oos_end": "2021-12", "trades": 25, "pf": 1.3, "wr": 48},
         ...
       ],
       "aggregate_oos": {"trades": 150, "pf": 1.25, "wr": 46, "sharpe": 0.9},
       "all_folds_profitable": true,
       "verdict": "VALID"  // all folds PF > 1.0 AND aggregate OOS PF > 1.2
     }
     ```
  5. Strategy is valid only if ALL folds show PF > 1.0 and aggregate OOS PF > 1.2.
- **Acceptance criteria:**
  - [ ] Walk-forward mode produces per-fold metrics
  - [ ] OOS trades are correctly separated from IS trades
  - [ ] No lookahead: OOS period only uses data available up to IS end
  - [ ] Aggregate OOS metrics computed from concatenated OOS trades
  - [ ] Verdict based on all-folds-profitable AND aggregate PF > 1.2
  - [ ] Backward compatible: `enable_walk_forward=False` runs single period (current behavior)
- **Dependencies:** TASK-V7-13 (statistical tests for per-fold metrics)
- **Complexity:** XL

---

### TASK-V7-17: Data integrity verification (REQ-BT-007)
- **Phase:** 3
- **Problem:** No pre-backtest data quality check. Regime is UNKNOWN, D1 data may not be loaded, forex volume is always 0.
- **Goal:** Automated data quality check before backtest runs.
- **Files to modify:**
  - `src/backtesting/backtest_engine.py` -- add `_check_data_quality()` method
- **Implementation details:**
  1. Before main backtest loop, run quality checks:
     - Count candles per instrument vs expected (H1: ~17 candles/day * trading days)
     - Detect gaps > 2x normal interval
     - Verify OHLC integrity: `high >= max(open, close)`, `low <= min(open, close)`
     - Check for duplicate timestamps
     - Report volume availability (% candles with volume > 0)
     - If `use_fundamental_data=True`, check macro_data record count per indicator
     - Check D1 data availability for each instrument (needed for MA200 filter)
  2. Return `data_quality` dict in summary:
     ```json
     "data_quality": {
       "EURUSD=X": {
         "candles": 8760,
         "expected": 8920,
         "gaps": 3,
         "volume_pct": 0.0,
         "ohlc_valid": true,
         "d1_data": true,
         "macro_data_indicators": 8
       }
     }
     ```
  3. Log warnings for issues but do not block backtest run.
- **Acceptance criteria:**
  - [ ] Data quality check runs before each backtest
  - [ ] Issues logged as warnings
  - [ ] Summary includes `data_quality` section
  - [ ] Known issues documented (forex volume = 0)
- **Dependencies:** None
- **Complexity:** M

---

### TASK-V7-18: Isolation mode backtest (REQ-BT-005)
- **Phase:** 3
- **Problem:** GC=F went from +$150 to -$7.69 when instrument universe changed (path dependence through correlation guard and capital allocation).
- **Goal:** Support isolation mode that runs each instrument independently.
- **Files to modify:**
  - `src/backtesting/backtest_params.py` -- add `isolation_mode: bool` field
  - `src/backtesting/backtest_engine.py` -- skip correlation guard and portfolio constraints in isolation mode
- **Implementation details:**
  1. Add `isolation_mode: bool = False` to BacktestParams.
  2. When `isolation_mode=True`:
     - Run each symbol in a separate loop (no shared state)
     - Disable correlation guard
     - Disable position cooldowns between instruments
     - Each symbol gets full capital allocation
  3. Summary includes both isolated and portfolio results when isolation_mode is run alongside normal mode.
  4. Flag instruments where PnL differs by >30% between modes as "path-dependent".
- **Acceptance criteria:**
  - [ ] Isolation mode produces per-instrument results independent of universe
  - [ ] GC=F results are identical regardless of which other instruments are in the universe
  - [ ] Path-dependence flag in summary
- **Dependencies:** None
- **Complexity:** M

---

### TASK-V7-19: Benchmark comparison (REQ-BT-008)
- **Phase:** 3
- **Problem:** No benchmark. Cannot tell if strategy adds value over random entry.
- **Goal:** Every backtest includes buy-and-hold and random entry benchmarks.
- **Files to modify:**
  - `src/backtesting/backtest_engine.py` -- add `_compute_benchmarks()` method
- **Implementation details:**
  1. **Buy-and-hold:** For each instrument, compute return from first candle open to last candle close.
  2. **Random entry benchmark:** Generate 1000 random entry points within the backtest period. For each entry, use the same SL/TP/position sizing rules as the strategy. Report median PF and 95th percentile PF.
  3. **Inverted signals:** Run same strategy but flip LONG<->SHORT. If inverted also profits, strategy may be capturing drift rather than direction.
  4. Add to summary under `"benchmarks"` key.
  5. Flag: strategy PF must exceed random 95th percentile to claim edge.
- **Acceptance criteria:**
  - [ ] Summary includes buy-and-hold return per instrument
  - [ ] Random entry benchmark with 1000 simulations
  - [ ] Inverted signal results
  - [ ] "Exceeds random" flag
- **Dependencies:** None
- **Complexity:** L

---

## Phase 4: Strategy Implementation

Priority: MEDIUM. Estimated total: 60-80 hours.

### TASK-V7-20: Strategy framework / pluggable strategy interface
- **Phase:** 4
- **Problem:** BacktestEngine uses a single strategy (composite score threshold). Need a way to plug in different strategies without rewriting the engine.
- **Goal:** Create a `BaseStrategy` interface and refactor backtest to use it.
- **Files to create:**
  - `src/backtesting/strategies/__init__.py`
  - `src/backtesting/strategies/base.py` -- BaseStrategy abstract class
  - `src/backtesting/strategies/composite_score.py` -- current strategy extracted
- **Files to modify:**
  - `src/backtesting/backtest_engine.py` -- accept strategy parameter
  - `src/backtesting/backtest_params.py` -- add `strategy: str` field
- **Implementation details:**
  1. Define `BaseStrategy` ABC:
     ```python
     from abc import ABC, abstractmethod

     class BaseStrategy(ABC):
         @abstractmethod
         def check_entry(self, context: dict) -> Optional[dict]:
             """Return entry signal dict or None.

             context: {
                 'df': pd.DataFrame (OHLCV history up to current candle),
                 'ta_indicators': dict,
                 'regime': str,
                 'symbol': str,
                 'market_type': str,
                 'timeframe': str,
                 'candle_ts': datetime,
                 'macro_data': dict,     # historical macro data
                 'fear_greed': float,    # F&G for the date
                 'geo_score': float,     # geo score for the date
                 'central_bank_rates': dict,
             }

             Returns: {
                 'direction': 'LONG'|'SHORT',
                 'entry_price': Decimal,
                 'sl_price': Decimal,
                 'tp_price': Decimal,
                 'composite_score': float,
             } or None
             """
             pass

         @abstractmethod
         def name(self) -> str:
             pass
     ```
  2. Extract current composite-score logic into `CompositeScoreStrategy(BaseStrategy)`.
  3. Modify `BacktestEngine.run()` to accept a strategy instance.
  4. Add `strategy: str = "composite"` to BacktestParams with a registry: `{"composite": CompositeScoreStrategy, "trend_rider": TrendRiderStrategy, ...}`.
- **Acceptance criteria:**
  - [ ] `BaseStrategy` ABC defined with `check_entry()` and `name()`
  - [ ] Current behavior extracted into `CompositeScoreStrategy`
  - [ ] Backtest produces identical results with CompositeScoreStrategy as before
  - [ ] New strategies can be added by implementing BaseStrategy
- **Dependencies:** None
- **Complexity:** L

---

### TASK-V7-21: Strategy 1 -- Trend Rider (D1)
- **Phase:** 4
- **Problem:** Pure trend-following on D1 is one of the most documented edges in finance. Current system uses composite score on H1 which is wrong for trend-following.
- **Goal:** Implement D1 trend-following strategy using ADX, SMA50/200, MACD.
- **Files to create:**
  - `src/backtesting/strategies/trend_rider.py`
- **Implementation details:**
  1. Implement `TrendRiderStrategy(BaseStrategy)`:
  2. **LONG entry:**
     - ADX(14) > 25
     - Close > SMA(200)
     - Close > SMA(50)
     - MACD histogram > 0 AND increasing
     - Price within 1.0 * ATR(14) of SMA(50) (pullback entry)
  3. **SHORT entry:** mirror conditions
  4. **Exit:**
     - SL: 2.0 * ATR(14) from entry
     - TP: 3.0 * ATR(14) from entry (R:R = 1.5:1)
     - Trailing: move SL to breakeven at 1.5 * ATR profit
     - Time exit: 15 D1 candles if not in profit
  5. **Target instruments:** EURUSD=X, AUDUSD=X, USDCAD=X, GC=F, BTC/USDT
  6. **Regime filter:** only STRONG_TREND_BULL and STRONG_TREND_BEAR
- **Acceptance criteria:**
  - [ ] Strategy generates signals on D1 data
  - [ ] Backtest runs on 2020-01 to 2025-12 (6 years)
  - [ ] Produces 100+ trades across all instruments
  - [ ] Entry conditions exactly match spec (ADX>25, SMA200, SMA50, MACD, pullback)
  - [ ] Unit test: known D1 data produces expected entry/exit
- **Dependencies:** TASK-V7-20
- **Complexity:** L

---

### TASK-V7-22: Strategy 2 -- Session Sniper (H1)
- **Phase:** 4
- **Problem:** London/NY session opens have documented institutional momentum. Current H1 strategy ignores session timing.
- **Goal:** Implement session-aware H1 strategy for forex.
- **Files to create:**
  - `src/backtesting/strategies/session_sniper.py`
- **Implementation details:**
  1. **LONG entry (London open, 07:00-09:00 UTC):**
     - H1 candle at 07:00 closes above previous H1 close
     - RSI(14) between 45 and 65
     - ATR(14) > 1.2 * ATR MA20 (above-average volatility)
     - NOT DXY RSI > 55 (existing filter)
     - Close above SMA(20) on H1
  2. **NY open (13:00-15:00 UTC):** same conditions, bonus if London direction confirms
  3. **Exit:**
     - SL: 1.5 * ATR(14)
     - TP1: 2.0 * ATR (close 50%), TP2: 3.0 * ATR (close 50%)
     - Time exit: 6 H1 candles
     - Hard close at 16:00 UTC
  4. **Target instruments:** EURUSD=X, GBPUSD=X, AUDUSD=X, USDCAD=X (forex only)
  5. **Weekday filter:** exclude Monday, Friday (existing)
- **Acceptance criteria:**
  - [ ] Strategy only generates signals during session windows
  - [ ] 40-80 trades/month expected
  - [ ] Backtest on 2023-01 to 2025-12
  - [ ] Unit test: verify session time filtering
- **Dependencies:** TASK-V7-20
- **Complexity:** L

---

### TASK-V7-23: Strategy 3 -- Crypto Extreme (D1)
- **Phase:** 4
- **Problem:** BTC exhibits strong mean-reversion from sentiment extremes. F&G < 25 with RSI < 30 marks bottoms.
- **Goal:** Implement contrarian F&G + RSI strategy for BTC.
- **Files to create:**
  - `src/backtesting/strategies/crypto_extreme.py`
- **Implementation details:**
  1. **LONG entry (buy fear):**
     - F&G <= 25 (extreme fear)
     - D1 RSI(14) <= 30
     - Higher low vs previous day's low (bullish reversal)
     - Funding rate < 0 (crowded short)
     - Confirmation: next D1 candle closes above open
  2. **SHORT entry (sell greed):**
     - F&G >= 75
     - D1 RSI(14) >= 70
     - Lower high vs previous day's high
     - Funding rate > 0.05%
     - Confirmation candle
  3. **Exit:**
     - SL: 3.5 * ATR(14)
     - TP: 5.0 * ATR(14) OR F&G returns to 50
     - Time exit: 14 D1 candles
  4. **Target instruments:** BTC/USDT (primary)
  5. **Requires:** Historical F&G data (TASK-V7-07), historical funding rates
- **Acceptance criteria:**
  - [ ] Strategy uses F&G index from macro_data
  - [ ] 4-8 trades per year expected
  - [ ] Backtest on 2020-01 to 2025-12
  - [ ] If N < 30, report as "insufficient data for conclusion"
- **Dependencies:** TASK-V7-07, TASK-V7-20
- **Complexity:** M

---

### TASK-V7-24: Strategy 4 -- Gold Macro (D1)
- **Phase:** 4
- **Problem:** Gold has a documented safe-haven relationship with VIX and DXY. GC=F showed promise in r4 backtest (+$150). Dedicated macro strategy should outperform composite.
- **Goal:** Implement VIX + DXY triggered gold strategy.
- **Files to create:**
  - `src/backtesting/strategies/gold_macro.py`
- **Implementation details:**
  1. **LONG entry (safe haven bid):**
     - Condition A (risk-off): VIX > 20 AND rising (2-day change > +2), DXY RSI < 50, GC=F > SMA(50)
     - OR Condition B (real rate decline): DXY < SMA(50), GC=F breaks 10-day high, ADX > 20
  2. **SHORT entry (risk-on):**
     - VIX < 15 AND declining
     - DXY RSI > 55
     - GC=F < SMA(50)
     - No HIGH-impact events in next 48h
  3. **Exit:**
     - SL: 2.5 * ATR(14)
     - TP: 3.5 * ATR(14)
     - Trailing: after 2.0 * ATR profit, trail at 1.5 * ATR
     - Time exit: 10 D1 candles
     - Hard exit: 24h before FOMC/NFP
  4. **Target instruments:** GC=F only
  5. **Requires:** VIX data from macro_data or yfinance, DXY data, economic calendar
- **Acceptance criteria:**
  - [ ] Strategy uses VIX and DXY data from DB/collectors
  - [ ] 2-3 trades/month expected
  - [ ] Backtest on 2020-01 to 2025-12
  - [ ] Hard exit before FOMC/NFP implemented using calendar data
- **Dependencies:** TASK-V7-20, TASK-V7-06 (VIX via FRED VIXCLS)
- **Complexity:** L

---

### TASK-V7-25: Strategy 5 -- Divergence Hunter (H4)
- **Phase:** 4
- **Problem:** RSI divergence with D1 trend filter is a high-probability setup. H4 provides good signal-to-noise ratio.
- **Goal:** Implement RSI divergence detection with D1 trend context.
- **Files to create:**
  - `src/backtesting/strategies/divergence_hunter.py`
- **Implementation details:**
  1. **Swing detection:** Identify swing highs/lows with minimum 3 H4 candles between swings.
  2. **LONG entry (bullish divergence):**
     - Price makes lower low on H4
     - RSI(14) makes higher low (divergence)
     - D1 close > SMA(200) (uptrend context)
     - Volume > MA(20) for stocks/crypto
     - Entry on next H4 candle open after RSI turns up
  3. **SHORT entry (bearish divergence):** mirror
  4. **Exit:**
     - SL: below swing low + 0.5 * ATR(14)
     - TP1: 1.5 * SL distance (close 50%)
     - TP2: 3.0 * SL distance (close 50%)
     - Time exit: 20 H4 candles (~3.3 days)
     - Move SL to breakeven after TP1
  5. **Target instruments:** All (EURUSD, AUDUSD, USDCAD, GC=F, BTC/USDT, SPY)
  6. **Implementation challenge:** Swing detection algorithm must be robust. Use `scipy.signal.argrelextrema` or manual implementation.
- **Acceptance criteria:**
  - [ ] Swing detection correctly identifies H4 swing highs/lows
  - [ ] Divergence detection compares price and RSI swing points
  - [ ] D1 trend filter applied (only trade divergences in direction of D1 trend)
  - [ ] 10-20 trades/month expected
  - [ ] Backtest on 2022-01 to 2025-12
  - [ ] Unit test: known divergence pattern produces correct entry signal
- **Dependencies:** TASK-V7-20
- **Complexity:** XL

---

## Phase 5: Backtest Validation and Comparative Analysis

Priority: MEDIUM. Estimated total: 16-24 hours.

### TASK-V7-26: Run walk-forward validation for each strategy
- **Phase:** 5
- **Problem:** Need to validate each strategy using walk-forward methodology before any live deployment.
- **Goal:** Run each strategy through walk-forward validation and document results.
- **Files to create:**
  - `scripts/run_strategy_backtests.py` -- automated script to run all strategies
  - `docs/BACKTEST_RESULTS_V7.md` -- results documentation
- **Implementation details:**
  1. For each strategy, run with `enable_walk_forward=True`:
     - Trend Rider: D1, 2020-2025, 5 folds
     - Session Sniper: H1, 2023-2025, 3 folds
     - Crypto Extreme: D1, 2020-2025, 5 folds
     - Gold Macro: D1, 2020-2025, 5 folds
     - Divergence Hunter: H4, 2022-2025, 4 folds
  2. Collect per-strategy results: WR, PF, Sharpe, max DD, trade count.
  3. Run statistical significance tests for each.
  4. Document in BACKTEST_RESULTS_V7.md.
- **Acceptance criteria:**
  - [ ] All 5 strategies backtested with walk-forward
  - [ ] Statistical significance tests for each
  - [ ] Results documented with per-fold breakdown
  - [ ] Clear pass/fail verdict per strategy
- **Dependencies:** TASK-V7-16, TASK-V7-21 through TASK-V7-25
- **Complexity:** L

---

### TASK-V7-27: Comparative analysis and hybrid portfolio
- **Phase:** 5
- **Problem:** Need to identify which instruments are best served by which strategy and test combined portfolio.
- **Goal:** Compare strategies and test hybrid portfolio allocation.
- **Files to create:**
  - `docs/STRATEGY_COMPARISON_V7.md`
- **Implementation details:**
  1. For each instrument, compare results across strategies that include it.
  2. Identify best strategy per instrument based on OOS PF and Sharpe.
  3. Build hybrid portfolio: assign each instrument to its best strategy.
  4. Run portfolio-level backtest with correlation guard and capital allocation.
  5. Compare hybrid to individual strategies.
- **Acceptance criteria:**
  - [ ] Per-instrument strategy comparison table
  - [ ] Hybrid portfolio backtest results
  - [ ] Clear recommendation: which strategy for which instrument
  - [ ] Results documented in STRATEGY_COMPARISON_V7.md
- **Dependencies:** TASK-V7-26
- **Complexity:** M

---

### TASK-V7-28: End-of-data sensitivity and transaction cost analysis (REQ-BT-004, REQ-BT-011)
- **Phase:** 5
- **Problem:** Choice of end date changes results. Transaction costs may flip marginal results.
- **Goal:** Add sensitivity analysis to backtest output.
- **Files to modify:**
  - `src/backtesting/backtest_engine.py` -- add sensitivity analysis to summary
- **Implementation details:**
  1. **End-of-data sensitivity:** After main backtest, re-compute metrics excluding last 1 week, 2 weeks, 1 month of trades. If PF changes >20%, flag as "period-sensitive".
  2. **Slippage sensitivity:** Compute PF at 0x, 1x, 2x, 3x slippage. Report the slippage level at which PF drops below 1.0.
  3. Add to summary under `"sensitivity"` key.
- **Acceptance criteria:**
  - [ ] End-of-data sensitivity with 3 end dates
  - [ ] Slippage sensitivity at 4 levels
  - [ ] "Fragile" flag if PF < 1.0 at 2x slippage
  - [ ] "Period-sensitive" flag if PF variance > 20%
- **Dependencies:** None
- **Complexity:** M

---

## Summary

| Phase | Tasks | Est. Hours | Priority |
|-------|-------|-----------|----------|
| 1: Data Wiring | TASK-V7-01..04 | 16-20h | CRITICAL |
| 2: Historical Backfill | TASK-V7-05..10 | 24-32h | HIGH |
| 3: Backtest Enhancement | TASK-V7-11..19 | 40-50h | HIGH |
| 4: Strategy Implementation | TASK-V7-20..25 | 60-80h | MEDIUM |
| 5: Validation | TASK-V7-26..28 | 16-24h | MEDIUM |
| **Total** | **28 tasks** | **156-206h** | |

### Critical Path
1. Phase 1 (wiring fixes) -- no dependencies, start immediately
2. Phase 2 (backfill) -- depends on Phase 1 for some tasks
3. Phase 3 (backtest enhancement) -- depends on Phase 2 for TASK-V7-11
4. Phase 4 (strategies) -- TASK-V7-20 has no dependencies, can start in parallel with Phase 3
5. Phase 5 (validation) -- depends on Phases 3 and 4

### Recommended Execution Order
Start Phase 1 and TASK-V7-20 (strategy framework) in parallel. Once Phase 1 complete, start Phase 2. Phase 3 can begin with tasks that have no Phase 2 dependencies (TASK-V7-12, V7-13, V7-14, V7-15). Strategy implementations (Phase 4) can proceed once framework is ready. Phase 5 is last.
