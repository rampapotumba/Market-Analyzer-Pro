"""Macro data collector: FRED API for economic indicators."""

import asyncio
import datetime
import json
import logging
from decimal import Decimal
from typing import Any, Optional

import httpx

from src.cache import cache
from src.collectors.base import BaseCollector, CollectorResult
from src.config import settings
from src.database.crud import upsert_macro_data
from src.database.engine import async_session_factory

_CACHE_TTL = 3600 * 6  # 6 hours — FRED data updates infrequently

logger = logging.getLogger(__name__)

FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# FRED series to collect
FRED_SERIES = {
    "FEDFUNDS": {"name": "Federal Funds Rate", "country": "US"},
    "CPIAUCSL": {"name": "CPI (All Urban)", "country": "US"},
    "UNRATE": {"name": "Unemployment Rate", "country": "US"},
    "GDPC1": {"name": "Real GDP", "country": "US"},
    "PAYEMS": {"name": "Non-Farm Payrolls", "country": "US"},
    "INDPRO": {"name": "Industrial Production Index", "country": "US"},
    "RETAILSMNSA": {"name": "Retail Sales", "country": "US"},
    "HOUST": {"name": "Housing Starts", "country": "US"},
}


class FREDCollector(BaseCollector):
    """Fetches macroeconomic data from the FRED API."""

    def __init__(self) -> None:
        super().__init__("FRED")
        self.api_key = settings.FRED_KEY

    async def _fetch_series(
        self,
        series_id: str,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        if not self.api_key:
            return []

        cache_key = f"fred:{series_id}:{limit}"
        cached = await cache.get(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except Exception:
                pass

        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": limit,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(FRED_BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()
            observations = data.get("observations", [])

        try:
            await cache.set(cache_key, json.dumps(observations), ttl=_CACHE_TTL)
        except Exception:
            pass

        return observations

    async def collect_series(self, series_id: str) -> CollectorResult:
        """Collect a single FRED series."""
        if not self.api_key:
            logger.info("[FRED] No API key configured, skipping")
            return CollectorResult(success=True, records_count=0, data=[])

        try:
            meta = FRED_SERIES.get(series_id, {})
            observations = await self._with_retry(self._fetch_series, series_id)

            records = []
            for obs in observations:
                if obs.get("value") == ".":
                    continue  # Missing value in FRED
                try:
                    release_dt = datetime.datetime.strptime(obs["date"], "%Y-%m-%d").replace(
                        tzinfo=datetime.timezone.utc
                    )
                    records.append({
                        "indicator_name": series_id,
                        "country": meta.get("country", "US"),
                        "value": Decimal(str(obs["value"])),
                        "previous_value": None,
                        "forecast_value": None,
                        "release_date": release_dt,
                        "source": "FRED",
                    })
                except (ValueError, KeyError) as e:
                    logger.warning(f"[FRED] Skipping observation: {e}")

            if records:
                async with async_session_factory() as session:
                    async with session.begin():
                        count = await upsert_macro_data(session, records)
            else:
                count = 0

            return CollectorResult(success=True, records_count=count, data=records)

        except Exception as exc:
            logger.error(f"[FRED] Error collecting {series_id}: {exc}")
            return CollectorResult(success=False, error=str(exc))

    async def collect(self) -> CollectorResult:
        """Collect all configured FRED series."""
        total = 0
        errors = []

        for series_id in FRED_SERIES:
            result = await self.collect_series(series_id)
            if result.success:
                total += result.records_count
            else:
                errors.append(result.error)
            await self._rate_limit()

        return CollectorResult(
            success=len(errors) == 0,
            records_count=total,
            error="; ".join(errors) if errors else None,
        )

    async def health_check(self) -> bool:
        if not self.api_key:
            return True  # Gracefully degraded
        try:
            obs = await self._fetch_series("FEDFUNDS", limit=1)
            return bool(obs)
        except Exception:
            return False
