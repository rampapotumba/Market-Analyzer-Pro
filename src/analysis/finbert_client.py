"""FinBERT microservice HTTP client.

Communicates with the FinBERT sentiment-analysis service running at
FINBERT_SERVICE_URL (default: http://localhost:8001).

Provides graceful degradation: when the service is unavailable all
methods return None so callers can fall back to TextBlob.

Usage::

    client = FinBERTClient()
    result = await client.score("Fed keeps rates unchanged")
    # result = ScoreResult(score=-0.12, label="negative", confidence=0.89)

    results = await client.score_batch([...])
    health = await client.health()
"""

import logging
from typing import Optional

import httpx
from pydantic import BaseModel

from src.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10.0  # seconds


class ScoreResult(BaseModel):
    score: float       # [-1, +1]
    label: str         # "positive" | "negative" | "neutral"
    confidence: float  # [0, 1]


class FinBERTClient:
    """Async HTTP client for the FinBERT microservice."""

    def __init__(self, base_url: Optional[str] = None, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._base_url = (base_url or settings.FINBERT_SERVICE_URL).rstrip("/")
        self._timeout = timeout
        # Shared async client (lazy-initialised, not bound to event loop at __init__)
        self._client: Optional[httpx.AsyncClient] = None

    # ── Public API ─────────────────────────────────────────────────────────────

    async def score(self, text: str) -> Optional[ScoreResult]:
        """Return sentiment for a single text.  Returns None on error."""
        try:
            resp = await self._http().post(
                f"{self._base_url}/score",
                json={"text": text[:1024]},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            return ScoreResult(**resp.json())
        except Exception as exc:
            logger.debug("FinBERT /score error: %s", exc)
            return None

    async def score_batch(self, texts: list[str]) -> Optional[list[Optional[ScoreResult]]]:
        """Score up to 50 texts.  Returns None on transport error;
        individual items may be None if the service skipped them."""
        if not texts:
            return []
        try:
            resp = await self._http().post(
                f"{self._base_url}/batch",
                json={"texts": [t[:1024] for t in texts[:50]]},
                timeout=self._timeout * 3,  # batch takes longer
            )
            resp.raise_for_status()
            data = resp.json()
            return [ScoreResult(**s) for s in data["scores"]]
        except Exception as exc:
            logger.debug("FinBERT /batch error: %s", exc)
            return None

    async def health(self) -> dict:
        """Return the /health payload or an error dict."""
        try:
            resp = await self._http().get(
                f"{self._base_url}/health",
                timeout=5.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            return {"status": "unavailable", "error": str(exc)}

    async def is_healthy(self) -> bool:
        """True if the service is up and the model is loaded."""
        h = await self.health()
        return h.get("status") == "ok"

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── Private ────────────────────────────────────────────────────────────────

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
            )
        return self._client


# Module-level singleton (shared across the process)
finbert = FinBERTClient()
