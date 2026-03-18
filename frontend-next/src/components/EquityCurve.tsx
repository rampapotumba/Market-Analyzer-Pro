"use client";

import { useEffect, useRef } from "react";

interface EquityPoint {
  date: string;
  equity: number;
}

interface EquityCurveProps {
  data: EquityPoint[];
  initialEquity?: number;
}

export function EquityCurve({ data, initialEquity = 10000 }: EquityCurveProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || data.length === 0) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const W = canvas.width;
    const H = canvas.height;
    const pad = { top: 16, right: 16, bottom: 24, left: 64 };

    ctx.clearRect(0, 0, W, H);

    const values = data.map((d) => d.equity);
    const minV = Math.min(initialEquity, ...values);
    const maxV = Math.max(initialEquity, ...values);
    const range = maxV - minV || 1;

    const toX = (i: number) =>
      pad.left + (i / (data.length - 1)) * (W - pad.left - pad.right);
    const toY = (v: number) =>
      pad.top + ((maxV - v) / range) * (H - pad.top - pad.bottom);

    // Background
    ctx.fillStyle = "#161b22";
    ctx.fillRect(0, 0, W, H);

    // Initial equity baseline
    const baseY = toY(initialEquity);
    ctx.beginPath();
    ctx.strokeStyle = "#30363d";
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.moveTo(pad.left, baseY);
    ctx.lineTo(W - pad.right, baseY);
    ctx.stroke();
    ctx.setLineDash([]);

    // Fill — green above baseline, red below
    for (let i = 0; i < data.length - 1; i++) {
      const x1 = toX(i);
      const x2 = toX(i + 1);
      const y1 = toY(values[i]);
      const y2 = toY(values[i + 1]);
      const above = values[i] >= initialEquity && values[i + 1] >= initialEquity;

      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.lineTo(x2, baseY);
      ctx.lineTo(x1, baseY);
      ctx.closePath();
      ctx.fillStyle = above ? "#22c55e22" : "#ef444422";
      ctx.fill();
    }

    // Equity line
    ctx.beginPath();
    ctx.strokeStyle = values[values.length - 1] >= initialEquity ? "#22c55e" : "#ef4444";
    ctx.lineWidth = 2;
    ctx.moveTo(toX(0), toY(values[0]));
    for (let i = 1; i < data.length; i++) {
      ctx.lineTo(toX(i), toY(values[i]));
    }
    ctx.stroke();

    // Y-axis labels
    ctx.fillStyle = "#8b949e";
    ctx.font = "10px monospace";
    ctx.textAlign = "right";
    [minV, initialEquity, maxV].forEach((v) => {
      ctx.fillText(`$${v.toLocaleString()}`, pad.left - 4, toY(v) + 4);
    });
  }, [data, initialEquity]);

  const lastEquity = data.length > 0 ? data[data.length - 1].equity : initialEquity;
  const pnlPct = ((lastEquity - initialEquity) / initialEquity) * 100;

  return (
    <div style={{ borderRadius: 8, border: '1px solid #30363d', background: '#161b22', padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <span style={{ fontSize: 13, color: '#8b949e', fontFamily: 'monospace' }}>Equity Curve</span>
        <span style={{ fontSize: 13, fontWeight: 600, fontFamily: 'monospace', color: pnlPct >= 0 ? '#22c55e' : '#ef4444' }}>
          {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%
        </span>
      </div>
      {data.length === 0 ? (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 128, color: '#8b949e', fontSize: 13, fontFamily: 'monospace' }}>
          No backtest data
        </div>
      ) : (
        <canvas ref={canvasRef} width={500} height={140} style={{ width: '100%' }} />
      )}
    </div>
  );
}
