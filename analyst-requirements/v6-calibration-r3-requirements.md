# Requirements: v6 Calibration Round 3 Post-Analysis

## Date: 2026-03-20
## From: market-analyst
## To: architect

## Background

Analysis of v6-cal-r3 backtest (first run with real historical F&G, DXY, Funding Rates, COT, 453 economic calendar events, 21-month window). Results: PF 1.09, WR 19.4%, DD 53.8%, $138 total PnL. System is not viable for live trading. Before any further calibration, the following bugs and structural issues must be addressed.

---

## Requirements

### REQ-001: Fix DXY Filter — Real Data Not Passed to Backtest Pipeline
- **Priority:** High
- **Problem:** `backtest_engine.py` line 1355 hardcodes `"dxy_rsi": None` despite real DXY historical data now being available. The DXY filter (SIM-38) is therefore non-functional in backtest mode — all forex signals pass via graceful degradation.
- **Required behavior:** Pre-compute DXY RSI(14) from stored DXY price_data (similar to how D1 data is pre-loaded via `d1_data_cache`). Pass the DXY RSI value for the current candle timestamp into `filter_context["dxy_rsi"]`. DXY should be loaded once at start of `_simulate()` and looked up by timestamp during the candle loop.
- **Acceptance:** Running cal-r3 backtest again should show non-zero rejections for `rejected_by_dxy_filter` in filter_stats. Forex LONG trades during high-DXY periods (Q3-Q4 2024) should be blocked.
- **Data reference:** `backtest_engine.py:1355`, `filter_pipeline.py:547-567`

### REQ-002: Block Monday Entries for Forex
- **Priority:** High
- **Problem:** Monday entries produce WR 4.0% (1 win out of 25 trades), losing -$399. The WEAK_WEEKDAY_SCORE_MULTIPLIER of 1.5x is not sufficient — signals still pass. Monday forex signals are consistently anti-predictive.
- **Required behavior:** Option A (recommended): Add Monday to BLOCKED_WEEKDAYS for forex (full block, not just score multiplier). Option B: Increase WEAK_WEEKDAY_SCORE_MULTIPLIER to 3.0+ which effectively blocks almost all Monday forex signals while keeping the multiplier architecture.
- **Acceptance:** Rerun backtest shows 0 Monday forex entries OR Monday PnL >= $0.
- **Data reference:** `config.py:217-220`, by_weekday analysis showing Monday -$399

### REQ-003: Investigate Time Exit Dominance — 70% of Trades
- **Priority:** High
- **Problem:** 127 out of 182 trades exit via time_exit (24 H1 candles). This means the system enters positions where the market does not move in the expected direction within 24 hours. The signal has no timing edge at H1.
- **Required behavior:** Run two diagnostic backtests: (a) H4 timeframe with same parameters — does time_exit rate drop below 50%? (b) H1 with TIME_EXIT_CANDLES["H1"] = 48 (original value) — do more trades reach TP/SL? The results should be documented and used to decide optimal timeframe.
- **Acceptance:** Analysis document comparing H1 vs H4 time_exit rates. If H4 reduces time_exit to < 40%, recommend switching default timeframe.
- **Data reference:** time_exit_count: 127/182 (69.8%), avg_win_duration: 12,037 min (8.3 days) vs avg_loss_duration: 4,411 min (3 days)

### REQ-004: Add Fear&Greed / Funding Rate Impact Tracking
- **Priority:** Medium
- **Problem:** Cannot isolate the impact of F&G and Funding Rate on crypto signal quality. ETH went from +$812 (no data) to -$26 (real data), but we cannot determine if F&G adjustments helped or hurt because there is no per-signal logging of the adjustment applied.
- **Required behavior:** In `BacktestTradeResult`, add optional fields: `fg_adjustment: Optional[Decimal]`, `fr_adjustment: Optional[Decimal]`. When a crypto signal is modified by F&G or FR data, log the adjustment amount. In `_compute_summary()`, add a section `by_adjustment` showing PnL with/without these adjustments.
- **Acceptance:** Backtest output includes breakdown of trades where F&G or FR modified the composite score, showing adjustment direction and trade result.
- **Data reference:** Config SIM-39 (F&G), SIM-40 (FR), ETH/USDT: 32 trades, -$26

### REQ-005: Per-Regime Instrument Blocking
- **Priority:** Medium
- **Problem:** VOLATILE regime produces -$33 across 95 trades (WR 19%). Blocking globally would remove too many trades. But VOLATILE is likely profitable for GC=F (gold volatility = trend continuation) and unprofitable for forex (volatility = noise).
- **Required behavior:** Add `blocked_regimes_by_instrument` to config, allowing regime blocks per instrument. Example: block VOLATILE for all forex pairs but allow for GC=F. The filter pipeline should check instrument-specific regime blocks before global blocks.
- **Acceptance:** Configurable per-instrument regime blocking. Backtest shows forex VOLATILE trades blocked while GC=F VOLATILE trades pass.
- **Data reference:** by_regime: VOLATILE 95 trades -$33, STRONG_TREND_BULL 85 trades +$171

### REQ-006: Calendar Filter A/B Comparison
- **Priority:** Medium
- **Problem:** Calendar filter blocks 135 signals, but we cannot measure if this improves or hurts results. With 453 HIGH-impact events and +/-4h window, 23% of trading hours are blocked. Some blocked signals near news events would have been profitable.
- **Required behavior:** Run two backtests with identical parameters except apply_calendar_filter=True vs False. Compare PF, WR, total PnL. Document the delta. If calendar filter produces PF improvement > 0.05, keep it. Otherwise, reduce window or make it per-instrument.
- **Acceptance:** Documented comparison with clear recommendation on calendar filter configuration.
- **Data reference:** rejected_by_calendar_filter: 135, total 453 economic events

### REQ-007: Add System Viability Gate to Backtest Output
- **Priority:** Low
- **Problem:** The system currently reports raw metrics but provides no automated assessment of whether the results meet minimum viability criteria. Manual analysis is required after every run.
- **Required behavior:** Add a `viability_assessment` section to `_compute_summary()` output:
  ```json
  "viability_assessment": {
    "pf_viable": false,     // PF >= 1.3
    "wr_viable": false,     // WR >= 25%
    "dd_viable": false,     // DD <= 25%
    "concentration_viable": false,  // no single instrument > 40% of PnL
    "overall": "NOT_VIABLE",  // all must pass for "VIABLE"
    "blocking_factors": ["pf_below_1.3", "dd_above_25pct", "concentration_risk"]
  }
  ```
- **Acceptance:** Backtest JSON output includes viability_assessment with correct pass/fail logic.
- **Data reference:** All cal-r3 metrics failing minimum viable thresholds

### REQ-008: Strategic Decision Required — Timeframe and Scope
- **Priority:** High (Decision, not implementation)
- **Problem:** Six rounds of calibration have produced a system that barely breaks even on historical data with real external inputs. The fundamental architecture (TA composite score at H1, multi-instrument) may not have a path to viability.
- **Required decision:** Product owner / architect must decide:
  1. Continue calibration within current architecture (expected: marginal improvements, PF 1.1-1.3 ceiling)
  2. Pivot to D1/H4 timeframe (requires new backtest infrastructure, may reveal hidden edge)
  3. Reduce to single instrument (GC=F) with optimized parameters (pragmatic but fragile)
  4. Redesign signal generation (abandon TA composite, move to pattern-based or ML-based approach)
- **Acceptance:** Written decision document with rationale. If option 1, define hard stop criterion (e.g., "if PF < 1.3 after 2 more rounds, pivot to option 2").
- **Data reference:** Full analysis report at `/Users/dmitriyfedotov/Projects/Market Analyzer Pro/analyst-reports/v6-cal-r3-analysis.md`
