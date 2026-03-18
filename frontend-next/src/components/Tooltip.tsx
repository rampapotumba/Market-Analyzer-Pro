"use client";

import React, { useState } from "react";

// ── Glossary ──────────────────────────────────────────────────────────────────

export const GLOSSARY: Record<string, { definition: string; formula?: string }> = {
  // ── Signal scores ──────────────────────────────────────────────────────────
  "Composite": {
    definition: "Weighted sum of all analysis components. Range ≈ −24.75 to +24.75. BUY ≥ 7.0, STRONG BUY ≥ 15.0.",
    formula: "W_ta×TA + W_fa×FA + W_sent×Sentiment + W_geo×Geo",
  },
  "Sentiment": {
    definition: "FinBERT NLP score from news and social media filtered for this specific instrument. Positive = bullish tone, negative = bearish.",
    formula: "Range [−100, +100] · weight 20% in composite",
  },
  "Confidence": {
    definition: "Signal reliability. Combines score magnitude, component agreement ratio, and how well the signal fits the current market regime.",
    formula: "(|composite| / 100) × agreement_ratio × regime_fit × 100",
  },
  "Horizon": {
    definition: "Expected trade duration based on the selected timeframe (M15 ≈ intraday, H1 ≈ day trade, H4 ≈ swing, D1 ≈ positional).",
  },
  "TA Score": {
    definition: "Technical Analysis score derived from RSI, MACD, moving averages, ADX, Stochastics, Bollinger Bands, volume, and order flow.",
    formula: "Range [−100, +100] · weight 45% in composite",
  },
  "FA Score": {
    definition: "Fundamental Analysis score. Forex: interest rate differentials. Stocks: earnings, P/E, revenue. Crypto: on-chain metrics.",
    formula: "Range [−100, +100] · weight 25% in composite",
  },
  "Geo": {
    definition: "Geopolitical risk score derived from GDELT news event analysis. High score = elevated global uncertainty.",
    formula: "Range [−100, +100] · weight 10% in composite",
  },
  "Avg Score": {
    definition: "Average composite score of signals that generated trades for this symbol. Higher = stronger conviction signals were used.",
    formula: "Σ composite_score / trade_count",
  },

  // ── Trade levels ───────────────────────────────────────────────────────────
  "Entry": {
    definition: "Recommended entry price. For market orders — current ask/bid; for limit orders — a pullback level near support/resistance.",
  },
  "Stop Loss": {
    definition: "Maximum loss level. Placed beyond the nearest key S&R zone with an ATR buffer scaled by current market regime.",
    formula: "Entry ± ATR × SL_multiplier(regime)",
  },
  "TP1": {
    definition: "First take-profit target. Close 50% of the position here and move stop-loss to breakeven.",
    formula: "Entry ± SL_distance × TP1_RR  (default 2.0)",
  },
  "TP2": {
    definition: "Second take-profit target for the remaining 50% of the position. Trail stop after TP1 is hit.",
    formula: "Entry ± SL_distance × TP2_RR  (default 3.5)",
  },
  "R:R": {
    definition: "Risk:Reward ratio — potential profit relative to potential loss. Minimum accepted threshold is 1.5.",
    formula: "TP_distance / SL_distance",
  },
  "Position": {
    definition: "Recommended position size as % of account capital. Capped so that a full SL loss equals max risk per trade (2%).",
    formula: "(Risk% × Account_USD) / SL_distance",
  },

  // ── Performance metrics ────────────────────────────────────────────────────
  "Win Rate": {
    definition: "Percentage of closed trades that ended profitably. Above 50% = more wins than losses.",
    formula: "wins / total_closed_trades × 100",
  },
  "Profit Factor": {
    definition: "Ratio of total gross profit to total gross loss. >1.0 = profitable system. >1.5 = good. >2.0 = excellent.",
    formula: "Σ winning_pnl / |Σ losing_pnl|",
  },
  "Total P&L": {
    definition: "Total realized profit and loss across all closed trades in USD.",
  },
  "Net PnL": {
    definition: "Net profit and loss — sum of all closed trade results in USD.",
  },
  "Unrealized": {
    definition: "Floating profit or loss on currently open positions at the latest market price. Not yet realized.",
  },
  "Unrealized P&L": {
    definition: "Floating profit or loss on this open position at the current market price. Not yet realized.",
  },
  "Avg Win": {
    definition: "Average profit per winning trade in USD.",
    formula: "Σ win_pnl / win_count",
  },
  "Avg Loss": {
    definition: "Average loss per losing trade in USD (shown as absolute value).",
    formula: "|Σ loss_pnl| / loss_count",
  },
  "Avg Pips": {
    definition: "Average pip gain/loss per trade for this symbol. Pip = smallest price increment (0.0001 for most forex pairs, 1 for JPY pairs).",
    formula: "Σ pnl_pips / trade_count",
  },
  "Pips": {
    definition: "Price movement expressed in pips — the smallest price increment. 1 pip = 0.0001 for most forex pairs (0.01 for JPY pairs).",
  },
  "P&L USD": {
    definition: "Realized profit and loss for this trade in USD, calculated from pip movement and position size.",
  },
  "Portfolio Heat": {
    definition: "Total risk currently committed across all open positions as % of account capital. Maximum allowed: 6%.",
    formula: "Σ (SL_distance × position_size) / account_size × 100",
  },
  "Total Trades": {
    definition: "Total number of closed (fully exited) trades. Open positions are not counted.",
  },
  "Duration": {
    definition: "Time a trade was open from entry to exit. Shorter duration on losers = good risk management.",
  },

  // ── Technical indicators ───────────────────────────────────────────────────
  "RSI (14)": {
    definition: "Relative Strength Index. Momentum oscillator measuring the speed of recent price changes.",
    formula: "< 30 oversold · > 70 overbought · in trend (ADX > 25): 40–55 = pullback entry",
  },
  "MACD Hist": {
    definition: "MACD Histogram — difference between MACD line and signal line. Positive = bullish momentum building.",
    formula: "MACD(12,26) − Signal(9)  ·  zero-cross = potential direction change",
  },
  "SMA 20": { definition: "20-period Simple Moving Average. Short-term trend reference. Price above = near-term bullish bias." },
  "SMA 50": { definition: "50-period Simple Moving Average. Medium-term trend reference. Often acts as dynamic support/resistance." },
  "SMA 200": { definition: "200-period Simple Moving Average. Long-term trend baseline. Price above = bull market structure." },
  "ADX (14)": {
    definition: "Average Directional Index. Measures trend strength (not direction). High ADX = strong trend, low = ranging.",
    formula: "> 25 trending (momentum signals reliable) · < 20 ranging (mean-reversion preferred)",
  },
  "ATR (14)": {
    definition: "Average True Range. Average absolute price movement per bar. Used to set volatility-adjusted SL/TP distances.",
  },
  "Stoch %K": {
    definition: "Stochastic Oscillator %K. Compares closing price to recent high-low range.",
    formula: "< 20 oversold · > 80 overbought · most effective in ranging markets (ADX < 20)",
  },
  "BB Width": {
    definition: "Bollinger Band Width. Measures how wide the bands are relative to the middle band. Squeeze = volatility compression before breakout.",
    formula: "(Upper − Lower) / Middle × 100",
  },

  // ── Macro (dashboard labels) ───────────────────────────────────────────────
  "VIX": {
    definition: "CBOE Volatility Index — implied 30-day volatility of S&P 500 options. Known as the market 'fear gauge'.",
    formula: "> 25 high fear (risk-off) · < 15 complacency (risk-on)",
  },
  "DXY": {
    definition: "US Dollar Index. Trade-weighted basket of USD vs EUR, JPY, GBP, CAD, SEK, CHF. Strong DXY = headwind for commodities and EM.",
    formula: "> 105 strong dollar · < 95 weak dollar",
  },
  "10Y Yield": {
    definition: "US 10-Year Treasury yield — the global risk-free rate benchmark. Rising yields tighten financial conditions.",
    formula: "> 4.5% restrictive · < 3.0% accommodative",
  },
  "Fed Rate": {
    definition: "Federal Funds Rate. Key US benchmark interest rate set by the FOMC. Primary driver of USD strength.",
    formula: "> 4.5% restrictive (SELL risk assets) · < 2.0% accommodative (BUY)",
  },
  "Funding Rate": {
    definition: "Perpetual futures 8-hour funding rate. Positive = longs pay shorts (crowd over-leveraged long). Negative = shorts pay longs.",
    formula: "> 0.01% per 8h overbought (SELL) · < −0.01% oversold (BUY)",
  },
  "Net Position": {
    definition: "COT net speculative position from the CFTC weekly Commitments of Traders report. Large positive = institutional longs dominant.",
    formula: "> +10,000 contracts bullish · < −10,000 bearish",
  },
  "CPI (US)": {
    definition: "Consumer Price Index. Measures average change in consumer prices. High CPI → Fed tightening pressure → USD strength.",
  },
  "Unemployment": {
    definition: "US Unemployment Rate. Low = strong economy. High = economic stress signal.",
    formula: "< 4% strong (BUY) · > 5.5% weak (SELL)",
  },
  "Real GDP": {
    definition: "Real Gross Domestic Product. Inflation-adjusted measure of US economic output in billions of USD.",
  },
  "Payrolls": {
    definition: "US Non-Farm Payrolls. Monthly count of new jobs added. Major monthly market-moving release.",
    formula: "> 155,000 strong growth (BUY)",
  },

  // ── Macro (macro page labels — INDICATOR_LABELS values) ───────────────────
  "Dollar Index": {
    definition: "US Dollar Index (DXY). Trade-weighted basket of USD vs EUR, JPY, GBP, CAD, SEK, CHF. Strong DXY = headwind for risk assets and commodities.",
    formula: "> 105 strong dollar · < 95 weak dollar",
  },
  "Volatility (VIX)": {
    definition: "CBOE VIX — implied 30-day volatility of S&P 500 options. The market 'fear gauge'. Spikes signal risk-off regime shifts.",
    formula: "> 25 high fear · < 15 complacency",
  },
  "10Y Treasury Yield": {
    definition: "US 10-Year Treasury yield. Global risk-free rate benchmark. Rising yields pressure equity valuations and EM currencies.",
    formula: "> 4.5% restrictive · < 3.0% accommodative",
  },
  "BTC Funding Rate": {
    definition: "Bitcoin perpetual futures 8-hour funding rate. Positive = longs pay shorts (market over-leveraged long). Negative = shorts pay longs.",
    formula: "> 0.01% per 8h overbought (SELL) · < −0.01% oversold (BUY)",
  },
  "ETH Funding Rate": {
    definition: "Ethereum perpetual futures 8-hour funding rate. Same mechanics as BTC funding — measures leverage imbalance.",
    formula: "> 0.01% per 8h overbought · < −0.01% oversold",
  },
  "SOL Funding Rate": {
    definition: "Solana perpetual futures 8-hour funding rate. Measures leverage imbalance in SOL perp markets.",
    formula: "> 0.01% per 8h overbought · < −0.01% oversold",
  },
  "Fed Funds Rate": {
    definition: "Federal Funds Rate — key US benchmark interest rate set by the FOMC. Primary driver of USD strength and global liquidity conditions.",
    formula: "> 4.5% restrictive (SELL risk) · < 2.0% accommodative (BUY risk)",
  },
  "Unemployment Rate": {
    definition: "US Unemployment Rate. Low unemployment = strong economy with potential wage inflation pressure. High = economic stress.",
    formula: "< 4% strong (BUY) · > 5.5% weak (SELL)",
  },
  "CPI (Inflation)": {
    definition: "US Consumer Price Index — measures average change in prices consumers pay. High CPI increases Fed tightening probability.",
  },
  "Nonfarm Payrolls": {
    definition: "US Non-Farm Payrolls. Monthly change in employment ex-agriculture. One of the most market-moving economic releases.",
    formula: "> 155,000 strong growth (BUY) · below expectations → USD weakness",
  },
  "Industrial Production": {
    definition: "US Industrial Production Index. Measures output from manufacturing, mining, and utilities sectors. Proxy for economic activity.",
  },
  "GDP (Real, $B)": {
    definition: "Real Gross Domestic Product in billions of USD. Inflation-adjusted measure of total US economic output.",
  },
  "Housing Starts": {
    definition: "US Housing Starts (thousands of units). Number of new residential construction projects started. Leads the construction cycle.",
  },
  "Retail Sales": {
    definition: "US Retail Sales in billions USD. Measures consumer spending at retail stores. Accounts for ~70% of US GDP.",
  },
  "COT EUR/USD": {
    definition: "CFTC Commitments of Traders net speculative position for EUR/USD. Large positive net = speculative longs dominate.",
    formula: "Long contracts − Short contracts (large non-commercials)",
  },
  "COT USD/JPY": {
    definition: "CFTC Commitments of Traders net speculative position for USD/JPY futures.",
    formula: "Long contracts − Short contracts (large non-commercials)",
  },
  "COT GBP/USD": {
    definition: "CFTC Commitments of Traders net speculative position for GBP/USD futures.",
    formula: "Long contracts − Short contracts (large non-commercials)",
  },
  "COT S&P 500": {
    definition: "CFTC Commitments of Traders net speculative position for S&P 500 E-mini futures. Positive = institutional bullish bias.",
  },
  "COT Bitcoin": {
    definition: "CFTC Commitments of Traders net speculative position for CME Bitcoin futures. Reflects institutional sentiment.",
  },

  // ── Calendar ───────────────────────────────────────────────────────────────
  "Impact": {
    definition: "Expected market impact of the economic release. HIGH = major market-moving event (NFP, CPI, FOMC). MEDIUM = moderate volatility. LOW = minor release.",
  },
  "Forecast": {
    definition: "Analyst consensus estimate for the upcoming release. A significant deviation from forecast ('surprise') typically causes sharp price moves.",
  },
  "Previous": {
    definition: "Prior period's actual reading. Used to assess direction of change and gauge relative surprise of the upcoming release.",
  },
};

// ── Tooltip component ─────────────────────────────────────────────────────────

export function Tooltip({ term, children }: { term: string; children: React.ReactNode }) {
  const def = GLOSSARY[term];
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  if (!def) return <>{children}</>;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>
      {children}
      <span
        onMouseEnter={e => setPos({ x: e.clientX, y: e.clientY })}
        onMouseLeave={() => setPos(null)}
        style={{ cursor: "help", color: "#4b5563", fontSize: 9, lineHeight: 1, userSelect: "none", flexShrink: 0 }}
      >ⓘ</span>
      {pos && (
        <div style={{
          position: "fixed",
          left: Math.min(pos.x + 14, (typeof window !== "undefined" ? window.innerWidth : 1200) - 290),
          top: pos.y + 16,
          zIndex: 20000,
          background: "#161b22",
          border: "1px solid #30363d",
          borderRadius: 8,
          padding: "10px 12px",
          width: 270,
          boxShadow: "0 8px 32px rgba(0,0,0,0.6)",
          pointerEvents: "none",
        }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: "#e6edf3", marginBottom: 4 }}>{term}</div>
          <div style={{ fontSize: 11, color: "#8b949e", lineHeight: 1.6 }}>{def.definition}</div>
          {def.formula && (
            <div style={{ marginTop: 7, fontSize: 10, fontFamily: "monospace", color: "#58a6ff", background: "#0d1117", borderRadius: 4, padding: "5px 8px", lineHeight: 1.5 }}>
              {def.formula}
            </div>
          )}
        </div>
      )}
    </span>
  );
}
