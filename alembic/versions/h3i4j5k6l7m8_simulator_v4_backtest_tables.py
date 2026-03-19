"""Simulator v4: backtest_runs and backtest_trades tables (SIM-22)

Revision ID: h3i4j5k6l7m8
Revises: g2h3i4j5k6l7
Create Date: 2026-03-19 00:00:00.000000

Changes:
  DROP (if exists) old backtest_runs / backtest_trades schema (walk-forward optimization)
  CREATE backtest_runs  — UUID PK, params JSONB, status, summary JSONB
  CREATE backtest_trades — SERIAL PK, run_id UUID FK (CASCADE), per-trade fields
"""

import sqlalchemy as sa
from alembic import op

revision = "h3i4j5k6l7m8"
down_revision = "g2h3i4j5k6l7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop old tables if they exist (old walk-forward schema, not v4)
    op.execute("DROP TABLE IF EXISTS backtest_trades CASCADE")
    op.execute("DROP TABLE IF EXISTS backtest_runs CASCADE")

    # backtest_runs — one row per backtest run
    op.execute("""
        CREATE TABLE backtest_runs (
            id          VARCHAR(36)  PRIMARY KEY,
            params      JSONB,
            status      VARCHAR(16)  NOT NULL DEFAULT 'pending',
            started_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ,
            summary     JSONB
        )
    """)

    # backtest_trades — one row per simulated trade
    op.execute("""
        CREATE TABLE backtest_trades (
            id               SERIAL       PRIMARY KEY,
            run_id           VARCHAR(36)  NOT NULL
                             REFERENCES backtest_runs(id) ON DELETE CASCADE,
            symbol           VARCHAR(32)  NOT NULL,
            timeframe        VARCHAR(8)   NOT NULL,
            direction        VARCHAR(8)   NOT NULL,
            entry_price      NUMERIC(18,8) NOT NULL,
            exit_price       NUMERIC(18,8),
            exit_reason      VARCHAR(32),
            pnl_pips         NUMERIC(14,4),
            pnl_usd          NUMERIC(14,4),
            result           VARCHAR(16),
            composite_score  NUMERIC(8,4),
            entry_at         TIMESTAMPTZ,
            exit_at          TIMESTAMPTZ,
            duration_minutes INTEGER,
            mfe              NUMERIC(18,8),
            mae              NUMERIC(18,8)
        )
    """)

    op.execute("""
        CREATE INDEX ix_backtest_trades_run_id ON backtest_trades(run_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS backtest_trades CASCADE")
    op.execute("DROP TABLE IF EXISTS backtest_runs CASCADE")
