"""Tests for src.utils.data_quality."""

import datetime

import numpy as np
import pandas as pd
import pytest

from src.utils.data_quality import DataQualityMonitor, DataQualityReport


def _make_ohlcv(n: int = 50, start: str = "2026-01-01") -> pd.DataFrame:
    """Create a clean OHLCV DataFrame with timestamps."""
    idx = pd.date_range(start, periods=n, freq="h", tz="UTC")
    prices = 100.0 + np.cumsum(np.random.randn(n) * 0.1)
    df = pd.DataFrame(
        {
            "timestamp": idx,
            "open": prices * 0.999,
            "high": prices * 1.003,
            "low": prices * 0.997,
            "close": prices,
            "volume": np.abs(np.random.randn(n)) * 1000 + 500,
        }
    )
    return df


class TestDataQualityOHLCV:
    def test_clean_data_no_issues(self):
        monitor = DataQualityMonitor()
        df = _make_ohlcv(50)
        # Fix reference time so no staleness
        now = pd.Timestamp("2026-01-03", tz="UTC").to_pydatetime()
        report = monitor.check(df, "EURUSD", "H1", now=now)
        assert report.is_clean, report.issues

    def test_empty_dataframe(self):
        monitor = DataQualityMonitor()
        report = monitor.check(pd.DataFrame(), "EURUSD", "H1")
        assert report.has_errors

    def test_missing_columns(self):
        monitor = DataQualityMonitor()
        df = pd.DataFrame({"open": [1.0], "close": [1.0]})
        report = monitor.check(df, "EURUSD", "H1")
        assert report.has_errors
        kinds = [i.kind for i in report.issues]
        assert "ohlcv" in kinds

    def test_high_less_than_low(self):
        monitor = DataQualityMonitor()
        df = _make_ohlcv(10)
        df.loc[5, "high"] = df.loc[5, "low"] - 0.001
        report = monitor.check(df, "EURUSD", "H1")
        ohlcv_errors = [i for i in report.issues if i.kind == "ohlcv" and i.severity == "error"]
        assert len(ohlcv_errors) > 0

    def test_open_outside_range(self):
        monitor = DataQualityMonitor()
        df = _make_ohlcv(10)
        df.loc[3, "open"] = df.loc[3, "high"] + 1.0
        report = monitor.check(df, "EURUSD", "H1")
        kinds = [i.kind for i in report.issues]
        assert "ohlcv" in kinds

    def test_negative_volume(self):
        monitor = DataQualityMonitor()
        df = _make_ohlcv(10)
        df.loc[2, "volume"] = -100
        report = monitor.check(df, "EURUSD", "H1")
        kinds = [i.kind for i in report.issues]
        assert "ohlcv" in kinds


class TestDataQualityGaps:
    def test_detects_timestamp_gap(self):
        monitor = DataQualityMonitor()
        df = _make_ohlcv(20)
        # Drop rows 8-13 to create a ~6h gap in an H1 series (max allowed = 3h)
        df = df.drop(index=list(range(8, 14))).reset_index(drop=True)
        last_ts = df["timestamp"].max()
        now = (last_ts + pd.Timedelta(hours=1)).to_pydatetime()
        report = monitor.check(df, "EURUSD", "H1", now=now)
        gap_issues = [i for i in report.issues if i.kind == "gap"]
        assert len(gap_issues) > 0

    def test_no_gap_within_tolerance(self):
        monitor = DataQualityMonitor()
        df = _make_ohlcv(20)
        now = pd.Timestamp("2026-01-03", tz="UTC").to_pydatetime()
        report = monitor.check(df, "EURUSD", "H1", now=now)
        gap_issues = [i for i in report.issues if i.kind == "gap"]
        assert len(gap_issues) == 0


class TestDataQualityStaleness:
    def test_detects_stale_data(self):
        monitor = DataQualityMonitor()
        df = _make_ohlcv(10, start="2025-01-01")  # very old data
        now = datetime.datetime(2026, 3, 17, tzinfo=datetime.timezone.utc)
        report = monitor.check(df, "EURUSD", "H1", now=now)
        stale_issues = [i for i in report.issues if i.kind == "stale"]
        assert len(stale_issues) > 0

    def test_fresh_data_not_stale(self):
        monitor = DataQualityMonitor()
        df = _make_ohlcv(10)
        # now = 2 hours after the last bar → within tolerance
        last_ts = df["timestamp"].max()
        now = (last_ts + pd.Timedelta(hours=2)).to_pydatetime()
        report = monitor.check(df, "EURUSD", "H1", now=now)
        stale_issues = [i for i in report.issues if i.kind == "stale"]
        assert len(stale_issues) == 0


class TestDataQualityFlat:
    def test_detects_flat_price(self):
        monitor = DataQualityMonitor()
        df = _make_ohlcv(30)
        # Last 20 bars all same close
        df.loc[10:, "close"] = 100.0
        now = pd.Timestamp("2026-01-05", tz="UTC").to_pydatetime()
        report = monitor.check(df, "EURUSD", "H1", now=now)
        flat_issues = [i for i in report.issues if i.kind == "flat"]
        assert len(flat_issues) > 0

    def test_normal_prices_not_flat(self):
        monitor = DataQualityMonitor()
        df = _make_ohlcv(30)
        now = pd.Timestamp("2026-01-03", tz="UTC").to_pydatetime()
        report = monitor.check(df, "EURUSD", "H1", now=now)
        flat_issues = [i for i in report.issues if i.kind == "flat"]
        assert len(flat_issues) == 0


class TestDataQualityOutliers:
    def test_detects_outlier(self):
        monitor = DataQualityMonitor()
        df = _make_ohlcv(50)
        # Inject a massive price spike
        df.loc[25, "close"] = df.loc[25, "close"] * 10
        now = pd.Timestamp("2026-01-04", tz="UTC").to_pydatetime()
        report = monitor.check(df, "EURUSD", "H1", now=now)
        outlier_issues = [i for i in report.issues if i.kind == "outlier"]
        assert len(outlier_issues) > 0

    def test_normal_returns_no_outlier(self):
        monitor = DataQualityMonitor()
        df = _make_ohlcv(50)
        now = pd.Timestamp("2026-01-04", tz="UTC").to_pydatetime()
        report = monitor.check(df, "EURUSD", "H1", now=now)
        outlier_issues = [i for i in report.issues if i.kind == "outlier"]
        assert len(outlier_issues) == 0


class TestDataQualityReport:
    def test_summary_structure(self):
        report = DataQualityReport("BTCUSDT", "H4")
        report.add("ohlcv", "warning", "Some issue")
        s = report.summary()
        assert s["symbol"] == "BTCUSDT"
        assert s["clean"] is False
        assert len(s["issues"]) == 1

    def test_has_errors_false_for_warnings(self):
        report = DataQualityReport("EURUSD", "H1")
        report.add("gap", "warning", "Small gap")
        assert report.has_errors is False
        assert report.is_clean is False

    def test_has_errors_true_for_errors(self):
        report = DataQualityReport("EURUSD", "H1")
        report.add("ohlcv", "error", "Bad data")
        assert report.has_errors is True
