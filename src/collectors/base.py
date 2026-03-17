"""Abstract base collector with retry logic, rate limiting and logging."""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class CollectorResult:
    success: bool
    data: Any = None
    error: Optional[str] = None
    records_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseCollector(ABC):
    """Abstract base class for all data collectors."""

    MAX_RETRIES: int = 3
    BACKOFF_BASE: float = 2.0  # seconds
    RATE_LIMIT_SLEEP: float = 0.5  # seconds between requests

    def __init__(self, name: str) -> None:
        self.name = name
        self.logger = logging.getLogger(f"{__name__}.{name}")

    async def _with_retry(self, coro_func, *args, **kwargs) -> Any:
        """Execute async function with exponential backoff retry."""
        last_error: Optional[Exception] = None

        for attempt in range(self.MAX_RETRIES):
            try:
                result = await coro_func(*args, **kwargs)
                return result
            except Exception as exc:
                last_error = exc
                wait_time = self.BACKOFF_BASE ** attempt
                self.logger.warning(
                    f"[{self.name}] Attempt {attempt + 1}/{self.MAX_RETRIES} failed: {exc}. "
                    f"Retrying in {wait_time:.1f}s..."
                )
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(wait_time)

        self.logger.error(f"[{self.name}] All {self.MAX_RETRIES} attempts failed: {last_error}")
        raise last_error

    async def _rate_limit(self) -> None:
        """Apply rate limiting pause."""
        await asyncio.sleep(self.RATE_LIMIT_SLEEP)

    @abstractmethod
    async def collect(self) -> CollectorResult:
        """Main collection method. Returns CollectorResult."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the data source is reachable."""
        ...
