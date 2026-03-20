## Last updated: 2026-03-20

## Current goal
v6 calibration completed (8 rounds, NoGo decision). Pivot to new approach required. v7 planned (data wiring, backfill, strategy overhaul). Uncommitted R5 code review fixes need to be committed first.

## Done (v5)
- All SIM-25..SIM-44 code implemented (Phases 1-5)
- Tasks 1-9: filter pipeline, session/volume/momentum/calendar, O(n^2)->O(n) optimization
- Code review passed, all hotfixes applied
- Full regression: 216 tests passed
- ALL 4 backtest phases completed

## Done (v6)
- v6 spec and tasks created (SPEC_SIMULATOR_V6.md, TASKS_V6.md)
- TASK-V6-01 through V6-13: all implemented (data loading, calibration, filters, diagnostics, exits, metrics, walk-forward stub)
- 8 calibration rounds (cal1 through r5) completed
- Performance optimization: O(n^2)->O(n) backtest engine
- DXY RSI timezone normalization + lookahead fix
- ETH/USDT blocked, instrument whitelist, architecture decision doc

## Done (v6 calibration rounds)
- cal1: 700 trades, PF 1.12, +$1766
- cal2: 239 trades, PF 1.28, +$533
- cal3 (r3): 182 trades, PF 1.09, +$138
- r4: 139 trades, PF 1.09, +$159
- r5: 47 trades, PF 1.27, +$131 -- FINAL, NoGo decision

## Key Decision: NoGo on H1 TA-only approach
- Analyst verdict: "The TA-only composite scoring system on H1 timeframe has been exhaustively tested across 8 calibration rounds. It does not produce a stable, statistically significant edge."
- 3 of 4 acceptance criteria failed (PF, WR, PnL concentration)
- Recommended pivot: D1 timeframe + narrow instrument universe + full composite scoring (FA+Sentiment+Geo)

## Uncommitted changes (R5 code review fixes)
- backtest_engine.py: whitelist block moved before D1 preload (review issue #1)
- config.py: ETH/USDT removed from INSTRUMENT_OVERRIDES, type hints fixed (review issues #2, #3, #4)
- filter_pipeline.py: docstring added for always-on blocked instrument filter (review issue #6)
- tests/test_simulator_v6.py: ETH/USDT test updated to verify block instead of override (review issue #2)

## v7 Planning (complete)
- Analyst reports: strategy-analysis-and-backtest-plan.md, data-sources-guide.md, backtest-methodology-requirements.md
- Architect: produced TASKS_V7.md with 20+ tasks across 5 phases
- Phase 1: Data wiring fixes (TASK-V7-01..04) -- wire social/FRED/rates/GDELT
- Phase 2: Historical data backfill (TASK-V7-05..10) -- FRED, F&G, rates, ACLED, CoinMetrics
- Phase 3: Backtest engine enhancement (TASK-V7-11..17) -- full composite, walk-forward, stats
- Phase 4: Strategy implementation (TASK-V7-18..22) -- 5 specialized strategies
- Phase 5: Validation (TASK-V7-23..25) -- full backtest, comparison, decision

## In progress
- Nothing currently assigned to any agent

## Next
1. Commit R5 code review fixes (uncommitted changes)
2. Begin v7 Phase 1: TASK-V7-01 (wire social_sentiment into SentimentEngineV2)
3. Developer needed for v7 implementation

## Blockers
- None (v7 tasks are fully specified and ready for development)
