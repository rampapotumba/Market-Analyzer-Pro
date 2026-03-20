"""Trade Simulator v7 tests — TASK-V7-02.

Test naming: test_v7_02_{what_we_check}
All DB interactions are mocked — no real database required.
"""

import datetime
import inspect
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest


# ── TASK-V7-02: FRED collector fetch limit + FA engine delta ─────────────────


class TestV702FredFetchLimit:
    """TASK-V7-02: FREDCollector._fetch_series uses limit=60 by default."""

    def test_v7_02_fred_fetch_limit(self) -> None:
        """_fetch_series default limit parameter must be 60 (5 years monthly)."""
        from src.collectors.macro_collector import FREDCollector

        sig = inspect.signature(FREDCollector._fetch_series)
        default_limit = sig.parameters["limit"].default
        assert default_limit == 60, (
            f"Expected _fetch_series default limit=60, got {default_limit}"
        )


class TestV702FaEngineDeltaWithHistory:
    """TASK-V7-02: FAEngine._delta() returns non-None when 2+ observations exist."""

    def _make_macro_record(
        self,
        indicator_name: str,
        value: str,
        release_date: datetime.datetime,
    ) -> MagicMock:
        record = MagicMock()
        record.indicator_name = indicator_name
        record.value = Decimal(value)
        record.release_date = release_date
        return record

    def _make_instrument(self, market: str = "forex", symbol: str = "EURUSD=X") -> MagicMock:
        instrument = MagicMock()
        instrument.market = market
        instrument.symbol = symbol
        return instrument

    def test_v7_02_fa_engine_delta_with_history(self) -> None:
        """FAEngine._delta() is non-None when 2 observations are provided per indicator."""
        from src.analysis.fa_engine import FAEngine

        now = datetime.datetime(2025, 3, 1, tzinfo=datetime.timezone.utc)
        month_ago = datetime.datetime(2025, 2, 1, tzinfo=datetime.timezone.utc)

        macro_data = [
            # FEDFUNDS: latest first (sorted DESC by release_date as returned by FRED)
            self._make_macro_record("FEDFUNDS", "5.33", now),
            self._make_macro_record("FEDFUNDS", "5.08", month_ago),
            # CPIAUCSL
            self._make_macro_record("CPIAUCSL", "310.50", now),
            self._make_macro_record("CPIAUCSL", "309.80", month_ago),
            # UNRATE
            self._make_macro_record("UNRATE", "3.9", now),
            self._make_macro_record("UNRATE", "4.1", month_ago),
            # GDPC1
            self._make_macro_record("GDPC1", "22000.0", now),
            self._make_macro_record("GDPC1", "21800.0", month_ago),
        ]

        instrument = self._make_instrument(market="forex", symbol="EURUSD=X")
        engine = FAEngine(instrument, macro_data, [])

        # All deltas must be computable with 2 observations
        assert engine._delta("FEDFUNDS") is not None
        assert engine._delta("CPIAUCSL") is not None
        assert engine._delta("UNRATE") is not None
        assert engine._delta("GDPC1") is not None

        # Verify actual delta values
        assert abs(engine._delta("FEDFUNDS") - (5.33 - 5.08)) < 1e-9  # type: ignore[operator]
        assert abs(engine._delta("UNRATE") - (3.9 - 4.1)) < 1e-9     # type: ignore[operator]

    def test_v7_02_fa_engine_delta_none_when_single_observation(self) -> None:
        """FAEngine._delta() returns None when only 1 observation per indicator."""
        from src.analysis.fa_engine import FAEngine

        now = datetime.datetime(2025, 3, 1, tzinfo=datetime.timezone.utc)
        macro_data = [
            self._make_macro_record("FEDFUNDS", "5.33", now),
        ]

        instrument = self._make_instrument(market="forex", symbol="EURUSD=X")
        engine = FAEngine(instrument, macro_data, [])

        assert engine._delta("FEDFUNDS") is None

    def test_v7_02_fa_score_nonzero_with_two_observations(self) -> None:
        """FAEngine.calculate_fa_score() returns non-zero when deltas are available."""
        from src.analysis.fa_engine import FAEngine

        now = datetime.datetime(2025, 3, 1, tzinfo=datetime.timezone.utc)
        month_ago = datetime.datetime(2025, 2, 1, tzinfo=datetime.timezone.utc)

        # Create meaningful delta: FEDFUNDS increased (USD stronger → forex bearish)
        macro_data = [
            self._make_macro_record("FEDFUNDS", "5.50", now),
            self._make_macro_record("FEDFUNDS", "5.00", month_ago),
        ]

        instrument = self._make_instrument(market="forex", symbol="EURUSD=X")
        engine = FAEngine(instrument, macro_data, [])

        score = engine.calculate_fa_score()
        # FEDFUNDS increased by 0.5 → score -= 0.5 * 10 = -5 → non-zero
        assert score != 0.0, f"Expected non-zero FA score, got {score}"


class TestV702NewFredSeriesDefined:
    """TASK-V7-02: New FRED series DFF, T10Y2Y, DTWEXBGS, UMCSENT are defined."""

    def test_v7_02_new_fred_series_defined(self) -> None:
        """DFF, T10Y2Y, DTWEXBGS, UMCSENT must be present in FRED_SERIES dict."""
        from src.collectors.macro_collector import FRED_SERIES

        required_series = {"DFF", "T10Y2Y", "DTWEXBGS", "UMCSENT"}
        missing = required_series - set(FRED_SERIES.keys())
        assert not missing, f"Missing FRED series: {missing}"

    def test_v7_02_new_fred_series_have_name_and_country(self) -> None:
        """Each new FRED series must have 'name' and 'country' fields."""
        from src.collectors.macro_collector import FRED_SERIES

        new_series = ["DFF", "T10Y2Y", "DTWEXBGS", "UMCSENT"]
        for series_id in new_series:
            meta = FRED_SERIES[series_id]
            assert "name" in meta, f"{series_id} missing 'name'"
            assert "country" in meta, f"{series_id} missing 'country'"
            assert meta["name"], f"{series_id} has empty 'name'"
            assert meta["country"] == "US", f"{series_id} expected country 'US'"

    def test_v7_02_existing_fred_series_still_present(self) -> None:
        """Original FRED series must not have been removed."""
        from src.collectors.macro_collector import FRED_SERIES

        original_series = {
            "FEDFUNDS", "CPIAUCSL", "UNRATE", "GDPC1",
            "PAYEMS", "INDPRO", "RETAILSMNSA", "HOUST",
        }
        missing = original_series - set(FRED_SERIES.keys())
        assert not missing, f"Original FRED series removed: {missing}"
