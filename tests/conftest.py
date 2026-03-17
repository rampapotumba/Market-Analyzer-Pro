"""Shared test fixtures."""

import datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.database.engine import init_db
from src.database.models import Base


@pytest.fixture
def sample_ohlcv_df() -> pd.DataFrame:
    """Create a synthetic OHLCV DataFrame with 200 bars."""
    np.random.seed(42)
    n = 200
    base_price = 1.1000
    returns = np.random.normal(0, 0.001, n)
    close_prices = base_price * np.cumprod(1 + returns)

    high = close_prices * (1 + np.abs(np.random.normal(0, 0.0005, n)))
    low = close_prices * (1 - np.abs(np.random.normal(0, 0.0005, n)))
    open_prices = close_prices * (1 + np.random.normal(0, 0.0003, n))
    volume = np.random.uniform(1000, 10000, n)

    idx = pd.date_range(
        start=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
        periods=n,
        freq="h",
    )

    return pd.DataFrame({
        "open": open_prices,
        "high": high,
        "low": low,
        "close": close_prices,
        "volume": volume,
    }, index=idx)


@pytest.fixture
def trending_bullish_df() -> pd.DataFrame:
    """Create a strongly bullish trending DataFrame with 250 bars."""
    n = 250
    base = 1.0
    # Strong uptrend with minimal noise
    close_prices = np.array([base + i * 0.002 for i in range(n)])
    # Volume increasing in uptrend
    volume = np.array([3000.0 + i * 20 for i in range(n)])
    high = close_prices + 0.0003
    low = close_prices - 0.0001
    open_prices = np.roll(close_prices, 1)
    open_prices[0] = close_prices[0] - 0.0001

    idx = pd.date_range(
        start=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
        periods=n,
        freq="h",
    )

    return pd.DataFrame({
        "open": open_prices,
        "high": high,
        "low": low,
        "close": close_prices,
        "volume": volume,
    }, index=idx)


@pytest.fixture
def trending_bearish_df() -> pd.DataFrame:
    """Create a strongly bearish trending DataFrame with 250 bars."""
    n = 250
    base = 1.5
    # Strong downtrend with minimal noise
    close_prices = np.array([base - i * 0.002 for i in range(n)])
    close_prices = np.clip(close_prices, 0.01, None)
    volume = np.array([3000.0 + i * 20 for i in range(n)])
    high = close_prices + 0.0001
    low = close_prices - 0.0003
    open_prices = np.roll(close_prices, 1)
    open_prices[0] = close_prices[0] + 0.0001

    idx = pd.date_range(
        start=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
        periods=n,
        freq="h",
    )

    return pd.DataFrame({
        "open": open_prices,
        "high": high,
        "low": low,
        "close": close_prices,
        "volume": volume,
    }, index=idx)


TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def test_db() -> AsyncSession:
    """Create a test database session with all tables."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
