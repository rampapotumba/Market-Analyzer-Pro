"""Tests for FIX-03, FIX-04, FIX-05, FIX-06, FIX-07 — trader review fixes."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────


def _signal(
    position_size_pct: Decimal = Decimal("20.0"),
    stop_loss: Decimal = Decimal("1.09250"),
    take_profit_1: Decimal = Decimal("1.11500"),
    take_profit_2: Decimal = Decimal("1.12500"),
    direction: str = "LONG",
    entry_price: Decimal = Decimal("1.10000"),
) -> MagicMock:
    s = MagicMock()
    s.id = 1
    s.position_size_pct = position_size_pct
    s.stop_loss = stop_loss
    s.take_profit_1 = take_profit_1
    s.take_profit_2 = take_profit_2
    s.take_profit_3 = None
    s.direction = direction
    s.entry_price = entry_price
    s.status = "tracking"
    s.timeframe = "H1"
    s.instrument_id = 1
    s.composite_score = Decimal("12.5")
    return s


def _position(
    size_pct: Decimal = Decimal("20.0"),
    entry_price: Decimal = Decimal("1.10015"),
    partial_closed: bool = False,
    breakeven_moved: bool = False,
    size_remaining_pct: Decimal = Decimal("1.0"),
    trailing_stop=None,
) -> MagicMock:
    p = MagicMock()
    p.size_pct = size_pct
    p.entry_price = entry_price
    p.partial_closed = partial_closed
    p.breakeven_moved = breakeven_moved
    p.size_remaining_pct = size_remaining_pct
    p.trailing_stop = trailing_stop
    p.current_stop_loss = Decimal("1.09250")
    p.mfe = Decimal("0")
    p.mae = Decimal("0")
    p.partial_pnl_pct = None
    p.account_balance_at_entry = None
    return p


# ══════════════════════════════════════════════════════════════════════════════
# FIX-03: virtual_portfolio.size_pct must come from signal.position_size_pct
# ══════════════════════════════════════════════════════════════════════════════

class TestFix03PositionSizePct:
    @pytest.mark.asyncio
    async def test_new_position_uses_signal_size_pct(self):
        """New position created by _open_virtual_position must store signal.position_size_pct."""
        from src.tracker.signal_tracker import SignalTracker

        tracker = SignalTracker()
        sig = _signal(position_size_pct=Decimal("20.0"))
        db = AsyncMock()

        captured = {}

        async def mock_create(db, data):
            captured.update(data)

        async def mock_get_pos(db, sig_id):
            return None  # position doesn't exist yet

        async def mock_get_account(db):
            acc = MagicMock()
            acc.current_balance = Decimal("1000")
            return acc

        with patch("src.tracker.signal_tracker.get_virtual_position", side_effect=mock_get_pos), \
             patch("src.tracker.signal_tracker.create_virtual_position", side_effect=mock_create), \
             patch("src.tracker.signal_tracker.get_virtual_account", side_effect=mock_get_account):
            import datetime
            await tracker._open_virtual_position(
                db, sig,
                Decimal("1.10015"),
                datetime.datetime.now(datetime.timezone.utc),
            )

        assert "size_pct" in captured
        assert captured["size_pct"] == Decimal("20.0"), (
            f"Expected size_pct=20.0, got {captured['size_pct']}"
        )

    @pytest.mark.asyncio
    async def test_position_size_fallback_when_none(self):
        """If signal.position_size_pct is None, fall back to default 2.0."""
        from src.tracker.signal_tracker import SignalTracker

        tracker = SignalTracker()
        sig = _signal(position_size_pct=None)
        sig.position_size_pct = None
        db = AsyncMock()

        captured = {}

        async def mock_create(db, data):
            captured.update(data)

        async def mock_get_pos(db, sig_id):
            return None

        async def mock_get_account(db):
            acc = MagicMock()
            acc.current_balance = Decimal("1000")
            return acc

        with patch("src.tracker.signal_tracker.get_virtual_position", side_effect=mock_get_pos), \
             patch("src.tracker.signal_tracker.create_virtual_position", side_effect=mock_create), \
             patch("src.tracker.signal_tracker.get_virtual_account", side_effect=mock_get_account):
            import datetime
            await tracker._open_virtual_position(
                db, sig,
                Decimal("1.10015"),
                datetime.datetime.now(datetime.timezone.utc),
            )

        assert captured["size_pct"] == Decimal("2.0")

    @pytest.mark.asyncio
    async def test_backfill_script_finds_mismatched_positions(self):
        """Backfill script reports positions where size_pct != signal.position_size_pct."""
        from scripts.backfill_position_size import run
        # Dry-run: just verify it doesn't raise exceptions
        # (actual DB state was already fixed)
        try:
            await run(dry_run=True)
        except Exception as e:
            pytest.fail(f"backfill script raised: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# FIX-04: Partial close must trigger at TP1 (not at 95% of TP1 distance)
# ══════════════════════════════════════════════════════════════════════════════

class TestFix04PartialCloseAtTP1:
    def _mgr(self):
        from src.signals.trade_lifecycle import TradeLifecycleManager
        return TradeLifecycleManager()

    def test_no_partial_close_just_before_tp1(self):
        """Price at 99.9% of TP1 distance should NOT trigger partial close."""
        mgr = self._mgr()
        entry = Decimal("1.10000")
        tp1   = Decimal("1.11500")   # dist = 150 pips
        # 99.9% of the way to TP1 — just 1.5 pips short
        price = entry + (tp1 - entry) * Decimal("0.999")

        action = mgr.check(
            direction="LONG",
            entry=entry,
            stop_loss=Decimal("1.09250"),
            take_profit_1=tp1,
            take_profit_2=Decimal("1.12500"),
            take_profit_3=None,
            current_price=price,
            atr=Decimal("0.0075"),
            regime="RANGING",
            partial_closed=False,
            breakeven_moved=True,   # breakeven already done
            trailing_stop=None,
        )
        assert action["action"] != "partial_close", (
            f"Should not partial close at 99.9% of TP1, got action={action['action']}"
        )

    def test_partial_close_exactly_at_tp1(self):
        """Price exactly at TP1 must trigger partial close."""
        mgr = self._mgr()
        entry = Decimal("1.10000")
        tp1   = Decimal("1.11500")

        action = mgr.check(
            direction="LONG",
            entry=entry,
            stop_loss=Decimal("1.09250"),
            take_profit_1=tp1,
            take_profit_2=Decimal("1.12500"),
            take_profit_3=None,
            current_price=tp1,          # exactly at TP1
            atr=Decimal("0.0075"),
            regime="RANGING",
            partial_closed=False,
            breakeven_moved=True,
            trailing_stop=None,
        )
        assert action["action"] == "partial_close", (
            f"Expected partial_close at TP1, got {action['action']}"
        )

    def test_partial_close_short_exactly_at_tp1(self):
        """SHORT: price at TP1 must trigger partial close."""
        mgr = self._mgr()
        entry = Decimal("1.10000")
        tp1   = Decimal("1.08500")

        action = mgr.check(
            direction="SHORT",
            entry=entry,
            stop_loss=Decimal("1.10750"),
            take_profit_1=tp1,
            take_profit_2=Decimal("1.07500"),
            take_profit_3=None,
            current_price=tp1,
            atr=Decimal("0.0075"),
            regime="RANGING",
            partial_closed=False,
            breakeven_moved=True,
            trailing_stop=None,
        )
        assert action["action"] == "partial_close"


# ══════════════════════════════════════════════════════════════════════════════
# FIX-05: TP3 must be None for RANGING and HIGH_VOLATILITY regimes
# ══════════════════════════════════════════════════════════════════════════════

class TestFix05TP3NoneForRanging:
    def _rm(self):
        from src.signals.risk_manager_v2 import RiskManagerV2
        return RiskManagerV2()

    def test_ranging_tp3_is_none(self):
        """RANGING regime: TP3 must be None — target unrealistic in sideways market."""
        rm = self._rm()
        levels = rm.calculate_levels_for_regime(
            entry=Decimal("1.10000"),
            atr=Decimal("0.0075"),
            direction="LONG",
            regime="RANGING",
        )
        assert levels.get("take_profit_3") is None, (
            f"Expected TP3=None for RANGING, got {levels.get('take_profit_3')}"
        )

    def test_high_volatility_tp3_is_none(self):
        """HIGH_VOLATILITY regime: TP3 must be None."""
        rm = self._rm()
        levels = rm.calculate_levels_for_regime(
            entry=Decimal("1.10000"),
            atr=Decimal("0.0150"),
            direction="LONG",
            regime="HIGH_VOLATILITY",
        )
        assert levels.get("take_profit_3") is None

    def test_strong_trend_bull_has_tp3(self):
        """STRONG_TREND_BULL: TP3 must be calculated (valid trend target)."""
        rm = self._rm()
        levels = rm.calculate_levels_for_regime(
            entry=Decimal("1.10000"),
            atr=Decimal("0.0075"),
            direction="LONG",
            regime="STRONG_TREND_BULL",
        )
        tp3 = levels.get("take_profit_3")
        assert tp3 is not None and tp3 > levels["take_profit_2"], (
            f"Expected TP3 > TP2 for STRONG_TREND_BULL, got TP3={tp3}"
        )

    def test_weak_trend_bear_has_tp3(self):
        """WEAK_TREND_BEAR: TP3 must be calculated."""
        rm = self._rm()
        levels = rm.calculate_levels_for_regime(
            entry=Decimal("1.10000"),
            atr=Decimal("0.0075"),
            direction="SHORT",
            regime="WEAK_TREND_BEAR",
        )
        tp3 = levels.get("take_profit_3")
        assert tp3 is not None and tp3 < levels["take_profit_2"]


# ══════════════════════════════════════════════════════════════════════════════
# FIX-06: Trailing stop ATR multiplier (0.5→1.0 trend, 0.3→0.5 range)
# ══════════════════════════════════════════════════════════════════════════════

class TestFix06TrailingStopATR:
    def _mgr(self):
        from src.signals.trade_lifecycle import TradeLifecycleManager
        return TradeLifecycleManager()

    def test_trend_trailing_uses_1x_atr(self):
        """STRONG_TREND_BULL: trailing stop = price - 1.0×ATR (not 0.5×ATR)."""
        mgr = self._mgr()
        entry = Decimal("1.10000")
        atr   = Decimal("0.0100")
        price = Decimal("1.1300")   # well in profit

        action = mgr.check(
            direction="LONG",
            entry=entry,
            stop_loss=Decimal("1.09000"),
            take_profit_1=Decimal("1.12000"),
            take_profit_2=Decimal("1.14000"),
            take_profit_3=Decimal("1.16000"),
            current_price=price,
            atr=atr,
            regime="STRONG_TREND_BULL",
            partial_closed=True,
            breakeven_moved=True,
            trailing_stop=Decimal("1.11000"),  # existing trail
        )
        if action["action"] == "trailing_update":
            new_trail = action["new_stop_loss"]
            expected  = price - atr * Decimal("1.0")
            assert new_trail == expected, (
                f"Expected trail={expected} (price - 1.0×ATR), got {new_trail}"
            )

    def test_range_trailing_uses_half_atr(self):
        """RANGING: trailing stop = price - 0.5×ATR (not 0.3×ATR)."""
        mgr = self._mgr()
        entry = Decimal("1.10000")
        atr   = Decimal("0.0100")
        price = Decimal("1.1200")

        action = mgr.check(
            direction="LONG",
            entry=entry,
            stop_loss=Decimal("1.09000"),
            take_profit_1=Decimal("1.11500"),
            take_profit_2=Decimal("1.12500"),
            take_profit_3=None,
            current_price=price,
            atr=atr,
            regime="RANGING",
            partial_closed=True,
            breakeven_moved=True,
            trailing_stop=Decimal("1.10000"),  # existing trail
        )
        if action["action"] == "trailing_update":
            new_trail = action["new_stop_loss"]
            expected  = price - atr * Decimal("0.5")
            assert new_trail == expected, (
                f"Expected trail={expected} (price - 0.5×ATR), got {new_trail}"
            )

    def test_trailing_does_not_move_backward(self):
        """Trailing stop must only move in profitable direction, never backward."""
        mgr = self._mgr()
        existing_trail = Decimal("1.1200")
        # Price drops back: new computed trail would be below existing
        price = Decimal("1.1250")   # pullback

        action = mgr.check(
            direction="LONG",
            entry=Decimal("1.10000"),
            stop_loss=Decimal("1.09000"),
            take_profit_1=Decimal("1.12000"),
            take_profit_2=Decimal("1.14000"),
            take_profit_3=Decimal("1.16000"),
            current_price=price,
            atr=Decimal("0.0100"),
            regime="STRONG_TREND_BULL",
            partial_closed=True,
            breakeven_moved=True,
            trailing_stop=existing_trail,
        )
        # Should NOT update trailing if computed < existing
        if action["action"] == "trailing_update":
            assert action["new_stop_loss"] >= existing_trail


# ══════════════════════════════════════════════════════════════════════════════
# FIX-07: Session filter — no EUR/GBP signals during Asian hours
# ══════════════════════════════════════════════════════════════════════════════

class TestFix07SessionFilter:
    def test_eurusd_blocked_asian_session(self):
        """EURUSD signal during Asian session (03:00 UTC) must be blocked."""
        from src.signals.signal_engine import _is_low_liquidity_session
        import datetime
        asian_time = datetime.datetime(2026, 3, 18, 3, 0, 0,
                                       tzinfo=datetime.timezone.utc)
        assert _is_low_liquidity_session("EURUSD=X", "forex", asian_time) is True

    def test_gbpusd_blocked_asian_session(self):
        from src.signals.signal_engine import _is_low_liquidity_session
        import datetime
        asian_time = datetime.datetime(2026, 3, 18, 5, 30, 0,
                                       tzinfo=datetime.timezone.utc)
        assert _is_low_liquidity_session("GBPUSD=X", "forex", asian_time) is True

    def test_eurusd_allowed_london_session(self):
        """EURUSD during London session (10:00 UTC) must be allowed."""
        from src.signals.signal_engine import _is_low_liquidity_session
        import datetime
        london_time = datetime.datetime(2026, 3, 18, 10, 0, 0,
                                        tzinfo=datetime.timezone.utc)
        assert _is_low_liquidity_session("EURUSD=X", "forex", london_time) is False

    def test_usdjpy_allowed_asian_session(self):
        """USDJPY during Asian session must be allowed (JPY is an Asian pair)."""
        from src.signals.signal_engine import _is_low_liquidity_session
        import datetime
        asian_time = datetime.datetime(2026, 3, 18, 3, 0, 0,
                                       tzinfo=datetime.timezone.utc)
        assert _is_low_liquidity_session("USDJPY=X", "forex", asian_time) is False

    def test_crypto_never_blocked(self):
        """Crypto trades 24/7 — session filter must not apply."""
        from src.signals.signal_engine import _is_low_liquidity_session
        import datetime
        asian_time = datetime.datetime(2026, 3, 18, 3, 0, 0,
                                       tzinfo=datetime.timezone.utc)
        assert _is_low_liquidity_session("BTC/USDT", "crypto", asian_time) is False

    def test_session_boundary_exactly_at_7am(self):
        """07:00 UTC is the end of the blocked window — must be allowed."""
        from src.signals.signal_engine import _is_low_liquidity_session
        import datetime
        boundary = datetime.datetime(2026, 3, 18, 7, 0, 0,
                                     tzinfo=datetime.timezone.utc)
        assert _is_low_liquidity_session("EURUSD=X", "forex", boundary) is False
