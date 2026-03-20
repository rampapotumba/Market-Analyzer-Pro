# Architecture Decision: TA-Only H1 Trading System

## Date: 2026-03-20
## Status: Pending r5 results
## Context: 6 rounds of backtesting completed. System PF = 1.09 after extensive calibration.

---

## Go Criteria (stay with current approach)

ALL of these must be met after r5 backtest:

| Criterion | Threshold | r4 Actual | r5 Actual |
|-----------|-----------|-----------|-----------|
| Profit Factor | >= 1.3 | 1.09 | ___ |
| Win Rate | >= 25% | 24.82% | ___ |
| Max Drawdown | <= 25% | 48.5% | ___ |
| PnL Concentration | No single instrument > 50% of PnL | GC=F = 94% | ___ |

## Hard Stop Rules

- **PF < 1.2 after r5** --> Skip directly to pivot. No more calibration rounds.
- **PF 1.2 to 1.29** --> One additional calibration round allowed (r6), but must demonstrate clear path to 1.3+.
- **PF >= 1.3 but DD > 25%** --> Conditional Go. Position sizing needs reduction before live deployment.

---

## Pivot Options (ordered by feasibility)

### Option A: Switch to D1 Timeframe

**Description:** Same TA composite scoring engine, but generate signals on D1 candles instead of H1. Average trade duration (4034 min ~ 67 hours ~ 2.8 days) already suggests the system operates on a D1 horizon despite using H1 entries.

**Effort:** Low
- Change `params.timeframe` from "H1" to "D1"
- Adjust `TIME_EXIT_CANDLES` and cooldown parameters
- Remove session/weekday filters (not applicable to D1)
- Full re-backtest required

**Risk:**
- ~5x fewer signals (D1 produces ~1 signal/day vs ~5 on H1)
- May not generate enough trades for statistical validation
- Slippage model may need adjustment for daily entries

**Data required:** Same data already collected.

### Option B: Narrow to GC=F + USDCAD Strategy

**Description:** Abandon multi-instrument approach. Optimize exclusively for the 2 instruments with demonstrated positive edge: GC=F (gold) and USDCAD. Effectively becomes a "gold trend + CAD carry" strategy.

**Effort:** Low
- Set `BACKTEST_INSTRUMENT_WHITELIST = ["GC=F", "USDCAD=X"]`
- Optimize score thresholds, SL/TP, and regime filters specifically for these 2 instruments
- Potentially add gold-specific indicators (gold/silver ratio, real yields)

**Risk:**
- Maximum 2 instruments = high concentration risk
- GC=F may not sustain edge in all market conditions
- Not a "system" -- closer to manual instrument selection

**Data required:** Same data already collected.

### Option C: Replace Composite Scoring with Pattern-Based Signals

**Description:** Instead of averaging 8 TA indicators into a composite score, identify specific high-conviction pattern combinations. Example: RSI divergence + MA cross + volume spike = signal, rather than "composite >= 15."

**Effort:** High
- Significant refactoring of TAEngine signal generation
- Need to define and validate specific patterns
- Each pattern needs its own backtest validation
- Estimated: 2-4 weeks development

**Risk:**
- Overfitting to historical patterns
- Complexity explosion (N patterns x M instruments x K timeframes)
- May not improve results if underlying data quality is the bottleneck

**Data required:** Same data, but need pattern recognition logic.

### Option D: Add ML Classification Layer

**Description:** Use existing TA scores (RSI, MACD, BB position, ADX, etc.) as features. Train an ML classifier (e.g., gradient boosting) on historical outcomes from 6 rounds of backtesting. The ML model replaces the fixed composite score threshold.

**Effort:** Medium-High
- Feature engineering from existing TA indicators
- Train/validate with walk-forward methodology
- Integrate prediction into signal pipeline
- Estimated: 1-3 weeks development

**Risk:**
- Small labeled dataset (~800 total signals across 6 rounds, ~140 trades)
- High overfitting risk with small N
- "ML on small data" often worse than simple rules
- Requires ongoing model retraining infrastructure

**Data required:** Existing backtest trade results as labels.

---

## Current System Limitations (immutable within current architecture)

1. **GC=F dependency:** Gold generates 94% of PnL. System is effectively a gold trend follower with noise from other instruments.
2. **LONG-only:** 132/139 trades are LONG. SHORT is effectively disabled by parameter gates.
3. **H1/D1 mismatch:** H1 entries with average 2.8-day hold time = suboptimal capital efficiency.
4. **Low win rate:** 24.82% requires R:R > 3:1 to break even. Current average R:R is ~2:1.
5. **Composite score ceiling:** TA-only composite score range is narrow (~-15 to +15), making threshold calibration fragile.

---

## r5 Results

<!-- Fill in after r5 backtest run -->

| Metric | Value |
|--------|-------|
| Run ID | ___ |
| Total trades | ___ |
| Win rate | ___ |
| Profit factor | ___ |
| Total PnL | ___ |
| Max drawdown | ___ |
| GC=F PnL share | ___ |
| ETH/USDT trades | ___ (expected: 0) |
| DXY filter rejections | ___ (expected: 50+) |

### Decision

**GO / PIVOT to Option ___**

Reasoning: ___
