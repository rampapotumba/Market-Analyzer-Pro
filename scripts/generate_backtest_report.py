"""Generate markdown reports from backtest_runs table.

Usage:
    python scripts/generate_backtest_report.py --run-id <uuid> [--output docs/REPORT.md]
    python scripts/generate_backtest_report.py --run-id <uuid1> --run-id <uuid2> [--output docs/COMPARE.md]

When a single run_id is provided, generates a full report.
When multiple run_ids are provided, generates a comparison table in addition to individual summaries.

DATABASE_URL is read from .env (or environment).
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Allow running from project root without installing the package
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


# ── DB helpers ────────────────────────────────────────────────────────────────


async def _fetch_run(session: AsyncSession, run_id: str) -> Optional[dict[str, Any]]:
    """Fetch a single backtest_run row by run_id."""
    result = await session.execute(
        text(
            "SELECT id, status, params_json, summary_json, created_at "
            "FROM backtest_runs WHERE id = :run_id"
        ),
        {"run_id": run_id},
    )
    row = result.fetchone()
    if row is None:
        return None
    run_id_db, status, params_raw, summary_raw, created_at = row
    params = json.loads(params_raw) if isinstance(params_raw, str) else (params_raw or {})
    summary = json.loads(summary_raw) if isinstance(summary_raw, str) else (summary_raw or {})
    return {
        "run_id": str(run_id_db),
        "status": status,
        "params": params,
        "summary": summary,
        "created_at": created_at,
    }


async def _fetch_trades(session: AsyncSession, run_id: str) -> list[dict[str, Any]]:
    """Fetch all backtest_trades for a run_id."""
    result = await session.execute(
        text(
            "SELECT symbol, direction, entry_price, exit_price, pnl_usd, "
            "       exit_reason, entry_at, exit_at, duration_minutes, regime "
            "FROM backtest_trades WHERE run_id = :run_id "
            "ORDER BY entry_at"
        ),
        {"run_id": run_id},
    )
    rows = result.fetchall()
    return [
        {
            "symbol": r[0],
            "direction": r[1],
            "entry_price": r[2],
            "exit_price": r[3],
            "pnl_usd": float(r[4]) if r[4] is not None else None,
            "exit_reason": r[5],
            "entry_at": r[6],
            "exit_at": r[7],
            "duration_minutes": r[8],
            "regime": r[9],
        }
        for r in rows
    ]


# ── Report sections ───────────────────────────────────────────────────────────


def _fmt_pct(val: Any, decimals: int = 2) -> str:
    if val is None:
        return "N/A"
    return f"{float(val):.{decimals}f}%"


def _fmt_usd(val: Any) -> str:
    if val is None:
        return "N/A"
    return f"${float(val):,.2f}"


def _fmt_val(val: Any, decimals: int = 4) -> str:
    if val is None:
        return "N/A"
    try:
        return f"{float(val):.{decimals}f}"
    except (TypeError, ValueError):
        return str(val)


def _build_single_report(run: dict[str, Any], trades: list[dict[str, Any]]) -> str:
    """Build a full markdown report for a single backtest run."""
    lines: list[str] = []
    run_id = run["run_id"]
    params = run["params"]
    summary = run["summary"]
    created_at = run["created_at"]

    created_str = created_at.strftime("%Y-%m-%d %H:%M UTC") if created_at else "N/A"

    lines.append(f"# Backtest Report — {run_id[:8]}...")
    lines.append("")
    lines.append(f"**Run ID:** `{run_id}`")
    lines.append(f"**Created:** {created_str}")
    lines.append(f"**Status:** {run.get('status', 'N/A')}")
    lines.append(f"**Data Hash:** `{summary.get('data_hash', 'N/A')}`")
    lines.append("")

    # Parameters
    lines.append("## Parameters")
    lines.append("")
    lines.append("| Parameter | Value |")
    lines.append("|-----------|-------|")
    for key, val in sorted(params.items()):
        lines.append(f"| `{key}` | `{val}` |")
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Total Trades | {summary.get('total_trades', 0)} |")
    lines.append(f"| Win Rate | {_fmt_pct(summary.get('win_rate_pct'))} |")
    lines.append(f"| Profit Factor | {_fmt_val(summary.get('profit_factor'), 4)} |")
    lines.append(f"| Total PnL | {_fmt_usd(summary.get('total_pnl_usd'))} |")
    lines.append(f"| Max Drawdown | {_fmt_pct(summary.get('max_drawdown_pct'))} |")
    lines.append(f"| Avg Duration | {_fmt_val(summary.get('avg_duration_minutes'), 1)} min |")
    lines.append(f"| LONG count | {summary.get('long_count', 0)} |")
    lines.append(f"| SHORT count | {summary.get('short_count', 0)} |")
    lines.append(f"| Win Rate LONG | {_fmt_pct(summary.get('win_rate_long_pct'))} |")
    lines.append(f"| Win Rate SHORT | {_fmt_pct(summary.get('win_rate_short_pct'))} |")
    lines.append(f"| SL exits | {summary.get('sl_hit_count', 0)} |")
    lines.append(f"| TP exits | {summary.get('tp_hit_count', 0)} |")
    lines.append(f"| Time exits | {summary.get('time_exit_count', 0)} |")
    lines.append(f"| MAE exits | {summary.get('mae_exit_count', 0)} |")
    lines.append("")

    # By Symbol
    by_symbol = summary.get("by_symbol", {})
    if by_symbol:
        lines.append("## By Symbol")
        lines.append("")
        lines.append("| Symbol | Trades | Win Rate | PnL |")
        lines.append("|--------|--------|----------|-----|")
        for sym, stats in sorted(by_symbol.items()):
            t = stats.get("trades", 0)
            w = stats.get("wins", 0)
            wr = f"{w / t * 100:.1f}%" if t > 0 else "N/A"
            pnl = _fmt_usd(stats.get("pnl_usd"))
            lines.append(f"| {sym} | {t} | {wr} | {pnl} |")
        lines.append("")

    # By Regime
    by_regime = summary.get("by_regime", {})
    if by_regime:
        lines.append("## By Regime")
        lines.append("")
        lines.append("| Regime | Trades | Win Rate | PnL |")
        lines.append("|--------|--------|----------|-----|")
        for regime, stats in sorted(by_regime.items()):
            t = stats.get("trades", 0)
            w = stats.get("wins", 0)
            wr = f"{w / t * 100:.1f}%" if t > 0 else "N/A"
            pnl = _fmt_usd(stats.get("pnl_usd"))
            lines.append(f"| {regime} | {t} | {wr} | {pnl} |")
        lines.append("")

    # Monthly returns
    monthly = summary.get("monthly_returns", [])
    if monthly:
        lines.append("## Monthly Returns")
        lines.append("")
        lines.append("| Month | Trades | PnL |")
        lines.append("|-------|--------|-----|")
        for m in monthly:
            lines.append(f"| {m.get('month')} | {m.get('trades', 0)} | {_fmt_usd(m.get('pnl_usd'))} |")
        lines.append("")

    # Equity Curve (text summary only — too large for full table)
    eq = summary.get("equity_curve", [])
    if eq:
        first = eq[0]
        last = eq[-1]
        lines.append("## Equity Curve (endpoints)")
        lines.append("")
        lines.append(f"- Start: `{first.get('date')}` — balance `{_fmt_usd(first.get('balance'))}`")
        lines.append(f"- End:   `{last.get('date')}` — balance `{_fmt_usd(last.get('balance'))}`")
        lines.append(f"- Points in curve: {len(eq)}")
        lines.append("")

    return "\n".join(lines)


def _build_comparison_report(runs: list[dict[str, Any]]) -> str:
    """Build a comparison table for multiple runs."""
    lines: list[str] = []
    lines.append("# Backtest Comparison Report")
    lines.append("")
    lines.append(f"Generated: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    # Header row
    run_labels = [f"Run {r['run_id'][:8]}" for r in runs]

    def _comparison_row(metric: str, extractor) -> str:
        values = [extractor(r) for r in runs]
        # Compute delta only for 2 runs
        delta = ""
        if len(runs) == 2:
            try:
                v0 = float(str(values[0]).replace("$", "").replace(",", "").replace("%", ""))
                v1 = float(str(values[1]).replace("$", "").replace(",", "").replace("%", ""))
                diff = v1 - v0
                delta = f"+{diff:.2f}" if diff >= 0 else f"{diff:.2f}"
            except (TypeError, ValueError):
                delta = "N/A"
        cells = " | ".join(str(v) for v in values)
        if len(runs) == 2:
            return f"| {metric} | {cells} | {delta} |"
        return f"| {metric} | {cells} |"

    header_cols = " | ".join(run_labels)
    separator_cols = " | ".join(["---"] * len(runs))
    if len(runs) == 2:
        lines.append(f"| Metric | {header_cols} | Delta |")
        lines.append(f"|--------|{separator_cols}|-------|")
    else:
        lines.append(f"| Metric | {header_cols} |")
        lines.append(f"|--------|{separator_cols}|")

    def _s(r: dict, key: str) -> Any:
        return r["summary"].get(key)

    lines.append(_comparison_row("Run ID", lambda r: f"`{r['run_id'][:8]}`"))
    lines.append(_comparison_row("Data Hash", lambda r: f"`{_s(r, 'data_hash') or 'N/A'}`"))
    lines.append(_comparison_row("Trades", lambda r: _s(r, "total_trades") or 0))
    lines.append(_comparison_row("Win Rate", lambda r: _fmt_pct(_s(r, "win_rate_pct"))))
    lines.append(_comparison_row("Profit Factor", lambda r: _fmt_val(_s(r, "profit_factor"))))
    lines.append(_comparison_row("Total PnL", lambda r: _fmt_usd(_s(r, "total_pnl_usd"))))
    lines.append(_comparison_row("Max Drawdown", lambda r: _fmt_pct(_s(r, "max_drawdown_pct"))))
    lines.append(_comparison_row("LONG WR", lambda r: _fmt_pct(_s(r, "win_rate_long_pct"))))
    lines.append(_comparison_row("SHORT WR", lambda r: _fmt_pct(_s(r, "win_rate_short_pct"))))
    lines.append(_comparison_row("SL exits", lambda r: _s(r, "sl_hit_count") or 0))
    lines.append(_comparison_row("TP exits", lambda r: _s(r, "tp_hit_count") or 0))
    lines.append("")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────


async def _main(run_ids: list[str], output: Optional[str]) -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("ERROR: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    runs: list[dict[str, Any]] = []
    trades_by_run: dict[str, list[dict[str, Any]]] = {}

    async with async_session() as session:
        for run_id in run_ids:
            run = await _fetch_run(session, run_id)
            if run is None:
                print(f"WARNING: run_id {run_id!r} not found in backtest_runs", file=sys.stderr)
                continue
            runs.append(run)
            trades_by_run[run_id] = await _fetch_trades(session, run_id)

    if not runs:
        print("ERROR: No runs found.", file=sys.stderr)
        sys.exit(1)

    sections: list[str] = []

    if len(runs) > 1:
        sections.append(_build_comparison_report(runs))
        sections.append("---")
        sections.append("")

    for run in runs:
        trades = trades_by_run.get(run["run_id"], [])
        sections.append(_build_single_report(run, trades))
        sections.append("")
        sections.append("---")
        sections.append("")

    report = "\n".join(sections)

    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(report, encoding="utf-8")
        print(f"Report written to {output}")
    else:
        print(report)

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate markdown backtest report from backtest_runs table."
    )
    parser.add_argument(
        "--run-id",
        dest="run_ids",
        action="append",
        required=True,
        metavar="UUID",
        help="Backtest run UUID (can be repeated for comparison).",
    )
    parser.add_argument(
        "--output",
        dest="output",
        default=None,
        metavar="PATH",
        help="Output markdown file path (default: stdout).",
    )
    args = parser.parse_args()
    asyncio.run(_main(args.run_ids, args.output))


if __name__ == "__main__":
    main()
