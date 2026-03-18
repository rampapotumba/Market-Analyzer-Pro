"use client";

import { useEffect, useState } from "react";
import { api, type BacktestRun } from "@/lib/api";
import { EquityCurve } from "@/components/EquityCurve";

const C = {
  bg: '#0d1117',
  card: '#161b22',
  border: '#30363d',
  text: '#e6edf3',
  muted: '#8b949e',
  green: '#22c55e',
  red: '#ef4444',
  yellow: '#f0a000',
  accent: '#58a6ff',
};

const MIN_TRADES_REQUIRED = 60; // MIN_OOS_TRADES × 2

function MetricCard({ label, value, suffix = "" }: { label: string; value: string | number; suffix?: string }) {
  return (
    <div style={{ borderRadius: 8, border: `1px solid ${C.border}`, background: C.card, padding: 16 }}>
      <div style={{ fontSize: 11, color: C.muted, marginBottom: 6, fontFamily: 'monospace', textTransform: 'uppercase', letterSpacing: '0.5px' }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color: C.text, fontFamily: 'monospace' }}>
        {value}{suffix}
      </div>
    </div>
  );
}

function ReadinessBar({ current, required }: { current: number; required: number }) {
  const pct = Math.min(100, Math.round((current / required) * 100));
  const remaining = Math.max(0, required - current);
  const color = pct >= 100 ? C.green : pct >= 60 ? C.yellow : C.accent;

  return (
    <div style={{ borderRadius: 8, border: `1px solid ${C.border}`, background: C.card, padding: '16px 20px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: C.text, fontFamily: 'monospace' }}>
          Backtest readiness
        </span>
        <span style={{ fontSize: 12, color: C.muted, fontFamily: 'monospace' }}>
          {current} / {required} closed trades
        </span>
      </div>

      {/* Progress bar */}
      <div style={{ height: 6, borderRadius: 3, background: '#21262d', overflow: 'hidden', marginBottom: 10 }}>
        <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 3, transition: 'width 0.4s ease' }} />
      </div>

      <div style={{ fontSize: 11, color: C.muted, fontFamily: 'monospace' }}>
        {pct >= 100
          ? '✓ Enough data — run a backtest to see results'
          : `${remaining} more closed trade${remaining !== 1 ? 's' : ''} needed to unlock backtesting`}
      </div>
    </div>
  );
}

export default function BacktestsPage() {
  const [backtests, setBacktests] = useState<BacktestRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [closedTrades, setClosedTrades] = useState<number | null>(null);

  useEffect(() => {
    api.getBacktests().then(setBacktests).catch(() => setBacktests([])).finally(() => setLoading(false));

    fetch('/api/v2/simulator/stats')
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setClosedTrades(d.total_trades); })
      .catch(() => {});
  }, []);

  const latest = backtests[0];

  const equityData = latest
    ? [
        { date: latest.start_date, equity: 10000 },
        { date: latest.end_date, equity: 10000 * (1 + latest.net_pnl_pct / 100) },
      ]
    : [];

  const thStyle = {
    padding: '10px 14px', textAlign: 'left' as const, fontSize: 10,
    fontWeight: 600, color: C.muted, textTransform: 'uppercase' as const,
    letterSpacing: '0.5px', fontFamily: 'monospace',
    borderBottom: `1px solid ${C.border}`,
  };

  return (
    <div style={{ maxWidth: 1280, margin: '0 auto', padding: '24px 16px', display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div>
        <h1 style={{ fontSize: 22, fontWeight: 700, color: C.text, fontFamily: 'monospace', margin: 0 }}>Backtests</h1>
        <p style={{ fontSize: 12, color: C.muted, fontFamily: 'monospace', marginTop: 4 }}>Walk-forward strategy validation · auto-runs weekly</p>
      </div>

      {/* Readiness progress */}
      {closedTrades !== null && (
        <ReadinessBar current={closedTrades} required={MIN_TRADES_REQUIRED} />
      )}

      {loading ? (
        <div style={{ color: C.muted, fontSize: 13, fontFamily: 'monospace' }}>Loading...</div>
      ) : backtests.length === 0 ? (
        <div style={{
          borderRadius: 8, border: `1px dashed ${C.border}`,
          padding: 48, textAlign: 'center', color: C.muted, fontSize: 13, fontFamily: 'monospace',
        }}>
          No backtests yet — results will appear automatically once enough trades accumulate
        </div>
      ) : (
        <>
          {latest && (
            <div>
              <h2 style={{ fontSize: 15, fontWeight: 600, color: C.text, marginBottom: 12, fontFamily: 'monospace' }}>Latest Run</h2>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 12, marginBottom: 16 }}>
                <MetricCard label="Total Trades" value={latest.total_trades} />
                <MetricCard label="Win Rate" value={(latest.win_rate * 100).toFixed(1)} suffix="%" />
                <MetricCard label="Sharpe Ratio" value={latest.sharpe_ratio.toFixed(2)} />
                <MetricCard label="Max Drawdown" value={(latest.max_drawdown * 100).toFixed(1)} suffix="%" />
              </div>
              <div style={{ maxWidth: 600 }}>
                <EquityCurve data={equityData} />
              </div>
            </div>
          )}

          <div style={{ borderRadius: 8, border: `1px solid ${C.border}`, background: C.card, overflow: 'hidden' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: C.bg }}>
                  {["ID", "TF", "Period", "Trades", "Win%", "Sharpe", "Drawdown", "Net PnL", "Date"].map((h) => (
                    <th key={h} style={thStyle}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {backtests.map((b) => (
                  <tr key={b.id} style={{ borderBottom: `1px solid ${C.border}` }}>
                    <td style={{ padding: '10px 14px', fontSize: 12, color: C.muted, fontFamily: 'monospace' }}>#{b.id}</td>
                    <td style={{ padding: '10px 14px', fontSize: 13, color: C.text, fontFamily: 'monospace' }}>{b.timeframe}</td>
                    <td style={{ padding: '10px 14px', fontSize: 11, color: C.muted, fontFamily: 'monospace' }}>
                      {new Date(b.start_date).toLocaleDateString()} – {new Date(b.end_date).toLocaleDateString()}
                    </td>
                    <td style={{ padding: '10px 14px', fontSize: 13, color: C.text }}>{b.total_trades}</td>
                    <td style={{ padding: '10px 14px', fontSize: 13, color: C.text, fontFamily: 'monospace' }}>{(b.win_rate * 100).toFixed(1)}%</td>
                    <td style={{ padding: '10px 14px', fontSize: 13, color: C.text, fontFamily: 'monospace' }}>{b.sharpe_ratio.toFixed(2)}</td>
                    <td style={{ padding: '10px 14px', fontSize: 13, color: C.red, fontFamily: 'monospace' }}>-{(b.max_drawdown * 100).toFixed(1)}%</td>
                    <td style={{ padding: '10px 14px', fontSize: 13, fontWeight: 600, fontFamily: 'monospace', color: b.net_pnl_pct >= 0 ? C.green : C.red }}>
                      {b.net_pnl_pct >= 0 ? "+" : ""}{b.net_pnl_pct.toFixed(1)}%
                    </td>
                    <td style={{ padding: '10px 14px', fontSize: 11, color: C.muted, fontFamily: 'monospace' }}>
                      {new Date(b.created_at).toLocaleDateString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
