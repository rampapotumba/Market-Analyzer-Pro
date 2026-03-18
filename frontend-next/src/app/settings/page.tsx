"use client";

import { useState, useEffect, useCallback } from "react";

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

interface SettingField {
  key: string;
  label: string;
  description: string;
  type: "text" | "number" | "select" | "toggle";
  options?: string[];
  defaultValue: string | number | boolean;
}

const SETTINGS: { section: string; fields: SettingField[] }[] = [
  {
    section: "Signal Filters",
    fields: [
      {
        key: "min_score",
        label: "Min Composite Score",
        description: "Minimum absolute score to display a signal (BUY threshold = 7.0, max ≈ 24.75)",
        type: "number",
        defaultValue: 7.0,
      },
      {
        key: "min_rr",
        label: "Min Risk:Reward",
        description: "Minimum R:R ratio to display a signal",
        type: "number",
        defaultValue: 1.5,
      },
      {
        key: "default_timeframe",
        label: "Default Timeframe",
        description: "Default timeframe filter for signals view",
        type: "select",
        options: ["ALL", "M15", "H1", "H4", "D1"],
        defaultValue: "H1",
      },
    ],
  },
  {
    section: "Display",
    fields: [
      {
        key: "show_expired",
        label: "Show Expired Signals",
        description: "Include expired/cancelled signals in the default view",
        type: "toggle",
        defaultValue: false,
      },
      {
        key: "compact_mode",
        label: "Compact Mode",
        description: "Use smaller row heights in tables",
        type: "toggle",
        defaultValue: false,
      },
    ],
  },
];

const STORAGE_KEY = "map_settings";

function loadSettings(): Record<string, string | number | boolean> {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
  } catch {
    return {};
  }
}

// ── Status section ────────────────────────────────────────────────────────────

interface ServiceStatus {
  name: string;
  url: string;
  status: "ok" | "error" | "loading";
  detail?: string;
}

async function checkService(url: string): Promise<{ ok: boolean; detail?: string }> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(4000) });
    if (res.ok) {
      const d = await res.json().catch(() => ({}));
      // Proxied endpoints return { status: "ok"|"error", detail? }
      if (d.status === "error") return { ok: false, detail: d.detail ?? "error" };
      return { ok: true, detail: d.version ?? d.model ?? undefined };
    }
    return { ok: false, detail: `HTTP ${res.status}` };
  } catch {
    return { ok: false, detail: "unreachable" };
  }
}

function StatusDot({ status }: { status: ServiceStatus["status"] }) {
  const color = status === "ok" ? C.green : status === "error" ? C.red : C.yellow;
  return (
    <span style={{
      display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
      background: color, flexShrink: 0,
    }} />
  );
}

// ── Input styles ──────────────────────────────────────────────────────────────

const inputStyle = {
  borderRadius: 6, border: `1px solid ${C.border}`, background: '#0d1117',
  padding: '6px 12px', fontSize: 13, color: C.text, fontFamily: 'monospace',
  outline: 'none',
};

// ── SettingRow ────────────────────────────────────────────────────────────────

function SettingRow({
  field,
  value,
  onChange,
}: {
  field: SettingField;
  value: string | number | boolean;
  onChange: (v: string | number | boolean) => void;
}) {
  return (
    <div style={{
      display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
      padding: '14px 0',
    }}>
      <div style={{ flex: 1, marginRight: 32 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: C.text, fontFamily: 'monospace' }}>{field.label}</div>
        <div style={{ fontSize: 11, color: C.muted, marginTop: 2, fontFamily: 'monospace' }}>{field.description}</div>
      </div>
      <div style={{ flexShrink: 0 }}>
        {field.type === "text" && (
          <input type="text" value={value as string} onChange={(e) => onChange(e.target.value)}
            style={{ ...inputStyle, width: 224 }} />
        )}
        {field.type === "number" && (
          <input type="number" value={value as number} step="0.1"
            onChange={(e) => onChange(parseFloat(e.target.value))}
            style={{ ...inputStyle, width: 96 }} />
        )}
        {field.type === "select" && (
          <select value={value as string} onChange={(e) => onChange(e.target.value)} style={inputStyle}>
            {field.options?.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
          </select>
        )}
        {field.type === "toggle" && (
          <button onClick={() => onChange(!value)} style={{
            position: 'relative', display: 'inline-flex', height: 24, width: 44,
            alignItems: 'center', borderRadius: 999, cursor: 'pointer',
            background: value ? C.accent : C.border, border: 'none', transition: 'background 0.2s',
          }}>
            <span style={{
              display: 'inline-block', height: 16, width: 16, borderRadius: '50%',
              background: '#fff', transition: 'transform 0.2s',
              transform: value ? 'translateX(24px)' : 'translateX(4px)',
            }} />
          </button>
        )}
      </div>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const [values, setValues] = useState<Record<string, string | number | boolean>>({});
  const [saved, setSaved] = useState(false);
  const [services, setServices] = useState<ServiceStatus[]>([
    { name: "Backend API",   url: "/api/v2/health",        status: "loading" },
    { name: "FinBERT NLP",   url: "/api/v2/health/finbert",       status: "loading" },
  ]);

  // Load from localStorage on mount
  useEffect(() => {
    const stored = loadSettings();
    const defaults: Record<string, string | number | boolean> = {};
    for (const { fields } of SETTINGS)
      for (const f of fields)
        defaults[f.key] = stored[f.key] ?? f.defaultValue;
    setValues(defaults);
  }, []);

  // Check service statuses
  const checkAll = useCallback(async () => {
    setServices((s) => s.map((svc) => ({ ...svc, status: "loading" })));
    const results = await Promise.all(
      services.map((svc) => checkService(svc.url))
    );
    setServices((s) =>
      s.map((svc, i) => ({
        ...svc,
        status: results[i].ok ? "ok" : "error",
        detail: results[i].detail,
      }))
    );
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { checkAll(); }, [checkAll]);

  function handleChange(key: string, val: string | number | boolean) {
    setValues((v) => ({ ...v, [key]: val }));
  }

  function handleSave() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(values));
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  function handleReset() {
    localStorage.removeItem(STORAGE_KEY);
    const defaults: Record<string, string | number | boolean> = {};
    for (const { fields } of SETTINGS)
      for (const f of fields)
        defaults[f.key] = f.defaultValue;
    setValues(defaults);
  }

  return (
    <div style={{ maxWidth: 640, margin: '0 auto', padding: '24px 16px', display: 'flex', flexDirection: 'column', gap: 24 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, color: C.text, fontFamily: 'monospace', margin: 0 }}>Settings</h1>

      {/* Setting sections */}
      {SETTINGS.map(({ section, fields }) => (
        <div key={section} style={{ borderRadius: 8, border: `1px solid ${C.border}`, background: C.card, overflow: 'hidden' }}>
          <div style={{ background: C.bg, borderBottom: `1px solid ${C.border}`, padding: '10px 16px' }}>
            <h2 style={{ fontSize: 12, fontWeight: 600, color: C.muted, fontFamily: 'monospace', textTransform: 'uppercase', letterSpacing: '0.5px', margin: 0 }}>{section}</h2>
          </div>
          <div style={{ padding: '0 16px' }}>
            {fields.map((field, i) => (
              <div key={field.key} style={{ borderBottom: i < fields.length - 1 ? `1px solid ${C.border}` : 'none' }}>
                <SettingRow
                  field={field}
                  value={values[field.key] ?? field.defaultValue}
                  onChange={(v) => handleChange(field.key, v)}
                />
              </div>
            ))}
          </div>
        </div>
      ))}

      {/* Save / Reset */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <button onClick={handleSave} style={{
          borderRadius: 6, background: C.accent, color: '#fff',
          padding: '8px 18px', fontSize: 13, fontWeight: 600,
          fontFamily: 'monospace', border: 'none', cursor: 'pointer',
        }}>
          Save Settings
        </button>
        <button onClick={handleReset} style={{
          borderRadius: 6, background: 'transparent', color: C.muted,
          padding: '8px 18px', fontSize: 13, fontWeight: 600,
          fontFamily: 'monospace', border: `1px solid ${C.border}`, cursor: 'pointer',
        }}>
          Reset to Defaults
        </button>
        {saved && (
          <span style={{ fontSize: 13, color: C.green, fontWeight: 600, fontFamily: 'monospace' }}>
            ✓ Saved
          </span>
        )}
      </div>

      {/* Service Status */}
      <div style={{ borderRadius: 8, border: `1px solid ${C.border}`, background: C.card, overflow: 'hidden' }}>
        <div style={{ background: C.bg, borderBottom: `1px solid ${C.border}`, padding: '10px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ fontSize: 12, fontWeight: 600, color: C.muted, fontFamily: 'monospace', textTransform: 'uppercase', letterSpacing: '0.5px', margin: 0 }}>Service Status</h2>
          <button onClick={checkAll} style={{ fontSize: 11, color: C.accent, background: 'transparent', border: 'none', cursor: 'pointer', fontFamily: 'monospace', fontWeight: 600 }}>
            ↻ Recheck
          </button>
        </div>
        <div style={{ padding: '0 16px' }}>
          {services.map((svc, i) => (
            <div key={svc.name} style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '12px 0', borderBottom: i < services.length - 1 ? `1px solid ${C.border}` : 'none',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <StatusDot status={svc.status} />
                <span style={{ fontSize: 13, color: C.text, fontFamily: 'monospace' }}>{svc.name}</span>
              </div>
              <span style={{ fontSize: 11, color: svc.status === "ok" ? C.green : svc.status === "error" ? C.red : C.yellow, fontFamily: 'monospace' }}>
                {svc.status === "loading" ? "checking…" : svc.status === "ok" ? `ok${svc.detail ? ` · ${svc.detail}` : ""}` : svc.detail ?? "error"}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* About */}
      <div style={{ borderRadius: 8, border: `1px solid ${C.border}`, background: C.card, padding: 16 }}>
        <h2 style={{ fontSize: 12, fontWeight: 600, color: C.muted, marginBottom: 12, fontFamily: 'monospace', textTransform: 'uppercase', letterSpacing: '0.5px' }}>About</h2>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: C.muted, fontFamily: 'monospace' }}>
          <p style={{ margin: 0 }}>Market Analyzer Pro v3</p>
          <p style={{ margin: 0 }}>Frontend: Next.js 14 + TypeScript + inline styles</p>
          <p style={{ margin: 0 }}>Backend: FastAPI + Celery + PostgreSQL/TimescaleDB + Redis</p>
          <p style={{ margin: 0 }}>NLP: FinBERT (ProsusAI/finbert) sentiment analysis</p>
        </div>
      </div>
    </div>
  );
}
