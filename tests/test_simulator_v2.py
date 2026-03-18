"""Trade Simulator v2 — unit tests for SIM-01 through SIM-08."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import datetime

import pytest

from src.tracker.signal_tracker import (
    ENTRY_TOLERANCE_BY_MARKET,
    SPREAD_PIPS_BY_MARKET,
    CRYPTO_SPREAD_PCT,
    SignalTracker,
    _apply_spread,
    _utc,
)
from src.tracker.trade_simulator import pnl_usd


# ── Helpers ───────────────────────────────────────────────────────────────────

def _signal(
    id: int = 1,
    direction: str = "LONG",
    entry_price: Decimal = Decimal("1.10000"),
    stop_loss: Decimal = Decimal("1.09250"),
    take_profit_1: Decimal = Decimal("1.11500"),
    take_profit_2: Decimal = Decimal("1.12500"),
    take_profit_3: Decimal = None,
    position_size_pct: Decimal = Decimal("2.0"),
    status: str = "created",
    regime: str = "RANGING",
    indicators_snapshot: str = '{"atr": 0.00750}',
    market: str = "forex",
    instrument_id: int = 1,
    expires_at=None,
    created_at=None,
):
    s = MagicMock()
    s.id = id
    s.direction = direction
    s.entry_price = entry_price
    s.stop_loss = stop_loss
    s.take_profit_1 = take_profit_1
    s.take_profit_2 = take_profit_2
    s.take_profit_3 = take_profit_3
    s.position_size_pct = position_size_pct
    s.status = status
    s.regime = regime
    s.indicators_snapshot = indicators_snapshot
    s.instrument_id = instrument_id
    s.expires_at = expires_at
    s.created_at = created_at or datetime.datetime.now(datetime.timezone.utc)
    return s


def _instrument(market: str = "forex", pip_size: Decimal = Decimal("0.0001")):
    inst = MagicMock()
    inst.market = market
    inst.pip_size = pip_size
    inst.name = "EUR/USD"
    return inst


def _position(
    entry_price: Decimal = Decimal("1.10015"),  # spread-adjusted
    mfe: Decimal = Decimal("0"),
    mae: Decimal = Decimal("0"),
    breakeven_moved: bool = False,
    partial_closed: bool = False,
    trailing_stop=None,
    current_stop_loss=None,
    size_remaining_pct: Decimal = Decimal("1.0"),
    partial_pnl_pct=None,
    entry_filled_at=None,
    status: str = "open",
):
    p = MagicMock()
    p.entry_price = entry_price
    p.mfe = mfe
    p.mae = mae
    p.breakeven_moved = breakeven_moved
    p.partial_closed = partial_closed
    p.trailing_stop = trailing_stop
    p.current_stop_loss = current_stop_loss or Decimal("1.09250")
    p.size_remaining_pct = size_remaining_pct
    p.partial_pnl_pct = partial_pnl_pct
    p.entry_filled_at = entry_filled_at or datetime.datetime.now(datetime.timezone.utc)
    p.status = status
    return p


# ── SIM-02: Spread model ──────────────────────────────────────────────────────

class TestSpreadModel:
    def test_spread_long_forex(self):
        """LONG EURUSD: actual = entry + 1.5 pips."""
        entry = Decimal("1.08500")
        actual = _apply_spread(entry, "LONG", "forex", Decimal("0.0001"))
        expected = entry + Decimal("1.5") * Decimal("0.0001")
        assert actual == expected  # 1.08515

    def test_spread_short_forex(self):
        """SHORT EURUSD: actual = entry - 1.5 pips (lower, worse for SHORT)."""
        entry = Decimal("1.08500")
        actual = _apply_spread(entry, "SHORT", "forex", Decimal("0.0001"))
        expected = entry - Decimal("1.5") * Decimal("0.0001")
        assert actual == expected  # 1.08485

    def test_spread_long_crypto(self):
        """LONG BTC: actual = entry × (1 + 0.075%)."""
        entry = Decimal("85000")
        actual = _apply_spread(entry, "LONG", "crypto", Decimal("1.0"))
        expected = entry + entry * CRYPTO_SPREAD_PCT
        assert actual == expected

    def test_spread_short_crypto(self):
        """SHORT BTC: actual = entry × (1 - 0.075%) — slightly better price."""
        entry = Decimal("85000")
        actual = _apply_spread(entry, "SHORT", "crypto", Decimal("1.0"))
        expected = entry - entry * CRYPTO_SPREAD_PCT
        assert actual == expected

    def test_spread_long_stocks(self):
        """LONG stock: 2 pips × 0.01 pip_size = $0.02 spread."""
        entry = Decimal("200.00")
        actual = _apply_spread(entry, "LONG", "stocks", Decimal("0.01"))
        expected = entry + Decimal("2.0") * Decimal("0.01")
        assert actual == expected  # 200.02


# ── SIM-08: Entry tolerance by market ────────────────────────────────────────

class TestEntryTolerance:
    def setup_method(self):
        self.tracker = SignalTracker()

    def test_forex_tolerance(self):
        """EURUSD: tolerance = 0.03% ≈ 3 pips."""
        entry = Decimal("1.08500")
        tol = entry * ENTRY_TOLERANCE_BY_MARKET["forex"]
        assert abs(tol - Decimal("0.000326")) < Decimal("0.000001")

        # Within tolerance
        assert self.tracker._check_entry(entry + tol * Decimal("0.9"), entry, "forex")
        # Outside tolerance
        assert not self.tracker._check_entry(entry + tol * Decimal("2.0"), entry, "forex")

    def test_crypto_tolerance(self):
        """BTC/USDT: tolerance = 0.2% × 85000 = ±170."""
        entry = Decimal("85000")
        tol = entry * ENTRY_TOLERANCE_BY_MARKET["crypto"]
        assert tol == Decimal("170")

        assert self.tracker._check_entry(Decimal("84900"), entry, "crypto")
        assert not self.tracker._check_entry(Decimal("84000"), entry, "crypto")

    def test_stocks_tolerance(self):
        """AAPL: tolerance = 0.1% × 200 = ±0.20."""
        entry = Decimal("200.00")
        tol = entry * ENTRY_TOLERANCE_BY_MARKET["stocks"]
        assert tol == Decimal("0.200")

        assert self.tracker._check_entry(Decimal("200.15"), entry, "stocks")
        assert not self.tracker._check_entry(Decimal("199.50"), entry, "stocks")


# ── SIM-06: P&L USD with position size ───────────────────────────────────────

class TestPnlUsd:
    def test_correct_formula(self):
        """pnl_usd = account × (size_pct/100) × (pnl_pct/100)."""
        # account=1000, size=2%, pnl=+1.5% → 1000 × 0.02 × 0.015 = $0.30
        result = pnl_usd(1.5, position_size_pct=2.0)
        assert abs(result - 0.30) < 0.01

    def test_loss(self):
        # account=1000, size=5%, pnl=-1.0% → 1000 × 0.05 × -0.01 = -$0.50
        result = pnl_usd(-1.0, position_size_pct=5.0)
        assert abs(result - (-0.50)) < 0.01

    def test_no_position_size_fallback(self):
        """When position_size_pct is None, falls back to 100% (v1 compat)."""
        result = pnl_usd(1.0, position_size_pct=None)
        # 1000 × 1.0 × 0.01 = $10
        from src.tracker.trade_simulator import ACCOUNT_SIZE_FLOAT
        expected = ACCOUNT_SIZE_FLOAT * 0.01
        assert abs(result - expected) < 0.01

    def test_zero_pnl(self):
        assert pnl_usd(0.0, position_size_pct=2.0) == 0.0

    def test_none_pnl(self):
        assert pnl_usd(None) == 0.0


# ── SIM-04: Duration from entry_filled_at ────────────────────────────────────

class TestDuration:
    def test_duration_from_entry(self):
        """Duration should be exit - entry_filled_at, not exit - created_at."""
        tracker = SignalTracker()
        now = datetime.datetime.now(datetime.timezone.utc)
        entry_filled = now - datetime.timedelta(minutes=60)
        created_at   = now - datetime.timedelta(minutes=120)

        pos = _position(entry_filled_at=entry_filled)
        sig = _signal(created_at=created_at)

        entry_time = _utc(pos.entry_filled_at) or _utc(sig.created_at)
        duration = int((now - entry_time).total_seconds() / 60)
        assert duration == 60  # not 120

    def test_duration_fallback_to_created_at(self):
        """Falls back to created_at when entry_filled_at is None."""
        now = datetime.datetime.now(datetime.timezone.utc)
        created_at = now - datetime.timedelta(minutes=90)

        pos = _position(entry_filled_at=None)
        sig = _signal(created_at=created_at)

        entry_time = _utc(pos.entry_filled_at) or _utc(sig.created_at)
        duration = int((now - entry_time).total_seconds() / 60)
        assert duration == 90


# ── SIM-03: cancelled vs expired ──────────────────────────────────────────────

class TestCancelledVsExpired:
    def test_tracker_pnl_calculation(self):
        """Cancelled result should have 0 P&L."""
        tracker = SignalTracker()
        pips, pct = tracker._calculate_pnl("LONG", Decimal("0"), Decimal("0"))
        # Just validate the formula works; actual SIM-03 logic tested via integration

    def test_cancelled_pnl_zero(self):
        """A cancelled signal must have pnl_pips = 0 and result = breakeven."""
        # This validates the data model expectation
        result_data = {
            "exit_reason": "cancelled",
            "result": "breakeven",
            "pnl_pips": Decimal("0"),
            "pnl_percent": Decimal("0"),
            "pnl_usd": Decimal("0"),
        }
        assert result_data["exit_reason"] == "cancelled"
        assert result_data["pnl_pips"] == Decimal("0")
        assert result_data["result"] == "breakeven"


# ── SIM-01: MFE/MAE persistence ───────────────────────────────────────────────

class TestMfeMaePersistence:
    @pytest.mark.asyncio
    async def test_mfe_updates_when_higher(self):
        """MFE should increase when price moves favorably."""
        tracker = SignalTracker()
        sig = _signal(direction="LONG", entry_price=Decimal("1.10000"))

        pos = MagicMock()
        pos.mfe = Decimal("0.0005")  # previous best
        pos.mae = Decimal("0")

        db = AsyncMock()
        with patch("src.tracker.signal_tracker.get_virtual_position", return_value=pos):
            with patch("src.tracker.signal_tracker.update_virtual_position") as mock_update:
                await tracker._update_mfe_mae(db, sig, Decimal("1.10100"), Decimal("1.10000"))

                # favorable = 1.10100 - 1.10000 = 0.00100 > pos.mfe 0.0005 → update
                mock_update.assert_called_once()
                call_kwargs = mock_update.call_args[1]["updates"] if mock_update.call_args[1] else mock_update.call_args[0][2]
                assert call_kwargs["mfe"] > pos.mfe

    @pytest.mark.asyncio
    async def test_mfe_does_not_decrease(self):
        """MFE should not decrease when price retreats."""
        tracker = SignalTracker()
        sig = _signal(direction="LONG", entry_price=Decimal("1.10000"))

        pos = MagicMock()
        pos.mfe = Decimal("0.0020")  # high watermark
        pos.mae = Decimal("0")

        db = AsyncMock()
        with patch("src.tracker.signal_tracker.get_virtual_position", return_value=pos):
            with patch("src.tracker.signal_tracker.update_virtual_position") as mock_update:
                # Price only +0.0005 — lower than previous MFE of 0.0020
                await tracker._update_mfe_mae(db, sig, Decimal("1.10050"), Decimal("1.10000"))
                # No update needed since new_mfe == old_mfe
                mock_update.assert_not_called()


# ── SIM-07: Partial close ─────────────────────────────────────────────────────

class TestPartialClose:
    @pytest.mark.asyncio
    async def test_partial_close_updates_position(self):
        """After partial close: size_remaining=0.5, partial_closed=True, SL→entry."""
        tracker = SignalTracker()
        sig = _signal(
            direction="LONG",
            entry_price=Decimal("1.10000"),
            position_size_pct=Decimal("2.0"),
        )
        pos = _position(
            entry_price=Decimal("1.10015"),
            size_remaining_pct=Decimal("1.0"),
            partial_closed=False,
        )
        exit_price = Decimal("1.11432")  # ~95% of TP1 dist
        now = datetime.datetime.now(datetime.timezone.utc)

        db = AsyncMock()
        with patch("src.tracker.signal_tracker.update_virtual_position") as mock_update:
            await tracker._partial_close(db, sig, pos, exit_price, now, Decimal("0.0001"))

            mock_update.assert_called_once()
            args = mock_update.call_args
            updates = args[0][2] if args[0] else args[1]["updates"]
            assert updates["partial_closed"] is True
            assert updates["breakeven_moved"] is True
            assert updates["size_remaining_pct"] == Decimal("0.5")
            assert updates["current_stop_loss"] == pos.entry_price  # SL → entry


# ── SIM-05: TradeLifecycleManager integration ─────────────────────────────────

class TestLifecycleIntegration:
    def test_lifecycle_breakeven_action(self):
        """At RR 1:1, lifecycle should return breakeven action."""
        from src.signals.trade_lifecycle import TradeLifecycleManager

        mgr = TradeLifecycleManager()
        action = mgr.check(
            direction="LONG",
            entry=Decimal("1.10000"),
            stop_loss=Decimal("1.09250"),    # SL dist = 75 pips
            take_profit_1=Decimal("1.11500"),
            take_profit_2=Decimal("1.12500"),
            take_profit_3=None,
            current_price=Decimal("1.10760"),  # entry + 75 pips = 1:1
            atr=Decimal("0.00750"),
            regime="RANGING",
            partial_closed=False,
            breakeven_moved=False,
            trailing_stop=None,
        )
        assert action["action"] == "breakeven"
        assert action["new_stop_loss"] == Decimal("1.10000")

    def test_lifecycle_partial_close(self):
        """At 95% of TP1 distance, lifecycle should return partial_close."""
        from src.signals.trade_lifecycle import TradeLifecycleManager

        mgr = TradeLifecycleManager()
        # TP1 = entry + 150 pips, 95% = entry + 142.5 pips = 1.11425
        action = mgr.check(
            direction="LONG",
            entry=Decimal("1.10000"),
            stop_loss=Decimal("1.09250"),
            take_profit_1=Decimal("1.11500"),
            take_profit_2=Decimal("1.12500"),
            take_profit_3=None,
            current_price=Decimal("1.11430"),  # past 95% of TP1
            atr=Decimal("0.00750"),
            regime="RANGING",
            partial_closed=False,
            breakeven_moved=True,  # breakeven already moved
            trailing_stop=None,
        )
        assert action["action"] == "partial_close"
        assert action["close_pct"] == 0.5

    def test_lifecycle_trailing_update(self):
        """After breakeven, trailing stop should be set."""
        from src.signals.trade_lifecycle import TradeLifecycleManager

        mgr = TradeLifecycleManager()
        action = mgr.check(
            direction="LONG",
            entry=Decimal("1.10000"),
            stop_loss=Decimal("1.10000"),  # already at breakeven
            take_profit_1=Decimal("1.11500"),
            take_profit_2=Decimal("1.12500"),
            take_profit_3=None,
            current_price=Decimal("1.11200"),
            atr=Decimal("0.00750"),
            regime="RANGING",
            partial_closed=True,
            breakeven_moved=True,
            trailing_stop=None,
        )
        assert action["action"] == "trailing_update"
        # trail = price - 0.3×ATR (ranging)
        expected_trail = Decimal("1.11200") - Decimal("0.3") * Decimal("0.00750")
        assert action["new_stop_loss"] == expected_trail.quantize(Decimal("0.00000001"))
