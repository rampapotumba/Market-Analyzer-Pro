"""FIX-03: Backfill virtual_portfolio.size_pct from signals.position_size_pct.

All positions created by the old code have size_pct=1.0 regardless of the signal's
calculated position size. This script fixes those records.

Run once:
    PYTHONPATH=. python scripts/backfill_position_size.py [--dry-run]
"""

import asyncio
import sys
import logging
from decimal import Decimal

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def run(dry_run: bool = False) -> None:
    from sqlalchemy import text
    from src.database.engine import async_session_factory

    async with async_session_factory() as db:
        # Find positions where size_pct doesn't match signal's position_size_pct
        result = await db.execute(text("""
            SELECT vp.id, vp.signal_id, vp.size_pct, s.position_size_pct
            FROM virtual_portfolio vp
            JOIN signals s ON s.id = vp.signal_id
            WHERE s.position_size_pct IS NOT NULL
              AND s.position_size_pct > 0
              AND vp.size_pct != s.position_size_pct
        """))
        rows = result.fetchall()

        if not rows:
            logger.info("Nothing to backfill — all positions already have correct size_pct.")
            return

        logger.info(f"Found {len(rows)} positions to update:")
        for row in rows[:10]:
            logger.info(
                f"  vp.id={row[0]}, signal_id={row[1]}: "
                f"size_pct {row[2]} → {row[3]}"
            )
        if len(rows) > 10:
            logger.info(f"  ... and {len(rows) - 10} more")

        if dry_run:
            logger.info("DRY RUN — no changes made.")
            return

        await db.execute(text("""
            UPDATE virtual_portfolio vp
            SET size_pct = s.position_size_pct
            FROM signals s
            WHERE vp.signal_id = s.id
              AND s.position_size_pct IS NOT NULL
              AND s.position_size_pct > 0
              AND vp.size_pct != s.position_size_pct
        """))
        await db.commit()
        logger.info(f"Updated {len(rows)} positions.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    asyncio.run(run(dry_run=dry_run))
