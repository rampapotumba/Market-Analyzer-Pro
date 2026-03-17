"""Sentiment Analysis Engine using TextBlob (Phase 1) / FinBERT (Phase 2)."""

import logging
from decimal import Decimal
from typing import Any, Optional

from textblob import TextBlob

logger = logging.getLogger(__name__)


class SentimentEngine:
    """
    Sentiment Analysis Engine.
    Phase 1: TextBlob-based scoring.
    Phase 2: FinBERT financial NLP model.
    """

    IMPORTANCE_WEIGHTS = {
        "critical": 3.0,
        "high": 2.0,
        "medium": 1.5,
        "low": 1.0,
    }

    def __init__(self, news_events: list[Any]) -> None:
        self.news_events = news_events

    def _score_text(self, text: str) -> float:
        """Score a piece of text using TextBlob. Returns [-1, 1]."""
        if not text or not text.strip():
            return 0.0
        try:
            blob = TextBlob(text)
            return blob.sentiment.polarity
        except Exception as e:
            logger.warning(f"[Sentiment] TextBlob error: {e}")
            return 0.0

    def _get_event_text(self, event: Any) -> str:
        """Extract text from news event."""
        if hasattr(event, 'headline'):
            headline = event.headline or ""
            summary = event.summary or ""
        elif isinstance(event, dict):
            headline = event.get('headline', "")
            summary = event.get('summary', "")
        else:
            return ""
        return f"{headline} {summary}".strip()

    def _get_event_weight(self, event: Any) -> float:
        """Get weight based on importance."""
        if hasattr(event, 'importance'):
            importance = event.importance or "low"
        elif isinstance(event, dict):
            importance = event.get('importance', 'low')
        else:
            importance = "low"
        return self.IMPORTANCE_WEIGHTS.get(importance, 1.0)

    def _get_stored_sentiment(self, event: Any) -> Optional[float]:
        """Get pre-computed sentiment score if available."""
        score = None
        if hasattr(event, 'sentiment_score') and event.sentiment_score is not None:
            score = float(event.sentiment_score)
        elif isinstance(event, dict) and event.get('sentiment_score') is not None:
            score = float(event['sentiment_score'])
        return score

    def calculate_sentiment_score(self) -> float:
        """
        Calculate weighted sentiment score from news events.
        Returns float in [-100, +100].
        """
        if not self.news_events:
            return 0.0

        weighted_sum = 0.0
        weight_total = 0.0

        for event in self.news_events:
            weight = self._get_event_weight(event)

            # Try to use pre-computed sentiment first
            stored = self._get_stored_sentiment(event)
            if stored is not None:
                polarity = stored
            else:
                text = self._get_event_text(event)
                polarity = self._score_text(text)

            weighted_sum += polarity * weight
            weight_total += weight

        if weight_total == 0:
            return 0.0

        avg_polarity = weighted_sum / weight_total
        # Scale from [-1, 1] to [-100, 100]
        score = avg_polarity * 100
        return max(-100.0, min(100.0, score))

    def get_summary(self) -> dict[str, Any]:
        """Get summary statistics of sentiment analysis."""
        if not self.news_events:
            return {
                "total_events": 0,
                "avg_score": 0.0,
                "bullish_count": 0,
                "bearish_count": 0,
                "neutral_count": 0,
            }

        scores = []
        for event in self.news_events:
            stored = self._get_stored_sentiment(event)
            if stored is not None:
                scores.append(stored)
            else:
                text = self._get_event_text(event)
                scores.append(self._score_text(text))

        bullish = sum(1 for s in scores if s > 0.1)
        bearish = sum(1 for s in scores if s < -0.1)
        neutral = len(scores) - bullish - bearish

        return {
            "total_events": len(scores),
            "avg_score": sum(scores) / len(scores) if scores else 0.0,
            "bullish_count": bullish,
            "bearish_count": bearish,
            "neutral_count": neutral,
        }
