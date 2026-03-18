/**
 * API client for Market Analyzer Pro backend.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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
  type: string;
  is_active: boolean;
}


export interface BacktestRun {
  id: number;
  instrument_id: number;
  timeframe: string;
  start_date: string;
  end_date: string;
  total_trades: number;
  win_rate: number;
  sharpe_ratio: number;
  max_drawdown: number;
  net_pnl_pct: number;
  created_at: string;
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

  // Backtests
  getBacktests: () => fetchJSON<BacktestRun[]>("/api/v2/backtests"),

  // Macro
  getMacroData: () => fetchJSON<MacroData[]>("/api/v2/macroeconomics?limit=2000"),

  // Regime
  getRegimes: () => fetchJSON<RegimeState[]>("/api/v2/regime"),

};
