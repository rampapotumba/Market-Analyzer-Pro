"""Tests for Phase 3.3.1 — Regime-adaptive SL/TP."""

import sys
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

# Stub asyncpg (not installed in dev env, required by database.engine at module level)
sys.modules.setdefault("asyncpg", MagicMock())
_stub_engine = MagicMock()
_stub_engine.async_session_factory = MagicMock()
sys.modules.setdefault("src.database.engine", _stub_engine)

from src.signals.risk_manager_v2 import (
    REGIME_SL_MULTIPLIERS,
    REGIME_TP1_RR,
    REGIME_TP2_RR,
    RiskManagerV2,
)


# ── Table structure tests ─────────────────────────────────────────────────────


def test_regime_sl_multipliers_high_volatility() -> None:
    """HIGH_VOLATILITY uses SL multiplier 2.5 (wider SL for volatile markets)."""
    assert REGIME_SL_MULTIPLIERS["HIGH_VOLATILITY"] == 2.5


def test_regime_sl_multipliers_low_volatility() -> None:
    """LOW_VOLATILITY uses SL multiplier 1.0 (tight SL when quiet)."""
    assert REGIME_SL_MULTIPLIERS["LOW_VOLATILITY"] == 1.0


def test_regime_sl_multipliers_ranging() -> None:
    """RANGING uses SL multiplier 1.2."""
    assert REGIME_SL_MULTIPLIERS["RANGING"] == 1.2


def test_regime_tp1_rr_ranging() -> None:
    """RANGING regime uses TP1 R:R of 1.5 (conservative target)."""
    assert REGIME_TP1_RR["RANGING"] == 1.5


def test_regime_tp1_rr_strong_trend() -> None:
    """STRONG_TREND_BULL uses TP1 R:R of 2.0."""
    assert REGIME_TP1_RR["STRONG_TREND_BULL"] == 2.0


def test_regime_tp2_rr_ranging_lt_strong_trend() -> None:
    """TP2 R:R in RANGING (2.5) is less than STRONG_TREND (3.5)."""
    assert REGIME_TP2_RR["RANGING"] < REGIME_TP2_RR["STRONG_TREND_BULL"]


def test_all_regimes_have_sl_entry() -> None:
    """Every regime has a configured SL multiplier."""
    required = [
        "STRONG_TREND_BULL", "STRONG_TREND_BEAR",
        "WEAK_TREND_BULL", "WEAK_TREND_BEAR",
        "RANGING", "HIGH_VOLATILITY", "LOW_VOLATILITY",
    ]
    for regime in required:
        assert regime in REGIME_SL_MULTIPLIERS, f"Missing SL multiplier for {regime}"
        assert regime in REGIME_TP1_RR, f"Missing TP1 RR for {regime}"
        assert regime in REGIME_TP2_RR, f"Missing TP2 RR for {regime}"


# ── calculate_levels_for_regime() ─────────────────────────────────────────────


def test_calculate_levels_for_regime_ranging_tp1_rr() -> None:
    """RANGING regime → TP1 R:R = 1.5 (not the legacy 2.0)."""
    rm = RiskManagerV2()
    entry = Decimal("1.1000")
    atr = Decimal("0.0050")

    levels = rm.calculate_levels_for_regime(
        entry=entry, atr=atr, direction="LONG", regime="RANGING"
    )

    sl = levels["stop_loss"]
    tp1 = levels["take_profit_1"]
    assert sl is not None and tp1 is not None

    sl_dist = abs(entry - sl)
    tp1_dist = abs(tp1 - entry)
    actual_rr = float(tp1_dist / sl_dist)
    assert actual_rr == pytest.approx(1.5, rel=0.01), (
        f"RANGING TP1_RR should be 1.5, got {actual_rr:.2f}"
    )


def test_calculate_levels_for_regime_high_volatility_sl() -> None:
    """HIGH_VOLATILITY → SL multiplier 2.5 (wider SL)."""
    rm = RiskManagerV2()
    entry = Decimal("1.1000")
    atr = Decimal("0.0050")

    levels = rm.calculate_levels_for_regime(
        entry=entry, atr=atr, direction="LONG", regime="HIGH_VOLATILITY"
    )
    sl = levels["stop_loss"]
    assert sl is not None

    expected_sl = entry - atr * Decimal("2.5")
    assert abs(sl - expected_sl) < Decimal("0.000001")


def test_calculate_levels_for_regime_strong_trend_tp1_rr() -> None:
    """STRONG_TREND_BULL → TP1 R:R = 2.0."""
    rm = RiskManagerV2()
    entry = Decimal("50000")
    atr = Decimal("500")

    levels = rm.calculate_levels_for_regime(
        entry=entry, atr=atr, direction="LONG", regime="STRONG_TREND_BULL"
    )
    sl = levels["stop_loss"]
    tp1 = levels["take_profit_1"]
    assert sl is not None and tp1 is not None

    sl_dist = abs(entry - sl)
    tp1_dist = abs(tp1 - entry)
    actual_rr = float(tp1_dist / sl_dist)
    assert actual_rr == pytest.approx(2.0, rel=0.01)


def test_calculate_levels_for_regime_short_direction() -> None:
    """SHORT direction: SL above entry, TP1 below entry."""
    rm = RiskManagerV2()
    entry = Decimal("1.1000")
    atr = Decimal("0.0050")

    levels = rm.calculate_levels_for_regime(
        entry=entry, atr=atr, direction="SHORT", regime="RANGING"
    )
    sl = levels["stop_loss"]
    tp1 = levels["take_profit_1"]
    assert sl is not None and tp1 is not None
    assert sl > entry, "SHORT: SL must be above entry"
    assert tp1 < entry, "SHORT: TP1 must be below entry"


def test_calculate_levels_for_regime_tp2_gt_tp1_long() -> None:
    """LONG: TP2 must be further from entry than TP1."""
    rm = RiskManagerV2()
    entry = Decimal("1.1000")
    atr = Decimal("0.0050")

    levels = rm.calculate_levels_for_regime(
        entry=entry, atr=atr, direction="LONG", regime="WEAK_TREND_BULL"
    )
    assert levels["take_profit_2"] > levels["take_profit_1"]


def test_calculate_levels_for_regime_returns_rr() -> None:
    """risk_reward_1 key is populated with a reasonable value."""
    rm = RiskManagerV2()
    levels = rm.calculate_levels_for_regime(
        entry=Decimal("100.0"),
        atr=Decimal("1.0"),
        direction="LONG",
        regime="RANGING",
    )
    assert levels["risk_reward_1"] is not None
    assert float(levels["risk_reward_1"]) > 0


def test_calculate_levels_for_regime_unknown_regime_falls_to_ranging() -> None:
    """Unknown regime falls back to RANGING defaults."""
    rm = RiskManagerV2()
    levels_unknown = rm.calculate_levels_for_regime(
        entry=Decimal("1.1000"), atr=Decimal("0.0050"),
        direction="LONG", regime="UNKNOWN_REGIME",
    )
    levels_ranging = rm.calculate_levels_for_regime(
        entry=Decimal("1.1000"), atr=Decimal("0.0050"),
        direction="LONG", regime="RANGING",
    )
    assert levels_unknown["stop_loss"] == levels_ranging["stop_loss"]
    assert levels_unknown["take_profit_1"] == levels_ranging["take_profit_1"]
