#!/usr/bin/env python3
"""Run final walk-forward validation across all active instruments.

Usage:
    python scripts/run_walkforward_all.py [--output reports/walkforward_final.json]

This script:
  1. Fetches all active instruments from the DB
  2. Loads their historical signal results
  3. Runs the full walk-forward engine (IS=18m, OOS=6m, 5 folds)
  4. Saves a JSON summary to --output (default: reports/walkforward_final.json)
  5. Prints a human-readable table

Exit codes:
  0 — all instruments passed validation
  1 — one or more instruments failed validation
"""
import argparse
import asyncio
import datetime
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("walkforward")


async def run(output_path: Path) -> int:
    """Run walk-forward for all instruments; return exit code."""
    from src.analysis.backtest_engine import BacktestEngine
    from src.database.engine import async_session_factory
    from src.database.models import Instrument
    from sqlalchemy import select

    engine = BacktestEngine()
    results: list[dict] = []
    failed = 0

    async with async_session_factory() as session:
        res = await session.execute(
            select(Instrument).where(Instrument.is_active.is_(True))
        )
        instruments = res.scalars().all()

    logger.info("Running walk-forward for %d instruments…", len(instruments))

    for instr in instruments:
        logger.info("  → %s (%s)", instr.symbol, instr.market_type)
        async with async_session_factory() as session:
            trades = await engine._load_historical_trades(session, instrument_id=instr.id)

        if len(trades) < 20:
            logger.warning("    Skipped %s — only %d trades", instr.symbol, len(trades))
            results.append(
                {
                    "instrument": instr.symbol,
                    "status": "SKIPPED",
                    "reason": f"only {len(trades)} trades",
                    "metrics": {},
                }
            )
            continue

        try:
            result = await engine.run_walk_forward(trades)
        except Exception as exc:
            logger.error("    FAILED %s: %s", instr.symbol, exc)
            results.append(
                {
                    "instrument": instr.symbol,
                    "status": "ERROR",
                    "reason": str(exc),
                    "metrics": {},
                }
            )
            failed += 1
            continue

        oos = result.get("oos_metrics", {})
        passed = result.get("passed_validation", False)
        status = "PASS" if passed else "FAIL"
        if not passed:
            failed += 1

        results.append(
            {
                "instrument": instr.symbol,
                "status": status,
                "metrics": {
                    "win_rate": round(oos.get("win_rate", 0.0) * 100, 1),
                    "sharpe": round(oos.get("sharpe_ratio", 0.0), 3),
                    "max_drawdown_pct": round(oos.get("max_drawdown", 0.0) * 100, 1),
                    "net_pnl_pct": round(oos.get("net_pnl_pct", 0.0), 2),
                    "total_oos_trades": oos.get("total_trades", 0),
                },
                "optimal_weights": result.get("optimal_weights"),
            }
        )

        marker = "✓" if passed else "✗"
        logger.info(
            "    %s %s — WR=%.1f%%, Sharpe=%.2f, DD=-%.1f%%, PnL=+%.1f%%",
            marker,
            instr.symbol,
            oos.get("win_rate", 0.0) * 100,
            oos.get("sharpe_ratio", 0.0),
            oos.get("max_drawdown", 0.0) * 100,
            oos.get("net_pnl_pct", 0.0),
        )

    # ── Write JSON report ────────────────────────────────────────────────────────
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "total_instruments": len(instruments),
        "passed": sum(1 for r in results if r["status"] == "PASS"),
        "failed": sum(1 for r in results if r["status"] == "FAIL"),
        "skipped": sum(1 for r in results if r["status"] == "SKIPPED"),
        "results": results,
    }
    output_path.write_text(json.dumps(report, indent=2))
    logger.info("Report written to %s", output_path)

    # ── Print summary table ──────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print(f"{'Instrument':<12} {'Status':<8} {'WR%':>6} {'Sharpe':>8} {'DD%':>7} {'PnL%':>7} {'Trades':>7}")
    print("-" * 72)
    for r in results:
        m = r.get("metrics", {})
        print(
            f"{r['instrument']:<12} {r['status']:<8} "
            f"{m.get('win_rate', '-'):>6} "
            f"{m.get('sharpe', '-'):>8} "
            f"{('-' + str(m.get('max_drawdown_pct', '-'))):>7} "
            f"{m.get('net_pnl_pct', '-'):>7} "
            f"{m.get('total_oos_trades', '-'):>7}"
        )
    print("=" * 72)
    print(
        f"\nTotal: {report['total_instruments']} instruments | "
        f"PASS: {report['passed']} | FAIL: {report['failed']} | "
        f"SKIP: {report['skipped']}\n"
    )

    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Final walk-forward validation")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/walkforward_final.json"),
        help="Path for the JSON output report",
    )
    args = parser.parse_args()
    exit_code = asyncio.run(run(args.output))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
