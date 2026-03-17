"""Tests for Signal Tracker."""

import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tracker.signal_tracker import SignalTracker


def make_signal(
    id: int = 1,
    direction: str = "LONG",
    status: str = "tracking",
    entry_price: Decimal = Decimal("1.1000"),
    stop_loss: Decimal = Decimal("1.0985"),
    take_profit_1: Decimal = Decimal("1.1020"),
    take_profit_2: Decimal = Decimal("1.1035"),
    instrument_id: int = 1,
    expires_in_hours: int = 24,
):
    """Create a mock Signal object."""
    signal = MagicMock()
    signal.id = id
    signal.direction = direction
    signal.status = status
    signal.entry_price = entry_price
    signal.stop_loss = stop_loss
    signal.take_profit_1 = take_profit_1
    signal.take_profit_2 = take_profit_2
    signal.instrument_id = instrument_id
    signal.created_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
    signal.expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        hours=expires_in_hours
    )
    return signal


class TestEntryDetection:
    """Test entry price hit detection."""

    def test_entry_detected_at_price(self):
        """Should detect entry when price is within tolerance."""
        tracker = SignalTracker()
        entry = Decimal("1.1000")
        current = Decimal("1.1001")  # Within 0.1% tolerance
        assert tracker._check_entry(current, entry, "LONG") is True

    def test_entry_not_detected_far_price(self):
        """Should not detect entry when price is far from entry."""
        tracker = SignalTracker()
        entry = Decimal("1.1000")
        current = Decimal("1.0950")  # 0.45% away
        assert tracker._check_entry(current, entry, "LONG") is False

    def test_entry_detected_exact_match(self):
        """Should detect entry at exact price match."""
        tracker = SignalTracker()
        entry = Decimal("1.1000")
        assert tracker._check_entry(entry, entry, "LONG") is True


class TestSLDetection:
    """Test Stop Loss hit detection."""

    def test_long_sl_hit_when_price_drops_below(self):
        """LONG SL triggered when price drops at or below SL."""
        tracker = SignalTracker()
        assert tracker._check_sl_hit(Decimal("1.0984"), Decimal("1.0985"), "LONG") is True

    def test_long_sl_not_hit_above_sl(self):
        """LONG SL NOT triggered when price is above SL."""
        tracker = SignalTracker()
        assert tracker._check_sl_hit(Decimal("1.0990"), Decimal("1.0985"), "LONG") is False

    def test_short_sl_hit_when_price_rises_above(self):
        """SHORT SL triggered when price rises at or above SL."""
        tracker = SignalTracker()
        assert tracker._check_sl_hit(Decimal("1.1016"), Decimal("1.1015"), "SHORT") is True

    def test_short_sl_not_hit_below_sl(self):
        """SHORT SL NOT triggered when price is below SL."""
        tracker = SignalTracker()
        assert tracker._check_sl_hit(Decimal("1.1010"), Decimal("1.1015"), "SHORT") is False

    def test_no_sl_returns_false(self):
        """No SL level should return False."""
        tracker = SignalTracker()
        assert tracker._check_sl_hit(Decimal("1.0000"), None, "LONG") is False


class TestTPDetection:
    """Test Take Profit hit detection."""

    def test_long_tp_hit_when_price_at_tp(self):
        """LONG TP triggered when price reaches TP."""
        tracker = SignalTracker()
        hit, _ = tracker._check_tp_hit(Decimal("1.1020"), Decimal("1.1020"), "LONG")
        assert hit is True

    def test_long_tp_not_hit_below_tp(self):
        """LONG TP NOT triggered when price is below TP."""
        tracker = SignalTracker()
        hit, _ = tracker._check_tp_hit(Decimal("1.1010"), Decimal("1.1020"), "LONG")
        assert hit is False

    def test_short_tp_hit_when_price_drops_to_tp(self):
        """SHORT TP triggered when price drops to/below TP."""
        tracker = SignalTracker()
        hit, _ = tracker._check_tp_hit(Decimal("1.0980"), Decimal("1.0980"), "SHORT")
        assert hit is True

    def test_short_tp_not_hit_above_tp(self):
        """SHORT TP NOT triggered when price is above TP."""
        tracker = SignalTracker()
        hit, _ = tracker._check_tp_hit(Decimal("1.1000"), Decimal("1.0980"), "SHORT")
        assert hit is False

    def test_no_tp_returns_false(self):
        """No TP level should return False."""
        tracker = SignalTracker()
        hit, _ = tracker._check_tp_hit(Decimal("1.2000"), None, "LONG")
        assert hit is False


class TestMFEMAE:
    """Test Maximum Favorable/Adverse Excursion tracking."""

    def test_mfe_increases_on_favorable_move(self):
        """MFE should increase when price moves in favorable direction."""
        tracker = SignalTracker()
        signal = make_signal(id=1, direction="LONG")
        entry = Decimal("1.1000")

        # First check: price above entry
        tracker._update_mfe_mae(signal, Decimal("1.1010"), entry)
        assert tracker._mfe[1] == Decimal("0.0010")

        # Second check: price even higher
        tracker._update_mfe_mae(signal, Decimal("1.1020"), entry)
        assert tracker._mfe[1] == Decimal("0.0020")

    def test_mae_increases_on_adverse_move(self):
        """MAE should increase when price moves against position."""
        tracker = SignalTracker()
        signal = make_signal(id=1, direction="LONG")
        entry = Decimal("1.1000")

        # Price goes below entry (adverse)
        tracker._update_mfe_mae(signal, Decimal("1.0990"), entry)
        assert tracker._mae[1] == Decimal("0.0010")


class TestPnLCalculation:
    """Test P&L calculation."""

    def test_long_win_pnl(self):
        """LONG trade with exit above entry should have positive PnL."""
        tracker = SignalTracker()
        pips, pct = tracker._calculate_pnl(
            "LONG",
            Decimal("1.1000"),
            Decimal("1.1020"),
            Decimal("0.0001"),
        )
        assert pips == Decimal("20.00")
        assert pct > 0

    def test_long_loss_pnl(self):
        """LONG trade with exit below entry should have negative PnL."""
        tracker = SignalTracker()
        pips, pct = tracker._calculate_pnl(
            "LONG",
            Decimal("1.1000"),
            Decimal("1.0985"),
            Decimal("0.0001"),
        )
        assert pips < 0
        assert pct < 0

    def test_short_win_pnl(self):
        """SHORT trade with exit below entry should have positive PnL."""
        tracker = SignalTracker()
        pips, pct = tracker._calculate_pnl(
            "SHORT",
            Decimal("1.1000"),
            Decimal("1.0980"),
            Decimal("0.0001"),
        )
        assert pips == Decimal("20.00")
        assert pct > 0

    def test_short_loss_pnl(self):
        """SHORT trade with exit above entry should have negative PnL."""
        tracker = SignalTracker()
        pips, pct = tracker._calculate_pnl(
            "SHORT",
            Decimal("1.1000"),
            Decimal("1.1015"),
            Decimal("0.0001"),
        )
        assert pips < 0


class TestSignalStateTransitions:
    """Test signal state machine transitions."""

    @pytest.mark.asyncio
    async def test_created_to_tracking_on_entry(self):
        """Signal should transition from created to tracking when entry hit."""
        tracker = SignalTracker()
        signal = make_signal(status="created", entry_price=Decimal("1.1000"))

        # Mock DB
        mock_db = AsyncMock()
        mock_instrument = MagicMock()
        mock_instrument.pip_size = Decimal("0.0001")
        mock_db.begin_nested = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=None),
        ))

        with patch('src.tracker.signal_tracker.get_instrument_by_id', return_value=mock_instrument):
            with patch('src.tracker.signal_tracker.get_price_data', return_value=[]):
                with patch('src.tracker.signal_tracker.update_signal_status') as mock_update:
                    # Override _get_current_price
                    tracker._get_current_price = AsyncMock(return_value=Decimal("1.1001"))
                    await tracker.check_signal(mock_db, signal)
                    mock_update.assert_called_with(mock_db, signal.id, "tracking")

    @pytest.mark.asyncio
    async def test_expired_signal_closed(self):
        """Expired signal should be marked as expired."""
        tracker = SignalTracker()
        signal = make_signal(
            status="created",
            expires_in_hours=-1,  # Already expired
        )

        mock_db = AsyncMock()
        mock_db.begin_nested = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=None),
        ))

        with patch('src.tracker.signal_tracker.get_instrument_by_id', return_value=MagicMock(pip_size=Decimal("0.0001"))):
            with patch('src.tracker.signal_tracker.update_signal_status') as mock_update:
                with patch('src.tracker.signal_tracker.create_signal_result'):
                    tracker._get_current_price = AsyncMock(return_value=Decimal("1.1005"))
                    await tracker.check_signal(mock_db, signal)
                    mock_update.assert_called_with(mock_db, signal.id, "expired")
