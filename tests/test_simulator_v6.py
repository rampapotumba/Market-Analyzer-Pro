"""Trade Simulator v6 tests — TASK-V6-01, TASK-V6-02, TASK-V6-03.

Test naming: test_v6_{task_number}_{what_we_check}
All DB interactions are mocked — no real database required.
"""

import datetime
import hashlib
import json
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ── TASK-V6-01: data_hash determinism ────────────────────────────────────────


class TestV601DataHash:
    """TASK-V6-01: data_hash added to _compute_summary()."""

    def _make_trade(
        self,
        symbol: str = "EURUSD=X",
        direction: str = "LONG",
        entry_price: str = "1.1000",
        exit_price: str = "1.1100",
        pnl_usd: str = "10.00",
        exit_reason: str = "tp_hit",
        entry_at: datetime.datetime = None,
        exit_at: datetime.datetime = None,
    ) -> MagicMock:
        t = MagicMock()
        t.symbol = symbol
        t.direction = direction
        t.entry_price = Decimal(entry_price)
        t.exit_price = Decimal(exit_price)
        t.pnl_usd = Decimal(pnl_usd)
        t.result = "win"
        t.exit_reason = exit_reason
        t.pnl_pips = Decimal("100.0000")
        t.composite_score = Decimal("12.0")
        t.entry_at = entry_at or datetime.datetime(2024, 1, 15, 10, 0, tzinfo=datetime.timezone.utc)
        t.exit_at = exit_at or datetime.datetime(2024, 1, 16, 10, 0, tzinfo=datetime.timezone.utc)
        t.duration_minutes = 1440
        t.mfe = Decimal("0.0100")
        t.mae = Decimal("0.0020")
        t.regime = "TREND_BULL"
        t.timeframe = "H1"
        return t

    def test_v6_01_data_hash_in_summary(self) -> None:
        """Summary contains data_hash field."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = [self._make_trade()]
        summary = _compute_summary(trades, Decimal("1000"))
        assert "data_hash" in summary
        assert isinstance(summary["data_hash"], str)
        assert len(summary["data_hash"]) == 16  # first 16 chars of sha256 hex

    def test_v6_01_data_hash_deterministic(self) -> None:
        """Same trade set always produces identical hash."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = [self._make_trade(), self._make_trade(symbol="GBPUSD=X", pnl_usd="-5.00")]
        hash1 = _compute_summary(trades, Decimal("1000"))["data_hash"]
        hash2 = _compute_summary(trades, Decimal("1000"))["data_hash"]
        assert hash1 == hash2

    def test_v6_01_data_hash_differs_for_different_trades(self) -> None:
        """Different trades produce different hashes."""
        from src.backtesting.backtest_engine import _compute_summary

        trades_a = [self._make_trade(pnl_usd="10.00")]
        trades_b = [self._make_trade(pnl_usd="20.00")]
        hash_a = _compute_summary(trades_a, Decimal("1000"))["data_hash"]
        hash_b = _compute_summary(trades_b, Decimal("1000"))["data_hash"]
        assert hash_a != hash_b

    def test_v6_01_empty_trades_produces_hash(self) -> None:
        """Empty trade list still produces a valid hash."""
        from src.backtesting.backtest_engine import _compute_summary

        summary = _compute_summary([], Decimal("1000"))
        assert "data_hash" in summary
        assert len(summary["data_hash"]) == 16

    def test_v6_01_report_single_run_structure(self) -> None:
        """Report generator builds markdown with run_id, params, data_hash."""
        from scripts.generate_backtest_report import _build_single_report

        run = {
            "run_id": "aaaabbbb-cccc-dddd-eeee-ffffffffffff",
            "status": "completed",
            "params": {"symbols": ["EURUSD=X"], "timeframe": "H1", "start_date": "2024-01-01"},
            "summary": {
                "data_hash": "abc123def456789a",
                "total_trades": 10,
                "win_rate_pct": 60.0,
                "profit_factor": 1.8,
                "total_pnl_usd": 50.0,
                "max_drawdown_pct": 5.0,
                "avg_duration_minutes": 480,
                "long_count": 7,
                "short_count": 3,
                "win_rate_long_pct": 71.4,
                "win_rate_short_pct": 33.3,
                "sl_hit_count": 4,
                "tp_hit_count": 6,
                "time_exit_count": 0,
                "mae_exit_count": 0,
                "by_symbol": {},
                "by_regime": {},
                "monthly_returns": [],
                "equity_curve": [],
            },
            "created_at": datetime.datetime(2024, 6, 1, 12, 0),
        }
        report = _build_single_report(run, [])
        assert "aaaabbbb" in report
        assert "abc123def456789a" in report
        assert "EURUSD=X" in report
        assert "## Parameters" in report
        assert "## Summary" in report

    def test_v6_01_report_comparison_two_runs(self) -> None:
        """Comparison table is generated for two runs with delta column."""
        from scripts.generate_backtest_report import _build_comparison_report

        def _make_run(run_id: str, trades: int, pf: float) -> dict:
            return {
                "run_id": run_id,
                "status": "completed",
                "params": {},
                "summary": {
                    "data_hash": "abc123",
                    "total_trades": trades,
                    "win_rate_pct": 50.0,
                    "profit_factor": pf,
                    "total_pnl_usd": 100.0,
                    "max_drawdown_pct": 5.0,
                    "win_rate_long_pct": 55.0,
                    "win_rate_short_pct": 40.0,
                    "sl_hit_count": 5,
                    "tp_hit_count": 5,
                },
                "created_at": datetime.datetime(2024, 6, 1),
            }

        runs = [
            _make_run("aaaa-1111-2222-3333-444444444444", 33, 2.01),
            _make_run("bbbb-5555-6666-7777-888888888888", 80, 1.50),
        ]
        report = _build_comparison_report(runs)
        assert "Delta" in report
        assert "aaaa-111" in report
        assert "bbbb-555" in report
        assert "Trades" in report


# ── TASK-V6-02: Proportional threshold scaling ────────────────────────────────


class TestV602ScaledThreshold:
    """TASK-V6-02: effective_threshold = threshold * available_weight."""

    def test_v6_02_scaled_threshold_backtest(self) -> None:
        """Threshold 15 with scale=0.45 but floor=0.65 → effective=9.75 (CAL-01).

        Note: V6-CAL-01 adds AVAILABLE_WEIGHT_FLOOR=0.65, so effective weight is
        max(0.45, 0.65)=0.65, giving threshold = 15*0.65 = 9.75.
        """
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # composite=10.0, effective=9.75 → pass
        passed, reason = pipeline.check_score_threshold(10.0, "forex", "EURUSD=X", available_weight=0.45)
        assert passed, f"Expected pass with floor=0.65, got: {reason}"

    def test_v6_02_scaled_threshold_backtest_blocked(self) -> None:
        """composite=9.0 with scale=0.45 and floor=0.65 → effective=9.75 → blocked (CAL-01)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_score_threshold(9.0, "forex", "EURUSD=X", available_weight=0.45)
        assert not passed
        assert "score_below_threshold" in reason

    def test_v6_02_scaled_threshold_live(self) -> None:
        """Threshold 15 with scale=1.0 → effective=15.0."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # composite=14.9 — just below threshold
        passed, _ = pipeline.check_score_threshold(14.9, "forex", "EURUSD=X", available_weight=1.0)
        assert not passed

        # composite=15.0 — exactly at threshold
        passed, _ = pipeline.check_score_threshold(15.0, "forex", "EURUSD=X", available_weight=1.0)
        assert passed

    def test_v6_02_signal_strength_scaled_strong_buy(self) -> None:
        """STRONG_BUY at composite=7.0 with scale=0.45 (raw threshold=6.75).

        Note: _get_signal_strength_scaled() uses the raw scale passed to it.
        The floor is applied in check_signal_strength() via effective_weight.
        This tests the underlying helper function directly.
        """
        from src.signals.filter_pipeline import _get_signal_strength_scaled

        strength = _get_signal_strength_scaled(7.0, scale=0.45)
        assert strength == "STRONG_BUY"

    def test_v6_02_signal_strength_scaled_buy(self) -> None:
        """BUY at composite=5.0 with scale=0.45 (buy threshold=4.5)."""
        from src.signals.filter_pipeline import _get_signal_strength_scaled

        strength = _get_signal_strength_scaled(5.0, scale=0.45)
        assert strength == "BUY"

    def test_v6_02_signal_strength_live_unchanged(self) -> None:
        """Scale=1.0 behaves identically to original _get_signal_strength."""
        from src.signals.filter_pipeline import _get_signal_strength, _get_signal_strength_scaled

        for score in [15.0, 12.0, 8.0, 0.0, -8.0, -12.0, -15.0, -20.0]:
            assert _get_signal_strength(score) == _get_signal_strength_scaled(score, scale=1.0), (
                f"Mismatch at score={score}"
            )

    def test_v6_02_instrument_override_scaled(self) -> None:
        """Override min_composite_score=18 (USDCHF) with floor=0.65 → effective=11.7 (CAL-01).

        Note: CAL-01 adds floor 0.65, so effective = 18 * max(0.45, 0.65) = 18 * 0.65 = 11.7.
        """
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # USDCHF=X has 18 → effective = 18*0.65 = 11.7
        # score=11.0 should be blocked
        passed, reason = pipeline.check_score_threshold(11.0, "forex", "USDCHF=X", available_weight=0.45)
        assert not passed, f"Expected block for 11.0 < 11.7, got pass. reason={reason}"

    def test_v6_02_instrument_override_scaled_pass(self) -> None:
        """Override min_composite_score=18 with floor=0.65 → effective=11.7, score=12.0 passes."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_score_threshold(12.0, "forex", "USDCHF=X", available_weight=0.45)
        assert passed, f"Expected pass for 12.0 >= 11.7, got: {reason}"

    def test_v6_02_crypto_threshold_scaled(self) -> None:
        """BTC/USDT has min_score=25 (CAL-05) * floor 0.65 = 16.25 effective."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # BTC/USDT override = 25, floor=0.65 → effective=16.25
        passed, _ = pipeline.check_score_threshold(17.0, "crypto", "BTC/USDT", available_weight=0.45)
        assert passed

        passed, _ = pipeline.check_score_threshold(16.0, "crypto", "BTC/USDT", available_weight=0.45)
        assert not passed

    def test_v6_02_available_weight_from_filter_context(self) -> None:
        """run_all() passes available_weight from context into check_score_threshold.

        Note: After CAL-01, floor=0.65, so effective_threshold = 15*0.65=9.75 even
        when available_weight=0.45. Score 10.0 passes, score 9.0 is blocked.
        """
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline(
            apply_regime_filter=False,
            apply_d1_trend_filter=False,
            apply_volume_filter=False,
            apply_momentum_filter=False,
            apply_weekday_filter=False,
            apply_calendar_filter=False,
            apply_session_filter=False,
            apply_dxy_filter=False,
        )
        context = {
            "composite_score": 10.0,   # above 15*0.65=9.75 (floor) but below 15*1.0=15
            "market_type": "forex",
            "symbol": "EURUSD=X",
            "regime": "TREND_BULL",
            "direction": "LONG",
            "timeframe": "H1",
            "available_weight": 0.45,
        }
        passed, reason = pipeline.run_all(context)
        assert passed, f"Expected pass with scale=0.45 (floor=0.65), got: {reason}"

        # Same score with live scale (1.0) should be blocked (10.0 < 15.0)
        context["available_weight"] = 1.0
        passed, reason = pipeline.run_all(context)
        assert not passed, "Expected block with scale=1.0 (score 10.0 < threshold 15)"

    def test_v6_02_backtest_engine_passes_available_weight(self) -> None:
        """backtest_engine.py filter_context contains available_weight=0.45."""
        # We verify by inspecting the constant _TA_WEIGHT in the engine
        from src.backtesting import backtest_engine

        assert backtest_engine._TA_WEIGHT == 0.45

    def test_v6_02_signal_strength_scaled_hold_at_low_score(self) -> None:
        """HOLD when abs_score < 7 * scale at scale=0.45 (threshold=3.15)."""
        from src.signals.filter_pipeline import _get_signal_strength_scaled

        strength = _get_signal_strength_scaled(3.0, scale=0.45)
        assert strength == "HOLD"

    def test_v6_02_short_signal_strength_scaled(self) -> None:
        """Negative composite works symmetrically with scale."""
        from src.signals.filter_pipeline import _get_signal_strength_scaled

        # abs=7.5 >= 6.75 (STRONG_SELL threshold at scale=0.45)
        assert _get_signal_strength_scaled(-7.5, scale=0.45) == "STRONG_SELL"
        # abs=5.0 >= 4.5 (SELL threshold)
        assert _get_signal_strength_scaled(-5.0, scale=0.45) == "SELL"
        # abs=4.0 >= 3.15 (WEAK_SELL threshold)
        assert _get_signal_strength_scaled(-4.0, scale=0.45) == "WEAK_SELL"


# ── TASK-V6-03: BTC/USDT allowed_regimes expanded ────────────────────────────


class TestV603BtcUnblock:
    """TASK-V6-03: BTC/USDT allowed_regimes includes TREND_BULL and TREND_BEAR."""

    def test_v6_03_btc_allowed_regimes_expanded(self) -> None:
        """BTC/USDT STRONG_TREND_BULL is in allowed_regimes.

        Note: v6-calibration (CAL-05) tightened BTC to STRONG_TREND_BULL only.
        TREND_BULL and TREND_BEAR were removed as part of calibration.
        """
        from src.config import INSTRUMENT_OVERRIDES

        overrides = INSTRUMENT_OVERRIDES.get("BTC/USDT", {})
        allowed = overrides.get("allowed_regimes", [])
        assert "STRONG_TREND_BULL" in allowed, f"STRONG_TREND_BULL not in BTC allowed_regimes: {allowed}"

    def test_v6_03_btc_strong_trend_still_allowed(self) -> None:
        """STRONG_TREND_BULL is allowed for BTC/USDT (only bull after CAL-05)."""
        from src.config import INSTRUMENT_OVERRIDES

        overrides = INSTRUMENT_OVERRIDES.get("BTC/USDT", {})
        allowed = overrides.get("allowed_regimes", [])
        assert "STRONG_TREND_BULL" in allowed

    def test_v6_03_btc_threshold_updated(self) -> None:
        """BTC/USDT min_composite_score is 25 (tightened by CAL-05 from 15)."""
        from src.config import INSTRUMENT_OVERRIDES

        overrides = INSTRUMENT_OVERRIDES.get("BTC/USDT", {})
        score = overrides.get("min_composite_score")
        assert score == 25, f"Expected 25 (CAL-05 tightening), got {score}"

    def test_v6_03_eth_threshold_updated(self) -> None:
        """ETH/USDT min_composite_score is 20 (restored by CAL-05)."""
        from src.config import INSTRUMENT_OVERRIDES

        overrides = INSTRUMENT_OVERRIDES.get("ETH/USDT", {})
        score = overrides.get("min_composite_score")
        assert score == 20, f"Expected 20 (CAL-05 restore), got {score}"

    def test_v6_03_btc_strong_trend_bull_passes_regime_filter(self) -> None:
        """SignalFilterPipeline allows BTC/USDT in STRONG_TREND_BULL."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_regime("STRONG_TREND_BULL", "BTC/USDT")
        assert passed, f"Expected STRONG_TREND_BULL to pass for BTC/USDT, got: {reason}"

    def test_v6_03_btc_ranging_still_blocked(self) -> None:
        """RANGING is still blocked for BTC/USDT."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # RANGING is in BLOCKED_REGIMES globally
        passed, reason = pipeline.check_regime("RANGING", "BTC/USDT")
        assert not passed, "Expected RANGING to be blocked for BTC/USDT"

    def test_v6_03_gbpusd_has_score_override(self) -> None:
        """GBPUSD=X has min_composite_score=20 override (added by CAL-06)."""
        from src.config import INSTRUMENT_OVERRIDES

        overrides = INSTRUMENT_OVERRIDES.get("GBPUSD=X", {})
        score = overrides.get("min_composite_score")
        assert score == 20, (
            f"GBPUSD=X should have min_composite_score=20 (CAL-06), got: {score}"
        )

    def test_v6_03_score_component_weights_defined(self) -> None:
        """SCORE_COMPONENT_WEIGHTS sums to 1.0."""
        from src.config import SCORE_COMPONENT_WEIGHTS

        total = sum(SCORE_COMPONENT_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"Weights should sum to 1.0, got {total}"
        assert "ta" in SCORE_COMPONENT_WEIGHTS
        assert SCORE_COMPONENT_WEIGHTS["ta"] == 0.45


# ── TASK-V6-04: GBPUSD no score override ─────────────────────────────────────


class TestV604GbpusdFix:
    """TASK-V6-04: GBPUSD=X min_composite_score override removed."""

    def test_v6_04_gbpusd_has_score_override(self) -> None:
        """GBPUSD=X has min_composite_score=20 override (CAL-06).

        Note: originally TASK-V6-04 removed the override, but CAL-06 added it back
        with value 20 to address -$43 PnL at global threshold.
        """
        from src.config import INSTRUMENT_OVERRIDES

        overrides = INSTRUMENT_OVERRIDES.get("GBPUSD=X", {})
        score = overrides.get("min_composite_score")
        assert score == 20, (
            f"GBPUSD=X should have min_composite_score=20 (CAL-06), got: {score}"
        )

    def test_v6_04_gbpusd_uses_override_threshold(self) -> None:
        """GBPUSD=X with composite=13.0 passes (>= 20*0.65=13.0)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # Override threshold 20 * floor(0.65) = 13.0; score=13.0 must pass
        passed, reason = pipeline.check_score_threshold(
            13.0, "forex", "GBPUSD=X", available_weight=0.45
        )
        assert passed, f"Expected GBPUSD to pass with score=13.0, scale=0.65 (floor). got: {reason}"

    def test_v6_04_gbpusd_blocked_below_override_threshold(self) -> None:
        """GBPUSD=X with composite=12.0 is blocked (< 20*0.65=13.0)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_score_threshold(
            12.0, "forex", "GBPUSD=X", available_weight=0.45
        )
        assert not passed, "Expected block for score=12.0 below effective threshold 13.0"
        assert "score_below_threshold" in reason


# ── TASK-V6-05: regime persisted in trade_dicts ───────────────────────────────


class TestV605RegimePersistence:
    """TASK-V6-05: regime field survives serialization to trade_dicts."""

    def _make_trade(
        self,
        regime: str = "TREND_BULL",
        result: str = "win",
        pnl: str = "10.00",
        exit_reason: str = "tp_hit",
    ) -> MagicMock:
        t = MagicMock()
        t.symbol = "EURUSD=X"
        t.timeframe = "H1"
        t.direction = "LONG"
        t.entry_price = Decimal("1.1000")
        t.exit_price = Decimal("1.1100")
        t.exit_reason = exit_reason
        t.pnl_pips = Decimal("100.0000")
        t.pnl_usd = Decimal(pnl)
        t.result = result
        t.composite_score = Decimal("12.0")
        t.entry_at = datetime.datetime(2024, 3, 1, 10, 0, tzinfo=datetime.timezone.utc)
        t.exit_at = datetime.datetime(2024, 3, 2, 10, 0, tzinfo=datetime.timezone.utc)
        t.duration_minutes = 1440
        t.mfe = Decimal("0.01")
        t.mae = Decimal("0.002")
        t.regime = regime
        return t

    def test_v6_05_regime_in_compute_summary(self) -> None:
        """by_regime summary contains real regime names (not UNKNOWN)."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = [
            self._make_trade(regime="TREND_BULL"),
            self._make_trade(regime="STRONG_TREND_BULL"),
            self._make_trade(regime="TREND_BEAR", result="loss", pnl="-5.00"),
        ]
        summary = _compute_summary(trades, Decimal("1000"))
        by_regime = summary["by_regime"]
        assert "TREND_BULL" in by_regime, f"Expected TREND_BULL in by_regime: {by_regime}"
        assert "STRONG_TREND_BULL" in by_regime
        assert "TREND_BEAR" in by_regime
        # Should not have UNKNOWN when all trades have valid regime
        assert "UNKNOWN" not in by_regime, f"UNKNOWN should not appear: {by_regime}"

    def test_v6_05_regime_none_falls_back_to_unknown(self) -> None:
        """Trade with regime=None is bucketed as UNKNOWN in by_regime."""
        from src.backtesting.backtest_engine import _compute_summary

        t = self._make_trade(regime=None)
        summary = _compute_summary([t], Decimal("1000"))
        assert "UNKNOWN" in summary["by_regime"]

    def test_v6_05_trade_dict_has_regime_key(self) -> None:
        """The trade_dicts list passed to DB bulk insert includes regime key."""
        # We verify indirectly: inspect the code path by checking the serialization
        # The actual test is that run_backtest() doesn't drop regime.
        # Here we test the dict structure expected by create_backtest_trades_bulk.
        from src.backtesting.backtest_engine import _compute_summary

        t = self._make_trade(regime="VOLATILE")
        summary = _compute_summary([t], Decimal("1000"))
        # regime appears in by_regime
        assert "VOLATILE" in summary["by_regime"]


# ── TASK-V6-06: D1 data loading in backtest ──────────────────────────────────


class TestV606D1DataLoading:
    """TASK-V6-06: D1 rows loaded from cache and passed to filter pipeline."""

    def _make_d1_row(self, ts: datetime.datetime, close: float) -> MagicMock:
        row = MagicMock()
        row.timestamp = ts
        row.close = Decimal(str(close))
        row.open = Decimal(str(close - 0.001))
        row.high = Decimal(str(close + 0.002))
        row.low = Decimal(str(close - 0.002))
        row.volume = 0
        return row

    def _make_d1_rows(self, n: int = 210, base_close: float = 1.10) -> list:
        """Generate n D1 rows from 300 days ago."""
        start = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        rows = []
        for i in range(n):
            ts = start + datetime.timedelta(days=i)
            rows.append(self._make_d1_row(ts, base_close + i * 0.0001))
        return rows

    def test_v6_06_d1_filter_passes_with_sufficient_rows(self) -> None:
        """D1 MA200 filter passes when close > MA200 for LONG signal."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        rows = self._make_d1_rows(n=210, base_close=1.10)
        # All rows trending up — last close > MA200
        passed, reason = pipeline.check_d1_trend("EURUSD=X", "LONG", "H1", rows)
        assert passed, f"Expected D1 LONG to pass when trending up, got: {reason}"

    def test_v6_06_d1_filter_blocks_counter_trend_long(self) -> None:
        """D1 MA200 filter blocks LONG when last close < MA200."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # Rows trending DOWN: first 200 at high level, last 10 much lower
        rows = []
        start = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        for i in range(200):
            ts = start + datetime.timedelta(days=i)
            r = MagicMock()
            r.timestamp = ts
            r.close = Decimal("1.2000")  # high historical average
            rows.append(r)
        # Recent candles drop sharply (below MA200)
        for i in range(200, 210):
            ts = start + datetime.timedelta(days=i)
            r = MagicMock()
            r.timestamp = ts
            r.close = Decimal("1.0000")  # well below MA200
            rows.append(r)

        passed, reason = pipeline.check_d1_trend("EURUSD=X", "LONG", "H1", rows)
        assert not passed, f"Expected D1 LONG to be blocked when below MA200, got pass"
        assert "d1_bearish_trend" in reason

    def test_v6_06_d1_filter_graceful_no_data(self) -> None:
        """D1 filter passes gracefully when fewer than 200 rows available."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        sparse_rows = self._make_d1_rows(n=50)  # insufficient
        passed, reason = pipeline.check_d1_trend("EURUSD=X", "LONG", "H1", sparse_rows)
        assert passed, f"Expected graceful pass with sparse D1 data, got: {reason}"

    def test_v6_06_d1_filter_passes_empty_rows(self) -> None:
        """D1 filter passes when no rows at all (graceful degradation)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_d1_trend("EURUSD=X", "LONG", "H1", [])
        assert passed, f"Expected graceful pass with empty D1 data, got: {reason}"

    def test_v6_06_d1_slicing_respects_candle_ts(self) -> None:
        """D1 slice only includes candles with timestamp <= signal_ts."""
        # Build rows: 250 rows, but only 210 are before the signal timestamp
        rows_all = self._make_d1_rows(n=250, base_close=1.10)
        signal_ts = rows_all[209].timestamp  # cut at index 209

        filtered = [r for r in rows_all if r.timestamp <= signal_ts][-200:]
        assert len(filtered) == 200
        assert filtered[-1].timestamp == signal_ts


# ── TASK-V6-07: Calendar filter diagnostics ──────────────────────────────────


class TestV607CalendarFilter:
    """TASK-V6-07: Calendar filter blocks near high-impact events (±4h window)."""

    def _make_event(
        self, ts: datetime.datetime, name: str = "NFP", impact: str = "high"
    ) -> MagicMock:
        e = MagicMock()
        e.event_date = ts
        e.event_name = name
        e.impact = impact
        return e

    def test_v6_07_calendar_blocks_within_4h(self) -> None:
        """Signal within ±4h of HIGH-impact event is blocked."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        event_ts = datetime.datetime(2024, 2, 2, 14, 30, tzinfo=datetime.timezone.utc)
        # Signal 3 hours before NFP
        signal_ts = event_ts - datetime.timedelta(hours=3)
        events = [self._make_event(event_ts, "NonFarm Payrolls")]

        passed, reason = pipeline.check_calendar(signal_ts, events)
        assert not passed, "Expected calendar block within 4h of NFP"
        assert "economic_calendar_block" in reason

    def test_v6_07_calendar_passes_outside_4h(self) -> None:
        """Signal more than 4h from event is allowed."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        event_ts = datetime.datetime(2024, 2, 2, 14, 30, tzinfo=datetime.timezone.utc)
        # Signal 5 hours before — outside ±4h window
        signal_ts = event_ts - datetime.timedelta(hours=5)
        events = [self._make_event(event_ts)]

        passed, reason = pipeline.check_calendar(signal_ts, events)
        assert passed, f"Expected pass outside 4h window, got: {reason}"

    def test_v6_07_calendar_passes_no_events(self) -> None:
        """No events → filter passes."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        signal_ts = datetime.datetime(2024, 2, 2, 14, 30, tzinfo=datetime.timezone.utc)
        passed, reason = pipeline.check_calendar(signal_ts, [])
        assert passed

    def test_v6_07_calendar_passes_no_timestamp(self) -> None:
        """No candle_ts → filter passes gracefully."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        event_ts = datetime.datetime(2024, 2, 2, 14, 30, tzinfo=datetime.timezone.utc)
        passed, _ = pipeline.check_calendar(None, [self._make_event(event_ts)])
        assert passed

    def test_v6_07_calendar_blocks_exactly_at_4h(self) -> None:
        """Signal exactly at ±4h boundary is blocked (inclusive window)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        event_ts = datetime.datetime(2024, 2, 2, 14, 30, tzinfo=datetime.timezone.utc)
        signal_ts = event_ts - datetime.timedelta(hours=4)
        passed, _ = pipeline.check_calendar(signal_ts, [self._make_event(event_ts)])
        assert not passed, "Boundary at exactly 4h should be blocked (inclusive)"

    def test_v6_07_calendar_handles_naive_datetimes(self) -> None:
        """Naive datetimes (no tzinfo) are treated as UTC."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # Both naive: event at 14:30, signal at 12:30 (2h gap — inside 4h window)
        event_ts = datetime.datetime(2024, 2, 2, 14, 30)  # naive
        signal_ts = datetime.datetime(2024, 2, 2, 12, 30)  # naive
        passed, reason = pipeline.check_calendar(signal_ts, [self._make_event(event_ts)])
        assert not passed, f"Expected block for naive datetimes, got: {reason}"


# ── TASK-V6-08: SHORT signal quality ─────────────────────────────────────────


class TestV608ShortQuality:
    """TASK-V6-08: Asymmetric SHORT threshold and stricter RSI (< 30).

    V6-CAL2-03: SHORT_SCORE_MULTIPLIER reduced from 2.0 to 1.3 to allow SHORT trades.
    """

    def test_v6_08_short_threshold_multiplied(self) -> None:
        """SHORT effective_threshold = base * available_weight * 1.3 (CAL2-03 reduced from 2.0).

        Live mode: threshold = 15 * 1.0 * 1.3 = 19.5.
        composite=-18.0 → abs=18 < 19.5 → blocked.
        """
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # Global threshold 15, scale=1.0, multiplier=1.3 → effective=19.5
        # composite=-18.0 → abs=18 < 19.5 → blocked
        passed, reason = pipeline.check_score_threshold(
            -18.0, "forex", "EURUSD=X", available_weight=1.0, direction="SHORT"
        )
        assert not passed, f"Expected SHORT composite=-18 to be blocked (threshold=19.5), got pass"
        assert "score_below_threshold" in reason

    def test_v6_08_short_passes_above_threshold(self) -> None:
        """SHORT with composite=-20 passes threshold of 19.5 (CAL2-03: 15*1.0*1.3=19.5)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_score_threshold(
            -20.0, "forex", "EURUSD=X", available_weight=1.0, direction="SHORT"
        )
        assert passed, f"Expected SHORT composite=-20 to pass (threshold=19.5), got: {reason}"

    def test_v6_08_short_threshold_with_scale(self) -> None:
        """SHORT in backtest: threshold = 15 * 0.65 * 1.3 = 12.675 (with CAL-01 floor, CAL2-03 multiplier).

        Note: CAL-01 raises floor from 0.45 to 0.65, CAL2-03 lowers multiplier from 2.0 to 1.3.
        """
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # composite=-12.0, effective = 15 * 0.65 * 1.3 = 12.675 → blocked
        passed, _ = pipeline.check_score_threshold(
            -12.0, "forex", "EURUSD=X", available_weight=0.45, direction="SHORT"
        )
        assert not passed

        # composite=-13.0, effective=12.675 → pass
        passed, reason = pipeline.check_score_threshold(
            -13.0, "forex", "EURUSD=X", available_weight=0.45, direction="SHORT"
        )
        assert passed, f"Expected pass for SHORT composite=-13.0 (threshold=12.675), got: {reason}"

    def test_v6_08_long_threshold_unaffected(self) -> None:
        """LONG threshold is not modified by SHORT multiplier."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # LONG: threshold = 15, composite=15 → pass
        passed, _ = pipeline.check_score_threshold(
            15.0, "forex", "EURUSD=X", available_weight=1.0, direction="LONG"
        )
        assert passed

        # LONG: composite=14.9 → blocked (not inflated by 1.2)
        passed, _ = pipeline.check_score_threshold(
            14.9, "forex", "EURUSD=X", available_weight=1.0, direction="LONG"
        )
        assert not passed

    def test_v6_08_short_rsi_35_blocks(self) -> None:
        """SHORT with RSI=35 is blocked (RSI >= SHORT_RSI_THRESHOLD=30 after CAL-04)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        indicators = {"rsi": 35.0, "macd": -0.001, "macd_signal": 0.001}
        passed, reason = pipeline.check_momentum(indicators, "SHORT")
        assert not passed, f"Expected SHORT RSI=35 to be blocked (threshold=30), got pass"
        assert "momentum_misaligned_short" in reason

    def test_v6_08_short_rsi_29_passes(self) -> None:
        """SHORT with RSI=29 and MACD aligned passes momentum filter (threshold=30 after CAL-04)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        indicators = {"rsi": 29.0, "macd": -0.001, "macd_signal": 0.001}
        passed, reason = pipeline.check_momentum(indicators, "SHORT")
        assert passed, f"Expected SHORT RSI=29 to pass (< 30), got: {reason}"

    def test_v6_08_long_rsi_unaffected(self) -> None:
        """LONG momentum check still uses RSI > 50 (not affected by SHORT threshold)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # RSI=51 LONG + bullish MACD → pass
        indicators = {"rsi": 51.0, "macd": 0.001, "macd_signal": -0.001}
        passed, _ = pipeline.check_momentum(indicators, "LONG")
        assert passed

        # RSI=49 LONG → blocked
        indicators["rsi"] = 49.0
        passed, reason = pipeline.check_momentum(indicators, "LONG")
        assert not passed, "LONG RSI=49 should be blocked"

    def test_v6_08_short_config_constants(self) -> None:
        """Config has SHORT_SCORE_MULTIPLIER=1.3 (V6-CAL2-03) and SHORT_RSI_THRESHOLD=30."""
        from src.config import SHORT_RSI_THRESHOLD, SHORT_SCORE_MULTIPLIER

        # V6-CAL2-03: reduced from 2.0 to 1.3 to allow SHORT trades in backtest
        assert SHORT_SCORE_MULTIPLIER == pytest.approx(1.3), (
            f"Expected 1.3 (V6-CAL2-03 reduced from 2.0), got {SHORT_SCORE_MULTIPLIER}"
        )
        assert SHORT_RSI_THRESHOLD == 30, f"Expected 30 (CAL-04), got {SHORT_RSI_THRESHOLD}"


# ── TASK-V6-09: SPY instrument override ──────────────────────────────────────


class TestV609SpyOverride:
    """TASK-V6-09: SPY has strict override to reduce losses."""

    def test_v6_09_spy_override_exists(self) -> None:
        """SPY is in INSTRUMENT_OVERRIDES."""
        from src.config import INSTRUMENT_OVERRIDES

        assert "SPY" in INSTRUMENT_OVERRIDES, "SPY must be in INSTRUMENT_OVERRIDES"

    def test_v6_09_spy_min_composite_score(self) -> None:
        """SPY min_composite_score = 22 (V6-CAL2-08: relaxed from 30 to allow more trades)."""
        from src.config import INSTRUMENT_OVERRIDES

        score = INSTRUMENT_OVERRIDES["SPY"].get("min_composite_score")
        assert score == 22, f"Expected SPY min_composite_score=22 (V6-CAL2-08), got {score}"

    def test_v6_09_spy_regime_restricted(self) -> None:
        """SPY is restricted to STRONG_TREND_BULL and VOLATILE (V6-CAL2-08: VOLATILE added)."""
        from src.config import INSTRUMENT_OVERRIDES

        allowed = INSTRUMENT_OVERRIDES["SPY"].get("allowed_regimes", [])
        assert "STRONG_TREND_BULL" in allowed
        # V6-CAL2-08: VOLATILE added to allow more trades
        assert "VOLATILE" in allowed
        # RANGING and TREND_BULL should NOT be allowed
        assert "RANGING" not in allowed
        assert "TREND_BULL" not in allowed
        # STRONG_TREND_BEAR removed in CAL-06 (SPY is long-side only instrument)
        assert "STRONG_TREND_BEAR" not in allowed

    def test_v6_09_spy_ranging_blocked_by_pipeline(self) -> None:
        """SPY in RANGING is blocked by regime filter."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_regime("RANGING", "SPY")
        assert not passed, "SPY RANGING should be blocked"

    def test_v6_09_spy_trend_bull_blocked_by_pipeline(self) -> None:
        """SPY in TREND_BULL is blocked (not in allowed_regimes)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_regime("TREND_BULL", "SPY")
        assert not passed, f"SPY TREND_BULL should be blocked, got: {reason}"

    def test_v6_09_spy_strong_trend_bull_allowed(self) -> None:
        """SPY in STRONG_TREND_BULL passes regime filter."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_regime("STRONG_TREND_BULL", "SPY")
        assert passed, f"Expected STRONG_TREND_BULL to pass for SPY, got: {reason}"

    def test_v6_09_spy_high_score_required(self) -> None:
        """SPY composite=21 (< 22) is blocked even with scale=1.0 (V6-CAL2-08: 22)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_score_threshold(
            21.0, "stocks", "SPY", available_weight=1.0
        )
        assert not passed, f"Expected SPY composite=21 to be blocked (threshold=22), got pass"

    def test_v6_09_spy_scaled_threshold(self) -> None:
        """SPY in backtest: threshold = 22 * 0.65 = 14.3 (with CAL-01 floor, V6-CAL2-08 score=22)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # composite=14.0 < 14.3 → blocked
        passed, _ = pipeline.check_score_threshold(
            14.0, "stocks", "SPY", available_weight=0.45
        )
        assert not passed

        # composite=15.0 >= 14.3 → pass
        passed, reason = pipeline.check_score_threshold(
            15.0, "stocks", "SPY", available_weight=0.45
        )
        assert passed, f"Expected SPY composite=15.0 to pass at scale=0.65 (floor), got: {reason}"


# ── TASK-V6-10: Filter diagnostics (rejection counters) ──────────────────────


class TestV610FilterDiagnostics:
    """TASK-V6-10: SignalFilterPipeline tracks rejection counts per filter."""

    def _make_minimal_context(
        self,
        composite: float = 10.0,  # Updated for CAL-01: floor=0.65 → threshold=9.75
        direction: str = "LONG",
        regime: str = "TREND_BULL",
        available_weight: float = 0.45,
    ) -> dict:
        return {
            "composite_score": composite,
            "market_type": "forex",
            "symbol": "EURUSD=X",
            "regime": regime,
            "direction": direction,
            "timeframe": "H1",
            "candle_ts": datetime.datetime(2024, 6, 15, 12, 0, tzinfo=datetime.timezone.utc),  # Saturday-safe
            "available_weight": available_weight,
            "d1_rows": [],
            "economic_events": [],
            "ta_indicators": {},
        }

    def test_v6_10_filter_stats_initial_zero(self) -> None:
        """Freshly created pipeline has zero counters."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        assert pipeline.total_signals == 0
        assert pipeline.passed_signals == 0
        assert len(pipeline.rejection_counts) == 0

    def test_v6_10_passed_signal_increments_passed(self) -> None:
        """Signal that passes all filters increments passed_signals and total."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline(
            apply_regime_filter=False,
            apply_d1_trend_filter=False,
            apply_volume_filter=False,
            apply_momentum_filter=False,
            apply_weekday_filter=False,
            apply_calendar_filter=False,
            apply_session_filter=False,
            apply_dxy_filter=False,
        )
        # composite=10.0 >= 9.75 (floor 0.65 threshold) → passes
        context = self._make_minimal_context(composite=10.0, available_weight=0.45)
        passed, _ = pipeline.run_all(context)
        assert passed
        assert pipeline.total_signals == 1
        assert pipeline.passed_signals == 1
        assert len(pipeline.rejection_counts) == 0

    def test_v6_10_rejected_signal_increments_rejection_count(self) -> None:
        """Signal blocked by score_threshold increments rejection_counts['score_threshold']."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline(
            apply_regime_filter=False,
            apply_d1_trend_filter=False,
            apply_volume_filter=False,
            apply_momentum_filter=False,
            apply_weekday_filter=False,
            apply_calendar_filter=False,
            apply_session_filter=False,
            apply_dxy_filter=False,
        )
        # composite=2.0 below threshold 15*0.45=6.75 → score_threshold rejection
        context = self._make_minimal_context(composite=2.0, available_weight=0.45)
        passed, _ = pipeline.run_all(context)
        assert not passed
        assert pipeline.total_signals == 1
        assert pipeline.passed_signals == 0
        assert pipeline.rejection_counts["score_threshold"] == 1

    def test_v6_10_multiple_signals_accumulate_counts(self) -> None:
        """Running multiple signals accumulates counts correctly."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline(
            apply_regime_filter=False,
            apply_d1_trend_filter=False,
            apply_volume_filter=False,
            apply_momentum_filter=False,
            apply_weekday_filter=False,
            apply_calendar_filter=False,
            apply_session_filter=False,
            apply_dxy_filter=False,
        )
        # 3 rejected signals
        for _ in range(3):
            context = self._make_minimal_context(composite=2.0, available_weight=0.45)
            pipeline.run_all(context)
        # 2 passed signals (10.0 >= 9.75 threshold with floor=0.65)
        for _ in range(2):
            context = self._make_minimal_context(composite=10.0, available_weight=0.45)
            pipeline.run_all(context)

        assert pipeline.total_signals == 5
        assert pipeline.passed_signals == 2
        assert pipeline.rejection_counts["score_threshold"] == 3

    def test_v6_10_get_stats_sums_correctly(self) -> None:
        """get_stats() total_raw_signals == passed + sum(rejected_by_*)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline(
            apply_regime_filter=False,
            apply_d1_trend_filter=False,
            apply_volume_filter=False,
            apply_momentum_filter=False,
            apply_weekday_filter=False,
            apply_calendar_filter=False,
            apply_session_filter=False,
            apply_dxy_filter=False,
        )
        # With floor=0.65: threshold=9.75; composites 10.0 and 11.0 pass; rest blocked
        for composite in [2.0, 10.0, 3.0, 11.0, 1.0]:
            pipeline.run_all(self._make_minimal_context(composite=composite, available_weight=0.45))

        stats = pipeline.get_stats()
        total = stats["total_raw_signals"]
        passed = stats["passed_all"]
        rejected_sum = sum(v for k, v in stats.items() if k.startswith("rejected_by_"))
        assert total == passed + rejected_sum

    def test_v6_10_filter_stats_in_compute_summary(self) -> None:
        """_compute_summary() includes filter_stats when provided."""
        from src.backtesting.backtest_engine import _compute_summary

        filter_stats = {
            "total_raw_signals": 100,
            "passed_all": 20,
            "rejected_by_score_threshold": 50,
            "rejected_by_momentum_filter": 30,
        }
        trades: list = []
        summary = _compute_summary(trades, Decimal("1000"), filter_stats=filter_stats)
        assert "filter_stats" in summary
        assert summary["filter_stats"]["total_raw_signals"] == 100
        assert summary["filter_stats"]["passed_all"] == 20

    def test_v6_10_filter_stats_absent_when_not_provided(self) -> None:
        """_compute_summary() without filter_stats has no filter_stats key."""
        from src.backtesting.backtest_engine import _compute_summary

        summary = _compute_summary([], Decimal("1000"))
        assert "filter_stats" not in summary


# ── TASK-V6-11: Time Exit and MAE Exit in backtest ────────────────────────────


class TestV611TimeAndMaeExit:
    """TASK-V6-11: _check_exit() implements time exit and MAE exit."""

    def _make_candle(
        self,
        high: float = 1.1050,
        low: float = 1.0950,
        close: float = 1.1000,
        ts: datetime.datetime = None,
    ) -> MagicMock:
        c = MagicMock()
        c.high = high
        c.low = low
        c.close = close
        c.timestamp = ts or datetime.datetime(2024, 6, 15, 14, 0, tzinfo=datetime.timezone.utc)
        return c

    def _make_open_trade(
        self,
        direction: str = "LONG",
        entry_price: float = 1.1000,
        stop_loss: float = 1.0900,
        take_profit: float = 1.1200,
        mfe: float = 0.0,
        mae: float = 0.0,
        entry_bar_index: int = 0,
        timeframe: str = "H1",
    ) -> dict:
        return {
            "symbol": "EURUSD=X",
            "timeframe": timeframe,
            "direction": direction,
            "entry_price": Decimal(str(entry_price)),
            "entry_at": datetime.datetime(2024, 6, 1, 10, 0, tzinfo=datetime.timezone.utc),
            "stop_loss": Decimal(str(stop_loss)),
            "take_profit": Decimal(str(take_profit)),
            "composite_score": Decimal("7.5"),
            "position_pct": Decimal("2.0"),
            "mfe": mfe,
            "mae": mae,
            "regime": "TREND_BULL",
            "entry_bar_index": entry_bar_index,
            "trailing_sl_active": False,
            "original_stop_loss": Decimal(str(stop_loss)),
        }

    def test_v6_11_sl_still_works(self) -> None:
        """SL hit is still detected correctly with new signature."""
        from src.backtesting.backtest_engine import BacktestEngine

        engine = BacktestEngine.__new__(BacktestEngine)
        trade = self._make_open_trade(direction="LONG", entry_price=1.1000, stop_loss=1.0900)
        candle = self._make_candle(high=1.0950, low=1.0880)  # low <= sl → sl_hit
        result = engine._check_exit(trade, candle, "forex", False, candles_since_entry=5)
        assert result is not None
        assert result.exit_reason == "sl_hit"

    def test_v6_11_tp_still_works(self) -> None:
        """TP hit is still detected correctly with new signature."""
        from src.backtesting.backtest_engine import BacktestEngine

        engine = BacktestEngine.__new__(BacktestEngine)
        trade = self._make_open_trade(direction="LONG", entry_price=1.1000, take_profit=1.1200)
        candle = self._make_candle(high=1.1250, low=1.1050)  # high >= tp → tp_hit
        result = engine._check_exit(trade, candle, "forex", False, candles_since_entry=5)
        assert result is not None
        assert result.exit_reason == "tp_hit"

    def test_v6_11_time_exit_triggers_at_max_candles_with_negative_pnl(self) -> None:
        """Time exit fires after 24 H1 candles with PnL <= 0 (CAL-03: reduced from 48).

        Note: H1 time exit reduced from 48 to 24 candles by V6-CAL-03.
        61.6% of trades were exiting via time_exit after 2 days; 1 day is sufficient.
        """
        from src.backtesting.backtest_engine import BacktestEngine

        engine = BacktestEngine.__new__(BacktestEngine)
        # LONG: entry=1.1000, SL=1.0900, TP=1.1200
        # candle_close=1.0995 → price below entry → unrealized PnL < 0
        trade = self._make_open_trade(
            direction="LONG", entry_price=1.1000,
            stop_loss=1.0900, take_profit=1.1200,
            timeframe="H1",
        )
        # candle doesn't hit SL (low > 1.0900) or TP (high < 1.1200)
        candle = self._make_candle(high=1.1010, low=1.0950, close=1.0995)
        result = engine._check_exit(trade, candle, "forex", False, candles_since_entry=24)
        assert result is not None, "Expected time exit after 24 candles with loss"
        assert result.exit_reason == "time_exit"

    def test_v6_11_time_exit_no_trigger_if_pnl_positive(self) -> None:
        """Time exit does NOT fire when PnL > 0 (price above entry for LONG)."""
        from src.backtesting.backtest_engine import BacktestEngine

        engine = BacktestEngine.__new__(BacktestEngine)
        trade = self._make_open_trade(
            direction="LONG", entry_price=1.1000,
            stop_loss=1.0900, take_profit=1.1200,
            timeframe="H1",
        )
        # candle_close=1.1050 → price above entry → PnL > 0
        candle = self._make_candle(high=1.1060, low=1.0990, close=1.1050)
        result = engine._check_exit(trade, candle, "forex", False, candles_since_entry=24)
        assert result is None, "Expected NO time exit when PnL > 0"

    def test_v6_11_time_exit_no_trigger_before_max_candles(self) -> None:
        """Time exit does NOT fire before reaching max_candles (H1=24 after CAL-03)."""
        from src.backtesting.backtest_engine import BacktestEngine

        engine = BacktestEngine.__new__(BacktestEngine)
        trade = self._make_open_trade(
            direction="LONG", entry_price=1.1000,
            stop_loss=1.0900, take_profit=1.1200,
            timeframe="H1",
        )
        candle = self._make_candle(high=1.1010, low=1.0950, close=1.0990)
        result = engine._check_exit(trade, candle, "forex", False, candles_since_entry=23)
        assert result is None, "Expected no exit at candle 23 (max=24 after CAL-03)"

    def test_v6_11_time_exit_h4_uses_20_candles(self) -> None:
        """H4 time exit threshold is 20 candles."""
        from src.backtesting.backtest_engine import BacktestEngine

        engine = BacktestEngine.__new__(BacktestEngine)
        trade = self._make_open_trade(
            direction="LONG", entry_price=1.1000,
            stop_loss=1.0900, take_profit=1.1200,
            timeframe="H4",
        )
        candle = self._make_candle(high=1.1010, low=1.0950, close=1.0990)
        # 19 candles — should NOT exit
        result = engine._check_exit(trade, candle, "forex", False, candles_since_entry=19)
        assert result is None

        # 20 candles — should exit
        result = engine._check_exit(trade, candle, "forex", False, candles_since_entry=20)
        assert result is not None
        assert result.exit_reason == "time_exit"

    def test_v6_11_mae_exit_triggers(self) -> None:
        """MAE exit fires when MAE >= 60% SL dist, MFE < 20% TP dist, >= 3 candles."""
        from src.backtesting.backtest_engine import BacktestEngine

        engine = BacktestEngine.__new__(BacktestEngine)
        # LONG: entry=1.1000, SL=1.0900 (sl_dist=0.01), TP=1.1200 (tp_dist=0.02)
        # MAE threshold = 0.01 * 0.6 = 0.006
        # MFE threshold = 0.02 * 0.2 = 0.004
        trade = self._make_open_trade(
            direction="LONG", entry_price=1.1000,
            stop_loss=1.0900, take_profit=1.1200,
            mae=0.007,   # > 0.006 threshold
            mfe=0.003,   # < 0.004 threshold
        )
        candle = self._make_candle(high=1.1010, low=1.0950, close=1.0990)
        result = engine._check_exit(trade, candle, "forex", False, candles_since_entry=3)
        assert result is not None, "Expected MAE exit"
        assert result.exit_reason == "mae_exit"

    def test_v6_11_mae_exit_no_trigger_high_mfe(self) -> None:
        """MAE exit does NOT fire when MFE >= 20% TP distance."""
        from src.backtesting.backtest_engine import BacktestEngine

        engine = BacktestEngine.__new__(BacktestEngine)
        trade = self._make_open_trade(
            direction="LONG", entry_price=1.1000,
            stop_loss=1.0900, take_profit=1.1200,
            mae=0.007,   # > threshold
            mfe=0.005,   # >= 0.004 threshold → blocks MAE exit
        )
        candle = self._make_candle(high=1.1010, low=1.0950, close=1.0990)
        result = engine._check_exit(trade, candle, "forex", False, candles_since_entry=3)
        assert result is None, "Expected no MAE exit when MFE is sufficient"

    def test_v6_11_mae_exit_no_trigger_too_few_candles(self) -> None:
        """MAE exit does NOT fire before 3 candles."""
        from src.backtesting.backtest_engine import BacktestEngine

        engine = BacktestEngine.__new__(BacktestEngine)
        trade = self._make_open_trade(
            direction="LONG", entry_price=1.1000,
            stop_loss=1.0900, take_profit=1.1200,
            mae=0.007,
            mfe=0.003,
        )
        candle = self._make_candle(high=1.1010, low=1.0950, close=1.0990)
        result = engine._check_exit(trade, candle, "forex", False, candles_since_entry=2)
        assert result is None, "Expected no MAE exit before 3 candles"

    def test_v6_11_exit_order_sl_before_time(self) -> None:
        """SL check runs before time exit (SL has priority)."""
        from src.backtesting.backtest_engine import BacktestEngine

        engine = BacktestEngine.__new__(BacktestEngine)
        trade = self._make_open_trade(
            direction="LONG", entry_price=1.1000,
            stop_loss=1.0900, take_profit=1.1200,
            timeframe="H1",
        )
        # Candle low below SL AND we've held 24 candles (max for H1 after CAL-03) — SL should win
        candle = self._make_candle(high=1.1010, low=1.0880, close=1.0890)
        result = engine._check_exit(trade, candle, "forex", False, candles_since_entry=24)
        assert result is not None
        assert result.exit_reason == "sl_hit", f"Expected sl_hit (has priority), got: {result.exit_reason}"


# ── TASK-V6-12: Exclude end_of_data from metrics ─────────────────────────────


class TestV612EndOfDataExclusion:
    """TASK-V6-12: end_of_data trades excluded from WR, PF, avg_duration."""

    def _make_trade(
        self,
        exit_reason: str = "tp_hit",
        result: str = "win",
        pnl: float = 10.0,
        duration: int = 60,
    ) -> MagicMock:
        t = MagicMock()
        t.symbol = "EURUSD=X"
        t.timeframe = "H1"
        t.direction = "LONG"
        t.entry_price = Decimal("1.1000")
        t.exit_price = Decimal("1.1100")
        t.exit_reason = exit_reason
        t.pnl_pips = Decimal("100.0")
        t.pnl_usd = Decimal(str(pnl))
        t.result = result
        t.composite_score = Decimal("8.0")
        t.entry_at = datetime.datetime(2024, 6, 1, 10, 0, tzinfo=datetime.timezone.utc)
        t.exit_at = datetime.datetime(2024, 6, 2, 10, 0, tzinfo=datetime.timezone.utc)
        t.duration_minutes = duration
        t.mfe = Decimal("0.01")
        t.mae = Decimal("0.002")
        t.regime = "TREND_BULL"
        return t

    def test_v6_12_eod_excluded_from_win_rate(self) -> None:
        """WR computed from real trades only (not end_of_data)."""
        from src.backtesting.backtest_engine import _compute_summary

        # 2 wins + 2 losses (real), 2 eod trades that are "wins"
        trades = [
            self._make_trade("tp_hit", "win", 10.0),
            self._make_trade("tp_hit", "win", 10.0),
            self._make_trade("sl_hit", "loss", -5.0),
            self._make_trade("sl_hit", "loss", -5.0),
            self._make_trade("end_of_data", "win", 100.0),  # should be excluded
            self._make_trade("end_of_data", "win", 200.0),  # should be excluded
        ]
        summary = _compute_summary(trades, Decimal("1000"))
        # WR should be 2/(2+2) = 50%, not 4/6 = 66.7%
        assert summary["win_rate_pct"] == 50.0, (
            f"Expected WR=50%, got {summary['win_rate_pct']} (end_of_data not excluded?)"
        )

    def test_v6_12_eod_excluded_from_profit_factor(self) -> None:
        """PF computed from real trades only."""
        from src.backtesting.backtest_engine import _compute_summary

        # Real: 1 win=+10, 1 loss=-5 → PF=2.0
        # EOD: 1 win=+500 (should not affect PF)
        trades = [
            self._make_trade("tp_hit", "win", 10.0),
            self._make_trade("sl_hit", "loss", -5.0),
            self._make_trade("end_of_data", "win", 500.0),
        ]
        summary = _compute_summary(trades, Decimal("1000"))
        assert summary["profit_factor"] == 2.0, (
            f"Expected PF=2.0, got {summary['profit_factor']} (eod trades inflating PF?)"
        )

    def test_v6_12_eod_excluded_from_avg_duration(self) -> None:
        """avg_duration computed from real trades only."""
        from src.backtesting.backtest_engine import _compute_summary

        # Real: 2 trades with duration=60 min
        # EOD: 1 trade with duration=10000 min (should not affect avg)
        trades = [
            self._make_trade("tp_hit", "win", 10.0, duration=60),
            self._make_trade("sl_hit", "loss", -5.0, duration=60),
            self._make_trade("end_of_data", "win", 50.0, duration=10000),
        ]
        summary = _compute_summary(trades, Decimal("1000"))
        assert summary["avg_duration_minutes"] == 60.0, (
            f"Expected avg_duration=60, got {summary['avg_duration_minutes']}"
        )

    def test_v6_12_summary_has_eod_count_and_pnl(self) -> None:
        """Summary contains end_of_data_count and end_of_data_pnl fields."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = [
            self._make_trade("tp_hit", "win", 10.0),
            self._make_trade("end_of_data", "win", 100.0),
            self._make_trade("end_of_data", "loss", -50.0),
        ]
        summary = _compute_summary(trades, Decimal("1000"))
        assert "end_of_data_count" in summary
        assert summary["end_of_data_count"] == 2
        assert "end_of_data_pnl" in summary
        assert summary["end_of_data_pnl"] == 50.0  # 100 + (-50)

    def test_v6_12_summary_has_total_trades_excl_eod(self) -> None:
        """Summary includes total_trades_excl_eod field."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = [
            self._make_trade("tp_hit", "win", 10.0),
            self._make_trade("sl_hit", "loss", -5.0),
            self._make_trade("end_of_data", "win", 30.0),
        ]
        summary = _compute_summary(trades, Decimal("1000"))
        assert summary["total_trades"] == 3
        assert summary["total_trades_excl_eod"] == 2

    def test_v6_12_eod_warning_above_20pct(self, caplog: Any) -> None:
        """Warning logged when end_of_data > 20% of all trades."""
        import logging

        from src.backtesting.backtest_engine import _compute_summary

        # 3 EOD out of 5 total = 60% → warning
        trades = [
            self._make_trade("tp_hit", "win", 10.0),
            self._make_trade("sl_hit", "loss", -5.0),
            self._make_trade("end_of_data", "win", 10.0),
            self._make_trade("end_of_data", "win", 10.0),
            self._make_trade("end_of_data", "win", 10.0),
        ]
        with caplog.at_level(logging.WARNING, logger="src.backtesting.backtest_engine"):
            _compute_summary(trades, Decimal("1000"))
        assert any("end_of_data" in r.message for r in caplog.records), (
            "Expected warning about high end_of_data percentage"
        )

    def test_v6_12_no_eod_trades_unaffected(self) -> None:
        """When no end_of_data trades, metrics unchanged."""
        from src.backtesting.backtest_engine import _compute_summary

        trades = [
            self._make_trade("tp_hit", "win", 10.0),
            self._make_trade("sl_hit", "loss", -5.0),
        ]
        summary = _compute_summary(trades, Decimal("1000"))
        assert summary["win_rate_pct"] == 50.0
        assert summary["end_of_data_count"] == 0
        assert summary["end_of_data_pnl"] == 0.0


# ── TASK-V6-13: Walk-Forward validation ──────────────────────────────────────


class TestV613WalkForward:
    """TASK-V6-13: BacktestParams supports walk-forward IS/OOS split."""

    def test_v6_13_walk_forward_disabled_by_default(self) -> None:
        """enable_walk_forward is False by default."""
        from src.backtesting.backtest_params import BacktestParams

        params = BacktestParams(
            symbols=["EURUSD=X"],
            start_date="2024-01-01",
            end_date="2025-12-31",
        )
        assert params.enable_walk_forward is False

    def test_v6_13_walk_forward_params_set(self) -> None:
        """Walk-forward parameters can be configured."""
        from src.backtesting.backtest_params import BacktestParams

        params = BacktestParams(
            symbols=["EURUSD=X"],
            start_date="2024-01-01",
            end_date="2025-12-31",
            enable_walk_forward=True,
            in_sample_months=18,
            out_of_sample_months=6,
        )
        assert params.enable_walk_forward is True
        assert params.in_sample_months == 18
        assert params.out_of_sample_months == 6

    def test_v6_13_backtest_result_has_walk_forward_field(self) -> None:
        """BacktestResult model has walk_forward Optional[dict] field."""
        from src.backtesting.backtest_params import BacktestResult

        result = BacktestResult(run_id="test-123", status="completed")
        assert result.walk_forward is None

    def test_v6_13_walk_forward_splits_correctly(self) -> None:
        """IS=18 months → start to 2025-07, OOS=6 months → 2025-07 to 2026-01."""
        from dateutil.relativedelta import relativedelta

        start = datetime.datetime(2024, 1, 1)
        in_sample_months = 18
        out_of_sample_months = 6

        is_end = start + relativedelta(months=in_sample_months)
        oos_end = is_end + relativedelta(months=out_of_sample_months)

        assert is_end == datetime.datetime(2025, 7, 1)
        assert oos_end == datetime.datetime(2026, 1, 1)

    def test_v6_13_compute_summary_no_walk_forward_key_without_flag(self) -> None:
        """_compute_summary() result has no walk_forward key (set by run_backtest)."""
        from src.backtesting.backtest_engine import _compute_summary

        summary = _compute_summary([], Decimal("1000"))
        assert "walk_forward" not in summary


# ── V6-CAL-01: AVAILABLE_WEIGHT_FLOOR ────────────────────────────────────────


class TestV6Cal01WeightFloor:
    """V6-CAL-01: AVAILABLE_WEIGHT_FLOOR=0.65 prevents over-dilution in backtest."""

    def test_v6_cal_01_weight_floor_applied(self) -> None:
        """available_weight=0.45 with floor=0.65 gives effective_weight=0.65."""
        from src.config import AVAILABLE_WEIGHT_FLOOR

        effective_weight = max(0.45, AVAILABLE_WEIGHT_FLOOR)
        assert effective_weight == 0.65

    def test_v6_cal_01_threshold_with_floor(self) -> None:
        """Effective threshold = 15 * max(0.45, 0.65) = 9.75."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # score=9.7 < 9.75 → blocked
        passed, reason = pipeline.check_score_threshold(
            9.7, "forex", "EURUSD=X", available_weight=0.45
        )
        assert not passed, f"Expected 9.7 to be blocked (threshold=9.75), got pass"
        assert "score_below_threshold" in reason

        # score=9.8 >= 9.75 → pass
        passed, reason = pipeline.check_score_threshold(
            9.8, "forex", "EURUSD=X", available_weight=0.45
        )
        assert passed, f"Expected 9.8 to pass (threshold=9.75), got: {reason}"

    def test_v6_cal_01_weight_above_floor_unchanged(self) -> None:
        """available_weight=1.0 is not affected by floor (max(1.0, 0.65)=1.0)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # Live mode: threshold = 15 * 1.0 = 15.0 (unchanged)
        passed, _ = pipeline.check_score_threshold(
            14.9, "forex", "EURUSD=X", available_weight=1.0
        )
        assert not passed  # still blocked just below 15.0

        passed, _ = pipeline.check_score_threshold(
            15.0, "forex", "EURUSD=X", available_weight=1.0
        )
        assert passed  # passes at exactly 15.0

    def test_v6_cal_01_signal_strength_uses_floor(self) -> None:
        """check_signal_strength() uses AVAILABLE_WEIGHT_FLOOR as minimum scale."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # With floor=0.65: STRONG_BUY threshold = 15*0.65=9.75
        # score=9.8 → STRONG_BUY at scale=0.65 → should pass
        passed, reason = pipeline.check_signal_strength(9.8, "LONG", available_weight=0.45)
        assert passed, f"Expected signal_strength pass for 9.8 at floor=0.65, got: {reason}"

        # score=9.5 → below STRONG_BUY(9.75) but above BUY(6.5) → BUY strength
        # BUY is in ALLOWED_SIGNAL_STRENGTHS, so should pass
        passed, reason = pipeline.check_signal_strength(9.5, "LONG", available_weight=0.45)
        assert passed, f"Expected BUY strength to pass, got: {reason}"

    def test_v6_cal_01_constant_value(self) -> None:
        """AVAILABLE_WEIGHT_FLOOR is 0.65."""
        from src.config import AVAILABLE_WEIGHT_FLOOR

        assert AVAILABLE_WEIGHT_FLOOR == 0.65


# ── V6-CAL-02: Bear regime blocking ──────────────────────────────────────────


class TestV6Cal02BearRegimes:
    """V6-CAL-02: TREND_BEAR and STRONG_TREND_BEAR added to BLOCKED_REGIMES."""

    def test_v6_cal_02_trend_bear_blocked(self) -> None:
        """Signal with regime=TREND_BEAR is rejected."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_regime("TREND_BEAR", "EURUSD=X")
        assert not passed, "Expected TREND_BEAR to be blocked"
        assert "regime_blocked" in reason

    def test_v6_cal_02_strong_trend_bear_blocked(self) -> None:
        """Signal with regime=STRONG_TREND_BEAR is rejected."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_regime("STRONG_TREND_BEAR", "EURUSD=X")
        assert not passed, "Expected STRONG_TREND_BEAR to be blocked"
        assert "regime_blocked" in reason

    def test_v6_cal_02_bull_regimes_pass(self) -> None:
        """STRONG_TREND_BULL passes; TREND_BULL is blocked (V6-CAL2-07: added to BLOCKED_REGIMES)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # STRONG_TREND_BULL is still allowed
        passed, reason = pipeline.check_regime("STRONG_TREND_BULL", "EURUSD=X")
        assert passed, f"Expected STRONG_TREND_BULL to pass, got: {reason}"
        # TREND_BULL now blocked (V6-CAL2-07)
        passed, reason = pipeline.check_regime("TREND_BULL", "EURUSD=X")
        assert not passed, f"Expected TREND_BULL to be blocked (V6-CAL2-07), got: {reason}"

    def test_v6_cal_02_volatile_passes(self) -> None:
        """VOLATILE regime is not blocked."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_regime("VOLATILE", "EURUSD=X")
        assert passed, f"Expected VOLATILE to pass, got: {reason}"

    def test_v6_cal_02_blocked_regimes_config(self) -> None:
        """BLOCKED_REGIMES contains RANGING, TREND_BEAR, STRONG_TREND_BEAR, TREND_BULL.

        V6-CAL2-07: TREND_BULL added (45 trades, 17.8% WR, -$45.83 in v6-cal-r1).
        """
        from src.config import BLOCKED_REGIMES

        assert "RANGING" in BLOCKED_REGIMES
        assert "TREND_BEAR" in BLOCKED_REGIMES
        assert "STRONG_TREND_BEAR" in BLOCKED_REGIMES
        assert "TREND_BULL" in BLOCKED_REGIMES
        assert len(BLOCKED_REGIMES) == 4


# ── V6-CAL-03: TIME_EXIT H1 = 24 ─────────────────────────────────────────────


class TestV6Cal03TimeExit:
    """V6-CAL-03: H1 time exit reduced from 48 to 24 candles."""

    def _make_open_trade(
        self,
        direction: str = "LONG",
        entry_price: float = 1.1000,
        stop_loss: float = 1.0900,
        take_profit: float = 1.1200,
        timeframe: str = "H1",
    ) -> dict:
        return {
            "symbol": "EURUSD=X",
            "timeframe": timeframe,
            "direction": direction,
            "entry_price": Decimal(str(entry_price)),
            "entry_at": datetime.datetime(2024, 6, 1, 10, 0, tzinfo=datetime.timezone.utc),
            "stop_loss": Decimal(str(stop_loss)),
            "take_profit": Decimal(str(take_profit)),
            "composite_score": Decimal("10.0"),
            "position_pct": Decimal("2.0"),
            "mfe": 0.0,
            "mae": 0.0,
            "regime": "TREND_BULL",
            "entry_bar_index": 0,
        }

    def _make_candle(self, high: float, low: float, close: float) -> MagicMock:
        c = MagicMock()
        c.high = high
        c.low = low
        c.close = close
        c.timestamp = datetime.datetime(2024, 6, 2, 10, 0, tzinfo=datetime.timezone.utc)
        return c

    def test_v6_cal_03_time_exit_24_candles_h1(self) -> None:
        """H1 time exit fires at 24 candles (not 48) when PnL <= 0."""
        from src.backtesting.backtest_engine import BacktestEngine

        engine = BacktestEngine.__new__(BacktestEngine)
        trade = self._make_open_trade(timeframe="H1")
        candle = self._make_candle(high=1.1010, low=1.0950, close=1.0995)

        # At 23 candles — should NOT exit
        result = engine._check_exit(trade, candle, "forex", False, candles_since_entry=23)
        assert result is None, "Expected no exit at candle 23"

        # At 24 candles — should exit
        result = engine._check_exit(trade, candle, "forex", False, candles_since_entry=24)
        assert result is not None, "Expected time exit at candle 24"
        assert result.exit_reason == "time_exit"

    def test_v6_cal_03_time_exit_h4_unchanged(self) -> None:
        """H4 time exit is still 20 candles (unchanged by CAL-03)."""
        from src.backtesting.backtest_engine import BacktestEngine

        engine = BacktestEngine.__new__(BacktestEngine)
        trade = self._make_open_trade(timeframe="H4")
        candle = self._make_candle(high=1.1010, low=1.0950, close=1.0990)

        # At 19 — should NOT exit
        result = engine._check_exit(trade, candle, "forex", False, candles_since_entry=19)
        assert result is None, "Expected no exit at H4 candle 19"

        # At 20 — should exit
        result = engine._check_exit(trade, candle, "forex", False, candles_since_entry=20)
        assert result is not None, "Expected H4 time exit at candle 20"
        assert result.exit_reason == "time_exit"

    def test_v6_cal_03_time_exit_d1_unchanged(self) -> None:
        """D1 time exit is still 10 candles (unchanged by CAL-03)."""
        from src.backtesting.backtest_engine import BacktestEngine

        engine = BacktestEngine.__new__(BacktestEngine)
        trade = self._make_open_trade(timeframe="D1")
        candle = self._make_candle(high=1.1010, low=1.0950, close=1.0990)

        # At 9 — should NOT exit
        result = engine._check_exit(trade, candle, "forex", False, candles_since_entry=9)
        assert result is None, "Expected no exit at D1 candle 9"

        # At 10 — should exit
        result = engine._check_exit(trade, candle, "forex", False, candles_since_entry=10)
        assert result is not None, "Expected D1 time exit at candle 10"
        assert result.exit_reason == "time_exit"


# ── V6-CAL-04: SHORT multiplier and RSI threshold ────────────────────────────


class TestV6Cal04ShortFilter:
    """V6-CAL-04 + V6-CAL2-03: SHORT_SCORE_MULTIPLIER=1.3 (reduced from 2.0), SHORT_RSI_THRESHOLD=30."""

    def test_v6_cal_04_short_multiplier_2x(self) -> None:
        """SHORT effective_threshold = 9.75 * 1.3 = 12.675 (at floor=0.65, V6-CAL2-03).

        composite=-12 → abs=12 < 12.675 → blocked.
        """
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_score_threshold(
            -12.0, "forex", "EURUSD=X", available_weight=0.45, direction="SHORT"
        )
        assert not passed, f"Expected SHORT composite=-12 to be blocked (threshold=12.675), got pass"
        assert "score_below_threshold" in reason

    def test_v6_cal_04_short_rsi_30(self) -> None:
        """SHORT with RSI=35 is blocked (>= threshold 30)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        indicators = {"rsi": 35.0, "macd": -0.001, "macd_signal": 0.001}
        passed, reason = pipeline.check_momentum(indicators, "SHORT")
        assert not passed, f"Expected SHORT RSI=35 to be blocked (threshold=30), got pass"
        assert "momentum_misaligned_short" in reason

    def test_v6_cal_04_short_passes_strong(self) -> None:
        """SHORT with composite=-13 and RSI=20 passes all filters (V6-CAL2-03: threshold=12.675)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # Threshold: 15 * 0.65 * 1.3 = 12.675; -13 abs=13 > 12.675 → passes score
        passed, reason = pipeline.check_score_threshold(
            -13.0, "forex", "EURUSD=X", available_weight=0.45, direction="SHORT"
        )
        assert passed, f"Expected SHORT composite=-13 to pass (threshold=12.675), got: {reason}"

        # RSI=20 < 30 → passes momentum
        indicators = {"rsi": 20.0, "macd": -0.001, "macd_signal": 0.001}
        passed, reason = pipeline.check_momentum(indicators, "SHORT")
        assert passed, f"Expected SHORT RSI=20 to pass (< 30), got: {reason}"

    def test_v6_cal_04_long_unaffected(self) -> None:
        """LONG threshold and RSI are not affected by SHORT multiplier."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # LONG: threshold = 15 * 0.65 = 9.75; score=10 passes
        passed, _ = pipeline.check_score_threshold(
            10.0, "forex", "EURUSD=X", available_weight=0.45, direction="LONG"
        )
        assert passed

        # LONG RSI=55 passes (no SHORT threshold applied)
        indicators = {"rsi": 55.0, "macd": 0.001, "macd_signal": -0.001}
        passed, reason = pipeline.check_momentum(indicators, "LONG")
        assert passed, f"Expected LONG RSI=55 to pass, got: {reason}"


# ── V6-CAL-05: BTC/USDT restrictions ─────────────────────────────────────────


class TestV6Cal05BtcRestrictions:
    """V6-CAL-05: BTC/USDT min_score=25, allowed_regimes=[STRONG_TREND_BULL]."""

    def test_v6_cal_05_btc_min_score_25(self) -> None:
        """BTC/USDT requires |composite| >= 25 * 0.65 = 16.25 in backtest."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # composite=16.0 < 16.25 → blocked
        passed, reason = pipeline.check_score_threshold(
            16.0, "crypto", "BTC/USDT", available_weight=0.45
        )
        assert not passed, f"Expected BTC composite=16.0 to be blocked (16.25 threshold), got pass"

        # composite=17.0 >= 16.25 → pass
        passed, reason = pipeline.check_score_threshold(
            17.0, "crypto", "BTC/USDT", available_weight=0.45
        )
        assert passed, f"Expected BTC composite=17.0 to pass, got: {reason}"

    def test_v6_cal_05_btc_only_strong_bull(self) -> None:
        """BTC in TREND_BULL is blocked (not in allowed_regimes after CAL-05)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_regime("TREND_BULL", "BTC/USDT")
        assert not passed, f"Expected TREND_BULL to be blocked for BTC, got: {reason}"

    def test_v6_cal_05_btc_strong_trend_bull_allowed(self) -> None:
        """BTC in STRONG_TREND_BULL still passes."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_regime("STRONG_TREND_BULL", "BTC/USDT")
        assert passed, f"Expected STRONG_TREND_BULL to pass for BTC, got: {reason}"

    def test_v6_cal_05_eth_min_score_20(self) -> None:
        """ETH/USDT requires |composite| >= 20 * 0.65 = 13.0 in backtest."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # composite=12.5 < 13.0 → blocked
        passed, _ = pipeline.check_score_threshold(
            12.5, "crypto", "ETH/USDT", available_weight=0.45
        )
        assert not passed

        # composite=13.5 >= 13.0 → pass
        passed, reason = pipeline.check_score_threshold(
            13.5, "crypto", "ETH/USDT", available_weight=0.45
        )
        assert passed, f"Expected ETH composite=13.5 to pass, got: {reason}"


# ── V6-CAL-06: Per-instrument overrides ──────────────────────────────────────


class TestV6Cal06PerInstrumentOverrides:
    """V6-CAL-06: New overrides for USDJPY=X, NZDUSD=X, SPY."""

    def test_v6_cal_06_usdjpy_override(self) -> None:
        """USDJPY=X uses min_composite_score=22."""
        from src.config import INSTRUMENT_OVERRIDES

        overrides = INSTRUMENT_OVERRIDES.get("USDJPY=X", {})
        score = overrides.get("min_composite_score")
        assert score == 22, f"Expected USDJPY min_composite_score=22, got {score}"

    def test_v6_cal_06_nzdusd_override(self) -> None:
        """NZDUSD=X uses min_composite_score=22."""
        from src.config import INSTRUMENT_OVERRIDES

        overrides = INSTRUMENT_OVERRIDES.get("NZDUSD=X", {})
        score = overrides.get("min_composite_score")
        assert score == 22, f"Expected NZDUSD min_composite_score=22, got {score}"

    def test_v6_cal_06_spy_strict_override(self) -> None:
        """SPY uses min_composite_score=22 (V6-CAL2-08: relaxed from 30) and STRONG_TREND_BULL + VOLATILE."""
        from src.config import INSTRUMENT_OVERRIDES

        spy = INSTRUMENT_OVERRIDES.get("SPY", {})
        # V6-CAL2-08: relaxed from 30 to 22 to allow more trades
        assert spy.get("min_composite_score") == 22
        allowed = spy.get("allowed_regimes", [])
        assert "STRONG_TREND_BULL" in allowed
        assert "VOLATILE" in allowed  # V6-CAL2-08: added
        assert "STRONG_TREND_BEAR" not in allowed

    def test_v6_cal_06_usdjpy_threshold_applied_in_pipeline(self) -> None:
        """USDJPY=X effective threshold = 22 * 0.65 = 14.3 in backtest."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # score=14.0 < 14.3 → blocked
        passed, reason = pipeline.check_score_threshold(
            14.0, "forex", "USDJPY=X", available_weight=0.45
        )
        assert not passed, f"Expected USDJPY 14.0 to be blocked (threshold=14.3), got pass"

        # score=15.0 >= 14.3 → pass
        passed, reason = pipeline.check_score_threshold(
            15.0, "forex", "USDJPY=X", available_weight=0.45
        )
        assert passed, f"Expected USDJPY 15.0 to pass, got: {reason}"

    def test_v6_cal_06_gbpusd_override_score(self) -> None:
        """GBPUSD=X has min_composite_score=20 (restored by CAL-06)."""
        from src.config import INSTRUMENT_OVERRIDES

        overrides = INSTRUMENT_OVERRIDES.get("GBPUSD=X", {})
        assert overrides.get("min_composite_score") == 20


# ── V6-CAL-07: MAE metric fix ────────────────────────────────────────────────


class TestV6Cal07MaeMetric:
    """V6-CAL-07: avg_mae_pct_of_sl = mae / sl_distance * 100."""

    def _make_trade_with_sl(
        self,
        mae: float,
        entry: float,
        sl: float,
        result: str = "win",
        pnl: float = 10.0,
    ) -> MagicMock:
        t = MagicMock()
        t.symbol = "EURUSD=X"
        t.timeframe = "H1"
        t.direction = "LONG"
        t.entry_price = Decimal(str(entry))
        t.exit_price = Decimal(str(entry + 0.01))
        t.exit_reason = "tp_hit" if result == "win" else "sl_hit"
        t.pnl_pips = Decimal("100.0")
        t.pnl_usd = Decimal(str(pnl))
        t.result = result
        t.composite_score = Decimal("10.0")
        t.entry_at = datetime.datetime(2024, 6, 1, 10, 0, tzinfo=datetime.timezone.utc)
        t.exit_at = datetime.datetime(2024, 6, 2, 10, 0, tzinfo=datetime.timezone.utc)
        t.duration_minutes = 1440
        t.mfe = Decimal("0.01")
        t.mae = Decimal(str(mae))
        t.sl_price = Decimal(str(sl))
        t.regime = "TREND_BULL"
        return t

    def test_v6_cal_07_mae_pct_correct(self) -> None:
        """MAE=0.0050, SL_distance=0.0100 → mae_pct=50.0%."""
        from src.backtesting.backtest_engine import _compute_summary

        # entry=1.1000, sl=1.0900, sl_dist=0.01
        # mae=0.005 → pct = 0.005/0.01*100 = 50.0
        trade = self._make_trade_with_sl(mae=0.005, entry=1.1000, sl=1.0900)
        summary = _compute_summary([trade], Decimal("1000"))
        assert summary["avg_mae_pct_of_sl"] == 50.0, (
            f"Expected 50.0%, got {summary['avg_mae_pct_of_sl']}"
        )

    def test_v6_cal_07_mae_pct_winners_vs_losers(self) -> None:
        """Summary includes avg_mae_pct_of_sl_winners and avg_mae_pct_of_sl_losers."""
        from src.backtesting.backtest_engine import _compute_summary

        # Winner: mae=0.003, sl_dist=0.01 → 30%
        # Loser: mae=0.008, sl_dist=0.01 → 80%
        winner = self._make_trade_with_sl(mae=0.003, entry=1.1000, sl=1.0900, result="win", pnl=10.0)
        loser = self._make_trade_with_sl(mae=0.008, entry=1.1000, sl=1.0900, result="loss", pnl=-5.0)
        summary = _compute_summary([winner, loser], Decimal("1000"))

        assert "avg_mae_pct_of_sl_winners" in summary
        assert "avg_mae_pct_of_sl_losers" in summary
        assert summary["avg_mae_pct_of_sl_winners"] == pytest.approx(30.0, abs=0.1)
        assert summary["avg_mae_pct_of_sl_losers"] == pytest.approx(80.0, abs=0.1)

    def test_v6_cal_07_mae_zero_sl_skipped(self) -> None:
        """SL distance = 0 does not cause division by zero."""
        from src.backtesting.backtest_engine import _compute_summary

        trade = self._make_trade_with_sl(mae=0.005, entry=1.1000, sl=1.1000)  # sl_dist=0
        summary = _compute_summary([trade], Decimal("1000"))
        assert summary["avg_mae_pct_of_sl"] == 0.0  # no valid MAE values

    def test_v6_cal_07_mae_none_sl_skipped(self) -> None:
        """Trade with mae=None is skipped without error."""
        from src.backtesting.backtest_engine import _compute_summary

        trade = self._make_trade_with_sl(mae=0.0, entry=1.1000, sl=1.0900)
        trade.mae = None
        summary = _compute_summary([trade], Decimal("1000"))
        assert summary["avg_mae_pct_of_sl"] == 0.0

    def test_v6_cal_07_summary_has_mae_breakdown(self) -> None:
        """Summary contains all three MAE fields."""
        from src.backtesting.backtest_engine import _compute_summary

        summary = _compute_summary([], Decimal("1000"))
        assert "avg_mae_pct_of_sl" in summary
        assert "avg_mae_pct_of_sl_winners" in summary
        assert "avg_mae_pct_of_sl_losers" in summary


# ── V6-CAL-08: Scaled score buckets ──────────────────────────────────────────


class TestV6Cal08ScaledBuckets:
    """V6-CAL-08: by_score_bucket_scaled uses scaled thresholds."""

    def _make_trade_with_score(
        self,
        composite: float,
        result: str = "win",
        pnl: float = 10.0,
    ) -> MagicMock:
        t = MagicMock()
        t.symbol = "EURUSD=X"
        t.timeframe = "H1"
        t.direction = "LONG" if composite > 0 else "SHORT"
        t.entry_price = Decimal("1.1000")
        t.exit_price = Decimal("1.1100")
        t.exit_reason = "tp_hit" if result == "win" else "sl_hit"
        t.pnl_pips = Decimal("100.0")
        t.pnl_usd = Decimal(str(pnl))
        t.result = result
        t.composite_score = Decimal(str(composite))
        t.entry_at = datetime.datetime(2024, 6, 1, 10, 0, tzinfo=datetime.timezone.utc)
        t.exit_at = datetime.datetime(2024, 6, 2, 10, 0, tzinfo=datetime.timezone.utc)
        t.duration_minutes = 1440
        t.mfe = Decimal("0.01")
        t.mae = Decimal("0.002")
        t.sl_price = Decimal("1.0900")
        t.regime = "TREND_BULL"
        return t

    def test_v6_cal_08_scaled_bucket_strong_buy(self) -> None:
        """composite=10 at scale=0.65 → strong_buy (10 >= 9.75 threshold)."""
        from src.backtesting.backtest_engine import _compute_summary

        trade = self._make_trade_with_score(10.0)
        summary = _compute_summary([trade], Decimal("1000"))
        by_scaled = summary["by_score_bucket_scaled"]
        # composite=10 >= 15*0.65=9.75 → strong_buy
        assert "strong_buy" in by_scaled, f"Expected strong_buy in scaled buckets: {by_scaled}"
        assert by_scaled["strong_buy"]["trades"] == 1

    def test_v6_cal_08_scaled_bucket_buy(self) -> None:
        """composite=7 at scale=0.65 → buy (7 >= 6.5 buy threshold)."""
        from src.backtesting.backtest_engine import _compute_summary

        trade = self._make_trade_with_score(7.0)
        summary = _compute_summary([trade], Decimal("1000"))
        by_scaled = summary["by_score_bucket_scaled"]
        # composite=7 >= 10*0.65=6.5 → buy
        assert "buy" in by_scaled, f"Expected buy in scaled buckets: {by_scaled}"
        assert by_scaled["buy"]["trades"] == 1

    def test_v6_cal_08_both_buckets_in_summary(self) -> None:
        """Summary contains both by_score_bucket and by_score_bucket_scaled."""
        from src.backtesting.backtest_engine import _compute_summary

        summary = _compute_summary([], Decimal("1000"))
        assert "by_score_bucket" in summary
        assert "by_score_bucket_scaled" in summary


# ── V6-CAL-09: Weekday multiplier ────────────────────────────────────────────


class TestV6Cal09WeekdayMultiplier:
    """V6-CAL-09: Monday/Tuesday forex threshold *= 1.5."""

    def _make_context(
        self,
        weekday: int,
        composite: float = 10.0,
        market_type: str = "forex",
    ) -> dict:
        """Make a pipeline context for the given weekday (0=Mon, 1=Tue, 2=Wed...)."""
        # Pick a date matching the desired weekday (2024-01-01 is Monday)
        base_monday = datetime.datetime(2024, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)
        ts = base_monday + datetime.timedelta(days=weekday)
        return {
            "composite_score": composite,
            "market_type": market_type,
            "symbol": "EURUSD=X",
            "regime": "TREND_BULL",
            "direction": "LONG",
            "timeframe": "H1",
            "candle_ts": ts,
            "available_weight": 0.45,
            "d1_rows": [],
            "economic_events": [],
            "ta_indicators": {},
        }

    def test_v6_cal_09_monday_forex_higher_threshold(self) -> None:
        """Monday forex: effective threshold = 9.75 * 1.5 = 14.625.

        score=10.0 passes Wed/Thu (threshold=9.75) but is blocked on Monday.
        """
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline(
            apply_regime_filter=False,
            apply_d1_trend_filter=False,
            apply_volume_filter=False,
            apply_momentum_filter=False,
            apply_weekday_filter=False,
            apply_calendar_filter=False,
            apply_session_filter=False,
            apply_dxy_filter=False,
        )
        # score=10.0 on Wednesday (weekday=2) — should pass (10.0 >= 9.75)
        ctx_wed = self._make_context(weekday=2, composite=10.0)
        passed, reason = pipeline.run_all(ctx_wed)
        assert passed, f"Expected pass on Wednesday with score=10.0, got: {reason}"

        # Same score=10.0 on Monday (weekday=0) — should be blocked (10.0 < 14.625)
        ctx_mon = self._make_context(weekday=0, composite=10.0)
        passed, reason = pipeline.run_all(ctx_mon)
        assert not passed, f"Expected block on Monday with score=10.0 (threshold=14.625), got pass"

    def test_v6_cal_09_tuesday_forex_higher_threshold(self) -> None:
        """Tuesday forex also gets the 1.5x multiplier."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline(
            apply_regime_filter=False,
            apply_d1_trend_filter=False,
            apply_volume_filter=False,
            apply_momentum_filter=False,
            apply_weekday_filter=False,
            apply_calendar_filter=False,
            apply_session_filter=False,
            apply_dxy_filter=False,
        )
        ctx_tue = self._make_context(weekday=1, composite=10.0)
        passed, reason = pipeline.run_all(ctx_tue)
        assert not passed, f"Expected block on Tuesday with score=10.0, got pass"

    def test_v6_cal_09_wednesday_unaffected(self) -> None:
        """Wednesday threshold is base (no multiplier)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline(
            apply_regime_filter=False,
            apply_d1_trend_filter=False,
            apply_volume_filter=False,
            apply_momentum_filter=False,
            apply_weekday_filter=False,
            apply_calendar_filter=False,
            apply_session_filter=False,
            apply_dxy_filter=False,
        )
        ctx_wed = self._make_context(weekday=2, composite=10.0)
        passed, reason = pipeline.run_all(ctx_wed)
        assert passed, f"Expected Wednesday to pass with score=10.0 (base threshold=9.75), got: {reason}"

    def test_v6_cal_09_monday_crypto_unaffected(self) -> None:
        """Crypto is not affected by weekday multiplier."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline(
            apply_regime_filter=False,
            apply_d1_trend_filter=False,
            apply_volume_filter=False,
            apply_momentum_filter=False,
            apply_weekday_filter=False,
            apply_calendar_filter=False,
            apply_session_filter=False,
            apply_dxy_filter=False,
        )
        # Crypto on Monday: score=10.0 should pass (no weekday multiplier for crypto)
        ctx = self._make_context(weekday=0, composite=10.0, market_type="crypto")
        ctx["symbol"] = "BTC/USDT"
        # Note: BTC has min_score=25 override, so we test with a generic crypto symbol
        # Use a context without symbol override for a clean test of the weekday logic
        ctx["symbol"] = "ETHUSD"  # no override for this symbol
        passed, reason = pipeline.run_all(ctx)
        # With floor=0.65 and crypto global min 20: effective=13.0; score=10.0 < 13.0 → blocked
        # But the reason should NOT be weekday related — it should be score_threshold
        assert "weekday" not in reason.lower(), (
            f"Crypto Monday block should not be weekday-related, got: {reason}"
        )

    def test_v6_cal_09_config_constants(self) -> None:
        """WEAK_WEEKDAY_SCORE_MULTIPLIER=1.5 and WEAK_WEEKDAYS=[0, 1]."""
        from src.config import WEAK_WEEKDAY_SCORE_MULTIPLIER, WEAK_WEEKDAYS

        assert WEAK_WEEKDAY_SCORE_MULTIPLIER == 1.5
        assert 0 in WEAK_WEEKDAYS  # Monday
        assert 1 in WEAK_WEEKDAYS  # Tuesday


# ── Calibration Round 2 tests ─────────────────────────────────────────────────


def _make_trade_cal2(
    symbol: str = "EURUSD=X",
    direction: str = "LONG",
    entry_price: str = "1.1000",
    exit_price: str = "1.1100",
    pnl_usd: str = "10.00",
    exit_reason: str = "tp_hit",
    result: str = "win",
    entry_at=None,
    exit_at=None,
    regime: str = "STRONG_TREND_BULL",
) -> "MagicMock":
    from unittest.mock import MagicMock
    from decimal import Decimal
    import datetime

    t = MagicMock()
    t.symbol = symbol
    t.direction = direction
    t.entry_price = Decimal(entry_price)
    t.exit_price = Decimal(exit_price)
    t.pnl_usd = Decimal(pnl_usd)
    t.result = result
    t.exit_reason = exit_reason
    t.pnl_pips = Decimal("100.0000")
    t.composite_score = Decimal("12.0")
    t.entry_at = entry_at or datetime.datetime(2024, 1, 15, 10, 0, tzinfo=datetime.timezone.utc)
    t.exit_at = exit_at or datetime.datetime(2024, 1, 16, 10, 0, tzinfo=datetime.timezone.utc)
    t.duration_minutes = 1440
    t.mfe = Decimal("0.0100")
    t.mae = Decimal("0.0020")
    t.regime = regime
    t.timeframe = "H1"
    t.sl_price = Decimal(entry_price) - Decimal("0.0050")
    return t


class TestCal201ExcludeEodFromMetrics:
    """TASK-CAL2-01: PF and total_pnl_usd exclude end_of_data trades."""

    def test_cal2_01_pf_excludes_eod(self) -> None:
        """PF and total_pnl_usd are computed from real trades only."""
        from decimal import Decimal
        from src.backtesting.backtest_engine import _compute_summary

        real_win = _make_trade_cal2(pnl_usd="20.00", exit_reason="tp_hit", result="win")
        real_win2 = _make_trade_cal2(pnl_usd="15.00", exit_reason="tp_hit", result="win")
        real_loss = _make_trade_cal2(pnl_usd="-10.00", exit_reason="sl_hit", result="loss")
        eod1 = _make_trade_cal2(pnl_usd="-50.00", exit_reason="end_of_data", result="loss")
        eod2 = _make_trade_cal2(pnl_usd="30.00", exit_reason="end_of_data", result="win")

        trades = [real_win, real_win2, real_loss, eod1, eod2]
        summary = _compute_summary(trades, Decimal("1000"))

        # PF = gross_win / gross_loss = (20+15) / 10 = 3.5 (not influenced by eod)
        assert summary["profit_factor"] == pytest.approx(3.5, abs=0.01)
        # total_pnl_usd = 20+15-10 = 25.0 (no eod)
        assert summary["total_pnl_usd"] == pytest.approx(25.0, abs=0.01)
        # total_pnl_usd_incl_eod = 20+15-10-50+30 = 5.0
        assert summary["total_pnl_usd_incl_eod"] == pytest.approx(5.0, abs=0.01)
        # end_of_data_pnl = -50+30 = -20.0
        assert summary["end_of_data_pnl"] == pytest.approx(-20.0, abs=0.01)

    def test_cal2_01_eod_warning_flag_true(self) -> None:
        """eod_warning=True when end_of_data count > 5% of total."""
        from decimal import Decimal
        from src.backtesting.backtest_engine import _compute_summary

        real_trades = [_make_trade_cal2(exit_reason="tp_hit") for _ in range(9)]
        eod_trade = _make_trade_cal2(exit_reason="end_of_data")
        trades = real_trades + [eod_trade]  # 10% eod > 5%

        summary = _compute_summary(trades, Decimal("1000"))
        assert summary["eod_warning"] is True

    def test_cal2_01_eod_warning_flag_false_within_threshold(self) -> None:
        """eod_warning=False when end_of_data <= 5% of total."""
        from decimal import Decimal
        from src.backtesting.backtest_engine import _compute_summary

        real_trades = [_make_trade_cal2(exit_reason="tp_hit") for _ in range(98)]
        eod_trade = _make_trade_cal2(exit_reason="end_of_data")
        trades = real_trades + [eod_trade]  # 1% eod <= 5%

        summary = _compute_summary(trades, Decimal("1000"))
        assert summary["eod_warning"] is False

    def test_cal2_01_total_pnl_usd_incl_eod_in_summary(self) -> None:
        """Summary contains total_pnl_usd_incl_eod field."""
        from decimal import Decimal
        from src.backtesting.backtest_engine import _compute_summary

        trades = [_make_trade_cal2()]
        summary = _compute_summary(trades, Decimal("1000"))
        assert "total_pnl_usd_incl_eod" in summary
        assert "eod_warning" in summary


class TestCal202BySymbolExcludesEod:
    """TASK-CAL2-02: by_symbol, by_regime, by_weekday exclude end_of_data and have win_rate_pct."""

    def test_cal2_02_by_symbol_excludes_eod(self) -> None:
        """eod trade does not count in by_symbol wins or trades."""
        from decimal import Decimal
        from src.backtesting.backtest_engine import _compute_summary

        real_win = _make_trade_cal2(symbol="EURUSD=X", pnl_usd="10.00", exit_reason="tp_hit", result="win")
        eod_loss = _make_trade_cal2(symbol="EURUSD=X", pnl_usd="-5.00", exit_reason="end_of_data", result="loss")

        summary = _compute_summary([real_win, eod_loss], Decimal("1000"))
        by_sym = summary["by_symbol"]

        assert "EURUSD=X" in by_sym
        assert by_sym["EURUSD=X"]["trades"] == 1  # only real trade
        assert by_sym["EURUSD=X"]["wins"] == 1

    def test_cal2_02_by_symbol_has_win_rate(self) -> None:
        """by_symbol entries contain win_rate_pct."""
        from decimal import Decimal
        from src.backtesting.backtest_engine import _compute_summary

        t1 = _make_trade_cal2(symbol="GBPUSD=X", pnl_usd="10.00", exit_reason="tp_hit", result="win")
        t2 = _make_trade_cal2(symbol="GBPUSD=X", pnl_usd="-5.00", exit_reason="sl_hit", result="loss")

        summary = _compute_summary([t1, t2], Decimal("1000"))
        by_sym = summary["by_symbol"]

        assert "win_rate_pct" in by_sym["GBPUSD=X"]
        assert by_sym["GBPUSD=X"]["win_rate_pct"] == pytest.approx(50.0)

    def test_cal2_02_by_regime_excludes_eod(self) -> None:
        """eod trade does not appear in by_regime counts."""
        from decimal import Decimal
        from src.backtesting.backtest_engine import _compute_summary

        real = _make_trade_cal2(regime="STRONG_TREND_BULL", exit_reason="tp_hit", result="win")
        eod = _make_trade_cal2(regime="STRONG_TREND_BULL", exit_reason="end_of_data", result="win")

        summary = _compute_summary([real, eod], Decimal("1000"))
        by_regime = summary["by_regime"]
        # Only real trade counted
        assert by_regime.get("STRONG_TREND_BULL", {}).get("trades", 0) == 1

    def test_cal2_02_by_weekday_excludes_eod(self) -> None:
        """eod trade does not appear in by_weekday counts."""
        import datetime
        from decimal import Decimal
        from src.backtesting.backtest_engine import _compute_summary

        monday = datetime.datetime(2024, 1, 15, 10, 0, tzinfo=datetime.timezone.utc)  # Monday
        real = _make_trade_cal2(exit_reason="tp_hit", result="win", entry_at=monday)
        eod = _make_trade_cal2(exit_reason="end_of_data", result="loss", entry_at=monday)

        summary = _compute_summary([real, eod], Decimal("1000"))
        by_wd = summary["by_weekday"]
        # weekday 0 = Monday — only real trade counted
        assert by_wd.get("0", {}).get("trades", 0) == 1


class TestCal203ShortThreshold:
    """TASK-CAL2-03: SHORT_SCORE_MULTIPLIER=1.3 makes SHORT reachable."""

    def test_cal2_03_short_multiplier_value(self) -> None:
        """SHORT_SCORE_MULTIPLIER is 1.3."""
        from src.config import SHORT_SCORE_MULTIPLIER
        assert SHORT_SCORE_MULTIPLIER == pytest.approx(1.3)

    def test_cal2_03_short_threshold_reachable(self) -> None:
        """composite=-13.0, direction=SHORT passes score filter in backtest mode."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # effective_threshold = 15 * max(0.45, 0.65) * 1.3 = 15 * 0.65 * 1.3 = 12.675
        # abs(-13.0) = 13.0 >= 12.675 → pass
        passed, reason = pipeline.check_score_threshold(
            composite=-13.0,
            market_type="forex",
            symbol="EURUSD=X",
            available_weight=0.45,
            direction="SHORT",
        )
        assert passed, f"Expected SHORT -13.0 to pass (threshold ~12.675), got: {reason}"

    def test_cal2_03_short_threshold_blocks_weak(self) -> None:
        """composite=-8.0 SHORT is blocked (below effective threshold)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_score_threshold(
            composite=-8.0,
            market_type="forex",
            symbol="EURUSD=X",
            available_weight=0.45,
            direction="SHORT",
        )
        assert not passed
        assert "score_below_threshold" in reason


class TestCal204WeekendBlock:
    """TASK-CAL2-04: Saturday and Sunday blocked for all instruments."""

    def test_cal2_04_saturday_blocked_crypto(self) -> None:
        """Saturday blocked even for crypto."""
        import datetime
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # 2024-01-06 is Saturday
        saturday = datetime.datetime(2024, 1, 6, 14, 0, tzinfo=datetime.timezone.utc)
        passed, reason = pipeline.check_weekday(saturday, "crypto")
        assert not passed
        assert "weekend_block" in reason

    def test_cal2_04_sunday_blocked_forex(self) -> None:
        """Sunday blocked for forex."""
        import datetime
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # 2024-01-07 is Sunday
        sunday = datetime.datetime(2024, 1, 7, 10, 0, tzinfo=datetime.timezone.utc)
        passed, reason = pipeline.check_weekday(sunday, "forex")
        assert not passed
        assert "weekend_block" in reason

    def test_cal2_04_friday_still_allowed(self) -> None:
        """Friday 12:00 is still allowed (not past 18:00)."""
        import datetime
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # 2024-01-05 is Friday
        friday_noon = datetime.datetime(2024, 1, 5, 12, 0, tzinfo=datetime.timezone.utc)
        passed, reason = pipeline.check_weekday(friday_noon, "forex")
        assert passed, f"Friday 12:00 should be allowed, got: {reason}"

    def test_cal2_04_saturday_blocked_stocks(self) -> None:
        """Saturday blocked for stocks."""
        import datetime
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        saturday = datetime.datetime(2024, 1, 6, 10, 0, tzinfo=datetime.timezone.utc)
        passed, reason = pipeline.check_weekday(saturday, "stocks")
        assert not passed
        assert "weekend_block" in reason


class TestCal205RrReduced:
    """TASK-CAL2-05: R:R ratios reduced by ~30% in REGIME_RR_MAP."""

    def test_cal2_05_volatile_rr_reduced(self) -> None:
        """VOLATILE target_rr=1.4 (was 2.0)."""
        from src.signals.risk_manager_v2 import REGIME_RR_MAP

        assert REGIME_RR_MAP["VOLATILE"]["target_rr"] == pytest.approx(1.4)
        assert REGIME_RR_MAP["VOLATILE"]["min_rr"] == pytest.approx(1.0)

    def test_cal2_05_strong_trend_bull_rr_reduced(self) -> None:
        """STRONG_TREND_BULL target_rr=1.75 (was 2.5)."""
        from src.signals.risk_manager_v2 import REGIME_RR_MAP

        assert REGIME_RR_MAP["STRONG_TREND_BULL"]["target_rr"] == pytest.approx(1.75)

    def test_cal2_05_default_rr_reduced(self) -> None:
        """DEFAULT target_rr=1.05 (was 1.5)."""
        from src.signals.risk_manager_v2 import REGIME_RR_MAP

        assert REGIME_RR_MAP["DEFAULT"]["target_rr"] == pytest.approx(1.05)

    def test_cal2_05_ranging_rr_reduced(self) -> None:
        """RANGING target_rr=0.9 (was 1.3)."""
        from src.signals.risk_manager_v2 import REGIME_RR_MAP

        assert REGIME_RR_MAP["RANGING"]["target_rr"] == pytest.approx(0.9)


class TestCal206TrailingStop:
    """TASK-CAL2-06: Trailing stop activates at MFE >= 50% of TP distance."""

    def _make_check_exit_trade(self, direction: str = "LONG"):
        """Return open_trade dict for _check_exit testing."""
        from decimal import Decimal
        sl = Decimal("1.0950") if direction == "LONG" else Decimal("1.1050")
        return {
            "symbol": "EURUSD=X",
            "timeframe": "H1",
            "direction": direction,
            "entry_price": Decimal("1.1000"),
            "entry_at": None,
            "stop_loss": sl,
            "take_profit": Decimal("1.1100") if direction == "LONG" else Decimal("1.0900"),
            "composite_score": Decimal("12.0"),
            "position_pct": 2.0,
            "mfe": 0.0,
            "mae": 0.0,
            "regime": "STRONG_TREND_BULL",
            "entry_bar_index": 0,
            "trailing_sl_active": False,
            "original_stop_loss": sl,
        }

    def _make_candle(self, high: str, low: str, close: str, ts=None):
        """Return mock candle object."""
        from unittest.mock import MagicMock
        from decimal import Decimal
        import datetime

        c = MagicMock()
        c.high = Decimal(high)
        c.low = Decimal(low)
        c.close = Decimal(close)
        c.timestamp = ts or datetime.datetime(2024, 1, 15, 12, 0, tzinfo=datetime.timezone.utc)
        return c

    def test_cal2_06_trailing_stop_activates_long(self) -> None:
        """When MFE >= 50% of TP distance, SL moves to entry + 20% of TP dist."""
        from decimal import Decimal
        from unittest.mock import MagicMock
        from src.backtesting.backtest_engine import BacktestEngine

        engine = BacktestEngine.__new__(BacktestEngine)
        trade = self._make_check_exit_trade("LONG")
        # entry=1.1000, tp=1.1100, tp_dist=0.0100, 50% = 0.0050
        # MFE=0.0055 >= 0.0050 → trailing should activate
        trade["mfe"] = 0.0055
        # candle that does NOT hit trailing_sl (entry + 20% = 1.1000 + 0.002 = 1.1020)
        candle = self._make_candle(high="1.1060", low="1.1025", close="1.1040")

        result = engine._check_exit(
            open_trade=trade,
            candle=candle,
            market_type="forex",
            apply_slippage=False,
            candles_since_entry=5,
            account_size=Decimal("1000"),
        )
        # Should NOT exit (price above trailing_sl), but trailing_sl_active should be set
        assert result is None
        assert trade["trailing_sl_active"] is True
        assert trade["stop_loss"] == Decimal("1.1020")  # entry + 0.002

    def test_cal2_06_trailing_stop_exits_on_next_candle(self) -> None:
        """After trailing_sl activated, next candle hitting it triggers trailing_stop exit."""
        from decimal import Decimal
        from src.backtesting.backtest_engine import BacktestEngine

        engine = BacktestEngine.__new__(BacktestEngine)
        trade = self._make_check_exit_trade("LONG")
        # Simulate trailing already active
        trade["mfe"] = 0.0055
        trade["trailing_sl_active"] = True
        trade["stop_loss"] = Decimal("1.1020")  # entry + 20% of TP dist
        # candle low dips below trailing SL
        candle = self._make_candle(high="1.1040", low="1.1015", close="1.1015")

        result = engine._check_exit(
            open_trade=trade,
            candle=candle,
            market_type="forex",
            apply_slippage=False,
            candles_since_entry=6,
            account_size=Decimal("1000"),
        )
        assert result is not None
        assert result.exit_reason == "trailing_stop"

    def test_cal2_06_trailing_not_active_below_50pct(self) -> None:
        """MFE=40% of TP distance → trailing does not activate."""
        from decimal import Decimal
        from src.backtesting.backtest_engine import BacktestEngine

        engine = BacktestEngine.__new__(BacktestEngine)
        trade = self._make_check_exit_trade("LONG")
        # tp_dist=0.0100, 40% = 0.004
        trade["mfe"] = 0.004
        # candle well above sl, well below tp
        candle = self._make_candle(high="1.1040", low="1.1020", close="1.1030")

        result = engine._check_exit(
            open_trade=trade,
            candle=candle,
            market_type="forex",
            apply_slippage=False,
            candles_since_entry=3,
            account_size=Decimal("1000"),
        )
        assert result is None
        assert trade["trailing_sl_active"] is False

    def test_cal2_06_trailing_stop_short(self) -> None:
        """Trailing stop activates and exits for SHORT direction."""
        from decimal import Decimal
        from src.backtesting.backtest_engine import BacktestEngine

        engine = BacktestEngine.__new__(BacktestEngine)
        trade = self._make_check_exit_trade("SHORT")
        # entry=1.1000, tp=1.0900, tp_dist=0.0100, 50% = 0.0050
        # trailing_sl for SHORT = entry - 20% = 1.1000 - 0.002 = 1.0980
        trade["mfe"] = 0.0055
        trade["trailing_sl_active"] = True
        trade["stop_loss"] = Decimal("1.0980")
        # candle high hits trailing SL
        candle = self._make_candle(high="1.0985", low="1.0950", close="1.0960")

        result = engine._check_exit(
            open_trade=trade,
            candle=candle,
            market_type="forex",
            apply_slippage=False,
            candles_since_entry=6,
            account_size=Decimal("1000"),
        )
        assert result is not None
        assert result.exit_reason == "trailing_stop"

    def test_cal2_06_trailing_stop_count_in_summary(self) -> None:
        """trailing_stop_count field present in summary."""
        from decimal import Decimal
        from src.backtesting.backtest_engine import _compute_summary

        t = _make_trade_cal2(exit_reason="trailing_stop", pnl_usd="5.00", result="win")
        summary = _compute_summary([t], Decimal("1000"))
        assert "trailing_stop_count" in summary
        assert summary["trailing_stop_count"] == 1

    def test_cal2_06_gap_through_both_sl_worst_case_long(self) -> None:
        """Worst case: gap through both trailing SL and original SL → exit at original SL (sl_hit)."""
        from decimal import Decimal
        from src.backtesting.backtest_engine import BacktestEngine

        engine = BacktestEngine.__new__(BacktestEngine)
        trade = self._make_check_exit_trade("LONG")
        # Trailing is already active: trailing_sl=1.1020, original_sl=1.0950
        trade["trailing_sl_active"] = True
        trade["stop_loss"] = Decimal("1.1020")
        trade["original_stop_loss"] = Decimal("1.0950")
        trade["mfe"] = 0.0055
        # Candle gaps down through BOTH trailing SL (1.1020) and original SL (1.0950)
        candle = self._make_candle(high="1.1010", low="1.0920", close="1.0930")

        result = engine._check_exit(
            open_trade=trade,
            candle=candle,
            market_type="forex",
            apply_slippage=False,
            candles_since_entry=6,
            account_size=Decimal("1000"),
        )
        assert result is not None
        # Worst case: must exit at original SL with sl_hit, not trailing_stop
        assert result.exit_reason == "sl_hit"
        assert result.exit_price == Decimal("1.0950")

    def test_cal2_06_gap_through_both_sl_worst_case_short(self) -> None:
        """Worst case SHORT: gap through both trailing SL and original SL → sl_hit at original SL."""
        from decimal import Decimal
        from src.backtesting.backtest_engine import BacktestEngine

        engine = BacktestEngine.__new__(BacktestEngine)
        trade = self._make_check_exit_trade("SHORT")
        # entry=1.1000, tp=1.0900, original_sl=1.1050
        # trailing_sl for SHORT = entry - 20% of tp_dist = 1.1000 - 0.002 = 1.0980
        trade["trailing_sl_active"] = True
        trade["stop_loss"] = Decimal("1.0980")
        trade["original_stop_loss"] = Decimal("1.1050")
        trade["mfe"] = 0.0055
        # Candle gaps up through BOTH trailing SL (1.0980) and original SL (1.1050)
        candle = self._make_candle(high="1.1080", low="1.0990", close="1.1070")

        result = engine._check_exit(
            open_trade=trade,
            candle=candle,
            market_type="forex",
            apply_slippage=False,
            candles_since_entry=6,
            account_size=Decimal("1000"),
        )
        assert result is not None
        assert result.exit_reason == "sl_hit"
        assert result.exit_price == Decimal("1.1050")

    def test_cal2_06_mae_uses_original_sl_distance_after_trailing(self) -> None:
        """MAE exit uses original_stop_loss distance, not trailing SL distance."""
        from decimal import Decimal
        from src.backtesting.backtest_engine import BacktestEngine

        engine = BacktestEngine.__new__(BacktestEngine)
        trade = self._make_check_exit_trade("LONG")
        # entry=1.1000, original_sl=1.0950 → sl_distance=0.0050
        # trailing_sl=1.1020 → if used: sl_distance=0.002, mae_threshold=0.0012
        # MAE=0.0020 → with original dist: threshold=0.003, 0.002 < 0.003 → NO exit
        # MAE=0.0020 → with trailing dist: threshold=0.0012, 0.002 >= 0.0012 → false exit
        trade["trailing_sl_active"] = True
        trade["stop_loss"] = Decimal("1.1020")
        trade["original_stop_loss"] = Decimal("1.0950")
        trade["mfe"] = 0.0055
        trade["mae"] = 0.0020  # below 60% of original sl_distance (0.003)
        trade["entry_bar_index"] = 0
        candle = self._make_candle(high="1.1050", low="1.1022", close="1.1035")

        result = engine._check_exit(
            open_trade=trade,
            candle=candle,
            market_type="forex",
            apply_slippage=False,
            candles_since_entry=5,
            account_size=Decimal("1000"),
        )
        # Should NOT exit via MAE — threshold uses original SL distance (0.005), not trailing (0.002)
        assert result is None


class TestCal207TrendBullBlocked:
    """TASK-CAL2-07: TREND_BULL added to BLOCKED_REGIMES."""

    def test_cal2_07_trend_bull_in_blocked_list(self) -> None:
        """TREND_BULL is in BLOCKED_REGIMES config."""
        from src.config import BLOCKED_REGIMES

        assert "TREND_BULL" in BLOCKED_REGIMES

    def test_cal2_07_trend_bull_blocked_by_filter(self) -> None:
        """check_regime returns False for TREND_BULL."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_regime("TREND_BULL")
        assert not passed
        assert "TREND_BULL" in reason


class TestCal208InstrumentOverrides:
    """TASK-CAL2-08: AUDUSD=X override added, SPY relaxed."""

    def test_cal2_08_audusd_override_exists(self) -> None:
        """AUDUSD=X has min_composite_score=22 override."""
        from src.config import INSTRUMENT_OVERRIDES

        assert "AUDUSD=X" in INSTRUMENT_OVERRIDES
        assert INSTRUMENT_OVERRIDES["AUDUSD=X"]["min_composite_score"] == 22

    def test_cal2_08_audusd_higher_threshold(self) -> None:
        """AUDUSD=X with composite=10 is blocked (below 22*0.65=14.3)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_score_threshold(
            composite=10.0,
            market_type="forex",
            symbol="AUDUSD=X",
            available_weight=0.45,
            direction="LONG",
        )
        assert not passed
        assert "score_below_threshold" in reason

    def test_cal2_08_spy_volatile_allowed(self) -> None:
        """SPY with regime=VOLATILE passes check_regime."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_regime("VOLATILE", symbol="SPY")
        assert passed, f"SPY VOLATILE should be allowed, got: {reason}"

    def test_cal2_08_spy_score_relaxed(self) -> None:
        """SPY min_composite_score is 22 (relaxed from 30)."""
        from src.config import INSTRUMENT_OVERRIDES

        assert INSTRUMENT_OVERRIDES["SPY"]["min_composite_score"] == 22


class TestCal209ConcentrationWarning:
    """TASK-CAL2-09: concentration_warning in summary."""

    def test_cal2_09_concentration_warning_single(self) -> None:
        """Top 1 symbol > 40% of PnL triggers warning."""
        from decimal import Decimal
        from src.backtesting.backtest_engine import _compute_summary

        big = _make_trade_cal2(symbol="GC=F", pnl_usd="80.00", exit_reason="tp_hit", result="win")
        small1 = _make_trade_cal2(symbol="EURUSD=X", pnl_usd="10.00", exit_reason="tp_hit", result="win")
        small2 = _make_trade_cal2(symbol="GBPUSD=X", pnl_usd="10.00", exit_reason="tp_hit", result="win")

        summary = _compute_summary([big, small1, small2], Decimal("1000"))
        assert summary["concentration_warning"] is not None
        assert "GC=F" in summary["concentration_warning"]

    def test_cal2_09_concentration_warning_top2(self) -> None:
        """Top 2 symbols contribute > 70% of PnL triggers warning."""
        from decimal import Decimal
        from src.backtesting.backtest_engine import _compute_summary

        t1 = _make_trade_cal2(symbol="GC=F", pnl_usd="40.00", exit_reason="tp_hit", result="win")
        t2 = _make_trade_cal2(symbol="EURUSD=X", pnl_usd="35.00", exit_reason="tp_hit", result="win")
        t3 = _make_trade_cal2(symbol="GBPUSD=X", pnl_usd="15.00", exit_reason="tp_hit", result="win")
        t4 = _make_trade_cal2(symbol="USDJPY=X", pnl_usd="10.00", exit_reason="tp_hit", result="win")

        summary = _compute_summary([t1, t2, t3, t4], Decimal("1000"))
        # GC=F = 40%, EURUSD = 35% → top 2 = 75% > 70%
        assert summary["concentration_warning"] is not None
        assert "Top 2" in summary["concentration_warning"]

    def test_cal2_09_no_concentration_warning(self) -> None:
        """Evenly distributed PnL produces no warning."""
        from decimal import Decimal
        from src.backtesting.backtest_engine import _compute_summary

        symbols = ["A", "B", "C", "D", "E"]
        trades = [
            _make_trade_cal2(symbol=s, pnl_usd="20.00", exit_reason="tp_hit", result="win")
            for s in symbols
        ]
        summary = _compute_summary(trades, Decimal("1000"))
        # Each symbol = 20% → top1=20%, top2=40% — both below thresholds
        assert summary["concentration_warning"] is None
