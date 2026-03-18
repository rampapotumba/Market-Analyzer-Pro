"""Simulator v2: add lifecycle and spread tracking columns

Revision ID: d1e2f3a4b5c6
Revises: c3d4e5f6a7b8
Create Date: 2026-03-18 00:00:00.000000

Changes:
  virtual_portfolio: mfe, mae, breakeven_moved, partial_closed, trailing_stop,
                     current_stop_loss, size_remaining_pct, partial_close_price,
                     partial_close_at, partial_pnl_pct, entry_filled_at
  signal_results:    pnl_usd, partial_close_pnl_usd, full_close_pnl_usd
"""

import sqlalchemy as sa
from alembic import op

revision = "d1e2f3a4b5c6"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── virtual_portfolio ────────────────────────────────────────────────────
    op.add_column(
        "virtual_portfolio",
        sa.Column("mfe", sa.Numeric(18, 8), server_default="0", nullable=False),
    )
    op.add_column(
        "virtual_portfolio",
        sa.Column("mae", sa.Numeric(18, 8), server_default="0", nullable=False),
    )
    op.add_column(
        "virtual_portfolio",
        sa.Column("breakeven_moved", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "virtual_portfolio",
        sa.Column("partial_closed", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "virtual_portfolio",
        sa.Column("trailing_stop", sa.Numeric(18, 8), nullable=True),
    )
    op.add_column(
        "virtual_portfolio",
        sa.Column("current_stop_loss", sa.Numeric(18, 8), nullable=True),
    )
    op.add_column(
        "virtual_portfolio",
        sa.Column("size_remaining_pct", sa.Numeric(8, 4), server_default="1.0", nullable=False),
    )
    op.add_column(
        "virtual_portfolio",
        sa.Column("partial_close_price", sa.Numeric(18, 8), nullable=True),
    )
    op.add_column(
        "virtual_portfolio",
        sa.Column("partial_close_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "virtual_portfolio",
        sa.Column("partial_pnl_pct", sa.Numeric(8, 4), nullable=True),
    )
    op.add_column(
        "virtual_portfolio",
        sa.Column("entry_filled_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── signal_results ────────────────────────────────────────────────────────
    op.add_column(
        "signal_results",
        sa.Column("pnl_usd", sa.Numeric(14, 4), nullable=True),
    )
    op.add_column(
        "signal_results",
        sa.Column("partial_close_pnl_usd", sa.Numeric(14, 4), nullable=True),
    )
    op.add_column(
        "signal_results",
        sa.Column("full_close_pnl_usd", sa.Numeric(14, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("signal_results", "full_close_pnl_usd")
    op.drop_column("signal_results", "partial_close_pnl_usd")
    op.drop_column("signal_results", "pnl_usd")

    op.drop_column("virtual_portfolio", "entry_filled_at")
    op.drop_column("virtual_portfolio", "partial_pnl_pct")
    op.drop_column("virtual_portfolio", "partial_close_at")
    op.drop_column("virtual_portfolio", "partial_close_price")
    op.drop_column("virtual_portfolio", "size_remaining_pct")
    op.drop_column("virtual_portfolio", "current_stop_loss")
    op.drop_column("virtual_portfolio", "trailing_stop")
    op.drop_column("virtual_portfolio", "partial_closed")
    op.drop_column("virtual_portfolio", "breakeven_moved")
    op.drop_column("virtual_portfolio", "mae")
    op.drop_column("virtual_portfolio", "mfe")
