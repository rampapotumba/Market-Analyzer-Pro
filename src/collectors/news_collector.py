"""News collector: Finnhub API + TextBlob sentiment scoring."""

import asyncio
import datetime
import json
import logging
from decimal import Decimal
from typing import Any, Optional

import httpx
from textblob import TextBlob

from src.collectors.base import BaseCollector, CollectorResult
from src.config import settings
from src.database.crud import create_news_event
from src.database.engine import async_session_factory

logger = logging.getLogger(__name__)

FINNHUB_BASE_URL = "https://finnhub.io/api/v1"


def _score_sentiment(text: str) -> Decimal:
    """Calculate TextBlob sentiment score, mapped to [-1, 1]."""
    try:
        blob = TextBlob(text)
        polarity = blob.sentiment.polarity  # [-1.0, 1.0]
        return Decimal(str(round(polarity, 4)))
    except Exception:
        return Decimal("0.0")


def _determine_importance(category: str, headline: str) -> str:
    """Determine news importance based on category and keywords."""
    critical_keywords = ["fed", "rate decision", "gdp", "cpi", "nfp", "crash", "crisis", "war"]
    high_keywords = ["inflation", "unemployment", "earnings", "merger", "acquisition", "bankruptcy"]

    text = (category + " " + headline).lower()

    if any(kw in text for kw in critical_keywords):
        return "critical"
    if any(kw in text for kw in high_keywords):
        return "high"
    if category in ("forex", "economic", "central bank"):
        return "medium"
    return "low"


class FinnhubNewsCollector(BaseCollector):
    """Collects market news from Finnhub API."""

    def __init__(self) -> None:
        super().__init__("FinnhubNews")
        self.api_key = settings.FINNHUB_KEY

    async def _fetch_general_news(self, category: str = "general") -> list[dict[str, Any]]:
        """Fetch general market news."""
        if not self.api_key:
            return []

        params = {"category": category, "token": self.api_key}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{FINNHUB_BASE_URL}/news", params=params)
            response.raise_for_status()
            return response.json()

    async def _fetch_company_news(
        self,
        symbol: str,
        from_date: str,
        to_date: str,
    ) -> list[dict[str, Any]]:
        """Fetch company-specific news."""
        if not self.api_key:
            return []

        params = {
            "symbol": symbol,
            "from": from_date,
            "to": to_date,
            "token": self.api_key,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{FINNHUB_BASE_URL}/company-news", params=params)
            response.raise_for_status()
            return response.json()

    async def _process_news_items(
        self, items: list[dict], related_symbols: Optional[list[str]] = None
    ) -> int:
        """Process and save news items to DB."""
        saved = 0
        async with async_session_factory() as session:
            async with session.begin():
                for item in items:
                    try:
                        headline = item.get("headline", "")
                        if not headline:
                            continue

                        published_at = None
                        ts = item.get("datetime")
                        if ts:
                            published_at = datetime.datetime.fromtimestamp(
                                ts, tz=datetime.timezone.utc
                            )

                        sentiment = _score_sentiment(
                            headline + " " + item.get("summary", "")
                        )
                        category = item.get("category", "general")
                        importance = _determine_importance(category, headline)

                        data = {
                            "headline": headline[:500],
                            "summary": (item.get("summary") or "")[:2000],
                            "source": item.get("source", ""),
                            "url": (item.get("url") or "")[:500],
                            "published_at": published_at,
                            "sentiment_score": sentiment,
                            "importance": importance,
                            "related_instruments": json.dumps(related_symbols or []),
                            "category": category[:64],
                        }
                        await create_news_event(session, data)
                        saved += 1
                    except Exception as e:
                        logger.warning(f"[FinnhubNews] Failed to save news item: {e}")

        return saved

    async def collect_general_news(self) -> CollectorResult:
        """Collect general market news."""
        if not self.api_key:
            logger.info("[FinnhubNews] No API key, skipping")
            return CollectorResult(success=True, records_count=0)

        try:
            items = await self._with_retry(self._fetch_general_news, "general")
            saved = await self._process_news_items(items)
            return CollectorResult(success=True, records_count=saved)
        except Exception as exc:
            logger.error(f"[FinnhubNews] Error: {exc}")
            return CollectorResult(success=False, error=str(exc))

    async def collect(self) -> CollectorResult:
        """Main collection: general market news."""
        return await self.collect_general_news()

    async def health_check(self) -> bool:
        if not self.api_key:
            return True  # Gracefully degraded
        try:
            items = await self._fetch_general_news("general")
            return isinstance(items, list)
        except Exception:
            return False
