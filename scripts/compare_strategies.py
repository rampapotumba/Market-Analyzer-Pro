#!/usr/bin/env python3
"""Compare named strategies and build a hybrid portfolio allocation (TASK-V7-27).

For each instrument, identifies the best strategy based on OOS Profit Factor
and Sharpe ratio, then produces a recommended hybrid portfolio allocation.

Usage:
    python3 scripts/compare_strategies.py
    python3 scripts/compare_strategies.py --input reports/strategy_backtests.json
    python3 scripts/compare_strategies.py --output-json reports/hybrid_allocation.json

Exit codes:
    0 — comparison completed successfully
    1 — input file not found or parse error
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("compare_strategies")

# ── Instrument coverage per strategy ─────────────────────────────────────────
# Maps strategy_name → set of instruments covered by that strategy.
# Mirrors the symbol lists from run_strategy_backtests.py / STRATEGY_CONFIGS.

STRATEGY_INSTRUMENTS: dict[str, list[str]] = {
    "trend_rider": [
        "EURUSD=X",
        "AUDUSD=X",
        "USDCAD=X",
        "GC=F",
        "BTC/USDT",
    ],
    "session_sniper": [
        "EURUSD=X",
        "GBPUSD=X",
        "AUDUSD=X",
        "USDCAD=X",
    ],
    "crypto_extreme": [
        "BTC/USDT",
    ],
    "gold_macro": [
        "GC=F",
    ],
    "divergence_hunter": [
        "EURUSD=X",
        "AUDUSD=X",
        "USDCAD=X",
        "GC=F",
        "BTC/USDT",
    ],
}

# Display names for pretty-printing.
STRATEGY_DISPLAY_NAMES: dict[str, str] = {
    "trend_rider": "Trend Rider",
    "session_sniper": "Session Sniper",
    "crypto_extreme": "Crypto Extreme",
    "gold_macro": "Gold Macro",
    "divergence_hunter": "Divergence Hunter",
}

# Minimum OOS trades required for a strategy result to be eligible for
# instrument allocation.  Below this threshold the result is treated as
# statistically insufficient.
MIN_TRADES_THRESHOLD = 20

# Fallback strategy name used when no strategy qualifies for an instrument.
FALLBACK_STRATEGY = "composite"


# ── Comparison matrix ─────────────────────────────────────────────────────────

def build_comparison_matrix(
    strategy_results: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Build instrument × strategy → metrics matrix.

    Args:
        strategy_results: List of per-strategy result dicts (as produced by
            run_strategy_backtests.py).  Each dict must have at minimum:
            - "strategy": strategy key (str)
            - "oos_trades": int
            - "oos_pf": float | None
            - "oos_sharpe": float | None

    Returns:
        Nested dict: matrix[instrument][strategy] = {"pf", "sharpe", "trade_count"}
    """
    matrix: dict[str, dict[str, dict[str, Any]]] = {}

    # Collect every instrument across all strategies.
    all_instruments: set[str] = set()
    for instruments in STRATEGY_INSTRUMENTS.values():
        all_instruments.update(instruments)

    for instrument in sorted(all_instruments):
        matrix[instrument] = {}

    # Populate with results.
    for result in strategy_results:
        strategy = result.get("strategy", "")
        if strategy not in STRATEGY_INSTRUMENTS:
            logger.warning("Unknown strategy in results: %s — skipping", strategy)
            continue

        covered = STRATEGY_INSTRUMENTS[strategy]
        oos_trades = int(result.get("oos_trades") or 0)
        oos_pf = result.get("oos_pf")
        oos_sharpe = result.get("oos_sharpe")

        for instrument in covered:
            if instrument not in matrix:
                matrix[instrument] = {}
            matrix[instrument][strategy] = {
                "pf": float(oos_pf) if oos_pf is not None else None,
                "sharpe": float(oos_sharpe) if oos_sharpe is not None else None,
                "trade_count": oos_trades,
            }

    return matrix


# ── Best strategy selection ───────────────────────────────────────────────────

def _select_best_strategy(
    instrument: str,
    strategy_metrics: dict[str, dict[str, Any]],
) -> Optional[str]:
    """Select the best strategy for an instrument by OOS PF (minimum 20 trades).

    Returns strategy name or None if no eligible strategy exists.

    Selection rules (in priority order):
    1. Strategy must have trade_count >= MIN_TRADES_THRESHOLD.
    2. Among eligible strategies, pick the one with highest OOS PF.
    3. If multiple strategies share the highest PF, break ties by Sharpe ratio.
    4. If PF is None (all wins, undefined), treat as positive infinity — eligible.
    """
    eligible: list[tuple[str, dict[str, Any]]] = [
        (strat, metrics)
        for strat, metrics in strategy_metrics.items()
        if metrics.get("trade_count", 0) >= MIN_TRADES_THRESHOLD
    ]

    if not eligible:
        logger.warning(
            "No strategy meets min trade threshold (%d) for %s — using fallback",
            MIN_TRADES_THRESHOLD,
            instrument,
        )
        return None

    def _sort_key(item: tuple[str, dict[str, Any]]) -> tuple[float, float]:
        _, metrics = item
        # None PF (all-wins) → treat as very high value for sorting.
        pf = metrics.get("pf")
        pf_val = float("inf") if pf is None else float(pf)
        sharpe = metrics.get("sharpe")
        sharpe_val = float("-inf") if sharpe is None else float(sharpe)
        return (pf_val, sharpe_val)

    best_strategy, _ = max(eligible, key=_sort_key)
    return best_strategy


def build_hybrid_allocation(results: dict[str, Any]) -> dict[str, str]:
    """Build hybrid portfolio allocation mapping instrument → best strategy.

    Args:
        results: Dict with key "strategy_results" (list) or a list of result
            dicts directly.  Accepts both shapes for flexibility.

    Returns:
        Dict mapping instrument name to strategy name.  Instruments with no
        qualifying strategy map to FALLBACK_STRATEGY ("composite").
    """
    # Accept either a raw list or a wrapper dict.
    if isinstance(results, list):
        strategy_results = results
    else:
        strategy_results = results.get("strategy_results") or results.get("results") or []

    matrix = build_comparison_matrix(strategy_results)

    allocation: dict[str, str] = {}
    for instrument, strategy_metrics in matrix.items():
        best = _select_best_strategy(instrument, strategy_metrics)
        allocation[instrument] = best if best is not None else FALLBACK_STRATEGY

    return allocation


# ── Loading helpers ───────────────────────────────────────────────────────────

def load_strategy_results(input_path: Path) -> list[dict[str, Any]]:
    """Load strategy backtest results from a JSON file.

    Returns the list of per-strategy result dicts.
    Raises SystemExit(1) on file not found or parse error.
    """
    if not input_path.exists():
        logger.error("Input file not found: %s", input_path)
        sys.exit(1)

    try:
        data = json.loads(input_path.read_text())
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse JSON from %s: %s", input_path, exc)
        sys.exit(1)

    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("results") or data.get("strategy_results") or []

    logger.error("Unexpected JSON structure in %s", input_path)
    sys.exit(1)


def _make_placeholder_results() -> list[dict[str, Any]]:
    """Return placeholder results when no input file is provided.

    This lets the script run standalone without real backtest data.
    """
    placeholder: list[dict[str, Any]] = []
    for strategy_name, display_name in STRATEGY_DISPLAY_NAMES.items():
        placeholder.append(
            {
                "strategy": strategy_name,
                "display_name": display_name,
                "status": "PLACEHOLDER",
                "oos_trades": 0,
                "oos_pf": None,
                "oos_wr": None,
                "oos_sharpe": None,
                "verdict": "N/A",
                "error": None,
            }
        )
    return placeholder


# ── Printing ──────────────────────────────────────────────────────────────────

def _print_comparison_matrix(matrix: dict[str, dict[str, dict[str, Any]]]) -> None:
    """Print the instrument × strategy comparison matrix to stdout."""
    strategies = sorted(STRATEGY_INSTRUMENTS.keys())
    instruments = sorted(matrix.keys())

    # Header
    col_w = 18
    cell_w = 24
    print("\n" + "=" * (col_w + cell_w * len(strategies)))
    print(f"{'Instrument':<{col_w}}", end="")
    for s in strategies:
        label = STRATEGY_DISPLAY_NAMES.get(s, s)
        print(f"{label:^{cell_w}}", end="")
    print()
    print(f"{'':>{col_w}}", end="")
    for _ in strategies:
        print(f"{'PF / Sharpe / N':^{cell_w}}", end="")
    print()
    print("-" * (col_w + cell_w * len(strategies)))

    for instrument in instruments:
        print(f"{instrument:<{col_w}}", end="")
        for s in strategies:
            metrics = matrix[instrument].get(s)
            if metrics is None:
                cell = "-"
            else:
                pf = metrics["pf"]
                sharpe = metrics["sharpe"]
                n = metrics["trade_count"]
                pf_s = f"{pf:.2f}" if pf is not None else "inf"
                sh_s = f"{sharpe:.2f}" if sharpe is not None else "?"
                cell = f"{pf_s} / {sh_s} / {n}"
            print(f"{cell:^{cell_w}}", end="")
        print()

    print("=" * (col_w + cell_w * len(strategies)))


def _print_allocation(allocation: dict[str, str]) -> None:
    """Print the recommended hybrid allocation to stdout."""
    print("\nRecommended Hybrid Portfolio Allocation:")
    print("-" * 44)
    for instrument, strategy in sorted(allocation.items()):
        display = STRATEGY_DISPLAY_NAMES.get(strategy, strategy)
        print(f"  {instrument:<18} → {display}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare strategies and build hybrid portfolio allocation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Without --input the script uses placeholder data and prints\n"
            "coverage/allocation structure without real metrics.\n\n"
            "Feed results from run_strategy_backtests.py via --input."
        ),
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Path to JSON file produced by run_strategy_backtests.py",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        dest="output_json",
        help="Write allocation result to this JSON file",
    )
    args = parser.parse_args()

    # Load strategy results.
    if args.input is not None:
        strategy_results = load_strategy_results(args.input)
        logger.info("Loaded %d strategy results from %s", len(strategy_results), args.input)
    else:
        logger.info("No --input provided — using placeholder data")
        strategy_results = _make_placeholder_results()

    # Build comparison matrix.
    matrix = build_comparison_matrix(strategy_results)

    # Print matrix.
    _print_comparison_matrix(matrix)

    # Build and print hybrid allocation.
    allocation = build_hybrid_allocation(strategy_results)
    _print_allocation(allocation)

    # Write JSON output if requested.
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        output = {
            "allocation": allocation,
            "matrix": {
                instrument: {
                    strat: metrics
                    for strat, metrics in strat_metrics.items()
                }
                for instrument, strat_metrics in matrix.items()
            },
        }
        args.output_json.write_text(json.dumps(output, indent=2, default=str))
        logger.info("Allocation written to %s", args.output_json)


if __name__ == "__main__":
    main()
