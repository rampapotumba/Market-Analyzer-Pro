"""Real-time news collector via RSS feeds. No API key required."""

import asyncio
import datetime
import json
import logging
from typing import Any

import feedparser
import httpx
from textblob import TextBlob

from src.collectors.base import BaseCollector, CollectorResult
from src.database.crud import create_news_event
from src.database.engine import async_session_factory

logger = logging.getLogger(__name__)

# RSS feeds — general market news (no auth required)
RSS_FEEDS: list[dict[str, Any]] = [
    # Forex / Macro
    {
        "url": "https://investinglive.com/feed/news",
        "category": "forex",
        "symbols": ["EURUSD=X", "GBPUSD=X", "USDJPY=X"],
    },
    {
        "url": "https://www.fxstreet.com/rss",
        "category": "forex",
        "symbols": ["EURUSD=X", "GBPUSD=X", "USDJPY=X"],
    },
    # General markets
    {
        "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "category": "general",
        "symbols": [],
    },
    {
        "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        "category": "general",
        "symbols": [],
    },
    # Stocks
    {
        "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SPY&region=US&lang=en-US",
        "category": "stock",
        "symbols": ["SPY"],
    },
    # Crypto
    {
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "category": "crypto",
        "symbols": ["BTC/USDT", "ETH/USDT"],
    },
    {
        "url": "https://cointelegraph.com/rss",
        "category": "crypto",
        "symbols": ["BTC/USDT", "ETH/USDT"],
    },
    # Central banks / macro
    {
        "url": "https://www.federalreserve.gov/feeds/press_all.xml",
        "category": "central bank",
        "symbols": [],
    },
]

CRITICAL_KEYWORDS = ["fed", "rate decision", "gdp", "cpi", "nfp", "crash", "crisis", "war", "recession", "emergency"]
HIGH_KEYWORDS = ["inflation", "unemployment", "earnings", "merger", "acquisition", "bankruptcy", "powell", "lagarde", "rate hike", "rate cut"]


def _score_sentiment(text: str) -> float:
    try:
        from decimal import Decimal
        from textblob import TextBlob
        return float(round(TextBlob(text).sentiment.polarity, 4))
    except Exception:
        return 0.0


def _determine_importance(category: str, headline: str) -> str:
    text = (category + " " + headline).lower()
    if any(kw in text for kw in CRITICAL_KEYWORDS):
        return "critical"
    if any(kw in text for kw in HIGH_KEYWORDS):
        return "high"
    if category in ("forex", "economic", "central bank"):
        return "medium"
    return "low"


class RSSNewsCollector(BaseCollector):
    """
    Polls multiple RSS feeds and saves new articles to the DB.
    Tracks seen URLs in-memory to avoid duplicates within a session.
    """

    def __init__(self) -> None:
        super().__init__("RSSNews")
        self._seen_urls: set[str] = set()

    async def _fetch_feed(self, feed_cfg: dict[str, Any]) -> list[dict[str, Any]]:
        """Download and parse a single RSS feed."""
        url = feed_cfg["url"]
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; MarketAnalyzerBot/1.0)"},
                )
                if resp.status_code != 200:
                    logger.debug(f"[RSS] {url} → {resp.status_code}")
                    return []
                content = resp.content

            parsed = feedparser.parse(content)
            items = []
            for entry in parsed.entries:
                article_url = entry.get("link", "")
                if not article_url or article_url in self._seen_urls:
                    continue

                headline = entry.get("title", "").strip()
                if not headline:
                    continue

                summary = entry.get("summary", "") or entry.get("description", "") or ""

                # Parse published date
                published_at = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    import time
                    ts = time.mktime(entry.published_parsed)
                    published_at = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
                elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    import time
                    ts = time.mktime(entry.updated_parsed)
                    published_at = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)

                # Skip articles older than 24 hours
                if published_at:
                    age = datetime.datetime.now(datetime.timezone.utc) - published_at
                    if age.total_seconds() > 86400:
                        continue

                items.append({
                    "url": article_url,
                    "headline": headline,
                    "summary": summary,
                    "published_at": published_at,
                    "category": feed_cfg["category"],
                    "symbols": feed_cfg["symbols"],
                    "source": parsed.feed.get("title", url.split("/")[2]),
                })

            return items

        except Exception as exc:
            logger.debug(f"[RSS] Failed to fetch {url}: {exc}")
            return []

    async def collect(self) -> CollectorResult:
        """Fetch all feeds and save new articles."""
        # Fetch all feeds concurrently
        tasks = [self._fetch_feed(cfg) for cfg in RSS_FEEDS]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_items: list[dict[str, Any]] = []
        for r in results:
            if isinstance(r, list):
                all_items.extend(r)

        if not all_items:
            return CollectorResult(success=True, records_count=0)

        saved = 0
        affected_symbols: set[str] = set()
        has_general_news = False

        async with async_session_factory() as session:
            async with session.begin():
                for item in all_items:
                    try:
                        text = item["headline"] + " " + item["summary"]
                        sentiment = _score_sentiment(text)
                        importance = _determine_importance(item["category"], item["headline"])

                        from decimal import Decimal
                        await create_news_event(session, {
                            "headline": item["headline"][:500],
                            "summary": item["summary"][:2000],
                            "source": item["source"][:100],
                            "url": item["url"][:500],
                            "published_at": item["published_at"],
                            "sentiment_score": Decimal(str(sentiment)),
                            "importance": importance,
                            "related_instruments": json.dumps(item["symbols"]),
                            "category": item["category"][:64],
                        })
                        self._seen_urls.add(item["url"])
                        saved += 1

                        # Track which symbols/categories this news affects
                        if item["symbols"]:
                            affected_symbols.update(item["symbols"])
                        elif item["category"] in ("general", "central bank"):
                            has_general_news = True

                    except Exception as exc:
                        logger.debug(f"[RSS] Failed to save item: {exc}")

        if saved:
            logger.info(f"[RSS] Saved {saved} new articles")

        return CollectorResult(
            success=True,
            records_count=saved,
            metadata={
                "affected_symbols": list(affected_symbols),
                "has_general_news": has_general_news,
            },
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(RSS_FEEDS[0]["url"])
                return r.status_code == 200
        except Exception:
            return False
