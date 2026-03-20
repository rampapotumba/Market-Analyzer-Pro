#!/usr/bin/env python3
"""Walk-forward validation script for all named strategies (TASK-V7-26).

Runs each of the 5 strategies through walk-forward validation using BacktestEngine
and prints a summary table.  Results are also saved as JSON.

Usage:
    python3 scripts/run_strategy_backtests.py --strategy all
    python3 scripts/run_strategy_backtests.py --strategy trend_rider
    python3 scripts/run_strategy_backtests.py --strategy all --dry-run
    python3 scripts/run_strategy_backtests.py --strategy all --output reports/strategies_wf.json

Exit codes:
    0 — all strategies passed validation (or --dry-run mode)
    1 — one or more strategies failed or errored
"""

import argparse
import asyncio
import datetime
import json
import logging
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("strategy_backtests")

# ── Validation thresholds ─────────────────────────────────────────────────────
_MIN_OOS_TRADES = 10          # minimum OOS trades to consider valid
_MIN_OOS_PROFIT_FACTOR = 1.2  # minimum OOS profit factor
_MIN_OOS_WIN_RATE = 0.40      # minimum OOS win rate (40%)
_MIN_OOS_SHARPE = 0.5         # minimum OOS Sharpe ratio


# ── Strategy configurations ───────────────────────────────────────────────────

STRATEGY_CONFIGS: dict[str, dict[str, Any]] = {
    "trend_rider": {
        "display_name": "Trend Rider",
        "timeframe": "D1",
        "start_date": "2020-01-01",
        "end_date": "2025-01-01",
        "in_sample_months": 18,
        "out_of_sample_months": 6,
        "symbols": [
            "EURUSD=X",
            "AUDUSD=X",
            "USDCAD=X",
            "GC=F",
            "BTC/USDT",
        ],
    },
    "session_sniper": {
        "display_name": "Session Sniper",
        "timeframe": "H1",
        "start_date": "2023-01-01",
        "end_date": "2025-01-01",
        "in_sample_months": 12,
        "out_of_sample_months": 6,
        "symbols": [
            "EURUSD=X",
            "GBPUSD=X",
            "USDJPY=X",
            "AUDUSD=X",
            "USDCAD=X",
        ],
    },
    "crypto_extreme": {
        "display_name": "Crypto Extreme",
        "timeframe": "D1",
        "start_date": "2020-01-01",
        "end_date": "2025-01-01",
        "in_sample_months": 18,
        "out_of_sample_months": 6,
        "symbols": [
            "BTC/USDT",
            "ETH/USDT",
        ],
    },
    "gold_macro": {
        "display_name": "Gold Macro",
        "timeframe": "D1",
        "start_date": "2020-01-01",
        "end_date": "2025-01-01",
        "in_sample_months": 18,
        "out_of_sample_months": 6,
        "symbols": [
            "GC=F",
            "SI=F",
        ],
    },
    "divergence_hunter": {
        "display_name": "Divergence Hunter",
        "timeframe": "H4",
        "start_date": "2022-01-01",
        "end_date": "2025-01-01",
        "in_sample_months": 12,
        "out_of_sample_months": 6,
        "symbols": [
            "EURUSD=X",
            "GBPUSD=X",
            "USDJPY=X",
            "AUDUSD=X",
        ],
    },
}

ALL_STRATEGIES = list(STRATEGY_CONFIGS.keys())


def build_backtest_params(strategy_name: str) -> "BacktestParams":  # noqa: F821
    """Construct BacktestParams for the given strategy using STRATEGY_CONFIGS."""
    from src.backtesting.backtest_params import BacktestParams

    cfg = STRATEGY_CONFIGS[strategy_name]
    return BacktestParams(
        symbols=cfg["symbols"],
        timeframe=cfg["timeframe"],
        start_date=cfg["start_date"],
        end_date=cfg["end_date"],
        strategy=strategy_name,
        enable_walk_forward=True,
        in_sample_months=cfg["in_sample_months"],
        out_of_sample_months=cfg["out_of_sample_months"],
        use_fundamental_data=True,
    )


def _verdict(oos: dict[str, Any], total_oos_trades: int) -> str:
    """Return VALID or INVALID based on OOS metrics."""
    if total_oos_trades < _MIN_OOS_TRADES:
        return "INVALID"
    pf = float(oos.get("profit_factor") or 0.0)
    wr = float(oos.get("win_rate") or 0.0)
    sharpe = float(oos.get("sharpe_ratio") or 0.0)
    if pf >= _MIN_OOS_PROFIT_FACTOR and wr >= _MIN_OOS_WIN_RATE and sharpe >= _MIN_OOS_SHARPE:
        return "VALID"
    return "INVALID"


async def run_strategy(
    strategy_name: str,
    dry_run: bool,
    session_factory: Any,
) -> dict[str, Any]:
    """Run walk-forward validation for a single strategy.

    Returns a result dict with keys:
        strategy, display_name, status, oos_trades, oos_pf, oos_wr, oos_sharpe, verdict, error
    """
    cfg = STRATEGY_CONFIGS[strategy_name]
    display = cfg["display_name"]

    if dry_run:
        logger.info("[DRY-RUN] Would run %s — skipping actual backtest", display)
        return {
            "strategy": strategy_name,
            "display_name": display,
            "status": "DRY_RUN",
            "oos_trades": 0,
            "oos_pf": None,
            "oos_wr": None,
            "oos_sharpe": None,
            "verdict": "N/A",
            "error": None,
        }

    from src.backtesting.backtest_engine import BacktestEngine

    params = build_backtest_params(strategy_name)
    engine = BacktestEngine()

    logger.info("Running %s (%s, %s–%s, %d symbols)…",
                display, params.timeframe, params.start_date, params.end_date,
                len(params.symbols))

    try:
        async with session_factory() as session:
            result = await engine.run(session, params)
    except Exception as exc:
        logger.error("FAILED %s: %s", display, exc)
        return {
            "strategy": strategy_name,
            "display_name": display,
            "status": "ERROR",
            "oos_trades": 0,
            "oos_pf": None,
            "oos_wr": None,
            "oos_sharpe": None,
            "verdict": "INVALID",
            "error": str(exc),
        }

    wf = result.walk_forward or {}
    oos = wf.get("oos_metrics") or {}
    total_oos_trades = int(oos.get("total_trades") or 0)

    pf_raw = oos.get("profit_factor")
    wr_raw = oos.get("win_rate")
    sharpe_raw = oos.get("sharpe_ratio")

    pf_val = round(float(pf_raw), 3) if pf_raw is not None else None
    wr_val = round(float(wr_raw) * 100, 1) if wr_raw is not None else None
    sharpe_val = round(float(sharpe_raw), 3) if sharpe_raw is not None else None

    verdict = _verdict(oos, total_oos_trades)

    logger.info(
        "%s %s — OOS trades=%d, PF=%s, WR=%s%%, Sharpe=%s → %s",
        "+" if verdict == "VALID" else "-",
        display,
        total_oos_trades,
        pf_val,
        wr_val,
        sharpe_val,
        verdict,
    )

    return {
        "strategy": strategy_name,
        "display_name": display,
        "status": result.status,
        "oos_trades": total_oos_trades,
        "oos_pf": pf_val,
        "oos_wr": wr_val,
        "oos_sharpe": sharpe_val,
        "verdict": verdict,
        "error": result.error,
        "walk_forward": wf,
    }


def _print_summary(results: list[dict[str, Any]]) -> None:
    """Print formatted summary table to stdout."""
    col_w = 20
    print("\n" + "=" * 76)
    print(
        f"{'Strategy':<{col_w}} {'Trades':>7} {'PF':>7} {'WR%':>7} {'Sharpe':>8} {'Verdict':>8}"
    )
    print("-" * 76)
    for r in results:
        pf_s = f"{r['oos_pf']:.3f}" if r["oos_pf"] is not None else "-"
        wr_s = f"{r['oos_wr']:.1f}" if r["oos_wr"] is not None else "-"
        sh_s = f"{r['oos_sharpe']:.3f}" if r["oos_sharpe"] is not None else "-"
        print(
            f"{r['display_name']:<{col_w}} "
            f"{r['oos_trades']:>7} "
            f"{pf_s:>7} "
            f"{wr_s:>7} "
            f"{sh_s:>8} "
            f"{r['verdict']:>8}"
        )
    print("=" * 76)
    valid = sum(1 for r in results if r["verdict"] == "VALID")
    invalid = sum(1 for r in results if r["verdict"] == "INVALID")
    errors = sum(1 for r in results if r["status"] == "ERROR")
    dry = sum(1 for r in results if r["status"] == "DRY_RUN")
    parts = [f"Total: {len(results)}"]
    if dry:
        parts.append(f"DRY_RUN: {dry}")
    else:
        parts += [f"VALID: {valid}", f"INVALID: {invalid}"]
        if errors:
            parts.append(f"ERROR: {errors}")
    print(" | ".join(parts) + "\n")


async def main_async(strategies: list[str], dry_run: bool, output: Path) -> int:
    """Async entry point — runs strategies sequentially to avoid DB contention."""
    try:
        from src.database.engine import async_session_factory
    except Exception as exc:
        if dry_run:
            logger.warning("DB unavailable (dry-run mode, continuing): %s", exc)
            async_session_factory = None  # type: ignore[assignment]
        else:
            logger.error("Cannot connect to database: %s", exc)
            logger.error("Ensure DATABASE_URL is set and the database is running.")
            return 1

    results: list[dict[str, Any]] = []
    failed = 0

    for strategy_name in strategies:
        result = await run_strategy(strategy_name, dry_run, async_session_factory)
        results.append(result)
        if result["verdict"] == "INVALID" and result["status"] != "DRY_RUN":
            failed += 1

    _print_summary(results)

    # ── Write JSON report ─────────────────────────────────────────────────────
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "dry_run": dry_run,
        "strategies_run": strategies,
        "results": results,
    }
    output.write_text(json.dumps(report, indent=2, default=str))
    logger.info("Report written to %s", output)

    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Walk-forward validation for named strategies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Strategies: " + ", ".join(ALL_STRATEGIES) + "\n"
            "Use 'all' to run every strategy."
        ),
    )
    parser.add_argument(
        "--strategy",
        default="all",
        help="Strategy name or 'all' (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would run without executing backtests",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/strategy_backtests.json"),
        help="Path for JSON output report (default: reports/strategy_backtests.json)",
    )
    args = parser.parse_args()

    if args.strategy == "all":
        strategies_to_run = ALL_STRATEGIES
    elif args.strategy in STRATEGY_CONFIGS:
        strategies_to_run = [args.strategy]
    else:
        valid = ", ".join(ALL_STRATEGIES) + ", all"
        parser.error(f"Unknown strategy '{args.strategy}'. Valid options: {valid}")
        return  # unreachable, keeps mypy happy

    exit_code = asyncio.run(main_async(strategies_to_run, args.dry_run, args.output))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
