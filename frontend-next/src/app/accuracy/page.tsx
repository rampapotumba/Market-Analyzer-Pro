"use client";

import { useEffect, useState, useCallback } from "react";
import { Tooltip } from "@/components/Tooltip";

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

interface SimStats {
  total_trades: number;
  wins: number;
  losses: number;
  breakevens: number;
  win_rate_pct: number;
  total_pnl_usd: number;
  total_pnl_pct: number;
  profit_factor: number;
  avg_win_usd: number;
  avg_loss_usd: number;
}

interface Trade {
  symbol: string;
  timeframe: string;
  direction: string;
  result: string;
  pnl_pips: number;
  pnl_usd: number;
  exit_reason: string;
  duration_minutes: number;
  composite_score: number;
}

interface SymbolStats {
  symbol: string;
  total: number;
  wins: number;
  losses: number;
  breakevens: number;
  win_rate: number;
  total_pnl_usd: number;
  avg_pnl_pips: number;
  profit_factor: number;
  avg_score: number;
}

function computeBySymbol(trades: Trade[]): SymbolStats[] {
  const map = new Map<string, Trade[]>();
  for (const t of trades) {
    if (!map.has(t.symbol)) map.set(t.symbol, []);
    map.get(t.symbol)!.push(t);
  }
  const result: SymbolStats[] = [];
  for (const [symbol, ts] of map) {
    const wins = ts.filter(t => t.result === "win").length;
    const losses = ts.filter(t => t.result === "loss").length;
    const breakevens = ts.filter(t => t.result === "breakeven").length;
    const total_pnl_usd = ts.reduce((s, t) => s + t.pnl_usd, 0);
    const avg_pnl_pips = ts.reduce((s, t) => s + t.pnl_pips, 0) / ts.length;
    const win_pnl = ts.filter(t => t.result === "win").reduce((s, t) => s + t.pnl_usd, 0);
    const loss_pnl = Math.abs(ts.filter(t => t.result === "loss").reduce((s, t) => s + t.pnl_usd, 0));
    const profit_factor = loss_pnl > 0 ? win_pnl / loss_pnl : wins > 0 ? 999 : 0;
    const avg_score = ts.reduce((s, t) => s + t.composite_score, 0) / ts.length;
    result.push({
      symbol,
      total: ts.length,
      wins, losses, breakevens,
      win_rate: ts.length > 0 ? (wins / ts.length) * 100 : 0,
      total_pnl_usd,
      avg_pnl_pips,
      profit_factor,
      avg_score,
    });
  }
  return result.sort((a, b) => b.total - a.total);
}

function winColor(pct: number) {
  return pct >= 55 ? C.green : pct >= 45 ? C.yellow : C.red;
}
function pfColor(pf: number) {
  return pf >= 1.5 ? C.green : pf >= 1.0 ? C.yellow : C.red;
}
function pnlColor(v: number) {
  return v > 0 ? C.green : v < 0 ? C.red : C.muted;
}

function SummaryCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ borderRadius: 8, border: `1px solid ${C.border}`, background: C.card, padding: 16 }}>
      <div style={{ fontSize: 11, color: C.muted, marginBottom: 6, fontFamily: 'monospace', textTransform: 'uppercase', letterSpacing: '0.5px' }}><Tooltip term={label}>{label}</Tooltip></div>
      <div style={{ fontSize: 22, fontWeight: 700, fontFamily: 'monospace', color: color ?? C.text }}>{value}</div>
    </div>
  );
}

const thStyle = {
  padding: '10px 14px', textAlign: 'left' as const, fontSize: 10,
  fontWeight: 600, color: C.muted, textTransform: 'uppercase' as const,
  letterSpacing: '0.5px', fontFamily: 'monospace',
  borderBottom: `1px solid ${C.border}`,
};

export default function AccuracyPage() {
  const [stats, setStats] = useState<SimStats | null>(null);
  const [bySymbol, setBySymbol] = useState<SymbolStats[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [statsRes, tradesRes] = await Promise.all([
        fetch('/api/v2/simulator/stats'),
        fetch('/api/v2/simulator/trades?limit=500'),
      ]);
      if (statsRes.ok) setStats(await statsRes.json());
      if (tradesRes.ok) {
        const trades: Trade[] = await tradesRes.json();
        setBySymbol(computeBySymbol(trades));
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const hasData = stats && stats.total_trades > 0;

  return (
    <div style={{ maxWidth: 1280, margin: '0 auto', padding: '24px 16px', display: 'flex', flexDirection: 'column', gap: 24 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, color: C.text, fontFamily: 'monospace', margin: 0 }}>Signal Accuracy</h1>
          <p style={{ fontSize: 12, color: C.muted, fontFamily: 'monospace', marginTop: 4 }}>
            Based on {stats?.total_trades ?? 0} closed trade{stats?.total_trades !== 1 ? 's' : ''}
          </p>
        </div>
        <button
          onClick={load}
          style={{ fontSize: 12, color: C.accent, background: 'transparent', border: 'none', cursor: 'pointer', fontFamily: 'monospace', fontWeight: 600 }}
        >
          ↻ Refresh
        </button>
      </div>

      {loading ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 12 }}>
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} style={{ height: 80, borderRadius: 8, background: C.card, border: `1px solid ${C.border}` }} />
          ))}
        </div>
      ) : !hasData ? (
        <div style={{
          borderRadius: 8, border: `1px dashed ${C.border}`,
          padding: 64, textAlign: 'center', color: C.muted, fontSize: 13, fontFamily: 'monospace',
        }}>
          No closed trades yet — accuracy stats will appear as signals complete
        </div>
      ) : (
        <>
          {/* Summary cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 12 }}>
            <SummaryCard label="Total Trades"    value={String(stats!.total_trades)} />
            <SummaryCard label="Win Rate"        value={stats!.win_rate_pct != null ? `${stats!.win_rate_pct.toFixed(1)}%` : "—"}   color={winColor(stats!.win_rate_pct ?? 0)} />
            <SummaryCard label="Profit Factor"   value={stats!.profit_factor != null ? stats!.profit_factor.toFixed(2) : "—"}        color={pfColor(stats!.profit_factor ?? 0)} />
            <SummaryCard label="Net PnL"         value={stats!.total_pnl_usd != null ? `$${stats!.total_pnl_usd.toFixed(2)}` : "—"} color={pnlColor(stats!.total_pnl_usd ?? 0)} />
            <SummaryCard label="Avg Win"         value={stats!.avg_win_usd != null ? `$${stats!.avg_win_usd.toFixed(2)}` : "—"}     color={C.green} />
            <SummaryCard label="Avg Loss"        value={stats!.avg_loss_usd != null ? `$${stats!.avg_loss_usd.toFixed(2)}` : "—"}   color={C.red} />
          </div>

          {/* W/L breakdown */}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {[
              { label: `${stats!.wins} wins`, color: C.green },
              { label: `${stats!.losses} losses`, color: C.red },
              ...(stats!.breakevens > 0 ? [{ label: `${stats!.breakevens} b/e`, color: C.muted }] : []),
            ].map(({ label, color }) => (
              <span key={label} style={{
                fontSize: 12, fontFamily: 'monospace', fontWeight: 600, color,
                background: `${color}15`, border: `1px solid ${color}40`,
                borderRadius: 999, padding: '3px 10px',
              }}>{label}</span>
            ))}
          </div>

          {/* Per-symbol table */}
          {bySymbol.length > 0 && (
            <div style={{ borderRadius: 8, border: `1px solid ${C.border}`, background: C.card, overflow: 'hidden' }}>
              <div style={{ padding: '12px 16px', borderBottom: `1px solid ${C.border}`, fontSize: 12, fontWeight: 600, color: C.muted, fontFamily: 'monospace', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                Per Symbol
              </div>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ background: C.bg }}>
                      {["Symbol", "Trades", "W / L / B/E", "Win Rate", "Profit Factor", "Avg Pips", "Net PnL", "Avg Score"].map(h => (
                        <th key={h} style={thStyle}><Tooltip term={h}>{h}</Tooltip></th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {bySymbol.map((s) => (
                      <tr key={s.symbol} style={{ borderBottom: `1px solid ${C.border}` }}>
                        <td style={{ padding: '10px 14px', fontSize: 13, fontWeight: 600, color: C.text, fontFamily: 'monospace' }}>{s.symbol.replace('=X', '').replace('USDT', '')}</td>
                        <td style={{ padding: '10px 14px', fontSize: 13, color: C.muted }}>{s.total}</td>
                        <td style={{ padding: '10px 14px', fontSize: 12, fontFamily: 'monospace' }}>
                          <span style={{ color: C.green }}>{s.wins}</span>
                          <span style={{ color: C.muted }}> / </span>
                          <span style={{ color: C.red }}>{s.losses}</span>
                          <span style={{ color: C.muted }}> / {s.breakevens}</span>
                        </td>
                        <td style={{ padding: '10px 14px', fontSize: 13, fontWeight: 600, color: winColor(s.win_rate), fontFamily: 'monospace' }}>
                          {s.win_rate.toFixed(0)}%
                        </td>
                        <td style={{ padding: '10px 14px', fontSize: 13, fontWeight: 600, color: pfColor(s.profit_factor), fontFamily: 'monospace' }}>
                          {s.profit_factor === 999 ? '∞' : s.profit_factor.toFixed(2)}
                        </td>
                        <td style={{ padding: '10px 14px', fontSize: 13, color: pnlColor(s.avg_pnl_pips), fontFamily: 'monospace' }}>
                          {s.avg_pnl_pips > 0 ? '+' : ''}{s.avg_pnl_pips.toFixed(1)}
                        </td>
                        <td style={{ padding: '10px 14px', fontSize: 13, fontWeight: 600, color: pnlColor(s.total_pnl_usd), fontFamily: 'monospace' }}>
                          {s.total_pnl_usd > 0 ? '+' : ''}${s.total_pnl_usd.toFixed(2)}
                        </td>
                        <td style={{ padding: '10px 14px', fontSize: 12, color: C.muted, fontFamily: 'monospace' }}>
                          {s.avg_score.toFixed(1)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Legend */}
          <div style={{ fontSize: 11, color: C.muted, fontFamily: 'monospace', display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            <span>Win Rate: <span style={{ color: C.green }}>≥55% good</span> · <span style={{ color: C.yellow }}>≥45% ok</span> · <span style={{ color: C.red }}>&lt;45% poor</span></span>
            <span>Profit Factor: <span style={{ color: C.green }}>≥1.5 good</span> · <span style={{ color: C.yellow }}>≥1.0 ok</span> · <span style={{ color: C.red }}>&lt;1.0 poor</span></span>
          </div>
        </>
      )}
    </div>
  );
}
