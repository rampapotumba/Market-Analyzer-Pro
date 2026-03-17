"""Multi-Timeframe Filter for signal confirmation."""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Timeframe hierarchy (lower index = lower timeframe)
TIMEFRAME_HIERARCHY = ["M1", "M5", "M15", "H1", "H4", "D1", "W1", "MN1"]

# MTF multipliers
MTF_MULTIPLIERS = {
    "agree_2": 1.2,   # Signal agrees with 2 higher timeframes
    "agree_1": 1.0,   # Signal agrees with 1 higher timeframe
    "neutral": 1.0,   # No higher timeframe data available
    "disagree_1": 0.7,  # Signal disagrees with 1 higher timeframe
    "disagree_2": 0.4,  # Signal disagrees with 2 higher timeframes
}


def _get_direction_from_score(score: float) -> int:
    """Get direction integer from score. Returns -1, 0, or 1."""
    if score >= 30:
        return 1
    if score <= -30:
        return -1
    return 0


class MTFFilter:
    """
    Multi-Timeframe confirmation filter.

    Adjusts composite signal score based on agreement/disagreement
    with higher timeframe signals.
    """

    def apply(
        self,
        score: float,
        working_tf: str,
        higher_tf_signals: list[dict[str, Any]],
    ) -> float:
        """
        Apply MTF multiplier to the composite score.

        Args:
            score: Base composite score [-100, +100]
            working_tf: Working timeframe (e.g., "H1")
            higher_tf_signals: List of dicts with 'timeframe' and 'score' keys.
                               Should contain signals from higher TFs.

        Returns:
            Adjusted score [-100, +100]
        """
        if not higher_tf_signals:
            return score

        working_direction = _get_direction_from_score(score)
        if working_direction == 0:
            # HOLD signals don't get multiplied
            return score

        # Only consider the 2 nearest higher timeframes
        working_idx = TIMEFRAME_HIERARCHY.index(working_tf) if working_tf in TIMEFRAME_HIERARCHY else -1

        # Filter and sort higher TF signals
        relevant_tfs = []
        for tf_signal in higher_tf_signals:
            tf = tf_signal.get("timeframe", "")
            tf_score = tf_signal.get("score", 0.0)
            if tf in TIMEFRAME_HIERARCHY:
                tf_idx = TIMEFRAME_HIERARCHY.index(tf)
                if tf_idx > working_idx:
                    tf_direction = _get_direction_from_score(tf_score)
                    relevant_tfs.append({
                        "timeframe": tf,
                        "idx": tf_idx,
                        "direction": tf_direction,
                    })

        if not relevant_tfs:
            return score

        # Sort by distance from working TF (nearest first)
        relevant_tfs.sort(key=lambda x: x["idx"] - working_idx)

        # Consider up to 2 nearest higher TFs
        nearest = relevant_tfs[:2]

        agreements = sum(1 for tf in nearest if tf["direction"] == working_direction and tf["direction"] != 0)
        disagreements = sum(1 for tf in nearest if tf["direction"] != 0 and tf["direction"] != working_direction)

        if disagreements >= 2:
            multiplier = MTF_MULTIPLIERS["disagree_2"]
        elif disagreements == 1 and agreements == 0:
            multiplier = MTF_MULTIPLIERS["disagree_1"]
        elif agreements >= 2:
            multiplier = MTF_MULTIPLIERS["agree_2"]
        elif agreements == 1:
            multiplier = MTF_MULTIPLIERS["agree_1"]
        else:
            multiplier = MTF_MULTIPLIERS["neutral"]

        adjusted = score * multiplier
        adjusted = max(-100.0, min(100.0, adjusted))

        logger.debug(
            f"[MTF] TF={working_tf}, score={score:.2f}, "
            f"agreements={agreements}, disagreements={disagreements}, "
            f"multiplier={multiplier}, adjusted={adjusted:.2f}"
        )

        return adjusted

    def get_timeframe_weights(self, timeframe: str) -> dict[str, float]:
        """
        Get analysis weights for a given timeframe.

        Returns:
            dict with keys: ta, fa, sentiment, geo
        """
        scalping_tfs = {"M1", "M5", "M15"}
        daytrading_tfs = {"H1"}
        swing_tfs = {"H4", "D1"}
        positional_tfs = {"W1"}
        macro_tfs = {"MN1"}

        if timeframe in scalping_tfs:
            return {"ta": 0.90, "fa": 0.00, "sentiment": 0.05, "geo": 0.05}
        elif timeframe in daytrading_tfs:
            return {"ta": 0.70, "fa": 0.10, "sentiment": 0.15, "geo": 0.05}
        elif timeframe in swing_tfs:
            return {"ta": 0.45, "fa": 0.25, "sentiment": 0.20, "geo": 0.10}
        elif timeframe in positional_tfs:
            return {"ta": 0.20, "fa": 0.40, "sentiment": 0.25, "geo": 0.15}
        elif timeframe in macro_tfs:
            return {"ta": 0.10, "fa": 0.50, "sentiment": 0.20, "geo": 0.20}
        else:
            return {"ta": 0.45, "fa": 0.25, "sentiment": 0.20, "geo": 0.10}

    def get_horizon(self, timeframe: str) -> str:
        """Get trading horizon description for a timeframe."""
        horizons = {
            "M1": "1-5 minutes",
            "M5": "5-30 minutes",
            "M15": "15 minutes - 2 hours",
            "H1": "1-8 hours",
            "H4": "4-24 hours",
            "D1": "1-5 days",
            "W1": "1-4 weeks",
            "MN1": "1-6 months",
        }
        return horizons.get(timeframe, "unknown")
