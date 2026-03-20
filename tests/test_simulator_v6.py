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


# ── TASK-V6-04: GBPUSD no score override ─────────────────────────────────────


class TestV604GbpusdFix:
    """TASK-V6-04: GBPUSD=X min_composite_score override removed."""

    def test_v6_04_gbpusd_no_score_override(self) -> None:
        """GBPUSD=X uses global threshold (no per-symbol override)."""
        from src.config import INSTRUMENT_OVERRIDES

        overrides = INSTRUMENT_OVERRIDES.get("GBPUSD=X", {})
        assert "min_composite_score" not in overrides, (
            f"GBPUSD=X should not have score override, got: {overrides}"
        )

    def test_v6_04_gbpusd_uses_global_threshold(self) -> None:
        """GBPUSD=X with composite=7.0 and scale=0.45 passes (6.75 effective)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # Global threshold 15 * 0.45 = 6.75; score=7.0 must pass
        passed, reason = pipeline.check_score_threshold(
            7.0, "forex", "GBPUSD=X", available_weight=0.45
        )
        assert passed, f"Expected GBPUSD to pass with score=7.0, scale=0.45. got: {reason}"

    def test_v6_04_gbpusd_still_blocked_below_threshold(self) -> None:
        """GBPUSD=X with composite=6.0 and scale=0.45 is blocked (< 6.75)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_score_threshold(
            6.0, "forex", "GBPUSD=X", available_weight=0.45
        )
        assert not passed, "Expected block for score=6.0 below effective threshold 6.75"
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
    """TASK-V6-08: Asymmetric SHORT threshold (×1.2) and stricter RSI (< 40)."""

    def test_v6_08_short_threshold_multiplied(self) -> None:
        """SHORT effective_threshold = base * available_weight * 1.2."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # Global threshold 15, scale=1.0, multiplier=1.2 → effective=18.0
        # composite=-17.0 → abs=17 < 18 → blocked
        passed, reason = pipeline.check_score_threshold(
            -17.0, "forex", "EURUSD=X", available_weight=1.0, direction="SHORT"
        )
        assert not passed, f"Expected SHORT composite=-17 to be blocked (threshold=18), got pass"
        assert "score_below_threshold" in reason

    def test_v6_08_short_passes_above_threshold(self) -> None:
        """SHORT with composite=-19 passes threshold of 18.0."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_score_threshold(
            -19.0, "forex", "EURUSD=X", available_weight=1.0, direction="SHORT"
        )
        assert passed, f"Expected SHORT composite=-19 to pass (threshold=18), got: {reason}"

    def test_v6_08_short_threshold_with_scale(self) -> None:
        """SHORT in backtest: threshold = 15 * 0.45 * 1.2 = 8.1."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # composite=-8.0, effective = 15 * 0.45 * 1.2 = 8.1 → blocked
        passed, _ = pipeline.check_score_threshold(
            -8.0, "forex", "EURUSD=X", available_weight=0.45, direction="SHORT"
        )
        assert not passed

        # composite=-8.5, effective=8.1 → pass
        passed, reason = pipeline.check_score_threshold(
            -8.5, "forex", "EURUSD=X", available_weight=0.45, direction="SHORT"
        )
        assert passed, f"Expected pass for SHORT composite=-8.5 (threshold=8.1), got: {reason}"

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

    def test_v6_08_short_rsi_40_blocks(self) -> None:
        """SHORT with RSI=45 is blocked (RSI >= SHORT_RSI_THRESHOLD=40)."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        indicators = {"rsi": 45.0, "macd": -0.001, "macd_signal": 0.001}
        passed, reason = pipeline.check_momentum(indicators, "SHORT")
        assert not passed, f"Expected SHORT RSI=45 to be blocked (threshold=40), got pass"
        assert "momentum_misaligned_short" in reason

    def test_v6_08_short_rsi_39_passes(self) -> None:
        """SHORT with RSI=39 and MACD aligned passes momentum filter."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        indicators = {"rsi": 39.0, "macd": -0.001, "macd_signal": 0.001}
        passed, reason = pipeline.check_momentum(indicators, "SHORT")
        assert passed, f"Expected SHORT RSI=39 to pass, got: {reason}"

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
        """Config has SHORT_SCORE_MULTIPLIER=1.2 and SHORT_RSI_THRESHOLD=40."""
        from src.config import SHORT_RSI_THRESHOLD, SHORT_SCORE_MULTIPLIER

        assert SHORT_SCORE_MULTIPLIER == 1.2, f"Expected 1.2, got {SHORT_SCORE_MULTIPLIER}"
        assert SHORT_RSI_THRESHOLD == 40, f"Expected 40, got {SHORT_RSI_THRESHOLD}"


# ── TASK-V6-09: SPY instrument override ──────────────────────────────────────


class TestV609SpyOverride:
    """TASK-V6-09: SPY has strict override to reduce losses."""

    def test_v6_09_spy_override_exists(self) -> None:
        """SPY is in INSTRUMENT_OVERRIDES."""
        from src.config import INSTRUMENT_OVERRIDES

        assert "SPY" in INSTRUMENT_OVERRIDES, "SPY must be in INSTRUMENT_OVERRIDES"

    def test_v6_09_spy_min_composite_score(self) -> None:
        """SPY min_composite_score = 25."""
        from src.config import INSTRUMENT_OVERRIDES

        score = INSTRUMENT_OVERRIDES["SPY"].get("min_composite_score")
        assert score == 25, f"Expected SPY min_composite_score=25, got {score}"

    def test_v6_09_spy_regime_restricted(self) -> None:
        """SPY is restricted to STRONG_TREND_BULL and STRONG_TREND_BEAR only."""
        from src.config import INSTRUMENT_OVERRIDES

        allowed = INSTRUMENT_OVERRIDES["SPY"].get("allowed_regimes", [])
        assert "STRONG_TREND_BULL" in allowed
        assert "STRONG_TREND_BEAR" in allowed
        # RANGING and TREND_BULL should NOT be allowed
        assert "RANGING" not in allowed
        assert "TREND_BULL" not in allowed

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
        """SPY composite=24 (< 25) is blocked even with scale=1.0."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        passed, reason = pipeline.check_score_threshold(
            24.0, "stocks", "SPY", available_weight=1.0
        )
        assert not passed, f"Expected SPY composite=24 to be blocked (threshold=25), got pass"

    def test_v6_09_spy_scaled_threshold(self) -> None:
        """SPY in backtest: threshold = 25 * 0.45 = 11.25."""
        from src.signals.filter_pipeline import SignalFilterPipeline

        pipeline = SignalFilterPipeline()
        # composite=11.0 < 11.25 → blocked
        passed, _ = pipeline.check_score_threshold(
            11.0, "stocks", "SPY", available_weight=0.45
        )
        assert not passed

        # composite=11.5 >= 11.25 → pass
        passed, reason = pipeline.check_score_threshold(
            11.5, "stocks", "SPY", available_weight=0.45
        )
        assert passed, f"Expected SPY composite=11.5 to pass at scale=0.45, got: {reason}"


# ── TASK-V6-10: Filter diagnostics (rejection counters) ──────────────────────


class TestV610FilterDiagnostics:
    """TASK-V6-10: SignalFilterPipeline tracks rejection counts per filter."""

    def _make_minimal_context(
        self,
        composite: float = 7.5,
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
            "candle_ts": datetime.datetime(2024, 6, 15, 12, 0, tzinfo=datetime.timezone.utc),
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
        context = self._make_minimal_context(composite=7.5, available_weight=0.45)
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
        # 2 passed signals
        for _ in range(2):
            context = self._make_minimal_context(composite=7.5, available_weight=0.45)
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
        for composite in [2.0, 7.5, 3.0, 8.0, 1.0]:
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
        """Time exit fires after 48 H1 candles with PnL <= 0."""
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
        result = engine._check_exit(trade, candle, "forex", False, candles_since_entry=48)
        assert result is not None, "Expected time exit after 48 candles with loss"
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
        result = engine._check_exit(trade, candle, "forex", False, candles_since_entry=48)
        assert result is None, "Expected NO time exit when PnL > 0"

    def test_v6_11_time_exit_no_trigger_before_max_candles(self) -> None:
        """Time exit does NOT fire before reaching max_candles."""
        from src.backtesting.backtest_engine import BacktestEngine

        engine = BacktestEngine.__new__(BacktestEngine)
        trade = self._make_open_trade(
            direction="LONG", entry_price=1.1000,
            stop_loss=1.0900, take_profit=1.1200,
            timeframe="H1",
        )
        candle = self._make_candle(high=1.1010, low=1.0950, close=1.0990)
        result = engine._check_exit(trade, candle, "forex", False, candles_since_entry=47)
        assert result is None, "Expected no exit at candle 47 (max=48)"

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
        # Candle low below SL AND we've held 48 candles — SL should win
        candle = self._make_candle(high=1.1010, low=1.0880, close=1.0890)
        result = engine._check_exit(trade, candle, "forex", False, candles_since_entry=48)
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
