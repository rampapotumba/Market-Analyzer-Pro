"use client";

import { useEffect, useRef } from "react";

interface DataPoint {
  date: string;
  value: number;
}

interface DifferentialChartProps {
  title: string;
  data: DataPoint[];
  color?: string;
  unit?: string;
}

export function DifferentialChart({
  title,
  data,
  color = "#3b82f6",
  unit = "%",
}: DifferentialChartProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || data.length === 0) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const W = canvas.width;
    const H = canvas.height;
    const padding = { top: 16, right: 16, bottom: 24, left: 48 };

    ctx.clearRect(0, 0, W, H);

    const values = data.map((d) => d.value);
    const minV = Math.min(...values);
    const maxV = Math.max(...values);
    const range = maxV - minV || 1;

    const toX = (i: number) =>
      padding.left + (i / (data.length - 1)) * (W - padding.left - padding.right);
    const toY = (v: number) =>
      padding.top + ((maxV - v) / range) * (H - padding.top - padding.bottom);

    // Zero line
    if (minV < 0 && maxV > 0) {
      const zeroY = toY(0);
      ctx.beginPath();
      ctx.strokeStyle = "#e5e7eb";
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.moveTo(padding.left, zeroY);
      ctx.lineTo(W - padding.right, zeroY);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Fill area
    ctx.beginPath();
    ctx.moveTo(toX(0), toY(values[0]));
    for (let i = 1; i < data.length; i++) {
      ctx.lineTo(toX(i), toY(values[i]));
    }
    ctx.lineTo(toX(data.length - 1), H - padding.bottom);
    ctx.lineTo(toX(0), H - padding.bottom);
    ctx.closePath();
    ctx.fillStyle = `${color}22`;
    ctx.fill();

    // Line
    ctx.beginPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.moveTo(toX(0), toY(values[0]));
    for (let i = 1; i < data.length; i++) {
      ctx.lineTo(toX(i), toY(values[i]));
    }
    ctx.stroke();

    // Y-axis labels
    ctx.fillStyle = "#9ca3af";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "right";
    [minV, (minV + maxV) / 2, maxV].forEach((v) => {
      ctx.fillText(`${v.toFixed(2)}${unit}`, padding.left - 4, toY(v) + 4);
    });
  }, [data, color, unit]);

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <h3 className="text-sm font-medium text-gray-700 mb-3">{title}</h3>
      {data.length === 0 ? (
        <div className="flex items-center justify-center h-32 text-gray-400 text-sm">
          No data
        </div>
      ) : (
        <canvas ref={canvasRef} width={400} height={120} className="w-full" />
      )}
    </div>
  );
}
