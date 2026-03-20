#!/usr/bin/env bash
# Run all v5 backtests sequentially (fixed: filter_flags actually applied).
# Picks up Phase 2 already running, then does Phase 3, Phase 4, Final, and re-runs Phase 1.
set -e

BASE_URL="http://localhost:8000"
DOCS_DIR="$(dirname "$0")/../docs"

wait_for_backtest() {
  local run_id="$1" label="$2"
  echo "[$(date '+%H:%M:%S')] Waiting for $label (run_id=$run_id)..."
  while true; do
    local resp status pct
    resp=$(curl -s "$BASE_URL/api/v2/backtest/$run_id/status")
    status=$(echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',''))" 2>/dev/null)
    pct=$(echo "$resp"   | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('progress_pct') or '')" 2>/dev/null)
    echo "[$(date '+%H:%M:%S')] $label — $status${pct:+ ${pct}%}"
    [ "$status" = "completed" ] && { echo "[$(date '+%H:%M:%S')] $label DONE"; return 0; }
    [ "$status" = "failed" ]    && { echo "[$(date '+%H:%M:%S')] $label FAILED"; return 1; }
    sleep 30
  done
}

start_backtest() {
  curl -s "$BASE_URL/api/v2/backtest/run" \
    -X POST -H "Content-Type: application/json" -d "$1" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])"
}

save_results() {
  local file="$1" label="$2" run_id="$3"
  curl -s "$BASE_URL/api/v2/backtest/$run_id/results" > /tmp/bt_results.json
  python3 - "$file" "$label" "$run_id" /tmp/bt_results.json <<'PYEOF'
import sys, json
out_file, label, run_id, json_file = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
with open(json_file) as f: d = json.load(f)

def fmt(v, dec=2):
    if v is None: return "—"
    try: return f"{float(v):.{dec}f}"
    except: return str(v)

p = d.get("params", {})
total = d.get("total_trades", 0) or 0
pnl   = float(d.get("total_pnl_usd") or 0)
acc   = float(p.get("account_size", 1000))
dur   = 24  # months Jan2024-Dec2025

lines = [
    f"# Backtest Results — {label}", "",
    f"**Дата:** {str(d.get('completed_at',''))[:10]} | **Run ID:** `{run_id}`",
    f"**Период:** {p.get('start_date')} — {p.get('end_date')} | **TF:** {p.get('timeframe')}",
    f"**Фильтры:** ranging={p.get('apply_ranging_filter')} d1={p.get('apply_d1_trend_filter')} vol={p.get('apply_volume_filter')} weekday={p.get('apply_weekday_filter')} momentum={p.get('apply_momentum_filter')} calendar={p.get('apply_calendar_filter')} min_score={p.get('min_composite_score')}",
    "",
    "## Ключевые метрики", "",
    "| Метрика | Значение |", "|---------|---------|",
    f"| Total trades | **{total}** |",
    f"| Trades/month | ~{total/dur:.1f} |",
    f"| Win rate | **{fmt(d.get('win_rate_pct'))}%** |",
    f"| Profit factor | **{fmt(d.get('profit_factor'))}** |",
    f"| Total PnL | **${pnl:+.2f} ({pnl/acc*100:.1f}%)** |",
    f"| Max drawdown | **{fmt(d.get('max_drawdown_pct'))}%** |",
    f"| Avg duration | {fmt(d.get('avg_duration_minutes'),0)} мин |",
    f"| LONG / SHORT | {d.get('long_count',0)} / {d.get('short_count',0)} |",
    f"| WR LONG | {fmt(d.get('win_rate_long_pct'))}% |",
    f"| WR SHORT | {fmt(d.get('win_rate_short_pct'))}% |",
    f"| SL hits | {d.get('sl_hit_count','—')} |",
    f"| TP hits | {d.get('tp_hit_count','—')} |",
    f"| MAE exits | {d.get('mae_exit_count','—')} |",
    f"| Time exits | {d.get('time_exit_count','—')} |",
    f"| Avg MAE % of SL | {fmt(d.get('avg_mae_pct_of_sl'))} |",
    "",
    "## По инструментам", "",
    "| Символ | Trades | Wins | WR% | PnL USD |",
    "|--------|--------|------|-----|---------|",
]
for sym, v in (d.get("by_symbol") or {}).items():
    t, w = v.get("trades",0), v.get("wins",0)
    wr = f"{w/t*100:.1f}" if t else "—"
    lines.append(f"| {sym} | {t} | {w} | {wr}% | {v.get('pnl_usd',0):+.2f} |")

lines += ["", "## По score bucket", "", "| Bucket | Trades | Wins | PnL USD |", "|--------|--------|------|---------|"]
for b, v in (d.get("by_score_bucket") or {}).items():
    lines.append(f"| {b} | {v.get('trades',0)} | {v.get('wins',0)} | {v.get('pnl_usd',0):+.2f} |")

lines += ["", "## По дням недели", "", "| День | Trades | Wins | PnL USD |", "|------|--------|------|---------|"]
dn = {0:"Пн",1:"Вт",2:"Ср",3:"Чт",4:"Пт"}
for k, v in sorted((d.get("by_weekday") or {}).items(), key=lambda x: int(x[0])):
    lines.append(f"| {dn.get(int(k),k)} | {v.get('trades',0)} | {v.get('wins',0)} | {v.get('pnl_usd',0):+.2f} |")

lines += ["", "## По режиму", "", "| Режим | Trades | Wins | PnL USD |", "|-------|--------|------|---------|"]
for r, v in (d.get("by_regime") or {}).items():
    lines.append(f"| {r} | {v.get('trades',0)} | {v.get('wins',0)} | {v.get('pnl_usd',0):+.2f} |")

with open(out_file, "w") as f: f.write("\n".join(lines) + "\n")
print(f"Saved {out_file}")
PYEOF
  python3 -c "
import json
with open('/tmp/bt_results.json') as f: d=json.load(f)
print(f\"  trades={d['total_trades']} WR={d['win_rate_pct']}% PF={d['profit_factor']} DD={d['max_drawdown_pct']}%\")
"
}

COMMON='"symbols":["EURUSD=X","GBPUSD=X","AUDUSD=X","BTC/USDT","ETH/USDT","SPY"],"timeframe":"H1","start_date":"2024-01-01","end_date":"2025-12-31","account_size":1000.0,"apply_slippage":true,"apply_swap":true'

# ── Phase 1 (re-run with fixed code) ─────────────────────────────────────────
echo "[$(date '+%H:%M:%S')] === Phase 1 (re-run, fixed filter_flags) ==="
P1_ID=$(start_backtest "{$COMMON,\"apply_ranging_filter\":true,\"apply_d1_trend_filter\":true,\"apply_volume_filter\":false,\"apply_weekday_filter\":false,\"apply_momentum_filter\":false,\"apply_calendar_filter\":false,\"min_composite_score\":15}")
echo "Phase 1 run_id=$P1_ID"
wait_for_backtest "$P1_ID" "Phase 1"
save_results "$DOCS_DIR/BACKTEST_RESULTS_V5_P1.md" "Phase 1 — SIM-25/26/27/28 (score≥15, ranging, D1 MA200, overrides)" "$P1_ID"

# ── Phase 2 (already running — wait for it) ───────────────────────────────────
P2_ID="4a7f95f0-6f4c-48ba-b10c-540d3910b8c5"
echo "[$(date '+%H:%M:%S')] === Phase 2 (already running) ==="
wait_for_backtest "$P2_ID" "Phase 2"
save_results "$DOCS_DIR/BACKTEST_RESULTS_V5_P2.md" "Phase 2 — +SIM-29/30/31/32 (volume, momentum, strength, weekday)" "$P2_ID"

# ── Phase 3 ───────────────────────────────────────────────────────────────────
echo "[$(date '+%H:%M:%S')] === Phase 3 ==="
P3_ID=$(start_backtest "{$COMMON,\"apply_ranging_filter\":true,\"apply_d1_trend_filter\":true,\"apply_volume_filter\":true,\"apply_weekday_filter\":true,\"apply_momentum_filter\":true,\"apply_calendar_filter\":true,\"min_composite_score\":15}")
echo "Phase 3 run_id=$P3_ID"
wait_for_backtest "$P3_ID" "Phase 3"
save_results "$DOCS_DIR/BACKTEST_RESULTS_V5_P3.md" "Phase 3 — +SIM-33..37 (calendar, breakeven, time exit, S/R, swap)" "$P3_ID"

# ── Phase 4 — same params as Phase 3 (DXY/F&G/FR/COT act on live data only) ──
echo "[$(date '+%H:%M:%S')] === Phase 4 ==="
P4_ID=$(start_backtest "{$COMMON,\"apply_ranging_filter\":true,\"apply_d1_trend_filter\":true,\"apply_volume_filter\":true,\"apply_weekday_filter\":true,\"apply_momentum_filter\":true,\"apply_calendar_filter\":true,\"min_composite_score\":15}")
echo "Phase 4 run_id=$P4_ID"
wait_for_backtest "$P4_ID" "Phase 4"
save_results "$DOCS_DIR/BACKTEST_RESULTS_V5_P4.md" "Phase 4 — +SIM-38..41 (DXY, F&G, funding, COT)" "$P4_ID"

# ── Final ─────────────────────────────────────────────────────────────────────
echo "[$(date '+%H:%M:%S')] === Final (all filters) ==="
FINAL_ID=$(start_backtest "{$COMMON,\"apply_ranging_filter\":true,\"apply_d1_trend_filter\":true,\"apply_volume_filter\":true,\"apply_weekday_filter\":true,\"apply_momentum_filter\":true,\"apply_calendar_filter\":true,\"min_composite_score\":15}")
echo "Final run_id=$FINAL_ID"
wait_for_backtest "$FINAL_ID" "Final"
save_results "$DOCS_DIR/BACKTEST_RESULTS_FINAL.md" "Final — все v5-фильтры активны" "$FINAL_ID"

echo ""
echo "=== ALL BACKTESTS COMPLETE ==="
