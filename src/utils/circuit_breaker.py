"""Circuit Breaker pattern for unstable external APIs.

States:
  CLOSED   — normal operation; calls pass through
  OPEN     — failure threshold exceeded; calls are short-circuited
  HALF_OPEN — probe state; one call allowed to test recovery

Usage:
    cb = CircuitBreaker("GDELT", failure_threshold=5, reset_timeout=60)

    @cb.guard
    async def fetch_gdelt():
        ...

    # Or inline:
    result = await cb.call(fetch_gdelt_raw)
"""

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class CBState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpen(Exception):
    """Raised when a call is rejected because the circuit is OPEN."""


class CircuitBreaker:
    """
    Thread-safe (asyncio) circuit breaker.

    Args:
        name:              Descriptive name for logging.
        failure_threshold: Number of consecutive failures before opening.
        reset_timeout:     Seconds to wait before transitioning to HALF_OPEN.
        success_threshold: Consecutive successes in HALF_OPEN before closing.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        reset_timeout: float = 60.0,
        success_threshold: int = 2,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.success_threshold = success_threshold

        self._state = CBState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = asyncio.Lock()

    # ── State ─────────────────────────────────────────────────────────────────

    @property
    def state(self) -> CBState:
        return self._state

    @property
    def is_open(self) -> bool:
        return self._state == CBState.OPEN

    # ── Main interface ────────────────────────────────────────────────────────

    async def call(self, coro_func: Callable, *args, **kwargs) -> Any:
        """
        Execute `coro_func(*args, **kwargs)` if circuit allows,
        raise CircuitBreakerOpen otherwise.
        """
        async with self._lock:
            if self._state == CBState.OPEN:
                elapsed = time.monotonic() - (self._last_failure_time or 0)
                if elapsed >= self.reset_timeout:
                    logger.info("[CB:%s] Transitioning OPEN → HALF_OPEN", self.name)
                    self._state = CBState.HALF_OPEN
                    self._success_count = 0
                else:
                    raise CircuitBreakerOpen(
                        f"Circuit {self.name!r} is OPEN "
                        f"({self.reset_timeout - elapsed:.0f}s remaining)"
                    )

        try:
            result = await coro_func(*args, **kwargs)
        except Exception as exc:
            await self._on_failure(exc)
            raise

        await self._on_success()
        return result

    def guard(self, func: Callable) -> Callable:
        """Decorator: wrap an async function with this circuit breaker."""
        import functools

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            return await self.call(func, *args, **kwargs)

        return wrapper

    # ── State transitions ─────────────────────────────────────────────────────

    async def _on_success(self) -> None:
        async with self._lock:
            if self._state == CBState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    logger.info("[CB:%s] Transitioning HALF_OPEN → CLOSED", self.name)
                    self._state = CBState.CLOSED
                    self._failure_count = 0
            elif self._state == CBState.CLOSED:
                self._failure_count = 0

    async def _on_failure(self, exc: Exception) -> None:
        async with self._lock:
            self._last_failure_time = time.monotonic()

            if self._state == CBState.HALF_OPEN:
                logger.warning(
                    "[CB:%s] Probe failed (%s) — returning to OPEN", self.name, exc
                )
                self._state = CBState.OPEN
                return

            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                logger.error(
                    "[CB:%s] Threshold reached (%d/%d) — OPEN (timeout=%ds)",
                    self.name,
                    self._failure_count,
                    self.failure_threshold,
                    self.reset_timeout,
                )
                self._state = CBState.OPEN

    # ── Manual control ────────────────────────────────────────────────────────

    async def reset(self) -> None:
        """Manually reset to CLOSED state."""
        async with self._lock:
            self._state = CBState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = None
        logger.info("[CB:%s] Manually reset to CLOSED", self.name)

    def stats(self) -> dict:
        return {
            "name": self.name,
            "state": self._state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "last_failure_ago": (
                round(time.monotonic() - self._last_failure_time, 1)
                if self._last_failure_time
                else None
            ),
        }


# ── Pre-built instances for known unstable APIs ───────────────────────────────

gdelt_cb = CircuitBreaker("GDELT", failure_threshold=5, reset_timeout=120)
glassnode_cb = CircuitBreaker("Glassnode", failure_threshold=3, reset_timeout=300)
finnhub_cb = CircuitBreaker("Finnhub", failure_threshold=5, reset_timeout=60)
fred_cb = CircuitBreaker("FRED", failure_threshold=3, reset_timeout=120)
finbert_cb = CircuitBreaker("FinBERT", failure_threshold=5, reset_timeout=30)
