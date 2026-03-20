"""Pluggable strategy interface for BacktestEngine (TASK-V7-20).

Registry maps strategy name strings to BaseStrategy subclasses.
BacktestEngine accepts a strategy instance or resolves one by name.

Usage:
    from src.backtesting.strategies import STRATEGY_REGISTRY
    strategy = STRATEGY_REGISTRY["composite"]()
"""

from src.backtesting.strategies.base import BaseStrategy
from src.backtesting.strategies.composite_score import CompositeScoreStrategy
from src.backtesting.strategies.crypto_extreme import CryptoExtremeStrategy
from src.backtesting.strategies.divergence_hunter import DivergenceHunterStrategy
from src.backtesting.strategies.gold_macro import GoldMacroStrategy
from src.backtesting.strategies.session_sniper import SessionSniperStrategy
from src.backtesting.strategies.trend_rider import TrendRiderStrategy

# Maps strategy name → class.  Add new strategies here.
STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    "composite": CompositeScoreStrategy,
    "crypto_extreme": CryptoExtremeStrategy,
    "divergence_hunter": DivergenceHunterStrategy,
    "gold_macro": GoldMacroStrategy,
    "session_sniper": SessionSniperStrategy,
    "trend_rider": TrendRiderStrategy,
}

__all__ = [
    "BaseStrategy",
    "CompositeScoreStrategy",
    "CryptoExtremeStrategy",
    "DivergenceHunterStrategy",
    "GoldMacroStrategy",
    "SessionSniperStrategy",
    "TrendRiderStrategy",
    "STRATEGY_REGISTRY",
]
