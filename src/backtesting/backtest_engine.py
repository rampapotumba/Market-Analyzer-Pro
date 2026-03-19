"""BacktestEngine v4 — candle-by-candle historical simulation (SIM-22).

Stub implementation: run_backtest() creates a run record and returns the run_id.
Full implementation follows in Phase 2 (TASKS_V4.md §2.2).
"""

import logging
import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from src.backtesting.backtest_params import BacktestParams, BacktestResult

logger = logging.getLogger(__name__)


class BacktestEngine:
    """Candle-by-candle backtest engine.

    Phase 2 implementation plan (SIM-22):
      1. create_backtest_run (status=running)
      2. Load price_data for each symbol in [start_date, end_date]
      3. Iterate candles chronologically — NO lookahead (slice [0..i-1])
      4. SignalEngineV2.generate() on slice → signal?
      5. Entry fill on NEXT candle open price
      6. SL/TP check per SIM-09 logic (high/low of candle)
      7. Accumulate BacktestTradeResult list in memory
      8. Bulk insert → backtest_trades
      9. Compute summary (win_rate, PF, max_drawdown, equity_curve)
      10. Update backtest_run (status=completed, summary=...)
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def run_backtest(self, params: BacktestParams) -> str:
        """Create a backtest run record and return run_id (UUID string).

        Full simulation logic will be implemented in Phase 2.
        """
        run_id = str(uuid.uuid4())

        logger.info(
            "[SIM-22] BacktestEngine.run_backtest stub: run_id=%s symbols=%s tf=%s",
            run_id, params.symbols, params.timeframe,
        )

        # Phase 2: replace stub with full simulation
        return run_id
