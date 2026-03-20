"""Pluggable strategy interface for BacktestEngine (TASK-V7-20).

Registry maps strategy name strings to BaseStrategy subclasses.
BacktestEngine accepts a strategy instance or resolves one by name.

Usage:
    from src.backtesting.strategies import STRATEGY_REGISTRY
    strategy = STRATEGY_REGISTRY["composite"]()
"""

from src.backtesting.strategies.base import BaseStrategy
from src.backtesting.strategies.composite_score import CompositeScoreStrategy

# Maps strategy name → class.  Add new strategies here.
STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    "composite": CompositeScoreStrategy,
}

__all__ = [
    "BaseStrategy",
    "CompositeScoreStrategy",
    "STRATEGY_REGISTRY",
]
