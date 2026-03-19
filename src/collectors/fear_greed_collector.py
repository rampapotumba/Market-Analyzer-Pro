"""Fear & Greed Index collector for crypto sentiment (SIM-39).

API: https://api.alternative.me/fng/?limit=1
Data stored in macro_data table (indicator="FEAR_GREED", country="GLOBAL").
"""

import datetime
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"
_CACHE_TTL_SECONDS = 3600  # 1 hour

# In-memory cache
_cached_value: Optional[int] = None
_cached_at: Optional[datetime.datetime] = None

# Symbols eligible for Fear & Greed adjustment
_CRYPTO_SYMBOLS: frozenset[str] = frozenset({"BTC/USDT", "ETH/USDT", "BTC/USD", "ETH/USD"})


def get_cached_fear_greed() -> Optional[int]:
    """Return cached Fear & Greed value if fresh (<1h old)."""
    if _cached_value is None or _cached_at is None:
        return None
    age = (datetime.datetime.now(datetime.timezone.utc) - _cached_at).total_seconds()
    return _cached_value if age < _CACHE_TTL_SECONDS else None


async def fetch_fear_greed() -> Optional[int]:
    """Fetch current Fear & Greed Index from alternative.me API."""
    global _cached_value, _cached_at
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(_FEAR_GREED_URL)
            resp.raise_for_status()
            data = resp.json()
            value = int(data["data"][0]["value"])
            _cached_value = value
            _cached_at = datetime.datetime.now(datetime.timezone.utc)
            logger.info("[SIM-39] Fear & Greed Index: %d", value)
            return value
    except Exception as exc:
        logger.warning("[SIM-39] Failed to fetch Fear & Greed: %s", exc)
        return None


def get_fear_greed_adjustment(value: Optional[int], direction: str, symbol: str) -> int:
    """Calculate composite score adjustment based on Fear & Greed.

    Rules:
    - value <= 20 (Extreme Fear): +5 to LONG composite for BTC/ETH
    - value >= 80 (Extreme Greed): +5 to SHORT composite for BTC/ETH
    - 21-79: 0
    - Non-crypto symbols: 0
    """
    if symbol not in _CRYPTO_SYMBOLS:
        return 0
    if value is None:
        return 0
    if value <= 20 and direction == "LONG":
        return 5
    if value >= 80 and direction == "SHORT":
        return 5
    return 0
