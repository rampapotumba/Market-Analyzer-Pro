"""Seed script to populate initial instruments."""

import asyncio
import logging
from decimal import Decimal

from src.database.crud import get_or_create_instrument
from src.database.engine import async_session_factory, init_db

logger = logging.getLogger(__name__)

INITIAL_INSTRUMENTS = [
    # Forex
    {"symbol": "EURUSD=X", "market": "forex", "name": "EUR/USD", "pip_size": Decimal("0.0001")},
    {"symbol": "GBPUSD=X", "market": "forex", "name": "GBP/USD", "pip_size": Decimal("0.0001")},
    {"symbol": "USDJPY=X", "market": "forex", "name": "USD/JPY", "pip_size": Decimal("0.01")},
    {"symbol": "AUDUSD=X", "market": "forex", "name": "AUD/USD", "pip_size": Decimal("0.0001")},
    {"symbol": "USDCHF=X", "market": "forex", "name": "USD/CHF", "pip_size": Decimal("0.0001")},
    # Stocks
    {"symbol": "AAPL", "market": "stocks", "name": "Apple Inc.", "pip_size": Decimal("0.01")},
    {"symbol": "MSFT", "market": "stocks", "name": "Microsoft Corp.", "pip_size": Decimal("0.01")},
    {"symbol": "GOOGL", "market": "stocks", "name": "Alphabet Inc.", "pip_size": Decimal("0.01")},
    {"symbol": "SPY", "market": "stocks", "name": "S&P 500 ETF", "pip_size": Decimal("0.01")},
    {"symbol": "QQQ", "market": "stocks", "name": "Nasdaq 100 ETF", "pip_size": Decimal("0.01")},
    # Crypto
    {"symbol": "BTC/USDT", "market": "crypto", "name": "Bitcoin/USDT", "pip_size": Decimal("1.0")},
    {"symbol": "ETH/USDT", "market": "crypto", "name": "Ethereum/USDT", "pip_size": Decimal("0.01")},
    {"symbol": "SOL/USDT", "market": "crypto", "name": "Solana/USDT", "pip_size": Decimal("0.01")},
]


async def seed_instruments() -> None:
    """Seed instruments table with initial data."""
    await init_db()

    async with async_session_factory() as session:
        async with session.begin():
            created_count = 0
            for instr in INITIAL_INSTRUMENTS:
                _, created = await get_or_create_instrument(
                    session,
                    symbol=instr["symbol"],
                    market=instr["market"],
                    name=instr["name"],
                    pip_size=instr["pip_size"],
                )
                if created:
                    created_count += 1
                    logger.info(f"Created instrument: {instr['symbol']}")

    print(f"Seeded {created_count} new instruments ({len(INITIAL_INSTRUMENTS)} total defined)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(seed_instruments())
