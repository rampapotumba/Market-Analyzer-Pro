"use client";

interface RegimeWidgetProps {
  regime: string;
  adx?: number | null;
  atrPercentile?: number | null;
  vix?: number | null;
}

const REGIME_COLORS: Record<string, string> = {
  STRONG_TREND_BULL: "bg-green-500",
  WEAK_TREND_BULL: "bg-green-300",
  STRONG_TREND_BEAR: "bg-red-500",
  WEAK_TREND_BEAR: "bg-red-300",
  RANGING: "bg-yellow-400",
  HIGH_VOLATILITY: "bg-orange-500",
  LOW_VOLATILITY: "bg-blue-300",
};

const REGIME_LABELS: Record<string, string> = {
  STRONG_TREND_BULL: "Strong Bull",
  WEAK_TREND_BULL: "Weak Bull",
  STRONG_TREND_BEAR: "Strong Bear",
  WEAK_TREND_BEAR: "Weak Bear",
  RANGING: "Ranging",
  HIGH_VOLATILITY: "High Vol",
  LOW_VOLATILITY: "Low Vol",
};

export function RegimeWidget({ regime, adx, atrPercentile, vix }: RegimeWidgetProps) {
  const color = REGIME_COLORS[regime] ?? "bg-gray-400";
  const label = REGIME_LABELS[regime] ?? regime;

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <h3 className="text-sm font-medium text-gray-500 mb-2">Market Regime</h3>
      <div className={`inline-flex items-center rounded-full px-3 py-1 text-white text-sm font-semibold ${color}`}>
        {label}
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2 text-xs text-gray-600">
        {adx !== null && adx !== undefined && (
          <div>
            <span className="block text-gray-400">ADX</span>
            <span className="font-medium">{adx.toFixed(1)}</span>
          </div>
        )}
        {atrPercentile !== null && atrPercentile !== undefined && (
          <div>
            <span className="block text-gray-400">ATR %ile</span>
            <span className="font-medium">{atrPercentile.toFixed(0)}%</span>
          </div>
        )}
        {vix !== null && vix !== undefined && (
          <div>
            <span className="block text-gray-400">VIX</span>
            <span className="font-medium">{vix.toFixed(1)}</span>
          </div>
        )}
      </div>
    </div>
  );
}
