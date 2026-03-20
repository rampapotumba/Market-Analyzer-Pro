#!/usr/bin/env bash
# Run all v5 backtests sequentially and save results to docs/
set -e

BASE_URL="http://localhost:8000"
DOCS_DIR="$(dirname "$0")/../docs"

# ── Helpers ──────────────────────────────────────────────────────────────────

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
    if [ "$status" = "failed" ];    then echo "[$(date '+%H:%M:%S')] $label FAILED"; return 1; fi
    sleep 30
  done
}

start_backtest() {
  local payload="$1"
  curl -s "$BASE_URL/api/v2/backtest/run" \
    -X POST -H "Content-Type: application/json" \
    -d "$payload" | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])"
}

fetch_results() {
  local run_id="$1"
  curl -s "$BASE_URL/api/v2/backtest/$run_id/results"
}

# ── Summary markdown builder ──────────────────────────────────────────────────

write_results_md() {
  local file="$1"
  local label="$2"
  local run_id="$3"
  local json="$4"

  python3 - "$file" "$label" "$run_id" <<'PYEOF'
import sys, json

out_file, label, run_id = sys.argv[1], sys.argv[2], sys.argv[3]
raw = sys.stdin.read()
d = json.loads(raw)

def fmt(v, decimals=2):
    if v is None: return "—"
    return f"{float(v):.{decimals}f}"

params = d.get("params", {})
total = d.get("total_trades", 0)
duration_months = 24  # Jan 2024 – Dec 2025
trades_per_month = total / duration_months if duration_months else 0

lines = [
    f"# Backtest Results — {label}",
    "",
    f"**Дата запуска:** {d.get('completed_at','')[:10]}",
    f"**Run ID:** `{run_id}`",
    f"**Период:** {params.get('start_date')} — {params.get('end_date')}",
    f"**Таймфрейм:** {params.get('timeframe')} | **Счёт:** ${float(params.get('account_size',1000)):.0f} | **Slippage:** {'✓' if params.get('apply_slippage') else '✗'} | **Swap:** {'✓' if params.get('apply_swap') else '✗'}",
    "",
    "## Ключевые метрики",
    "",
    "| Метрика | Значение |",
    "|---------|---------|",
    f"| Total trades | **{total}** |",
    f"| Trades/month | ~{trades_per_month:.1f} |",
    f"| Win rate | **{fmt(d.get('win_rate_pct'))}%** |",
    f"| Profit factor | **{fmt(d.get('profit_factor'))}** |",
    f"| Total PnL | **+${fmt(d.get('total_pnl_usd'))} ({fmt(float(d.get('total_pnl_usd',0))/10, 2)}%)** |",
    f"| Max drawdown | **{fmt(d.get('max_drawdown_pct'))}%** |",
    f"| Avg duration | {fmt(d.get('avg_duration_minutes'),0)} мин |",
    f"| LONG count | {d.get('long_count',0)} ({fmt(d.get('long_count',0)/total*100) if total else '—'}%) |",
    f"| SHORT count | {d.get('short_count',0)} ({fmt(d.get('short_count',0)/total*100) if total else '—'}%) |",
    f"| SL hits | {d.get('sl_hit_count','—')} |",
    f"| TP hits | {d.get('tp_hit_count','—')} |",
    f"| MAE exits | {d.get('mae_exit_count','—')} |",
    f"| Time exits | {d.get('time_exit_count','—')} |",
    f"| Win rate LONG | {fmt(d.get('win_rate_long_pct'))}% |",
    f"| Win rate SHORT | {fmt(d.get('win_rate_short_pct'))}% |",
    f"| Avg win duration | {fmt(d.get('avg_win_duration_minutes'),0)} мин |",
    f"| Avg loss duration | {fmt(d.get('avg_loss_duration_minutes'),0)} мин |",
    f"| Avg MAE % of SL | {fmt(d.get('avg_mae_pct_of_sl'))} |",
    "",
    "## По инструментам",
    "",
    "| Символ | Trades | Wins | WR% | PnL USD |",
    "|--------|--------|------|-----|---------|",
]
for sym, v in (d.get("by_symbol") or {}).items():
    t, w = v.get("trades",0), v.get("wins",0)
    wr = f"{w/t*100:.1f}" if t else "—"
    lines.append(f"| {sym} | {t} | {w} | {wr}% | {v.get('pnl_usd',0):.2f} |")

lines += [
    "",
    "## По score bucket",
    "",
    "| Bucket | Trades | Wins | PnL USD |",
    "|--------|--------|------|---------|",
]
for bucket, v in (d.get("by_score_bucket") or {}).items():
    lines.append(f"| {bucket} | {v.get('trades',0)} | {v.get('wins',0)} | {v.get('pnl_usd',0):.2f} |")

lines += [
    "",
    "## По дням недели",
    "",
    "| День | Trades | Wins | PnL USD |",
    "|------|--------|------|---------|",
]
day_names = {0:"Пн",1:"Вт",2:"Ср",3:"Чт",4:"Пт"}
for day_k, v in sorted((d.get("by_weekday") or {}).items(), key=lambda x: int(x[0])):
    lines.append(f"| {day_names.get(int(day_k), day_k)} ({day_k}) | {v.get('trades',0)} | {v.get('wins',0)} | {v.get('pnl_usd',0):.2f} |")

lines += [
    "",
    "## По режиму",
    "",
    "| Режим | Trades | Wins | PnL USD |",
    "|-------|--------|------|---------|",
]
for regime, v in (d.get("by_regime") or {}).items():
    lines.append(f"| {regime} | {v.get('trades',0)} | {v.get('wins',0)} | {v.get('pnl_usd',0):.2f} |")

with open(out_file, "w") as f:
    f.write("\n".join(lines) + "\n")
print(f"Wrote {out_file}")
PYEOF
  echo "$json" | python3 /dev/stdin
}

# We need a slightly different approach — pass JSON via file
save_results() {
  local file="$1"
  local label="$2"
  local run_id="$3"
  local json="$4"

  echo "$json" > /tmp/bt_results.json
  python3 - "$file" "$label" "$run_id" /tmp/bt_results.json <<'PYEOF'
import sys, json

out_file, label, run_id, json_file = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
with open(json_file) as f:
    d = json.load(f)

def fmt(v, decimals=2):
    if v is None: return "—"
    try: return f"{float(v):.{decimals}f}"
    except: return str(v)

params = d.get("params", {})
total = d.get("total_trades", 0) or 0
duration_months = 24
trades_per_month = total / duration_months if duration_months else 0
pnl = float(d.get("total_pnl_usd") or 0)
account = float(params.get("account_size", 1000))

lines = [
    f"# Backtest Results — {label}",
    "",
    f"**Дата запуска:** {str(d.get('completed_at',''))[:10]}",
    f"**Run ID:** `{run_id}`",
    f"**Период:** {params.get('start_date')} — {params.get('end_date')}",
    f"**Таймфрейм:** {params.get('timeframe')} | **Счёт:** ${account:.0f} | **Slippage:** {'✓' if params.get('apply_slippage') else '✗'} | **Swap:** {'✓' if params.get('apply_swap') else '✗'}",
    "",
    "## Ключевые метрики",
    "",
    "| Метрика | Значение |",
    "|---------|---------|",
    f"| Total trades | **{total}** |",
    f"| Trades/month | ~{trades_per_month:.1f} |",
    f"| Win rate | **{fmt(d.get('win_rate_pct'))}%** |",
    f"| Profit factor | **{fmt(d.get('profit_factor'))}** |",
    f"| Total PnL | **${pnl:+.2f} ({pnl/account*100:.1f}%)** |",
    f"| Max drawdown | **{fmt(d.get('max_drawdown_pct'))}%** |",
    f"| Avg duration | {fmt(d.get('avg_duration_minutes'),0)} мин |",
    f"| LONG count | {d.get('long_count',0)} ({d.get('long_count',0)/total*100:.1f}% if total else '—'%) |" if total else f"| LONG count | {d.get('long_count',0)} |",
    f"| SHORT count | {d.get('short_count',0)} ({d.get('short_count',0)/total*100:.1f}% if total else '—'%) |" if total else f"| SHORT count | {d.get('short_count',0)} |",
    f"| SL hits | {d.get('sl_hit_count','—')} |",
    f"| TP hits | {d.get('tp_hit_count','—')} |",
    f"| MAE exits | {d.get('mae_exit_count','—')} |",
    f"| Time exits | {d.get('time_exit_count','—')} |",
    f"| Win rate LONG | {fmt(d.get('win_rate_long_pct'))}% |",
    f"| Win rate SHORT | {fmt(d.get('win_rate_short_pct'))}% |",
    f"| Avg win duration | {fmt(d.get('avg_win_duration_minutes'),0)} мин |",
    f"| Avg loss duration | {fmt(d.get('avg_loss_duration_minutes'),0)} мин |",
    f"| Avg MAE % of SL | {fmt(d.get('avg_mae_pct_of_sl'))} |",
    "",
    "## По инструментам",
    "",
    "| Символ | Trades | Wins | WR% | PnL USD |",
    "|--------|--------|------|-----|---------|",
]
for sym, v in (d.get("by_symbol") or {}).items():
    t, w = v.get("trades",0), v.get("wins",0)
    wr = f"{w/t*100:.1f}" if t else "—"
    lines.append(f"| {sym} | {t} | {w} | {wr}% | {v.get('pnl_usd',0):.2f} |")

lines += [
    "",
    "## По score bucket",
    "",
    "| Bucket | Trades | Wins | PnL USD |",
    "|--------|--------|------|---------|",
]
for bucket, v in (d.get("by_score_bucket") or {}).items():
    lines.append(f"| {bucket} | {v.get('trades',0)} | {v.get('wins',0)} | {v.get('pnl_usd',0):.2f} |")

lines += [
    "",
    "## По дням недели",
    "",
    "| День | Trades | Wins | PnL USD |",
    "|------|--------|------|---------|",
]
day_names = {0:"Пн",1:"Вт",2:"Ср",3:"Чт",4:"Пт"}
for day_k, v in sorted((d.get("by_weekday") or {}).items(), key=lambda x: int(x[0])):
    lines.append(f"| {day_names.get(int(day_k), day_k)} ({day_k}) | {v.get('trades',0)} | {v.get('wins',0)} | {v.get('pnl_usd',0):.2f} |")

lines += [
    "",
    "## По режиму",
    "",
    "| Режим | Trades | Wins | PnL USD |",
    "|-------|--------|------|---------|",
]
for regime, v in (d.get("by_regime") or {}).items():
    lines.append(f"| {regime} | {v.get('trades',0)} | {v.get('wins',0)} | {v.get('pnl_usd',0):.2f} |")

with open(out_file, "w") as f:
    f.write("\n".join(lines) + "\n")
print(f"Saved {out_file}")
PYEOF
}

# ── Phase 1 ── already running ────────────────────────────────────────────────
P1_RUN_ID="20882e43-9f51-4e60-82b0-3ba33f7ba05c"
wait_for_backtest "$P1_RUN_ID" "Phase 1"
P1_JSON=$(fetch_results "$P1_RUN_ID")
save_results "$DOCS_DIR/BACKTEST_RESULTS_V5_P1.md" "Phase 1 — SIM-25/26/27/28 (score≥15, ranging block, D1 MA200, overrides)" "$P1_RUN_ID" "$P1_JSON"
echo "$P1_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Phase 1: trades={d[\"total_trades\"]} WR={d[\"win_rate_pct\"]}% PF={d[\"profit_factor\"]} DD={d[\"max_drawdown_pct\"]}%')"

# ── Phase 2 ───────────────────────────────────────────────────────────────────
echo "[$(date '+%H:%M:%S')] Starting Phase 2 backtest..."
P2_RUN_ID=$(start_backtest '{
  "symbols": ["EURUSD=X","GBPUSD=X","AUDUSD=X","BTC/USDT","ETH/USDT","SPY"],
  "timeframe": "H1", "start_date": "2024-01-01", "end_date": "2025-12-31",
  "account_size": 1000.0, "apply_slippage": true, "apply_swap": true,
  "apply_ranging_filter": true, "apply_d1_trend_filter": true,
  "apply_volume_filter": true, "apply_weekday_filter": true,
  "apply_momentum_filter": true, "apply_calendar_filter": false,
  "min_composite_score": 15
}')
echo "Phase 2 run_id=$P2_RUN_ID"
wait_for_backtest "$P2_RUN_ID" "Phase 2"
P2_JSON=$(fetch_results "$P2_RUN_ID")
save_results "$DOCS_DIR/BACKTEST_RESULTS_V5_P2.md" "Phase 2 — +SIM-29/30/31/32 (volume, momentum, strength, weekday)" "$P2_RUN_ID" "$P2_JSON"
echo "$P2_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Phase 2: trades={d[\"total_trades\"]} WR={d[\"win_rate_pct\"]}% PF={d[\"profit_factor\"]} DD={d[\"max_drawdown_pct\"]}%')"

# ── Phase 3 ───────────────────────────────────────────────────────────────────
echo "[$(date '+%H:%M:%S')] Starting Phase 3 backtest..."
P3_RUN_ID=$(start_backtest '{
  "symbols": ["EURUSD=X","GBPUSD=X","AUDUSD=X","BTC/USDT","ETH/USDT","SPY"],
  "timeframe": "H1", "start_date": "2024-01-01", "end_date": "2025-12-31",
  "account_size": 1000.0, "apply_slippage": true, "apply_swap": true,
  "apply_ranging_filter": true, "apply_d1_trend_filter": true,
  "apply_volume_filter": true, "apply_weekday_filter": true,
  "apply_momentum_filter": true, "apply_calendar_filter": true,
  "min_composite_score": 15
}')
echo "Phase 3 run_id=$P3_RUN_ID"
wait_for_backtest "$P3_RUN_ID" "Phase 3"
P3_JSON=$(fetch_results "$P3_RUN_ID")
save_results "$DOCS_DIR/BACKTEST_RESULTS_V5_P3.md" "Phase 3 — +SIM-33/34/35/36/37 (calendar, breakeven, time exit, S/R, swap)" "$P3_RUN_ID" "$P3_JSON"
echo "$P3_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Phase 3: trades={d[\"total_trades\"]} WR={d[\"win_rate_pct\"]}% PF={d[\"profit_factor\"]} DD={d[\"max_drawdown_pct\"]}%')"

# ── Phase 4 ───────────────────────────────────────────────────────────────────
echo "[$(date '+%H:%M:%S')] Starting Phase 4 backtest..."
P4_RUN_ID=$(start_backtest '{
  "symbols": ["EURUSD=X","GBPUSD=X","AUDUSD=X","BTC/USDT","ETH/USDT","SPY"],
  "timeframe": "H1", "start_date": "2024-01-01", "end_date": "2025-12-31",
  "account_size": 1000.0, "apply_slippage": true, "apply_swap": true,
  "apply_ranging_filter": true, "apply_d1_trend_filter": true,
  "apply_volume_filter": true, "apply_weekday_filter": true,
  "apply_momentum_filter": true, "apply_calendar_filter": true,
  "min_composite_score": 15
}')
echo "Phase 4 run_id=$P4_RUN_ID"
wait_for_backtest "$P4_RUN_ID" "Phase 4"
P4_JSON=$(fetch_results "$P4_RUN_ID")
save_results "$DOCS_DIR/BACKTEST_RESULTS_V5_P4.md" "Phase 4 — +SIM-38/39/40/41 (DXY, Fear&Greed, funding rate, COT)" "$P4_RUN_ID" "$P4_JSON"
echo "$P4_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Phase 4: trades={d[\"total_trades\"]} WR={d[\"win_rate_pct\"]}% PF={d[\"profit_factor\"]} DD={d[\"max_drawdown_pct\"]}%')"

# ── Final ─────────────────────────────────────────────────────────────────────
echo "[$(date '+%H:%M:%S')] Starting Final backtest (all filters)..."
FINAL_RUN_ID=$(start_backtest '{
  "symbols": ["EURUSD=X","GBPUSD=X","AUDUSD=X","BTC/USDT","ETH/USDT","SPY"],
  "timeframe": "H1", "start_date": "2024-01-01", "end_date": "2025-12-31",
  "account_size": 1000.0, "apply_slippage": true, "apply_swap": true,
  "apply_ranging_filter": true, "apply_d1_trend_filter": true,
  "apply_volume_filter": true, "apply_weekday_filter": true,
  "apply_momentum_filter": true, "apply_calendar_filter": true,
  "min_composite_score": 15
}')
echo "Final run_id=$FINAL_RUN_ID"
wait_for_backtest "$FINAL_RUN_ID" "Final"
FINAL_JSON=$(fetch_results "$FINAL_RUN_ID")
save_results "$DOCS_DIR/BACKTEST_RESULTS_FINAL.md" "Final — все v5-фильтры активны (SIM-25..SIM-44)" "$FINAL_RUN_ID" "$FINAL_JSON"
echo "$FINAL_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Final: trades={d[\"total_trades\"]} WR={d[\"win_rate_pct\"]}% PF={d[\"profit_factor\"]} DD={d[\"max_drawdown_pct\"]}%')"

echo ""
echo "=== ALL BACKTESTS COMPLETE ==="
echo "Results saved to docs/"
