"""LLM Engine: uses Claude API to analyze market data and generate a score."""

import json
import logging
from typing import Any, Optional

from src.config import settings
from src.database.models import Instrument

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are an expert financial market analyst. You receive structured market data
and must return a JSON object with your analysis.

Your output MUST be valid JSON with this exact structure:
{
  "score": <float from -100 to +100>,
  "bias": <"BULLISH" | "BEARISH" | "NEUTRAL">,
  "confidence": <float from 0 to 100>,
  "key_factors": [<list of up to 5 short strings describing the main drivers>],
  "reasoning": "<2-3 sentences explaining your conclusion>"
}

Scoring guide:
  +60 to +100 = Strong bullish signal
  +30 to +60  = Moderate bullish
  -30 to +30  = Neutral / no clear direction
  -60 to -30  = Moderate bearish
  -100 to -60 = Strong bearish signal

Be concise. Focus on what actually matters for the next 4-24 hours."""


def _extract_macro_value(macro_records: list, indicator_name: str) -> Optional[float]:
    """Extract the most recent value for a given indicator from macro_records."""
    for record in macro_records:
        if getattr(record, "indicator_name", "") == indicator_name:
            val = getattr(record, "value", None)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
    return None


def _build_prompt(
    instrument: Instrument,
    timeframe: str,
    ta_score: float,
    fa_score: float,
    sentiment_score: float,
    geo_score: float,
    ta_indicators: dict[str, Any],
    macro_records: list,
    news_records: list,
) -> str:
    """Build the analysis prompt from all available data."""

    # Key TA indicators
    ind_lines = []
    key_inds = [
        ("current_price", "Price"),
        ("rsi_14", "RSI(14)"),
        ("macd", "MACD"),
        ("macd_signal", "MACD Signal"),
        ("macd_histogram", "MACD Hist"),
        ("adx", "ADX"),
        ("atr_14", "ATR(14)"),
        ("bb_upper", "BB Upper"),
        ("bb_lower", "BB Lower"),
        ("sma_20", "SMA20"),
        ("ema_50", "EMA50"),
        ("stoch_k", "Stoch %K"),
        ("stoch_d", "Stoch %D"),
    ]
    for key, label in key_inds:
        val = ta_indicators.get(key)
        if val is not None:
            ind_lines.append(f"  {label}: {round(float(val), 5)}")

    # Macro data (last 3)
    macro_lines = []
    for m in list(macro_records)[:3]:
        macro_lines.append(
            f"  {m.indicator_name} ({m.country}): {m.value}"
        )

    # News headlines (last 5)
    news_lines = []
    for n in list(news_records)[:5]:
        sentiment = float(n.sentiment_score) if n.sentiment_score else 0.0
        news_lines.append(
            f"  [{n.importance}] {n.headline[:100]} (sentiment={sentiment:+.2f})"
        )

    # Smart Money Levels from ta_indicators
    def _fmt(val: Any, decimals: int = 5) -> str:
        if val is None:
            return "N/A"
        try:
            return str(round(float(val), decimals))
        except (TypeError, ValueError):
            return "N/A"

    pdh = _fmt(ta_indicators.get("pdh"))
    pdl = _fmt(ta_indicators.get("pdl"))
    vpoc = _fmt(ta_indicators.get("vpoc"))
    vah = _fmt(ta_indicators.get("vah"))
    val_level = _fmt(ta_indicators.get("val"))
    bull_ob_low = _fmt(ta_indicators.get("bull_ob_low"))
    bull_ob_high = _fmt(ta_indicators.get("bull_ob_high"))
    bear_ob_low = _fmt(ta_indicators.get("bear_ob_low"))
    bear_ob_high = _fmt(ta_indicators.get("bear_ob_high"))
    fib_618 = _fmt(ta_indicators.get("fib_618"))
    fib_382 = _fmt(ta_indicators.get("fib_382"))

    # Market context values from macro_records
    dxy_value = _extract_macro_value(macro_records, "DXY")
    vix_value = _extract_macro_value(macro_records, "VIX")
    tnx_value = _extract_macro_value(macro_records, "TNX")

    dxy_str = _fmt(dxy_value, 3) if dxy_value is not None else "N/A"
    vix_str = _fmt(vix_value, 2) if vix_value is not None else "N/A"
    tnx_str = _fmt(tnx_value, 3) if tnx_value is not None else "N/A"

    prompt = f"""Analyze the following market data for {instrument.symbol} ({instrument.name}) on {timeframe} timeframe.

## Pre-computed Engine Scores (scale -100 to +100)
  Technical Analysis score : {ta_score:+.2f}
  Fundamental Analysis score: {fa_score:+.2f}
  News Sentiment score      : {sentiment_score:+.2f}
  Geopolitical score        : {geo_score:+.2f}

## Technical Indicators
{chr(10).join(ind_lines) if ind_lines else "  (no data)"}

## Smart Money Levels
  PDH: {pdh}, PDL: {pdl}
  VPOC: {vpoc}, VAH: {vah}, VAL: {val_level}
  Bullish OB: {bull_ob_low} - {bull_ob_high}
  Bearish OB: {bear_ob_low} - {bear_ob_high}
  Fib 0.618: {fib_618}, Fib 0.382: {fib_382}

## Market Context (Correlation)
  DXY: {dxy_str}
  VIX: {vix_str}
  US10Y: {tnx_str}

## Recent Macro Data
{chr(10).join(macro_lines) if macro_lines else "  (no data)"}

## Recent News
{chr(10).join(news_lines) if news_lines else "  (no data)"}

## Your task
Based on ALL of the above, provide your independent assessment.
Do NOT simply average the scores — interpret them holistically.
Consider: trend strength, momentum, macro backdrop, sentiment, smart money levels.
Return ONLY the JSON object, no other text."""

    return prompt


class LLMEngine:
    """
    Uses Claude API to analyze market data and produce a score from -100 to +100.

    Gracefully degrades (returns 0.0) when ANTHROPIC_API_KEY is not configured.
    """

    def __init__(self) -> None:
        self.api_key = settings.ANTHROPIC_API_KEY
        self.enabled = bool(self.api_key)
        if not self.enabled:
            logger.debug("[LLMEngine] ANTHROPIC_API_KEY not set — LLM analysis disabled")

    async def calculate_llm_score(
        self,
        instrument: Instrument,
        timeframe: str,
        ta_score: float,
        fa_score: float,
        sentiment_score: float,
        geo_score: float,
        ta_indicators: dict[str, Any],
        macro_records: list,
        news_records: list,
    ) -> tuple[float, dict[str, Any]]:
        """
        Call Claude API and return (llm_score, llm_meta).

        llm_meta contains: bias, confidence, key_factors, reasoning.
        Returns (0.0, {}) if API key is not set or call fails.
        """
        if not self.enabled:
            return 0.0, {}

        try:
            import anthropic

            prompt = _build_prompt(
                instrument, timeframe,
                ta_score, fa_score, sentiment_score, geo_score,
                ta_indicators, macro_records, news_records,
            )

            client = anthropic.AsyncAnthropic(api_key=self.api_key)
            response = await client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=512,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )

            raw = response.content[0].text.strip()

            # Strip markdown code block if present
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(
                    l for l in lines
                    if not l.startswith("```")
                ).strip()

            data = json.loads(raw)

            score = float(data.get("score", 0.0))
            score = max(-100.0, min(100.0, score))

            meta = {
                "bias": data.get("bias", "NEUTRAL"),
                "confidence": float(data.get("confidence", 0.0)),
                "key_factors": data.get("key_factors", []),
                "reasoning": data.get("reasoning", ""),
            }

            logger.info(
                f"[LLMEngine] {instrument.symbol}/{timeframe}: "
                f"score={score:+.1f}, bias={meta['bias']}, "
                f"confidence={meta['confidence']:.0f}%"
            )
            return score, meta

        except Exception as exc:
            logger.warning(f"[LLMEngine] Analysis failed for {instrument.symbol}: {exc}")
            return 0.0, {}
