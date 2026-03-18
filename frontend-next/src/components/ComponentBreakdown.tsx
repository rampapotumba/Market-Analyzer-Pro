"use client";

interface ComponentScore {
  label: string;
  score: number;
  weight: number;
}

interface ComponentBreakdownProps {
  components: ComponentScore[];
  compositeScore: number;
}

function ScoreBar({ score, max = 100 }: { score: number; max?: number }) {
  const abs = Math.abs(score);
  const pct = (abs / max) * 50; // 50% = max reach from center
  const isPositive = score >= 0;

  return (
    <div className="relative flex items-center h-4">
      <div className="w-full h-2 bg-gray-100 rounded-full overflow-hidden relative">
        {/* Center line */}
        <div className="absolute inset-y-0 left-1/2 w-px bg-gray-300" />
        {/* Bar */}
        <div
          className={`absolute inset-y-0 ${isPositive ? "bg-green-500 left-1/2" : "bg-red-500 right-1/2"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export function ComponentBreakdown({ components, compositeScore }: ComponentBreakdownProps) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-medium text-gray-700">Signal Breakdown</h3>
        <span
          className={`text-lg font-bold ${
            compositeScore > 0
              ? "text-green-600"
              : compositeScore < 0
              ? "text-red-500"
              : "text-gray-500"
          }`}
        >
          {compositeScore > 0 ? "+" : ""}
          {compositeScore.toFixed(1)}
        </span>
      </div>
      <div className="space-y-3">
        {components.map(({ label, score, weight }) => (
          <div key={label}>
            <div className="flex items-center justify-between text-xs mb-1">
              <span className="text-gray-600 font-medium">{label}</span>
              <div className="flex items-center gap-2">
                <span className="text-gray-400">{(weight * 100).toFixed(0)}%</span>
                <span
                  className={`font-semibold w-12 text-right ${
                    score > 0 ? "text-green-600" : score < 0 ? "text-red-500" : "text-gray-400"
                  }`}
                >
                  {score > 0 ? "+" : ""}
                  {score.toFixed(1)}
                </span>
              </div>
            </div>
            <ScoreBar score={score} />
          </div>
        ))}
      </div>
    </div>
  );
}
