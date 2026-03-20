/**
 * API client for Market Analyzer Pro backend.
 */

// Empty string = same origin → Next.js proxy rewrites /api/* → localhost:8000
// Set NEXT_PUBLIC_API_URL only when frontend and backend are on different hosts
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

async function fetchJSON<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

// ── Types ──────────────────────────────────────────────────────────────────

export interface Signal {
  id: number;
  instrument_id: number;
  symbol: string;
  timeframe: string;
  direction: "LONG" | "SHORT" | "HOLD";
  composite_score: number;
  confidence: number;
  strength: string;
  entry_price: number;
  stop_loss: number;
  take_profit_1: number;
  take_profit_2?: number;
  take_profit_3?: number;
  risk_reward: number;
  regime: string;
  status: string;
  created_at: string;
  expires_at?: string;
  // LLM (Claude) fields
  llm_score?: number | null;
  llm_bias?: "BULLISH" | "BEARISH" | "NEUTRAL" | null;
  llm_confidence?: number | null;
}

export interface SignalDetail extends Signal {
  llm_reasoning?: string | null;
  llm_key_factors?: string[];
}

export interface Instrument {
  id: number;
  symbol: string;
  name: string;
  market: string;  // "forex" | "crypto" | "stocks"
  is_active: boolean;
}


export interface BacktestRun {
  id: string;          // UUID
  status: string;      // "pending" | "running" | "completed" | "failed"
  progress_pct?: number | null;  // 0–100, only present when status=running
  started_at?: string;
  completed_at?: string;
  params?: string;     // JSON string: { symbols, timeframe, start_date, end_date, account_size }
  summary?: string;    // JSON string: { total_trades, win_rate_pct, profit_factor, ... }
}


export interface BacktestTrade {
  id: number;
  run_id: string;
  symbol?: string;
  timeframe?: string;
  direction?: string;
  entry_price?: number;
  exit_price?: number;
  exit_reason?: string;
  pnl_pips?: number;
  pnl_usd?: number;
  result?: string;
  composite_score?: number;
  entry_at?: string;
  exit_at?: string;
  duration_minutes?: number;
  mfe?: number;
  mae?: number;
}

// Flat response from GET /api/v2/backtest/{run_id}/results
export interface BacktestResults {
  run_id: string;
  status: string;
  params?: {
    symbols: string[];
    timeframe: string;
    start_date: string;
    end_date: string;
    account_size?: number;
  };
  started_at?: string;
  completed_at?: string;
  total_trades: number;
  win_rate_pct: number;
  profit_factor?: number | null;
  total_pnl_usd: number;
  avg_duration_minutes?: number;
  // from BacktestEngine summary (merged into response)
  long_count?: number;
  short_count?: number;
  max_drawdown_pct?: number;
  equity_curve?: Array<{ date: string; equity?: number; balance?: number }>;
  monthly_returns?: Record<string, number>;
  by_symbol?: Record<string, { trades: number; wins: number; pnl_usd: number }>;
  by_score_bucket?: Record<string, { trades: number; wins: number; pnl_usd: number }>;
  // trades excluded from results — use getBacktestTrades() for paginated list
}

export interface BacktestRunParams {
  symbols: string[];
  timeframe: string;
  start_date: string;
  end_date: string;
  account_size?: number;
}

export interface MacroData {
  indicator: string;
  country: string;
  value: number;
  previous_value: number | null;
  release_date: string;
}

export interface RegimeState {
  instrument_id: number;
  regime: string;
  adx?: number;
  atr_percentile?: number;
  vix?: number;
  detected_at: string;
}


// ── API functions ──────────────────────────────────────────────────────────

export const api = {
  // Signals
  getSignals: (status?: string) =>
    fetchJSON<Signal[]>(`/api/v2/signals${status ? `?status=${status}` : ""}`),
  getSignal: (id: number) => fetchJSON<SignalDetail>(`/api/v2/signals/${id}`),

  // Instruments
  getInstruments: () => fetchJSON<Instrument[]>("/api/v2/instruments"),

  // Portfolio heat (used in Simulator)
  getPortfolioHeat: () => fetchJSON<{ portfolio_heat_pct: number }>("/api/v2/portfolio/heat"),

  // Backtests (v4)
  listBacktestRuns: () => fetchJSON<BacktestRun[]>("/api/v2/backtest/list"),
  runBacktest: (params: BacktestRunParams) =>
    fetchJSON<{ run_id: string }>("/api/v2/backtest/run", {
      method: "POST",
      body: JSON.stringify(params),
    }),
  getBacktestStatus: (runId: string) =>
    fetchJSON<BacktestRun>(`/api/v2/backtest/${runId}/status`),
  getBacktestResults: (runId: string) =>
    fetchJSON<BacktestResults>(`/api/v2/backtest/${runId}/results`),
  deleteBacktestRun: (runId: string) =>
    fetch(`${API_BASE}/api/v2/backtest/${runId}`, { method: "DELETE" }),
  getBacktestTrades: (runId: string, offset = 0, limit = 100) =>
    fetchJSON<{ total: number; limit: number; offset: number; trades: BacktestTrade[] }>(
      `/api/v2/backtest/${runId}/trades?offset=${offset}&limit=${limit}`
    ),

  // Macro
  getMacroData: () => fetchJSON<MacroData[]>("/api/v2/macroeconomics?limit=2000"),

  // Regime
  getRegimes: () => fetchJSON<RegimeState[]>("/api/v2/regime"),

};
