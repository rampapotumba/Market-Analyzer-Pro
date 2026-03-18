"""Tests for src.api.websocket_v2."""

import json

import pytest

from src.api.websocket_v2 import ConnectionManagerV2, _json_safe


# ── _json_safe ────────────────────────────────────────────────────────────────

class TestJsonSafe:
    def test_decimal(self):
        from decimal import Decimal
        assert _json_safe(Decimal("1.5")) == pytest.approx(1.5)

    def test_datetime(self):
        import datetime
        dt = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        assert "2026" in _json_safe(dt)

    def test_nested_dict(self):
        from decimal import Decimal
        result = _json_safe({"price": Decimal("100.0"), "name": "BTC"})
        assert result["price"] == pytest.approx(100.0)
        assert result["name"] == "BTC"

    def test_list(self):
        from decimal import Decimal
        result = _json_safe([Decimal("1.0"), Decimal("2.0")])
        assert result == [pytest.approx(1.0), pytest.approx(2.0)]

    def test_passthrough_primitives(self):
        assert _json_safe(42) == 42
        assert _json_safe("hello") == "hello"
        assert _json_safe(None) is None


# ── ConnectionManagerV2 ───────────────────────────────────────────────────────

class TestConnectionManagerV2:
    def test_initial_state(self):
        mgr = ConnectionManagerV2()
        assert mgr.signal_subs == []
        assert mgr.portfolio_subs == []
        assert mgr.price_subs == {}

    def test_disconnect_signals_removes(self):
        import unittest.mock as mock
        mgr = ConnectionManagerV2()
        ws = mock.MagicMock()
        mgr.signal_subs.append(ws)
        mgr.disconnect_signals(ws)
        assert ws not in mgr.signal_subs

    def test_disconnect_portfolio_removes(self):
        import unittest.mock as mock
        mgr = ConnectionManagerV2()
        ws = mock.MagicMock()
        mgr.portfolio_subs.append(ws)
        mgr.disconnect_portfolio(ws)
        assert ws not in mgr.portfolio_subs

    def test_disconnect_price_removes(self):
        import unittest.mock as mock
        mgr = ConnectionManagerV2()
        ws = mock.MagicMock()
        mgr.price_subs["EURUSD"] = [ws]
        mgr.disconnect_price(ws, "EURUSD")
        assert ws not in mgr.price_subs["EURUSD"]

    def test_disconnect_nonexistent_no_error(self):
        import unittest.mock as mock
        mgr = ConnectionManagerV2()
        ws = mock.MagicMock()
        # Should not raise
        mgr.disconnect_signals(ws)
        mgr.disconnect_portfolio(ws)
        mgr.disconnect_price(ws, "EURUSD")

    @pytest.mark.asyncio
    async def test_broadcast_signal_removes_dead_connection(self):
        """Dead connections should be removed during broadcast."""
        import unittest.mock as mock
        mgr = ConnectionManagerV2()

        dead_ws = mock.MagicMock()
        dead_ws.send_text = mock.AsyncMock(side_effect=Exception("connection closed"))
        mgr.signal_subs.append(dead_ws)

        await mgr.broadcast_signal({"id": 1, "direction": "LONG"})
        assert dead_ws not in mgr.signal_subs

    @pytest.mark.asyncio
    async def test_broadcast_portfolio_removes_dead_connection(self):
        import unittest.mock as mock
        mgr = ConnectionManagerV2()

        dead_ws = mock.MagicMock()
        dead_ws.send_text = mock.AsyncMock(side_effect=Exception("gone"))
        mgr.portfolio_subs.append(dead_ws)

        await mgr.broadcast_portfolio({"signal_id": 1, "status": "closed"})
        assert dead_ws not in mgr.portfolio_subs

    @pytest.mark.asyncio
    async def test_broadcast_price_sends_to_correct_symbol(self):
        """Price broadcast should only reach subscribers of that symbol."""
        import unittest.mock as mock
        mgr = ConnectionManagerV2()

        ws_eur = mock.MagicMock()
        ws_eur.send_text = mock.AsyncMock()
        ws_gbp = mock.MagicMock()
        ws_gbp.send_text = mock.AsyncMock()

        mgr.price_subs["EURUSD"] = [ws_eur]
        mgr.price_subs["GBPUSD"] = [ws_gbp]

        await mgr.broadcast_price("EURUSD", {"close": 1.1000})

        ws_eur.send_text.assert_called_once()
        ws_gbp.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_broadcast_signal_sends_correct_payload(self):
        import unittest.mock as mock
        mgr = ConnectionManagerV2()

        ws = mock.MagicMock()
        received: list[str] = []
        ws.send_text = mock.AsyncMock(side_effect=lambda t: received.append(t))
        mgr.signal_subs.append(ws)

        await mgr.broadcast_signal({"id": 42, "direction": "SHORT"})

        assert len(received) == 1
        data = json.loads(received[0])
        assert data["type"] == "signal"
        assert data["data"]["id"] == 42
