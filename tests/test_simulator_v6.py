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
        """Threshold 15 with scale=0.45 → effective=6.75."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # composite=7.0, scale=0.45: |7.0| >= 6.75 → pass
        passed, reason = pipeline.check_score_threshold(7.0, "forex", "EURUSD=X", available_weight=0.45)
        assert passed, f"Expected pass, got: {reason}"

    def test_v6_02_scaled_threshold_backtest_blocked(self) -> None:
        """composite=6.0 with scale=0.45 → effective=6.75 → blocked."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_score_threshold(6.0, "forex", "EURUSD=X", available_weight=0.45)
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
        """STRONG_BUY at composite=7.0 with scale=0.45 (threshold=6.75)."""
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
        """Override min_composite_score=20 with scale=0.45 → effective=9.0."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # BTC/USDT has min_composite_score=15 after TASK-V6-03 change,
        # but USDCHF=X still has 18 → effective = 18*0.45 = 8.1
        # score=8.0 should be blocked
        passed, reason = pipeline.check_score_threshold(8.0, "forex", "USDCHF=X", available_weight=0.45)
        assert not passed, f"Expected block, got pass. reason={reason}"

    def test_v6_02_instrument_override_scaled_pass(self) -> None:
        """Override min_composite_score=18 with scale=0.45 → effective=8.1, score=8.5 passes."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_score_threshold(8.5, "forex", "USDCHF=X", available_weight=0.45)
        assert passed, f"Expected pass, got: {reason}"

    def test_v6_02_crypto_threshold_scaled(self) -> None:
        """Crypto threshold 15 (after TASK-V6-03 BTC gets 15) * 0.45 = 6.75."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # BTC/USDT override = 15, scale=0.45 → effective=6.75
        passed, _ = pipeline.check_score_threshold(7.0, "crypto", "BTC/USDT", available_weight=0.45)
        assert passed

        passed, _ = pipeline.check_score_threshold(6.0, "crypto", "BTC/USDT", available_weight=0.45)
        assert not passed

    def test_v6_02_available_weight_from_filter_context(self) -> None:
        """run_all() passes available_weight from context into check_score_threshold."""
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
            "composite_score": 7.5,   # above 15*0.45=6.75 but below 15*1.0=15
            "market_type": "forex",
            "symbol": "EURUSD=X",
            "regime": "TREND_BULL",
            "direction": "LONG",
            "timeframe": "H1",
            "available_weight": 0.45,
        }
        passed, reason = pipeline.run_all(context)
        assert passed, f"Expected pass with scale=0.45, got: {reason}"

        # Same score with live scale (1.0) should be blocked
        context["available_weight"] = 1.0
        passed, reason = pipeline.run_all(context)
        assert not passed, "Expected block with scale=1.0 (score 7.5 < threshold 15)"

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
        """TREND_BULL is allowed for BTC/USDT."""
        from src.config import INSTRUMENT_OVERRIDES

        overrides = INSTRUMENT_OVERRIDES.get("BTC/USDT", {})
        allowed = overrides.get("allowed_regimes", [])
        assert "TREND_BULL" in allowed, f"TREND_BULL not in BTC allowed_regimes: {allowed}"
        assert "TREND_BEAR" in allowed, f"TREND_BEAR not in BTC allowed_regimes: {allowed}"

    def test_v6_03_btc_strong_trend_still_allowed(self) -> None:
        """STRONG_TREND_BULL/BEAR still allowed for BTC/USDT."""
        from src.config import INSTRUMENT_OVERRIDES

        overrides = INSTRUMENT_OVERRIDES.get("BTC/USDT", {})
        allowed = overrides.get("allowed_regimes", [])
        assert "STRONG_TREND_BULL" in allowed
        assert "STRONG_TREND_BEAR" in allowed

    def test_v6_03_btc_threshold_lowered(self) -> None:
        """BTC/USDT min_composite_score is 15 (was 20)."""
        from src.config import INSTRUMENT_OVERRIDES

        overrides = INSTRUMENT_OVERRIDES.get("BTC/USDT", {})
        score = overrides.get("min_composite_score")
        assert score == 15, f"Expected 15, got {score}"

    def test_v6_03_eth_threshold_lowered(self) -> None:
        """ETH/USDT min_composite_score is 15 (was 20)."""
        from src.config import INSTRUMENT_OVERRIDES

        overrides = INSTRUMENT_OVERRIDES.get("ETH/USDT", {})
        score = overrides.get("min_composite_score")
        assert score == 15, f"Expected 15, got {score}"

    def test_v6_03_btc_trend_bull_passes_regime_filter(self) -> None:
        """SignalFilterPipeline allows BTC/USDT in TREND_BULL."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_regime("TREND_BULL", "BTC/USDT")
        assert passed, f"Expected TREND_BULL to pass for BTC/USDT, got: {reason}"

    def test_v6_03_btc_ranging_still_blocked(self) -> None:
        """RANGING is still blocked for BTC/USDT."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # RANGING is in BLOCKED_REGIMES globally
        passed, reason = pipeline.check_regime("RANGING", "BTC/USDT")
        assert not passed, "Expected RANGING to be blocked for BTC/USDT"

    def test_v6_03_gbpusd_no_score_override(self) -> None:
        """GBPUSD=X has no min_composite_score override (TASK-V6-04 prep)."""
        from src.config import INSTRUMENT_OVERRIDES

        overrides = INSTRUMENT_OVERRIDES.get("GBPUSD=X", {})
        assert "min_composite_score" not in overrides, (
            f"GBPUSD=X should not have score override, got: {overrides}"
        )

    def test_v6_03_score_component_weights_defined(self) -> None:
        """SCORE_COMPONENT_WEIGHTS sums to 1.0."""
        from src.config import SCORE_COMPONENT_WEIGHTS

        total = sum(SCORE_COMPONENT_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"Weights should sum to 1.0, got {total}"
        assert "ta" in SCORE_COMPONENT_WEIGHTS
        assert SCORE_COMPONENT_WEIGHTS["ta"] == 0.45
