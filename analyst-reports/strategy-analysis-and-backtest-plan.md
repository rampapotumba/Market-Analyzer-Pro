# Analysis Report: Strategy Proposals and Backtest Plan
## Date: 2026-03-20
## Analyst: market-analyst agent

---

## Executive Summary

The current system uses a single monolithic strategy: weighted composite score (TA 45%, FA 25%,
Sentiment 20%, Geo 10%) on H1 timeframe across all asset classes. Six rounds of backtesting have
failed to produce a statistically reliable positive edge -- PF ranges from 0.60 to 2.01 depending
on the time window, instrument universe, and filter configuration. The best result (PF 2.01) is
based on 33 trades over 24 months, which is statistically meaningless.

The fundamental problem is not the architecture. The fundamental problem is that one strategy is
being applied uniformly to forex, crypto, commodities, and stocks on a single timeframe, while
averaging four very different signal sources into one composite number. This averaging dilutes
strong TA signals with noise from FA/sentiment/geo components that may or may not be relevant
for a given instrument and timeframe.

This report proposes 5 concrete strategies that can be tested with the existing infrastructure.
Each exploits a specific market microstructure inefficiency rather than hoping that averaging
everything together produces an edge.

---

## Part 1: Current System Diagnosis

### Why the Composite Score Approach Struggles

1. **Averaging destroys signal**: A strong TA signal (ta_score=+40) gets diluted to
   composite = 0.45*40 + 0.25*0 + 0.20*0 + 0.10*0 = 18.0 when FA/sentiment/geo are neutral.
   An 18.0 barely passes the 15.0 threshold. The TA conviction is high but the composite
   barely qualifies.

2. **Same logic for different markets**: Forex (EURUSD) and crypto (BTC) have fundamentally
   different drivers, volatility regimes, and liquidity profiles. Applying identical weights
   and thresholds makes no market-microstructure sense.

3. **H1 is wrong for most strategies**: Trend-following on H1 produces ~1.4 trades/month
   with current filters. The signal-to-noise ratio on H1 is poor for trend-following
   (too many regime changes) and for mean-reversion (too slow for rotations).

4. **Backtest is TA-only**: In backtest mode, FA=0, sentiment=0, geo=0. The composite
   score is effectively `0.45 * ta_score`, with thresholds scaled by `available_weight_floor=0.65`.
   This means the backtest tests a DIFFERENT strategy than what runs live (live has 4 components,
   backtest has 1). All backtest results are for the TA-only sub-strategy, not the full system.

5. **No directional bias by asset class**: SHORT is effectively disabled by filters (RSI<30,
   score multiplier 1.3x, bear regimes blocked). This is appropriate for stocks (equity premium)
   but wrong for forex (no intrinsic bias) and crypto (deep bear markets exist).

### What the Data Tells Us

From the 6 backtest rounds:

| Finding | Evidence | Implication |
|---------|----------|-------------|
| LONG works, SHORT does not | WR LONG 57% vs SHORT 37% (v5 P2) | SHORT filters are too aggressive OR short setups are genuinely bad on H1 |
| AUDUSD is consistently profitable | +$101 v5, +$23 r5 | Some instruments suit the strategy better |
| SPY is consistently unprofitable | -$114 v5, +$43 r5 (1 trade) | Stock index on H1 with TA-only is noise |
| Wednesday is the best day | +$319 v5, consistently | Institutional flow patterns may be detectable |
| Monday and Friday are losers | -$37/-$61 v5, -$229/-$88 P1 | Gap risk and position squaring are real |
| GC=F results are unstable | +$150 r4, -$7 r5 | Path-dependent, possibly noise |
| ETH is a consistent loser | Blocked after 6 rounds of losses | The strategy has no edge in ETH |

---

## Part 2: Strategy Proposals

### Strategy 1: "Trend Rider" -- D1 Trend Following with TA Only

**Description**: Pure trend-following strategy on D1 timeframe using only technical analysis.
Eliminates the composite score averaging problem by using TA directly. Targets strong trending
markets where directional persistence is highest.

**Data sources used**:
- Price data: D1 candles
- TA indicators: ADX(14), SMA(50), SMA(200), ATR(14), MACD(12,26,9)
- Regime detector: for entry timing

**Entry logic**:
```
LONG entry:
  - ADX(14) > 25 (confirmed trend)
  - Close > SMA(200) (macro uptrend)
  - Close > SMA(50) (intermediate uptrend)
  - MACD histogram > 0 AND increasing (momentum)
  - Price pulls back to within 1.0 * ATR(14) of SMA(50) (entry on pullback, not breakout)

SHORT entry:
  - Mirror conditions (ADX>25, Close < SMA200, Close < SMA50, MACD hist < 0 decreasing)
  - Price rallies to within 1.0 * ATR(14) of SMA(50)
```

**Exit logic**:
- SL: 2.0 * ATR(14) below entry (LONG) / above entry (SHORT)
- TP: 3.0 * ATR(14) -- R:R target of 1.5:1
- Trailing stop: Move SL to breakeven when price reaches 1.5 * ATR in profit
- Time exit: 15 D1 candles (3 weeks) if not in profit

**Target instruments**: EURUSD, AUDUSD, USDCAD, GC=F, BTC/USDT
(instruments that showed any profitability + gold as trend asset)

**Expected characteristics**:
- Trade frequency: 3-5 per instrument per year, ~20-30 total/year
- Holding period: 3-15 trading days
- Expected WR: 35-42% (trend-following typically has low WR, high R:R)
- Target R:R: 1.5:1 to 2.5:1

**Backtest parameters**:
- Period: 2020-01 to 2025-12 (6 years for statistical significance with D1)
- Timeframe: D1
- Slippage: 2x standard (conservative)
- No composite score -- use raw TA conditions
- Regime filter: only STRONG_TREND_BULL and STRONG_TREND_BEAR

**Why this might work**:
D1 trend following is one of the most documented edges in financial markets (Moskowitz, Ooi, Pedersen
2012 -- "Time Series Momentum"). Price trends on D1 persist due to institutional position building
(takes days to accumulate), herding behavior, and anchoring bias. The pullback entry reduces
adverse excursion compared to breakout entry. ADX+SMA200 combination is widely validated.

**Risks and failure modes**:
- Choppy markets (2015-2016 EUR range) produce many false signals
- Low trade frequency means long evaluation period needed
- Pullback entry may miss strong breakout moves
- D1 data from yfinance may have quality issues (adjusted close, gaps)

---

### Strategy 2: "Session Sniper" -- London/NY Open Momentum on H1

**Description**: Captures the London and New York session open momentum that occurs as
institutional traders begin their day. Trades only during high-liquidity windows on forex pairs.

**Data sources used**:
- Price data: H1 candles
- TA indicators: ATR(14), RSI(14), volume (if available from alternative source)
- DXY RSI (existing collector)
- Session timing

**Entry logic**:
```
LONG entry (London open):
  - Time: 07:00-09:00 UTC (London open window)
  - H1 candle at 07:00 closes above previous H1 close
  - RSI(14) between 45 and 65 (not overbought, but with upward momentum)
  - ATR(14) > 1.2 * ATR(14, 20-period MA) (above-average volatility -- session starting)
  - NOT DXY RSI > 55 for USD-quote pairs (existing filter)
  - Close above H4 VWAP equivalent (SMA20 of H1 as proxy)

SHORT entry:
  - Mirror conditions with RSI 35-55, close below previous

LONG entry (NY open):
  - Time: 13:00-15:00 UTC
  - Same conditions as London open
  - Additional: London session direction confirms (if London moved up, NY LONG is stronger)
```

**Exit logic**:
- SL: 1.5 * ATR(14) -- tighter than trend-following because we expect quick resolution
- TP1: 2.0 * ATR(14) -- close 50% (R:R = 1.33)
- TP2: 3.0 * ATR(14) -- close remaining 50% (R:R = 2.0)
- Time exit: 6 H1 candles (6 hours) -- session momentum should resolve by then
- Hard close at 16:00 UTC (before Asian session low liquidity)

**Target instruments**: EURUSD, GBPUSD, AUDUSD, USDCAD (forex only)

**Expected characteristics**:
- Trade frequency: 2-4 per instrument per week, ~40-80 total/month
- Holding period: 2-8 hours
- Expected WR: 48-55% (momentum has decent WR on session opens)
- Target R:R: 1.3:1 to 2.0:1

**Backtest parameters**:
- Period: 2023-01 to 2025-12 (3 years, H1 data availability)
- Timeframe: H1
- Slippage: 1x standard
- No composite score -- use session + momentum conditions
- Weekday filter: exclude Monday, Friday (existing)

**Why this might work**:
London and New York opens are the highest-volume, highest-volatility periods in forex.
Institutional order flow creates momentum that persists for 2-4 hours. This is well-documented
in market microstructure research (Evans and Lyons 2002 -- "Order Flow and Exchange Rate Dynamics").
The strategy captures the "continuation of opening move" pattern that exists because large
institutions cannot execute their orders instantly.

**Risks and failure modes**:
- False breakouts during news events (calendar filter helps)
- Low volatility regimes (2019 EUR range) reduce opportunity
- Slippage at session open may be higher than modeled
- Requires precise H1 candle timing (candle must align with exact session open)

---

### Strategy 3: "Crypto Extreme" -- Fear & Greed Mean-Reversion on D1

**Description**: Contrarian strategy for BTC (and potentially ETH) that buys extreme fear and
sells extreme greed. Uses existing Fear & Greed collector and D1 timeframe to capture
multi-day reversions from sentiment extremes.

**Data sources used**:
- Fear & Greed Index (existing collector: api.alternative.me)
- Price data: D1 candles
- TA indicators: RSI(14) on D1, ATR(14)
- Funding rate (existing: Binance)
- BTC dominance (can be derived from existing data)

**Entry logic**:
```
LONG entry (buy fear):
  - Fear & Greed <= 25 (extreme fear)
  - D1 RSI(14) <= 30 (oversold)
  - D1 close forms a bullish reversal pattern (higher low compared to previous day's low)
  - Funding rate < 0 (shorts are paying -- crowded short)
  - Wait for 1 D1 candle confirmation (close above open after signal day)

SHORT entry (sell greed):
  - Fear & Greed >= 75 (extreme greed)
  - D1 RSI(14) >= 70 (overbought)
  - D1 close forms a bearish reversal pattern (lower high)
  - Funding rate > 0.05% (longs are paying -- crowded long)
  - Wait for 1 D1 candle confirmation
```

**Exit logic**:
- SL: 3.5 * ATR(14) -- wide stop for crypto volatility (matches existing BTC override)
- TP: 5.0 * ATR(14) -- R:R target of 1.43:1
- Alternative TP: Fear & Greed returns to 50 (neutral zone)
- Time exit: 14 D1 candles (2 weeks)

**Target instruments**: BTC/USDT (primary), ETH/USDT (if unblocked, test separately)

**Expected characteristics**:
- Trade frequency: 4-8 per year (extreme conditions are rare)
- Holding period: 3-10 days
- Expected WR: 55-65% (mean-reversion from extremes has high WR)
- Target R:R: 1.0:1 to 1.5:1

**Backtest parameters**:
- Period: 2020-01 to 2025-12 (6 years, captures 2 bull/bear cycles)
- Timeframe: D1
- Slippage: 2x standard crypto (0.2%)
- Need to backfill Fear & Greed history (API provides historical data)
- Need to backfill funding rate history

**Why this might work**:
Crypto markets are dominated by retail participants who exhibit extreme herding behavior.
Fear & Greed Index is a reliable measure of this herding. Academic research (Klement 2013) and
industry data (Santiment, Glassnode) consistently show that extreme fear in crypto marks
local bottoms and extreme greed marks local tops. The funding rate adds a crowding indicator --
when one side is extremely crowded (paying high funding), the reversal is more likely and more violent.

**Risks and failure modes**:
- Extreme conditions can persist (crypto winter 2022 -- fear stayed low for months)
- Confirmation candle may miss the exact bottom/top
- Very low trade frequency makes statistical validation difficult
- Relies heavily on Fear & Greed API reliability and data quality
- ETH correlation with BTC means these are not independent trades

---

### Strategy 4: "Gold Macro" -- Gold as Safe Haven with Macro Triggers on D1

**Description**: Trades gold (GC=F) based on macro conditions that historically drive safe-haven
flows. Uses existing DXY collector, regime detection, and economic calendar.

**Data sources used**:
- Price data: D1 candles for GC=F
- DXY index and RSI (existing collector)
- Economic calendar (existing: 453 events)
- VIX level (existing in macro_data)
- TA indicators: SMA(50), SMA(200), ATR(14)

**Entry logic**:
```
LONG Gold entry (safe haven bid):
  - Condition set A (risk-off):
    - VIX > 20 AND VIX rising (2-day change > +2 points)
    - DXY RSI(14) < 50 (USD weakening)
    - GC=F close > SMA(50) on D1 (gold in uptrend)
  - OR Condition set B (real rate decline):
    - DXY in downtrend (close < SMA50)
    - GC=F breaks above 10-day high
    - ADX(14) > 20 (trend emerging)

SHORT Gold entry (risk-on):
  - VIX < 15 AND declining
  - DXY RSI(14) > 55 (USD strengthening)
  - GC=F close < SMA(50) on D1
  - High-impact economic events are NOT in next 48 hours
```

**Exit logic**:
- SL: 2.5 * ATR(14)
- TP: 3.5 * ATR(14) -- R:R = 1.4:1
- Trailing stop: After 2.0 * ATR profit, trail at 1.5 * ATR
- Time exit: 10 D1 candles (2 weeks)
- Hard exit: 24h before FOMC/NFP (gold is extremely sensitive to these)

**Target instruments**: GC=F only

**Expected characteristics**:
- Trade frequency: 2-3 per month
- Holding period: 3-10 trading days
- Expected WR: 42-50%
- Target R:R: 1.4:1 to 2.0:1

**Backtest parameters**:
- Period: 2020-01 to 2025-12 (6 years, includes COVID crash, rate hike cycle, 2024-25 gold rally)
- Timeframe: D1
- Slippage: 2x standard
- Must verify VIX and DXY data availability in macro_data table
- Calendar filter: use only FOMC, NFP, CPI events (highest impact on gold)

**Why this might work**:
Gold's safe-haven role is one of the most robust relationships in finance. When VIX rises and
USD weakens, institutional money flows into gold. This relationship has held across decades.
The DXY-gold inverse correlation is -0.4 to -0.6 historically. By waiting for both VIX spike
AND DXY weakness, we filter for high-conviction setups. The r4 backtest showed GC=F was the
top performer (+$150, 40 trades) even with the composite score approach -- a dedicated macro
strategy should perform better.

**Risks and failure modes**:
- Correlation breakdown (2022 Q1: gold and DXY both rose during Ukraine crisis)
- VIX data from yfinance may have delays
- Gold is sensitive to real rates (TIPS yields), which are not currently collected
- FOMC decisions can cause violent reversals regardless of position
- The GC=F collapse in r5 suggests instrument-level instability

---

### Strategy 5: "Divergence Hunter" -- RSI Divergence with Volume on H4

**Description**: Identifies hidden RSI divergences on H4 timeframe -- when price makes a new
high/low but RSI does not confirm, signaling exhaustion. Combines with volume analysis for
confirmation in non-forex assets.

**Data sources used**:
- Price data: H4 candles
- TA indicators: RSI(14), ATR(14), volume
- D1 trend context: SMA(200) for directional bias

**Entry logic**:
```
LONG entry (bullish divergence):
  - Price makes a lower low on H4 (swing low < previous swing low)
  - RSI(14) makes a higher low (bullish divergence)
  - D1 close > SMA(200) (trade divergences only in direction of major trend)
  - Volume on divergence candle > MA(20) volume (for stocks, crypto -- skip for forex)
  - Entry on next H4 candle open after divergence confirms (RSI turns up)

SHORT entry (bearish divergence):
  - Price makes a higher high, RSI makes a lower high
  - D1 close < SMA(200)
  - Volume confirmation
  - Entry on next H4 candle open
```

**Exit logic**:
- SL: Below the swing low that formed the divergence + 0.5 * ATR(14) buffer
- TP1: 1.5 * SL distance (close 50%)
- TP2: 3.0 * SL distance (close remaining 50%)
- Time exit: 20 H4 candles (~3.3 days) if not at TP1
- Move SL to breakeven after TP1 hit

**Target instruments**: All instruments (EURUSD, AUDUSD, USDCAD, GC=F, BTC/USDT, SPY)

**Expected characteristics**:
- Trade frequency: 1-3 per instrument per month, ~10-20 total/month
- Holding period: 8-48 hours (2-12 H4 candles)
- Expected WR: 50-58% (divergences with trend have good WR)
- Target R:R: 1.5:1 to 3.0:1

**Backtest parameters**:
- Period: 2022-01 to 2025-12 (4 years, H4 data)
- Timeframe: H4
- Slippage: 1x standard
- Swing detection: minimum 3 H4 candles between swings
- D1 data must be loaded for SMA(200) filter

**Why this might work**:
RSI divergence is one of the highest-probability reversal setups in technical analysis.
The key innovation here is filtering divergences by the D1 trend direction -- we only take
bullish divergences in D1 uptrends and bearish divergences in D1 downtrends. This converts
a counter-trend setup into a "buy the dip in an uptrend" setup, which is statistically
superior. H4 provides a good balance between signal quality (less noise than H1) and
frequency (more signals than D1).

**Risks and failure modes**:
- Divergences can persist (RSI can stay divergent for many candles before resolving)
- Swing detection algorithm needs careful calibration
- Requires enough H4 history for reliable RSI and swing detection
- Multiple divergences in sequence (price keeps making lower lows) can cause multiple losses

---

## Part 3: Backtest Plan

### Priority Order

| # | Strategy | Priority | Rationale |
|---|----------|----------|-----------|
| 1 | Strategy 1: Trend Rider (D1) | Highest | Most academically supported, uses existing indicators, tests D1 timeframe for first time |
| 2 | Strategy 4: Gold Macro (D1) | High | GC=F showed promise in r4, uses existing DXY/VIX collectors, gold is well-understood |
| 3 | Strategy 5: Divergence Hunter (H4) | High | Tests H4 timeframe, divergence is implementable with existing TAEngine, broad instrument applicability |
| 4 | Strategy 2: Session Sniper (H1) | Medium | Uses existing H1 infrastructure, but needs precise session timing and higher trade frequency |
| 5 | Strategy 3: Crypto Extreme (D1) | Lower | Low trade frequency (4-8/year) makes statistical validation very difficult, needs F&G history backfill |

### Implementation Plan

#### Phase A: Infrastructure Prerequisites (1-2 days)
Before running any strategy backtest:
1. Fix regime detection in backtest (REQ-BT-009) -- regime must not be UNKNOWN
2. Verify D1 data availability for all instruments
3. Implement confidence interval calculation (REQ-BT-001)
4. Add filter activation stats (REQ-BT-003)
5. Ensure backtest can run on D1 and H4 timeframes (not just H1)

#### Phase B: Strategy 1 -- Trend Rider (3-5 days)
1. Implement D1 trend-following entry logic as a new strategy module
   (separate from composite score -- can be a new method in BacktestEngine or a pluggable
   strategy class)
2. Run on 6 years of D1 data: 2020-01 to 2025-12
3. Target: 100+ trades across all instruments
4. Run walk-forward: train 2020-2022, validate 2023; train 2020-2023, validate 2024; etc.
5. Compute statistical significance (t-test, bootstrap PF CI)
6. Compare to buy-and-hold benchmark

**Success criteria**:
- Walk-forward aggregate OOS PF > 1.2
- Bootstrap 5th percentile PF > 1.0
- t-test p-value < 0.05
- Max DD < 20%

**Expected timeline for results**: 1 day for implementation, 1 day for backtest runs and analysis

#### Phase C: Strategy 4 -- Gold Macro (2-3 days)
1. Implement VIX + DXY condition logic for GC=F only
2. Verify VIX and DXY data availability in macro_data/price_data tables
3. Run on 6 years of D1 data
4. Walk-forward with 3 folds
5. Compare to Strategy 1 results for GC=F specifically

**Success criteria**: Same as Phase B, plus GC=F results must be stable (low path dependence
since it runs in isolation)

#### Phase D: Strategy 5 -- Divergence Hunter (3-5 days)
1. Implement swing detection and RSI divergence identification
2. This is the most complex implementation -- needs careful swing point logic
3. Run on 4 years of H4 data: 2022-01 to 2025-12
4. Walk-forward with 4 folds
5. Compare across instruments: which instruments produce divergence edge?

**Success criteria**: Same statistical thresholds as Phase B

#### Phase E: Strategy 2 -- Session Sniper (2-3 days)
1. Implement session-aware entry logic
2. Run on 3 years of H1 data
3. Higher trade frequency expected -- should reach 200+ trades easily
4. Walk-forward with 5 folds (more data allows more folds)

**Success criteria**: Same thresholds, plus trades/month > 10

#### Phase F: Strategy 3 -- Crypto Extreme (2-3 days)
1. Backfill Fear & Greed history (api.alternative.me provides historical data)
2. Backfill funding rate history from Binance
3. Run on 6 years BTC D1 data
4. Low trade count expected -- may not reach statistical significance
5. If N < 50, combine with Monte Carlo simulation for confidence

**Success criteria**: Due to low N, relax to bootstrap 5th percentile PF > 0.8 and qualitative
assessment of trades. If N < 30, report as "insufficient data for conclusion."

### Phase G: Comparative Analysis and Hybrid Strategy (2-3 days)
After all individual strategies are backtested:
1. Compare risk-adjusted returns (Sharpe, Sortino) across all 5 strategies
2. Identify which instruments are best served by which strategy
3. Test a hybrid portfolio: assign each instrument to its best strategy
4. Run portfolio-level backtest with correlation guard and capital allocation
5. This is the final "can we run multiple strategies together" validation

---

## Appendix: Key Metrics Thresholds

For any strategy to be considered viable for live trading:

| Metric | Minimum Threshold | Ideal Target |
|--------|-------------------|--------------|
| Walk-forward OOS Profit Factor | > 1.2 | > 1.5 |
| Bootstrap 5th percentile PF | > 1.0 | > 1.2 |
| Win Rate | > 30% (if R:R > 2) or > 45% (if R:R < 1.5) | > 50% |
| Max Drawdown | < 25% | < 15% |
| Sharpe Ratio (annualized) | > 0.5 | > 1.0 |
| t-test p-value on returns | < 0.05 | < 0.01 |
| Minimum total trades (OOS) | > 50 | > 100 |
| Trades per month | > 3 | > 10 |
| Slippage 2x PF | > 1.0 | > 1.2 |
| Max consecutive losses | < 10 | < 7 |

If a strategy fails to meet minimum thresholds across all walk-forward folds,
it should be abandoned, not "fixed" with more filters. More filters on a strategy
without edge is overfitting, not improvement.

---

## Appendix: Comparison to Current Approach

| Aspect | Current (Composite Score H1) | Proposed Strategies |
|--------|------------------------------|---------------------|
| Signal source | Average of TA+FA+Sentiment+Geo | Strategy-specific conditions |
| Timeframe | H1 only | D1, H4, H1 depending on strategy |
| Instruments | Same logic for all | Strategy-instrument matching |
| Entry trigger | Composite score > threshold | Specific market conditions |
| Backtest scope | 1 window, no OOS | Walk-forward validation |
| Trade frequency | 1.4/month (too low) | 3-80/month depending on strategy |
| SHORT handling | Effectively disabled | Strategy-dependent (enabled for trend-following, disabled for Session Sniper) |
| Statistical validation | None | t-test, bootstrap CI, Sharpe |
| Edge thesis | "Everything combined is better" | Specific microstructure argument per strategy |

The current approach can continue to run in parallel. These new strategies are additive --
they do not require removing the existing system. If a new strategy validates, it can
replace the composite score for the relevant instrument-timeframe combination.
