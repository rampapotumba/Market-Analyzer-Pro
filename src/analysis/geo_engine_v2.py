"""Geopolitical Risk Engine v2.

Uses GDELT (Global Database of Events, Language, and Tone) to score
geopolitical risk for countries relevant to each instrument.

GDELT API:
  https://api.gdeltproject.org/api/v2/doc/doc?query=...&mode=ArtList

Score output: [-50, +50] (clamped from internal [-100, +100])
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

# Circuit breaker constants
_CB_FAIL_THRESHOLD = 3        # consecutive failures before tripping
_CB_COOLDOWN_SECONDS = 3600   # 1 hour pause after tripping

# Cache key prefixes
_CB_FAIL_KEY_PREFIX = "geo:cb:fail:"
_CB_TRIPPED_KEY_PREFIX = "geo:cb:tripped:"

# Risk event keywords that trigger elevated geopolitical risk
_RISK_KEYWORDS = [
    "war", "military", "sanctions", "conflict", "attack", "invasion",
    "nuclear", "crisis", "coup", "protest", "revolution", "terrorism",
    "default", "recession", "inflation surge",
]

# Theme-based queries for each country code.
# Theme-based approach is more reliable than sourcecountry: alone because
# GDELT themes are pre-tagged and the sourcecountry filter often returns
# empty arrays with ISO-2 codes (GDELT uses FIPS internally).
_COUNTRY_PRIMARY_QUERY: dict[str, str] = {
    "US": (
        "theme:TAX_FNCACT OR theme:ECON_BANKRUPTCY OR theme:POLITICAL_TURMOIL "
        "OR theme:ECON_INFLATION OR theme:ECON_UNEMPLOYMENT "
        "sourcecountry:US lang:English"
    ),
    "EU": (
        "theme:EUROZONE OR theme:ECON_INFLATION OR theme:POLITICAL_TURMOIL "
        "domain:ecb.europa.eu OR sourcecountry:EI OR sourcecountry:GM lang:English"
    ),
    "UK": (
        "theme:POLITICAL_TURMOIL OR theme:ECON_INFLATION OR theme:ECON_BREXIT "
        "sourcecountry:UK lang:English"
    ),
    "JP": (
        "theme:ECON_INFLATION OR theme:ECON_JAPANECONOMY OR theme:POLITICAL_TURMOIL "
        "sourcecountry:JA lang:English"
    ),
    "CN": (
        "theme:ECON_CHINACURRENCY OR theme:ECON_TRADEWAR OR theme:POLITICAL_TURMOIL "
        "OR theme:ECON_INFLATION sourcecountry:CH lang:English"
    ),
    "RU": (
        "theme:MILITARY OR theme:SANCTION OR theme:POLITICAL_TURMOIL "
        "sourcecountry:RS lang:English"
    ),
    "ME": (
        "theme:MILITARY OR theme:OIL OR theme:POLITICAL_TURMOIL "
        "sourcecountry:IZ OR sourcecountry:SA OR sourcecountry:IR lang:English"
    ),
    "EM": (
        "theme:ECON_EMERGING OR theme:ECON_INFLATION OR theme:POLITICAL_TURMOIL "
        "lang:English"
    ),
}

# Fallback queries — broader (no country filter, just themes)
_COUNTRY_FALLBACK_QUERY: dict[str, str] = {
    "US": "theme:TAX_FNCACT OR theme:ECON_BANKRUPTCY OR theme:ECON_INFLATION lang:English",
    "EU": "theme:EUROZONE OR theme:ECON_INFLATION lang:English",
    "UK": "theme:ECON_BREXIT OR theme:POLITICAL_TURMOIL lang:English",
    "JP": "theme:ECON_JAPANECONOMY OR theme:ECON_INFLATION lang:English",
    "CN": "theme:ECON_CHINACURRENCY OR theme:ECON_TRADEWAR lang:English",
    "RU": "theme:MILITARY OR theme:SANCTION lang:English",
    "ME": "theme:MILITARY OR theme:OIL lang:English",
    "EM": "theme:ECON_EMERGING OR theme:ECON_INFLATION lang:English",
}

# Map country codes to affected instrument symbols.
# Symbols must match the format used throughout the system (with suffix =X, =F etc.)
_COUNTRY_INSTRUMENTS: dict[str, list[str]] = {
    "US": ["EURUSD=X", "USDJPY=X", "GBPUSD=X", "GC=F", "SPY", "QQQ", "BTC/USDT"],
    "EU": ["EURUSD=X", "EURJPY=X", "EURGBP=X"],
    "UK": ["GBPUSD=X", "EURGBP=X"],
    "JP": ["USDJPY=X", "EURJPY=X"],
    "CN": ["BTC/USDT", "AUDUSD=X"],
    "RU": ["GC=F", "CL=F"],       # Gold and crude oil proxies
    "ME": ["CL=F", "GC=F"],        # Middle East — oil and gold
    "EM": ["AUDUSD=X", "NZDUSD=X"],  # Emerging markets general
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
        Result is clamped to [-50, +50].
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
        raw = sum(scores) / len(scores)
        return max(-50.0, min(50.0, raw))

    async def fetch_gdelt_tone(self, country: str) -> Optional[float]:
        """Fetch average GDELT tone for a country over the last 24h.

        Uses theme-based primary query; falls back to broader query when
        primary returns an empty article list.

        Returns tone value (negative = hostile news, positive = positive news).
        Returns None on failure (caller treats None as neutral / 0).
        """
        cache_key = f"geo:tone:{country}"
        cached = await cache.get(cache_key)
        if cached is not None:
            return float(cached)

        # Circuit breaker check
        if await self._is_circuit_open(country):
            logger.warning(
                "GeoEngineV2: circuit breaker open for %s — skipping GDELT", country
            )
            return None

        primary_query = _COUNTRY_PRIMARY_QUERY.get(country)
        if primary_query is None:
            logger.warning("GeoEngineV2: no query template for country %s", country)
            return None

        tone = await self._fetch_tone_with_query(country, primary_query)

        if tone is None:
            # Try fallback (broader query, no country filter)
            fallback_query = _COUNTRY_FALLBACK_QUERY.get(country)
            if fallback_query:
                logger.warning(
                    "GeoEngineV2: primary query empty for %s — trying fallback", country
                )
                tone = await self._fetch_tone_with_query(country, fallback_query, is_fallback=True)

        if tone is not None:
            await self._reset_circuit(country)
            await cache.set(cache_key, str(tone), ttl=_GDELT_CACHE_TTL)
        else:
            await self._record_failure(country)

        return tone

    async def detect_risk_events(self, country: str) -> list[dict]:
        """Return a list of high-risk news events for the country from GDELT."""
        if await self._is_circuit_open(country):
            logger.warning(
                "GeoEngineV2: circuit breaker open for %s — skipping risk event detection",
                country,
            )
            return []

        primary_query = _COUNTRY_PRIMARY_QUERY.get(country)
        if primary_query is None:
            return []

        keyword_query = " OR ".join(_RISK_KEYWORDS[:8])
        query = f"({keyword_query}) {primary_query}"

        try:
            params = {
                "query": query,
                "mode": "artlist",
                "maxrecords": "20",
                "format": "json",
                "timespan": "24h",
                "sort": "tonedesc",
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
            logger.warning(
                "GeoEngineV2: risk event detection failed for %s: %s", country, exc
            )
            return []

    async def score(self, symbol: str) -> float:
        """Alias for calculate_geopolitical_risk with circuit-breaker default."""
        try:
            return await self.calculate_geopolitical_risk(symbol)
        except Exception as exc:
            logger.error("GeoEngineV2.score error for %s: %s", symbol, exc)
            return 0.0

    # ── Circuit breaker helpers ─────────────────────────────────────────────────

    async def _is_circuit_open(self, country: str) -> bool:
        """Return True if the circuit breaker is tripped for this country."""
        tripped_key = f"{_CB_TRIPPED_KEY_PREFIX}{country}"
        return bool(await cache.get(tripped_key))

    async def _record_failure(self, country: str) -> None:
        """Increment failure counter; trip the circuit if threshold is reached."""
        fail_key = f"{_CB_FAIL_KEY_PREFIX}{country}"
        raw = await cache.get(fail_key)
        failures = int(raw) if raw is not None else 0
        failures += 1

        if failures >= _CB_FAIL_THRESHOLD:
            tripped_key = f"{_CB_TRIPPED_KEY_PREFIX}{country}"
            await cache.set(tripped_key, "1", ttl=_CB_COOLDOWN_SECONDS)
            await cache.delete(fail_key)
            logger.warning(
                "GeoEngineV2: circuit breaker TRIPPED for %s after %d consecutive failures",
                country,
                failures,
            )
        else:
            await cache.set(fail_key, str(failures), ttl=_CB_COOLDOWN_SECONDS)
            logger.warning(
                "GeoEngineV2: GDELT failure %d/%d for %s",
                failures,
                _CB_FAIL_THRESHOLD,
                country,
            )

    async def _reset_circuit(self, country: str) -> None:
        """Reset failure counter after a successful request."""
        fail_key = f"{_CB_FAIL_KEY_PREFIX}{country}"
        await cache.delete(fail_key)

    # ── Internal fetch helpers ──────────────────────────────────────────────────

    async def _fetch_tone_with_query(
        self,
        country: str,
        query: str,
        *,
        is_fallback: bool = False,
    ) -> Optional[float]:
        """Execute a single GDELT artlist request and return average tone.

        Returns None when the response contains no articles or on error.
        """
        label = "fallback" if is_fallback else "primary"
        try:
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
                logger.warning(
                    "GeoEngineV2: %s query returned empty articles for %s",
                    label,
                    country,
                )
                return None

            tones = [
                float(a["tone"].split(",")[0])
                for a in articles
                if "tone" in a and a["tone"]
            ]
            if not tones:
                return None

            return sum(tones) / len(tones)

        except Exception as exc:
            logger.warning(
                "GeoEngineV2: GDELT %s fetch failed for %s: %s", label, country, exc
            )
            return None


# ── Helper functions ───────────────────────────────────────────────────────────

def _symbol_to_countries(symbol: str) -> list[str]:
    """Return the list of countries relevant to a trading symbol.

    Normalisation strips slashes and hyphens so that e.g. "EURUSD=X",
    "EURUSD", "EUR/USD" all resolve correctly against stored keys.
    """
    # Normalise: upper-case, strip /, -, _ but keep = so "GC=F" stays "GC=F"
    sym_norm = symbol.upper().replace("/", "").replace("-", "").replace("_", "")
    countries = []
    for country, symbols in _COUNTRY_INSTRUMENTS.items():
        normalised = [
            s.upper().replace("/", "").replace("-", "").replace("_", "")
            for s in symbols
        ]
        if sym_norm in normalised:
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
