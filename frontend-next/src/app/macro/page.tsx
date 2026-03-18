"use client";

import { useEffect, useState, useCallback } from "react";
import { api, type MacroData } from "@/lib/api";
import { Tooltip } from "@/components/Tooltip";

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

// ── Human-readable names ────────────────────────────────────────────────────

const INDICATOR_LABELS: Record<string, string> = {
  DXY:                  "Dollar Index",
  VIX:                  "Volatility (VIX)",
  TNX:                  "10Y Treasury Yield",
  FUNDING_RATE_BTC:     "BTC Funding Rate",
  FUNDING_RATE_ETH:     "ETH Funding Rate",
  FUNDING_RATE_SOL:     "SOL Funding Rate",
  FEDFUNDS:             "Fed Funds Rate",
  UNRATE:               "Unemployment Rate",
  CPIAUCSL:             "CPI (Inflation)",
  PAYEMS:               "Nonfarm Payrolls",
  INDPRO:               "Industrial Production",
  GDPC1:                "GDP (Real, $B)",
  HOUST:                "Housing Starts",
  RETAILSMNSA:          "Retail Sales",
  "COT_NET_EURUSD=X":   "COT EUR/USD",
  "COT_NET_USDJPY=X":   "COT USD/JPY",
  "COT_NET_GBPUSD=X":   "COT GBP/USD",
  "COT_NET_SPY":        "COT S&P 500",
  "COT_NET_BTC/USDT":   "COT Bitcoin",
};

// ── Value formatting ────────────────────────────────────────────────────────

function fmtValue(indicator: string, value: number): string {
  if (indicator.startsWith("FUNDING_RATE_")) {
    return (value * 100).toFixed(4) + "%";
  }
  if (indicator === "PAYEMS") {
    return (value / 1000).toFixed(1) + "K";
  }
  if (indicator === "RETAILSMNSA") {
    return "$" + (value / 1000).toFixed(0) + "B";
  }
  if (indicator === "GDPC1") {
    return "$" + value.toFixed(0) + "B";
  }
  if (indicator === "HOUST") {
    return value.toFixed(0) + "K";
  }
  if (indicator.startsWith("COT_NET_")) {
    const abs = Math.abs(value);
    const sign = value < 0 ? "−" : "+";
    if (abs >= 1000) return sign + (abs / 1000).toFixed(1) + "K";
    return sign + abs.toFixed(0);
  }
  if (indicator === "UNRATE" || indicator === "FEDFUNDS") {
    return value.toFixed(2) + "%";
  }
  return value.toFixed(2);
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

// ── Deduplication ────────────────────────────────────────────────────────────

interface MacroItem {
  indicator: string;
  country: string;
  value: number;
  prevValue: number | null;
  release_date: string;
}

function dedupe(raw: MacroData[]): MacroItem[] {
  const groups = new Map<string, MacroData[]>();
  for (const d of raw) {
    const key = `${d.country}::${d.indicator}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(d);
  }
  const result: MacroItem[] = [];
  for (const records of groups.values()) {
    records.sort((a, b) => b.release_date.localeCompare(a.release_date));
    const latest = records[0];
    const prev = records[1] ?? null;
    result.push({
      indicator: latest.indicator,
      country: latest.country,
      value: latest.value,
      prevValue: prev ? prev.value : null,
      release_date: latest.release_date,
    });
  }
  return result;
}

// ── Card component ───────────────────────────────────────────────────────────

function MacroCard({ item }: { item: MacroItem }) {
  const label = INDICATOR_LABELS[item.indicator] ?? item.indicator;
  const valStr = fmtValue(item.indicator, item.value);

  const delta = item.prevValue !== null && item.prevValue !== item.value
    ? item.value - item.prevValue : null;
  const arrow = delta === null ? null : delta > 0 ? "↑" : "↓";
  const arrowColor = delta === null ? C.muted : delta > 0 ? C.green : C.red;

  // For COT and some indicators, up is not necessarily good — just show direction
  const isFundingRate = item.indicator.startsWith("FUNDING_RATE_");
  const deltaStr = delta !== null
    ? isFundingRate
      ? (delta * 100).toFixed(4) + "%"
      : item.indicator.startsWith("COT_NET_")
        ? (Math.abs(delta) >= 1000 ? (Math.abs(delta) / 1000).toFixed(1) + "K" : Math.abs(delta).toFixed(0))
        : Math.abs(delta) >= 1000
          ? (Math.abs(delta) / 1000).toFixed(1) + "K"
          : Math.abs(delta).toFixed(2)
    : null;

  return (
    <div style={{
      borderRadius: 8, border: `1px solid ${C.border}`, background: C.card,
      padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 6,
    }}>
      <div style={{ fontSize: 11, color: C.muted, fontFamily: 'monospace', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
        <Tooltip term={label}>{label}</Tooltip>
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontSize: 22, fontWeight: 700, color: C.text, fontFamily: 'monospace' }}>
          {valStr}
        </span>
        {arrow && (
          <span style={{ fontSize: 13, fontWeight: 600, color: arrowColor, fontFamily: 'monospace' }}>
            {arrow} {deltaStr}
          </span>
        )}
        {delta === 0 && (
          <span style={{ fontSize: 12, color: C.muted, fontFamily: 'monospace' }}>—</span>
        )}
      </div>
      <div style={{ fontSize: 10, color: C.muted, fontFamily: 'monospace' }}>
        {fmtDate(item.release_date)}
      </div>
    </div>
  );
}

// ── Section ──────────────────────────────────────────────────────────────────

function Section({ title, subtitle, items }: { title: string; subtitle: string; items: MacroItem[] }) {
  if (items.length === 0) return null;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div>
        <div style={{ fontSize: 13, fontWeight: 600, color: C.text, fontFamily: 'monospace' }}>{title}</div>
        <div style={{ fontSize: 11, color: C.muted, fontFamily: 'monospace', marginTop: 2 }}>{subtitle}</div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 10 }}>
        {items.map((item) => <MacroCard key={`${item.country}::${item.indicator}`} item={item} />)}
      </div>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

const SECTION_ORDER = ["GLOBAL", "US", "EU", "GB", "JP", "CN", "CA", "AU", "CH"];
const SECTION_LABELS: Record<string, [string, string]> = {
  GLOBAL: ["Global Market",    "Real-time market indicators · updated hourly"],
  US:     ["United States",    "FRED macro data + COT positions · monthly/weekly"],
  EU:     ["Euro Zone",        "ECB macro data"],
  GB:     ["United Kingdom",   "BOE macro data"],
  JP:     ["Japan",            "BOJ macro data"],
  CN:     ["China",            "PBOC macro data"],
  CA:     ["Canada",           "BOC macro data"],
  AU:     ["Australia",        "RBA macro data"],
  CH:     ["Switzerland",      "SNB macro data"],
};

export default function MacroPage() {
  const [items, setItems] = useState<MacroItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    api.getMacroData()
      .then((raw) => setItems(dedupe(raw)))
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = search
    ? items.filter((d) =>
        d.indicator.toLowerCase().includes(search.toLowerCase()) ||
        (INDICATOR_LABELS[d.indicator] ?? "").toLowerCase().includes(search.toLowerCase()) ||
        d.country.toLowerCase().includes(search.toLowerCase())
      )
    : items;

  const byCountry = new Map<string, MacroItem[]>();
  for (const item of filtered) {
    if (!byCountry.has(item.country)) byCountry.set(item.country, []);
    byCountry.get(item.country)!.push(item);
  }

  const sections = SECTION_ORDER
    .filter((c) => byCountry.has(c))
    .map((c) => ({ country: c, items: byCountry.get(c)! }));

  // Countries not in SECTION_ORDER
  for (const [country, its] of byCountry) {
    if (!SECTION_ORDER.includes(country)) sections.push({ country, items: its });
  }

  return (
    <div style={{ maxWidth: 1280, margin: '0 auto', padding: '24px 16px', display: 'flex', flexDirection: 'column', gap: 28 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, color: C.text, fontFamily: 'monospace', margin: 0 }}>Macro Data</h1>
          <p style={{ fontSize: 12, color: C.muted, fontFamily: 'monospace', marginTop: 4 }}>
            {items.length} indicators · latest value per source
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input
            type="text"
            placeholder="Search indicator..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              borderRadius: 6, border: `1px solid ${C.border}`, background: C.card,
              padding: '6px 12px', fontSize: 12, color: C.text,
              fontFamily: 'monospace', outline: 'none', width: 200,
            }}
          />
          <button
            onClick={load}
            style={{ fontSize: 12, color: C.accent, background: 'transparent', border: 'none', cursor: 'pointer', fontFamily: 'monospace', fontWeight: 600 }}
          >
            ↻ Refresh
          </button>
        </div>
      </div>

      {/* Content */}
      {loading ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 10 }}>
          {Array.from({ length: 12 }).map((_, i) => (
            <div key={i} style={{ height: 90, borderRadius: 8, background: C.card, border: `1px solid ${C.border}` }} />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div style={{
          borderRadius: 8, border: `1px dashed ${C.border}`,
          padding: 64, textAlign: 'center', color: C.muted, fontSize: 13, fontFamily: 'monospace',
        }}>
          No macro data available
        </div>
      ) : (
        sections.map(({ country, items: its }) => {
          const [title, subtitle] = SECTION_LABELS[country] ?? [country, ""];
          return <Section key={country} title={title} subtitle={subtitle} items={its} />;
        })
      )}
    </div>
  );
}
