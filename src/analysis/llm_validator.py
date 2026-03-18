"""
LLM-based signal validation using Claude API.

Provides a second-opinion filter: structured JSON response (approve/reject/reduce)
before a signal is emitted. Results are cached in Redis to avoid redundant calls.

Configuration:
    ANTHROPIC_API_KEY  — required in .env to enable
    LLM_VALIDATION_ENABLED — set to false to disable (default: true if key present)
"""

import json
import logging
from decimal import Decimal
from typing import Any, Optional

from src.cache import cache
from src.config import settings

logger = logging.getLogger(__name__)

_CACHE_TTL = 300  # 5 minutes — same market, same signal context

_SYSTEM_PROMPT = """You are a senior quantitative analyst reviewing trading signals.
Evaluate the signal data and respond with a JSON object (no extra text):
{
  "decision": "approve" | "reject" | "reduce",
  "reason": "<one-sentence explanation>",
  "adjusted_confidence": <0-100 float, only if reducing>
}

Rules:
- "approve": signal is coherent, risk/reward is acceptable
- "reduce": approve but lower confidence (e.g. mixed signals, unclear regime)
- "reject": signal has red flags (conflicting sources, poor R:R, event risk)
- Be strict: reject if R:R < 1.2 or if TA and FA strongly conflict
- Never reject solely based on direction (LONG or SHORT are both valid)
"""


class LLMValidator:
    """
    Optional Claude-based validation gate for trading signals.

    Enabled only when ANTHROPIC_API_KEY is set and the feature flag is on.
    On any error (network, quota, timeout) the signal is approved by default
    to avoid blocking signal generation.
    """

    def __init__(self) -> None:
        self._enabled = bool(
            getattr(settings, "ANTHROPIC_API_KEY", None)
            and getattr(settings, "LLM_VALIDATION_ENABLED", True)
        )
        if not self._enabled:
            logger.info("[LLMValidator] Disabled (no ANTHROPIC_API_KEY or feature flag off)")

    # ── Public API ────────────────────────────────────────────────────────────

    async def validate(
        self,
        symbol: str,
        timeframe: str,
        signal: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Validate a signal dict.

        Returns a dict with keys:
            approved  bool
            decision  "approve" | "reject" | "reduce"
            reason    str
            adjusted_confidence  Optional[float]  (if reduced)
        """
        if not self._enabled:
            return _approved()

        cache_key = _cache_key(symbol, timeframe, signal)
        cached = await cache.get(cache_key)
        if cached:
            try:
                result = json.loads(cached)
                logger.debug("[LLMValidator] Cache hit for %s %s", symbol, timeframe)
                return result
            except Exception:
                pass

        try:
            result = await self._call_claude(symbol, timeframe, signal)
        except Exception as exc:
            logger.warning("[LLMValidator] API error (approving by default): %s", exc)
            return _approved()

        # Cache the result
        try:
            await cache.set(cache_key, json.dumps(result), ttl=_CACHE_TTL)
        except Exception:
            pass

        return result

    # ── Private ───────────────────────────────────────────────────────────────

    async def _call_claude(
        self,
        symbol: str,
        timeframe: str,
        signal: dict[str, Any],
    ) -> dict[str, Any]:
        """Call Claude API and parse the decision."""
        try:
            import anthropic
        except ImportError:
            logger.warning("[LLMValidator] anthropic package not installed")
            return _approved()

        client = anthropic.AsyncAnthropic(
            api_key=getattr(settings, "ANTHROPIC_API_KEY", "")
        )

        prompt = _build_prompt(symbol, timeframe, signal)

        response = await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=256,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = next(
            (b.text for b in response.content if b.type == "text"), ""
        ).strip()

        return _parse_response(raw, signal)

    @property
    def enabled(self) -> bool:
        return self._enabled


# ── Helpers ───────────────────────────────────────────────────────────────────


def _cache_key(symbol: str, timeframe: str, signal: dict[str, Any]) -> str:
    composite = round(float(signal.get("composite_score", 0)), 1)
    direction = signal.get("direction", "")
    regime = signal.get("regime", "")
    return f"llm_validation:{symbol}:{timeframe}:{direction}:{composite}:{regime}"


def _build_prompt(symbol: str, timeframe: str, signal: dict[str, Any]) -> str:
    rr = signal.get("risk_reward")
    rr_str = f"{float(rr):.2f}" if rr else "N/A"
    entry = signal.get("entry_price")
    sl = signal.get("stop_loss")
    tp1 = signal.get("take_profit_1")

    lines = [
        f"Symbol: {symbol}  Timeframe: {timeframe}",
        f"Direction: {signal.get('direction')}  Strength: {signal.get('signal_strength')}",
        f"Composite Score: {signal.get('composite_score'):.1f}",
        f"  TA={signal.get('ta_score'):.1f}  FA={signal.get('fa_score'):.1f}"
        f"  Sentiment={signal.get('sentiment_score'):.1f}  Geo={signal.get('geo_score'):.1f}",
        f"Regime: {signal.get('regime')}",
        f"Confidence: {signal.get('confidence'):.1f}%",
        f"Entry: {float(entry):.5f}  SL: {float(sl):.5f}  TP1: {float(tp1):.5f}" if entry and sl and tp1 else "Entry/SL/TP: N/A",
        f"R:R: {rr_str}",
        f"Portfolio heat: {signal.get('portfolio_heat'):.1f}%",
    ]
    if signal.get("earnings_days_ahead") is not None:
        lines.append(f"Earnings in: {signal['earnings_days_ahead']} days")

    return "\n".join(lines)


def _parse_response(raw: str, signal: dict[str, Any]) -> dict[str, Any]:
    """Parse Claude's JSON response. Falls back to approve on any parse error."""
    try:
        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text.strip())

        decision = data.get("decision", "approve")
        if decision not in ("approve", "reject", "reduce"):
            decision = "approve"

        result: dict[str, Any] = {
            "approved": decision != "reject",
            "decision": decision,
            "reason": data.get("reason", ""),
            "adjusted_confidence": None,
        }

        if decision == "reduce":
            adj = data.get("adjusted_confidence")
            if adj is not None:
                try:
                    result["adjusted_confidence"] = float(adj)
                except (TypeError, ValueError):
                    pass

        return result
    except Exception as exc:
        logger.debug("[LLMValidator] Parse error (%s) — approving: %s", exc, raw[:100])
        return _approved()


def _approved() -> dict[str, Any]:
    return {
        "approved": True,
        "decision": "approve",
        "reason": "",
        "adjusted_confidence": None,
    }


# Singleton
llm_validator = LLMValidator()
