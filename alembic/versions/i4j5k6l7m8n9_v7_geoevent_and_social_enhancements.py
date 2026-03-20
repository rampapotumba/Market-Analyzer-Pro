"""V7: GeoEvent table, social_sentiment enhancements

Revision ID: i4j5k6l7m8n9
Revises: h3i4j5k6l7m8
Create Date: 2026-03-20 00:00:00.000000

Changes:
  1. CREATE geo_events table (ACLED/GDELT geopolitical events)
  2. ALTER social_sentiment.score → nullable
  3. ADD 4 extended columns to social_sentiment (fear_greed_index, reddit_score,
     stocktwits_bullish_pct, put_call_ratio)
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "i4j5k6l7m8n9"
down_revision = "h3i4j5k6l7m8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Create geo_events table ────────────────────────────────────────────
    op.create_table(
        "geo_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("event_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("country", sa.String(100), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=True),
        sa.Column("fatalities", sa.Integer(), server_default="0"),
        sa.Column("severity_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("raw_data", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_geo_events_country_date", "geo_events", ["country", "event_date"]
    )

    # ── 2. Make social_sentiment.score nullable ───────────────────────────────
    op.alter_column(
        "social_sentiment",
        "score",
        existing_type=sa.Numeric(8, 4),
        nullable=True,
    )

    # ── 3. Add extended fields to social_sentiment ────────────────────────────
    op.add_column(
        "social_sentiment",
        sa.Column("fear_greed_index", sa.Numeric(6, 2), nullable=True),
    )
    op.add_column(
        "social_sentiment",
        sa.Column("reddit_score", sa.Numeric(8, 4), nullable=True),
    )
    op.add_column(
        "social_sentiment",
        sa.Column("stocktwits_bullish_pct", sa.Numeric(6, 2), nullable=True),
    )
    op.add_column(
        "social_sentiment",
        sa.Column("put_call_ratio", sa.Numeric(8, 4), nullable=True),
    )


def downgrade() -> None:
    # ── Reverse social_sentiment columns ──────────────────────────────────────
    op.drop_column("social_sentiment", "put_call_ratio")
    op.drop_column("social_sentiment", "stocktwits_bullish_pct")
    op.drop_column("social_sentiment", "reddit_score")
    op.drop_column("social_sentiment", "fear_greed_index")

    op.alter_column(
        "social_sentiment",
        "score",
        existing_type=sa.Numeric(8, 4),
        nullable=False,
    )

    # ── Drop geo_events ──────────────────────────────────────────────────────
    op.drop_index("ix_geo_events_country_date", table_name="geo_events")
    op.drop_table("geo_events")
