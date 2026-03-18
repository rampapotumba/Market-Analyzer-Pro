"""Tests for Phase 3.1.1 — MTF Filter direction threshold fix."""

import pytest

from src.signals.mtf_filter import MTFFilter, _get_direction_from_score


# ── _get_direction_from_score ─────────────────────────────────────────────────


def test_direction_positive_above_threshold() -> None:
    """Score above BUY_THRESHOLD (7.0) returns +1."""
    assert _get_direction_from_score(10.0) == 1


def test_direction_negative_below_threshold() -> None:
    """Score below SELL_THRESHOLD (-7.0) returns -1."""
    assert _get_direction_from_score(-8.0) == -1


def test_direction_neutral_between_thresholds() -> None:
    """Score between thresholds returns 0."""
    assert _get_direction_from_score(3.0) == 0
    assert _get_direction_from_score(-3.0) == 0
    assert _get_direction_from_score(0.0) == 0


def test_direction_exactly_at_threshold() -> None:
    """Score at exactly BUY_THRESHOLD (7.0) returns +1."""
    assert _get_direction_from_score(7.0) == 1
    assert _get_direction_from_score(-7.0) == -1


def test_direction_old_threshold_is_no_longer_required() -> None:
    """Old threshold was ±30 — a score of 20 should now be directional, not neutral."""
    assert _get_direction_from_score(20.0) == 1
    assert _get_direction_from_score(-20.0) == -1
    # Under old ±30 threshold these would have been 0; under v3 (±7.0) they are directional


def test_direction_custom_threshold() -> None:
    """Custom thresholds work via explicit arguments."""
    assert _get_direction_from_score(15.0, buy_threshold=20.0, sell_threshold=-20.0) == 0
    assert _get_direction_from_score(25.0, buy_threshold=20.0, sell_threshold=-20.0) == 1


# ── MTFFilter.apply ───────────────────────────────────────────────────────────


def test_mtf_apply_agree_2_boosts_score() -> None:
    """Two higher-TF signals in same direction → multiplier 1.2."""
    mtf = MTFFilter()
    score = mtf.apply(
        score=10.0,
        working_tf="H1",
        higher_tf_signals=[
            {"timeframe": "H4", "score": 12.0},
            {"timeframe": "D1", "score": 14.0},
        ],
    )
    assert pytest.approx(score, rel=1e-3) == 10.0 * 1.2


def test_mtf_apply_disagree_2_reduces_score() -> None:
    """Two higher-TF signals in opposite direction → multiplier 0.4."""
    mtf = MTFFilter()
    score = mtf.apply(
        score=10.0,
        working_tf="H1",
        higher_tf_signals=[
            {"timeframe": "H4", "score": -12.0},
            {"timeframe": "D1", "score": -14.0},
        ],
    )
    assert pytest.approx(score, rel=1e-3) == 10.0 * 0.4


def test_mtf_apply_neutral_when_no_higher_tf() -> None:
    """No higher-TF signals → score unchanged."""
    mtf = MTFFilter()
    score = mtf.apply(score=10.0, working_tf="H1", higher_tf_signals=[])
    assert score == 10.0


def test_mtf_apply_hold_signal_not_multiplied() -> None:
    """HOLD signals (between ±7.0) are not multiplied."""
    mtf = MTFFilter()
    score = mtf.apply(
        score=3.0,
        working_tf="H1",
        higher_tf_signals=[{"timeframe": "H4", "score": 12.0}],
    )
    assert score == 3.0  # unchanged — working direction was 0


def test_mtf_filter_thresholds_via_init() -> None:
    """MTFFilter.__init__ accepts custom buy/sell thresholds."""
    mtf = MTFFilter(buy_threshold=15.0, sell_threshold=-15.0)
    # Score of 10.0 < 15.0 → neutral, no multiplier applied
    score = mtf.apply(
        score=10.0,
        working_tf="H1",
        higher_tf_signals=[{"timeframe": "H4", "score": 12.0}],
    )
    assert score == 10.0  # held neutral
