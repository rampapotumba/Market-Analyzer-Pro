"""
Prometheus metrics for Market Analyzer Pro.

Exposes custom business metrics alongside the default FastAPI HTTP metrics
provided by prometheus-fastapi-instrumentator.
"""

import logging
from typing import Optional

from prometheus_client import Counter, Gauge, Histogram, Summary, Info

logger = logging.getLogger(__name__)

# ── HTTP Instrumentation ──────────────────────────────────────────────────────
# (handled by prometheus-fastapi-instrumentator in setup_metrics())

# ── Signal Metrics ────────────────────────────────────────────────────────────

signals_generated_total = Counter(
    "signals_generated_total",
    "Total number of trading signals generated",
    ["market", "direction", "strength"],
)

signals_active = Gauge(
    "signals_active",
    "Number of currently active (open) signals",
    ["market"],
)

signal_composite_score = Histogram(
    "signal_composite_score",
    "Distribution of composite scores for generated signals",
    ["market", "direction"],
    buckets=[-100, -75, -60, -45, -30, -15, 0, 15, 30, 45, 60, 75, 100],
)

signal_confidence = Histogram(
    "signal_confidence_percent",
    "Distribution of signal confidence values",
    buckets=[0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
)

# ── Collector Metrics ─────────────────────────────────────────────────────────

collector_runs_total = Counter(
    "collector_runs_total",
    "Total number of collector runs",
    ["collector", "status"],  # status: success | error
)

collector_latency_seconds = Histogram(
    "collector_latency_seconds",
    "Time spent per collector run",
    ["collector"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60, 120],
)

collector_up = Gauge(
    "collector_up",
    "Whether a collector is currently healthy (1=up, 0=down)",
    ["collector"],
)

# ── Portfolio Metrics ─────────────────────────────────────────────────────────

portfolio_heat_percent = Gauge(
    "portfolio_heat_percent",
    "Current portfolio heat as a percentage of account risk",
)

open_positions_total = Gauge(
    "open_positions_total",
    "Number of currently open virtual positions",
    ["market"],
)

realized_pnl_pips = Summary(
    "realized_pnl_pips",
    "P&L in pips for closed signals",
    ["market", "exit_reason"],
)

# ── Circuit Breaker Metrics ───────────────────────────────────────────────────

circuit_breaker_state = Gauge(
    "circuit_breaker_state",
    "Circuit breaker state: 0=CLOSED, 1=OPEN, 2=HALF_OPEN",
    ["name"],
)

circuit_breaker_failures_total = Counter(
    "circuit_breaker_failures_total",
    "Total failures recorded by circuit breaker",
    ["name"],
)

# ── Data Quality Metrics ──────────────────────────────────────────────────────

data_quality_issues_total = Counter(
    "data_quality_issues_total",
    "Total number of data quality issues detected",
    ["symbol", "kind", "severity"],
)

data_freshness_seconds = Gauge(
    "data_freshness_seconds",
    "Age of the most recent price bar in seconds",
    ["symbol", "timeframe"],
)

# ── Backtest Metrics ──────────────────────────────────────────────────────────

backtest_runs_total = Counter(
    "backtest_runs_total",
    "Total number of backtest runs",
    ["instrument", "status"],
)

backtest_sharpe = Gauge(
    "backtest_sharpe_ratio",
    "Most recent out-of-sample Sharpe ratio per instrument",
    ["instrument"],
)

# ── Application Info ──────────────────────────────────────────────────────────

app_info = Info(
    "market_analyzer_pro",
    "Market Analyzer Pro version and build information",
)
app_info.info({"version": "2.0.0", "phase": "2"})


# ── Helper Functions ──────────────────────────────────────────────────────────


def record_signal(
    market: str,
    direction: str,
    strength: str,
    composite_score: float,
    confidence: float,
) -> None:
    """Record a newly generated signal in Prometheus metrics."""
    signals_generated_total.labels(
        market=market, direction=direction, strength=strength
    ).inc()
    signal_composite_score.labels(market=market, direction=direction).observe(
        composite_score
    )
    signal_confidence.observe(confidence)


def record_collector_run(
    name: str, success: bool, latency: Optional[float] = None
) -> None:
    """Record a collector run outcome."""
    status = "success" if success else "error"
    collector_runs_total.labels(collector=name, status=status).inc()
    collector_up.labels(collector=name).set(1 if success else 0)
    if latency is not None:
        collector_latency_seconds.labels(collector=name).observe(latency)


def record_circuit_breaker(name: str, state_value: int, failed: bool = False) -> None:
    """Update circuit breaker metrics. state_value: 0=CLOSED, 1=OPEN, 2=HALF_OPEN."""
    circuit_breaker_state.labels(name=name).set(state_value)
    if failed:
        circuit_breaker_failures_total.labels(name=name).inc()
