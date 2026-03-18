"""Geopolitical Risk Engine v2.

Uses GDELT (Global Database of Events, Language, and Tone) to score
geopolitical risk for countries relevant to each instrument.

GDELT API:
  https://api.gdeltproject.org/api/v2/doc/doc?query=...&mode=ArtList

Score output: [-100, +100]
  Negative = geopolitical stress / risk event → bearish for risk assets
  Positive = stable / de-escalating environment → bullish for risk assets

Country → instrument mapping is maintained in _COUNTRY_INSTRUMENTS.
"""

import datetime
import logging
from decimal import Decimal
from typing import Optional

import httpx

from src.cache import cache

logger = logging.getLogger(__name__)

# GDELT API
_GDELT_BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
_GDELT_CACHE_TTL = 3600  # 1h

# Risk event keywords that trigger elevated geopolitical risk
_RISK_KEYWORDS = [
    "war", "military", "sanctions", "conflict", "attack", "invasion",
    "nuclear", "crisis", "coup", "protest", "revolution", "terrorism",
    "default", "recession", "inflation surge",
]

# Map country codes to affected instrument symbols (subset)
_COUNTRY_INSTRUMENTS: dict[str, list[str]] = {
    "US": ["EURUSD", "USDJPY", "GBPUSD", "XAUUSD", "SPY", "QQQ", "BTC/USDT"],
    "EU": ["EURUSD", "EURJPY", "EURGBP"],
    "UK": ["GBPUSD", "EURGBP"],
    "JP": ["USDJPY", "EURJPY"],
    "CN": ["BTC/USDT", "AUDUSD"],
    "RU": ["XAUUSD", "USOIL"],
    "ME": ["USOIL", "XAUUSD"],   # Middle East
    "EM": ["AUDUSD", "NZDUSD"],  # Emerging markets general
}

# Tone breakpoints → score mapping
# GDELT tone: negative = hostile/negative coverage, positive = positive coverage
_TONE_NEUTRAL = 0.0
_TONE_NEGATIVE_THRESHOLD = -3.0
_TONE_POSITIVE_THRESHOLD = 3.0


class GeoEngineV2:
    """Geopolitical risk engine powered by GDELT."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=15.0)

    async def close(self) -> None:
        await self._client.aclose()

    # ── Public API ─────────────────────────────────────────────────────────────

    async def calculate_geopolitical_risk(self, symbol: str) -> float:
        """Return a geopolitical risk score for the given symbol.

        Positive = low risk / stable (bullish), negative = high risk (bearish).
        """
        countries = _symbol_to_countries(symbol)
        if not countries:
            return 0.0

        scores: list[float] = []
        for country in countries:
            tone = await self.fetch_gdelt_tone(country)
            if tone is not None:
                scores.append(_tone_to_score(tone))

        if not scores:
            return 0.0
        return max(-100.0, min(100.0, sum(scores) / len(scores)))

    async def fetch_gdelt_tone(self, country: str) -> Optional[float]:
        """Fetch average GDELT tone for a country over the last 24h.

        Returns tone value (negative = hostile news, positive = positive news).
        """
        cache_key = f"geo:tone:{country}"
        cached = await cache.get(cache_key)
        if cached is not None:
            return float(cached)

        try:
            # GDELT query: articles mentioning the country in last 24h
            query = f"sourcecountry:{country} lang:English"
            params = {
                "query": query,
                "mode": "artlist",
                "maxrecords": "50",
                "format": "json",
                "timespan": "24h",
                "sort": "datedesc",
            }
            resp = await self._client.get(_GDELT_BASE, params=params, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()

            articles = data.get("articles", [])
            if not articles:
                return None

            tones = [
                float(a["tone"].split(",")[0])  # first field is overall tone
                for a in articles
                if "tone" in a and a["tone"]
            ]
            if not tones:
                return None

            avg_tone = sum(tones) / len(tones)
            await cache.set(cache_key, str(avg_tone), ttl=_GDELT_CACHE_TTL)
            return avg_tone

        except Exception as exc:
            logger.warning("GeoEngineV2: GDELT fetch failed for %s: %s", country, exc)
            return None  # circuit breaker → 0 (caller handles None → 0)

    async def detect_risk_events(self, country: str) -> list[dict]:
        """Return a list of high-risk news events for the country from GDELT."""
        try:
            keyword_query = " OR ".join(_RISK_KEYWORDS[:8])  # limit query length
            query = f"({keyword_query}) sourcecountry:{country} lang:English"
            params = {
                "query": query,
                "mode": "artlist",
                "maxrecords": "20",
                "format": "json",
                "timespan": "24h",
                "sort": "tonedesc",  # most negative tone first
            }
            resp = await self._client.get(_GDELT_BASE, params=params, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()

            events = []
            for a in data.get("articles", []):
                tone_raw = a.get("tone", "")
                tone = float(tone_raw.split(",")[0]) if tone_raw else 0.0
                events.append({
                    "title": a.get("title", ""),
                    "url": a.get("url", ""),
                    "tone": tone,
                    "seendate": a.get("seendate", ""),
                })
            return events

        except Exception as exc:
            logger.warning("GeoEngineV2: risk event detection failed for %s: %s", country, exc)
            return []

    async def score(self, symbol: str) -> float:
        """Alias for calculate_geopolitical_risk with circuit-breaker default."""
        try:
            return await self.calculate_geopolitical_risk(symbol)
        except Exception as exc:
            logger.error("GeoEngineV2.score error for %s: %s", symbol, exc)
            return 0.0


# ── Helper functions ───────────────────────────────────────────────────────────

def _symbol_to_countries(symbol: str) -> list[str]:
    """Return the list of countries relevant to a trading symbol."""
    sym = symbol.upper().replace("/", "").replace("_", "").replace("-", "")
    countries = []
    for country, symbols in _COUNTRY_INSTRUMENTS.items():
        normalised = [s.upper().replace("/", "") for s in symbols]
        if sym in normalised:
            countries.append(country)
    return countries


def _tone_to_score(tone: float) -> float:
    """Map GDELT tone to [-100, +100] sentiment score.

    GDELT tone ≈ % positive mentions - % negative mentions.
    Typical range: -10 to +3.

    We map:
      tone >= +3.0 → +100 (very positive coverage)
      tone   0.0   →   0  (neutral)
      tone <= -3.0 → -100 (hostile/negative coverage)
    """
    if tone >= _TONE_POSITIVE_THRESHOLD:
        return 100.0
    if tone <= _TONE_NEGATIVE_THRESHOLD:
        return -100.0
    if tone >= 0:
        return (tone / _TONE_POSITIVE_THRESHOLD) * 100.0
    return (tone / abs(_TONE_NEGATIVE_THRESHOLD)) * 100.0
