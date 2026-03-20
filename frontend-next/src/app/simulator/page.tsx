"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { api } from "@/lib/api";
import { PortfolioHeatBar } from "@/components/PortfolioHeatBar";
import { Tooltip } from "@/components/Tooltip";

const API = process.env.NEXT_PUBLIC_API_URL ?? "";
// WebSocket must be absolute — derive from current page origin when no env var is set
const WS_BASE = API
  ? API.replace(/^http/, "ws")
  : (typeof window !== "undefined" ? `ws://${window.location.host}` : "ws://localhost:3000");

const C = {
  bg: '#0d1117',
  card: '#161b22',
  border: '#30363d',
  text: '#e6edf3',
  muted: '#8b949e',
  accent: '#58a6ff',
  green: '#22c55e',
  red: '#ef4444',
  yellow: '#f0a000',
};

// ── Types ─────────────────────────────────────────────────────────────────────

interface SimStats {
  account_size_usd: number;
  total_trades: number;
  open_positions: number;
  wins: number;
  losses: number;
  breakevens: number;
  win_rate_pct: number;
  total_pnl_usd: number;
  total_pnl_pct: number;
  unrealized_pnl_usd: number;
  avg_win_usd: number;
  avg_loss_usd: number;
  profit_factor: number | null;
  // SIM-16 account fields
  account_initial_balance: number;
  account_current_balance: number;
  account_peak_balance: number;
  account_drawdown_pct: number;
  account_total_return_pct: number;
  // SIM-24
  partial_close_count?: number;
}

interface Trade {
  signal_id: number;
  symbol: string;
  name: string;
  timeframe: string;
  direction: "LONG" | "SHORT";
  entry_price: number;
  exit_price: number;
  stop_loss: number;
  take_profit_1: number;
  exit_reason: string;
  pnl_pips: number;
  pnl_pct: number;
  pnl_usd: number;
  result: "win" | "loss" | "breakeven";
  duration_minutes: number | null;
  entry_at: string | null;
  exit_at: string | null;
  composite_score: number;
}

interface OpenPosition {
  signal_id: number;
  symbol: string;
  name: string;
  timeframe: string;
  direction: "LONG" | "SHORT";
  entry_price: number;
  current_price: number;
  stop_loss: number;
  take_profit_1: number;
  unrealized_pnl_pct: number;
  unrealized_pnl_usd: number;
  size_pct: number;
  size_remaining_pct: number;
  account_balance_at_entry: number | null;
  opened_at: string | null;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtUsd(v: number) {
  const abs = Math.abs(v);
  const str = abs.toFixed(2);
  return (v >= 0 ? "+" : "−") + "$" + str;
}

function fmtPct(v: number) {
  return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
}

function fmtPrice(price: number): string {
  if (price >= 1000) return price.toFixed(2);
  if (price >= 100)  return price.toFixed(2);
  if (price >= 1)    return price.toFixed(4);
  return price.toFixed(5);
}

/** SIM-12: Recompute unrealized P&L from live price. */
function calcLivePnl(
  pos: OpenPosition,
  livePrice: number,
  fallbackBalance: number,
): { usd: number; pct: number } {
  const balance = pos.account_balance_at_entry ?? fallbackBalance;
  const effectiveSize = pos.size_pct * pos.size_remaining_pct; // e.g. 2 * 0.5 = 1
  const movePct =
    pos.direction === "LONG"
      ? ((livePrice - pos.entry_price) / pos.entry_price) * 100
      : ((pos.entry_price - livePrice) / pos.entry_price) * 100;
  const usd = balance * (effectiveSize / 100) * (movePct / 100);
  return { usd, pct: movePct };
}

function fmtDuration(mins: number | null) {
  if (mins === null) return "—";
  if (mins < 60) return `${mins}m`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

function fmtTime(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("ru-RU", {
    day: "2-digit", month: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

// ── Stat widget ───────────────────────────────────────────────────────────────

function StatCard({ label, value, sub, positive }: {
  label: string; value: string; sub?: string; positive?: boolean;
}) {
  const valColor = positive === undefined ? C.text : positive ? C.green : C.red;
  return (
    <div style={{ borderRadius: 8, border: `1px solid ${C.border}`, background: C.card, padding: 18, display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 10, color: C.muted, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px', fontFamily: 'monospace' }}><Tooltip term={label}>{label}</Tooltip></span>
      <span style={{ fontSize: 22, fontWeight: 700, color: valColor, fontFamily: 'monospace' }}>{value}</span>
      {sub && <span style={{ fontSize: 11, color: C.muted, fontFamily: 'monospace' }}>{sub}</span>}
    </div>
  );
}

// ── Account balance widget ────────────────────────────────────────────────────

function AccountWidget({
  stats,
  liveEquity,
}: {
  stats: SimStats;
  liveEquity: number;
}) {
  const initial = stats.account_initial_balance;
  const current = stats.account_current_balance;
  const peak = stats.account_peak_balance;
  const drawdown = stats.account_drawdown_pct;
  const totalReturn = stats.account_total_return_pct;
  const liveReturn = initial > 0 ? ((liveEquity - initial) / initial) * 100 : 0;

  const returnColor = totalReturn >= 0 ? C.green : C.red;
  const liveColor = liveEquity >= current ? (liveEquity >= initial ? C.green : C.red) : C.red;

  const bar = (val: number, max: number, color: string) => {
    const pct = Math.min(100, max > 0 ? (val / max) * 100 : 0);
    return (
      <div style={{ height: 3, background: C.border, borderRadius: 2, marginTop: 6 }}>
        <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 2, transition: 'width 0.4s' }} />
      </div>
    );
  };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12 }}>
      {/* Starting balance */}
      <div style={{ borderRadius: 8, border: `1px solid ${C.border}`, background: C.card, padding: 18 }}>
        <div style={{ fontSize: 10, color: C.muted, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px', fontFamily: 'monospace', marginBottom: 6 }}>
          <Tooltip term="Starting Balance">Starting Balance</Tooltip>
        </div>
        <div style={{ fontSize: 22, fontWeight: 700, color: C.text, fontFamily: 'monospace' }}>
          ${initial.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </div>
        <div style={{ fontSize: 11, color: C.muted, fontFamily: 'monospace', marginTop: 4 }}>
          Peak: ${peak.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </div>
      </div>

      {/* Current balance (realized P&L only) */}
      <div style={{ borderRadius: 8, border: `1px solid ${C.border}`, background: C.card, padding: 18 }}>
        <div style={{ fontSize: 10, color: C.muted, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px', fontFamily: 'monospace', marginBottom: 6 }}>
          <Tooltip term="Current Balance">Current Balance</Tooltip>
        </div>
        <div style={{ fontSize: 22, fontWeight: 700, color: returnColor, fontFamily: 'monospace' }}>
          ${current.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </div>
        <div style={{ fontSize: 11, color: C.muted, fontFamily: 'monospace', marginTop: 4 }}>
          {totalReturn >= 0 ? '+' : ''}{totalReturn.toFixed(2)}% · DD {drawdown.toFixed(2)}%
        </div>
        {bar(current, peak, returnColor)}
      </div>

      {/* Live equity (current balance + live unrealized) */}
      <div style={{ borderRadius: 8, border: `1px solid ${liveColor}40`, background: C.card, padding: 18, position: 'relative' }}>
        <div style={{ fontSize: 10, color: C.muted, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px', fontFamily: 'monospace', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
          <Tooltip term="Live Equity">Live Equity</Tooltip>
          <span style={{ fontSize: 8, color: C.green, letterSpacing: 1 }}>● LIVE</span>
        </div>
        <div style={{ fontSize: 22, fontWeight: 700, color: liveColor, fontFamily: 'monospace' }}>
          ${liveEquity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </div>
        <div style={{ fontSize: 11, color: C.muted, fontFamily: 'monospace', marginTop: 4 }}>
          {liveReturn >= 0 ? '+' : ''}{liveReturn.toFixed(2)}% from start · incl. open
        </div>
        {bar(liveEquity, Math.max(liveEquity, peak), liveColor)}
      </div>
    </div>
  );
}

// ── Open positions table ──────────────────────────────────────────────────────

function OpenPositionsTable({
  positions,
  livePrices,
  accountSize,
}: {
  positions: OpenPosition[];
  livePrices: Record<string, number>;
  accountSize: number;
}) {
  if (!positions.length) return null;

  const thStyle = {
    padding: '10px 14px', textAlign: 'left' as const, fontSize: 10,
    fontWeight: 600, color: C.muted, textTransform: 'uppercase' as const,
    letterSpacing: '0.5px', fontFamily: 'monospace',
    borderBottom: `1px solid ${C.border}`,
  };

  return (
    <div>
      <h2 style={{ fontSize: 15, fontWeight: 600, color: C.text, marginBottom: 12, fontFamily: 'monospace' }}>
        Open Positions ({positions.length})
      </h2>
      <div style={{ borderRadius: 8, border: `1px solid ${C.border}`, background: C.card, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ background: C.bg }}>
              {(["Symbol", "TF", "Dir", "Entry", "Current", "SL", "TP1", "Unrealized P&L", "Opened"] as const).map((h) => (
                <th key={h} style={thStyle}><Tooltip term={h}>{h}</Tooltip></th>
              ))}
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => {
              const livePrice = livePrices[p.symbol] ?? p.current_price;
              const hasLive = p.symbol in livePrices;
              const { usd: liveUsd, pct: livePct } = calcLivePnl(p, livePrice, accountSize);
              const pnlColor = liveUsd >= 0 ? C.green : C.red;

              return (
                <tr key={p.signal_id} style={{ borderBottom: `1px solid ${C.border}` }}>
                  <td style={{ padding: '10px 14px', fontWeight: 600, color: C.text, fontFamily: 'monospace' }}>{p.symbol}</td>
                  <td style={{ padding: '10px 14px', color: C.muted, fontFamily: 'monospace' }}>{p.timeframe}</td>
                  <td style={{ padding: '10px 14px' }}>
                    <span style={{
                      borderRadius: 4, padding: '2px 8px', fontSize: 11, fontWeight: 700,
                      fontFamily: 'monospace',
                      color: p.direction === 'LONG' ? C.green : C.red,
                      background: p.direction === 'LONG' ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)',
                      border: `1px solid ${p.direction === 'LONG' ? 'rgba(34,197,94,0.3)' : 'rgba(239,68,68,0.3)'}`,
                    }}>
                      {p.direction}
                    </span>
                  </td>
                  <td style={{ padding: '10px 14px', fontFamily: 'monospace', color: C.muted }}>{fmtPrice(p.entry_price)}</td>
                  <td style={{ padding: '10px 14px', fontFamily: 'monospace', color: C.text }}>
                    {fmtPrice(livePrice)}
                    {hasLive && (
                      <span style={{ marginLeft: 5, fontSize: 9, color: C.green, fontFamily: 'monospace' }}>●</span>
                    )}
                  </td>
                  <td style={{ padding: '10px 14px', fontFamily: 'monospace', color: C.red }}>{p.stop_loss ? fmtPrice(p.stop_loss) : "—"}</td>
                  <td style={{ padding: '10px 14px', fontFamily: 'monospace', color: C.green }}>{p.take_profit_1 ? fmtPrice(p.take_profit_1) : "—"}</td>
                  <td style={{ padding: '10px 14px', fontWeight: 600, fontFamily: 'monospace', color: pnlColor }}>
                    {fmtUsd(liveUsd)}
                    <span style={{ marginLeft: 6, fontSize: 11, fontWeight: 400, color: C.muted }}>{fmtPct(livePct)}</span>
                  </td>
                  <td style={{ padding: '10px 14px', color: C.muted, fontSize: 11, fontFamily: 'monospace' }}>{fmtTime(p.opened_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Trade history table ───────────────────────────────────────────────────────

const RESULT_FILTER_OPTS = ["ALL", "win", "loss", "breakeven"];

function TradeHistory({ trades, filter, onFilter }: {
  trades: Trade[];
  filter: string;
  onFilter: (f: string) => void;
}) {
  const thStyle = {
    padding: '10px 14px', textAlign: 'left' as const, fontSize: 10,
    fontWeight: 600, color: C.muted, textTransform: 'uppercase' as const,
    letterSpacing: '0.5px', fontFamily: 'monospace',
    borderBottom: `1px solid ${C.border}`,
  };

  const resultColor = (r: string) =>
    r === 'win' ? C.green : r === 'loss' ? C.red : C.muted;
  const resultBg = (r: string) =>
    r === 'win' ? 'rgba(34,197,94,0.1)' : r === 'loss' ? 'rgba(239,68,68,0.1)' : 'rgba(139,148,158,0.1)';

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <h2 style={{ fontSize: 15, fontWeight: 600, color: C.text, fontFamily: 'monospace' }}>Closed Trades</h2>
        <div style={{ display: 'flex', gap: 4 }}>
          {RESULT_FILTER_OPTS.map((f) => (
            <button
              key={f}
              onClick={() => onFilter(f)}
              style={{
                padding: '4px 12px', borderRadius: 4, fontSize: 11, fontWeight: 600,
                fontFamily: 'monospace', cursor: 'pointer', border: 'none',
                background: filter === f ? C.accent : C.border,
                color: filter === f ? '#fff' : C.muted,
                transition: 'background 0.15s',
              }}
            >
              {f.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      <div style={{ borderRadius: 8, border: `1px solid ${C.border}`, background: C.card, overflowX: 'auto' }}>
        {trades.length === 0 ? (
          <div style={{ padding: 48, textAlign: 'center', color: C.muted, fontSize: 13, fontFamily: 'monospace' }}>
            No closed trades yet
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ background: C.bg }}>
                {["Symbol", "TF", "Dir", "Entry", "Exit", "Reason", "Duration", "Pips", "P&L USD", "Result"].map(
                  (h) => <th key={h} style={thStyle}><Tooltip term={h}>{h}</Tooltip></th>
                )}
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <tr key={t.signal_id} style={{ borderBottom: `1px solid ${C.border}` }}>
                  <td style={{ padding: '10px 14px' }}>
                    <div style={{ fontWeight: 600, color: C.text, fontFamily: 'monospace' }}>{t.symbol}</div>
                    <div style={{ fontSize: 11, color: C.muted, fontFamily: 'monospace' }}>{fmtTime(t.exit_at)}</div>
                  </td>
                  <td style={{ padding: '10px 14px', color: C.muted, fontFamily: 'monospace' }}>{t.timeframe}</td>
                  <td style={{ padding: '10px 14px' }}>
                    <span style={{
                      borderRadius: 4, padding: '2px 8px', fontSize: 11, fontWeight: 700,
                      fontFamily: 'monospace',
                      color: t.direction === 'LONG' ? C.green : C.red,
                      background: t.direction === 'LONG' ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)',
                      border: `1px solid ${t.direction === 'LONG' ? 'rgba(34,197,94,0.3)' : 'rgba(239,68,68,0.3)'}`,
                    }}>
                      {t.direction}
                    </span>
                  </td>
                  <td style={{ padding: '10px 14px', fontFamily: 'monospace', fontSize: 12, color: C.text }}>{t.entry_price.toFixed(5)}</td>
                  <td style={{ padding: '10px 14px', fontFamily: 'monospace', fontSize: 12, color: C.text }}>{t.exit_price.toFixed(5)}</td>
                  <td style={{ padding: '10px 14px', color: C.muted, fontSize: 12 }}>{t.exit_reason?.replace("_", " ")}</td>
                  <td style={{ padding: '10px 14px', color: C.muted, fontSize: 12, fontFamily: 'monospace' }}>{fmtDuration(t.duration_minutes)}</td>
                  <td style={{ padding: '10px 14px', fontFamily: 'monospace', fontSize: 12, color: t.pnl_pips >= 0 ? C.green : C.red }}>
                    {t.pnl_pips >= 0 ? "+" : ""}{t.pnl_pips.toFixed(1)}
                  </td>
                  <td style={{ padding: '10px 14px', fontWeight: 600, fontFamily: 'monospace', color: t.pnl_usd >= 0 ? C.green : C.red }}>
                    {fmtUsd(t.pnl_usd)}
                  </td>
                  <td style={{ padding: '10px 14px' }}>
                    <span style={{
                      borderRadius: 4, padding: '2px 8px', fontSize: 11, fontWeight: 600,
                      fontFamily: 'monospace', color: resultColor(t.result),
                      background: resultBg(t.result),
                      border: `1px solid ${resultColor(t.result)}40`,
                    }}>
                      {t.result}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function SimulatorPage() {
  const [stats, setStats] = useState<SimStats | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [openPositions, setOpenPositions] = useState<OpenPosition[]>([]);
  const [tradeFilter, setTradeFilter] = useState("ALL");
  const [loading, setLoading] = useState(true);
  const [heat, setHeat] = useState(0);
  const [livePrices, setLivePrices] = useState<Record<string, number>>({});
  const wsRef = useRef<WebSocket | null>(null);

  const load = useCallback(async () => {
    try {
      const [statsRes, openRes] = await Promise.all([
        fetch("/api/v2/simulator/stats"),
        fetch("/api/v2/simulator/open"),
      ]);
      if (statsRes.ok) setStats(await statsRes.json());
      if (openRes.ok) setOpenPositions(await openRes.json());
    } catch { /* ignore */ }
    api.getPortfolioHeat().then((r) => setHeat(r.portfolio_heat_pct ?? 0)).catch(() => {});
    setLoading(false);
  }, []);

  const loadTrades = useCallback(async () => {
    const url =
      tradeFilter === "ALL"
        ? "/api/v2/simulator/trades?limit=100"
        : `/api/v2/simulator/trades?limit=100&result=${tradeFilter}`;
    try {
      const res = await fetch(url);
      if (res.ok) setTrades(await res.json());
    } catch { /* ignore */ }
  }, [tradeFilter]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { loadTrades(); }, [loadTrades]);

  useEffect(() => {
    const id = setInterval(() => { load(); loadTrades(); }, 30_000);
    return () => clearInterval(id);
  }, [load, loadTrades]);

  // Live price WebSocket — subscribe to all-symbols feed
  useEffect(() => {
    let ws: WebSocket;
    let retryTimer: ReturnType<typeof setTimeout>;

    function connect() {
      ws = new WebSocket(`${WS_BASE}/ws/prices`);
      wsRef.current = ws;

      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          if (msg.type === "tick" && msg.symbol && msg.price) {
            const price = parseFloat(msg.price);
            if (!isNaN(price)) {
              setLivePrices((prev) => ({ ...prev, [msg.symbol]: price }));
            }
          }
        } catch { /* ignore */ }
      };

      ws.onclose = () => {
        retryTimer = setTimeout(connect, 3000);
      };
      ws.onerror = () => ws.close();
    }

    connect();
    return () => {
      clearTimeout(retryTimer);
      wsRef.current?.close();
    };
  }, []);

  const pnlPositive = stats ? stats.total_pnl_usd >= 0 : undefined;
  const unrealizedPositive = stats ? stats.unrealized_pnl_usd >= 0 : undefined;

  // Live equity = current balance + sum of live unrealized P&L across all open positions
  const accountSize = stats?.account_current_balance ?? stats?.account_size_usd ?? 1000;
  const liveUnrealized = openPositions.reduce((sum, pos) => {
    const livePrice = livePrices[pos.symbol] ?? pos.current_price;
    const { usd } = calcLivePnl(pos, livePrice, accountSize);
    return sum + usd;
  }, 0);
  const liveEquity = accountSize + liveUnrealized;

  return (
    <div style={{ maxWidth: 1280, margin: '0 auto', padding: '24px 16px', display: 'flex', flexDirection: 'column', gap: 32 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, color: C.text, fontFamily: 'monospace', margin: 0 }}>Trade Simulator</h1>
          <p style={{ fontSize: 12, color: C.muted, fontFamily: 'monospace', marginTop: 4 }}>
            Virtual account:{" "}
            <span style={{ fontWeight: 600, color: C.text }}>
              ${(stats?.account_current_balance ?? 1000).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD
            </span>
            {" · "}live SL/TP monitoring every 1 min · auto-refresh 30 s
          </p>
        </div>
        <button
          onClick={() => { setLoading(true); load(); loadTrades(); }}
          style={{
            fontSize: 13, color: C.accent, background: 'transparent',
            border: 'none', cursor: 'pointer', fontFamily: 'monospace', fontWeight: 600,
          }}
        >
          ↻ Refresh
        </button>
      </div>

      {/* Portfolio Heat */}
      <div style={{ maxWidth: 360 }}>
        <div style={{ fontSize: 10, color: C.muted, fontFamily: 'monospace', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 6 }}>
          <Tooltip term="Portfolio Heat">Portfolio Heat</Tooltip>
        </div>
        <PortfolioHeatBar heatPct={heat} />
      </div>

      {/* Account balance widgets */}
      {stats && (
        <AccountWidget stats={stats} liveEquity={liveEquity} />
      )}

      {/* Stat widgets */}
      {loading && !stats ? (
        <div style={{ textAlign: 'center', color: C.muted, padding: 48, fontSize: 13, fontFamily: 'monospace' }}>Loading…</div>
      ) : stats ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 12 }}>
          <StatCard label="Total Trades" value={String(stats.total_trades)} sub={`${stats.open_positions} open`} />
          <StatCard
            label="Win Rate"
            value={`${stats.win_rate_pct}%`}
            sub={`${stats.wins}W · ${stats.losses}L · ${stats.breakevens}BE`}
            positive={stats.win_rate_pct >= 50}
          />
          <StatCard
            label="Total P&L"
            value={fmtUsd(stats.total_pnl_usd)}
            sub={fmtPct(stats.total_pnl_pct)}
            positive={pnlPositive}
          />
          <StatCard
            label="Unrealized"
            value={fmtUsd(liveUnrealized)}
            sub={`${stats.open_positions} position${stats.open_positions !== 1 ? "s" : ""} · live`}
            positive={liveUnrealized >= 0 ? true : liveUnrealized < 0 ? false : undefined}
          />
          <StatCard
            label="Profit Factor"
            value={stats.profit_factor != null ? stats.profit_factor.toFixed(2) : "—"}
            sub={`Avg win ${fmtUsd(stats.avg_win_usd)} / loss ${fmtUsd(stats.avg_loss_usd)}`}
            positive={stats.profit_factor != null ? stats.profit_factor >= 1 : undefined}
          />
          {stats.partial_close_count != null && (
            <StatCard
              label="Partial Closes"
              value={String(stats.partial_close_count)}
              sub="TP1 hit → 50% closed"
            />
          )}
        </div>
      ) : null}

      <TradeHistory trades={trades} filter={tradeFilter} onFilter={setTradeFilter} />
      {openPositions.length > 0 && (
        <OpenPositionsTable
          positions={openPositions}
          livePrices={livePrices}
          accountSize={accountSize}
        />
      )}
    </div>
  );
}
