"""BaseStrategy abstract interface for BacktestEngine (TASK-V7-20).

All strategy implementations must subclass BaseStrategy and implement:
  - check_entry(context) -> Optional[dict]
  - name() -> str

Pluggable strategies allow the backtest engine to swap signal-generation logic
without changing the simulation loop (SL/TP, exit logic, filters, stats).
"""

from abc import ABC, abstractmethod
from typing import Optional


class BaseStrategy(ABC):
    """Abstract base class for backtest entry strategies.

    A strategy is responsible only for deciding whether to enter a trade
    and at what price/direction.  All post-entry logic (SL/TP, filters,
    breakeven, time-exit) remains in BacktestEngine.

    The context dict is constructed by BacktestEngine._simulate_symbol()
    and contains everything available at the current candle without lookahead.
    """

    @abstractmethod
    def check_entry(self, context: dict) -> Optional[dict]:
        """Evaluate a potential trade entry at the current candle.

        Args:
            context: Dict with the following keys (all may be None/empty):
                df             — pd.DataFrame of OHLCV up to current candle (no lookahead)
                ta_indicators  — Dict of scalar TA indicator values at the current bar
                regime         — Market regime string (e.g. "STRONG_TREND_BULL")
                symbol         — Instrument symbol (e.g. "EURUSD=X")
                market_type    — "forex" | "crypto" | "stocks"
                timeframe      — Timeframe string (e.g. "H1")
                candle_ts      — datetime of the current candle
                macro_data     — List of macro data rows (may be empty)
                fear_greed     — Fear & Greed value (float or None)
                geo_score      — Geopolitical score (float or None)
                central_bank_rates — Dict[bank_code, rate] (may be empty)
                ta_score       — Pre-computed TA score (float or None)
                atr_value      — Pre-computed ATR value (float or None)
                composite_score — Pre-computed composite score (float or None)

        Returns:
            Entry signal dict with keys:
                direction        — "LONG" | "SHORT"
                entry_price      — Decimal (next candle open, set by engine)
                composite_score  — Decimal
                regime           — str
                atr              — Decimal
                position_pct     — float (default 2.0)
                ta_indicators    — dict
                support_levels   — list[Decimal]
                resistance_levels — list[Decimal]
            or None if no entry should be taken.
        """
        ...

    @abstractmethod
    def name(self) -> str:
        """Return a unique human-readable name for this strategy."""
        ...
