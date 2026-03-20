"use client";

import { useState, useEffect, useCallback, useRef } from "react";

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

type ActionState = "idle" | "loading" | "done" | "error";

// ── Confirm Modal ─────────────────────────────────────────────────────────────

function ConfirmModal({
  title,
  message,
  confirmLabel,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onCancel]);

  return (
    <div
      onClick={onCancel}
      style={{
        position: "fixed", inset: 0, zIndex: 1000,
        background: "rgba(0,0,0,0.6)", display: "flex",
        alignItems: "center", justifyContent: "center",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "#161b22", border: "1px solid #30363d",
          borderRadius: 10, padding: "28px 32px", maxWidth: 420, width: "100%",
          display: "flex", flexDirection: "column", gap: 16,
        }}
      >
        <div style={{ fontSize: 16, fontWeight: 700, color: "#e6edf3", fontFamily: "monospace" }}>{title}</div>
        <div style={{ fontSize: 13, color: "#8b949e", fontFamily: "monospace", lineHeight: 1.6 }}>{message}</div>
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", marginTop: 4 }}>
          <button onClick={onCancel} style={{
            borderRadius: 6, border: "1px solid #30363d", background: "transparent",
            color: "#8b949e", padding: "8px 18px", fontSize: 13, fontWeight: 600,
            fontFamily: "monospace", cursor: "pointer",
          }}>
            Cancel
          </button>
          <button onClick={onConfirm} style={{
            borderRadius: 6, border: "none", background: "#ef4444",
            color: "#fff", padding: "8px 18px", fontSize: 13, fontWeight: 600,
            fontFamily: "monospace", cursor: "pointer",
          }}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function SettingsPage() {
  const [values, setValues] = useState<Record<string, string | number | boolean>>({});
  const [saved, setSaved] = useState(false);
  const [modal, setModal] = useState<"sim_reset" | "log_clear" | null>(null);
  const [simState, setSimState] = useState<ActionState>("idle");
  const [simResult, setSimResult] = useState("");
  const [logState, setLogState] = useState<ActionState>("idle");
  const [logResult, setLogResult] = useState("");
  const [services, setServices] = useState<ServiceStatus[]>([
    { name: "Backend API",  url: "/api/v2/health",           status: "loading" },
    { name: "PostgreSQL",   url: "/api/v2/health/postgres",  status: "loading" },
    { name: "Redis",        url: "/api/v2/health/redis",     status: "loading" },
    { name: "Scheduler",   url: "/api/v2/health/scheduler", status: "loading" },
    { name: "FinBERT NLP",  url: "/api/v2/health/finbert",   status: "loading" },
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

  async function execSimReset() {
    setModal(null);
    setSimState("loading");
    try {
      const res = await fetch("/api/v2/simulator/reset", { method: "POST" });
      const data = await res.json();
      if (res.ok && data.ok) {
        setSimResult(`Deleted ${data.deleted_positions} positions, ${data.deleted_signals} signals · Balance → $${data.balance_restored_to}`);
        setSimState("done");
      } else {
        setSimResult("Reset failed");
        setSimState("error");
      }
    } catch {
      setSimResult("Network error");
      setSimState("error");
    }
    setTimeout(() => setSimState("idle"), 6000);
  }

  async function execLogClear() {
    setModal(null);
    setLogState("loading");
    try {
      const res = await fetch("/api/v2/system/logs/clear", { method: "POST" });
      const data = await res.json();
      if (res.ok && data.ok) {
        setLogResult(`Deleted ${data.deleted} log entries`);
        setLogState("done");
      } else {
        setLogResult("Clear failed");
        setLogState("error");
      }
    } catch {
      setLogResult("Network error");
      setLogState("error");
    }
    setTimeout(() => setLogState("idle"), 6000);
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

      {/* Danger Zone */}
      <div style={{ borderRadius: 8, border: `1px solid #3d1515`, background: '#1a0d0d', overflow: 'hidden' }}>
        <div style={{ background: '#200e0e', borderBottom: '1px solid #3d1515', padding: '10px 16px' }}>
          <h2 style={{ fontSize: 12, fontWeight: 600, color: C.red, fontFamily: 'monospace', textTransform: 'uppercase', letterSpacing: '0.5px', margin: 0 }}>
            Danger Zone
          </h2>
        </div>

        {/* Row: Reset Simulator */}
        <div style={{ padding: '16px 16px 14px', borderBottom: '1px solid #3d1515', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 24, flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: C.text, fontFamily: 'monospace' }}>Reset Simulator</div>
            <div style={{ fontSize: 11, color: C.muted, marginTop: 3, fontFamily: 'monospace' }}>
              Delete all positions and signals · restore balance to $1000
            </div>
            {(simState === "done" || simState === "error") && (
              <div style={{ fontSize: 11, marginTop: 6, fontFamily: 'monospace', color: simState === "done" ? C.green : C.red }}>
                {simState === "done" ? "✓ " : "✕ "}{simResult}
              </div>
            )}
          </div>
          <button
            onClick={() => setModal("sim_reset")}
            disabled={simState === "loading"}
            style={{
              flexShrink: 0, borderRadius: 6, border: '1px solid #3d1515',
              background: 'transparent', color: C.red,
              padding: '7px 16px', fontSize: 12, fontWeight: 600,
              fontFamily: 'monospace', cursor: simState === "loading" ? 'default' : 'pointer',
              opacity: simState === "loading" ? 0.6 : 1,
            }}
          >
            {simState === "loading" ? "Resetting…" : "Reset Simulator"}
          </button>
        </div>

        {/* Row: Clear Logs */}
        <div style={{ padding: '14px 16px 16px', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 24, flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: C.text, fontFamily: 'monospace' }}>Clear System Logs</div>
            <div style={{ fontSize: 11, color: C.muted, marginTop: 3, fontFamily: 'monospace' }}>
              Delete all entries from the system event log
            </div>
            {(logState === "done" || logState === "error") && (
              <div style={{ fontSize: 11, marginTop: 6, fontFamily: 'monospace', color: logState === "done" ? C.green : C.red }}>
                {logState === "done" ? "✓ " : "✕ "}{logResult}
              </div>
            )}
          </div>
          <button
            onClick={() => setModal("log_clear")}
            disabled={logState === "loading"}
            style={{
              flexShrink: 0, borderRadius: 6, border: '1px solid #3d1515',
              background: 'transparent', color: C.red,
              padding: '7px 16px', fontSize: 12, fontWeight: 600,
              fontFamily: 'monospace', cursor: logState === "loading" ? 'default' : 'pointer',
              opacity: logState === "loading" ? 0.6 : 1,
            }}
          >
            {logState === "loading" ? "Clearing…" : "Clear Logs"}
          </button>
        </div>
      </div>

      {/* Confirm modals */}
      {modal === "sim_reset" && (
        <ConfirmModal
          title="Reset Simulator?"
          message="This will delete ALL positions and signals, and restore the virtual balance to $1000. This action cannot be undone."
          confirmLabel="Yes, reset everything"
          onConfirm={execSimReset}
          onCancel={() => setModal(null)}
        />
      )}
      {modal === "log_clear" && (
        <ConfirmModal
          title="Clear System Logs?"
          message="This will permanently delete all entries from the system event log. This action cannot be undone."
          confirmLabel="Yes, clear all logs"
          onConfirm={execLogClear}
          onCancel={() => setModal(null)}
        />
      )}

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
