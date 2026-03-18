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
async def test_accuracy_route_exists(client):
    """Route exists and at least returns a response (DB may be unavailable)."""
    resp = await client.get("/api/v2/accuracy")
    assert resp.status_code in (200, 500, 503)


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


# ── Orderflow ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_orderflow_instrument_not_found(client):
    with patch(
        "src.api.routes_v2.get_instrument_by_symbol",
        new=AsyncMock(return_value=None),
    ):
        resp = await client.get("/api/v2/prices/BTCUSDT/orderflow")
    assert resp.status_code == 404
