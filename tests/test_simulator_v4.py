"""Trade Simulator v4 tests — SIM-17..SIM-24.

Test naming: test_{sim_number}_{what_we_check}
All DB interactions are mocked — no real database required.
"""

import datetime
from decimal import Decimal
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
import pytest_asyncio


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_db_session() -> AsyncMock:
    """Async mock of SQLAlchemy AsyncSession."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.flush = AsyncMock()
    return session


@pytest.fixture
def mock_position_long() -> MagicMock:
    """Mock VirtualPortfolio for an open LONG position."""
    pos = MagicMock()
    pos.id = 1
    pos.signal_id = 101
    pos.status = "open"

    # Price levels
    pos.entry_price = Decimal("1.10000")
    pos.current_stop_loss = Decimal("1.09000")   # 100 pips SL
    pos.current_price = Decimal("1.10000")

    # Position sizing
    pos.size_pct = Decimal("2.0")
    pos.size_remaining_pct = Decimal("1.0")
    pos.partial_closed = False
    pos.breakeven_moved = False
    pos.partial_close_price = None
    pos.partial_close_at = None

    # MFE / MAE (in price units)
    pos.mfe = Decimal("0")
    pos.mae = Decimal("0")

    # Swap / account
    pos.accrued_swap_pips = Decimal("0")
    pos.accrued_swap_usd = Decimal("0")
    pos.last_swap_date = None
    pos.account_balance_at_entry = Decimal("1000.0")

    # Instrument info (via relationship mock)
    instrument = MagicMock()
    instrument.symbol = "EURUSD=X"
    instrument.market = "forex"
    instrument.pip_size = Decimal("0.0001")
    pos.signal = MagicMock()
    pos.signal.instrument = instrument
    pos.signal.direction = "LONG"
    pos.signal.timeframe = "H1"
    pos.signal.take_profit_1 = Decimal("1.11330")
    pos.signal.take_profit_2 = Decimal("1.12000")
    pos.signal.take_profit_3 = Decimal("1.12660")
    pos.signal.stop_loss = Decimal("1.09000")
    pos.signal.entry_price = Decimal("1.10000")
    pos.signal.composite_score = Decimal("12.0")
    pos.signal.regime = "TREND_BULL"

    return pos


@pytest.fixture
def mock_position_short() -> MagicMock:
    """Mock VirtualPortfolio for an open SHORT position."""
    pos = MagicMock()
    pos.id = 2
    pos.signal_id = 102
    pos.status = "open"

    # Price levels
    pos.entry_price = Decimal("1.10000")
    pos.current_stop_loss = Decimal("1.11000")   # 100 pips SL
    pos.current_price = Decimal("1.10000")

    # Position sizing
    pos.size_pct = Decimal("2.0")
    pos.size_remaining_pct = Decimal("1.0")
    pos.partial_closed = False
    pos.breakeven_moved = False
    pos.partial_close_price = None
    pos.partial_close_at = None

    # MFE / MAE
    pos.mfe = Decimal("0")
    pos.mae = Decimal("0")

    # Swap / account
    pos.accrued_swap_pips = Decimal("0")
    pos.accrued_swap_usd = Decimal("0")
    pos.last_swap_date = None
    pos.account_balance_at_entry = Decimal("1000.0")

    # Instrument info
    instrument = MagicMock()
    instrument.symbol = "EURUSD=X"
    instrument.market = "forex"
    instrument.pip_size = Decimal("0.0001")
    pos.signal = MagicMock()
    pos.signal.instrument = instrument
    pos.signal.direction = "SHORT"
    pos.signal.timeframe = "H1"
    pos.signal.take_profit_1 = Decimal("1.08670")
    pos.signal.take_profit_2 = Decimal("1.08000")
    pos.signal.take_profit_3 = Decimal("1.07340")
    pos.signal.stop_loss = Decimal("1.11000")
    pos.signal.entry_price = Decimal("1.10000")
    pos.signal.composite_score = Decimal("-12.0")
    pos.signal.regime = "TREND_BEAR"

    return pos


@pytest.fixture
def mock_candle_data() -> dict:
    """Single OHLCV candle as dict (price units for EURUSD=X)."""
    return {
        "timestamp": datetime.datetime(2024, 6, 1, 12, 0, tzinfo=datetime.timezone.utc),
        "open":  Decimal("1.10050"),
        "high":  Decimal("1.10200"),
        "low":   Decimal("1.09900"),
        "close": Decimal("1.10100"),
        "volume": Decimal("5000"),
    }


@pytest.fixture
def mock_ohlcv_df() -> pd.DataFrame:
    """Small synthetic OHLCV DataFrame (50 bars) for backtest tests."""
    import numpy as np

    np.random.seed(123)
    n = 50
    base = 1.1000
    close = base + np.cumsum(np.random.normal(0, 0.0005, n))
    high = close + np.abs(np.random.normal(0, 0.0002, n))
    low = close - np.abs(np.random.normal(0, 0.0002, n))
    open_ = np.roll(close, 1)
    open_[0] = close[0]

    idx = pd.date_range(
        start=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
        periods=n,
        freq="h",
    )
    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": np.random.uniform(1000, 5000, n),
    }, index=idx)
