"use client";

import { useEffect, useState } from "react";
import { api, type Signal, type SignalDetail } from "@/lib/api";
import { SIGNAL_STATUS } from "@/lib/signalStatus";

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
  yellow: '#f0a500',
  purple: '#a371f7',
};

function LlmBadge({ bias, confidence }: { bias?: string | null; confidence?: number | null }) {
  if (!bias) {
    return <span style={{ fontSize: 11, color: C.muted, fontFamily: 'monospace' }}>N/A</span>;
  }
  const color =
    bias === "BULLISH" ? C.green :
    bias === "BEARISH" ? C.red :
    C.muted;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <span style={{
        display: 'inline-flex', borderRadius: 4, padding: '2px 6px',
        fontSize: 10, fontWeight: 700, fontFamily: 'monospace',
        color, background: `${color}18`, border: `1px solid ${color}40`,
      }}>
        {bias === "BULLISH" ? "▲" : bias === "BEARISH" ? "▼" : "—"} {bias}
      </span>
      {confidence != null && (
        <span style={{ fontSize: 10, color: C.muted, fontFamily: 'monospace' }}>
          {confidence.toFixed(0)}%
        </span>
      )}
    </span>
  );
}

function DetailPanel({ detail }: { detail: SignalDetail }) {
  const hasLlm = detail.llm_bias || detail.llm_reasoning || (detail.llm_key_factors?.length ?? 0) > 0;

  if (!hasLlm) {
    return (
      <tr>
        <td colSpan={12} style={{ padding: '12px 24px', background: '#0d1117', borderBottom: `1px solid ${C.border}` }}>
          <span style={{ fontSize: 12, color: C.muted, fontFamily: 'monospace' }}>
            Claude analysis not available for this signal (score below threshold).
          </span>
        </td>
      </tr>
    );
  }

  return (
    <tr>
      <td colSpan={12} style={{ padding: '16px 24px', background: '#0d1117', borderBottom: `1px solid ${C.border}` }}>
        <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>

          {/* Score block */}
          <div style={{ minWidth: 140 }}>
            <div style={{ fontSize: 10, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 6, fontFamily: 'monospace' }}>
              Claude Analysis
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <span style={{ fontSize: 11, color: C.muted, fontFamily: 'monospace', width: 80 }}>Bias</span>
                <LlmBadge bias={detail.llm_bias} confidence={detail.llm_confidence} />
              </div>
              {detail.llm_score != null && (
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span style={{ fontSize: 11, color: C.muted, fontFamily: 'monospace', width: 80 }}>Score</span>
                  <span style={{
                    fontSize: 12, fontFamily: 'monospace', fontWeight: 600,
                    color: detail.llm_score > 0 ? C.green : detail.llm_score < 0 ? C.red : C.muted,
                  }}>
                    {detail.llm_score > 0 ? '+' : ''}{detail.llm_score.toFixed(1)}
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* Key factors */}
          {(detail.llm_key_factors?.length ?? 0) > 0 && (
            <div style={{ minWidth: 200 }}>
              <div style={{ fontSize: 10, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 6, fontFamily: 'monospace' }}>
                Key Factors
              </div>
              <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 3 }}>
                {detail.llm_key_factors!.map((f, i) => (
                  <li key={i} style={{ fontSize: 12, color: C.text, fontFamily: 'monospace' }}>
                    <span style={{ color: C.purple, marginRight: 6 }}>›</span>{f}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Reasoning */}
          {detail.llm_reasoning && (
            <div style={{ flex: 1, minWidth: 260 }}>
              <div style={{ fontSize: 10, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 6, fontFamily: 'monospace' }}>
                Reasoning
              </div>
              <p style={{ margin: 0, fontSize: 12, color: C.text, lineHeight: 1.6 }}>
                {detail.llm_reasoning}
              </p>
            </div>
          )}

        </div>
      </td>
    </tr>
  );
}

function StatusBadge({ status }: { status: string }) {
  const s = SIGNAL_STATUS[status] ?? { color: C.muted, bg: `${C.muted}18`, label: status.toUpperCase() };
  return (
    <span style={{
      display: 'inline-flex', borderRadius: 4, padding: '2px 7px',
      fontSize: 10, fontWeight: 700, fontFamily: 'monospace',
      color: s.color, background: s.bg, border: `1px solid ${s.color}40`,
    }}>
      {s.label}
    </span>
  );
}

function SignalRow({
  signal,
  isExpanded,
  detail,
  loadingDetail,
  onToggle,
}: {
  signal: Signal;
  isExpanded: boolean;
  detail: SignalDetail | null;
  loadingDetail: boolean;
  onToggle: () => void;
}) {
  const dirColor =
    signal.direction === "LONG" ? C.green :
    signal.direction === "SHORT" ? C.red :
    C.muted;

  return (
    <>
      <tr
        onClick={onToggle}
        style={{
          borderBottom: isExpanded ? 'none' : `1px solid ${C.border}`,
          cursor: 'pointer',
          background: isExpanded ? '#12181f' : 'transparent',
          transition: 'background 0.15s',
        }}
      >
        <td style={{ padding: '10px 14px', fontSize: 13, fontWeight: 600, color: C.text, fontFamily: 'monospace' }}>
          {signal.symbol}
        </td>
        <td style={{ padding: '10px 14px', fontSize: 12, color: C.muted, fontFamily: 'monospace' }}>
          {signal.timeframe}
        </td>
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
        <td style={{ padding: '10px 14px', fontSize: 13, fontFamily: 'monospace', color: C.text }}>
          {signal.composite_score?.toFixed(1) ?? "—"}
        </td>
        <td style={{ padding: '10px 14px' }}>
          <LlmBadge bias={signal.llm_bias} confidence={signal.llm_confidence} />
        </td>
        <td style={{ padding: '10px 14px', fontSize: 12, fontFamily: 'monospace', color: C.text }}>
          {signal.entry_price?.toFixed(5) ?? "—"}
        </td>
        <td style={{ padding: '10px 14px', fontSize: 12, fontFamily: 'monospace', color: C.red }}>
          {signal.stop_loss?.toFixed(5) ?? "—"}
        </td>
        <td style={{ padding: '10px 14px', fontSize: 12, fontFamily: 'monospace', color: C.green }}>
          {signal.take_profit_1?.toFixed(5) ?? "—"}
        </td>
        <td style={{ padding: '10px 14px', fontSize: 13, color: C.text }}>
          {signal.risk_reward?.toFixed(1) ?? "—"}
        </td>
        <td style={{ padding: '10px 14px', fontSize: 12, color: C.muted }}>
          {signal.regime}
        </td>
        <td style={{ padding: '10px 14px' }}>
          <StatusBadge status={signal.status} />
        </td>
        <td style={{ padding: '10px 14px', fontSize: 11, color: C.muted, fontFamily: 'monospace' }}>
          {(() => {
            const d = new Date(signal.created_at);
            const pad = (n: number) => String(n).padStart(2, '0');
            return `${pad(d.getDate())}.${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
          })()}
        </td>
      </tr>

      {isExpanded && (
        loadingDetail ? (
          <tr>
            <td colSpan={12} style={{ padding: '12px 24px', background: '#0d1117', borderBottom: `1px solid ${C.border}` }}>
              <span style={{ fontSize: 12, color: C.muted, fontFamily: 'monospace' }}>Loading...</span>
            </td>
          </tr>
        ) : detail ? (
          <DetailPanel detail={detail} />
        ) : null
      )}
    </>
  );
}

export default function SignalsPage() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [tab, setTab] = useState(STATUS_TABS[0]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [detailCache, setDetailCache] = useState<Record<number, SignalDetail>>({});
  const [loadingDetail, setLoadingDetail] = useState(false);

  useEffect(() => {
    setLoading(true);
    api
      .getSignals(tab.value)
      .then(setSignals)
      .catch(() => setSignals([]))
      .finally(() => setLoading(false));
  }, [tab]);

  function handleToggle(id: number) {
    if (expandedId === id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(id);
    if (!detailCache[id]) {
      setLoadingDetail(true);
      api.getSignal(id)
        .then((d) => setDetailCache((prev) => ({ ...prev, [id]: d })))
        .catch(() => {})
        .finally(() => setLoadingDetail(false));
    }
  }

  const HEADERS = ["Symbol", "TF", "Direction", "Score", "Claude", "Entry", "SL", "TP1", "R:R", "Regime", "Status", "Date"];

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
              padding: '8px 14px', fontSize: 12, fontWeight: 600, fontFamily: 'monospace',
              background: 'transparent', border: 'none',
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
                {HEADERS.map((h) => (
                  <th
                    key={h}
                    style={{
                      padding: '10px 14px', textAlign: 'left', fontSize: 10,
                      fontWeight: 600, color: h === "Claude" ? C.purple : C.muted,
                      textTransform: 'uppercase', letterSpacing: '0.5px',
                      fontFamily: 'monospace', borderBottom: `1px solid ${C.border}`,
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
                  <td colSpan={12} style={{ padding: '48px', textAlign: 'center', color: C.muted, fontSize: 13 }}>
                    Loading...
                  </td>
                </tr>
              ) : signals.length === 0 ? (
                <tr>
                  <td colSpan={12} style={{ padding: '48px', textAlign: 'center', color: C.muted, fontSize: 13 }}>
                    No signals found
                  </td>
                </tr>
              ) : (
                signals.map((s) => (
                  <SignalRow
                    key={s.id}
                    signal={s}
                    isExpanded={expandedId === s.id}
                    detail={detailCache[s.id] ?? null}
                    loadingDetail={loadingDetail && expandedId === s.id}
                    onToggle={() => handleToggle(s.id)}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
