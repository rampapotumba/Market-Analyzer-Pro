"""Social Sentiment Collector.

Sources:
  - Reddit (PRAW) — wallstreetbets, investing, CryptoCurrency subreddits
  - Alternative.me Fear & Greed index (crypto)
  - Stocktwits trending symbols
  - Yahoo Finance options Put/Call ratio

Collected data is stored in the `social_sentiment` table.
"""

import datetime
import logging
from decimal import Decimal
from typing import Optional

import httpx
from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.cache import cache
from src.collectors.base import BaseCollector
from src.config import settings
from src.database.engine import async_session_factory
from src.database.models import Instrument, SocialSentiment

logger = logging.getLogger(__name__)

# Alternative.me Fear & Greed
_FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1&format=json"
_FEAR_GREED_CACHE_TTL = 3600  # 1h

# Stocktwits
_STOCKTWITS_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
_STOCKTWITS_CACHE_TTL = 900  # 15min

# Yahoo Finance options
_YAHOO_OPTIONS_URL = "https://query1.finance.yahoo.com/v7/finance/options/{symbol}"
_YAHOO_CACHE_TTL = 900  # 15min

# Reddit
_REDDIT_CACHE_TTL = 1800  # 30min
_REDDIT_POST_LIMIT = 100  # top posts per subreddit per scan

# Subreddits per asset class
_SUBREDDITS: dict[str, list[str]] = {
    "crypto": ["CryptoCurrency", "Bitcoin", "ethereum", "CryptoMarkets"],
    "stocks": ["wallstreetbets", "stocks", "investing", "StockMarket"],
    "forex": ["Forex", "algotrading"],
}


class SocialCollector(BaseCollector):
    """Collects social sentiment from multiple sources."""

    def __init__(self) -> None:
        super().__init__("SocialCollector")

    async def collect(self):  # type: ignore[override]
        """Celery entry point — delegates to collect_all."""
        await self.collect_all()

    async def health_check(self) -> bool:
        """Check if Alternative.me Fear & Greed API is reachable."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(_FEAR_GREED_URL)
                return resp.status_code == 200
        except Exception:
            return False

    async def collect_all(self) -> None:
        """Run all collection tasks for all active instruments."""
        async with async_session_factory() as session:
            result = await session.execute(
                select(Instrument).where(Instrument.is_active.is_(True))
            )
            instruments = result.scalars().all()

        # Global: Fear & Greed index
        fear_greed = await self._fetch_fear_greed()

        async with httpx.AsyncClient(timeout=15.0) as client:
            for instrument in instruments:
                try:
                    await self._collect_instrument(
                        instrument, fear_greed, client
                    )
                except Exception as exc:
                    logger.error(
                        "SocialCollector: failed for %s: %s",
                        instrument.symbol,
                        exc,
                    )

    async def _collect_instrument(
        self,
        instrument: Instrument,
        fear_greed: Optional[float],
        client: httpx.AsyncClient,
    ) -> None:
        symbol = instrument.symbol
        market_type = instrument.market_type  # "crypto" | "stocks" | "forex"

        # Stocktwits (stocks and crypto)
        stocktwits_score: Optional[float] = None
        bullish_pct: Optional[float] = None
        if market_type in ("stocks", "crypto"):
            ticker = symbol.split("/")[0].upper()
            st = await self._fetch_stocktwits(ticker, client)
            if st:
                stocktwits_score = st.get("score")
                bullish_pct = st.get("bullish_pct")

        # Options PCR (stocks only, US equities)
        pcr: Optional[float] = None
        if market_type == "stocks":
            ticker = symbol.split("/")[0].upper()
            pcr = await self._fetch_pcr(ticker, client)

        # Reddit (all market types; requires PRAW credentials)
        reddit_score: Optional[float] = None
        subreddit_list = _SUBREDDITS.get(market_type, [])
        if subreddit_list:
            reddit_score = await self._fetch_reddit_score(symbol, subreddit_list)

        # Save to DB
        row = {
            "instrument_id": instrument.id,
            "source": "combined",
            "timestamp": datetime.datetime.now(datetime.timezone.utc),
            "fear_greed_index": Decimal(str(round(fear_greed, 2))) if fear_greed is not None else None,
            "stocktwits_bullish_pct": Decimal(str(round(bullish_pct, 2))) if bullish_pct is not None else None,
            "put_call_ratio": Decimal(str(round(pcr, 4))) if pcr is not None else None,
            "reddit_score": Decimal(str(round(reddit_score, 2))) if reddit_score is not None else None,
        }

        async with async_session_factory() as session:
            stmt = pg_insert(SocialSentiment).values(**row)
            stmt = stmt.on_conflict_do_nothing()
            await session.execute(stmt)
            await session.commit()

        logger.info(
            "SocialCollector: saved %s — fg=%.1f, st_bull=%.1f%%, pcr=%.3f",
            symbol,
            fear_greed or 0.0,
            bullish_pct or 0.0,
            pcr or 0.0,
        )

    # ── Alternative.me Fear & Greed ────────────────────────────────────────────

    async def _fetch_fear_greed(self) -> Optional[float]:
        """Fetch the current Fear & Greed index (0=extreme fear, 100=extreme greed)."""
        cached = await cache.get("social:fear_greed")
        if cached is not None:
            return float(cached)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(_FEAR_GREED_URL)
                resp.raise_for_status()
                data = resp.json()
                value = float(data["data"][0]["value"])
                await cache.set("social:fear_greed", str(value), ttl=_FEAR_GREED_CACHE_TTL)
                return value
        except Exception as exc:
            logger.warning("SocialCollector: Fear & Greed fetch failed: %s", exc)
            return None

    # ── Stocktwits ─────────────────────────────────────────────────────────────

    async def _fetch_stocktwits(
        self,
        ticker: str,
        client: httpx.AsyncClient,
    ) -> Optional[dict]:
        """Fetch Stocktwits bull/bear ratio for a ticker.

        Returns {"score": float[-100,+100], "bullish_pct": float[0,100]}
        or None on failure.
        """
        cache_key = f"social:stocktwits:{ticker}"
        cached = await cache.get(cache_key)
        if cached is not None:
            import json
            return json.loads(cached)

        try:
            url = _STOCKTWITS_URL.format(symbol=ticker)
            resp = await client.get(url, timeout=10.0)
            if resp.status_code == 429:
                logger.debug("Stocktwits rate-limited for %s", ticker)
                return None
            resp.raise_for_status()
            data = resp.json()

            messages = data.get("messages", [])
            if not messages:
                return None

            bull = sum(
                1
                for m in messages
                if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bullish"
            )
            bear = sum(
                1
                for m in messages
                if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bearish"
            )
            total = bull + bear
            if total == 0:
                return None

            bullish_pct = bull / total * 100.0
            score = (bullish_pct - 50.0) * 2.0  # map to [-100, +100]

            result = {"score": round(score, 2), "bullish_pct": round(bullish_pct, 2)}

            import json
            await cache.set(cache_key, json.dumps(result), ttl=_STOCKTWITS_CACHE_TTL)
            return result

        except Exception as exc:
            logger.debug("Stocktwits fetch failed for %s: %s", ticker, exc)
            return None

    # ── Options PCR ────────────────────────────────────────────────────────────

    async def _fetch_pcr(
        self,
        ticker: str,
        client: httpx.AsyncClient,
    ) -> Optional[float]:
        """Fetch the put/call open interest ratio from Yahoo Finance options."""
        cache_key = f"social:pcr:{ticker}"
        cached = await cache.get(cache_key)
        if cached is not None:
            return float(cached)

        try:
            url = _YAHOO_OPTIONS_URL.format(symbol=ticker)
            resp = await client.get(
                url,
                timeout=10.0,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            data = resp.json()

            chain = data.get("optionChain", {}).get("result", [])
            if not chain:
                return None

            options = chain[0].get("options", [])
            if not options:
                return None

            calls_oi = sum(o.get("openInterest", 0) for o in options[0].get("calls", []))
            puts_oi = sum(o.get("openInterest", 0) for o in options[0].get("puts", []))

            if calls_oi == 0:
                return None
            pcr = puts_oi / calls_oi

            await cache.set(cache_key, str(pcr), ttl=_YAHOO_CACHE_TTL)
            return pcr

        except Exception as exc:
            logger.debug("PCR fetch failed for %s: %s", ticker, exc)
            return None

    # ── Reddit (PRAW) ───────────────────────────────────────────────────────────

    async def _fetch_reddit_score(
        self,
        symbol: str,
        subreddits: list[str],
    ) -> Optional[float]:
        """Score Reddit sentiment for *symbol* across the given *subreddits*.

        Uses the PRAW (Python Reddit API Wrapper) library in read-only mode.
        Requires ``REDDIT_CLIENT_ID`` and ``REDDIT_CLIENT_SECRET`` in settings.

        Scoring logic:
          - Scan up to _REDDIT_POST_LIMIT hot posts per subreddit.
          - A post "mentions" the symbol if the uppercase ticker appears in
            the title or selftext.
          - Post score = log1p(upvotes) × upvote_ratio → rescaled to [-100, +100]
            using the mean upvote_ratio (>0.5 = bullish, <0.5 = bearish).

        Returns None if PRAW is unavailable, credentials are missing, or no
        mentions are found.
        """
        if not settings.REDDIT_CLIENT_ID or not settings.REDDIT_CLIENT_SECRET:
            return None

        cache_key = f"social:reddit:{symbol.upper()}"
        cached = await cache.get(cache_key)
        if cached is not None:
            return float(cached)

        try:
            import asyncpraw  # type: ignore[import-untyped]
        except ImportError:
            try:
                import praw  # type: ignore[import-untyped]
            except ImportError:
                logger.debug("Reddit: neither asyncpraw nor praw installed; skipping")
                return None
            return await self._fetch_reddit_praw_sync(symbol, subreddits, praw, cache_key)

        return await self._fetch_reddit_asyncpraw(symbol, subreddits, asyncpraw, cache_key)

    async def _fetch_reddit_asyncpraw(
        self,
        symbol: str,
        subreddits: list[str],
        asyncpraw,  # type: ignore[no-untyped-def]
        cache_key: str,
    ) -> Optional[float]:
        """Async PRAW implementation."""
        ticker = symbol.split("/")[0].upper()
        mentions: list[tuple[float, float]] = []  # (upvotes, ratio)

        try:
            reddit = asyncpraw.Reddit(
                client_id=settings.REDDIT_CLIENT_ID,
                client_secret=settings.REDDIT_CLIENT_SECRET,
                user_agent=settings.REDDIT_USER_AGENT,
            )
            async with reddit:
                for sub_name in subreddits:
                    try:
                        sub = await reddit.subreddit(sub_name)
                        async for post in sub.hot(limit=_REDDIT_POST_LIMIT):
                            text = f"{post.title} {getattr(post, 'selftext', '')}".upper()
                            if ticker in text:
                                mentions.append((float(post.score), float(post.upvote_ratio)))
                    except Exception as exc:
                        logger.debug("Reddit: error reading r/%s: %s", sub_name, exc)
        except Exception as exc:
            logger.warning("Reddit: PRAW session error: %s", exc)
            return None

        return self._score_mentions(mentions, cache_key)

    async def _fetch_reddit_praw_sync(
        self,
        symbol: str,
        subreddits: list[str],
        praw,  # type: ignore[no-untyped-def]
        cache_key: str,
    ) -> Optional[float]:
        """Synchronous PRAW fallback (runs in thread executor)."""
        import asyncio

        ticker = symbol.split("/")[0].upper()

        def _sync_fetch() -> list[tuple[float, float]]:
            reddit = praw.Reddit(
                client_id=settings.REDDIT_CLIENT_ID,
                client_secret=settings.REDDIT_CLIENT_SECRET,
                user_agent=settings.REDDIT_USER_AGENT,
                read_only=True,
            )
            result: list[tuple[float, float]] = []
            for sub_name in subreddits:
                try:
                    sub = reddit.subreddit(sub_name)
                    for post in sub.hot(limit=_REDDIT_POST_LIMIT):
                        text = f"{post.title} {getattr(post, 'selftext', '')}".upper()
                        if ticker in text:
                            result.append((float(post.score), float(post.upvote_ratio)))
                except Exception as exc:
                    logger.debug("Reddit sync: error reading r/%s: %s", sub_name, exc)
            return result

        try:
            loop = asyncio.get_event_loop()
            mentions = await loop.run_in_executor(None, _sync_fetch)
        except Exception as exc:
            logger.warning("Reddit: sync fetch error: %s", exc)
            return None

        return self._score_mentions(mentions, cache_key)

    async def _score_mentions(
        self,
        mentions: list[tuple[float, float]],
        cache_key: str,
    ) -> Optional[float]:
        """Convert raw mention data to a [-100, +100] sentiment score."""
        import math

        if not mentions:
            return None

        # Weighted average upvote_ratio (weighted by log(score+1))
        total_weight = 0.0
        weighted_ratio = 0.0
        for upvotes, ratio in mentions:
            w = math.log1p(max(upvotes, 0))
            weighted_ratio += ratio * w
            total_weight += w

        if total_weight == 0:
            return None

        avg_ratio = weighted_ratio / total_weight
        # Map [0.0, 1.0] → [-100, +100]: ratio=0.5 → score=0
        score = (avg_ratio - 0.5) * 200.0
        score = max(-100.0, min(100.0, round(score, 2)))

        await cache.set(cache_key, str(score), ttl=_REDDIT_CACHE_TTL)
        logger.info(
            "Reddit: %s — %d mentions, avg_ratio=%.2f, score=%.1f",
            cache_key,
            len(mentions),
            avg_ratio,
            score,
        )
        return score
