"""Redis TTL-cache wrapper.

Usage:
    from src.cache import cache

    # Store a value (auto-serialised to JSON)
    await cache.set("my_key", {"value": 42}, ttl=300)

    # Retrieve (returns None if missing/expired)
    data = await cache.get("my_key")

    # Delete
    await cache.delete("my_key")

    # Decorator pattern
    @cache.cached(ttl=60, key="signal:{symbol}:{timeframe}")
    async def expensive_fn(symbol: str, timeframe: str) -> dict: ...
"""

import json
import logging
from collections.abc import Callable
from functools import wraps
from typing import Any, Optional

import redis.asyncio as aioredis
from redis.asyncio import Redis
from redis.exceptions import RedisError

from src.config import settings

logger = logging.getLogger(__name__)

# Sentinel value that distinguishes "key not found" from "key stored as None"
_MISSING = object()


class RedisCache:
    """Async Redis cache with JSON serialisation and graceful degradation."""

    def __init__(self) -> None:
        self._client: Optional[Redis] = None

    async def _get_client(self) -> Optional[Redis]:
        if self._client is None:
            try:
                self._client = aioredis.from_url(
                    settings.REDIS_URL,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=2,
                    socket_timeout=2,
                )
                # Verify connectivity
                await self._client.ping()
                logger.info("Redis connection established: %s", settings.REDIS_URL)
            except RedisError as exc:
                logger.warning("Redis unavailable (%s) — cache disabled", exc)
                self._client = None
        return self._client

    async def get(self, key: str) -> Any:
        """Return cached value or *None* if missing / Redis unavailable."""
        client = await self._get_client()
        if client is None:
            return None
        try:
            raw = await client.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except (RedisError, json.JSONDecodeError) as exc:
            logger.debug("Cache GET error for key=%s: %s", key, exc)
            return None

    async def set(self, key: str, value: Any, ttl: int = 300) -> bool:
        """Store *value* serialised as JSON with the given TTL (seconds).

        Returns True on success, False if Redis is unavailable.
        """
        client = await self._get_client()
        if client is None:
            return False
        try:
            serialised = json.dumps(value, default=str)
            await client.setex(key, ttl, serialised)
            return True
        except (RedisError, TypeError) as exc:
            logger.debug("Cache SET error for key=%s: %s", key, exc)
            return False

    async def delete(self, key: str) -> bool:
        """Delete a key. Returns True if the key existed."""
        client = await self._get_client()
        if client is None:
            return False
        try:
            result = await client.delete(key)
            return bool(result)
        except RedisError as exc:
            logger.debug("Cache DELETE error for key=%s: %s", key, exc)
            return False

    async def exists(self, key: str) -> bool:
        """Return True if the key exists in Redis."""
        client = await self._get_client()
        if client is None:
            return False
        try:
            return bool(await client.exists(key))
        except RedisError:
            return False

    async def ttl(self, key: str) -> int:
        """Return remaining TTL in seconds (-1 = no TTL, -2 = not found)."""
        client = await self._get_client()
        if client is None:
            return -2
        try:
            return await client.ttl(key)
        except RedisError:
            return -2

    async def close(self) -> None:
        """Close the Redis connection pool."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def cached(
        self,
        ttl: int = 300,
        key_template: Optional[str] = None,
    ) -> Callable:
        """Decorator: cache the return value of an async function.

        Args:
            ttl: Time-to-live in seconds.
            key_template: Optional format string using function argument names,
                e.g. ``"signal:{symbol}:{timeframe}"``. If omitted, the cache
                key is built from the function's qualified name + all positional
                and keyword arguments.

        Example::

            @cache.cached(ttl=60, key_template="price:{symbol}:{timeframe}")
            async def get_price(symbol: str, timeframe: str) -> list[dict]:
                ...
        """

        def decorator(func: Callable) -> Callable:
            @wraps(func)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                # Build cache key
                if key_template:
                    # Bind positional args to the template using kwarg names
                    import inspect  # noqa: PLC0415

                    sig = inspect.signature(func)
                    bound = sig.bind(*args, **kwargs)
                    bound.apply_defaults()
                    cache_key = key_template.format(**bound.arguments)
                else:
                    parts = [f"{func.__module__}.{func.__qualname__}"]
                    parts.extend(str(a) for a in args)
                    parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
                    cache_key = ":".join(parts)

                cached_value = await self.get(cache_key)
                if cached_value is not None:
                    return cached_value

                result = await func(*args, **kwargs)
                if result is not None:
                    await self.set(cache_key, result, ttl=ttl)
                return result

            return wrapper

        return decorator


# Module-level singleton
cache = RedisCache()
