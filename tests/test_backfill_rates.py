"""Tests for scripts/backfill_historical.py — backfill_rates().

All DB and HTTP interactions are mocked.
No real database or network access required.

Test naming: test_backfill_rates_{what_we_check}
"""

import datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_fred_obs(date: str, value: str) -> dict:
    return {"date": date, "value": value, "realtime_start": date, "realtime_end": "9999-12-31"}


def _make_ecb_jsondata(observations: dict[str, list], time_values: list[dict]) -> dict:
    """Build a minimal ECB SDMX-JSON response."""
    return {
        "dataSets": [
            {
                "series": {
                    "0:0:0:0:0:0:0": {
                        "observations": observations,
                    }
                }
            }
        ],
        "structure": {
            "dimensions": {
                "observation": [
                    {"values": time_values}
                ]
            }
        },
    }


# ── _parse_rate_date (aliased as _parse_date in tests) ───────────────────────


class TestParseDate:
    def test_iso_date(self) -> None:
        from scripts.backfill_historical import _parse_rate_date as _parse_date

        dt = _parse_date("2005-03-15")
        assert dt is not None
        assert dt.year == 2005
        assert dt.month == 3
        assert dt.day == 15
        assert dt.tzinfo is not None

    def test_year_month(self) -> None:
        from scripts.backfill_historical import _parse_rate_date as _parse_date

        dt = _parse_date("2010-06")
        assert dt is not None
        assert dt.year == 2010
        assert dt.month == 6
        assert dt.day == 1

    def test_missing_value_returns_none(self) -> None:
        from scripts.backfill_historical import _parse_rate_date as _parse_date

        assert _parse_date(".") is None
        assert _parse_date("") is None
        assert _parse_date(None) is None  # type: ignore[arg-type]

    def test_unparseable_returns_none(self) -> None:
        from scripts.backfill_historical import _parse_rate_date as _parse_date

        assert _parse_date("not-a-date") is None


# ── _fetch_rates_fred_series ──────────────────────────────────────────────────


class TestFetchFredSeries:
    @pytest.mark.asyncio
    async def test_returns_observations(self) -> None:
        from scripts.backfill_historical import _fetch_rates_fred_series as _fetch_fred_series

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "observations": [
                _make_fred_obs("2000-01-01", "5.45"),
                _make_fred_obs("2000-02-01", "5.73"),
                _make_fred_obs("2000-03-01", "."),  # missing — should be filtered
            ]
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await _fetch_fred_series(mock_client, "FEDFUNDS", "test_key", "2000-01-01")

        assert len(result) == 2
        assert result[0]["value"] == "5.45"
        assert result[1]["value"] == "5.73"

    @pytest.mark.asyncio
    async def test_http_400_returns_empty(self) -> None:
        import httpx

        from scripts.backfill_historical import _fetch_rates_fred_series as _fetch_fred_series

        mock_resp = MagicMock()
        mock_resp.status_code = 400

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "400 Bad Request",
                request=MagicMock(),
                response=mock_resp,
            )
        )

        result = await _fetch_fred_series(mock_client, "NONEXISTENT", "test_key", "2000-01-01")
        assert result == []

    @pytest.mark.asyncio
    async def test_network_error_returns_empty(self) -> None:
        import httpx

        from scripts.backfill_historical import _fetch_rates_fred_series as _fetch_fred_series

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )

        result = await _fetch_fred_series(mock_client, "FEDFUNDS", "test_key", "2000-01-01")
        assert result == []


# ── _fetch_ecb_history ────────────────────────────────────────────────────────


class TestFetchEcbHistory:
    @pytest.mark.asyncio
    async def test_parses_sdmx_json(self) -> None:
        from scripts.backfill_historical import _fetch_ecb_history

        ecb_data = _make_ecb_jsondata(
            observations={
                "0": [0.0],
                "1": [2.0],
                "2": [4.25],
            },
            time_values=[
                {"id": "2000-01"},
                {"id": "2005-01"},
                {"id": "2023-09"},
            ],
        )

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = ecb_data

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await _fetch_ecb_history(mock_client, "2000-01")

        assert len(result) == 3
        assert result[0]["date"] == "2000-01-01"
        assert result[0]["value"] == 0.0
        assert result[2]["value"] == 4.25

    @pytest.mark.asyncio
    async def test_network_error_returns_empty(self) -> None:
        import httpx

        from scripts.backfill_historical import _fetch_ecb_history

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )

        result = await _fetch_ecb_history(mock_client, "2000-01")
        assert result == []

    @pytest.mark.asyncio
    async def test_malformed_response_returns_empty(self) -> None:
        from scripts.backfill_historical import _fetch_ecb_history

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"unexpected": "format"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await _fetch_ecb_history(mock_client, "2000-01")
        assert result == []


# ── _upsert_rate_records ──────────────────────────────────────────────────────


class TestUpsertRateRecords:
    @pytest.mark.asyncio
    async def test_dry_run_does_not_write(self) -> None:
        from scripts.backfill_historical import _upsert_rate_records

        mock_session = AsyncMock()

        records = [
            {
                "bank": "FED",
                "currency": "USD",
                "rate": Decimal("5.25"),
                "effective_date": datetime.datetime(2023, 9, 1, tzinfo=datetime.timezone.utc),
                "source": "FRED/FEDFUNDS",
            }
        ]

        count = await _upsert_rate_records(mock_session, records, dry_run=True)

        assert count == 1
        mock_session.execute.assert_not_called()
        mock_session.flush.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_records_returns_zero(self) -> None:
        from scripts.backfill_historical import _upsert_rate_records

        mock_session = AsyncMock()
        count = await _upsert_rate_records(mock_session, [], dry_run=False)

        assert count == 0
        mock_session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_writes_records_to_db(self) -> None:
        from scripts.backfill_historical import _upsert_rate_records

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.flush = AsyncMock()

        records = [
            {
                "bank": "FED",
                "currency": "USD",
                "rate": Decimal("5.25"),
                "effective_date": datetime.datetime(2023, 9, 1, tzinfo=datetime.timezone.utc),
                "source": "FRED/FEDFUNDS",
            },
            {
                "bank": "FED",
                "currency": "USD",
                "rate": Decimal("5.50"),
                "effective_date": datetime.datetime(2023, 10, 1, tzinfo=datetime.timezone.utc),
                "source": "FRED/FEDFUNDS",
            },
        ]

        count = await _upsert_rate_records(mock_session, records, dry_run=False)

        assert count == 2
        assert mock_session.execute.call_count == 2
        mock_session.flush.assert_called_once()


# ── backfill_rates ────────────────────────────────────────────────────────────
#
# backfill_rates() returns int (total count across all banks), not dict.
# settings and async_session_factory are lazy-imported inside backfill_rates(),
# so patches target their canonical module paths:
#   - src.config.settings
#   - src.database.engine.async_session_factory


class TestBackfillRates:
    @pytest.mark.asyncio
    async def test_no_fred_key_skips_fred_banks(self) -> None:
        """Without FRED_KEY only ECB is attempted (via its own API)."""
        from scripts.backfill_historical import backfill_rates

        mock_settings = MagicMock()
        mock_settings.FRED_KEY = ""

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(return_value=mock_session)

        ecb_resp = MagicMock()
        ecb_resp.raise_for_status = MagicMock()
        ecb_resp.json.return_value = {"unexpected": "format"}

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=ecb_resp)

        with (
            patch("scripts.backfill_historical.httpx.AsyncClient", return_value=mock_http),
            patch("src.config.settings", mock_settings),
            patch("src.database.engine.async_session_factory", return_value=mock_session),
        ):
            total = await backfill_rates(banks=["FED", "ECB"], dry_run=True)

        # FED skipped because no FRED key; ECB attempted but returned 0 (malformed response)
        assert total == 0

    @pytest.mark.asyncio
    async def test_dry_run_returns_counts_without_db_writes(self) -> None:
        """dry_run=True counts records but never calls session.execute."""
        from scripts.backfill_historical import backfill_rates

        mock_settings = MagicMock()
        mock_settings.FRED_KEY = "test_fred_key"

        fred_obs = [_make_fred_obs("2000-01-01", "6.00"), _make_fred_obs("2000-02-01", "5.75")]
        fred_resp = MagicMock()
        fred_resp.raise_for_status = MagicMock()
        fred_resp.json.return_value = {"observations": fred_obs}

        ecb_resp = MagicMock()
        ecb_resp.raise_for_status = MagicMock()
        ecb_resp.json.return_value = {"unexpected": "format"}

        async def mock_get(url, **kwargs):
            if "ecb.europa.eu" in url:
                return ecb_resp
            return fred_resp

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(return_value=mock_session)
        mock_session.execute = AsyncMock()

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(side_effect=mock_get)

        with (
            patch("scripts.backfill_historical.httpx.AsyncClient", return_value=mock_http),
            patch("src.config.settings", mock_settings),
            patch("src.database.engine.async_session_factory", return_value=mock_session),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            total = await backfill_rates(banks=["FED"], start="2000-01-01", dry_run=True)

        # Dry run: 2 FED records counted, no DB writes
        assert total == 2
        mock_session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_bank_failure_does_not_stop_others(self) -> None:
        """If one bank's FRED call fails, remaining banks still processed."""
        from scripts.backfill_historical import backfill_rates

        mock_settings = MagicMock()
        mock_settings.FRED_KEY = "test_fred_key"

        import httpx as _httpx

        call_log: list[str] = []

        async def mock_get(url, params=None, **kwargs):
            series_id = (params or {}).get("series_id", "")
            call_log.append(series_id)
            if series_id == "FEDFUNDS":
                raise _httpx.ConnectError("simulated failure")
            # All other series succeed with minimal data
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {
                "observations": [_make_fred_obs("2000-01-01", "5.00")]
            }
            return resp

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(return_value=mock_session)
        mock_session.execute = AsyncMock()
        mock_session.flush = AsyncMock()

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(side_effect=mock_get)

        with (
            patch("scripts.backfill_historical.httpx.AsyncClient", return_value=mock_http),
            patch("src.config.settings", mock_settings),
            patch("src.database.engine.async_session_factory", return_value=mock_session),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            total = await backfill_rates(banks=["FED", "BOE"], start="2000-01-01", dry_run=False)

        # FED failed (0), BOE succeeded (1) — total = 1
        assert total == 1

    @pytest.mark.asyncio
    async def test_bank_filter_respected(self) -> None:
        """When banks list is provided, only those banks are processed."""
        from scripts.backfill_historical import backfill_rates

        mock_settings = MagicMock()
        mock_settings.FRED_KEY = "test_fred_key"

        called_series: list[str] = []

        async def mock_get(url, params=None, **kwargs):
            series_id = (params or {}).get("series_id", "")
            called_series.append(series_id)
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {
                "observations": [_make_fred_obs("2000-01-01", "5.00")]
            }
            return resp

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(return_value=mock_session)
        mock_session.execute = AsyncMock()
        mock_session.flush = AsyncMock()

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(side_effect=mock_get)

        with (
            patch("scripts.backfill_historical.httpx.AsyncClient", return_value=mock_http),
            patch("src.config.settings", mock_settings),
            patch("src.database.engine.async_session_factory", return_value=mock_session),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            total = await backfill_rates(banks=["FED"], start="2000-01-01", dry_run=True)

        # Only FED series called, not BOE/BOJ/BOC etc.
        assert "FEDFUNDS" in called_series
        assert "INTDSRGBM193N" not in called_series  # BOE series
        # FED processed and returned 1 record (dry_run)
        assert total == 1

    @pytest.mark.asyncio
    async def test_ecb_records_stored_with_correct_bank_currency(self) -> None:
        """ECB observations are stored with bank='ECB', currency='EUR'."""
        from scripts.backfill_historical import backfill_rates

        mock_settings = MagicMock()
        mock_settings.FRED_KEY = ""  # no FRED — only ECB runs

        ecb_data = _make_ecb_jsondata(
            observations={"0": [4.0], "1": [4.25]},
            time_values=[{"id": "2023-07"}, {"id": "2023-09"}],
        )
        ecb_resp = MagicMock()
        ecb_resp.raise_for_status = MagicMock()
        ecb_resp.json.return_value = ecb_data

        inserted_records: list[dict] = []

        async def fake_upsert(session, records, dry_run):
            inserted_records.extend(records)
            return len(records)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(return_value=mock_session)

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=ecb_resp)

        with (
            patch("scripts.backfill_historical.httpx.AsyncClient", return_value=mock_http),
            patch("src.config.settings", mock_settings),
            patch("src.database.engine.async_session_factory", return_value=mock_session),
            patch("scripts.backfill_historical._upsert_rate_records", side_effect=fake_upsert),
        ):
            total = await backfill_rates(banks=["ECB"], start="2000-01-01", dry_run=False)

        assert total == 2
        assert all(r["bank"] == "ECB" for r in inserted_records)
        assert all(r["currency"] == "EUR" for r in inserted_records)
        assert all(isinstance(r["rate"], Decimal) for r in inserted_records)
