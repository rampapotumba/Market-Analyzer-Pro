"use client";

import { useEffect, useRef, useState } from "react";
import { api, type BacktestRun, type BacktestResults, type BacktestRunParams, type Instrument } from "@/lib/api";
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

function MetricCard({ label, value, suffix = "", color }: { label: string; value: string | number; suffix?: string; color?: string }) {
  return (
    <div style={{ borderRadius: 8, border: `1px solid ${C.border}`, background: C.card, padding: 16 }}>
      <div style={{ fontSize: 11, color: C.muted, marginBottom: 6, fontFamily: 'monospace', textTransform: 'uppercase', letterSpacing: '0.5px' }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color: color ?? C.text, fontFamily: 'monospace' }}>
        {value}{suffix}
      </div>
    </div>
  );
}

const STATUS_COLOR: Record<string, string> = {
  pending: C.muted,
  running: C.yellow,
  done: C.green,
  completed: C.green,
  failed: C.red,
};

function StatusBadge({ status, progressPct }: { status: string; progressPct?: number | null }) {
  const label = status === "running" && progressPct != null
    ? `running ${progressPct.toFixed(0)}%`
    : status;
  return (
    <span style={{
      fontSize: 11, fontFamily: 'monospace', fontWeight: 600,
      color: STATUS_COLOR[status] ?? C.muted,
      textTransform: 'uppercase',
    }}>
      {label}
    </span>
  );
}

function SymbolMultiSelect({
  instruments,
  selected,
  onChange,
}: {
  instruments: Instrument[];
  selected: string[];
  onChange: (syms: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  function toggle(sym: string) {
    onChange(selected.includes(sym) ? selected.filter(s => s !== sym) : [...selected, sym]);
  }

  const label = selected.length === 0
    ? "Select symbols…"
    : selected.length <= 3
      ? selected.join(", ")
      : `${selected.slice(0, 3).join(", ")} +${selected.length - 3}`;

  // Group by type
  const groups: Record<string, Instrument[]> = {};
  for (const inst of instruments) {
    const g = inst.market || "other";
    if (!groups[g]) groups[g] = [];
    groups[g].push(inst);
  }

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        style={{
          width: '100%', background: '#0d1117', border: `1px solid ${open ? C.accent : C.border}`,
          borderRadius: 6, color: selected.length ? C.text : C.muted,
          fontFamily: 'monospace', fontSize: 13, padding: '7px 10px',
          textAlign: 'left', cursor: 'pointer', display: 'flex', justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
        <span style={{ marginLeft: 8, color: C.muted, fontSize: 10 }}>{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div style={{
          position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 100,
          background: '#161b22', border: `1px solid ${C.border}`, borderRadius: 6,
          marginTop: 4, maxHeight: 280, overflowY: 'auto', boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
        }}>
          {/* Select all / clear */}
          <div style={{ padding: '6px 10px', borderBottom: `1px solid ${C.border}`, display: 'flex', gap: 12 }}>
            <button type="button" onClick={() => onChange(instruments.map(i => i.symbol))}
              style={{ background: 'none', border: 'none', color: C.accent, fontFamily: 'monospace', fontSize: 11, cursor: 'pointer', padding: 0 }}>
              All
            </button>
            <button type="button" onClick={() => onChange([])}
              style={{ background: 'none', border: 'none', color: C.muted, fontFamily: 'monospace', fontSize: 11, cursor: 'pointer', padding: 0 }}>
              Clear
            </button>
          </div>

          {Object.entries(groups).map(([group, insts]) => (
            <div key={group}>
              <div style={{ padding: '5px 10px 3px', fontSize: 10, color: C.muted, fontFamily: 'monospace', textTransform: 'uppercase', letterSpacing: '0.5px', background: '#0d1117' }}>
                {group}
              </div>
              {insts.map(inst => {
                const checked = selected.includes(inst.symbol);
                return (
                  <div
                    key={inst.symbol}
                    onClick={() => toggle(inst.symbol)}
                    style={{
                      padding: '7px 10px', display: 'flex', alignItems: 'center', gap: 10,
                      cursor: 'pointer', background: checked ? '#1c2128' : 'transparent',
                    }}
                    onMouseEnter={e => { if (!checked) (e.currentTarget as HTMLDivElement).style.background = '#21262d'; }}
                    onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.background = checked ? '#1c2128' : 'transparent'; }}
                  >
                    <span style={{
                      width: 14, height: 14, border: `1px solid ${checked ? C.accent : C.border}`,
                      borderRadius: 3, background: checked ? C.accent : 'transparent',
                      display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
                    }}>
                      {checked && <span style={{ color: '#0d1117', fontSize: 10, fontWeight: 700 }}>✓</span>}
                    </span>
                    <span style={{ fontFamily: 'monospace', fontSize: 13, color: C.text }}>{inst.symbol}</span>
                    <span style={{ fontFamily: 'monospace', fontSize: 11, color: C.muted, marginLeft: 'auto' }}>{inst.name}</span>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function RunForm({ onStarted }: { onStarted: (runId: string) => void }) {
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>(["EURUSD=X", "BTC/USDT", "SPY"]);
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [timeframe, setTimeframe] = useState("H1");
  const [startDate, setStartDate] = useState("2026-03-05");
  const [endDate, setEndDate] = useState("2026-03-19");
  const [accountSize, setAccountSize] = useState("1000");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getInstruments().then(setInstruments).catch(() => {});
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (selectedSymbols.length === 0) { setError("Select at least one symbol"); return; }
    setLoading(true);
    setError(null);
    try {
      const params: BacktestRunParams = {
        symbols: selectedSymbols,
        timeframe,
        start_date: startDate,
        end_date: endDate,
        account_size: parseFloat(accountSize) || 1000,
      };
      const res = await api.runBacktest(params);
      onStarted(res.run_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start backtest");
    } finally {
      setLoading(false);
    }
  }

  const inputStyle: React.CSSProperties = {
    background: '#0d1117', border: `1px solid ${C.border}`, borderRadius: 6,
    color: C.text, fontFamily: 'monospace', fontSize: 13, padding: '7px 10px',
    width: '100%', boxSizing: 'border-box',
    colorScheme: 'dark',
  };
  const labelStyle: React.CSSProperties = {
    fontSize: 11, color: C.muted, fontFamily: 'monospace',
    textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 4, display: 'block',
  };

  return (
    <form onSubmit={submit} style={{ borderRadius: 8, border: `1px solid ${C.border}`, background: C.card, padding: 20, display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ fontSize: 14, fontWeight: 600, color: C.text, fontFamily: 'monospace' }}>Run Backtest</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12 }}>
        <div style={{ gridColumn: 'span 2' }}>
          <label style={labelStyle}>Symbols</label>
          <SymbolMultiSelect instruments={instruments} selected={selectedSymbols} onChange={setSelectedSymbols} />
        </div>
        <div>
          <label style={labelStyle}>Timeframe</label>
          <select style={inputStyle} value={timeframe} onChange={e => setTimeframe(e.target.value)}>
            {["M5","M15","M30","H1","H4","H12","D1"].map(tf => <option key={tf}>{tf}</option>)}
          </select>
        </div>
        <div>
          <label style={labelStyle}>Start Date</label>
          <input style={inputStyle} type="date" value={startDate} onChange={e => setStartDate(e.target.value)} />
        </div>
        <div>
          <label style={labelStyle}>End Date</label>
          <input style={inputStyle} type="date" value={endDate} onChange={e => setEndDate(e.target.value)} />
        </div>
        <div>
          <label style={labelStyle}>Account Size (USD)</label>
          <input style={inputStyle} type="number" value={accountSize} onChange={e => setAccountSize(e.target.value)} min="100" step="100" />
        </div>
      </div>
      {error && <div style={{ fontSize: 12, color: C.red, fontFamily: 'monospace' }}>{error}</div>}
      <div>
        <button
          type="submit"
          disabled={loading}
          style={{
            background: loading ? '#21262d' : C.accent, color: '#0d1117',
            border: 'none', borderRadius: 6, padding: '8px 20px',
            fontFamily: 'monospace', fontSize: 13, fontWeight: 700,
            cursor: loading ? 'not-allowed' : 'pointer',
          }}
        >
          {loading ? "Starting..." : "Run Backtest"}
        </button>
      </div>
    </form>
  );
}

function ResultsPanel({ runId }: { runId: string }) {
  const [results, setResults] = useState<BacktestResults | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getBacktestResults(runId)
      .then(setResults)
      .catch(e => setError(e.message));
  }, [runId]);

  if (error) return <div style={{ color: C.red, fontFamily: 'monospace', fontSize: 12 }}>{error}</div>;
  if (!results) return <div style={{ color: C.muted, fontFamily: 'monospace', fontSize: 12 }}>Loading results...</div>;

  const equityData = (results.equity_curve ?? []).map(p => ({
    date: p.date,
    equity: p.equity ?? p.balance ?? 0,
  }));
  const accountSize = results.params?.account_size ?? 1000;
  const pf = results.profit_factor ?? null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 12 }}>
        <MetricCard label="Total Trades" value={results.total_trades} />
        <MetricCard label="Win Rate" value={results.win_rate_pct.toFixed(1)} suffix="%" color={results.win_rate_pct >= 50 ? C.green : C.red} />
        <MetricCard label="Profit Factor" value={pf != null ? pf.toFixed(2) : "—"} color={pf != null ? (pf >= 1.5 ? C.green : pf >= 1.0 ? C.yellow : C.red) : C.muted} />
        {results.max_drawdown_pct != null && (
          <MetricCard label="Max Drawdown" value={results.max_drawdown_pct.toFixed(1)} suffix="%" color={C.red} />
        )}
        <MetricCard
          label="Net PnL"
          value={results.total_pnl_usd >= 0 ? `+${results.total_pnl_usd.toFixed(0)}` : results.total_pnl_usd.toFixed(0)}
          suffix=" USD"
          color={results.total_pnl_usd >= 0 ? C.green : C.red}
        />
        {results.long_count != null && results.short_count != null && (
          <MetricCard label="Long / Short" value={`${results.long_count} / ${results.short_count}`} />
        )}
      </div>

      {equityData.length > 1 && (
        <div style={{ maxWidth: 700 }}>
          <EquityCurve data={equityData} initialEquity={accountSize} />
        </div>
      )}

      {results.by_symbol && Object.keys(results.by_symbol).length > 0 && (
        <div style={{ borderRadius: 8, border: `1px solid ${C.border}`, background: C.card, overflow: 'hidden' }}>
          <div style={{ padding: '10px 14px', fontSize: 12, fontWeight: 600, color: C.text, fontFamily: 'monospace', borderBottom: `1px solid ${C.border}` }}>By Symbol</div>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                {["Symbol", "Trades", "Win%", "PnL USD"].map(h => (
                  <th key={h} style={{ padding: '8px 14px', textAlign: 'left', fontSize: 10, color: C.muted, fontFamily: 'monospace', textTransform: 'uppercase', letterSpacing: '0.5px' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Object.entries(results.by_symbol).map(([sym, d]) => {
                const wr = d.trades > 0 ? (d.wins / d.trades) * 100 : 0;
                return (
                  <tr key={sym} style={{ borderTop: `1px solid ${C.border}` }}>
                    <td style={{ padding: '8px 14px', fontSize: 13, color: C.text, fontFamily: 'monospace' }}>{sym}</td>
                    <td style={{ padding: '8px 14px', fontSize: 13, color: C.text }}>{d.trades}</td>
                    <td style={{ padding: '8px 14px', fontSize: 13, color: wr >= 50 ? C.green : C.red, fontFamily: 'monospace' }}>{wr.toFixed(1)}%</td>
                    <td style={{ padding: '8px 14px', fontSize: 13, fontFamily: 'monospace', color: d.pnl_usd >= 0 ? C.green : C.red }}>
                      {d.pnl_usd >= 0 ? "+" : ""}{d.pnl_usd.toFixed(1)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function BacktestsPage() {
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function loadRuns() {
    api.listBacktestRuns().then(setRuns).catch(() => setRuns([])).finally(() => setLoading(false));
  }

  useEffect(() => {
    loadRuns();
  }, []);

  // Poll while any run is pending/running
  useEffect(() => {
    const hasActive = runs.some(r => r.status === "pending" || r.status === "running");
    if (hasActive && !pollingRef.current) {
      pollingRef.current = setInterval(loadRuns, 3000);
    } else if (!hasActive && pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
    return () => {
      if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null; }
    };
  }, [runs]);

  async function handleDelete(runId: string, e: React.MouseEvent) {
    e.stopPropagation();
    await api.deleteBacktestRun(runId);
    if (selectedRunId === runId) setSelectedRunId(null);
    loadRuns();
  }

  function handleStarted(runId: string) {
    loadRuns();
    setSelectedRunId(runId);
  }

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
        <p style={{ fontSize: 12, color: C.muted, fontFamily: 'monospace', marginTop: 4 }}>Candle-by-candle strategy simulation · no-lookahead · SIM-22</p>
      </div>

      <RunForm onStarted={handleStarted} />

      {loading ? (
        <div style={{ color: C.muted, fontSize: 13, fontFamily: 'monospace' }}>Loading...</div>
      ) : runs.length === 0 ? (
        <div style={{
          borderRadius: 8, border: `1px dashed ${C.border}`,
          padding: 48, textAlign: 'center', color: C.muted, fontSize: 13, fontFamily: 'monospace',
        }}>
          No backtest runs yet — use the form above to start one
        </div>
      ) : (
        <>
          <div style={{ borderRadius: 8, border: `1px solid ${C.border}`, background: C.card, overflow: 'hidden' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: C.bg }}>
                  {["Run ID", "Status", "Symbols", "TF", "Period", "Started", "", ""].map((h) => (
                    <th key={h} style={thStyle}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => {
                  const params = r.params ? JSON.parse(r.params) : null;
                  const isSelected = r.id === selectedRunId;
                  return (
                    <tr
                      key={r.id}
                      style={{ borderBottom: `1px solid ${C.border}`, background: isSelected ? '#1c2128' : 'transparent', cursor: 'pointer' }}
                      onClick={() => setSelectedRunId(isSelected ? null : r.id)}
                    >
                      <td style={{ padding: '10px 14px', fontSize: 11, color: C.muted, fontFamily: 'monospace' }}>{r.id.slice(0, 8)}…</td>
                      <td style={{ padding: '10px 14px' }}><StatusBadge status={r.status} progressPct={r.progress_pct} /></td>
                      <td style={{ padding: '10px 14px', fontSize: 12, color: C.text, fontFamily: 'monospace' }}>
                        {params?.symbols?.join(", ") ?? "—"}
                      </td>
                      <td style={{ padding: '10px 14px', fontSize: 13, color: C.text, fontFamily: 'monospace' }}>{params?.timeframe ?? "—"}</td>
                      <td style={{ padding: '10px 14px', fontSize: 11, color: C.muted, fontFamily: 'monospace' }}>
                        {params ? `${params.start_date} – ${params.end_date}` : "—"}
                      </td>
                      <td style={{ padding: '10px 14px', fontSize: 11, color: C.muted, fontFamily: 'monospace' }}>
                        {r.started_at ? new Date(r.started_at).toLocaleString() : "—"}
                      </td>
                      <td style={{ padding: '10px 14px', fontSize: 11, color: C.accent, fontFamily: 'monospace' }}>
                        {r.status === "done" || r.status === "completed" ? (isSelected ? "▲ hide" : "▼ results") : ""}
                      </td>
                      <td style={{ padding: '6px 14px' }} onClick={e => e.stopPropagation()}>
                        <button
                          onClick={e => handleDelete(r.id, e)}
                          style={{
                            background: 'transparent', border: `1px solid ${C.border}`,
                            borderRadius: 4, color: C.muted, cursor: 'pointer',
                            fontSize: 11, fontFamily: 'monospace', padding: '3px 8px',
                          }}
                          onMouseEnter={e => (e.currentTarget.style.color = C.red)}
                          onMouseLeave={e => (e.currentTarget.style.color = C.muted)}
                        >
                          ✕
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {selectedRunId && (runs.find(r => r.id === selectedRunId)?.status === "done" || runs.find(r => r.id === selectedRunId)?.status === "completed") && (
            <div style={{ borderRadius: 8, border: `1px solid ${C.border}`, background: C.card, padding: 20 }}>
              <div style={{ fontSize: 14, fontWeight: 600, color: C.text, fontFamily: 'monospace', marginBottom: 16 }}>
                Results — {selectedRunId.slice(0, 8)}…
              </div>
              <ResultsPanel runId={selectedRunId} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
