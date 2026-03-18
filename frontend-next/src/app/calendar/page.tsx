"use client";

import { useEffect, useState, useCallback } from "react";
import { Tooltip } from "@/components/Tooltip";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const C = {
  bg: '#0d1117',
  card: '#161b22',
  border: '#30363d',
  text: '#e6edf3',
  muted: '#8b949e',
  accent: '#58a6ff',
  red: '#ef4444',
  yellow: '#f59e0b',
};

interface CalendarEvent {
  currency: string;
  event: string;
  impact: "HIGH" | "MEDIUM" | "LOW" | string;
  event_date: string;
  forecast?: number | null;
  previous?: number | null;
}

const IMPACT_CHIPS = [
  { key: "ALL",    label: "TOTAL",  color: "#8b949e" },
  { key: "HIGH",   label: "HIGH",   color: "#ef4444" },
  { key: "MEDIUM", label: "MEDIUM", color: "#f59e0b" },
  { key: "LOW",    label: "LOW",    color: "#8b949e" },
] as const;
const IMPACT_COLOR: Record<string, string> = {
  HIGH: C.red,
  MEDIUM: C.yellow,
  LOW: C.muted,
};

function impactColor(impact: string) {
  return IMPACT_COLOR[impact.toUpperCase()] ?? C.muted;
}

export default function CalendarPage() {
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [impactFilter, setImpactFilter] = useState("ALL");
  const [currencyFilter, setCurrencyFilter] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/v2/macroeconomics/calendar`);
      if (res.ok) setEvents(await res.json());
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = events.filter((ev) => {
    if (impactFilter !== "ALL" && ev.impact.toUpperCase() !== impactFilter) return false;
    if (currencyFilter && !ev.currency.toLowerCase().includes(currencyFilter.toLowerCase())) return false;
    return true;
  });

  const counts = {
    ALL: events.length,
    HIGH: events.filter((e) => e.impact.toUpperCase() === "HIGH").length,
    MEDIUM: events.filter((e) => e.impact.toUpperCase() === "MEDIUM").length,
    LOW: events.filter((e) => e.impact.toUpperCase() === "LOW").length,
  };

  const thStyle = {
    padding: '8px 12px', textAlign: 'left' as const, fontSize: 10,
    fontWeight: 600, color: C.muted, textTransform: 'uppercase' as const,
    letterSpacing: '0.5px', fontFamily: 'monospace',
    borderBottom: `1px solid ${C.border}`, whiteSpace: 'nowrap' as const,
  };

  return (
    <div style={{ maxWidth: 1280, margin: '0 auto', padding: '24px 16px', display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, color: C.text, fontFamily: 'monospace', margin: 0 }}>Economic Calendar</h1>
          <p style={{ fontSize: 12, color: C.muted, fontFamily: 'monospace', marginTop: 4 }}>Upcoming macro events · next 14 days</p>
        </div>
        {/* Impact chips — clickable filters */}
        <div style={{ display: 'flex', gap: 8 }}>
          {IMPACT_CHIPS.map(({ key, label, color }) => {
            const active = impactFilter === key;
            return (
              <button
                key={key}
                onClick={() => setImpactFilter(key)}
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: 6,
                  borderRadius: 999, border: `1px solid ${active ? color : `${color}40`}`,
                  padding: '4px 12px', fontSize: 11, fontWeight: 600,
                  fontFamily: 'monospace', background: active ? `${color}20` : `${color}10`,
                  color, cursor: 'pointer', transition: 'all 0.15s',
                }}
              >
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: color, display: 'inline-block' }} />
                {label}: {counts[key as keyof typeof counts]}
              </button>
            );
          })}
        </div>
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        {/* Currency search */}
        <input
          type="text"
          placeholder="Currency (USD, EUR...)"
          value={currencyFilter}
          onChange={(e) => setCurrencyFilter(e.target.value)}
          style={{
            borderRadius: 6, border: `1px solid ${C.border}`, background: C.card,
            padding: '6px 12px', fontSize: 12, color: C.text, fontFamily: 'monospace',
            outline: 'none', width: 180,
          }}
        />

        <button
          onClick={load}
          style={{ fontSize: 12, color: C.accent, background: 'transparent', border: 'none', cursor: 'pointer', fontFamily: 'monospace', fontWeight: 600, marginLeft: 'auto' }}
        >
          ↻ Refresh
        </button>
      </div>

      {/* Table */}
      <div style={{ borderRadius: 8, border: `1px solid ${C.border}`, background: C.card, overflow: 'hidden' }}>
        {loading ? (
          <div style={{ padding: 64, textAlign: 'center', color: C.muted, fontSize: 13, fontFamily: 'monospace' }}>Loading…</div>
        ) : filtered.length === 0 ? (
          <div style={{ padding: 64, textAlign: 'center', color: C.muted, fontSize: 13, fontFamily: 'monospace' }}>No events found</div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ background: C.bg }}>
                  {["Time (local)", "Currency", "Event", "Impact", "Forecast", "Previous"].map((h) => (
                    <th key={h} style={thStyle}><Tooltip term={h}>{h}</Tooltip></th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map((ev, i) => {
                  const dt = new Date(ev.event_date);
                  const dateStr = dt.toLocaleDateString(undefined, { year: "numeric", month: "2-digit", day: "2-digit" });
                  const timeStr = dt.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
                  const iCol = impactColor(ev.impact);
                  return (
                    <tr key={i} style={{ borderBottom: `1px solid ${C.border}` }}>
                      <td style={{ padding: '8px 12px', fontFamily: 'monospace', whiteSpace: 'nowrap', color: C.muted, fontSize: 12 }}>
                        <span style={{ color: C.text }}>{dateStr}</span>
                        {' '}
                        <span>{timeStr}</span>
                      </td>
                      <td style={{ padding: '8px 12px', fontWeight: 700, color: C.text, fontFamily: 'monospace' }}>{ev.currency}</td>
                      <td style={{ padding: '8px 12px', color: C.text }}>{ev.event}</td>
                      <td style={{ padding: '8px 12px' }}>
                        <span style={{
                          color: iCol, fontWeight: 700, fontSize: 11,
                          fontFamily: 'monospace', background: `${iCol}15`,
                          border: `1px solid ${iCol}40`, borderRadius: 4,
                          padding: '2px 7px',
                        }}>
                          {ev.impact.toUpperCase()}
                        </span>
                      </td>
                      <td style={{ padding: '8px 12px', fontFamily: 'monospace', color: C.text }}>{ev.forecast ?? "—"}</td>
                      <td style={{ padding: '8px 12px', fontFamily: 'monospace', color: C.muted }}>{ev.previous ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <p style={{ fontSize: 11, color: C.muted, textAlign: 'right', fontFamily: 'monospace' }}>
        {filtered.length} event{filtered.length !== 1 ? "s" : ""} shown
      </p>
    </div>
  );
}
