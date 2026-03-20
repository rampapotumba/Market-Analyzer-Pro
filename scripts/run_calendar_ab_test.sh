#!/usr/bin/env bash
# CAL3-07: Calendar filter A/B comparison backtest.
#
# Runs two identical backtests: one with apply_calendar_filter=true
# and one with apply_calendar_filter=false.
# Records PF/WR/PnL/DD for each and prints a summary comparison.
#
# Usage: ./scripts/run_calendar_ab_test.sh
# Output: docs/BACKTEST_RESULTS_V6_CAL3_CALENDAR_AB.md

set -e

BASE_URL="${BACKTEST_BASE_URL:-http://localhost:8000}"
DOCS_DIR="$(dirname "$0")/../docs"
OUTPUT_FILE="$DOCS_DIR/BACKTEST_RESULTS_V6_CAL3_CALENDAR_AB.md"

# Backtest parameters (same for both runs)
START_DATE="2024-01-01"
END_DATE="2025-06-30"
SYMBOLS='["EURUSD=X","GBPUSD=X","USDJPY=X","USDCHF=X","AUDUSD=X","NZDUSD=X","BTC/USDT","ETH/USDT","GC=F","SPY"]'
TIMEFRAME="H1"
ACCOUNT_SIZE="1000"

# ── Helpers ───────────────────────────────────────────────────────────────────

wait_for_backtest() {
    local run_id="$1"
    local label="$2"
    echo "[$(date '+%H:%M:%S')] Waiting for $label (run_id=$run_id)..."
    while true; do
        local resp
        resp=$(curl -s "$BASE_URL/api/v2/backtest/$run_id/status")
        local status pct
        status=$(echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',''))" 2>/dev/null)
        pct=$(echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('progress_pct') or '')" 2>/dev/null)
        echo "[$(date '+%H:%M:%S')] $label — status=$status${pct:+ progress=${pct}%}"
        if [ "$status" = "completed" ]; then echo "[$(date '+%H:%M:%S')] $label DONE"; return 0; fi
        if [ "$status" = "failed" ]; then echo "[$(date '+%H:%M:%S')] $label FAILED"; return 1; fi
        sleep 30
    done
}

start_backtest() {
    local payload="$1"
    curl -s "$BASE_URL/api/v2/backtest/run" \
        -X POST -H "Content-Type: application/json" \
        -d "$payload" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('run_id',''))"
}

get_summary_field() {
    local run_id="$1"
    local field="$2"
    curl -s "$BASE_URL/api/v2/backtest/$run_id" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('summary',{}).get('$field','N/A'))"
}

# ── Run A: calendar ON ────────────────────────────────────────────────────────

echo ""
echo "=== CAL3-07: Calendar A/B Test ==="
echo "Start: $(date)"
echo ""
echo "[A] Starting backtest with apply_calendar_filter=true..."

PAYLOAD_A=$(python3 -c "
import json
print(json.dumps({
    'symbols': $SYMBOLS,
    'timeframe': '$TIMEFRAME',
    'start_date': '$START_DATE',
    'end_date': '$END_DATE',
    'account_size': $ACCOUNT_SIZE,
    'apply_calendar_filter': True,
}))
")

RUN_A=$(start_backtest "$PAYLOAD_A")
if [ -z "$RUN_A" ]; then echo "ERROR: Failed to start backtest A"; exit 1; fi
echo "[A] run_id=$RUN_A"
wait_for_backtest "$RUN_A" "Calendar ON"

# ── Run B: calendar OFF ───────────────────────────────────────────────────────

echo ""
echo "[B] Starting backtest with apply_calendar_filter=false..."

PAYLOAD_B=$(python3 -c "
import json
print(json.dumps({
    'symbols': $SYMBOLS,
    'timeframe': '$TIMEFRAME',
    'start_date': '$START_DATE',
    'end_date': '$END_DATE',
    'account_size': $ACCOUNT_SIZE,
    'apply_calendar_filter': False,
}))
")

RUN_B=$(start_backtest "$PAYLOAD_B")
if [ -z "$RUN_B" ]; then echo "ERROR: Failed to start backtest B"; exit 1; fi
echo "[B] run_id=$RUN_B"
wait_for_backtest "$RUN_B" "Calendar OFF"

# ── Collect results ───────────────────────────────────────────────────────────

echo ""
echo "=== Collecting results ==="

collect_summary() {
    local run_id="$1"
    curl -s "$BASE_URL/api/v2/backtest/$run_id" | python3 -c "
import sys, json
d = json.load(sys.stdin)
s = d.get('summary', {})
vs = s.get('viability_assessment', {})
print(f\"total_trades:    {s.get('total_trades', 'N/A')}\")
print(f\"win_rate_pct:    {s.get('win_rate_pct', 'N/A')}\")
print(f\"profit_factor:   {s.get('profit_factor', 'N/A')}\")
print(f\"total_pnl_usd:   {s.get('total_pnl_usd', 'N/A')}\")
print(f\"max_drawdown_pct:{s.get('max_drawdown_pct', 'N/A')}\")
print(f\"time_exit_count: {s.get('time_exit_count', 'N/A')}\")
print(f\"viability:       {vs.get('overall', 'N/A')} blocking={vs.get('blocking_factors', [])}\")
fs = s.get('filter_stats', {})
print(f\"calendar_blocks: {fs.get('rejected_by_calendar_filter', 0)}\")
"
}

echo ""
echo "--- [A] Calendar ON (run_id=$RUN_A) ---"
SUMMARY_A=$(collect_summary "$RUN_A")
echo "$SUMMARY_A"

echo ""
echo "--- [B] Calendar OFF (run_id=$RUN_B) ---"
SUMMARY_B=$(collect_summary "$RUN_B")
echo "$SUMMARY_B"

# ── Compute delta and write report ────────────────────────────────────────────

TODAY=$(date '+%Y-%m-%d')

python3 - "$RUN_A" "$RUN_B" "$BASE_URL" "$OUTPUT_FILE" "$TODAY" << 'PYEOF'
import sys, json, urllib.request

run_a, run_b, base_url, output_file, today = sys.argv[1:]

def fetch(run_id):
    url = f"{base_url}/api/v2/backtest/{run_id}"
    with urllib.request.urlopen(url) as resp:
        return json.load(resp)

d_a = fetch(run_a)
d_b = fetch(run_b)
s_a = d_a.get("summary", {})
s_b = d_b.get("summary", {})
fs_a = s_a.get("filter_stats", {})
vs_a = s_a.get("viability_assessment", {})
vs_b = s_b.get("viability_assessment", {})

def fmt(v):
    return f"{v:.4f}" if isinstance(v, float) else str(v) if v is not None else "N/A"

pf_a = s_a.get("profit_factor") or 0
pf_b = s_b.get("profit_factor") or 0
pnl_a = float(s_a.get("total_pnl_usd") or 0)
pnl_b = float(s_b.get("total_pnl_usd") or 0)
pf_delta = float(pf_a or 0) - float(pf_b or 0)
pnl_delta = pnl_a - pnl_b
calendar_blocks = fs_a.get("rejected_by_calendar_filter", 0)

# Decision recommendation
if pf_delta >= 0.05:
    recommendation = "KEEP calendar filter — improves PF by >= 0.05"
elif pf_delta >= 0:
    recommendation = "MARGINAL — calendar filter provides minimal benefit (< 0.05 PF improvement). Consider reducing ±4h window to ±2h."
elif pf_delta >= -0.05:
    recommendation = "REMOVE or REDUCE window — calendar filter slightly hurts PF. Reduce to ±2h or remove."
else:
    recommendation = "REMOVE calendar filter — calendar filter significantly hurts PF (delta < -0.05)."

report = f"""# Calendar A/B Test — {today}

## Setup

- Period: {s_a.get('params', {}).get('start_date', '')} – {s_a.get('params', {}).get('end_date', '')}
- Timeframe: H1
- Account: $1,000
- All other filters: identical

## Results

| Metric | Calendar ON | Calendar OFF | Delta (ON - OFF) |
|--------|-------------|--------------|------------------|
| Trades | {s_a.get('total_trades', 'N/A')} | {s_b.get('total_trades', 'N/A')} | {(s_a.get('total_trades') or 0) - (s_b.get('total_trades') or 0)} |
| Win Rate % | {fmt(s_a.get('win_rate_pct', 0))} | {fmt(s_b.get('win_rate_pct', 0))} | {fmt(float(s_a.get('win_rate_pct') or 0) - float(s_b.get('win_rate_pct') or 0))} |
| Profit Factor | {fmt(pf_a)} | {fmt(pf_b)} | {fmt(pf_delta)} |
| Total PnL USD | {fmt(pnl_a)} | {fmt(pnl_b)} | {fmt(pnl_delta)} |
| Max DD % | {fmt(s_a.get('max_drawdown_pct', 0))} | {fmt(s_b.get('max_drawdown_pct', 0))} | — |
| Time Exit Count | {s_a.get('time_exit_count', 'N/A')} | {s_b.get('time_exit_count', 'N/A')} | — |
| Calendar Blocked | {calendar_blocks} | 0 | — |

## Viability Assessment

| | Calendar ON | Calendar OFF |
|-|-------------|--------------|
| Overall | {vs_a.get('overall', 'N/A')} | {vs_b.get('overall', 'N/A')} |
| Blocking factors | {', '.join(vs_a.get('blocking_factors', [])) or 'none'} | {', '.join(vs_b.get('blocking_factors', [])) or 'none'} |

## Recommendation

**{recommendation}**

### Decision criteria used

- PF delta >= +0.05 → KEEP
- PF delta 0..+0.05 → MARGINAL (reduce window)
- PF delta < 0 → REMOVE or REDUCE

## Run IDs

- Calendar ON:  `{run_a}`
- Calendar OFF: `{run_b}`
"""

with open(output_file, "w") as f:
    f.write(report)
print(f"Report written to: {output_file}")
print(f"Recommendation: {recommendation}")
PYEOF

echo ""
echo "=== Done. Report: $OUTPUT_FILE ==="
echo "End: $(date)"
