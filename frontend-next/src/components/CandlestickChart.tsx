"use client";

import { useEffect, useRef } from "react";
import type { Time } from "lightweight-charts";

interface Candle {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

interface CandlestickChartProps {
  candles: Candle[];
  height?: number;
  dark?: boolean;
}

export function CandlestickChart({ candles, height = 320, dark = false }: CandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<{ remove: () => void } | null>(null);

  useEffect(() => {
    if (!containerRef.current || candles.length === 0) return;

    let cancelled = false;

    import("lightweight-charts").then((lc) => {
      if (cancelled || !containerRef.current) return;

      // Clean up previous chart
      chartRef.current?.remove();
      chartRef.current = null;

      const bg = dark ? "#161b22" : "#ffffff";
      const textCol = dark ? "#c9d1d9" : "#374151";
      const gridCol = dark ? "#21262d" : "#f3f4f6";
      const borderCol = dark ? "#30363d" : "#e5e7eb";

      const chart = lc.createChart(containerRef.current, {
        autoSize: true,
        height,
        layout: {
          background: { type: lc.ColorType.Solid, color: bg },
          textColor: textCol,
        },
        grid: {
          vertLines: { color: gridCol },
          horzLines: { color: gridCol },
        },
        rightPriceScale: { borderColor: borderCol },
        timeScale: {
          borderColor: borderCol,
          timeVisible: true,
          secondsVisible: false,
        },
      });

      chartRef.current = chart;

      // Detect precision: if price < 10 → forex (5 decimals), else stocks/crypto (2)
      const sampleClose = candles[candles.length - 1]?.close ?? 0;
      const isForex = sampleClose > 0 && sampleClose < 10;
      const precision = isForex ? 5 : 2;
      const minMove = isForex ? 0.00001 : 0.01;

      const hasVolume = candles.some((c) => c.volume > 0);

      const candleSeries = chart.addSeries(lc.CandlestickSeries, {
        upColor: "#22c55e",
        downColor: "#ef4444",
        borderUpColor: "#22c55e",
        borderDownColor: "#ef4444",
        wickUpColor: "#22c55e",
        wickDownColor: "#ef4444",
        priceFormat: { type: "price", precision, minMove },
      });

      if (hasVolume) {
        const volumeSeries = chart.addSeries(lc.HistogramSeries, {
          color: "#93c5fd",
          priceFormat: { type: "volume" as const },
          priceScaleId: "volume",
        });

        volumeSeries.priceScale().applyOptions({
          scaleMargins: { top: 0.8, bottom: 0 },
        });

        const volumeData = candles.map((c) => ({
          time: (new Date(c.timestamp).getTime() / 1000) as unknown as Time,
          value: c.volume,
          color: c.close >= c.open ? "#bbf7d0" : "#fecaca",
        }));

        volumeSeries.setData(volumeData);
      }

      const candleData = candles.map((c) => ({
        time: (new Date(c.timestamp).getTime() / 1000) as unknown as Time,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }));

      candleSeries.setData(candleData);
      chart.timeScale().fitContent();
    });

    return () => {
      cancelled = true;
      chartRef.current?.remove();
      chartRef.current = null;
    };
  }, [candles, height]);

  if (candles.length === 0) {
    return (
      <div
        style={{ height }}
        className="flex items-center justify-center bg-gray-50 rounded-lg text-gray-400 text-sm"
      >
        No price data available
      </div>
    );
  }

  return <div ref={containerRef} style={{ height }} className="w-full" />;
}
