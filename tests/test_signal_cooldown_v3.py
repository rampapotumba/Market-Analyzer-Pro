"""Tests for Phase 3.4 — Signal cooldown with direction-reversal bypass."""

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Stub asyncpg (not installed in dev env)
sys.modules.setdefault("asyncpg", MagicMock())
_redis_exceptions = MagicMock()
_redis_exceptions.RedisError = Exception
_redis_mod = MagicMock()
_redis_mod.exceptions = _redis_exceptions
_redis_asyncio = MagicMock()
sys.modules.setdefault("redis", _redis_mod)
sys.modules.setdefault("redis.asyncio", _redis_asyncio)
sys.modules.setdefault("redis.exceptions", _redis_exceptions)
_stub_engine = MagicMock()
_stub_engine.async_session_factory = MagicMock()
sys.modules.setdefault("src.database.engine", _stub_engine)

from src.signals.signal_engine_v2 import SignalEngineV2  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_signal(direction: str, minutes_ago: int) -> MagicMock:
    """Create a mock Signal with given direction and creation time."""
    sig = MagicMock()
    sig.direction = direction
    sig.created_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return sig


def _make_engine() -> SignalEngineV2:
    portfolio = MagicMock()
    portfolio.can_open.return_value = (True, "")
    portfolio.portfolio_heat.return_value = 0.0
    return SignalEngineV2(portfolio=portfolio)


# ── _check_cooldown ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cooldown_no_previous_signal_allows() -> None:
    """No previous signal → cooldown returns False (allow)."""
    engine = _make_engine()
    mock_db = MagicMock()

    with patch(
        "src.signals.signal_engine_v2.get_latest_signal_for_instrument",
        new=AsyncMock(return_value=None),
    ):
        blocked = await engine._check_cooldown(
            db=mock_db, instrument_id=1, timeframe="H1", current_direction="LONG"
        )
    assert blocked is False


@pytest.mark.asyncio
async def test_cooldown_outside_window_allows() -> None:
    """Signal older than cooldown window → not blocked."""
    engine = _make_engine()
    mock_db = MagicMock()
    # H1 cooldown = 60 min → signal 90 min ago is outside window
    last_signal = _make_signal("LONG", minutes_ago=90)

    with patch(
        "src.signals.signal_engine_v2.get_latest_signal_for_instrument",
        new=AsyncMock(return_value=last_signal),
    ):
        blocked = await engine._check_cooldown(
            db=mock_db, instrument_id=1, timeframe="H1", current_direction="LONG"
        )
    assert blocked is False


@pytest.mark.asyncio
async def test_cooldown_active_same_direction_blocks() -> None:
    """Cooldown active + same direction → blocked (return True)."""
    engine = _make_engine()
    mock_db = MagicMock()
    # H1 cooldown = 60 min → signal 30 min ago is within window
    last_signal = _make_signal("LONG", minutes_ago=30)

    with patch(
        "src.signals.signal_engine_v2.get_latest_signal_for_instrument",
        new=AsyncMock(return_value=last_signal),
    ):
        blocked = await engine._check_cooldown(
            db=mock_db, instrument_id=1, timeframe="H1", current_direction="LONG"
        )
    assert blocked is True


@pytest.mark.asyncio
async def test_cooldown_active_direction_reversal_bypasses() -> None:
    """Cooldown active + direction reversed → bypass (return False)."""
    engine = _make_engine()
    mock_db = MagicMock()
    # Within cooldown window, but direction reversed: LONG → SHORT
    last_signal = _make_signal("LONG", minutes_ago=30)

    with patch(
        "src.signals.signal_engine_v2.get_latest_signal_for_instrument",
        new=AsyncMock(return_value=last_signal),
    ):
        blocked = await engine._check_cooldown(
            db=mock_db, instrument_id=1, timeframe="H1", current_direction="SHORT"
        )
    assert blocked is False  # bypassed


@pytest.mark.asyncio
async def test_cooldown_short_to_long_reversal_bypasses() -> None:
    """SHORT → LONG reversal also bypasses cooldown."""
    engine = _make_engine()
    mock_db = MagicMock()
    last_signal = _make_signal("SHORT", minutes_ago=10)

    with patch(
        "src.signals.signal_engine_v2.get_latest_signal_for_instrument",
        new=AsyncMock(return_value=last_signal),
    ):
        blocked = await engine._check_cooldown(
            db=mock_db, instrument_id=1, timeframe="M15", current_direction="LONG"
        )
    assert blocked is False


@pytest.mark.asyncio
async def test_cooldown_different_timeframes_have_different_windows() -> None:
    """D1 cooldown = 1440 min; a signal 120 min ago should still be in cooldown."""
    engine = _make_engine()
    mock_db = MagicMock()
    last_signal = _make_signal("LONG", minutes_ago=120)

    with patch(
        "src.signals.signal_engine_v2.get_latest_signal_for_instrument",
        new=AsyncMock(return_value=last_signal),
    ):
        blocked = await engine._check_cooldown(
            db=mock_db, instrument_id=1, timeframe="D1", current_direction="LONG"
        )
    assert blocked is True  # D1 cooldown = 1440 min, 120 < 1440


@pytest.mark.asyncio
async def test_cooldown_guard_in_generate_skips_when_no_db() -> None:
    """generate() skips cooldown check entirely when db=None or instrument_id=None.

    The guard in generate() is:
        if db is not None and instrument_id is not None: ...
    So passing db=None means _check_cooldown is never called.
    """
    engine = _make_engine()
    # Verify that calling generate() without db doesn't raise (cooldown skipped)
    # We patch all the heavy deps so generate() actually runs through
    with patch(
        "src.signals.signal_engine_v2.get_latest_signal_for_instrument",
        new=AsyncMock(return_value=None),
    ) as mock_query, patch.object(
        engine._portfolio, "can_open", return_value=(False, "heat limit")
    ):
        # Pass db=None → cooldown check bypassed, portfolio check blocks it
        result = await engine.generate(
            symbol="EUR/USD",
            timeframe="H1",
            ta_score=10.0,
            fa_score=5.0,
            sentiment_score=5.0,
            geo_score=0.0,
            regime="RANGING",
            market_type="forex",
            entry_price=Decimal("1.1"),
            atr=Decimal("0.005"),
            db=None,
            instrument_id=None,
        )
        # _check_cooldown was NOT called because db=None
        mock_query.assert_not_called()
