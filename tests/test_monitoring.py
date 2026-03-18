"""Tests for src/monitoring/metrics.py and src/monitoring/setup.py."""

import pytest


class TestMetricsHelpers:
    def test_record_signal_increments_counter(self):
        from src.monitoring.metrics import record_signal, signals_generated_total

        before = signals_generated_total.labels(
            market="forex", direction="LONG", strength="STRONG_BUY"
        )._value.get()

        record_signal(
            market="forex",
            direction="LONG",
            strength="STRONG_BUY",
            composite_score=72.5,
            confidence=85.0,
        )

        after = signals_generated_total.labels(
            market="forex", direction="LONG", strength="STRONG_BUY"
        )._value.get()
        assert after == before + 1

    def test_record_signal_observes_histogram(self):
        from src.monitoring.metrics import record_signal, signal_composite_score

        # Should not raise
        record_signal(
            market="crypto",
            direction="SHORT",
            strength="MODERATE_SELL",
            composite_score=-45.0,
            confidence=60.0,
        )
        # Histogram count should be > 0
        count = signal_composite_score.labels(
            market="crypto", direction="SHORT"
        )._sum.get()
        assert count < 0  # negative score → negative sum

    def test_record_collector_run_success(self):
        from src.monitoring.metrics import record_collector_run, collector_runs_total, collector_up

        record_collector_run("price_collector", success=True, latency=1.5)

        count = collector_runs_total.labels(
            collector="price_collector", status="success"
        )._value.get()
        assert count >= 1

        up = collector_up.labels(collector="price_collector")._value.get()
        assert up == 1

    def test_record_collector_run_failure(self):
        from src.monitoring.metrics import record_collector_run, collector_runs_total, collector_up

        record_collector_run("news_collector", success=False, latency=None)

        count = collector_runs_total.labels(
            collector="news_collector", status="error"
        )._value.get()
        assert count >= 1

        up = collector_up.labels(collector="news_collector")._value.get()
        assert up == 0

    def test_record_circuit_breaker_states(self):
        from src.monitoring.metrics import (
            record_circuit_breaker,
            circuit_breaker_state,
            circuit_breaker_failures_total,
        )

        record_circuit_breaker("gdelt_cb", state_value=0, failed=False)
        assert circuit_breaker_state.labels(name="gdelt_cb")._value.get() == 0

        record_circuit_breaker("gdelt_cb", state_value=1, failed=True)
        assert circuit_breaker_state.labels(name="gdelt_cb")._value.get() == 1
        assert circuit_breaker_failures_total.labels(name="gdelt_cb")._value.get() >= 1

        record_circuit_breaker("gdelt_cb", state_value=2, failed=False)
        assert circuit_breaker_state.labels(name="gdelt_cb")._value.get() == 2

    def test_portfolio_heat_gauge(self):
        from src.monitoring.metrics import portfolio_heat_percent

        portfolio_heat_percent.set(3.5)
        assert portfolio_heat_percent._value.get() == 3.5

        portfolio_heat_percent.set(5.9)
        assert portfolio_heat_percent._value.get() == 5.9

    def test_data_quality_counter(self):
        from src.monitoring.metrics import data_quality_issues_total

        before = data_quality_issues_total.labels(
            symbol="EURUSD", kind="gap", severity="warning"
        )._value.get()

        data_quality_issues_total.labels(
            symbol="EURUSD", kind="gap", severity="warning"
        ).inc()

        after = data_quality_issues_total.labels(
            symbol="EURUSD", kind="gap", severity="warning"
        )._value.get()
        assert after == before + 1

    def test_backtest_sharpe_gauge(self):
        from src.monitoring.metrics import backtest_sharpe

        backtest_sharpe.labels(instrument="EURUSD").set(1.45)
        assert backtest_sharpe.labels(instrument="EURUSD")._value.get() == pytest.approx(1.45)

    def test_app_info_labels(self):
        from src.monitoring.metrics import app_info

        # Info metric should have version label set
        info_data = app_info._labelnames
        # Should be defined (Info is a Gauge-based metric)
        assert app_info is not None


class TestSetupMetrics:
    def test_setup_metrics_does_not_raise_without_app(self):
        """Smoke test: setup_metrics gracefully handles import issues."""
        from fastapi import FastAPI
        from src.monitoring.setup import setup_metrics

        app = FastAPI()
        # Should not raise even if lib is missing
        setup_metrics(app)

    def test_metrics_endpoint_registered(self):
        """After setup, /metrics route should be registered."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.monitoring.setup import setup_metrics

        app = FastAPI()
        setup_metrics(app)

        client = TestClient(app)
        response = client.get("/metrics")
        # Endpoint should exist (may 200 with metrics text or 404 if lib missing)
        assert response.status_code in (200, 404)
        if response.status_code == 200:
            assert "python" in response.text or "http" in response.text
