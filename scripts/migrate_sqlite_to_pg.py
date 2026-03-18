"""Migrate data from SQLite to PostgreSQL (price_data, signals, economic_events, news_events, macro_data)."""
import asyncio
import sqlite3
import os
import sys
from datetime import datetime, timezone


def parse_dt(s):
    """Parse SQLite datetime string to timezone-aware datetime."""
    if s is None:
        return None
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg
from dotenv import load_dotenv

load_dotenv()

SQLITE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "market_analyzer.db")
PG_DSN = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")


async def migrate():
    print(f"SQLite: {SQLITE_PATH}")
    print(f"PostgreSQL: {PG_DSN[:40]}...")

    sqlite = sqlite3.connect(SQLITE_PATH)
    sqlite.row_factory = sqlite3.Row
    cur = sqlite.cursor()

    pg = await asyncpg.connect(PG_DSN)

    # ── price_data ────────────────────────────────────────────────────────────
    print("\n[1/5] Migrating price_data...")
    existing = await pg.fetchval("SELECT COUNT(*) FROM price_data")
    print(f"  PostgreSQL already has {existing} rows")

    rows = cur.execute("SELECT * FROM price_data ORDER BY id").fetchall()
    print(f"  SQLite has {len(rows)} rows")

    if rows and existing == 0:
        records = [
            (r["instrument_id"], r["timeframe"], r["timestamp"],
             float(r["open"]), float(r["high"]), float(r["low"]),
             float(r["close"]), float(r["volume"] or 0))
            for r in rows
        ]
        await pg.executemany(
            """INSERT INTO price_data (instrument_id, timeframe, timestamp, open, high, low, close, volume)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
               ON CONFLICT (instrument_id, timeframe, timestamp) DO NOTHING""",
            records,
        )
        print(f"  ✓ Inserted {len(records)} price rows")
    else:
        print("  Skipped (already has data)")

    # ── signals ───────────────────────────────────────────────────────────────
    print("\n[2/5] Migrating signals...")
    existing = await pg.fetchval("SELECT COUNT(*) FROM signals")
    print(f"  PostgreSQL already has {existing} rows")

    rows = cur.execute("SELECT * FROM signals ORDER BY id").fetchall()
    print(f"  SQLite has {len(rows)} rows")

    if rows and existing == 0:
        records = [
            (
                r["id"], r["instrument_id"], r["timeframe"], parse_dt(r["created_at"]),
                r["direction"], r["signal_strength"],
                float(r["entry_price"]) if r["entry_price"] else None,
                float(r["stop_loss"]) if r["stop_loss"] else None,
                float(r["take_profit_1"]) if r["take_profit_1"] else None,
                float(r["take_profit_2"]) if r["take_profit_2"] else None,
                float(r["risk_reward"]) if r["risk_reward"] else None,
                float(r["position_size_pct"]) if r["position_size_pct"] else None,
                float(r["composite_score"]), float(r["ta_score"]),
                float(r["fa_score"]), float(r["sentiment_score"]),
                float(r["geo_score"]), float(r["confidence"]),
                r["horizon"], r["reasoning"], r["indicators_snapshot"],
                r["status"], parse_dt(r["expires_at"]),
            )
            for r in rows
        ]
        await pg.executemany(
            """INSERT INTO signals
               (id, instrument_id, timeframe, created_at, direction, signal_strength,
                entry_price, stop_loss, take_profit_1, take_profit_2, risk_reward,
                position_size_pct, composite_score, ta_score, fa_score, sentiment_score,
                geo_score, confidence, horizon, reasoning, indicators_snapshot, status, expires_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23)
               ON CONFLICT (id) DO NOTHING""",
            records,
        )
        # Reset sequence so new inserts don't conflict
        await pg.execute("SELECT setval('signals_id_seq', (SELECT MAX(id) FROM signals))")
        print(f"  ✓ Inserted {len(records)} signals")
    else:
        print("  Skipped (already has data)")

    # ── economic_events ───────────────────────────────────────────────────────
    print("\n[3/5] Migrating economic_events...")
    existing = await pg.fetchval("SELECT COUNT(*) FROM economic_events")
    rows = cur.execute("SELECT * FROM economic_events ORDER BY id").fetchall()
    print(f"  SQLite: {len(rows)}, PostgreSQL: {existing}")

    if rows and existing == 0:
        # Check columns
        cols = [desc[0] for desc in cur.execute("PRAGMA table_info(economic_events)").fetchall()]
        print(f"  Columns: {cols}")
        records = []
        for r in rows:
            record = {c: r[c] for c in cols if c != "id"}
            records.append(tuple(record.values()))

        # Build insert dynamically
        col_names = [c for c in cols if c != "id"]
        placeholders = ",".join(f"${i+1}" for i in range(len(col_names)))
        col_str = ",".join(col_names)
        await pg.executemany(
            f"INSERT INTO economic_events ({col_str}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
            records,
        )
        print(f"  ✓ Inserted {len(records)} economic events")
    else:
        print("  Skipped")

    # ── macro_data ────────────────────────────────────────────────────────────
    print("\n[4/5] Migrating macro_data...")
    existing = await pg.fetchval("SELECT COUNT(*) FROM macro_data")
    rows = cur.execute("SELECT * FROM macro_data ORDER BY id").fetchall()
    print(f"  SQLite: {len(rows)}, PostgreSQL: {existing}")

    if rows and existing == 0:
        cols = [desc[0] for desc in cur.execute("PRAGMA table_info(macro_data)").fetchall()]
        col_names = [c for c in cols if c != "id"]
        placeholders = ",".join(f"${i+1}" for i in range(len(col_names)))
        col_str = ",".join(col_names)
        records = [tuple(r[c] for c in col_names) for r in rows]
        await pg.executemany(
            f"INSERT INTO macro_data ({col_str}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
            records,
        )
        print(f"  ✓ Inserted {len(records)} macro rows")
    else:
        print("  Skipped")

    # ── news_events ───────────────────────────────────────────────────────────
    print("\n[5/5] Migrating news_events...")
    existing = await pg.fetchval("SELECT COUNT(*) FROM news_events")
    rows = cur.execute("SELECT * FROM news_events ORDER BY id").fetchall()
    print(f"  SQLite: {len(rows)}, PostgreSQL: {existing}")

    if rows and existing == 0:
        cols = [desc[0] for desc in cur.execute("PRAGMA table_info(news_events)").fetchall()]
        col_names = [c for c in cols if c != "id"]
        placeholders = ",".join(f"${i+1}" for i in range(len(col_names)))
        col_str = ",".join(col_names)
        records = [tuple(r[c] for c in col_names) for r in rows]
        # Insert in batches to avoid memory issues
        batch_size = 500
        total = 0
        for i in range(0, len(records), batch_size):
            batch = records[i:i+batch_size]
            await pg.executemany(
                f"INSERT INTO news_events ({col_str}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                batch,
            )
            total += len(batch)
        print(f"  ✓ Inserted {total} news events")
    else:
        print("  Skipped")

    await pg.close()
    sqlite.close()

    print("\n✅ Migration complete!")


if __name__ == "__main__":
    asyncio.run(migrate())
