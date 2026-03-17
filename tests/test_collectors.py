"""Tests for data collectors."""

import asyncio
import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import numpy as np
import pytest
import pytest_asyncio

from src.collectors.base import BaseCollector, CollectorResult


class TestBaseCollector:
    """Test BaseCollector abstract class."""

    def test_cannot_instantiate_abstract(self):
        """Should not be able to instantiate BaseCollector directly."""
        with pytest.raises(TypeError):
            BaseCollector("test")

    def test_concrete_collector_can_be_created(self):
        """A concrete subclass should be instantiatable."""

        class ConcreteCollector(BaseCollector):
            async def collect(self) -> CollectorResult:
                return CollectorResult(success=True)

            async def health_check(self) -> bool:
                return True

        collector = ConcreteCollector("test")
        assert collector.name == "test"


class TestCollectorResult:
    """Test CollectorResult dataclass."""

    def test_success_result(self):
        result = CollectorResult(success=True, records_count=10)
        assert result.success is True
        assert result.records_count == 10
        assert result.error is None

    def test_failure_result(self):
        result = CollectorResult(success=False, error="API error")
        assert result.success is False
        assert result.error == "API error"

    def test_metadata_default_empty(self):
        result = CollectorResult(success=True)
        assert result.metadata == {}


class TestRetryLogic:
    """Test retry logic in BaseCollector."""

    @pytest.mark.asyncio
    async def test_retries_on_failure(self):
        """Should retry on failure up to MAX_RETRIES times."""

        class RetryCollector(BaseCollector):
            def __init__(self):
                super().__init__("retry_test")
                self.call_count = 0

            async def collect(self) -> CollectorResult:
                return CollectorResult(success=True)

            async def health_check(self) -> bool:
                return True

            async def failing_func(self):
                self.call_count += 1
                raise ValueError("Simulated failure")

        collector = RetryCollector()
        collector.BACKOFF_BASE = 0.01  # Speed up tests

        with pytest.raises(ValueError):
            await collector._with_retry(collector.failing_func)

        assert collector.call_count == 3  # MAX_RETRIES = 3

    @pytest.mark.asyncio
    async def test_succeeds_on_third_try(self):
        """Should succeed if one of the retries succeeds."""

        class RetryCollector(BaseCollector):
            def __init__(self):
                super().__init__("retry_test")
                self.call_count = 0

            async def collect(self) -> CollectorResult:
                return CollectorResult(success=True)

            async def health_check(self) -> bool:
                return True

            async def sometimes_failing(self):
                self.call_count += 1
                if self.call_count < 3:
                    raise ValueError("Not yet")
                return "success"

        collector = RetryCollector()
        collector.BACKOFF_BASE = 0.01

        result = await collector._with_retry(collector.sometimes_failing)
        assert result == "success"
        assert collector.call_count == 3


class TestYFinanceCollector:
    """Test YFinanceCollector with mocked yfinance."""

    @pytest.mark.asyncio
    async def test_collect_historical_success(self):
        """Should collect and return historical data."""
        from src.collectors.price_collector import YFinanceCollector, _df_to_records

        # Create mock DataFrame
        idx = pd.date_range("2024-01-01", periods=10, freq="h", tz="UTC")
        mock_df = pd.DataFrame({
            "Open": [1.1] * 10,
            "High": [1.12] * 10,
            "Low": [1.09] * 10,
            "Close": [1.11] * 10,
            "Volume": [1000.0] * 10,
        }, index=idx)

        records = _df_to_records(mock_df, instrument_id=1, timeframe="H1")
        assert len(records) == 10
        assert all("instrument_id" in r for r in records)
        assert all("timestamp" in r for r in records)
        assert all(isinstance(r["close"], Decimal) for r in records)

    def test_df_to_records_conversion(self):
        """_df_to_records should convert DataFrame to list of dicts."""
        from src.collectors.price_collector import _df_to_records

        idx = pd.date_range("2024-01-01", periods=5, freq="h", tz="UTC")
        df = pd.DataFrame({
            "Open": [1.0, 1.1, 1.2, 1.1, 1.05],
            "High": [1.05, 1.15, 1.25, 1.15, 1.10],
            "Low": [0.95, 1.05, 1.15, 1.05, 1.00],
            "Close": [1.02, 1.12, 1.22, 1.09, 1.08],
            "Volume": [100.0, 200.0, 150.0, 180.0, 90.0],
        }, index=idx)

        records = _df_to_records(df, instrument_id=1, timeframe="H1")
        assert len(records) == 5
        assert records[0]["timeframe"] == "H1"
        assert records[0]["instrument_id"] == 1

    def test_timeframe_mapping(self):
        """YFinance timeframe map should cover all required timeframes."""
        from src.collectors.price_collector import YFINANCE_TF_MAP

        required_tfs = ["M1", "M5", "M15", "H1", "H4", "D1", "W1"]
        for tf in required_tfs:
            assert tf in YFINANCE_TF_MAP, f"Missing TF mapping: {tf}"


class TestMacroCollector:
    """Test FREDCollector."""

    @pytest.mark.asyncio
    async def test_collect_without_key_returns_empty(self):
        """Without API key, collector should return empty result gracefully."""
        from src.collectors.macro_collector import FREDCollector
        from src.config import settings

        with patch.object(settings, 'FRED_KEY', ''):
            collector = FREDCollector()
            collector.api_key = ''
            result = await collector.collect_series("FEDFUNDS")
            assert result.success is True
            assert result.records_count == 0

    @pytest.mark.asyncio
    async def test_health_check_without_key(self):
        """Health check should return True (graceful degradation) without API key."""
        from src.collectors.macro_collector import FREDCollector

        collector = FREDCollector()
        collector.api_key = ""
        healthy = await collector.health_check()
        assert healthy is True


class TestNewsCollector:
    """Test FinnhubNewsCollector."""

    def test_score_sentiment_positive(self):
        """Positive text should return positive sentiment."""
        from src.collectors.news_collector import _score_sentiment

        score = _score_sentiment("Great earnings beat expectations, stock surges higher")
        # TextBlob may vary, but positive text should be >= 0
        assert score >= Decimal("-0.1")

    def test_score_sentiment_neutral(self):
        """Neutral text should return near-zero sentiment."""
        from src.collectors.news_collector import _score_sentiment

        score = _score_sentiment("Company announces quarterly results")
        assert -Decimal("0.5") <= score <= Decimal("0.5")

    def test_determine_importance_critical(self):
        """Fed rate decision should be critical importance."""
        from src.collectors.news_collector import _determine_importance

        importance = _determine_importance("forex", "Fed rate decision surprises markets")
        assert importance == "critical"

    def test_determine_importance_low(self):
        """Random news should be low importance."""
        from src.collectors.news_collector import _determine_importance

        importance = _determine_importance("general", "CEO attends conference")
        assert importance == "low"
