"""Simulator v3: candle SL/TP, slippage, swap, unrealized USD, virtual account

Revision ID: e5f6a7b8c9d0
Revises: d1e2f3a4b5c6
Create Date: 2026-03-18 00:00:00.000000

Changes:
  signal_results:    candle_high_at_exit, candle_low_at_exit, exit_slippage_pips,
                     swap_pips, swap_usd, composite_score
  virtual_portfolio: unrealized_pnl_usd, accrued_swap_pips, accrued_swap_usd,
                     last_swap_date, account_balance_at_entry
  virtual_account:   новая таблица (динамический баланс счёта)
"""

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── signal_results ────────────────────────────────────────────────────────
    op.add_column(
        "signal_results",
        sa.Column("candle_high_at_exit", sa.Numeric(18, 8), nullable=True),
    )
    op.add_column(
        "signal_results",
        sa.Column("candle_low_at_exit", sa.Numeric(18, 8), nullable=True),
    )
    op.add_column(
        "signal_results",
        sa.Column("exit_slippage_pips", sa.Numeric(8, 4), nullable=True),
    )
    op.add_column(
        "signal_results",
        sa.Column("swap_pips", sa.Numeric(14, 4), nullable=True),
    )
    op.add_column(
        "signal_results",
        sa.Column("swap_usd", sa.Numeric(14, 4), nullable=True),
    )
    op.add_column(
        "signal_results",
        sa.Column("composite_score", sa.Numeric(8, 4), nullable=True),
    )

    # ── virtual_portfolio ─────────────────────────────────────────────────────
    op.add_column(
        "virtual_portfolio",
        sa.Column("unrealized_pnl_usd", sa.Numeric(14, 4), nullable=True),
    )
    op.add_column(
        "virtual_portfolio",
        sa.Column("accrued_swap_pips", sa.Numeric(14, 4), server_default="0", nullable=True),
    )
    op.add_column(
        "virtual_portfolio",
        sa.Column("accrued_swap_usd", sa.Numeric(14, 4), server_default="0", nullable=True),
    )
    op.add_column(
        "virtual_portfolio",
        sa.Column("last_swap_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "virtual_portfolio",
        sa.Column("account_balance_at_entry", sa.Numeric(14, 4), nullable=True),
    )

    # ── virtual_account ───────────────────────────────────────────────────────
    # Table may already exist if created outside of Alembic — use IF NOT EXISTS
    op.execute("""
        CREATE TABLE IF NOT EXISTS virtual_account (
            id SERIAL PRIMARY KEY,
            initial_balance NUMERIC(14,4) NOT NULL DEFAULT 1000.0,
            current_balance NUMERIC(14,4) NOT NULL DEFAULT 1000.0,
            peak_balance    NUMERIC(14,4) NOT NULL DEFAULT 1000.0,
            total_realized_pnl NUMERIC(14,4) NOT NULL DEFAULT 0.0,
            total_trades    INTEGER NOT NULL DEFAULT 0,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    # Insert initial row only if table is empty
    op.execute("""
        INSERT INTO virtual_account
            (initial_balance, current_balance, peak_balance, total_realized_pnl, total_trades)
        SELECT 1000.0, 1000.0, 1000.0, 0.0, 0
        WHERE NOT EXISTS (SELECT 1 FROM virtual_account)
    """)


def downgrade() -> None:
    op.drop_table("virtual_account")

    op.drop_column("virtual_portfolio", "account_balance_at_entry")
    op.drop_column("virtual_portfolio", "last_swap_date")
    op.drop_column("virtual_portfolio", "accrued_swap_usd")
    op.drop_column("virtual_portfolio", "accrued_swap_pips")
    op.drop_column("virtual_portfolio", "unrealized_pnl_usd")

    op.drop_column("signal_results", "composite_score")
    op.drop_column("signal_results", "swap_usd")
    op.drop_column("signal_results", "swap_pips")
    op.drop_column("signal_results", "exit_slippage_pips")
    op.drop_column("signal_results", "candle_low_at_exit")
    op.drop_column("signal_results", "candle_high_at_exit")
