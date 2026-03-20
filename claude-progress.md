## Last updated: 2026-03-20

## Current goal
v5 complete. v6 planned — waiting for developer to begin implementation.

## Done (v5)
- All SIM-25..SIM-44 code implemented (Phases 1-5)
- Tasks 1-9: filter pipeline, session/volume/momentum/calendar, O(n²)→O(n) optimization
- Code review passed, all hotfixes applied
- Full regression: 216 tests passed
- ALL 4 backtest phases completed

## v5 Key Results (verified from JSON, NOT from FINAL.md)
- Baseline: 38 trades, PF 0.60, -$1054
- Phase 1: 52 trades, PF 0.72, -$236
- Phase 2: 33 trades, PF 2.01, +$253.73
- Phase 3: 33 trades, PF 2.01, +$253.73 (IDENTICAL to Phase 2 — calendar filter is no-op)
- BACKTEST_RESULTS_FINAL.md contains incorrect data (analyst finding)

## v6 Planning (complete)
- Market analyst: produced 15 requirements (REQ-V6-001..015)
  - `analyst-requirements/backtest-final-analysis-requirements.md`
  - `analyst-reports/backtest-v5-final-analysis.md`
- Architect: produced spec and tasks
  - `docs/SPEC_SIMULATOR_V6.md`
  - `docs/TASKS_V6.md`

## Key v6 Issues (from analyst)
1. [P0] FINAL.md report data doesn't match actual JSON — need automated reporting
2. [P0] Composite score scaling: threshold 15 requires ta_score>=33.3 (unreachable for most)
3. [P0] BTC/USDT blocked by restrictive regime + score override combo
4. [P1] GBPUSD 0 trades (min_score=20 override too high)
5. [P1] D1 trend filter always passes (d1_rows=[] hardcoded)
6. [P1] Calendar filter is no-op (Phase 2 === Phase 3)
7. [P1] SHORT signals net negative (-$72)

## Next
- Developer: begin TASK-V6-01 (automated report generation)
- Commit all v5 changes before starting v6
