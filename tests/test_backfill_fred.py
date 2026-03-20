"""Tests for backfill_fred() in scripts/backfill_historical.py — TASK-V7-06."""

import datetime
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from scripts.backfill_historical import (
    _fetch_fred_series,
    _parse_fred_observations,
    backfill_fred,
)
from src.collectors.macro_collector import FRED_SERIES


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_observations(count: int, with_missing: int = 0) -> list[dict[str, Any]]:
    """Build a list of fake FRED observation dicts.

    Args:
        count: Number of valid (non-missing) observations.
        with_missing: Number of "." (missing) observations appended at the end.
    """
    obs = [
        {"date": f"200{i // 12}-{(i % 12) + 1:02d}-01", "value": str(5.0 + i * 0.1)}
        for i in range(count)
    ]
    obs += [{"date": f"2025-{i + 1:02d}-01", "value": "."} for i in range(with_missing)]
    return obs


def _make_session_factory_mock() -> tuple[MagicMock, AsyncMock]:
    """Create a mock async_session_factory that returns an async context manager.

    Returns:
        Tuple of (factory_mock, session_mock).
    """
    mock_session = AsyncMock(spec=AsyncSession)

    begin_cm = AsyncMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=begin_cm)

    @asynccontextmanager
    async def _factory():
        yield mock_session

    factory_mock = MagicMock(side_effect=_factory)
    return factory_mock, mock_session


# ── _parse_fred_observations ──────────────────────────────────────────────────


class TestParseFredObservations:
    def test_valid_observations_parsed_correctly(self) -> None:
        obs = [
            {"date": "2023-01-01", "value": "5.33"},
            {"date": "2023-02-01", "value": "5.50"},
        ]
        records = _parse_fred_observations("FEDFUNDS", obs, "US")

        assert len(records) == 2
        assert records[0]["indicator_name"] == "FEDFUNDS"
        assert records[0]["country"] == "US"
        assert records[0]["value"] == Decimal("5.33")
        assert records[0]["source"] == "FRED"
        assert records[0]["release_date"] == datetime.datetime(
            2023, 1, 1, tzinfo=datetime.timezone.utc
        )
        assert records[0]["previous_value"] is None
        assert records[0]["forecast_value"] is None

    def test_missing_value_dot_skipped(self) -> None:
        obs = [
            {"date": "2023-01-01", "value": "."},
            {"date": "2023-02-01", "value": "5.50"},
        ]
        records = _parse_fred_observations("FEDFUNDS", obs, "US")

        assert len(records) == 1
        assert records[0]["value"] == Decimal("5.50")

    def test_all_missing_returns_empty(self) -> None:
        obs = [
            {"date": "2023-01-01", "value": "."},
            {"date": "2023-02-01", "value": "."},
        ]
        records = _parse_fred_observations("FEDFUNDS", obs, "US")
        assert records == []

    def test_empty_observations_returns_empty(self) -> None:
        records = _parse_fred_observations("FEDFUNDS", [], "US")
        assert records == []

    def test_malformed_date_skipped(self) -> None:
        obs = [
            {"date": "not-a-date", "value": "5.0"},
            {"date": "2023-06-01", "value": "4.5"},
        ]
        records = _parse_fred_observations("CPIAUCSL", obs, "US")
        assert len(records) == 1
        assert records[0]["value"] == Decimal("4.5")

    def test_country_passed_through(self) -> None:
        obs = [{"date": "2023-01-01", "value": "1.5"}]
        records = _parse_fred_observations("SOMESER", obs, "DE")
        assert records[0]["country"] == "DE"

    def test_decimal_precision_preserved(self) -> None:
        obs = [{"date": "2020-03-01", "value": "1234.56789"}]
        records = _parse_fred_observations("GDPC1", obs, "US")
        assert records[0]["value"] == Decimal("1234.56789")


# ── backfill_fred ─────────────────────────────────────────────────────────────


class TestBackfillFred:
    @pytest.mark.asyncio
    async def test_no_api_key_returns_zero(self) -> None:
        with patch("src.config.settings") as mock_settings:
            mock_settings.FRED_KEY = ""
            result = await backfill_fred(dry_run=False)

        assert result == 0

    @pytest.mark.asyncio
    async def test_dry_run_does_not_write_to_db(self) -> None:
        observations = _make_observations(count=5)

        with (
            patch("src.config.settings") as mock_settings,
            patch("scripts.backfill_historical._fetch_fred_series", new_callable=AsyncMock) as mock_fetch,
            patch("src.database.crud.upsert_macro_data", new_callable=AsyncMock) as mock_upsert,
        ):
            mock_settings.FRED_KEY = "test_key"
            mock_fetch.return_value = observations

            result = await backfill_fred(dry_run=True)

        mock_upsert.assert_not_called()
        assert result == 0

    @pytest.mark.asyncio
    async def test_upserts_records_for_each_series(self) -> None:
        observations = _make_observations(count=10)
        upsert_count_per_series = 10

        factory_mock, _ = _make_session_factory_mock()

        with (
            patch("src.config.settings") as mock_settings,
            patch("scripts.backfill_historical._fetch_fred_series", new_callable=AsyncMock) as mock_fetch,
            patch("src.database.crud.upsert_macro_data", new_callable=AsyncMock) as mock_upsert,
            patch("src.database.engine.async_session_factory", factory_mock),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_settings.FRED_KEY = "test_key"
            mock_fetch.return_value = observations
            mock_upsert.return_value = upsert_count_per_series

            result = await backfill_fred(dry_run=False)

        assert mock_fetch.call_count == len(FRED_SERIES)
        assert mock_upsert.call_count == len(FRED_SERIES)
        assert result == upsert_count_per_series * len(FRED_SERIES)

    @pytest.mark.asyncio
    async def test_skips_missing_dot_values(self) -> None:
        # 3 valid + 2 missing
        observations = _make_observations(count=3, with_missing=2)

        factory_mock, _ = _make_session_factory_mock()

        with (
            patch("src.config.settings") as mock_settings,
            patch("scripts.backfill_historical._fetch_fred_series", new_callable=AsyncMock) as mock_fetch,
            patch("src.database.crud.upsert_macro_data", new_callable=AsyncMock) as mock_upsert,
            patch("src.database.engine.async_session_factory", factory_mock),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_settings.FRED_KEY = "test_key"
            mock_fetch.return_value = observations
            mock_upsert.return_value = 3

            await backfill_fred(dry_run=False)

        # Verify upsert was called with exactly 3 records (not 5)
        called_records = mock_upsert.call_args_list[0][0][1]
        assert len(called_records) == 3
        for rec in called_records:
            assert rec["value"] != "."

    @pytest.mark.asyncio
    async def test_all_missing_observations_skips_upsert(self) -> None:
        all_missing = [{"date": "2023-01-01", "value": "."} for _ in range(5)]

        factory_mock, _ = _make_session_factory_mock()

        with (
            patch("src.config.settings") as mock_settings,
            patch("scripts.backfill_historical._fetch_fred_series", new_callable=AsyncMock) as mock_fetch,
            patch("src.database.crud.upsert_macro_data", new_callable=AsyncMock) as mock_upsert,
            patch("src.database.engine.async_session_factory", factory_mock),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_settings.FRED_KEY = "test_key"
            mock_fetch.return_value = all_missing

            result = await backfill_fred(dry_run=False)

        mock_upsert.assert_not_called()
        # result should be 0 (nothing upserted)
        assert result == 0

    @pytest.mark.asyncio
    async def test_http_error_continues_to_next_series(self) -> None:
        """HTTP errors on one series should not abort the rest."""
        import httpx

        good_observations = _make_observations(count=5)

        fetch_call_count = 0

        async def mock_fetch_side_effect(*args, **kwargs) -> list:
            nonlocal fetch_call_count
            fetch_call_count += 1
            if fetch_call_count == 1:
                # First series fails
                mock_resp = MagicMock()
                mock_resp.status_code = 429
                mock_resp.text = "Rate limit exceeded"
                raise httpx.HTTPStatusError("Rate limit", request=MagicMock(), response=mock_resp)
            return good_observations

        factory_mock, _ = _make_session_factory_mock()

        with (
            patch("src.config.settings") as mock_settings,
            patch("scripts.backfill_historical._fetch_fred_series", side_effect=mock_fetch_side_effect),
            patch("src.database.crud.upsert_macro_data", new_callable=AsyncMock) as mock_upsert,
            patch("src.database.engine.async_session_factory", factory_mock),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_settings.FRED_KEY = "test_key"
            mock_upsert.return_value = 5

            result = await backfill_fred(dry_run=False)

        # First series failed, remaining (len-1) succeeded
        expected_series_count = len(FRED_SERIES) - 1
        assert mock_upsert.call_count == expected_series_count
        assert result == 5 * expected_series_count

    @pytest.mark.asyncio
    async def test_rate_limit_sleep_called_between_series(self) -> None:
        factory_mock, _ = _make_session_factory_mock()

        with (
            patch("src.config.settings") as mock_settings,
            patch("scripts.backfill_historical._fetch_fred_series", new_callable=AsyncMock) as mock_fetch,
            patch("src.database.crud.upsert_macro_data", new_callable=AsyncMock) as mock_upsert,
            patch("src.database.engine.async_session_factory", factory_mock),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            mock_settings.FRED_KEY = "test_key"
            mock_fetch.return_value = []
            mock_upsert.return_value = 0

            await backfill_fred(dry_run=False)

        # Sleep called once per series
        assert mock_sleep.call_count == len(FRED_SERIES)
        # Each sleep uses the rate limit constant
        from scripts.backfill_historical import _FRED_RATE_LIMIT_SECONDS
        for call in mock_sleep.call_args_list:
            assert call[0][0] == _FRED_RATE_LIMIT_SECONDS

    @pytest.mark.asyncio
    async def test_fetch_fred_series_uses_correct_params(self) -> None:
        """_fetch_fred_series should pass correct params to FRED API."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"observations": [{"date": "2023-01-01", "value": "5.0"}]}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await _fetch_fred_series(
            client=mock_client,
            series_id="FEDFUNDS",
            api_key="test_key_123",
            observation_start="2000-01-01",
        )

        assert result == [{"date": "2023-01-01", "value": "5.0"}]
        call_kwargs = mock_client.get.call_args
        params = call_kwargs[1]["params"]
        assert params["series_id"] == "FEDFUNDS"
        assert params["api_key"] == "test_key_123"
        assert params["file_type"] == "json"
        assert params["observation_start"] == "2000-01-01"
        assert params["sort_order"] == "asc"
        assert params["limit"] == 10000

    @pytest.mark.asyncio
    async def test_idempotent_upsert_called_on_conflict(self) -> None:
        """Verify upsert_macro_data is called (it uses ON CONFLICT DO NOTHING for idempotency)."""
        observations = _make_observations(count=2)

        factory_mock, _ = _make_session_factory_mock()

        with (
            patch("src.config.settings") as mock_settings,
            patch("scripts.backfill_historical._fetch_fred_series", new_callable=AsyncMock) as mock_fetch,
            patch("src.database.crud.upsert_macro_data", new_callable=AsyncMock) as mock_upsert,
            patch("src.database.engine.async_session_factory", factory_mock),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_settings.FRED_KEY = "test_key"
            mock_fetch.return_value = observations
            mock_upsert.return_value = 2

            await backfill_fred(dry_run=False)
            # Call again to simulate re-run
            await backfill_fred(dry_run=False)

        # upsert_macro_data delegates idempotency to DB (ON CONFLICT DO NOTHING)
        # Just verify it was called both times
        assert mock_upsert.call_count == 2 * len(FRED_SERIES)
