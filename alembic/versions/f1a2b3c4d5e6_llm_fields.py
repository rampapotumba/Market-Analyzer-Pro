"""Add LLM fields to signals table.

Revision ID: f1a2b3c4d5e6
Revises: e5f6a7b8c9d0
Create Date: 2026-03-18

"""
from alembic import op
import sqlalchemy as sa

revision = "f1a2b3c4d5e6"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("signals", sa.Column("llm_score", sa.Numeric(8, 4), nullable=True))
    op.add_column("signals", sa.Column("llm_bias", sa.String(10), nullable=True))
    op.add_column("signals", sa.Column("llm_confidence", sa.Numeric(5, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("signals", "llm_confidence")
    op.drop_column("signals", "llm_bias")
    op.drop_column("signals", "llm_score")
