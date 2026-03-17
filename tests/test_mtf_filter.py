"""Tests for Multi-Timeframe Filter."""

import pytest

from src.signals.mtf_filter import (
    MTFFilter,
    MTF_MULTIPLIERS,
    TIMEFRAME_HIERARCHY,
    _get_direction_from_score,
)


# ── Direction Helper ──────────────────────────────────────────────────────────

class TestGetDirectionFromScore:
    """Test the score → direction integer helper."""

    def test_above_threshold_returns_long(self):
        assert _get_direction_from_score(30.0) == 1
        assert _get_direction_from_score(70.0) == 1
        assert _get_direction_from_score(100.0) == 1

    def test_below_threshold_returns_short(self):
        assert _get_direction_from_score(-30.0) == -1
        assert _get_direction_from_score(-70.0) == -1
        assert _get_direction_from_score(-100.0) == -1

    def test_inside_band_returns_hold(self):
        assert _get_direction_from_score(0.0) == 0
        assert _get_direction_from_score(29.9) == 0
        assert _get_direction_from_score(-29.9) == 0

    def test_exact_positive_boundary(self):
        assert _get_direction_from_score(30.0) == 1

    def test_exact_negative_boundary(self):
        assert _get_direction_from_score(-30.0) == -1


# ── MTFFilter.apply ───────────────────────────────────────────────────────────

class TestMTFFilterApply:
    """Test the composite score adjustment logic."""

    # ── Agree cases ──

    def test_agree_2_tfs_multiplier_1_2(self):
        mtf = MTFFilter()
        adjusted = mtf.apply(50.0, "H1", [
            {"timeframe": "H4", "score": 60.0},
            {"timeframe": "D1", "score": 70.0},
        ])
        assert abs(adjusted - 50.0 * MTF_MULTIPLIERS["agree_2"]) < 0.001

    def test_agree_1_tf_no_boost(self):
        mtf = MTFFilter()
        adjusted = mtf.apply(50.0, "H1", [
            {"timeframe": "H4", "score": 60.0},
        ])
        assert abs(adjusted - 50.0 * MTF_MULTIPLIERS["agree_1"]) < 0.001

    # ── Disagree cases ──

    def test_disagree_2_tfs_multiplier_0_4(self):
        mtf = MTFFilter()
        adjusted = mtf.apply(50.0, "H1", [
            {"timeframe": "H4", "score": -60.0},
            {"timeframe": "D1", "score": -70.0},
        ])
        assert abs(adjusted - 50.0 * MTF_MULTIPLIERS["disagree_2"]) < 0.001

    def test_disagree_1_tf_only_multiplier_0_7(self):
        mtf = MTFFilter()
        adjusted = mtf.apply(50.0, "H1", [
            {"timeframe": "H4", "score": -60.0},
        ])
        assert abs(adjusted - 50.0 * MTF_MULTIPLIERS["disagree_1"]) < 0.001

    def test_neutral_when_all_hold(self):
        """Higher TFs in HOLD zone → neutral multiplier."""
        mtf = MTFFilter()
        adjusted = mtf.apply(50.0, "H1", [
            {"timeframe": "H4", "score": 10.0},   # HOLD (direction=0)
            {"timeframe": "D1", "score": -10.0},  # HOLD (direction=0)
        ])
        assert abs(adjusted - 50.0 * MTF_MULTIPLIERS["neutral"]) < 0.001

    # ── Edge cases ──

    def test_empty_higher_tfs_unchanged(self):
        mtf = MTFFilter()
        assert mtf.apply(45.0, "H1", []) == 45.0

    def test_hold_score_not_multiplied(self):
        """Working direction = 0 (HOLD) → score returned as-is."""
        mtf = MTFFilter()
        adjusted = mtf.apply(10.0, "H1", [
            {"timeframe": "H4", "score": 60.0},
            {"timeframe": "D1", "score": 60.0},
        ])
        assert adjusted == 10.0

    def test_score_capped_at_positive_100(self):
        mtf = MTFFilter()
        adjusted = mtf.apply(90.0, "H1", [
            {"timeframe": "H4", "score": 60.0},
            {"timeframe": "D1", "score": 60.0},
        ])
        assert adjusted <= 100.0

    def test_score_capped_at_negative_100(self):
        mtf = MTFFilter()
        adjusted = mtf.apply(-90.0, "H1", [
            {"timeframe": "H4", "score": -60.0},
            {"timeframe": "D1", "score": -60.0},
        ])
        assert adjusted >= -100.0

    def test_lower_tf_signals_ignored(self):
        """H1 signal should ignore H4 input when working TF is D1."""
        mtf = MTFFilter()
        adjusted = mtf.apply(50.0, "D1", [
            {"timeframe": "H1", "score": -80.0},  # Lower than D1
            {"timeframe": "H4", "score": -80.0},  # Lower than D1
        ])
        # No relevant higher TFs → unchanged
        assert adjusted == 50.0

    def test_only_2_nearest_higher_tfs_considered(self):
        """Only the 2 closest higher TFs matter, not all of them."""
        mtf = MTFFilter()
        # Working: M15, higher TFs: H1 (close), H4, D1, W1 (far)
        # H1 and H4 agree (LONG), D1 and W1 disagree (SHORT)
        # Only H1 + H4 should be used → agree_2
        adjusted = mtf.apply(50.0, "M15", [
            {"timeframe": "H1", "score": 60.0},   # agree
            {"timeframe": "H4", "score": 60.0},   # agree
            {"timeframe": "D1", "score": -80.0},  # disagree (further away)
            {"timeframe": "W1", "score": -80.0},  # disagree (furthest)
        ])
        assert abs(adjusted - 50.0 * 1.2) < 0.001

    def test_unknown_working_tf_handled(self):
        """Unknown working TF should not crash; idx=-1 treats all as higher."""
        mtf = MTFFilter()
        adjusted = mtf.apply(50.0, "UNKNOWN", [
            {"timeframe": "H4", "score": 60.0},
        ])
        assert -100.0 <= adjusted <= 100.0

    def test_missing_timeframe_key_skipped(self):
        """Higher TF signal without 'timeframe' key should be skipped."""
        mtf = MTFFilter()
        adjusted = mtf.apply(50.0, "H1", [
            {"score": 60.0},  # no 'timeframe' key
        ])
        assert adjusted == 50.0

    def test_negative_working_score_agree_decreases_magnitude(self):
        """For SHORT signal (-50), agreeing TFs apply ×1.2 (more negative)."""
        mtf = MTFFilter()
        adjusted = mtf.apply(-50.0, "H1", [
            {"timeframe": "H4", "score": -60.0},
            {"timeframe": "D1", "score": -60.0},
        ])
        assert abs(adjusted - (-50.0 * 1.2)) < 0.001


# ── get_timeframe_weights ─────────────────────────────────────────────────────

class TestGetTimeframeWeights:
    """Test weight retrieval for each timeframe class."""

    @pytest.mark.parametrize("tf", ["M1", "M5", "M15"])
    def test_scalping_weights_sum_to_1(self, tf):
        mtf = MTFFilter()
        weights = mtf.get_timeframe_weights(tf)
        assert abs(sum(weights.values()) - 1.0) < 1e-10

    def test_daytrading_weights_sum_to_1(self):
        assert abs(sum(MTFFilter().get_timeframe_weights("H1").values()) - 1.0) < 1e-10

    @pytest.mark.parametrize("tf", ["H4", "D1"])
    def test_swing_weights_sum_to_1(self, tf):
        assert abs(sum(MTFFilter().get_timeframe_weights(tf).values()) - 1.0) < 1e-10

    def test_positional_weights_sum_to_1(self):
        assert abs(sum(MTFFilter().get_timeframe_weights("W1").values()) - 1.0) < 1e-10

    def test_macro_weights_sum_to_1(self):
        assert abs(sum(MTFFilter().get_timeframe_weights("MN1").values()) - 1.0) < 1e-10

    def test_unknown_tf_defaults_and_sums_to_1(self):
        weights = MTFFilter().get_timeframe_weights("XUNKNOWN")
        assert abs(sum(weights.values()) - 1.0) < 1e-10

    def test_scalping_ta_dominated(self):
        """Scalping should be 90% TA, 0% FA."""
        w = MTFFilter().get_timeframe_weights("M1")
        assert w["ta"] == 0.90
        assert w["fa"] == 0.00

    def test_macro_fa_dominated(self):
        """Monthly should be 50% FA."""
        w = MTFFilter().get_timeframe_weights("MN1")
        assert w["fa"] == 0.50

    def test_all_weights_keys_present(self):
        """All weight dicts must have ta, fa, sentiment, geo."""
        required = {"ta", "fa", "sentiment", "geo"}
        for tf in TIMEFRAME_HIERARCHY:
            weights = MTFFilter().get_timeframe_weights(tf)
            assert set(weights.keys()) == required, f"Missing keys for {tf}"


# ── get_horizon ───────────────────────────────────────────────────────────────

class TestGetHorizon:
    """Test horizon description strings."""

    @pytest.mark.parametrize("tf, keyword", [
        ("M1", "minute"),
        ("M5", "minute"),
        ("M15", "minute"),
        ("H1", "hour"),
        ("H4", "hour"),
        ("D1", "day"),
        ("W1", "week"),
        ("MN1", "month"),
    ])
    def test_horizon_contains_keyword(self, tf, keyword):
        assert keyword in MTFFilter().get_horizon(tf)

    def test_unknown_tf_returns_unknown(self):
        assert MTFFilter().get_horizon("XYZABC") == "unknown"
