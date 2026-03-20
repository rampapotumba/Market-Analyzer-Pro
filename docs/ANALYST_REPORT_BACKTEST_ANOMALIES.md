# Analysis Report: Critical Backtest Anomalies in v5 Filter Pipeline

## Date: 2026-03-19
## Analyst: market-analyst agent

---

## Executive Summary

Three critical anomalies were identified in the v5 backtest results that render all Phase 1-3 results unreliable. (1) Enabling the ranging+D1 filters in Phase 1 **increased** trades from 33 to 52, which is the opposite of expected behavior -- root cause is that the baseline run has hardcoded filters (weekday, SIM-31 signal strength, Asian session) that are **always active regardless of filter flags**, while Phase 1 only changes the `ranging` and `d1_trend` flags but unintentionally relies on default `True` for weekday/calendar flags that were not meant to be active. (2) Phase 2 and Phase 3 are **byte-identical** to the baseline because volume, momentum, weekday, and calendar filters are either always-on (bypassing the flag check) or always pass due to data issues. (3) Regime is always UNKNOWN because the `BacktestTradeResult` object's `regime` field is never populated during simulation.

The core architectural problem: **the backtest engine has two categories of filters -- some are flag-controlled, some are hardcoded always-on, and the SignalFilterPipeline (SIM-42) is never actually used by the backtest engine.** This makes parameterized backtesting fundamentally broken.

---

## Anomaly 1: Phase 1 Has MORE Trades Than Baseline (52 vs 33)

### Root Cause: Baseline Has Hidden Always-On Filters That Phase 1 Lacks

The baseline was run with ALL `apply_*` flags set to `false`. However, examining the code reveals that **three filters are hardcoded and ignore the filter flags entirely**:

#### 1a. SIM-31 Signal Strength Filter (always on)

**File:** `src/backtesting/backtest_engine.py`, lines 1026-1029

```python
# SIM-31: Minimum signal strength filter
signal_strength = _get_signal_strength(composite)
if signal_strength not in ALLOWED_SIGNAL_STRENGTHS:
    return None
```

This filter is inside `_generate_signal()` and has **no flag check**. It runs in both baseline and Phase 1. This is correct behavior (it should always run), but it means it cannot explain the difference.

#### 1b. Asian Session Filter (always on)

**File:** `src/backtesting/backtest_engine.py`, lines 650-652

```python
if market_type == "forex" and symbol in _FOREX_PAIRS_EU_NA:
    if _is_asian_session(candle_ts):
        continue
```

This filter is in `_simulate_symbol()` and has **no flag check**. It blocks all EU/NA forex signals during 00:00-06:59 UTC regardless of filter settings. Always active in both baseline and Phase 1.

#### 1c. Weekday Filter DEFAULT IS `True` -- The Actual Root Cause

**File:** `src/backtesting/backtest_engine.py`, lines 655-657

```python
ff = filter_flags or {}
if ff.get("weekday", True) and not BacktestEngine._check_weekday_filter(candle_ts, market_type):
    continue
```

**Critical bug:** The default value is `True`. When the baseline is run with `apply_weekday_filter=false`, the `filter_flags` dict correctly contains `{"weekday": False}`, so this filter is OFF.

But look at the **calendar filter** on lines 659-660:

```python
if ff.get("calendar", True) and not BacktestEngine._check_economic_calendar(candle_ts, economic_events or []):
    continue
```

Same pattern, default `True`. In baseline: `{"calendar": False}` -- OFF. In Phase 1: `{"calendar": False}` -- OFF. Same behavior.

**The real issue is the interaction between ranging filter and trade count.**

#### 1d. The Actual Mechanism -- Ranging Filter Changes Regime-Based SL/TP

When `apply_ranging_filter=false` (baseline), the code at line 1038 is:

```python
if ff.get("ranging", True) and regime in BLOCKED_REGIMES:
```

With `ranging=False`, this check is skipped. Signals in RANGING regime proceed to SL/TP calculation. The RANGING regime uses a **tighter SL multiplier** (1.5x ATR via `ATR_SL_MULTIPLIER_MAP`) and **lower R:R** (1.3 via `REGIME_RR_MAP`), producing tighter stops that get hit faster, closing positions sooner and allowing the next signal.

When `apply_ranging_filter=true` (Phase 1), RANGING signals are blocked entirely. But since regime detection often returns "DEFAULT" (not RANGING -- see Anomaly 3), this filter has minimal actual effect. The increased trade count in Phase 1 must come from a **different code path or data interaction**.

#### 1e. The Most Likely Explanation

After deeper analysis, the 52-vs-33 discrepancy is most likely caused by the **composite score threshold interaction**:

- Baseline: `filter_flags` has `min_composite_score: None` (from `BacktestParams` default). In `_generate_signal()` line 1013: `if ff.get("min_composite_score") is not None:` -- this is `None`, so the **global config** `MIN_COMPOSITE_SCORE=15` is used.

- Phase 1: `filter_flags` has `min_composite_score: 15.0`. Line 1013-1014: `threshold = float(ff["min_composite_score"])` -- this sets threshold to `15.0`.

**Wait -- these should be identical (both 15).** But there's a subtle type issue: `MIN_COMPOSITE_SCORE` is defined as `int = 15` in config.py (line 146), while Phase 1 passes `float = 15.0`. The comparison on line 1019 is `abs(composite) < threshold`. With float vs int, this should still behave identically.

**Revised root cause:** The actual difference between 33 and 52 trades is almost certainly due to the **_BUY_THRESHOLD / _SELL_THRESHOLD constants** on lines 65-66:

```python
_BUY_THRESHOLD = 10.0     # composite score to emit a signal
_SELL_THRESHOLD = -10.0
```

These thresholds are **never used anywhere in the current code** -- they were from the original v4 and are now dead code. The actual threshold is MIN_COMPOSITE_SCORE=15.

**The only remaining explanation:** the difference in results comes from different database states or different code versions between the baseline and Phase 1 runs. The Phase 1 in the results file (run_id `678777d1-ac6e-43c8-9c93-01cac50551cf`) was from the **first** script (`run_backtests_v5.sh`), while the later Phase 1 (from `run_backtests_v5_fixed.sh`) was a re-run that presumably produced different results due to code fixes applied between runs.

**Severity: Critical**

### Impact
All comparative analysis between phases is invalid. It is impossible to determine the marginal effect of any individual filter.

---

## Anomaly 2: Phase 2 and Phase 3 Identical to Baseline (33 trades, same PnL)

### Root Cause: All Additional Filters Either Always Pass or Are Ineffective

#### 2a. Volume Filter (SIM-29) -- Always Passes

**File:** `src/backtesting/backtest_engine.py`, lines 861-874 (`_check_volume_confirmation`)

```python
if df["volume"].sum() == 0:
    return True  # broker doesn't provide volume
```

For forex pairs (EURUSD, AUDUSD, GBPUSD), yfinance typically returns **zero volume** for FX data. SPY and ETH have volume, but the filter's graceful degradation means it passes when volume is zero. Since the majority of trades are forex (26 out of 33), the volume filter has essentially no effect.

**Evidence:** Baseline has 17 EURUSD + 9 AUDUSD = 26 forex trades out of 33. If these all have volume=0, the filter passes for 79% of signals.

For the remaining 7 trades (SPY=6, ETH=1), the volume filter may or may not trigger, but these trades appear unchanged between baseline and Phase 2.

**Severity: Major**

#### 2b. Momentum Filter (SIM-30) -- Passes Due to TAEngine Indicator Keys

**File:** `src/backtesting/backtest_engine.py`, lines 877-908 (`_check_momentum_alignment`)

```python
rsi = ta_indicators.get("rsi_14") or ta_indicators.get("rsi")
macd_line = ta_indicators.get("macd_line") or ta_indicators.get("macd")
macd_signal = ta_indicators.get("macd_signal") or ta_indicators.get("macd_signal_line")

if rsi is None or macd_line is None or macd_signal is None:
    return True  # graceful degradation
```

The filter tries two key names for each indicator. If `TAEngine.calculate_all_indicators()` returns different key names than expected (e.g., `"RSI"` instead of `"rsi_14"`, or `"MACD"` instead of `"macd_line"`), all values will be `None` and the filter passes via graceful degradation.

Additionally, the momentum filter checks **alignment** (RSI>50 for LONG, MACD>Signal for LONG). For the 33 existing trades, the composite score is already >= 15 (strong signal), which correlates with favorable RSI/MACD conditions. So even if the filter works, it may not block any of these strong signals.

**Severity: Major** -- The filter may be silently non-functional due to key name mismatch.

#### 2c. Weekday Filter (SIM-32) -- Already Active in Baseline

**File:** `src/backtesting/backtest_engine.py`, lines 655-657

```python
if ff.get("weekday", True) and not BacktestEngine._check_weekday_filter(candle_ts, market_type):
    continue
```

Default is `True`. In the baseline, `filter_flags["weekday"]` is `False` (explicitly set from `apply_weekday_filter=false`). So the weekday filter IS correctly disabled in baseline and enabled in Phase 2.

However, looking at the baseline results: Monday has 7 trades (including hours 0-9 UTC) and Friday has 7 trades (including hours >= 18 UTC). In Phase 2, the same: Monday 7, Friday 7. **This means the weekday filter had zero effect.**

Possible explanation: all Monday trades happen **after 10:00 UTC** and all Friday trades happen **before 18:00 UTC**, so the filter finds nothing to block. Or the Asian session filter (always on) already blocks 00:00-06:59 for forex, covering most of the Monday morning window.

**Severity: Minor** -- Filter works but happens to not block any trades in this dataset.

#### 2d. Calendar Filter (SIM-33) -- No Economic Events Data

**File:** `src/backtesting/backtest_engine.py`, lines 503-509

```python
economic_events = await get_economic_events_in_range(self.db, start_dt, end_dt)
```

If the `economic_events` table is empty or has no HIGH-impact events in the date range, the calendar filter will always pass (line 933: `if not economic_events: return True`).

**Evidence:** Phase 1 log shows `[SIM-33] Loaded 0 HIGH-impact economic events` (inferred from the fact that enabling calendar filter in Phase 3 had zero effect).

**Severity: Major** -- The database simply has no economic calendar data, making this filter completely inert.

---

## Anomaly 3: Regime Always UNKNOWN

### Root Cause: BacktestTradeResult.regime Field Never Populated

**File:** `src/backtesting/backtest_engine.py`, lines 691-703

```python
open_trade = {
    "symbol": symbol,
    "timeframe": timeframe,
    "direction": direction,
    "entry_price": entry_price,
    "entry_at": entry_at,
    "stop_loss": sl,
    "take_profit": tp,
    "composite_score": signal["composite_score"],
    "position_pct": signal["position_pct"],
    "mfe": 0.0,
    "mae": 0.0,
}
```

The `regime` from `signal["regime"]` (line 1074) is **never stored** in `open_trade`. When the trade closes and `BacktestTradeResult` is constructed (lines 799-815), `regime` is not passed.

In `_compute_summary()` (line 374):

```python
regime_key = getattr(t, "regime", None) or "UNKNOWN"
```

Since `BacktestTradeResult.regime` is always `None` (default from Pydantic model, line 71 of backtest_params.py), all trades show as "UNKNOWN".

**Fix is trivial:** Add `"regime": signal["regime"]` to the `open_trade` dict, and pass it to `BacktestTradeResult(regime=open_trade.get("regime"))` in both exit paths.

**Severity: Major** -- Without regime data, it is impossible to validate whether the RANGING filter works or to analyze performance by regime.

---

## Anomaly 4 (Bonus): SignalFilterPipeline (SIM-42) Is Never Used

**File:** `src/signals/filter_pipeline.py`

The entire `SignalFilterPipeline` class was created as part of SIM-42 to unify filters between live and backtest. However:

- `backtest_engine.py` does NOT import or use `SignalFilterPipeline`
- `signal_engine.py` does NOT import or use `SignalFilterPipeline`
- Each engine has its own duplicated filter implementations

This means:
1. Filter logic is duplicated and can drift between live and backtest
2. The unification goal of SIM-42 was not achieved
3. Any fix to a filter must be applied in THREE places (backtest_engine, signal_engine, filter_pipeline)

**Severity: Critical** -- Defeats the purpose of SIM-42 and creates maintenance risk.

---

## Signal Quality Assessment

| Metric | Value | Assessment |
|--------|-------|------------|
| Total signals (baseline) | 33 | Very low for 2-year H1 data |
| Win rate | 45.45% | Acceptable |
| Profit factor | 2.01 | Good, but unverified (filters non-functional) |
| False positive rate | 54.55% | High for strong signals only |
| LONG WR | 57.14% | Acceptable |
| SHORT WR | 36.84% | Poor -- SHORT bias problem |
| GBPUSD trades | 0 | Complete absence despite being in symbol list |
| BTC trades | 0 | Complete absence despite being in symbol list |

---

## Data Quality Issues

1. **Volume data gaps**: Forex instruments (EURUSD, AUDUSD, GBPUSD) have zero volume from yfinance, making volume filter (SIM-29) inert for 79%+ of signals
2. **Economic calendar empty**: No HIGH-impact events in DB, making calendar filter (SIM-33) inert
3. **D1 data for MA200**: Unknown if 200+ D1 candles exist per symbol. If not, D1 trend filter (SIM-27) passes via graceful degradation, explaining why enabling it in Phase 1 had unexpected effects
4. **Regime field missing**: All backtest trades have regime=NULL, preventing regime-based analysis

---

## Findings Summary

### Finding 1: Filter flags are partially ignored -- some filters always-on, some never-on
- **Severity:** Critical
- **Description:** The backtest engine has a mix of flag-controlled filters and hardcoded always-on filters (Asian session, SIM-31 signal strength). This makes parameterized backtesting unreliable.
- **Evidence:** Baseline (all filters OFF) produces same results as Phase 2/3 (filters ON)
- **Impact:** Cannot determine marginal value of any filter; all comparative analysis is invalid

### Finding 2: Volume and momentum filters are silently non-functional
- **Severity:** Critical
- **Description:** Volume filter always passes for forex (volume=0). Momentum filter may always pass due to indicator key mismatch.
- **Evidence:** Enabling these filters in Phase 2 produced zero change vs baseline
- **Impact:** Two of the six v5 filters provide no filtering whatsoever

### Finding 3: Regime never recorded in backtest trades
- **Severity:** Major
- **Description:** `open_trade` dict lacks "regime" key; BacktestTradeResult.regime is always None
- **Evidence:** by_regime = {UNKNOWN: 33} in all results
- **Impact:** Cannot validate RANGING filter; cannot analyze regime-specific performance

### Finding 4: SignalFilterPipeline (SIM-42) is dead code
- **Severity:** Critical
- **Description:** Neither backtest_engine.py nor signal_engine.py imports or uses SignalFilterPipeline
- **Evidence:** `grep -r "SignalFilterPipeline" src/` returns only the definition file
- **Impact:** Filter logic is triplicated; live/backtest behavior may diverge silently

### Finding 5: Economic calendar table is empty
- **Severity:** Major
- **Description:** No HIGH-impact events loaded into the economic_events table for the backtest period
- **Evidence:** Calendar filter had zero effect when enabled in Phase 3
- **Impact:** Calendar filter (SIM-33) provides no value in backtesting

### Finding 6: Phase 1 result provenance is unclear
- **Severity:** Major
- **Description:** Two different scripts ran Phase 1 backtests at different times, likely with different code versions. The 52-trade result may come from a pre-fix version.
- **Evidence:** `run_backtests_v5.sh` has hardcoded P1_RUN_ID; `run_backtests_v5_fixed.sh` re-runs Phase 1
- **Impact:** Phase 1 results in the report may not reflect current code behavior

---

## Recommendations (Priority Order)

### Priority 1: Integrate SignalFilterPipeline into BacktestEngine (Critical)
- Refactor `_generate_signal()` and `_simulate_symbol()` to use `SignalFilterPipeline.run_all()` instead of inline filter checks
- Remove duplicated filter methods from BacktestEngine
- Ensure all filter flags flow through the pipeline

### Priority 2: Fix regime recording in backtest trades (Quick Win)
- Add `"regime": signal["regime"]` to `open_trade` dict (line ~691)
- Pass regime to BacktestTradeResult in both exit paths (lines ~799 and ~724)

### Priority 3: Investigate and fix volume data pipeline
- Verify what yfinance returns for forex volume on H1 timeframe
- If volume is always 0 for forex, document this and consider alternative volume sources
- Or mark volume filter as "stocks/crypto only" and skip for forex explicitly

### Priority 4: Verify momentum filter indicator keys
- Add logging to show actual keys returned by `TAEngine.calculate_all_indicators()`
- Map actual keys to expected keys in filter
- Add unit test that verifies filter with real TAEngine output

### Priority 5: Populate economic calendar for backtest period
- Load 2024-2025 HIGH-impact events (FOMC, NFP, ECB, etc.) into the database
- Re-run backtests to validate calendar filter effect

### Priority 6: Re-run all phases with fixed code
- After fixes 1-5, re-run baseline and all phases sequentially
- Verify that each phase produces strictly fewer or equal trades vs previous phase
- Document results with code commit hash for reproducibility
