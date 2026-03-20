"""
One-time cleanup: cancel virtual_portfolio positions that were erroneously
opened for signals with status 'created' or 'cancelled'.

Run once:
    python scripts/cleanup_bad_positions.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.engine import async_session_factory
from src.database.models import Signal, VirtualPortfolio
from sqlalchemy import select, update


async def main() -> None:
    async with async_session_factory() as db:
        # Find all open positions whose signal is NOT tracking
        result = await db.execute(
            select(VirtualPortfolio, Signal)
            .join(Signal, VirtualPortfolio.signal_id == Signal.id)
            .where(
                VirtualPortfolio.status == "open",
                Signal.status.in_(["created", "cancelled"]),
            )
        )
        rows = result.all()

        if not rows:
            print("No bad positions found.")
            return

        print(f"Found {len(rows)} positions to cancel:")
        for pos, sig in rows:
            print(f"  position_id={pos.id}  signal_id={sig.id}  symbol={sig.instrument_id}  sig_status={sig.status}")

        bad_position_ids = [pos.id for pos, _ in rows]

        await db.execute(
            update(VirtualPortfolio)
            .where(VirtualPortfolio.id.in_(bad_position_ids))
            .values(status="cancelled")
        )
        await db.commit()
        print(f"Cancelled {len(bad_position_ids)} positions.")


if __name__ == "__main__":
    asyncio.run(main())
