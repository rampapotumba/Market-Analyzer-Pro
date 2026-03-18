"""Data Quality Monitor.

Validates incoming OHLCV and indicator data before processing.
Issues warnings (and optionally raises) on quality violations.

Checks:
  - OHLCV sanity: high >= low, open/close within [low, high], volume ≥ 0
  - Gap detection: timestamp gaps larger than expected timeframe interval
  - Stale data: last bar is too old
  - Flat price detection: all closes identical (feed stuck)
  - Extreme outliers: close change > N standard deviations
"""

import datetime
import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Gap thresholds: expected max gap per timeframe (minutes)
_MAX_GAP_MINUTES: dict[str, int] = {
    "M1": 5,
    "M5": 20,
    "M15": 60,
    "H1": 180,
    "H4": 720,
    "D1": 2880,
}

# Stale data thresholds (how many intervals since last bar is "stale")
_STALE_INTERVALS = 3

# Outlier threshold in standard deviations
_OUTLIER_ZSCORE = 5.0


class DataQualityIssue:
    """Describes a single data quality problem."""

    __slots__ = ("kind", "severity", "message")

    def __init__(self, kind: str, severity: str, message: str) -> None:
        self.kind = kind          # ohlcv / gap / stale / flat / outlier
        self.severity = severity  # warning / error
        self.message = message

    def __repr__(self) -> str:
        return f"DataQualityIssue({self.severity}: {self.kind} — {self.message})"


class DataQualityReport:
    """Collection of quality issues for a single dataset."""

    def __init__(self, symbol: str, timeframe: str) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.issues: list[DataQualityIssue] = []

    def add(self, kind: str, severity: str, message: str) -> None:
        issue = DataQualityIssue(kind, severity, message)
        self.issues.append(issue)
        log_fn = logger.warning if severity == "warning" else logger.error
        log_fn("[DQ:%s/%s] %s: %s", self.symbol, self.timeframe, kind, message)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    @property
    def is_clean(self) -> bool:
        return len(self.issues) == 0

    def summary(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "clean": self.is_clean,
            "issues": [
                {"kind": i.kind, "severity": i.severity, "message": i.message}
                for i in self.issues
            ],
        }


class DataQualityMonitor:
    """
    Validates a pandas OHLCV DataFrame.

    Expected DataFrame columns: open, high, low, close, volume
    Expected index or 'timestamp' column: datetime (UTC)
    """

    def check(
        self,
        df: pd.DataFrame,
        symbol: str = "UNKNOWN",
        timeframe: str = "H1",
        now: Optional[datetime.datetime] = None,
    ) -> DataQualityReport:
        report = DataQualityReport(symbol, timeframe)

        if df.empty:
            report.add("empty", "error", "DataFrame is empty")
            return report

        self._check_ohlcv(df, report)
        self._check_gaps(df, timeframe, report)
        self._check_staleness(df, timeframe, report, now)
        self._check_flat_price(df, report)
        self._check_outliers(df, report)

        return report

    # ── OHLCV sanity ──────────────────────────────────────────────────────────

    def _check_ohlcv(self, df: pd.DataFrame, report: DataQualityReport) -> None:
        cols = {"open", "high", "low", "close", "volume"}
        missing = cols - set(df.columns)
        if missing:
            report.add("ohlcv", "error", f"Missing columns: {missing}")
            return

        # high >= low
        bad_hl = (df["high"] < df["low"]).sum()
        if bad_hl > 0:
            report.add("ohlcv", "error", f"{bad_hl} rows where high < low")

        # open and close within [low, high]
        bad_open = ((df["open"] < df["low"]) | (df["open"] > df["high"])).sum()
        bad_close = ((df["close"] < df["low"]) | (df["close"] > df["high"])).sum()
        if bad_open > 0:
            report.add("ohlcv", "warning", f"{bad_open} rows where open ∉ [low, high]")
        if bad_close > 0:
            report.add("ohlcv", "warning", f"{bad_close} rows where close ∉ [low, high]")

        # negative volume
        if "volume" in df.columns:
            bad_vol = (df["volume"] < 0).sum()
            if bad_vol > 0:
                report.add("ohlcv", "warning", f"{bad_vol} rows with negative volume")

    # ── Gap detection ─────────────────────────────────────────────────────────

    def _check_gaps(
        self, df: pd.DataFrame, timeframe: str, report: DataQualityReport
    ) -> None:
        ts = self._get_timestamps(df)
        if ts is None or len(ts) < 2:
            return

        ts_sorted = ts.sort_values()
        deltas = ts_sorted.diff().dropna()
        max_allowed = pd.Timedelta(
            minutes=_MAX_GAP_MINUTES.get(timeframe, 180)
        )
        bad_gaps = deltas[deltas > max_allowed]
        if not bad_gaps.empty:
            report.add(
                "gap",
                "warning",
                f"{len(bad_gaps)} timestamp gaps exceed {max_allowed} "
                f"(largest: {bad_gaps.max()})",
            )

    # ── Staleness ─────────────────────────────────────────────────────────────

    def _check_staleness(
        self,
        df: pd.DataFrame,
        timeframe: str,
        report: DataQualityReport,
        now: Optional[datetime.datetime],
    ) -> None:
        ts = self._get_timestamps(df)
        if ts is None or ts.empty:
            return

        last_ts = ts.max()
        if not isinstance(last_ts, datetime.datetime):
            try:
                last_ts = pd.Timestamp(last_ts).to_pydatetime()
            except Exception:
                return

        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=datetime.timezone.utc)

        ref = now or datetime.datetime.now(datetime.timezone.utc)
        age = ref - last_ts

        interval_min = _MAX_GAP_MINUTES.get(timeframe, 60)
        threshold = datetime.timedelta(minutes=interval_min * _STALE_INTERVALS)
        if age > threshold:
            report.add(
                "stale",
                "warning",
                f"Last bar is {age} old (threshold: {threshold})",
            )

    # ── Flat price ────────────────────────────────────────────────────────────

    def _check_flat_price(self, df: pd.DataFrame, report: DataQualityReport) -> None:
        if "close" not in df.columns or len(df) < 5:
            return
        std = df["close"].tail(20).std()
        if std == 0:
            report.add(
                "flat",
                "error",
                "All recent close prices are identical (possible feed freeze)",
            )

    # ── Outliers ──────────────────────────────────────────────────────────────

    def _check_outliers(self, df: pd.DataFrame, report: DataQualityReport) -> None:
        if "close" not in df.columns or len(df) < 10:
            return
        returns = df["close"].pct_change().dropna()
        if returns.std() == 0:
            return
        z_scores = (returns - returns.mean()) / returns.std()
        outliers = (z_scores.abs() > _OUTLIER_ZSCORE).sum()
        if outliers > 0:
            report.add(
                "outlier",
                "warning",
                f"{outliers} returns exceed {_OUTLIER_ZSCORE}σ (possible bad ticks)",
            )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _get_timestamps(df: pd.DataFrame) -> Optional[pd.Series]:
        if "timestamp" in df.columns:
            return pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        if isinstance(df.index, pd.DatetimeTzAware if hasattr(pd, "DatetimeTzAware") else type(None)):
            return pd.Series(df.index)
        try:
            return pd.to_datetime(df.index, utc=True, errors="coerce")
        except Exception:
            return None
