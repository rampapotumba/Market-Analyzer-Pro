"""Sentiment Engine v2 — multi-source weighted scoring with FinBERT.

Sources and default weights
─────────────────────────────
  news        40%   — news headlines + summaries (FinBERT → TextBlob fallback)
  social      30%   — Reddit/Stocktwits fear-greed + posts
  fear_greed  20%   — Alternative.me Fear & Greed index (crypto/macro)
  options     10%   — Put/Call ratio (stocks)

Weights are renormalised automatically when a source has no data.

Score output: [-100, +100] where positive = bullish sentiment.
"""

import logging
from typing import Any, Optional

from src.analysis.finbert_client import FinBERTClient, finbert as _default_finbert

logger = logging.getLogger(__name__)

# Default source weights (must sum to 1.0)
_SOURCE_WEIGHTS: dict[str, float] = {
    "news": 0.40,
    "social": 0.30,
    "fear_greed": 0.20,
    "options": 0.10,
}

# TextBlob is the fallback when FinBERT is unavailable
_TEXTBLOB_AVAILABLE = True
try:
    from textblob import TextBlob  # type: ignore[import]
except ImportError:
    _TEXTBLOB_AVAILABLE = False

# Importance multipliers (same as v1)
_IMPORTANCE_WEIGHTS: dict[str, float] = {
    "critical": 3.0,
    "high": 2.0,
    "medium": 1.5,
    "low": 1.0,
}


class SentimentEngineV2:
    """Multi-source sentiment engine with FinBERT support.

    Usage (async — FinBERT)::

        engine = SentimentEngineV2(news_events=events, social_data=social)
        score = await engine.calculate()

    Usage (sync fallback — TextBlob)::

        score = engine.calculate_sync()
    """

    def __init__(
        self,
        news_events: Optional[list[Any]] = None,
        social_data: Optional[dict[str, Any]] = None,
        fear_greed_index: Optional[float] = None,
        put_call_ratio: Optional[float] = None,
        finbert_client: Optional[FinBERTClient] = None,
    ) -> None:
        self._news = news_events or []
        self._social = social_data or {}
        self._fear_greed = fear_greed_index   # 0–100 (0=extreme fear, 100=extreme greed)
        self._pcr = put_call_ratio             # e.g. 0.8 (low=bullish), 1.2 (high=bearish)
        self._finbert = finbert_client or _default_finbert

    # ── Public API ─────────────────────────────────────────────────────────────

    async def calculate(self) -> float:
        """Full async scoring using FinBERT where available."""
        parts: dict[str, Optional[float]] = {
            "news": await self._score_news_finbert(),
            "social": self._score_social(),
            "fear_greed": self._score_fear_greed(),
            "options": self._score_options(),
        }
        return _weighted_average(parts, _SOURCE_WEIGHTS)

    def calculate_sync(self) -> float:
        """Synchronous fallback using TextBlob only (no FinBERT)."""
        parts: dict[str, Optional[float]] = {
            "news": self._score_news_textblob(),
            "social": self._score_social(),
            "fear_greed": self._score_fear_greed(),
            "options": self._score_options(),
        }
        return _weighted_average(parts, _SOURCE_WEIGHTS)

    def get_summary(self) -> dict[str, Any]:
        """Return metadata about available sources."""
        return {
            "news_count": len(self._news),
            "social_sources": list(self._social.keys()),
            "fear_greed_available": self._fear_greed is not None,
            "pcr_available": self._pcr is not None,
        }

    # ── News scoring ───────────────────────────────────────────────────────────

    async def _score_news_finbert(self) -> Optional[float]:
        """Score news using FinBERT; fall back to TextBlob on failure."""
        if not self._news:
            return None

        texts = [_extract_text(e) for e in self._news]
        texts = [t for t in texts if t]
        if not texts:
            return None

        results = await self._finbert.score_batch(texts)
        if results is None:
            logger.debug("SentimentV2: FinBERT unavailable, falling back to TextBlob")
            return self._score_news_textblob()

        # Weight by importance if available
        weighted_sum = 0.0
        weight_total = 0.0
        for event, sr in zip(self._news, results):
            if sr is None:
                continue
            w = _importance_weight(event)
            weighted_sum += sr.score * w
            weight_total += w

        if weight_total == 0:
            return None

        # sr.score is already in [-1, +1]
        return max(-100.0, min(100.0, (weighted_sum / weight_total) * 100.0))

    def _score_news_textblob(self) -> Optional[float]:
        """TextBlob fallback for news scoring."""
        if not self._news or not _TEXTBLOB_AVAILABLE:
            return None

        weighted_sum = 0.0
        weight_total = 0.0
        for event in self._news:
            text = _extract_text(event)
            if not text:
                continue
            try:
                polarity = TextBlob(text).sentiment.polarity  # [-1, +1]
            except Exception:
                continue
            w = _importance_weight(event)
            weighted_sum += polarity * w
            weight_total += w

        if weight_total == 0:
            return None
        return max(-100.0, min(100.0, (weighted_sum / weight_total) * 100.0))

    # ── Social scoring ─────────────────────────────────────────────────────────

    def _score_social(self) -> Optional[float]:
        """Score social sentiment from aggregated data.

        Expected keys in social_data:
            reddit_score    float [-100, +100] — Reddit sentiment
            stocktwits_score float [-100, +100] — Stocktwits
            bullish_pct     float [0, 100]     — % bullish posts
        """
        if not self._social:
            return None

        parts = []

        reddit = self._social.get("reddit_score")
        if reddit is not None:
            parts.append(float(reddit))

        stocktwits = self._social.get("stocktwits_score")
        if stocktwits is not None:
            parts.append(float(stocktwits))

        bullish_pct = self._social.get("bullish_pct")
        if bullish_pct is not None:
            # 50% = neutral; map to [-100, +100]
            parts.append((float(bullish_pct) - 50.0) * 2.0)

        if not parts:
            return None
        return max(-100.0, min(100.0, sum(parts) / len(parts)))

    # ── Fear & Greed ───────────────────────────────────────────────────────────

    def _score_fear_greed(self) -> Optional[float]:
        """Map Fear & Greed index [0–100] to [-100, +100].

        0   = extreme fear  → -100
        25  = fear          → -50
        50  = neutral       → 0
        75  = greed         → +50
        100 = extreme greed → +100
        """
        if self._fear_greed is None:
            return None
        return max(-100.0, min(100.0, (float(self._fear_greed) - 50.0) * 2.0))

    # ── Options PCR ────────────────────────────────────────────────────────────

    def _score_options(self) -> Optional[float]:
        """Map Put/Call ratio to sentiment score.

        PCR < 0.7  → bullish (more calls)  → +100
        PCR = 1.0  → neutral               →   0
        PCR > 1.3  → bearish (more puts)   → -100
        Linear interpolation between breakpoints.
        """
        if self._pcr is None:
            return None
        pcr = float(self._pcr)
        if pcr <= 0.7:
            return 100.0
        if pcr >= 1.3:
            return -100.0
        # Linear: 0.7→+100, 1.0→0, 1.3→-100
        return max(-100.0, min(100.0, (1.0 - pcr) / 0.3 * 100.0))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extract_text(event: Any) -> str:
    """Extract combined headline + summary text from a news event."""
    if isinstance(event, dict):
        return f"{event.get('headline', '')} {event.get('summary', '')}".strip()
    headline = getattr(event, "headline", "") or ""
    summary = getattr(event, "summary", "") or ""
    return f"{headline} {summary}".strip()


def _importance_weight(event: Any) -> float:
    """Return the importance weight for a news event."""
    if isinstance(event, dict):
        importance = event.get("importance", "low")
    else:
        importance = getattr(event, "importance", "low") or "low"
    return _IMPORTANCE_WEIGHTS.get(importance, 1.0)


def _weighted_average(
    parts: dict[str, Optional[float]],
    weights: dict[str, float],
) -> float:
    """Compute weighted average over non-None parts; renormalise weights."""
    total_weight = 0.0
    weighted_sum = 0.0
    for key, value in parts.items():
        if value is None:
            continue
        w = weights.get(key, 0.0)
        weighted_sum += value * w
        total_weight += w

    if total_weight == 0:
        return 0.0
    return max(-100.0, min(100.0, weighted_sum / total_weight))
