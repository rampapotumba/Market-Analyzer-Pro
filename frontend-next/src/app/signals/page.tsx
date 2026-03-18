"use client";

import { useEffect, useState } from "react";
import { api, type Signal } from "@/lib/api";

const STATUS_TABS = [
  { label: "ALL",       value: undefined },
  { label: "ACTIVE",    value: "created" },
  { label: "TRACKING",  value: "tracking" },
  { label: "COMPLETED", value: "completed" },
  { label: "CANCELLED", value: "cancelled" },
];

const C = {
  bg: '#0d1117',
  card: '#161b22',
  border: '#30363d',
  text: '#e6edf3',
  muted: '#8b949e',
  accent: '#58a6ff',
  green: '#22c55e',
  red: '#ef4444',
};

function SignalRow({ signal }: { signal: Signal }) {
  const dirColor =
    signal.direction === "LONG" ? C.green
    : signal.direction === "SHORT" ? C.red
    : C.muted;

  return (
    <tr style={{ borderBottom: `1px solid ${C.border}` }}>
      <td style={{ padding: '10px 14px', fontSize: 13, fontWeight: 600, color: C.text, fontFamily: 'monospace' }}>{signal.symbol}</td>
      <td style={{ padding: '10px 14px', fontSize: 12, color: C.muted, fontFamily: 'monospace' }}>{signal.timeframe}</td>
      <td style={{ padding: '10px 14px' }}>
        <span style={{
          display: 'inline-flex', borderRadius: 4, padding: '2px 8px',
          fontSize: 11, fontWeight: 700, fontFamily: 'monospace',
          color: dirColor,
          background: signal.direction === "LONG" ? 'rgba(34,197,94,0.1)' : signal.direction === "SHORT" ? 'rgba(239,68,68,0.1)' : 'rgba(139,148,158,0.1)',
          border: `1px solid ${dirColor}40`,
        }}>
          {signal.direction}
        </span>
      </td>
      <td style={{ padding: '10px 14px', fontSize: 13, fontFamily: 'monospace', color: C.text }}>{signal.composite_score?.toFixed(1) ?? "—"}</td>
      <td style={{ padding: '10px 14px', fontSize: 12, fontFamily: 'monospace', color: C.text }}>{signal.entry_price?.toFixed(5) ?? "—"}</td>
      <td style={{ padding: '10px 14px', fontSize: 12, fontFamily: 'monospace', color: C.red }}>{signal.stop_loss?.toFixed(5) ?? "—"}</td>
      <td style={{ padding: '10px 14px', fontSize: 12, fontFamily: 'monospace', color: C.green }}>{signal.take_profit_1?.toFixed(5) ?? "—"}</td>
      <td style={{ padding: '10px 14px', fontSize: 13, color: C.text }}>{signal.risk_reward?.toFixed(1) ?? "—"}</td>
      <td style={{ padding: '10px 14px', fontSize: 12, color: C.muted }}>{signal.regime}</td>
      <td style={{ padding: '10px 14px', fontSize: 11, color: C.muted, fontFamily: 'monospace' }}>
        {new Date(signal.created_at).toLocaleDateString()}
      </td>
    </tr>
  );
}

export default function SignalsPage() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [tab, setTab] = useState(STATUS_TABS[0]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api
      .getSignals(tab.value)
      .then(setSignals)
      .catch(() => setSignals([]))
      .finally(() => setLoading(false));
  }, [tab]);

  return (
    <div style={{ maxWidth: 1280, margin: '0 auto', padding: '24px 16px' }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, color: C.text, marginBottom: 20, fontFamily: 'monospace' }}>Signals</h1>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: 4, borderBottom: `1px solid ${C.border}`, marginBottom: 20 }}>
        {STATUS_TABS.map((t) => (
          <button
            key={t.label}
            onClick={() => setTab(t)}
            style={{
              padding: '8px 14px',
              fontSize: 12,
              fontWeight: 600,
              fontFamily: 'monospace',
              background: 'transparent',
              border: 'none',
              borderBottom: `2px solid ${tab.label === t.label ? C.accent : 'transparent'}`,
              color: tab.label === t.label ? C.accent : C.muted,
              cursor: 'pointer',
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Table */}
      <div style={{ borderRadius: 8, border: `1px solid ${C.border}`, background: C.card, overflow: 'hidden' }}>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: C.bg }}>
                {["Symbol", "TF", "Direction", "Score", "Entry", "SL", "TP1", "R:R", "Regime", "Date"].map((h) => (
                  <th
                    key={h}
                    style={{
                      padding: '10px 14px', textAlign: 'left', fontSize: 10,
                      fontWeight: 600, color: C.muted, textTransform: 'uppercase',
                      letterSpacing: '0.5px', fontFamily: 'monospace',
                      borderBottom: `1px solid ${C.border}`,
                    }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={10} style={{ padding: '48px', textAlign: 'center', color: C.muted, fontSize: 13 }}>
                    Loading...
                  </td>
                </tr>
              ) : signals.length === 0 ? (
                <tr>
                  <td colSpan={10} style={{ padding: '48px', textAlign: 'center', color: C.muted, fontSize: 13 }}>
                    No signals found
                  </td>
                </tr>
              ) : (
                signals.map((s) => <SignalRow key={s.id} signal={s} />)
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
