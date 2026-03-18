"""initial (SQLite placeholder — no-op on PostgreSQL)

Revision ID: fb6cdd34ba8b
Revises:
Create Date: 2026-03-17 00:55:29.604101

This revision was auto-generated against SQLite in Phase 1.
On PostgreSQL all tables are created by the v2 migration (a1b2c3d4e5f6).
"""
from typing import Sequence, Union

revision: str = "fb6cdd34ba8b"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass  # no-op: schema is created by a1b2c3d4e5f6


def downgrade() -> None:
    pass
