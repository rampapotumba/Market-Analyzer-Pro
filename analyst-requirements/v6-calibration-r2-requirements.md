# Requirements: v6 Calibration Round 2
## Date: 2026-03-20
## From: market-analyst
## To: architect

## Background

Analysis of the v6 calibrated backtest (250 trades, PF 1.72, PnL +$1,843, DD 33.7%) revealed several structural problems that inflate reported performance and limit the system's robustness. The primary concerns are: (1) end-of-data trades contribute 37% of PnL, (2) SHORT is mathematically impossible, (3) 75% of trades exit via time_exit never reaching TP, (4) 95% of profit comes from 2 instruments, (5) drawdown exceeds 20% target, and (6) two allowed regimes (STRONG_TREND_BULL, TREND_BULL) are net-negative.

Full analysis: `analyst-reports/v6-calibrated-r2-analysis.md`

## Requirements

### REQ-CAL2-001: Exclude end_of_data PnL from headline metrics
- Priority: **High**
- Problem: 3 end_of_data trades contribute $683.94 (37% of total PnL), inflating PF from ~1.35 to 1.72. These are unrealized positions closed at arbitrary end-of-period price, not actual trade outcomes.
- Required behavior: (a) PF and total_pnl_usd must be computed EXCLUDING end_of_data trades. (b) Report end_of_data separately as already done, but do NOT include in headline PF. (c) If end_of_data_count > 5% of total_trades, add a warning flag in summary.
- Acceptance: Re-run backtest and verify that `profit_factor` and `total_pnl_usd` in summary do not include end_of_data PnL. `end_of_data_pnl` remains available as a separate field.
- Data reference: `backtest_engine.py:_compute_summary()` lines 280-283 -- `total_pnl` currently includes eod. Also `gross_win` and `gross_loss` include eod wins/losses.

### REQ-CAL2-002: Reduce SHORT_SCORE_MULTIPLIER to allow SHORT trades
- Priority: **High**
- Problem: SHORT_SCORE_MULTIPLIER = 2.0 creates effective threshold 19.5 (forex) which is unreachable in backtest (max composite ~13.5). SHORT trades = 0 out of 88,327 signals. System is unintentionally LONG-only.
- Required behavior: Two options for architect to decide:
  - **Option A (recommended)**: Set SHORT_SCORE_MULTIPLIER = 1.3. Effective threshold becomes 15*0.65*1.3 = 12.675, achievable for strong SHORT signals. Combined with RSI < 30 momentum filter, this still requires high conviction.
  - **Option B**: Accept LONG-only system explicitly. Remove SHORT scoring logic entirely, save computation. Document this as a design decision.
- Acceptance: If Option A: backtest produces SHORT trades > 0 with separate WR tracked. If Option B: code comments and docs state LONG-only design.
- Data reference: `config.py:SHORT_SCORE_MULTIPLIER`, `filter_pipeline.py:check_score_threshold()` line 329

### REQ-CAL2-003: Reduce TP distance or add trailing stop to address 75% time_exit rate
- Priority: **High**
- Problem: 188/250 trades (75.2%) exit via time_exit. Only 25 (10%) reach TP. TP targets are set by REGIME_RR_MAP (1.3x-2.5x R:R) but price rarely reaches those levels within 24 candles. The system generates small losses on the majority of trades.
- Required behavior: Architect should evaluate one or more of:
  - **3a**: Reduce R:R ratios by 30-40% (e.g., VOLATILE from 2.0 to 1.3, STRONG_TREND from 2.5 to 1.7). This should increase TP hit rate.
  - **3b**: Implement trailing stop that locks in partial gains. When MFE reaches 50% of TP distance, move SL to breakeven + small buffer.
  - **3c**: Add partial close at 1:1 R:R (close 50% of position), let remainder run to full TP.
  - **3d**: Increase time_exit candle limit (H1: 24 -> 36) to give trades more time to reach TP. But may increase drawdown.
- Acceptance: time_exit rate drops below 50%. TP hit rate increases above 20%.
- Data reference: `risk_manager_v2.py:REGIME_RR_MAP`, `backtest_engine.py:_TIME_EXIT_CANDLES`, `time_exit_count: 188`

### REQ-CAL2-004: Block weekend trading for all instruments
- Priority: **Medium**
- Problem: 9 weekend trades (Sat=2, Sun=7) produce -$225.79 with 0 wins. Crypto is currently exempt from the Monday gap filter but trades on weekends are unprofitable.
- Required behavior: Add Saturday (weekday=5) and Sunday (weekday=6) to the weekday filter for ALL market types including crypto. No trades should be opened on Saturday or Sunday.
- Acceptance: Backtest shows 0 trades on Saturday/Sunday. PnL improves by ~$226.
- Data reference: `filter_pipeline.py:check_weekday()` -- currently only blocks Mon 00-10 UTC and Fri 18+ UTC. `by_weekday` data shows Sat/Sun losses.

### REQ-CAL2-005: Consider blocking TREND_BULL regime or adding tighter filters
- Priority: **Medium**
- Problem: TREND_BULL produces 45 trades with -$45.83 (WR 17.8%, negative avg PnL). STRONG_TREND_BULL produces 96 trades with -$108.47 (WR 11.5%). Only VOLATILE is profitable (+$1,997 from 109 trades). 56% of trades enter non-profitable regimes.
- Required behavior: Two options:
  - **Option A**: Add TREND_BULL to BLOCKED_REGIMES. Removes 45 losing trades. Monitor if this also removes too many rare winners.
  - **Option B**: Keep TREND_BULL but require higher min_composite_score (e.g., 1.5x multiplier for non-VOLATILE regimes). This keeps the door open for strong signals while filtering noise.
  - For STRONG_TREND_BULL: do NOT block entirely (96 trades, some may be valuable in specific instruments like GC=F). Instead investigate which instruments profit in this regime and apply per-instrument overrides.
- Acceptance: Run comparative backtest with TREND_BULL blocked vs current. Document trade count and PnL impact.
- Data reference: `by_regime` data, `config.py:BLOCKED_REGIMES`

### REQ-CAL2-006: Add by_symbol win_rate field and exclude eod from by_symbol metrics
- Priority: **Medium**
- Problem: by_symbol includes end_of_data trades in win counts, creating inconsistency with win_rate_pct (which excludes eod). No explicit win_rate field exists in by_symbol -- consumers must calculate it.
- Required behavior: (a) Add `win_rate_pct` field to each entry in `by_symbol`. (b) Add `wins_excl_eod` or exclude eod trades from by_symbol win counting, matching the approach used for global win_rate_pct.
- Acceptance: by_symbol entries contain win_rate_pct. Sum of by_symbol wins matches global win count.
- Data reference: `backtest_engine.py:_compute_summary()` lines 335-343

### REQ-CAL2-007: Relax SPY filter to allow some trades
- Priority: **Low**
- Problem: SPY has min_score=30 + allowed_regimes=["STRONG_TREND_BULL"]. This produces 0 trades in 2 years. The override is too restrictive to be useful.
- Required behavior: Either (a) reduce min_score to 20 and allow VOLATILE + STRONG_TREND_BULL, or (b) remove SPY from instrument list if the intent is to not trade it.
- Acceptance: SPY produces at least 5 trades in 2-year backtest, OR SPY is removed from the symbol list with documented rationale.
- Data reference: `config.py:INSTRUMENT_OVERRIDES["SPY"]`

### REQ-CAL2-008: Add concentration risk warning to summary
- Priority: **Low**
- Problem: GC=F and ETH/USDT together produce 95% of total PnL ($1,753 of $1,843). If either instrument underperforms, the entire strategy becomes unprofitable. This concentration risk is not flagged anywhere.
- Required behavior: In _compute_summary(), compute a concentration metric: if any single instrument contributes > 40% of total PnL, or top 2 instruments contribute > 70%, add `concentration_warning` field to summary with details.
- Acceptance: Summary contains concentration_warning when applicable.
- Data reference: `by_symbol` data showing GC=F at 51% and ETH at 44% of total PnL

### REQ-CAL2-009: Investigate AUDUSD persistent losses
- Priority: **Low**
- Problem: AUDUSD=X has 46 trades, 4 wins (8.7% WR), -$97.87 PnL. It is the worst-performing forex pair. Current min_composite_score is the global default (15).
- Required behavior: Add AUDUSD=X to INSTRUMENT_OVERRIDES with min_composite_score = 22 (matching NZDUSD/USDJPY). Or consider removing from instrument list if forex-AUD is structurally unprofitable for this strategy.
- Acceptance: Backtest with AUDUSD override shows improved PnL or AUDUSD removed.
- Data reference: `by_symbol["AUDUSD=X"]`, no entry in INSTRUMENT_OVERRIDES

## Priority Summary

| Priority | REQ | Issue | Expected Impact |
|----------|-----|-------|-----------------|
| High | REQ-CAL2-001 | eod PnL inflates PF | Honest metrics: PF ~1.35 |
| High | REQ-CAL2-002 | SHORT impossible | Enable SHORT or accept LONG-only |
| High | REQ-CAL2-003 | 75% time_exit rate | Increase TP rate, improve consistency |
| Medium | REQ-CAL2-004 | Weekend losses | +$226 PnL recovery |
| Medium | REQ-CAL2-005 | Unprofitable regimes | Remove ~45-141 losing trades |
| Medium | REQ-CAL2-006 | by_symbol WR inconsistency | Cleaner metrics |
| Low | REQ-CAL2-007 | SPY 0 trades | Either make useful or remove |
| Low | REQ-CAL2-008 | Concentration risk | Risk awareness |
| Low | REQ-CAL2-009 | AUDUSD losses | +~$50-98 PnL recovery |
