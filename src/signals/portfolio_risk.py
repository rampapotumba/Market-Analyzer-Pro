"""Portfolio Risk Manager v2.

Responsibilities:
  - Track open positions by market type (forex / crypto / stocks)
  - Enforce max-open-per-market limits (3 / 2 / 5)
  - Compute portfolio heat (sum of per-trade risk as % of account)
  - Keep portfolio heat ≤ MAX_PORTFOLIO_HEAT (default 6%)
  - Correlation-aware position sizing adjustment
"""

import logging
from decimal import Decimal
from typing import Optional

import numpy as np
import pandas as pd

from src.config import settings

logger = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────────────
# Maximum open trades per market type
_MAX_OPEN: dict[str, int] = {
    "forex": settings.MAX_OPEN_FOREX,    # 3
    "crypto": settings.MAX_OPEN_CRYPTO,  # 2
    "stocks": settings.MAX_OPEN_STOCKS,  # 5
}

# Correlation threshold above which we consider instruments as correlated
_CORR_THRESHOLD = settings.CORRELATION_THRESHOLD  # 0.7

# Max portfolio heat (% of account)
_MAX_HEAT = settings.MAX_PORTFOLIO_HEAT  # 6.0


# ── Data classes (plain dicts for simplicity) ─────────────────────────────────

class OpenPosition:
    """Lightweight snapshot of an active virtual-portfolio position."""

    __slots__ = ("signal_id", "symbol", "market_type", "risk_pct", "direction")

    def __init__(
        self,
        signal_id: int,
        symbol: str,
        market_type: str,
        risk_pct: float,
        direction: str,
    ) -> None:
        self.signal_id = signal_id
        self.symbol = symbol
        self.market_type = market_type
        self.risk_pct = risk_pct
        self.direction = direction


class PortfolioRiskManager:
    """
    In-memory portfolio risk checker.

    The caller is responsible for keeping `positions` up-to-date by
    loading open rows from `virtual_portfolio` before each check.
    """

    def __init__(self, positions: Optional[list[OpenPosition]] = None) -> None:
        self._positions: list[OpenPosition] = positions or []

    # ── Public API ────────────────────────────────────────────────────────────

    def add_position(self, position: OpenPosition) -> None:
        self._positions.append(position)

    def remove_position(self, signal_id: int) -> None:
        self._positions = [p for p in self._positions if p.signal_id != signal_id]

    def portfolio_heat(self) -> float:
        """Total risk committed as % of account."""
        return sum(p.risk_pct for p in self._positions)

    def open_count(self, market_type: str) -> int:
        return sum(1 for p in self._positions if p.market_type == market_type)

    def can_open(
        self,
        symbol: str,
        market_type: str,
        risk_pct: float,
    ) -> tuple[bool, str]:
        """
        Check whether a new position can be opened.

        Returns:
            (allowed: bool, reason: str)
        """
        # 1. Market-type limit
        max_open = _MAX_OPEN.get(market_type, 5)
        if self.open_count(market_type) >= max_open:
            return False, (
                f"Max open {market_type} positions reached "
                f"({max_open}/{max_open})"
            )

        # 2. Portfolio heat
        new_heat = self.portfolio_heat() + risk_pct
        if new_heat > _MAX_HEAT:
            return False, (
                f"Portfolio heat would reach {new_heat:.1f}% "
                f"(max {_MAX_HEAT:.1f}%)"
            )

        return True, "OK"

    # ── Correlation ───────────────────────────────────────────────────────────

    def correlation_adjustment(
        self,
        new_symbol: str,
        new_direction: str,
        price_history: dict[str, pd.Series],
    ) -> float:
        """
        Compute a risk-scaling factor [0.5, 1.0] based on correlation with
        existing positions that share the same direction.

        If the new instrument is highly correlated (ρ > threshold) with an
        existing same-direction position, scale down size.

        Args:
            new_symbol: Symbol being considered
            new_direction: 'LONG' or 'SHORT'
            price_history: dict[symbol → close price Series (aligned index)]

        Returns:
            Multiplier in [0.5, 1.0]. 1.0 = no reduction.
        """
        if not self._positions or new_symbol not in price_history:
            return 1.0

        same_dir = [
            p for p in self._positions
            if p.direction == new_direction and p.symbol in price_history
        ]
        if not same_dir:
            return 1.0

        new_ret = price_history[new_symbol].pct_change().dropna()
        max_corr = 0.0

        for pos in same_dir:
            existing_ret = price_history[pos.symbol].pct_change().dropna()
            combined = pd.concat([new_ret, existing_ret], axis=1).dropna()
            if len(combined) < 10:
                continue
            corr_matrix = combined.corr().values
            corr = abs(corr_matrix[0, 1])
            if corr > max_corr:
                max_corr = corr

        if max_corr >= _CORR_THRESHOLD:
            # Linearly reduce from 1.0 (at threshold) to 0.5 (at ρ=1.0)
            excess = (max_corr - _CORR_THRESHOLD) / (1.0 - _CORR_THRESHOLD)
            multiplier = 1.0 - 0.5 * excess
            logger.debug(
                "Correlation %.2f with existing positions → size ×%.2f",
                max_corr,
                multiplier,
            )
            return round(multiplier, 4)

        return 1.0

    def correlation_score(
        self,
        new_symbol: str,
        new_direction: str,
        price_history: dict[str, pd.Series],
    ) -> float:
        """
        Convert correlation adjustment to a [-100, 0] score modifier.
        1.0 (no correlation) → 0; 0.5 (max correlated) → -50.
        """
        adj = self.correlation_adjustment(new_symbol, new_direction, price_history)
        return (adj - 1.0) * 100.0  # range: [-50, 0]

    # ── Heat summary ──────────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "total_positions": len(self._positions),
            "portfolio_heat": round(self.portfolio_heat(), 2),
            "max_heat": _MAX_HEAT,
            "heat_remaining": round(_MAX_HEAT - self.portfolio_heat(), 2),
            "by_market": {
                mtype: {
                    "open": self.open_count(mtype),
                    "max": _MAX_OPEN.get(mtype, 5),
                }
                for mtype in ("forex", "crypto", "stocks")
            },
        }
