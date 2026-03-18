"""Load tests for Market Analyzer Pro REST API.

Usage:
    pip install locust
    locust -f tests/load/locustfile.py --host=http://localhost:8000

Or headless (CI):
    locust -f tests/load/locustfile.py \
        --host=http://localhost:8000 \
        --headless \
        --users 50 \
        --spawn-rate 5 \
        --run-time 60s \
        --html reports/load_report.html

Target SLOs (from docs/SPEC.md):
  P95 latency < 500ms for read endpoints
  P95 latency < 2000ms for signal generation
  Error rate < 0.1%
"""

import random

from locust import HttpUser, TaskSet, between, task


class SignalsTasks(TaskSet):
    """Simulate a dashboard user browsing signals."""

    @task(5)
    def get_active_signals(self):
        self.client.get("/api/v2/signals?status=ACTIVE", name="/api/v2/signals [ACTIVE]")

    @task(2)
    def get_all_signals(self):
        self.client.get("/api/v2/signals", name="/api/v2/signals [ALL]")

    @task(1)
    def get_signal_by_id(self):
        signal_id = random.randint(1, 100)
        with self.client.get(
            f"/api/v2/signals/{signal_id}",
            name="/api/v2/signals/[id]",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()


class InstrumentsTasks(TaskSet):
    """Simulate browsing the instruments page."""

    @task(3)
    def get_instruments(self):
        self.client.get("/api/v2/instruments", name="/api/v2/instruments")

    @task(1)
    def get_instrument_by_id(self):
        instr_id = random.randint(1, 30)
        with self.client.get(
            f"/api/v2/instruments/{instr_id}",
            name="/api/v2/instruments/[id]",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()


class PortfolioTasks(TaskSet):
    """Simulate portfolio dashboard access."""

    @task(3)
    def get_portfolio(self):
        self.client.get("/api/v2/portfolio", name="/api/v2/portfolio")

    @task(2)
    def get_portfolio_heat(self):
        self.client.get("/api/v2/portfolio/heat", name="/api/v2/portfolio/heat")


class MacroTasks(TaskSet):
    """Simulate macro data page."""

    @task(1)
    def get_macro(self):
        self.client.get("/api/v2/macro", name="/api/v2/macro")

    @task(1)
    def get_regime(self):
        self.client.get("/api/v2/regime", name="/api/v2/regime")


class DashboardUser(HttpUser):
    """Simulates a typical analyst refreshing the dashboard."""

    wait_time = between(1, 5)  # 1-5s between requests (realistic UX pace)

    tasks = {
        SignalsTasks: 5,
        InstrumentsTasks: 2,
        PortfolioTasks: 2,
        MacroTasks: 1,
    }

    def on_start(self):
        """Health-check to confirm API is up before starting load."""
        resp = self.client.get("/api/v2/health", name="/api/v2/health")
        if resp.status_code != 200:
            self.environment.runner.quit()


class HeavyAnalystUser(HttpUser):
    """Power user hitting accuracy and backtests endpoints."""

    wait_time = between(2, 8)

    @task(3)
    def get_accuracy(self):
        self.client.get("/api/v2/accuracy", name="/api/v2/accuracy")

    @task(2)
    def get_backtests(self):
        self.client.get("/api/v2/backtests", name="/api/v2/backtests")

    @task(1)
    def get_active_signals(self):
        self.client.get("/api/v2/signals?status=ACTIVE", name="/api/v2/signals [ACTIVE]")
