"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { CandlestickChart } from "@/components/CandlestickChart";
import { SIGNAL_STATUS, signalStatusLabel } from "@/lib/signalStatus";

const API = process.env.NEXT_PUBLIC_API_URL ?? "";
const WS_BASE = API
  ? API.replace(/^http/, "ws")
  : (typeof window !== "undefined" ? `ws://${window.location.host}` : "ws://localhost:3000");

interface Instrument { id: number; symbol: string; name: string; market: string; is_active: boolean; }
interface Candle { timestamp: string; open: number; high: number; low: number; close: number; volume: number; }
interface SignalData {
  status?: string; direction: string; signal_strength: string;
  composite_score: number; ta_score?: number; fa_score?: number;
  sentiment_score?: number; geo_score?: number; confidence: number;
  entry_price?: number; stop_loss?: number; take_profit_1?: number;
  take_profit_2?: number; risk_reward?: number; position_size_pct?: number;
  horizon?: string; reasoning?: string; indicators_snapshot?: string;
  message?: string; created_at?: string; timeframe?: string;
}
interface CalendarEvent {
  currency: string;
  event: string;
  impact: string;
  event_date: string;
  forecast?: number | null;
  previous?: number | null;
}

interface MacroItem {
  country: string;
  indicator: string;
  value: number | null;
  previous_value: number | null;
  release_date: string;
}

interface RateItem {
  bank: string;
  rate: number | null;
  effective_date: string;
}

interface ActiveSignal {
  id: number; instrument_id: number; timeframe: string; direction: string;
  signal_strength: string; composite_score: number; entry_price?: number; status: string;
}

const TIMEFRAMES = ["M15", "H1", "H4", "D1", "W1"];

function toNum(v: unknown): number | null {
  if (v == null) return null;
  const n = typeof v === "string" ? parseFloat(v) : Number(v);
  return isNaN(n) ? null : n;
}
function fmt(v?: unknown, d = 5) {
  const n = toNum(v);
  if (n == null) return "—";
  return n.toFixed(d);
}
function fmtPrice(v?: unknown) {
  const n = toNum(v);
  if (n == null) return "—";
  return n > 100 ? n.toFixed(2) : n.toFixed(5);
}
function fmtScore(v?: unknown) {
  const n = toNum(v);
  if (n == null) return "—";
  return (n >= 0 ? "+" : "") + n.toFixed(1);
}

function ScoreBar({ label, value }: { label: string; value: number }) {
  const pct = Math.min(50, Math.abs(value) / 2);
  const col = value > 0 ? "#22c55e" : value < 0 ? "#ef4444" : "#9ca3af";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11 }}>
      <span style={{ width: 72, color: "#6b7280", flexShrink: 0 }}>{label}</span>
      <div style={{ flex: 1, height: 6, background: "#1e293b", borderRadius: 3, position: "relative", overflow: "hidden" }}>
        <div style={{ position: "absolute", top: 0, left: "50%", width: 1, height: "100%", background: "#334155" }} />
        <div style={{
          position: "absolute", top: 0, height: "100%", borderRadius: 3,
          background: col, width: `${pct}%`,
          left: value >= 0 ? "50%" : `${50 - pct}%`,
        }} />
      </div>
      <span style={{ width: 40, textAlign: "right", fontFamily: "monospace", fontWeight: 600, color: col }}>{fmtScore(value)}</span>
    </div>
  );
}

function IndBadge({ signal }: { signal: string }) {
  const col = signal === "BUY" ? { bg: "#0d3b1e", text: "#4ade80" }
    : signal === "SELL" ? { bg: "#3b0d0d", text: "#f87171" }
    : { bg: "#1e293b", text: "#94a3b8" };
  return (
    <span style={{ background: col.bg, color: col.text, borderRadius: 4, padding: "1px 6px", fontSize: 10, fontWeight: 600 }}>
      {signal}
    </span>
  );
}

export default function TradingDashboard() {
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [selected, setSelected] = useState<Instrument | null>(null);
  const [timeframe, setTimeframe] = useState("H1");
  const [candles, setCandles] = useState<Candle[]>([]);
  const [signal, setSignal] = useState<SignalData | null>(null);
  const signalCache = useRef<Record<string, SignalData>>({});
  const [activeSignals, setActiveSignals] = useState<ActiveSignal[]>([]);
  const [analyzing, setAnalyzing] = useState(false);
  const [calendarEvents, setCalendarEvents] = useState<CalendarEvent[]>([]);
  const [macroData, setMacroData] = useState<MacroItem[]>([]);
  const [rates, setRates] = useState<RateItem[]>([]);
  const [livePrices, setLivePrices] = useState<Record<string, number>>({});
  const [priceDir, setPriceDir] = useState<Record<string, "up" | "down">>({});
  const [wsConnected, setWsConnected] = useState(false);
  const [filter, setFilter] = useState("");

  const wsRef = useRef<WebSocket | null>(null);
  const selectedRef = useRef<Instrument | null>(null);

  // Keep ref in sync so WS handler always has fresh selected symbol
  selectedRef.current = selected;

  const cacheKey = selected ? `${selected.symbol}-${timeframe}` : null;

  const setAndCacheSignal = useCallback((data: SignalData) => {
    if (cacheKey) signalCache.current[cacheKey] = data;
    setSignal(data);
  }, [cacheKey]);

  // Load instruments
  useEffect(() => {
    fetch(`${API}/api/v2/instruments`)
      .then(r => r.json())
      .then((data: Instrument[]) => {
        setInstruments(data);
        if (data.length > 0) setSelected(data[0]);
      }).catch(() => {});
  }, []);

  // All-symbols WebSocket for live prices in sidebar
  useEffect(() => {
    let ws: WebSocket;
    let retryTimer: ReturnType<typeof setTimeout>;

    function connect() {
      ws = new WebSocket(`${WS_BASE}/ws/prices`);
      wsRef.current = ws;
      ws.onopen = () => setWsConnected(true);
      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          if (msg.type === "tick" && msg.symbol && msg.price) {
            const newPrice = parseFloat(msg.price);
            setLivePrices(prev => {
              const oldPrice = prev[msg.symbol];
              if (oldPrice !== undefined && oldPrice !== newPrice) {
                setPriceDir(d => ({ ...d, [msg.symbol]: newPrice > oldPrice ? "up" : "down" }));
              }
              return { ...prev, [msg.symbol]: newPrice };
            });
            // Update last candle close price on the chart for the selected instrument
            if (selectedRef.current?.symbol === msg.symbol) {
              setCandles(prev => {
                if (!prev.length) return prev;
                const last = prev[prev.length - 1];
                if (last.close === newPrice) return prev;
                const updated = [...prev];
                updated[updated.length - 1] = { ...last, close: newPrice, high: Math.max(last.high, newPrice), low: Math.min(last.low, newPrice) };
                return updated;
              });
            }
          }
        } catch {}
      };
      ws.onclose = () => {
        setWsConnected(false);
        retryTimer = setTimeout(connect, 3000);
      };
      ws.onerror = () => ws.close();
    }
    connect();
    return () => { ws?.close(); clearTimeout(retryTimer); };
  }, []);

  // Load candles
  const loadCandles = useCallback(async () => {
    if (!selected) return;
    try {
      const r = await fetch(`${API}/api/v2/prices/${encodeURIComponent(selected.symbol)}?timeframe=${timeframe}&limit=200`);
      if (r.ok) setCandles(await r.json());
    } catch {}
  }, [selected, timeframe]);

  useEffect(() => { loadCandles(); }, [loadCandles]);

  // Load economic calendar + macro fundamentals
  useEffect(() => {
    fetch(`${API}/api/v2/macroeconomics/calendar`)
      .then(r => r.ok ? r.json() : [])
      .then((data: CalendarEvent[]) => setCalendarEvents(data))
      .catch(() => {});
    fetch(`${API}/api/v2/macroeconomics`)
      .then(r => r.ok ? r.json() : [])
      .then((data: MacroItem[]) => setMacroData(data))
      .catch(() => {});
    fetch(`${API}/api/v2/macroeconomics/rates`)
      .then(r => r.ok ? r.json() : [])
      .then((data: RateItem[]) => setRates(data))
      .catch(() => {});
  }, []);

  // Load active signals
  const loadActiveSignals = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/v2/signals/active`);
      if (r.ok) setActiveSignals(await r.json());
    } catch {}
  }, []);

  useEffect(() => {
    loadActiveSignals();
    const t = setInterval(loadActiveSignals, 30000);
    return () => clearInterval(t);
  }, [loadActiveSignals]);

  // Load latest signal for selected symbol — check cache first, then DB
  useEffect(() => {
    if (!selected) return;
    const key = `${selected.symbol}-${timeframe}`;
    // Restore from cache immediately
    const cached = signalCache.current[key];
    if (cached) setSignal(cached);
    // Then try to load fresher data from DB
    fetch(`${API}/api/v2/signals/latest/${encodeURIComponent(selected.symbol)}?timeframe=${timeframe}`)
      .then(r => r.ok ? r.json() : null)
      .then((data: SignalData | null) => {
        if (data) {
          signalCache.current[key] = data;
          setSignal(data);
        }
      })
      .catch(() => {});
  }, [selected, timeframe]);

  const runAnalysis = async () => {
    if (!selected || analyzing) return;
    setAnalyzing(true);
    try {
      const r = await fetch(`${API}/api/v2/analyze/${encodeURIComponent(selected.symbol)}?timeframe=${timeframe}`, { method: "POST" });
      const data: SignalData = await r.json();
      setAndCacheSignal(data);
      loadActiveSignals();
      loadCandles();
    } catch {}
    finally { setAnalyzing(false); }
  };

  const lastCandle = candles[candles.length - 1];
  const prevCandle = candles[candles.length - 2];
  const livePrice = selected ? (livePrices[selected.symbol] ?? lastCandle?.close) : null;
  const priceChange = lastCandle && prevCandle ? (livePrice ?? lastCandle.close) - prevCandle.close : 0;
  const priceChangePct = prevCandle && prevCandle.close ? (priceChange / prevCandle.close) * 100 : 0;
  const decimals = livePrice && livePrice > 100 ? 2 : 5;

  const groups: Record<string, Instrument[]> = { forex: [], stock: [], crypto: [] };
  instruments.forEach(i => { (groups[i.market] ??= []).push(i); });
  const groupLabels: Record<string, string> = { forex: "Forex", stock: "Stocks", crypto: "Crypto" };

  const filtered = (instr: Instrument) =>
    !filter || instr.symbol.toLowerCase().includes(filter.toLowerCase()) || instr.name.toLowerCase().includes(filter.toLowerCase());

  const dirColor = (d?: string) => d === "LONG" ? "#22c55e" : d === "SHORT" ? "#ef4444" : "#64748b";
  const dirSymbol = (d?: string) => d === "LONG" ? "▲" : d === "SHORT" ? "▼" : "●";

  // Parse signal reasoning & indicators
  let reasoning: Record<string, unknown> = {};
  let indicators: Record<string, number> = {};
  try { reasoning = JSON.parse(signal?.reasoning ?? "{}"); } catch {}
  try { indicators = JSON.parse(signal?.indicators_snapshot ?? "{}"); } catch {}

  const llmScore = reasoning.llm_score as number | undefined;
  const llmBias = reasoning.llm_bias as string | undefined;
  const llmConf = reasoning.llm_confidence as number | undefined;
  const llmText = reasoning.llm_reasoning as string | undefined;
  const claudeFactors = ((reasoning.factors as string[]) ?? []).filter(f => f.startsWith("Claude:"));
  const hasLLM = llmText || (llmScore !== undefined && llmScore !== 0);

  // Fundamental analysis data
  const latestMacro = Object.values(
    macroData.reduce((acc, item) => {
      if (!acc[item.indicator]) acc[item.indicator] = item;
      return acc;
    }, {} as Record<string, MacroItem>)
  );

  const getMacroValue = (key: string) => latestMacro.find(m => m.indicator === key)?.value ?? null;

  const KEY_RATES = ["FED", "ECB", "BOE", "BOJ", "RBA"];
  const latestRates = Object.values(
    rates.reduce((acc, r) => {
      if (!acc[r.bank]) acc[r.bank] = r;
      return acc;
    }, {} as Record<string, RateItem>)
  ).filter(r => KEY_RATES.includes(r.bank));

  const market = selected?.market ?? "forex";

  type MacroBadge = "BUY" | "SELL" | "NEUTRAL";
  type MacroRow = { key: string; label: string; format: (v: number) => string; sig: (v: number) => MacroBadge };

  // Rows shown for every market
  const COMMON_MACRO: MacroRow[] = [
    { key: "FEDFUNDS",  label: "Fed Rate",     format: v => `${v.toFixed(2)}%`,           sig: v => v > 4.5 ? "SELL" : v < 2 ? "BUY" : "NEUTRAL" },
    { key: "CPIAUCSL",  label: "CPI (US)",      format: v => v.toFixed(1),                 sig: v => v > 320 ? "SELL" : "NEUTRAL" },
    { key: "UNRATE",    label: "Unemployment",  format: v => `${v.toFixed(1)}%`,           sig: v => v < 4 ? "BUY" : v > 5.5 ? "SELL" : "NEUTRAL" },
    { key: "VIX",       label: "VIX",           format: v => v.toFixed(1),                 sig: v => v > 25 ? "SELL" : v < 15 ? "BUY" : "NEUTRAL" },
    { key: "DXY",       label: "DXY",           format: v => v.toFixed(2),                 sig: v => v > 105 ? "SELL" : v < 95 ? "BUY" : "NEUTRAL" },
    { key: "TNX",       label: "10Y Yield",     format: v => `${v.toFixed(2)}%`,           sig: v => v > 4.5 ? "SELL" : v < 3 ? "BUY" : "NEUTRAL" },
  ];

  // Market-specific additions
  const EXTRA_MACRO: Record<string, MacroRow[]> = {
    forex: [
      { key: "GDPC1",   label: "Real GDP",      format: v => `${(v/1000).toFixed(0)}B`,  sig: v => v > 22000 ? "BUY" : "NEUTRAL" },
    ],
    stock: [
      { key: "GDPC1",   label: "Real GDP",      format: v => `${(v/1000).toFixed(0)}B`,  sig: v => v > 22000 ? "BUY" : "NEUTRAL" },
      { key: "PAYEMS",  label: "Payrolls",      format: v => `${(v/1000).toFixed(0)}K`,  sig: v => v > 155000 ? "BUY" : "NEUTRAL" },
    ],
    crypto: [
      { key: `FUNDING_RATE_${selected?.symbol?.split("/")[0] ?? "BTC"}`,
                        label: "Funding Rate",  format: v => `${(v*100).toFixed(4)}%`,   sig: v => v > 0.01 ? "SELL" : v < -0.01 ? "BUY" : "NEUTRAL" },
    ],
  };

  const FA_MACRO_ROWS: MacroRow[] = [
    ...COMMON_MACRO,
    ...(EXTRA_MACRO[market] ?? []),
  ];

  const indItems = [
    { name: "RSI (14)", value: fmt(indicators.rsi, 1), sig: indicators.rsi ? (indicators.rsi > 70 ? "SELL" : indicators.rsi < 30 ? "BUY" : "NEUTRAL") : "NEUTRAL" },
    { name: "MACD Hist", value: fmt(indicators.macd_hist, 5), sig: indicators.macd_hist ? (indicators.macd_hist > 0 ? "BUY" : "SELL") : "NEUTRAL" },
    { name: "SMA 20", value: fmtPrice(indicators.sma20), sig: indicators.current_price && indicators.sma20 ? (indicators.current_price > indicators.sma20 ? "BUY" : "SELL") : "NEUTRAL" },
    { name: "SMA 50", value: fmtPrice(indicators.sma50), sig: indicators.current_price && indicators.sma50 ? (indicators.current_price > indicators.sma50 ? "BUY" : "SELL") : "NEUTRAL" },
    { name: "SMA 200", value: fmtPrice(indicators.sma200), sig: indicators.current_price && indicators.sma200 ? (indicators.current_price > indicators.sma200 ? "BUY" : "SELL") : "NEUTRAL" },
    { name: "ADX (14)", value: fmt(indicators.adx, 1), sig: indicators.adx ? (indicators.adx > 25 ? "BUY" : "NEUTRAL") : "NEUTRAL" },
    { name: "ATR (14)", value: fmtPrice(indicators.atr), sig: "NEUTRAL" },
    { name: "Stoch %K", value: fmt(indicators.stoch_k, 1), sig: indicators.stoch_k ? (indicators.stoch_k > 80 ? "SELL" : indicators.stoch_k < 20 ? "BUY" : "NEUTRAL") : "NEUTRAL" },
    { name: "BB Width", value: fmt(indicators.bb_width, 5), sig: "NEUTRAL" },
  ];

  // Dark theme styles
  const dark = {
    bg: "#0d1117", sidebar: "#161b22", card: "#161b22",
    border: "#30363d", text: "#e6edf3", muted: "#8b949e", accent: "#58a6ff",
  };

  return (
    <div style={{ display: "flex", height: "calc(100vh - 56px)", background: dark.bg, color: dark.text, fontFamily: "system-ui, sans-serif", overflow: "hidden" }}>

      {/* ── Sidebar ── */}
      <aside style={{ width: 200, background: dark.sidebar, borderRight: `1px solid ${dark.border}`, display: "flex", flexDirection: "column", flexShrink: 0 }}>
        <div style={{ padding: "8px", borderBottom: `1px solid ${dark.border}` }}>
          <input
            placeholder="Search..."
            value={filter}
            onChange={e => setFilter(e.target.value)}
            style={{ width: "100%", background: dark.bg, border: `1px solid ${dark.border}`, borderRadius: 6, padding: "4px 8px", fontSize: 12, color: dark.text, outline: "none", boxSizing: "border-box" }}
          />
        </div>
        <div style={{ flex: 1, overflowY: "auto" }}>
          {Object.entries(groups).map(([market, items]) => {
            const vis = items.filter(filtered);
            if (!vis.length) return null;
            return (
              <div key={market}>
                <div style={{ padding: "8px 12px 4px", fontSize: 10, fontWeight: 700, color: dark.muted, textTransform: "uppercase", letterSpacing: 1 }}>{groupLabels[market]}</div>
                {vis.map(instr => {
                  const price = livePrices[instr.symbol];
                  const isActive = selected?.id === instr.id;
                  return (
                    <button key={instr.id} onClick={() => { setSelected(instr); setSignal(null); }}
                      style={{ width: "100%", display: "flex", alignItems: "center", justifyContent: "space-between", padding: "6px 12px", background: isActive ? "#1f2937" : "transparent", border: "none", borderLeft: isActive ? "2px solid #3b82f6" : "2px solid transparent", cursor: "pointer", textAlign: "left" }}>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ fontSize: 12, fontWeight: 600, color: dark.text, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{instr.name}</div>
                        <div style={{ fontSize: 10, color: dark.muted }}>{instr.symbol}</div>
                      </div>
                      {price && (
                        <div style={{
                          fontSize: 11, fontFamily: "monospace", fontWeight: 600, flexShrink: 0, marginLeft: 4,
                          color: priceDir[instr.symbol] === "up" ? "#22c55e" : priceDir[instr.symbol] === "down" ? "#ef4444" : dark.muted,
                          transition: "color 0.5s",
                        }}>
                          {price > 100 ? price.toFixed(2) : price.toFixed(5)}
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>
            );
          })}
        </div>
      </aside>

      {/* ── Main Content ── */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>

        {/* Symbol bar */}
        <div style={{ display: "flex", alignItems: "center", gap: 16, padding: "8px 16px", background: dark.sidebar, borderBottom: `1px solid ${dark.border}`, flexShrink: 0 }}>
          <div>
            <span style={{ fontWeight: 700, fontSize: 16 }}>{selected?.name ?? "—"}</span>
            <span style={{ color: dark.muted, fontSize: 12, marginLeft: 8 }}>{selected?.symbol}</span>
          </div>
          {livePrice && (
            <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
              <span style={{ fontFamily: "monospace", fontWeight: 700, fontSize: 18 }}>
                {livePrice.toFixed(decimals)}
              </span>
              <span style={{ fontSize: 13, color: priceChange >= 0 ? "#22c55e" : "#ef4444" }}>
                {priceChange >= 0 ? "+" : ""}{priceChange.toFixed(decimals)} ({priceChangePct >= 0 ? "+" : ""}{priceChangePct.toFixed(2)}%)
              </span>
            </div>
          )}
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginLeft: "auto" }}>
            {TIMEFRAMES.map(tf => (
              <button key={tf} onClick={() => setTimeframe(tf)}
                style={{ padding: "3px 10px", borderRadius: 4, border: "none", fontSize: 12, fontWeight: 600, cursor: "pointer", background: tf === timeframe ? "#3b82f6" : dark.bg, color: tf === timeframe ? "#fff" : dark.muted }}>
                {tf}
              </button>
            ))}
            <button onClick={runAnalysis} disabled={analyzing || !selected}
              style={{ marginLeft: 8, padding: "5px 16px", borderRadius: 6, border: "none", background: "#3b82f6", color: "#fff", fontWeight: 700, fontSize: 13, cursor: "pointer", opacity: analyzing ? 0.7 : 1, display: "flex", alignItems: "center", gap: 6 }}>
              {analyzing && <span style={{ display: "inline-block", width: 12, height: 12, borderRadius: "50%", border: "2px solid #fff", borderTopColor: "transparent", animation: "spin 0.7s linear infinite" }} />}
              {analyzing ? "Analyzing..." : "⚡ Analyze"}
            </button>
            <div style={{ display: "flex", alignItems: "center", gap: 4, marginLeft: 8 }}>
              <div style={{ width: 8, height: 8, borderRadius: "50%", background: wsConnected ? "#22c55e" : "#64748b" }} />
              <span style={{ fontSize: 11, color: dark.muted }}>{wsConnected ? "Live" : "Offline"}</span>
            </div>
          </div>
        </div>

        {/* Grid area */}
        <div style={{ flex: 1, overflowY: "auto", padding: 12, display: "flex", flexDirection: "column", gap: 12 }}>

          {/* Row 1: Chart + Signal */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 340px", gap: 12 }}>

            {/* Chart */}
            <div style={{ background: dark.card, border: `1px solid ${dark.border}`, borderRadius: 8, padding: 12, overflow: "hidden" }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: dark.muted }}>Price Chart</span>
                <span style={{ fontSize: 11, color: dark.muted }}>{timeframe}</span>
              </div>
              <CandlestickChart candles={candles} height={300} dark />
            </div>

            {/* Signal Panel */}
            <div style={{ background: dark.card, border: `1px solid ${dark.border}`, borderRadius: 8, padding: 12, overflowY: "auto" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: dark.muted }}>
                  Signal
                  {signal?.timeframe && <span style={{ fontWeight: 400, marginLeft: 6 }}>{signal.timeframe}</span>}
                </span>
                {signal && (
                  <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 4,
                    background: signal.direction === "LONG" ? "#0d3b1e" : signal.direction === "SHORT" ? "#3b0d0d" : "#1e293b",
                    color: dirColor(signal.direction) }}>
                    {signal.signal_strength?.replace("_", " ")}
                  </span>
                )}
              </div>

              {!signal ? (
                <div style={{ textAlign: "center", padding: "40px 0", color: dark.muted }}>
                  <div style={{ fontSize: 32, marginBottom: 8 }}>📊</div>
                  <div style={{ fontSize: 12 }}>Click Analyze to generate a signal</div>
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  {/* Direction */}
                  <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                    <div style={{ fontSize: 28, fontWeight: 800, color: dirColor(signal.direction) }}>
                      {dirSymbol(signal.direction)} {signal.direction}
                    </div>
                    <div>
                      <div style={{ fontSize: 11, color: dark.muted }}>Confidence</div>
                      <div style={{ fontSize: 15, fontWeight: 600 }}>{fmt(signal.confidence, 1)}%</div>
                    </div>
                    <div>
                      <div style={{ fontSize: 11, color: dark.muted }}>Horizon</div>
                      <div style={{ fontSize: 12 }}>{signal.horizon ?? "—"}</div>
                    </div>
                  </div>

                  {/* Composite + Sentiment score bars */}
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    {(["composite_score", "sentiment_score"] as const).map((key) => {
                      const val = toNum(signal[key]) ?? 0;
                      const label = key === "composite_score" ? "Composite" : "Sentiment";
                      const isComposite = key === "composite_score";
                      return (
                        <div key={key}>
                          <div style={{ fontSize: 11, color: dark.muted, marginBottom: 3 }}>{label}</div>
                          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <div style={{ flex: 1, height: isComposite ? 8 : 6, background: "#1e293b", borderRadius: 4, position: "relative", overflow: "hidden" }}>
                              <div style={{ position: "absolute", top: 0, left: "50%", width: 1, height: "100%", background: "#334155" }} />
                              <div style={{
                                position: "absolute", top: 0, height: "100%", borderRadius: 4,
                                background: val > 0 ? "#22c55e" : val < 0 ? "#ef4444" : "#9ca3af",
                                width: `${Math.min(50, Math.abs(val) / 2)}%`,
                                left: val >= 0 ? "50%" : `${50 - Math.min(50, Math.abs(val) / 2)}%`,
                              }} />
                            </div>
                            <span style={{ fontSize: isComposite ? 15 : 12, fontWeight: 700, minWidth: 44, textAlign: "right", color: val > 0 ? "#22c55e" : val < 0 ? "#ef4444" : dark.muted }}>
                              {val > 0 ? "+" : ""}{val.toFixed(1)}
                            </span>
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  {/* Levels */}
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6 }}>
                    {[
                      { label: "Entry", value: signal.entry_price, col: "#3b82f6" },
                      { label: "Stop Loss", value: signal.stop_loss, col: "#ef4444" },
                      { label: "TP1", value: signal.take_profit_1, col: "#22c55e" },
                      { label: "TP2", value: signal.take_profit_2, col: "#16a34a" },
                      { label: "R:R", value: null, text: toNum(signal.risk_reward) != null ? `1:${toNum(signal.risk_reward)!.toFixed(2)}` : "—", col: dark.text },
                      { label: "Position", value: null, text: toNum(signal.position_size_pct) != null ? `${toNum(signal.position_size_pct)!.toFixed(1)}%` : "—", col: dark.text },
                    ].map(({ label, value, text, col }) => (
                      <div key={label} style={{ background: dark.bg, borderRadius: 6, padding: "6px 8px", border: `1px solid ${dark.border}` }}>
                        <div style={{ fontSize: 10, color: dark.muted }}>{label}</div>
                        <div style={{ fontSize: 12, fontFamily: "monospace", fontWeight: 600, color: col }}>
                          {text ?? fmtPrice(value)}
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* HOLD message */}
                  {signal.status === "no_signal" && signal.message && (
                    <div style={{ fontSize: 11, color: "#fbbf24", background: "#422006", borderRadius: 6, padding: "8px 10px" }}>
                      {signal.message}
                    </div>
                  )}

                  {/* Claude AI block */}
                  {hasLLM && (
                    <div style={{ background: "rgba(88,166,255,0.06)", border: "1px solid rgba(88,166,255,0.2)", borderRadius: 8, padding: "10px 12px" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                        <span style={{ fontSize: 11, fontWeight: 700, color: "#58a6ff", letterSpacing: 0.5 }}>✦ CLAUDE AI</span>
                        {llmBias && <span style={{ fontSize: 12, fontWeight: 700, color: llmBias === "BULLISH" ? "#22c55e" : llmBias === "BEARISH" ? "#ef4444" : dark.muted }}>
                          {llmBias === "BULLISH" ? "▲" : llmBias === "BEARISH" ? "▼" : "●"} {llmBias}
                        </span>}
                        {llmScore !== undefined && <span style={{ marginLeft: "auto", fontSize: 12, fontWeight: 600, color: (llmScore ?? 0) > 0 ? "#22c55e" : "#ef4444" }}>{fmtScore(llmScore)}</span>}
                        {llmConf !== undefined && <span style={{ fontSize: 11, color: dark.muted }}>{(llmConf as number).toFixed(0)}% conf</span>}
                      </div>
                      {llmText && <div style={{ fontSize: 11, color: dark.muted, lineHeight: 1.5 }}>{llmText}</div>}
                      {claudeFactors.length > 0 && (
                        <ul style={{ margin: "6px 0 0", paddingLeft: 16, fontSize: 11, color: dark.muted }}>
                          {claudeFactors.map((f, i) => <li key={i}>{f.replace("Claude: ", "")}</li>)}
                        </ul>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Row 2: Technical Indicators + Fundamentals + Active Signals */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>

            {/* Technical Indicators */}
            <div style={{ background: dark.card, border: `1px solid ${dark.border}`, borderRadius: 8, padding: 12 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: dark.muted, marginBottom: 10 }}>Technical Indicators</div>
              {!signal ? (
                <div style={{ textAlign: "center", padding: "20px 0", color: dark.muted, fontSize: 12 }}>No data yet</div>
              ) : (
                <>
                  {/* TA Score bar only */}
                  <div style={{ marginBottom: 12 }}>
                    <ScoreBar label="TA Score" value={toNum(signal.ta_score) ?? 0} />
                  </div>
                  {/* Indicator rows */}
                  <div style={{ borderTop: `1px solid ${dark.border}`, paddingTop: 8 }}>
                    {indItems.map(item => (
                      <div key={item.name} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "3px 0", borderBottom: `1px solid ${dark.border}` }}>
                        <span style={{ fontSize: 12, color: dark.muted }}>{item.name}</span>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <span style={{ fontSize: 11, fontFamily: "monospace", color: dark.text }}>{item.value}</span>
                          <IndBadge signal={item.sig} />
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>

            {/* Fundamental Analysis */}
            <div style={{ background: dark.card, border: `1px solid ${dark.border}`, borderRadius: 8, padding: 12, overflowY: "auto" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: dark.muted }}>Fundamental Analysis</span>
                <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 4, background: dark.bg, color: dark.muted, textTransform: "uppercase" }}>
                  {market}
                </span>
              </div>

              {/* Score bars: FA + Geo */}
              {signal && (
                <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 12 }}>
                  <ScoreBar label="FA Score" value={toNum(signal.fa_score) ?? 0} />
                  <ScoreBar label="Geo"      value={toNum(signal.geo_score) ?? 0} />
                  {market === "crypto" && (
                    <div style={{ fontSize: 10, color: dark.muted, marginTop: 2, padding: "4px 8px", background: "#1e293b", borderRadius: 4 }}>
                      Crypto FA relies on sentiment & on-chain data
                    </div>
                  )}
                </div>
              )}

              {/* Macro indicators */}
              {FA_MACRO_ROWS.length > 0 && (
                <>
                  <div style={{ fontSize: 10, fontWeight: 700, color: dark.muted, textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 6 }}>
                    Macro Indicators
                  </div>
                  <div style={{ borderTop: `1px solid ${dark.border}`, marginBottom: 10 }}>
                    {FA_MACRO_ROWS.map(row => {
                      const val = getMacroValue(row.key);
                      const sig: MacroBadge = val != null ? row.sig(val) : "NEUTRAL";
                      return (
                        <div key={row.key} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "4px 0", borderBottom: `1px solid ${dark.border}` }}>
                          <span style={{ fontSize: 12, color: dark.muted }}>{row.label}</span>
                          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <span style={{ fontSize: 11, fontFamily: "monospace", color: dark.text }}>
                              {val != null ? row.format(val) : "—"}
                            </span>
                            <IndBadge signal={sig} />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </>
              )}

              {/* COT (Commitments of Traders) — forex only */}
              {market === "forex" && selected && (() => {
                const cotKey = `COT_NET_${selected.symbol}`;
                const cotVal = getMacroValue(cotKey);
                if (cotVal == null) return null;
                return (
                  <div style={{ marginTop: 10 }}>
                    <div style={{ fontSize: 10, fontWeight: 700, color: dark.muted, textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 6 }}>
                      COT Positioning
                    </div>
                    <div style={{ borderTop: `1px solid ${dark.border}` }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "4px 0" }}>
                        <span style={{ fontSize: 12, color: dark.muted }}>Net Position</span>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <span style={{ fontSize: 11, fontFamily: "monospace", color: dark.text }}>
                            {cotVal > 0 ? "+" : ""}{cotVal.toLocaleString()}
                          </span>
                          <IndBadge signal={cotVal > 10000 ? "BUY" : cotVal < -10000 ? "SELL" : "NEUTRAL"} />
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })()}

              {!signal && FA_MACRO_ROWS.every(r => getMacroValue(r.key) == null) && (
                <div style={{ textAlign: "center", padding: "20px 0", color: dark.muted, fontSize: 12 }}>
                  No fundamental data yet
                </div>
              )}
            </div>

            {/* Active Signals */}
            <div style={{ background: dark.card, border: `1px solid ${dark.border}`, borderRadius: 8, padding: 12 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: dark.muted }}>Active Signals</span>
                <button onClick={loadActiveSignals} style={{ fontSize: 11, padding: "2px 8px", background: dark.bg, border: `1px solid ${dark.border}`, borderRadius: 4, color: dark.muted, cursor: "pointer" }}>Refresh</button>
              </div>
              {activeSignals.length === 0 ? (
                <div style={{ textAlign: "center", padding: "20px 0", color: dark.muted, fontSize: 12 }}>No active signals</div>
              ) : (
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                  <thead>
                    <tr style={{ borderBottom: `1px solid ${dark.border}` }}>
                      {["Instrument", "Signal", "Dir", "Entry", "Score", "Status"].map(h => (
                        <th key={h} style={{ padding: "4px 6px", textAlign: "left", fontSize: 10, fontWeight: 600, color: dark.muted, textTransform: "uppercase" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {activeSignals.map(s => {
                      const instr = instruments.find(i => i.id === s.instrument_id);
                      const score = toNum(s.composite_score) ?? 0;
                      return (
                        <tr key={s.id} style={{ borderBottom: `1px solid ${dark.border}` }}>
                          <td style={{ padding: "5px 6px" }}>
                            <div style={{ fontWeight: 600 }}>{instr?.name ?? `#${s.instrument_id}`}</div>
                            <div style={{ fontSize: 10, color: dark.muted }}>{s.timeframe}</div>
                          </td>
                          <td style={{ padding: "5px 6px" }}>
                            <span style={{ fontSize: 10, fontWeight: 700, padding: "1px 5px", borderRadius: 3,
                              background: s.direction === "LONG" ? "#0d3b1e" : s.direction === "SHORT" ? "#3b0d0d" : "#1e293b",
                              color: dirColor(s.direction) }}>
                              {s.signal_strength?.replace("_", " ")}
                            </span>
                          </td>
                          <td style={{ padding: "5px 6px", color: dirColor(s.direction), fontWeight: 700 }}>{s.direction}</td>
                          <td style={{ padding: "5px 6px", fontFamily: "monospace", fontSize: 11 }}>{fmtPrice(s.entry_price)}</td>
                          <td style={{ padding: "5px 6px", fontWeight: 600, color: score >= 0 ? "#22c55e" : "#ef4444" }}>{fmtScore(score)}</td>
                          <td style={{ padding: "5px 6px" }}>
                            {(() => {
                              const st = SIGNAL_STATUS[s.status] ?? { color: "#8b949e", bg: "#8b949e18", label: s.status?.toUpperCase() };
                              return (
                                <span style={{ fontSize: 10, padding: "1px 6px", borderRadius: 4, fontFamily: "monospace", fontWeight: 700,
                                  background: st.bg, color: st.color, border: `1px solid ${st.color}40` }}>
                                  {signalStatusLabel(s.status)}
                                </span>
                              );
                            })()}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          </div>
          {/* Row 3: Economic Calendar */}
          {calendarEvents.length > 0 && (
            <div style={{ background: dark.card, border: `1px solid ${dark.border}`, borderRadius: 8, padding: 12 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: dark.muted, marginBottom: 8 }}>
                Economic Calendar <span style={{ fontSize: 10, fontWeight: 400 }}>(next 48h)</span>
              </div>
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                  <thead>
                    <tr style={{ borderBottom: `1px solid ${dark.border}` }}>
                      {["Time (UTC)", "Currency", "Event", "Impact", "Forecast", "Previous"].map(h => (
                        <th key={h} style={{ padding: "4px 8px", textAlign: "left", fontSize: 10, fontWeight: 600, color: dark.muted, textTransform: "uppercase", whiteSpace: "nowrap" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {calendarEvents.map((ev, i) => {
                      const dt = new Date(ev.event_date);
                      const impactColor = ev.impact === "HIGH" ? "#ef4444" : ev.impact === "MEDIUM" ? "#f59e0b" : dark.muted;
                      return (
                        <tr key={i} style={{ borderBottom: `1px solid ${dark.border}` }}>
                          <td style={{ padding: "5px 8px", fontFamily: "monospace", whiteSpace: "nowrap", color: dark.muted }}>
                            {dt.toISOString().slice(0, 10)} {dt.toISOString().slice(11, 16)}
                          </td>
                          <td style={{ padding: "5px 8px", fontWeight: 700 }}>{ev.currency}</td>
                          <td style={{ padding: "5px 8px" }}>{ev.event}</td>
                          <td style={{ padding: "5px 8px" }}>
                            <span style={{ color: impactColor, fontWeight: 600, fontSize: 10 }}>{ev.impact}</span>
                          </td>
                          <td style={{ padding: "5px 8px", fontFamily: "monospace" }}>{ev.forecast ?? "—"}</td>
                          <td style={{ padding: "5px 8px", fontFamily: "monospace" }}>{ev.previous ?? "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </div>

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
