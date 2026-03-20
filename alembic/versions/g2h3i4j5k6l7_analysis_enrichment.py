"""Analysis enrichment: market_price_at_signal, spread_pips_applied, breakeven_price.

Revision ID: g2h3i4j5k6l7
Revises: f1a2b3c4d5e6
Create Date: 2026-03-18

"""
from alembic import op
import sqlalchemy as sa

revision = "g2h3i4j5k6l7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # signals: market price at the moment the signal was generated
    # (entry_price may be a limit level different from current market price)
    op.add_column(
        "signals",
        sa.Column("market_price_at_signal", sa.Numeric(18, 8), nullable=True),
    )

    # virtual_portfolio: spread cost recorded at position open
    op.add_column(
        "virtual_portfolio",
        sa.Column("spread_pips_applied", sa.Numeric(8, 4), nullable=True),
    )

    # virtual_portfolio: exact price level where breakeven stop was placed
    op.add_column(
        "virtual_portfolio",
        sa.Column("breakeven_price", sa.Numeric(18, 8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("virtual_portfolio", "breakeven_price")
    op.drop_column("virtual_portfolio", "spread_pips_applied")
    op.drop_column("signals", "market_price_at_signal")
