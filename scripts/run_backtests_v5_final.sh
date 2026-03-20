#!/usr/bin/env bash
# Final backtest run for v5 — 4 phases from Baseline to All Filters ON.
# Commit: c40e6702e2aca05a7f8c44495fcef7622fb4cbb6
#
# Usage: bash scripts/run_backtests_v5_final.sh

set -euo pipefail

BASE_URL="http://localhost:8000"
RESULTS_DIR="/tmp"
GIT_COMMIT="c40e6702e2aca05a7f8c44495fcef7622fb4cbb6"

echo "=== Market Analyzer Pro — Final Backtest Run ==="
echo "Git commit: $GIT_COMMIT"
echo "Started: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# ── Helpers ──────────────────────────────────────────────────────────────────

start_backtest() {
    local payload="$1"
    local run_id
    run_id=$(curl -sf "$BASE_URL/api/v2/backtest/run" \
        -X POST \
        -H "Content-Type: application/json" \
        -d "$payload" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['run_id'])")
    echo "$run_id"
}

wait_for_backtest() {
    local run_id="$1"
    local label="$2"
    local max_wait_seconds=21600  # 6 hours max (full 2-year backtest is slow)
    local elapsed=0
    local interval=30

    echo "[$(date '+%H:%M:%S')] Waiting for $label (run_id=$run_id) ..."

    while [ "$elapsed" -lt "$max_wait_seconds" ]; do
        local resp status pct
        resp=$(curl -sf "$BASE_URL/api/v2/backtest/$run_id/status")
        status=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "error")
        pct=$(echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); v=d.get('progress_pct'); print(f'{v}%' if v is not None else '')" 2>/dev/null || echo "")

        echo "[$(date '+%H:%M:%S')] $label — status=$status${pct:+ progress=$pct}"

        if [ "$status" = "completed" ]; then
            echo "[$(date '+%H:%M:%S')] $label DONE"
            return 0
        fi
        if [ "$status" = "failed" ]; then
            echo "[$(date '+%H:%M:%S')] $label FAILED"
            return 1
        fi

        sleep "$interval"
        elapsed=$((elapsed + interval))
    done

    echo "[$(date '+%H:%M:%S')] $label TIMEOUT after ${max_wait_seconds}s"
    return 1
}

fetch_results() {
    local run_id="$1"
    local out_file="$2"
    curl -sf "$BASE_URL/api/v2/backtest/$run_id/results" > "$out_file"
}

print_summary() {
    local label="$1"
    local json_file="$2"
    python3 - "$label" "$json_file" <<'PYEOF'
import sys, json

label = sys.argv[1]
with open(sys.argv[2]) as f:
    d = json.load(f)

trades = d.get("total_trades") or 0
wr     = d.get("win_rate_pct") or 0.0
pf     = d.get("profit_factor") or 0.0
dd     = d.get("max_drawdown_pct") or 0.0
pnl    = d.get("total_pnl_usd") or 0.0

print(f"{label}: trades={trades} WR={float(wr):.1f}% PF={float(pf):.2f} DD={float(dd):.2f}% PnL=${float(pnl):+.2f}")
PYEOF
}

# ── Common symbols ────────────────────────────────────────────────────────────

SYMBOLS='["EURUSD=X","GBPUSD=X","AUDUSD=X","BTC/USDT","ETH/USDT","SPY"]'
PERIOD='"start_date":"2024-01-01","end_date":"2025-12-31"'
BASE_PARAMS='"timeframe":"H1","account_size":1000.0,"apply_slippage":true,"apply_swap":true'

# ── Phase: Baseline — ALL filters OFF ────────────────────────────────────────

echo "[$(date '+%H:%M:%S')] === Baseline: ALL filters OFF ==="

BASELINE_PAYLOAD=$(cat <<EOF
{
  "symbols": $SYMBOLS,
  $BASE_PARAMS,
  $PERIOD,
  "apply_ranging_filter": false,
  "apply_d1_trend_filter": false,
  "apply_volume_filter": false,
  "apply_weekday_filter": false,
  "apply_momentum_filter": false,
  "apply_calendar_filter": false,
  "apply_session_filter": false
}
EOF
)

BASELINE_ID=$(start_backtest "$BASELINE_PAYLOAD")
echo "Baseline run_id=$BASELINE_ID"
wait_for_backtest "$BASELINE_ID" "Baseline"
fetch_results "$BASELINE_ID" "$RESULTS_DIR/backtest_results_baseline.json"
print_summary "Baseline (all filters OFF)" "$RESULTS_DIR/backtest_results_baseline.json"

echo ""

# ── Phase 1 — Critical filters ON ────────────────────────────────────────────

echo "[$(date '+%H:%M:%S')] === Phase 1: Critical filters (ranging, D1, session, score≥15) ==="

P1_PAYLOAD=$(cat <<EOF
{
  "symbols": $SYMBOLS,
  $BASE_PARAMS,
  $PERIOD,
  "apply_ranging_filter": true,
  "apply_d1_trend_filter": true,
  "apply_volume_filter": false,
  "apply_weekday_filter": false,
  "apply_momentum_filter": false,
  "apply_calendar_filter": false,
  "apply_session_filter": true,
  "min_composite_score": 15
}
EOF
)

P1_ID=$(start_backtest "$P1_PAYLOAD")
echo "Phase 1 run_id=$P1_ID"
wait_for_backtest "$P1_ID" "Phase 1"
fetch_results "$P1_ID" "$RESULTS_DIR/backtest_results_phase1.json"
print_summary "Phase 1 (critical filters)" "$RESULTS_DIR/backtest_results_phase1.json"

echo ""

# ── Phase 2 — Phase 1 + structural filters ────────────────────────────────────

echo "[$(date '+%H:%M:%S')] === Phase 2: Phase 1 + structural filters (volume, weekday, momentum) ==="

P2_PAYLOAD=$(cat <<EOF
{
  "symbols": $SYMBOLS,
  $BASE_PARAMS,
  $PERIOD,
  "apply_ranging_filter": true,
  "apply_d1_trend_filter": true,
  "apply_volume_filter": true,
  "apply_weekday_filter": true,
  "apply_momentum_filter": true,
  "apply_calendar_filter": false,
  "apply_session_filter": true,
  "min_composite_score": 15
}
EOF
)

P2_ID=$(start_backtest "$P2_PAYLOAD")
echo "Phase 2 run_id=$P2_ID"
wait_for_backtest "$P2_ID" "Phase 2"
fetch_results "$P2_ID" "$RESULTS_DIR/backtest_results_phase2.json"
print_summary "Phase 2 (+ structural filters)" "$RESULTS_DIR/backtest_results_phase2.json"

echo ""

# ── Phase 3 — ALL filters ON ──────────────────────────────────────────────────

echo "[$(date '+%H:%M:%S')] === Phase 3: ALL filters ON (+ calendar, session) ==="

P3_PAYLOAD=$(cat <<EOF
{
  "symbols": $SYMBOLS,
  $BASE_PARAMS,
  $PERIOD,
  "apply_ranging_filter": true,
  "apply_d1_trend_filter": true,
  "apply_volume_filter": true,
  "apply_weekday_filter": true,
  "apply_momentum_filter": true,
  "apply_calendar_filter": true,
  "apply_session_filter": true,
  "min_composite_score": 15
}
EOF
)

P3_ID=$(start_backtest "$P3_PAYLOAD")
echo "Phase 3 run_id=$P3_ID"
wait_for_backtest "$P3_ID" "Phase 3"
fetch_results "$P3_ID" "$RESULTS_DIR/backtest_results_phase3.json"
print_summary "Phase 3 (all filters ON)" "$RESULTS_DIR/backtest_results_phase3.json"

echo ""

# ── Summary ───────────────────────────────────────────────────────────────────

echo "=== SUMMARY ==="
echo ""
print_summary "Baseline (all filters OFF)" "$RESULTS_DIR/backtest_results_baseline.json"
print_summary "Phase 1 (critical filters)" "$RESULTS_DIR/backtest_results_phase1.json"
print_summary "Phase 2 (+ structural filters)" "$RESULTS_DIR/backtest_results_phase2.json"
print_summary "Phase 3 (all filters ON)" "$RESULTS_DIR/backtest_results_phase3.json"

# ── Monotonicity check ────────────────────────────────────────────────────────

echo ""
echo "=== MONOTONICITY CHECK (trades must decrease each phase) ==="
python3 - \
    "$RESULTS_DIR/backtest_results_baseline.json" \
    "$RESULTS_DIR/backtest_results_phase1.json" \
    "$RESULTS_DIR/backtest_results_phase2.json" \
    "$RESULTS_DIR/backtest_results_phase3.json" <<'PYEOF'
import sys, json

labels = ["Baseline", "Phase 1", "Phase 2", "Phase 3"]
files = sys.argv[1:]

counts = []
for f, label in zip(files, labels):
    with open(f) as fp:
        d = json.load(fp)
    t = d.get("total_trades") or 0
    counts.append((label, t))
    print(f"  {label}: {t} trades")

print("")
ok = True
for i in range(1, len(counts)):
    prev_label, prev_n = counts[i - 1]
    curr_label, curr_n = counts[i]
    if curr_n > prev_n:
        print(f"WARNING: {curr_label} ({curr_n}) > {prev_label} ({prev_n}) — monotonicity VIOLATED")
        ok = False
    else:
        print(f"OK: {curr_label} ({curr_n}) <= {prev_label} ({prev_n})")

if ok:
    print("")
    print("All checks passed: trades are monotonically non-increasing.")
else:
    print("")
    print("MONOTONICITY VIOLATION DETECTED — review filter logic.")
PYEOF

echo ""
echo "Results saved to:"
echo "  $RESULTS_DIR/backtest_results_baseline.json"
echo "  $RESULTS_DIR/backtest_results_phase1.json"
echo "  $RESULTS_DIR/backtest_results_phase2.json"
echo "  $RESULTS_DIR/backtest_results_phase3.json"
echo ""
echo "Completed: $(date '+%Y-%m-%d %H:%M:%S')"
