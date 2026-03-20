"""Integration tests for src.api.routes_v2 using FastAPI TestClient."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from src.api.routes_v2 import router_v2


# ── App fixture ───────────────────────────────────────────────────────────────

@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(router_v2)
    return application


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ── Health ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_v2(client):
    resp = await client.get("/api/v2/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "2.0"


# ── Signals ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_signals_empty(client):
    with patch("src.api.routes_v2.get_signals", new=AsyncMock(return_value=[])):
        resp = await client.get("/api/v2/signals")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_signal_not_found(client):
    with patch("src.api.routes_v2.get_signal_by_id", new=AsyncMock(return_value=None)):
        resp = await client.get("/api/v2/signals/999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_signal_found(client):
    mock_signal = MagicMock()
    mock_signal.id = 1
    mock_signal.instrument_id = 1
    mock_signal.timeframe = "H4"
    mock_signal.direction = "LONG"
    mock_signal.signal_strength = "STRONG_BUY"
    mock_signal.composite_score = 72.5
    mock_signal.ta_score = 65.0
    mock_signal.fa_score = 55.0
    mock_signal.sentiment_score = 40.0
    mock_signal.geo_score = 10.0
    mock_signal.of_score = None
    mock_signal.confidence = 78.0
    mock_signal.regime = "STRONG_TREND_BULL"
    mock_signal.entry_price = None
    mock_signal.stop_loss = None
    mock_signal.take_profit_1 = None
    mock_signal.take_profit_2 = None
    mock_signal.take_profit_3 = None
    mock_signal.risk_reward = None
    mock_signal.position_size_pct = None
    mock_signal.earnings_days_ahead = None
    mock_signal.portfolio_heat = None
    mock_signal.status = "active"
    mock_signal.llm_score = None
    mock_signal.llm_bias = None
    mock_signal.llm_confidence = None
    mock_signal.reasoning = None
    mock_signal.instrument = MagicMock()
    mock_signal.instrument.symbol = "EURUSD=X"
    import datetime
    mock_signal.created_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)

    with patch("src.api.routes_v2.get_signal_by_id", new=AsyncMock(return_value=mock_signal)):
        resp = await client.get("/api/v2/signals/1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["direction"] == "LONG"
    assert data["confidence"] == 78.0


# ── Instruments ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_instruments_empty(client):
    with patch("src.api.routes_v2.get_all_instruments", new=AsyncMock(return_value=[])):
        resp = await client.get("/api/v2/instruments")
    assert resp.status_code == 200
    assert resp.json() == []


# ── Portfolio ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_portfolio_empty(client):
    with patch("src.api.routes_v2.get_open_positions", new=AsyncMock(return_value=[])):
        resp = await client.get("/api/v2/portfolio")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_portfolio_heat_zero(client):
    with patch("src.api.routes_v2.get_open_positions", new=AsyncMock(return_value=[])):
        resp = await client.get("/api/v2/portfolio/heat")
    assert resp.status_code == 200
    data = resp.json()
    assert data["portfolio_heat_pct"] == 0.0
    assert data["total_positions"] == 0
    assert "max_heat_pct" in data
    assert "heat_remaining_pct" in data


# ── Accuracy ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_accuracy_route_empty(app, client):
    """Accuracy endpoint returns empty list when no stats exist."""
    mock_session = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = []
    mock_session.execute = AsyncMock(return_value=execute_result)

    async def mock_get_session():
        yield mock_session

    from src.api.routes_v2 import get_session
    app.dependency_overrides[get_session] = mock_get_session
    try:
        resp = await client.get("/api/v2/accuracy")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == []


# ── Prices ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_prices_instrument_not_found(client):
    with patch(
        "src.api.routes_v2.get_instrument_by_symbol",
        new=AsyncMock(return_value=None),
    ):
        resp = await client.get("/api/v2/prices/UNKNOWN")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_prices_returns_data(client):
    import datetime
    from decimal import Decimal

    mock_instr = MagicMock()
    mock_instr.id = 1
    mock_instr.symbol = "EURUSD"

    mock_price = MagicMock()
    mock_price.timestamp = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    mock_price.open = Decimal("1.1000")
    mock_price.high = Decimal("1.1050")
    mock_price.low = Decimal("1.0980")
    mock_price.close = Decimal("1.1020")
    mock_price.volume = Decimal("1000000")

    with patch(
        "src.api.routes_v2.get_instrument_by_symbol",
        new=AsyncMock(return_value=mock_instr),
    ):
        with patch(
            "src.api.routes_v2.get_price_data",
            new=AsyncMock(return_value=[mock_price]),
        ):
            resp = await client.get("/api/v2/prices/EURUSD?timeframe=H1")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["open"] == pytest.approx(1.1000)


# ── Signals active ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_active_signals_empty(client):
    with patch("src.api.routes_v2.get_active_signals", new=AsyncMock(return_value=[])):
        resp = await client.get("/api/v2/signals/active")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_active_signals_returns_data(client):
    import datetime
    mock_sig = MagicMock()
    mock_sig.id = 1
    mock_sig.instrument_id = 2
    mock_sig.timeframe = "H1"
    mock_sig.direction = "LONG"
    mock_sig.signal_strength = "STRONG_BUY"
    mock_sig.composite_score = 18.0
    mock_sig.ta_score = mock_sig.fa_score = mock_sig.sentiment_score = mock_sig.geo_score = 10.0
    mock_sig.of_score = mock_sig.regime = None
    mock_sig.confidence = 70.0
    mock_sig.entry_price = mock_sig.stop_loss = mock_sig.take_profit_1 = None
    mock_sig.take_profit_2 = mock_sig.take_profit_3 = mock_sig.risk_reward = None
    mock_sig.position_size_pct = mock_sig.earnings_days_ahead = mock_sig.portfolio_heat = None
    mock_sig.status = "created"
    mock_sig.created_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    mock_sig.llm_score = mock_sig.llm_bias = mock_sig.llm_confidence = None
    mock_sig.instrument = MagicMock()
    mock_sig.instrument.symbol = "EURUSD=X"

    with patch("src.api.routes_v2.get_active_signals", new=AsyncMock(return_value=[mock_sig])):
        resp = await client.get("/api/v2/signals/active")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["direction"] == "LONG"
    assert data[0]["symbol"] == "EURUSD=X"


# ── Simulator reset ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_simulator_reset(app, client):
    mock_session = AsyncMock()
    # DELETE ... RETURNING returns a result whose fetchall() gives row list
    delete_result = MagicMock()
    delete_result.fetchall.return_value = []
    mock_session.execute = AsyncMock(return_value=delete_result)
    mock_session.commit = AsyncMock()

    async def mock_get_session():
        yield mock_session

    app.dependency_overrides[__import__("src.api.routes_v2", fromlist=["get_session"]).get_session] = mock_get_session
    try:
        resp = await client.post("/api/v2/simulator/reset")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "deleted_positions" in data
    assert "deleted_signals" in data


# ── System logs clear ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clear_system_logs(client):
    with patch("src.database.crud.delete_all_system_events", new=AsyncMock(return_value=5)):
        resp = await client.post("/api/v2/system/logs/clear")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["deleted"] == 5


# ── Health checks ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_postgres_ok(client):
    resp = await client.get("/api/v2/health/postgres")
    # DB not available in test env — just check route is reachable
    assert resp.status_code == 200
    assert resp.json()["status"] in ("ok", "error")


@pytest.mark.asyncio
async def test_health_redis_unreachable(client):
    resp = await client.get("/api/v2/health/redis")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("ok", "error")
    assert "detail" in data


@pytest.mark.asyncio
async def test_health_scheduler_not_running(client):
    mock_scheduler = MagicMock()
    mock_scheduler.running = False
    with patch("src.scheduler.jobs.scheduler", mock_scheduler):
        resp = await client.get("/api/v2/health/scheduler")
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"


@pytest.mark.asyncio
async def test_health_scheduler_running(client):
    mock_scheduler = MagicMock()
    mock_scheduler.running = True
    mock_scheduler.get_jobs.return_value = [MagicMock(), MagicMock()]
    with patch("src.scheduler.jobs.scheduler", mock_scheduler):
        resp = await client.get("/api/v2/health/scheduler")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "2 jobs" in data["detail"]


# ── Orderflow ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_orderflow_instrument_not_found(client):
    with patch(
        "src.api.routes_v2.get_instrument_by_symbol",
        new=AsyncMock(return_value=None),
    ):
        resp = await client.get("/api/v2/prices/BTCUSDT/orderflow")
    assert resp.status_code == 404
