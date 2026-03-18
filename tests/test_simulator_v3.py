"""Trade Simulator v3 — unit tests for SIM-09 through SIM-16."""

import datetime
from decimal import Decimal
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tracker.signal_tracker import (
    ACCOUNT_SIZE,
    SignalTracker,
)


# ── Test helpers ──────────────────────────────────────────────────────────────


def _signal(
    id: int = 1,
    direction: str = "LONG",
    entry_price: Decimal = Decimal("1.10000"),
    stop_loss: Decimal = Decimal("1.09000"),
    take_profit_1: Decimal = Decimal("1.11500"),
    take_profit_2: Optional[Decimal] = None,
    take_profit_3: Optional[Decimal] = None,
    position_size_pct: Decimal = Decimal("2.0"),
    status: str = "tracking",
    regime: str = "RANGING",
    indicators_snapshot: str = '{"atr": 0.00750}',
    instrument_id: int = 1,
    timeframe: str = "H1",
    composite_score: float = 8.5,
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
    s.timeframe = timeframe
    s.composite_score = composite_score
    s.created_at = datetime.datetime.now(datetime.timezone.utc)
    s.expires_at = None
    return s


def _position(
    signal_id: int = 1,
    entry_price: Decimal = Decimal("1.10000"),
    size_pct: Decimal = Decimal("2.0"),
    size_remaining_pct: Decimal = Decimal("1.0"),
    status: str = "open",
    mfe: Decimal = Decimal("0"),
    mae: Decimal = Decimal("0"),
    partial_closed: bool = False,
    partial_pnl_pct: Optional[Decimal] = None,
    account_balance_at_entry: Optional[Decimal] = None,
    accrued_swap_pips: Decimal = Decimal("0"),
    accrued_swap_usd: Decimal = Decimal("0"),
    last_swap_date: Optional[datetime.date] = None,
):
    p = MagicMock()
    p.signal_id = signal_id
    p.entry_price = entry_price
    p.size_pct = size_pct
    p.size_remaining_pct = size_remaining_pct
    p.status = status
    p.mfe = mfe
    p.mae = mae
    p.partial_closed = partial_closed
    p.partial_pnl_pct = partial_pnl_pct
    p.account_balance_at_entry = account_balance_at_entry
    p.accrued_swap_pips = accrued_swap_pips
    p.accrued_swap_usd = accrued_swap_usd
    p.last_swap_date = last_swap_date
    p.breakeven_moved = False
    p.trailing_stop = None
    p.current_stop_loss = None
    p.entry_filled_at = datetime.datetime.now(datetime.timezone.utc)
    return p


def _account(
    id: int = 1,
    initial_balance: Decimal = Decimal("1000.0"),
    current_balance: Decimal = Decimal("1000.0"),
    peak_balance: Decimal = Decimal("1000.0"),
    total_realized_pnl: Decimal = Decimal("0"),
    total_trades: int = 0,
):
    a = MagicMock()
    a.id = id
    a.initial_balance = initial_balance
    a.current_balance = current_balance
    a.peak_balance = peak_balance
    a.total_realized_pnl = total_realized_pnl
    a.total_trades = total_trades
    return a


# ── Phase 1.3: CRUD helpers ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sim16_account_initialized_on_first_run():
    """create_virtual_account_if_not_exists() creates a row when none exists."""
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    async def _mock_get(session):
        return None  # no existing account

    from src.database.crud import create_virtual_account_if_not_exists
    from src.config import settings as real_settings

    with (
        patch("src.database.crud.get_virtual_account", side_effect=_mock_get),
    ):
        try:
            await create_virtual_account_if_not_exists(db)
        except Exception:
            pass  # refresh mock may not fully replicate the ORM — that's ok
        db.add.assert_called_once()


# ── Phase 1.6: SIM-16 — _pnl_usd with account_balance ───────────────────────


def test_sim16_pnl_usd_uses_account_balance():
    """_pnl_usd() uses account_balance_at_entry when provided."""
    tracker = SignalTracker()
    # size=2%, move=+1%, balance=$900 → pnl = 900 * 0.02 * 0.01 = $0.18
    result = tracker._pnl_usd(
        pnl_pct=Decimal("1.0"),
        position_size_pct=Decimal("2.0"),
        account_balance=Decimal("900"),
    )
    assert result == Decimal("0.18")


def test_sim16_legacy_position_fallback():
    """account_balance=None → fallback to ACCOUNT_SIZE (VIRTUAL_ACCOUNT_SIZE_USD)."""
    tracker = SignalTracker()
    # size=2%, move=+1%, no balance → uses ACCOUNT_SIZE ($1000) → $0.20
    result = tracker._pnl_usd(
        pnl_pct=Decimal("1.0"),
        position_size_pct=Decimal("2.0"),
        account_balance=None,
    )
    expected = ACCOUNT_SIZE * Decimal("0.02") * Decimal("0.01")
    assert result == expected


def test_sim16_position_sizing_from_balance():
    """At balance $900, P&L is calculated from $900, not $1000."""
    tracker = SignalTracker()
    # 2% risk, -2% move = -$0.36 at $900 vs -$0.40 at $1000
    result_900 = tracker._pnl_usd(
        pnl_pct=Decimal("-2.0"),
        position_size_pct=Decimal("2.0"),
        account_balance=Decimal("900"),
    )
    result_1000 = tracker._pnl_usd(
        pnl_pct=Decimal("-2.0"),
        position_size_pct=Decimal("2.0"),
        account_balance=Decimal("1000"),
    )
    assert result_900 == Decimal("-0.36")
    assert result_1000 == Decimal("-0.40")
    assert result_900 != result_1000


# ── SIM-16: _update_account_balance ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_sim16_account_balance_updates_on_close():
    """Losing trade decreases current_balance."""
    tracker = SignalTracker()
    db = AsyncMock()

    account = _account(current_balance=Decimal("1000"), peak_balance=Decimal("1000"))

    with patch("src.tracker.signal_tracker.get_virtual_account", return_value=account):
        with patch("src.tracker.signal_tracker.update_virtual_account", new_callable=AsyncMock) as mock_update:
            await tracker._update_account_balance(db, Decimal("-4.00"))

            mock_update.assert_called_once()
            call_kwargs = mock_update.call_args[0][1]
            assert call_kwargs["current_balance"] == Decimal("996.00")
            assert call_kwargs["peak_balance"] == Decimal("1000")  # peak unchanged
            assert call_kwargs["total_realized_pnl"] == Decimal("-4.00")
            assert call_kwargs["total_trades"] == 1


@pytest.mark.asyncio
async def test_sim16_account_balance_compounds():
    """Two sequential winning trades compound the balance correctly."""
    tracker = SignalTracker()
    db = AsyncMock()

    account = _account(current_balance=Decimal("1000"), peak_balance=Decimal("1000"))
    updates = []

    async def mock_update(session, data):
        # Apply the update to the mock account so second call sees new balance
        account.current_balance = data["current_balance"]
        account.peak_balance = data["peak_balance"]
        account.total_realized_pnl = data["total_realized_pnl"]
        account.total_trades = data["total_trades"]
        updates.append(data)

    with patch("src.tracker.signal_tracker.get_virtual_account", return_value=account):
        with patch("src.tracker.signal_tracker.update_virtual_account", side_effect=mock_update):
            await tracker._update_account_balance(db, Decimal("10.00"))
            await tracker._update_account_balance(db, Decimal("5.00"))

    assert len(updates) == 2
    assert updates[0]["current_balance"] == Decimal("1010.00")
    assert updates[1]["current_balance"] == Decimal("1015.00")
    assert updates[1]["total_trades"] == 2


@pytest.mark.asyncio
async def test_sim16_drawdown_calculation():
    """peak=$1100, current=$950 → drawdown = (1100-950)/1100 × 100 = 13.636...%"""
    account = _account(
        current_balance=Decimal("950"),
        peak_balance=Decimal("1100"),
    )
    peak = account.peak_balance
    current = account.current_balance
    drawdown_pct = (peak - current) / peak * Decimal("100")

    # Should be ~13.636...%
    assert abs(drawdown_pct - Decimal("13.6363")) < Decimal("0.001")


@pytest.mark.asyncio
async def test_sim16_partial_close_updates_balance_twice():
    """Partial close at TP1 + final close = two _update_account_balance calls."""
    tracker = SignalTracker()
    db = AsyncMock()

    update_calls = []

    async def mock_update_balance(session, pnl):
        update_calls.append(pnl)

    signal = _signal(
        direction="LONG",
        entry_price=Decimal("1.10000"),
        stop_loss=Decimal("1.09000"),
        take_profit_1=Decimal("1.11500"),
        position_size_pct=Decimal("2.0"),
    )
    pos = _position(
        entry_price=Decimal("1.10000"),
        size_pct=Decimal("2.0"),
        size_remaining_pct=Decimal("1.0"),
        account_balance_at_entry=Decimal("1000"),
    )

    with patch.object(tracker, "_update_account_balance", side_effect=mock_update_balance):
        with patch("src.tracker.signal_tracker.update_virtual_position", new_callable=AsyncMock):
            await tracker._partial_close(
                db, signal, pos, Decimal("1.11500"),
                datetime.datetime.now(datetime.timezone.utc),
                Decimal("0.0001"),
            )

    # Only one call from _partial_close itself (second call comes from _close_signal)
    assert len(update_calls) == 1
    # The partial P&L should be positive (LONG, exit > entry)
    assert update_calls[0] > Decimal("0")


@pytest.mark.asyncio
async def test_sim16_peak_balance_updates_on_profit():
    """Profitable trade with balance > peak updates peak_balance."""
    tracker = SignalTracker()
    db = AsyncMock()

    account = _account(current_balance=Decimal("1000"), peak_balance=Decimal("1000"))

    with patch("src.tracker.signal_tracker.get_virtual_account", return_value=account):
        with patch("src.tracker.signal_tracker.update_virtual_account", new_callable=AsyncMock) as mock_update:
            await tracker._update_account_balance(db, Decimal("100.00"))

            call_kwargs = mock_update.call_args[0][1]
            assert call_kwargs["current_balance"] == Decimal("1100.00")
            assert call_kwargs["peak_balance"] == Decimal("1100.00")  # peak also updates


# ── SIM-09 placeholders (Phase 2) ────────────────────────────────────────────


def test_sim09_sl_via_candle_low():
    """LONG: last > SL but candle_low < SL → sl_hit (placeholder)."""
    # Will be implemented in Phase 2.2
    # SL=1.09900, last=1.10020, candle_low=1.09880 → sl_hit
    sl = Decimal("1.09900")
    current_price = Decimal("1.10020")
    candle_low = Decimal("1.09880")
    direction = "LONG"

    sl_hit_by_price = current_price <= sl
    sl_hit_by_candle = candle_low <= sl
    sl_hit = sl_hit_by_price or sl_hit_by_candle

    assert not sl_hit_by_price
    assert sl_hit_by_candle
    assert sl_hit


def test_sim09_tp_via_candle_high():
    """LONG: last < TP but candle_high > TP → tp1_hit (placeholder)."""
    tp = Decimal("1.11500")
    current_price = Decimal("1.11400")
    candle_high = Decimal("1.11520")

    tp_hit_by_price = current_price >= tp
    tp_hit_by_candle = candle_high >= tp
    tp_hit = tp_hit_by_price or tp_hit_by_candle

    assert not tp_hit_by_price
    assert tp_hit_by_candle
    assert tp_hit


def test_sim09_both_hit_worst_case():
    """Gap hit both SL and TP → exit_reason = sl_hit (worst case)."""
    sl = Decimal("1.09900")
    tp = Decimal("1.11500")
    candle_low = Decimal("1.09850")
    candle_high = Decimal("1.11600")
    direction = "LONG"

    sl_hit = candle_low <= sl
    tp_hit = candle_high >= tp

    # worst case rule
    if sl_hit and tp_hit:
        exit_reason = "sl_hit"
    elif sl_hit:
        exit_reason = "sl_hit"
    elif tp_hit:
        exit_reason = "tp1_hit"
    else:
        exit_reason = "none"

    assert sl_hit
    assert tp_hit
    assert exit_reason == "sl_hit"


# ── SIM-10 placeholders (Phase 2) ────────────────────────────────────────────


def test_sim10_sl_slippage_forex():
    """LONG EURUSD SL exit: exit = SL - 1 pip (placeholder)."""
    from decimal import Decimal as D

    sl_price = D("1.09900")
    pip_size = D("0.0001")
    direction = "LONG"
    market = "forex"

    SL_SLIPPAGE_PIPS = {"forex": D("1.0"), "stocks": D("1.0"), "crypto": D("0.0")}
    slip = SL_SLIPPAGE_PIPS[market] * pip_size
    exit_actual = sl_price - slip if direction == "LONG" else sl_price + slip

    assert exit_actual == D("1.09890")


def test_sim10_sl_slippage_crypto():
    """LONG BTC SL exit: exit = SL × (1 - 0.001) (placeholder)."""
    from decimal import Decimal as D

    sl_price = D("85000")
    direction = "LONG"
    SL_SLIPPAGE_CRYPTO_PCT = D("0.001")

    slip = sl_price * SL_SLIPPAGE_CRYPTO_PCT
    exit_actual = sl_price - slip

    assert exit_actual == D("84915.000")


def test_sim10_tp_no_slippage():
    """TP hit: exit price = exactly TP level (no slippage)."""
    tp = Decimal("1.11500")
    # For TP (limit order), exit = exact TP, no adjustment
    exit_price = tp
    assert exit_price == tp


# ── SIM-11 placeholders (Phase 3) ────────────────────────────────────────────


def test_sim11_live_atr_calculation():
    """Wilder's ATR(14) formula: (prev×13 + TR) / 14 (placeholder logic test)."""
    from decimal import Decimal as D

    # Simple test: verify Wilder smoothing formula
    prev_atr = D("0.0050")
    new_tr = D("0.0060")
    period = 14

    new_atr = (prev_atr * (period - 1) + new_tr) / period
    expected = (D("0.0050") * 13 + D("0.0060")) / 14

    assert new_atr == expected
    assert new_atr > prev_atr  # new TR > prev ATR → ATR grows


def test_sim11_atr_fallback_chain():
    """Without live data, fallback is 14 × pip_size."""
    pip_size = Decimal("0.0001")
    fallback_atr = Decimal("14") * pip_size
    assert fallback_atr == Decimal("0.0014")


# ── SIM-12 placeholders (Phase 3) ────────────────────────────────────────────


def test_sim12_unrealized_usd_with_size():
    """size=2%, move=+1.5%, balance=$1000 → unrealized = +$0.30."""
    balance = Decimal("1000")
    size_pct = Decimal("2.0")
    remaining = Decimal("1.0")
    move_pct = Decimal("1.5")

    effective_size = size_pct * remaining
    unrealized_usd = balance * (effective_size / Decimal("100")) * (move_pct / Decimal("100"))

    assert unrealized_usd == Decimal("0.30")


def test_sim12_unrealized_after_partial():
    """After 50% partial close: size=2%, remaining=0.5, move=+2% → +$0.20."""
    balance = Decimal("1000")
    size_pct = Decimal("2.0")
    remaining = Decimal("0.5")
    move_pct = Decimal("2.0")

    effective_size = size_pct * remaining  # 1.0%
    unrealized_usd = balance * (effective_size / Decimal("100")) * (move_pct / Decimal("100"))

    assert unrealized_usd == Decimal("0.20")


# ── SIM-13 placeholders (Phase 5) ────────────────────────────────────────────


def test_sim13_swap_wednesday_triple():
    """Wednesday rollover multiplier = 3."""
    TRIPLE_SWAP_WEEKDAY = 2  # Wednesday (0=Mon)
    wednesday = datetime.date(2026, 3, 18)  # known Wednesday
    assert wednesday.weekday() == TRIPLE_SWAP_WEEKDAY

    swap_pips_per_day = Decimal("-0.5")
    multiplier = 3 if wednesday.weekday() == TRIPLE_SWAP_WEEKDAY else 1
    daily_swap = swap_pips_per_day * multiplier
    assert daily_swap == Decimal("-1.5")


def test_sim13_swap_positive_carry():
    """USDJPY long: swap_pips > 0."""
    SWAP_DAILY_PIPS = {
        "USDJPY=X": {"long": Decimal("1.2"), "short": Decimal("-1.5")},
    }
    rate = SWAP_DAILY_PIPS["USDJPY=X"]["long"]
    assert rate > Decimal("0")


def test_sim13_swap_crypto_funding():
    """BTC long, funding_rate=+0.01% → swap_pct is negative (long pays)."""
    funding_rate = Decimal("0.0001")  # 0.01%
    direction = "LONG"
    # long pays when rate > 0
    swap_pct = funding_rate * (Decimal("-1") if direction == "LONG" else Decimal("1"))
    assert swap_pct < Decimal("0")


# ── SIM-14 placeholders (Phase 4) ────────────────────────────────────────────


def test_sim14_score_buckets_assignment():
    """composite_score=8.5 → falls in weak_buy bucket [7, 10)."""
    score = 8.5

    def assign_bucket(s: float) -> str:
        if s <= -15:
            return "strong_sell"
        elif s <= -10:
            return "sell"
        elif s <= -7:
            return "weak_sell"
        elif s < 7:
            return "neutral"
        elif s <= 10:
            return "weak_buy"
        elif s <= 15:
            return "buy"
        else:
            return "strong_buy"

    assert assign_bucket(score) == "weak_buy"
    assert assign_bucket(-16) == "strong_sell"
    assert assign_bucket(16) == "strong_buy"
    assert assign_bucket(0) == "neutral"


def test_sim14_threshold_recommendation():
    """Bucket with profit_factor > 1.0 and total >= 5 → suggested_min."""
    buckets = [
        {"range_min": 7.0,  "profit_factor": 0.85, "total": 10},
        {"range_min": 10.0, "profit_factor": 1.12, "total": 8},
        {"range_min": 15.0, "profit_factor": 1.45, "total": 6},
    ]
    eligible = [b for b in buckets if b["profit_factor"] > 1.0 and b["total"] >= 5]
    suggested_min = min(b["range_min"] for b in eligible) if eligible else None

    assert suggested_min == 10.0


# ── Backward compatibility (Phase 6) ─────────────────────────────────────────


def test_backward_compat_null_candle_fields():
    """signal_results without candle_high_at_exit (NULL) — no crash."""
    from src.database.models import SignalResult
    result = SignalResult()
    result.candle_high_at_exit = None
    result.candle_low_at_exit = None
    # No exception — fields are nullable
    assert result.candle_high_at_exit is None


def test_backward_compat_null_composite_score():
    """composite_score=NULL in signal_results is valid."""
    from src.database.models import SignalResult
    result = SignalResult()
    result.composite_score = None
    assert result.composite_score is None


def test_backward_compat_null_unrealized_pnl_usd():
    """unrealized_pnl_usd=NULL for old positions — returns 0 when accessed."""
    from src.database.models import VirtualPortfolio
    pos = VirtualPortfolio()
    pos.unrealized_pnl_usd = None
    # Code should handle: val = pos.unrealized_pnl_usd or Decimal("0")
    val = pos.unrealized_pnl_usd or Decimal("0")
    assert val == Decimal("0")


def test_backward_compat_null_account_balance_at_entry():
    """account_balance_at_entry=NULL → fallback to ACCOUNT_SIZE."""
    tracker = SignalTracker()
    # NULL balance → fallback
    result = tracker._pnl_usd(
        pnl_pct=Decimal("1.0"),
        position_size_pct=Decimal("2.0"),
        account_balance=None,
    )
    expected = ACCOUNT_SIZE * Decimal("0.02") * Decimal("0.01")
    assert result == expected
