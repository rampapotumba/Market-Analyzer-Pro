"use client";

interface PortfolioHeatBarProps {
  heatPct: number;
  maxHeatPct?: number;
}

export function PortfolioHeatBar({ heatPct = 0, maxHeatPct = 6.0 }: PortfolioHeatBarProps) {
  const safePct = heatPct ?? 0;
  const pct = Math.min(100, (safePct / maxHeatPct) * 100);
  const barColor =
    pct >= 90 ? '#ef4444' : pct >= 70 ? '#f97316' : '#22c55e';

  return (
    <div style={{ borderRadius: 8, border: '1px solid #30363d', background: '#161b22', padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <span style={{ fontSize: 12, color: '#8b949e', fontFamily: 'monospace' }}>Portfolio Heat</span>
        <span style={{ fontSize: 13, fontWeight: 600, color: '#e6edf3', fontFamily: 'monospace' }}>
          {safePct.toFixed(2)}% / {maxHeatPct}%
        </span>
      </div>
      <div style={{ height: 10, width: '100%', borderRadius: 999, background: '#30363d', overflow: 'hidden' }}>
        <div
          style={{
            height: '100%', borderRadius: 999,
            transition: 'width 0.5s ease',
            background: barColor,
            width: `${pct}%`,
          }}
        />
      </div>
      <p style={{ marginTop: 6, fontSize: 11, color: '#8b949e', fontFamily: 'monospace' }}>
        {pct >= 90 ? "Near max capacity" : pct >= 70 ? "Moderate risk" : "Healthy"}
      </p>
    </div>
  );
}
