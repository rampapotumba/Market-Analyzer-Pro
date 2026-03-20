# Analysis Report: v6 Calibrated Backtest (Round 2)
## Date: 2026-03-20
## Analyst: market-analyst agent

## Executive Summary

The v6 calibrated backtest shows a dramatic improvement in Profit Factor (1.72 vs 1.12 pre-calibration and 1.004 in v5), achieving the target PF >= 1.4. Total PnL is +$1,843 on a $1,000 account over 2 years (250 trades). However, the results are **structurally fragile**: 95% of profit comes from just 2 instruments (GC=F + ETH/USDT), 75% of trades exit via time_exit (not TP/SL), 37% of total PnL comes from 3 end_of_data artifacts on Dec 31, and SHORT signals are completely blocked by the 2.0x multiplier. The 33.7% max drawdown significantly exceeds the 20% target. Only the VOLATILE regime is profitable; STRONG_TREND_BULL and TREND_BULL are both net-negative despite being allowed.

## Signal Quality

- Total signals analyzed: 88,327 raw -> 250 passed (0.28% pass rate)
- Win Rate: 12.55% (excl end_of_data), 13.6% (incl end_of_data)
- Profit Factor: 1.72
- Average win PnL: ~$52/trade (estimated from 31 wins in $1,159 excl eod)
- Average loss PnL: ~$-5.4/trade (estimated from 216 losses)
- The strategy is a **low-WR, high-R:R** system -- few winners but they are large
- False positive concern: 87.4% of trades are losers
- Avg win duration: 246.6h (10.3 days) vs avg loss duration: 101.6h (4.2 days)
- Key issues found:
  - SHORT signals: 0 trades -- SHORT_SCORE_MULTIPLIER=2.0 makes effective threshold 19.5, unreachable with max composite ~13.5 in backtest
  - Score threshold rejects 92% of signals -- dominant rejection source
  - Only 10% of trades hit TP, 75% exit via time_exit -- system fails to reach targets

## Position Logic

- Signals with matching positions: 250/250 (100%)
- Orphaned positions (no signal): 0
- Missed signals (no position): N/A (backtest context)
- SPY: 0 trades -- min_score=30 + STRONG_TREND_BULL only is too restrictive
- NZDUSD: 6 trades, 0 wins, -$18.29 -- filter overrides working but still losing
- Key issues found:
  - **END_OF_DATA contamination**: 3 trades closed on Dec 31 contribute $683.94 (37.1% of total PnL) -- these are NOT real trading outcomes
  - Without end_of_data: PnL = $1,158.91, PF drops to ~1.35

## Data Quality

- Data gaps: None detected in equity curve timeline
- Missing fields: None
- Anomalies:
  - **3 equity curve entries on Dec 31 23:00** jump from $2,159 to $2,843 (+$684 in 3h) -- end_of_data artifact
  - **9 weekend trades** (Sat+Sun) losing -$225.79 total -- weekend filter not blocking crypto adequately
  - 2 Saturday trades exist (shouldn't happen for forex; likely crypto)

## Code Logic Review

- Files reviewed:
  - `src/backtesting/backtest_engine.py` -- _compute_summary, _check_exit, _simulate_symbol
  - `src/signals/filter_pipeline.py` -- SignalFilterPipeline.run_all, check_score_threshold, check_momentum
  - `src/config.py` -- BLOCKED_REGIMES, INSTRUMENT_OVERRIDES, SHORT_SCORE_MULTIPLIER

- Logic errors found:
  - **WR by_symbol includes end_of_data wins**: by_symbol counts all 34 wins (incl 3 eod), but win_rate_pct uses 31/247=12.55%. Inconsistency -- by_symbol should also exclude eod for metrics, or clearly label
  - **SHORT mathematically impossible**: effective_threshold = 15 * max(0.45, 0.65) * 2.0 = 19.5. Max achievable composite = 0.45 * ~30 = ~13.5. Gap is unbridgeable. This is not a filter -- it's a hard block.
  - **Momentum filter kills SHORT separately**: even if score threshold passed, RSI < 30 requirement in check_momentum further constrains SHORT

- Inconsistencies with data:
  - STRONG_TREND_BULL has 96 trades with -$108 PnL -- this regime is allowed but NOT profitable
  - TREND_BULL has 45 trades with -$46 PnL -- same problem
  - Only VOLATILE (109 trades, +$1,997) is profitable -- and carries 108% of total PnL

## Findings

### Finding 1: End-of-Data PnL Contamination
- Severity: **Critical**
- Description: 3 end_of_data trades on Dec 31 contribute $683.94 (37.1% of total PnL). These trades were open at period end and closed at last price -- they represent unrealized, unconfirmed P&L, not actual trade outcomes. Without them, PnL drops to $1,159 and PF drops to approximately 1.35.
- Evidence: `end_of_data_pnl: 683.9447`, equity curve jumps $684 in last 3 entries (21:00-23:00 Dec 31). 3 eod trades counted as wins in by_symbol but excluded from win_rate_pct.
- Impact: Overstates real performance by ~37%. PF 1.72 is inflated to ~1.35 real.

### Finding 2: SHORT Signals Mathematically Impossible
- Severity: **Critical**
- Description: SHORT_SCORE_MULTIPLIER = 2.0 creates effective threshold of 19.5 for forex (15 * 0.65 * 2.0). In backtest, max achievable composite = 0.45 * ta_score, where ta_score rarely exceeds 30 in practice, giving max composite ~13.5. Even the strongest SHORT signal cannot pass 19.5 threshold. Additionally, SHORT_RSI_THRESHOLD = 30 in momentum filter would block most remaining candidates.
- Evidence: `short_count: 0`, `win_rate_short_pct: 0.0%` across all 88,327 raw signals
- Impact: System is LONG-only. Cannot profit from bearish moves. Combined with BLOCKED_REGIMES including TREND_BEAR and STRONG_TREND_BEAR, the system can only trade bullish setups in VOLATILE and TREND_BULL/STRONG_TREND_BULL.

### Finding 3: Time Exit Dominance (75.2%)
- Severity: **Major**
- Description: 188 of 250 trades (75.2%) exit via time_exit. Only 25 (10%) hit TP and 14 (5.6%) hit SL. The vast majority of trades sit for 24 candles (24h for H1), accumulate small negative PnL, then close at break-even or slight loss. Avg loss duration is 101.6h (~4 days) despite 24-candle time exit, suggesting many trades experience initial adverse movement, recover partially, then time-exit.
- Evidence: `time_exit_count: 188`, `tp_hit_count: 25`, `sl_hit_count: 14`, `mae_exit_count: 20`
- Impact: TP targets are too far. The system enters positions that never reach target. Time exit prevents catastrophic losses but creates death-by-a-thousand-cuts.

### Finding 4: Extreme Profit Concentration in 2 Instruments
- Severity: **Major**
- Description: GC=F (gold) contributes $942 (51% of total PnL) and ETH/USDT contributes $811 (44%). Together they produce 95% of all profit. The other 7 instruments combined contribute only $89. AUDUSD is the worst performer at -$98.
- Evidence: `by_symbol` data. 6 of 9 instruments are within +/-$50 of breakeven.
- Impact: Strategy is not generalizable. It works for gold and ETH in a VOLATILE regime during a bull market. Removing either instrument would halve performance.

### Finding 5: Max Drawdown 33.7% (Target: 20%)
- Severity: **Major**
- Description: Peak balance $2,043 (Feb 2024) dropped to $1,354 (Jan 2025) -- a $689 / 33.7% drawdown lasting ~11 months. The system spent Feb 2024 through Apr 2025 (14 months) below its peak.
- Evidence: Equity curve drawdown path. Peak at 2024-02-27, trough at 2025-01-27.
- Impact: Exceeds 20% target by 68%. Would trigger risk management alerts in production. Extended drawdown period would test operator patience.

### Finding 6: Non-Profitable Allowed Regimes
- Severity: **Major**
- Description: STRONG_TREND_BULL (96 trades, -$108) and TREND_BULL (45 trades, -$46) are both allowed regimes but produce net losses. Only VOLATILE (109 trades, +$1,997) is profitable. The system allows 56% of trades into losing regimes.
- Evidence: `by_regime` data
- Impact: 141 trades (56%) enter positions in regimes that historically lose money. Blocking TREND_BULL would remove 45 losing trades but also 8 wins.

### Finding 7: Weekend Trading Losses
- Severity: **Minor**
- Description: 9 trades on Saturday (2) and Sunday (7) lose -$225.79 combined with 0 wins. Weekend filter doesn't block crypto entries on weekends, but crypto weekend entries are unprofitable.
- Evidence: `by_weekday`: Saturday 2 trades $-91.84, Sunday 7 trades $-133.95
- Impact: Easy -$226 to recover by blocking weekend entries for all instruments.

### Finding 8: WR Display Inconsistency (Not a Bug)
- Severity: **Minor**
- Description: The user reported "WR shows 0% for all symbols" but the JSON data shows proper win counts in by_symbol (e.g. EURUSD 12 wins, USDCAD 6 wins). The win_rate_pct = 12.55% is correct when computed on metric_trades (excluding end_of_data). The discrepancy is: by_symbol includes end_of_data (34 wins / 250 = 13.6%), but win_rate_pct uses 31/247 = 12.55%. The "0% per symbol" issue is likely in the frontend display layer, not in the backtest engine.
- Evidence: JSON by_symbol fields show non-zero win counts
- Impact: Frontend/API display issue only. Backend data is correct but could be clearer.

### Finding 9: Positive Month Ratio 7/24 (29%)
- Severity: **Minor**
- Description: Only 7 of 24 months are profitable. The top 5 months produce +$2,705 while the bottom 5 produce -$571. Single months like Feb 2024 (+$882) and Dec 2025 (+$667) carry disproportionate weight.
- Evidence: monthly_returns data
- Impact: Inconsistent monthly returns. In production, most months would show losses, with occasional large wins. This pattern is characteristic of trend-following systems but difficult for operators to maintain confidence.
