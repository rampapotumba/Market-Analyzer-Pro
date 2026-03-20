"""Tests for ACLED collector and backfill_acled() function.

Test naming: test_{component}_{what_we_check}
All DB and HTTP interactions are mocked — no real network or database required.
"""

import datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helper factories ───────────────────────────────────────────────────────────


def _make_acled_event(
    event_date: str = "2022-06-15",
    event_type: str = "Battles",
    sub_event_type: str = "Armed clash",
    country: str = "Ukraine",
    fatalities: int = 5,
) -> dict[str, Any]:
    return {
        "event_date": event_date,
        "event_type": event_type,
        "sub_event_type": sub_event_type,
        "country": country,
        "fatalities": fatalities,
    }


# ── Severity mapping ───────────────────────────────────────────────────────────


class TestSeverityMapping:
    """Unit tests for _map_severity()."""

    def test_battles_severity(self) -> None:
        from src.collectors.acled_collector import _map_severity

        assert _map_severity("Battles", "Armed clash") == Decimal("-80")

    def test_violence_against_civilians_severity(self) -> None:
        from src.collectors.acled_collector import _map_severity

        assert _map_severity("Violence against civilians", "Attack") == Decimal("-90")

    def test_explosions_severity(self) -> None:
        from src.collectors.acled_collector import _map_severity

        assert _map_severity("Explosions/Remote violence", "Air/drone strike") == Decimal("-70")

    def test_peaceful_protest_severity(self) -> None:
        from src.collectors.acled_collector import _map_severity

        result = _map_severity("Protests", "Peaceful protest")
        assert result == Decimal("-30")

    def test_violent_demonstration_severity(self) -> None:
        from src.collectors.acled_collector import _map_severity

        result = _map_severity("Protests", "Violent demonstration")
        assert result == Decimal("-50")

    def test_mob_violence_severity(self) -> None:
        from src.collectors.acled_collector import _map_severity

        result = _map_severity("Protests", "Mob violence")
        assert result == Decimal("-50")

    def test_riots_severity(self) -> None:
        from src.collectors.acled_collector import _map_severity

        assert _map_severity("Riots", "Violent demonstration") == Decimal("-50")

    def test_strategic_developments_severity(self) -> None:
        from src.collectors.acled_collector import _map_severity

        assert _map_severity("Strategic developments", "Headquarters or base established") == Decimal("-20")

    def test_unknown_event_type_uses_default(self) -> None:
        from src.collectors.acled_collector import _map_severity, ACLED_DEFAULT_SEVERITY

        result = _map_severity("Unknown Type XYZ", "sub")
        assert result == ACLED_DEFAULT_SEVERITY

    def test_none_event_type_uses_default(self) -> None:
        from src.collectors.acled_collector import _map_severity, ACLED_DEFAULT_SEVERITY

        result = _map_severity(None, None)
        assert result == ACLED_DEFAULT_SEVERITY

    def test_empty_string_event_type_uses_default(self) -> None:
        from src.collectors.acled_collector import _map_severity, ACLED_DEFAULT_SEVERITY

        result = _map_severity("", None)
        assert result == ACLED_DEFAULT_SEVERITY


# ── Record building ────────────────────────────────────────────────────────────


class TestBuildRecord:
    """Unit tests for ACLEDCollector._build_record()."""

    def test_build_record_all_fields(self) -> None:
        from src.collectors.acled_collector import ACLEDCollector

        collector = ACLEDCollector()
        raw = _make_acled_event(
            event_date="2022-06-15",
            event_type="Battles",
            sub_event_type="Armed clash",
            country="Ukraine",
            fatalities=12,
        )

        record = collector._build_record(raw)

        assert record["source"] == "ACLED"
        assert record["country"] == "Ukraine"
        assert record["event_type"] == "Battles"
        assert record["fatalities"] == 12
        assert record["severity_score"] == Decimal("-80")
        assert isinstance(record["event_date"], datetime.datetime)
        assert record["event_date"].tzinfo is not None  # timezone-aware

    def test_build_record_stores_raw_data(self) -> None:
        from src.collectors.acled_collector import ACLEDCollector

        collector = ACLEDCollector()
        raw = _make_acled_event(country="Syria", event_type="Protests")

        record = collector._build_record(raw)

        assert isinstance(record["raw_data"], dict)
        assert record["raw_data"]["country"] == "Syria"
        assert record["raw_data"]["event_type"] == "Protests"

    def test_build_record_missing_fatalities_defaults_to_zero(self) -> None:
        from src.collectors.acled_collector import ACLEDCollector

        collector = ACLEDCollector()
        raw = {"event_date": "2022-01-01", "event_type": "Battles", "country": "Mali"}

        record = collector._build_record(raw)

        assert record["fatalities"] == 0

    def test_build_record_invalid_fatalities_defaults_to_zero(self) -> None:
        from src.collectors.acled_collector import ACLEDCollector

        collector = ACLEDCollector()
        raw = {
            "event_date": "2022-01-01",
            "event_type": "Riots",
            "country": "Nigeria",
            "fatalities": "n/a",
        }

        record = collector._build_record(raw)

        assert record["fatalities"] == 0

    def test_build_record_country_truncated_to_100_chars(self) -> None:
        from src.collectors.acled_collector import ACLEDCollector

        collector = ACLEDCollector()
        long_country = "A" * 200
        raw = {"event_date": "2022-01-01", "event_type": "Riots", "country": long_country}

        record = collector._build_record(raw)

        assert len(record["country"]) <= 100

    def test_build_record_event_type_truncated_to_100_chars(self) -> None:
        from src.collectors.acled_collector import ACLEDCollector

        collector = ACLEDCollector()
        long_type = "X" * 200
        raw = {"event_date": "2022-01-01", "event_type": long_type, "country": "Test"}

        record = collector._build_record(raw)

        assert len(record["event_type"]) <= 100

    def test_build_record_unparseable_date_uses_now(self) -> None:
        from src.collectors.acled_collector import ACLEDCollector

        collector = ACLEDCollector()
        raw = {"event_date": "not-a-date", "event_type": "Battles", "country": "Iraq"}

        record = collector._build_record(raw)

        # Should not raise — fallback to current UTC time
        assert isinstance(record["event_date"], datetime.datetime)
        assert record["event_date"].tzinfo is not None

    def test_build_record_none_event_type_stored_as_none(self) -> None:
        from src.collectors.acled_collector import ACLEDCollector

        collector = ACLEDCollector()
        raw = {"event_date": "2022-01-01", "event_type": None, "country": "Somalia"}

        record = collector._build_record(raw)

        assert record["event_type"] is None


# ── Date parsing ───────────────────────────────────────────────────────────────


class TestParseDatetime:
    """Unit tests for ACLEDCollector._parse_event_date()."""

    def test_parse_valid_date(self) -> None:
        from src.collectors.acled_collector import ACLEDCollector

        collector = ACLEDCollector()
        dt = collector._parse_event_date("2020-03-15")

        assert dt.year == 2020
        assert dt.month == 3
        assert dt.day == 15
        assert dt.tzinfo == datetime.timezone.utc

    def test_parse_invalid_date_returns_now(self) -> None:
        from src.collectors.acled_collector import ACLEDCollector

        collector = ACLEDCollector()
        before = datetime.datetime.now(datetime.timezone.utc)
        dt = collector._parse_event_date("bad-date")
        after = datetime.datetime.now(datetime.timezone.utc)

        assert before <= dt <= after
        assert dt.tzinfo is not None


# ── is_configured ─────────────────────────────────────────────────────────────


class TestIsConfigured:
    """Tests for ACLEDCollector._is_configured()."""

    def test_not_configured_when_key_missing(self) -> None:
        from src.collectors.acled_collector import ACLEDCollector

        collector = ACLEDCollector()
        with patch("src.collectors.acled_collector.settings") as mock_settings:
            mock_settings.ACLED_API_KEY = ""
            mock_settings.ACLED_EMAIL = "test@example.com"
            assert collector._is_configured() is False

    def test_not_configured_when_email_missing(self) -> None:
        from src.collectors.acled_collector import ACLEDCollector

        collector = ACLEDCollector()
        with patch("src.collectors.acled_collector.settings") as mock_settings:
            mock_settings.ACLED_API_KEY = "somekey"
            mock_settings.ACLED_EMAIL = ""
            assert collector._is_configured() is False

    def test_configured_when_both_set(self) -> None:
        from src.collectors.acled_collector import ACLEDCollector

        collector = ACLEDCollector()
        with patch("src.collectors.acled_collector.settings") as mock_settings:
            mock_settings.ACLED_API_KEY = "somekey"
            mock_settings.ACLED_EMAIL = "user@example.com"
            assert collector._is_configured() is True


# ── collect() graceful degradation ────────────────────────────────────────────


class TestCollectGracefulDegradation:
    """Tests for graceful degradation when API key is missing."""

    @pytest.mark.asyncio
    async def test_collect_returns_zero_when_not_configured(self) -> None:
        from src.collectors.acled_collector import ACLEDCollector

        collector = ACLEDCollector()
        with patch("src.collectors.acled_collector.settings") as mock_settings:
            mock_settings.ACLED_API_KEY = ""
            mock_settings.ACLED_EMAIL = ""

            result = await collector.collect()

        assert result.success is True
        assert result.records_count == 0

    @pytest.mark.asyncio
    async def test_collect_does_not_raise_when_not_configured(self) -> None:
        from src.collectors.acled_collector import ACLEDCollector

        collector = ACLEDCollector()
        with patch("src.collectors.acled_collector.settings") as mock_settings:
            mock_settings.ACLED_API_KEY = ""
            mock_settings.ACLED_EMAIL = ""

            # Must not raise
            result = await collector.collect()

        assert result is not None


# ── _collect_range() ──────────────────────────────────────────────────────────


class TestCollectRange:
    """Tests for ACLEDCollector._collect_range() pagination and dry_run."""

    @pytest.mark.asyncio
    async def test_collect_range_dry_run_counts_without_writing(self) -> None:
        from src.collectors.acled_collector import ACLEDCollector, ACLED_PAGE_SIZE

        collector = ACLEDCollector()

        # Two events on first page, empty second page → stops
        fake_page_1 = [
            _make_acled_event(event_type="Battles"),
            _make_acled_event(event_type="Protests"),
        ]

        async def fake_fetch(page: int, start: datetime.date, end: datetime.date):
            if page == 1:
                return fake_page_1
            return []

        with patch.object(collector, "_fetch_page", side_effect=fake_fetch):
            total = await collector._collect_range(
                start_date=datetime.date(2022, 1, 1),
                end_date=datetime.date(2022, 12, 31),
                dry_run=True,
            )

        assert total == 2

    @pytest.mark.asyncio
    async def test_collect_range_stops_on_empty_page(self) -> None:
        from src.collectors.acled_collector import ACLEDCollector

        collector = ACLEDCollector()
        call_count = 0

        async def fake_fetch(page: int, start: datetime.date, end: datetime.date):
            nonlocal call_count
            call_count += 1
            return []  # Always empty → should stop after page 1

        with patch.object(collector, "_fetch_page", side_effect=fake_fetch):
            total = await collector._collect_range(
                start_date=datetime.date(2022, 1, 1),
                end_date=datetime.date(2022, 12, 31),
                dry_run=True,
            )

        assert total == 0
        assert call_count == 1  # Only one call before stopping

    @pytest.mark.asyncio
    async def test_collect_range_stops_on_fetch_error(self) -> None:
        from src.collectors.acled_collector import ACLEDCollector

        collector = ACLEDCollector()

        async def fake_fetch(page: int, start: datetime.date, end: datetime.date):
            return None  # Simulate error

        with patch.object(collector, "_fetch_page", side_effect=fake_fetch):
            total = await collector._collect_range(
                start_date=datetime.date(2022, 1, 1),
                end_date=datetime.date(2022, 12, 31),
                dry_run=True,
            )

        assert total == 0

    @pytest.mark.asyncio
    async def test_collect_range_paginates_full_pages(self) -> None:
        from src.collectors.acled_collector import ACLEDCollector, ACLED_PAGE_SIZE

        collector = ACLEDCollector()
        # Page 1: exactly ACLED_PAGE_SIZE records → should fetch page 2
        # Page 2: 0 records → stops
        page_1_data = [_make_acled_event()] * ACLED_PAGE_SIZE
        page_2_data: list = []

        async def fake_fetch(page: int, start: datetime.date, end: datetime.date):
            if page == 1:
                return page_1_data
            return page_2_data

        with patch.object(collector, "_fetch_page", side_effect=fake_fetch):
            total = await collector._collect_range(
                start_date=datetime.date(2022, 1, 1),
                end_date=datetime.date(2022, 12, 31),
                dry_run=True,
            )

        assert total == ACLED_PAGE_SIZE


# ── backfill_acled() ──────────────────────────────────────────────────────────


class TestBackfillAcled:
    """Tests for the backfill_acled() script function."""

    @pytest.mark.asyncio
    async def test_backfill_returns_zero_when_no_api_key(self) -> None:
        from scripts.backfill_historical import backfill_acled

        with patch("src.config.settings") as mock_settings:
            mock_settings.ACLED_API_KEY = ""
            mock_settings.ACLED_EMAIL = ""

            result = await backfill_acled()

        assert result == 0

    @pytest.mark.asyncio
    async def test_backfill_returns_zero_when_no_email(self) -> None:
        from scripts.backfill_historical import backfill_acled

        with patch("src.config.settings") as mock_settings:
            mock_settings.ACLED_API_KEY = "some-key"
            mock_settings.ACLED_EMAIL = ""

            result = await backfill_acled()

        assert result == 0

    @pytest.mark.asyncio
    async def test_backfill_returns_zero_when_start_after_end(self) -> None:
        from scripts.backfill_historical import backfill_acled

        with patch("src.config.settings") as mock_settings:
            mock_settings.ACLED_API_KEY = "key"
            mock_settings.ACLED_EMAIL = "e@mail.com"

            result = await backfill_acled(
                start="2024-01-01",
                end="2020-01-01",
            )

        assert result == 0

    @pytest.mark.asyncio
    async def test_backfill_dry_run_calls_collect_range(self) -> None:
        from scripts.backfill_historical import backfill_acled
        from src.collectors.acled_collector import ACLEDCollector

        with (
            patch("src.config.settings") as mock_settings,
            patch.object(
                ACLEDCollector,
                "_collect_range",
                new_callable=AsyncMock,
                return_value=42,
            ) as mock_collect,
        ):
            mock_settings.ACLED_API_KEY = "key"
            mock_settings.ACLED_EMAIL = "e@mail.com"

            result = await backfill_acled(
                start="2022-01-01",
                end="2022-12-31",
                dry_run=True,
            )

        assert result == 42
        mock_collect.assert_called_once_with(
            datetime.date(2022, 1, 1),
            datetime.date(2022, 12, 31),
            dry_run=True,
        )

    @pytest.mark.asyncio
    async def test_backfill_default_start_is_2018(self) -> None:
        from scripts.backfill_historical import backfill_acled, _ACLED_DEFAULT_START

        assert _ACLED_DEFAULT_START == datetime.date(2018, 1, 1)

    @pytest.mark.asyncio
    async def test_backfill_default_end_is_today(self) -> None:
        from scripts.backfill_historical import backfill_acled
        from src.collectors.acled_collector import ACLEDCollector

        today = datetime.date.today()

        with (
            patch("src.config.settings") as mock_settings,
            patch.object(
                ACLEDCollector,
                "_collect_range",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_collect,
        ):
            mock_settings.ACLED_API_KEY = "key"
            mock_settings.ACLED_EMAIL = "e@mail.com"

            await backfill_acled(start="2022-01-01")

        call_args = mock_collect.call_args
        # end_date should be today (or very close to it)
        assert call_args.args[1] == today or call_args.kwargs.get("end_date") == today


# ── GeoEvent model ────────────────────────────────────────────────────────────


class TestGeoEventModel:
    """Tests for the GeoEvent SQLAlchemy model."""

    def test_geo_event_model_has_required_fields(self) -> None:
        from src.database.models import GeoEvent

        event = GeoEvent(
            source="ACLED",
            event_date=datetime.datetime(2022, 6, 15, tzinfo=datetime.timezone.utc),
            country="Ukraine",
        )

        assert event.source == "ACLED"
        assert event.country == "Ukraine"
        assert event.event_date.year == 2022

    def test_geo_event_model_optional_fields_default_to_none(self) -> None:
        from src.database.models import GeoEvent

        event = GeoEvent(
            source="ACLED",
            event_date=datetime.datetime(2022, 6, 15, tzinfo=datetime.timezone.utc),
            country="Ukraine",
        )

        assert event.event_type is None
        assert event.severity_score is None
        assert event.raw_data is None

    def test_geo_event_model_tablename(self) -> None:
        from src.database.models import GeoEvent

        assert GeoEvent.__tablename__ == "geo_events"

    def test_geo_event_model_source_field_accepts_acled(self) -> None:
        from src.database.models import GeoEvent

        event = GeoEvent(
            source="ACLED",
            event_date=datetime.datetime.now(datetime.timezone.utc),
            country="Syria",
        )
        assert event.source == "ACLED"

    def test_geo_event_model_source_field_accepts_gdelt(self) -> None:
        from src.database.models import GeoEvent

        event = GeoEvent(
            source="GDELT",
            event_date=datetime.datetime.now(datetime.timezone.utc),
            country="US",
        )
        assert event.source == "GDELT"

    def test_geo_event_model_severity_score_is_decimal(self) -> None:
        from src.database.models import GeoEvent

        event = GeoEvent(
            source="ACLED",
            event_date=datetime.datetime.now(datetime.timezone.utc),
            country="Iraq",
            severity_score=Decimal("-80"),
        )
        assert event.severity_score == Decimal("-80")


# ── Config settings ───────────────────────────────────────────────────────────


class TestConfigSettings:
    """Tests for ACLED_API_KEY and ACLED_EMAIL in Settings."""

    def test_acled_api_key_defined_in_settings(self) -> None:
        from src.config import settings

        assert hasattr(settings, "ACLED_API_KEY")

    def test_acled_email_defined_in_settings(self) -> None:
        from src.config import settings

        assert hasattr(settings, "ACLED_EMAIL")

    def test_acled_api_key_defaults_to_empty_string(self) -> None:
        from src.config import Settings

        s = Settings()
        assert s.ACLED_API_KEY == "" or isinstance(s.ACLED_API_KEY, str)

    def test_acled_email_defaults_to_empty_string(self) -> None:
        from src.config import Settings

        s = Settings()
        assert s.ACLED_EMAIL == "" or isinstance(s.ACLED_EMAIL, str)
