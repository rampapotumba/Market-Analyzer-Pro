"""v2 schema: PostgreSQL/TimescaleDB migration

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-03-17 00:00:00.000000

This migration creates the complete v2 schema from scratch on PostgreSQL + TimescaleDB.
It does NOT depend on the v1 SQLite migration (fb6cdd34ba8b) — run this on a fresh
PostgreSQL database.

TimescaleDB hypertables are created for:
  - price_data   (partition by timestamp, chunk 7 days)
  - order_flow_data (partition by timestamp, chunk 1 day)
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "a1b2c3d4e5f6"
down_revision = "fb6cdd34ba8b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── instruments ───────────────────────────────────────────────────────────
    op.create_table(
        "instruments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("pip_size", sa.Numeric(18, 8), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("sector", sa.String(64), nullable=True),
        sa.Column("base_currency", sa.String(8), nullable=True),
        sa.Column("quote_currency", sa.String(8), nullable=True),
        sa.Column("central_bank", sa.String(16), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_instruments_symbol", "instruments", ["symbol"], unique=True)

    # ── price_data ────────────────────────────────────────────────────────────
    op.create_table(
        "price_data",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(18, 8), nullable=False),
        sa.Column("high", sa.Numeric(18, 8), nullable=False),
        sa.Column("low", sa.Numeric(18, 8), nullable=False),
        sa.Column("close", sa.Numeric(18, 8), nullable=False),
        sa.Column("volume", sa.Numeric(18, 8), nullable=True),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.PrimaryKeyConstraint("id", "timestamp"),
    )
    # Auto-increment via sequence (TimescaleDB requires timestamp in PK)
    op.execute("CREATE SEQUENCE IF NOT EXISTS price_data_id_seq")
    op.execute("ALTER TABLE price_data ALTER COLUMN id SET DEFAULT nextval('price_data_id_seq')")
    op.create_index("ix_price_data_instrument_timeframe", "price_data", ["instrument_id", "timeframe"])
    op.create_index("ix_price_data_timestamp", "price_data", ["timestamp"])
    op.execute("ALTER TABLE price_data ADD CONSTRAINT uix_price_data UNIQUE (instrument_id, timeframe, timestamp)")

    # Convert price_data to TimescaleDB hypertable (7-day chunks)
    op.execute(
        "SELECT create_hypertable('price_data', 'timestamp', "
        "chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE)"
    )

    # ── signals ───────────────────────────────────────────────────────────────
    op.create_table(
        "signals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("signal_strength", sa.String(16), nullable=False),
        sa.Column("entry_price", sa.Numeric(18, 8), nullable=True),
        sa.Column("stop_loss", sa.Numeric(18, 8), nullable=True),
        sa.Column("take_profit_1", sa.Numeric(18, 8), nullable=True),
        sa.Column("take_profit_2", sa.Numeric(18, 8), nullable=True),
        sa.Column("take_profit_3", sa.Numeric(18, 8), nullable=True),
        sa.Column("risk_reward", sa.Numeric(18, 4), nullable=True),
        sa.Column("position_size_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("composite_score", sa.Numeric(8, 4), server_default="0"),
        sa.Column("ta_score", sa.Numeric(8, 4), server_default="0"),
        sa.Column("fa_score", sa.Numeric(8, 4), server_default="0"),
        sa.Column("sentiment_score", sa.Numeric(8, 4), server_default="0"),
        sa.Column("geo_score", sa.Numeric(8, 4), server_default="0"),
        sa.Column("of_score", sa.Numeric(8, 4), nullable=True),
        sa.Column("correlation_score", sa.Numeric(8, 4), nullable=True),
        sa.Column("regime", sa.String(32), nullable=True),
        sa.Column("earnings_days_ahead", sa.Integer(), nullable=True),
        sa.Column("portfolio_heat", sa.Numeric(5, 2), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 2), server_default="0"),
        sa.Column("horizon", sa.String(32), nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("indicators_snapshot", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), server_default="created"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_signals_instrument_id", "signals", ["instrument_id"])
    op.create_index("ix_signals_status", "signals", ["status"])

    # ── signal_results ────────────────────────────────────────────────────────
    op.create_table(
        "signal_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=False),
        sa.Column("entry_filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entry_actual_price", sa.Numeric(18, 8), nullable=True),
        sa.Column("exit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_price", sa.Numeric(18, 8), nullable=True),
        sa.Column("exit_reason", sa.String(16), nullable=True),
        sa.Column("pnl_pips", sa.Numeric(18, 4), nullable=True),
        sa.Column("pnl_percent", sa.Numeric(8, 4), nullable=True),
        sa.Column("result", sa.String(16), nullable=True),
        sa.Column("max_favorable_excursion", sa.Numeric(18, 8), nullable=True),
        sa.Column("max_adverse_excursion", sa.Numeric(18, 8), nullable=True),
        sa.Column("price_at_expiry", sa.Numeric(18, 8), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["signal_id"], ["signals.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("signal_id"),
    )
    op.create_index("ix_signal_results_signal_id", "signal_results", ["signal_id"])

    # ── accuracy_stats ────────────────────────────────────────────────────────
    op.create_table(
        "accuracy_stats",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("period", sa.String(32), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("instrument_id", sa.Integer(), nullable=True),
        sa.Column("market", sa.String(16), nullable=True),
        sa.Column("timeframe", sa.String(8), nullable=True),
        sa.Column("total_signals", sa.Integer(), server_default="0"),
        sa.Column("wins", sa.Integer(), server_default="0"),
        sa.Column("losses", sa.Integer(), server_default="0"),
        sa.Column("breakevens", sa.Integer(), server_default="0"),
        sa.Column("win_rate", sa.Numeric(8, 4), nullable=True),
        sa.Column("profit_factor", sa.Numeric(8, 4), nullable=True),
        sa.Column("avg_win_pips", sa.Numeric(18, 4), nullable=True),
        sa.Column("avg_loss_pips", sa.Numeric(18, 4), nullable=True),
        sa.Column("sharpe_ratio", sa.Numeric(8, 4), nullable=True),
        sa.Column("max_drawdown_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("expectancy", sa.Numeric(18, 4), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── macro_data ────────────────────────────────────────────────────────────
    op.create_table(
        "macro_data",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("indicator_name", sa.String(64), nullable=False),
        sa.Column("country", sa.String(8), nullable=False),
        sa.Column("value", sa.Numeric(18, 8), nullable=True),
        sa.Column("previous_value", sa.Numeric(18, 8), nullable=True),
        sa.Column("forecast_value", sa.Numeric(18, 8), nullable=True),
        sa.Column("release_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("indicator_name", "country", "release_date", name="uix_macro_data"),
    )

    # ── central_bank_rates ────────────────────────────────────────────────────
    op.create_table(
        "central_bank_rates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("bank", sa.String(16), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("rate", sa.Numeric(8, 4), nullable=False),
        sa.Column("effective_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_meeting_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bias", sa.String(16), nullable=True),
        sa.Column("source", sa.String(64), nullable=True),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bank", "effective_date", name="uix_central_bank_rates"),
    )
    op.create_index("ix_cbr_bank_date", "central_bank_rates", ["bank", "effective_date"])

    # ── company_fundamentals ──────────────────────────────────────────────────
    op.create_table(
        "company_fundamentals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(16), nullable=False),
        sa.Column("pe_ratio", sa.Numeric(10, 4), nullable=True),
        sa.Column("eps", sa.Numeric(14, 4), nullable=True),
        sa.Column("revenue_growth_yoy", sa.Numeric(8, 4), nullable=True),
        sa.Column("gross_margin", sa.Numeric(8, 4), nullable=True),
        sa.Column("net_margin", sa.Numeric(8, 4), nullable=True),
        sa.Column("debt_to_equity", sa.Numeric(10, 4), nullable=True),
        sa.Column("roe", sa.Numeric(8, 4), nullable=True),
        sa.Column("analyst_rating", sa.String(16), nullable=True),
        sa.Column("analyst_target", sa.Numeric(14, 4), nullable=True),
        sa.Column("earnings_surprise_avg", sa.Numeric(8, 4), nullable=True),
        sa.Column("insider_net_shares", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instrument_id", "period", name="uix_company_fundamentals"),
    )
    op.create_index("ix_company_fundamentals_instrument_id", "company_fundamentals", ["instrument_id"])

    # ── onchain_data ──────────────────────────────────────────────────────────
    op.create_table(
        "onchain_data",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("nvt_ratio", sa.Numeric(14, 4), nullable=True),
        sa.Column("active_addresses", sa.BigInteger(), nullable=True),
        sa.Column("mvrv_ratio", sa.Numeric(10, 4), nullable=True),
        sa.Column("exchange_inflow", sa.Numeric(24, 8), nullable=True),
        sa.Column("exchange_outflow", sa.Numeric(24, 8), nullable=True),
        sa.Column("funding_rate", sa.Numeric(10, 6), nullable=True),
        sa.Column("open_interest", sa.Numeric(24, 4), nullable=True),
        sa.Column("dominance", sa.Numeric(8, 4), nullable=True),
        sa.Column("source", sa.String(32), nullable=True),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instrument_id", "timestamp", name="uix_onchain_data"),
    )
    op.create_index("ix_onchain_timestamp", "onchain_data", ["timestamp"])

    # ── regime_state ──────────────────────────────────────────────────────────
    op.create_table(
        "regime_state",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("regime", sa.String(32), nullable=False),
        sa.Column("adx", sa.Numeric(8, 4), nullable=True),
        sa.Column("atr_percentile", sa.Numeric(8, 4), nullable=True),
        sa.Column("vix", sa.Numeric(8, 4), nullable=True),
        sa.Column("ta_weight", sa.Numeric(5, 3), nullable=False),
        sa.Column("fa_weight", sa.Numeric(5, 3), nullable=False),
        sa.Column("sentiment_weight", sa.Numeric(5, 3), nullable=False),
        sa.Column("geo_weight", sa.Numeric(5, 3), nullable=False),
        sa.Column("sl_atr_multiplier", sa.Numeric(5, 3), nullable=False),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_regime_instrument_date", "regime_state", ["instrument_id", "detected_at"])

    # ── order_flow_data ───────────────────────────────────────────────────────
    op.create_table(
        "order_flow_data",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cvd", sa.Numeric(24, 8), nullable=True),
        sa.Column("funding_rate", sa.Numeric(10, 6), nullable=True),
        sa.Column("open_interest", sa.Numeric(24, 4), nullable=True),
        sa.Column("long_liquidations", sa.Numeric(24, 4), nullable=True),
        sa.Column("short_liquidations", sa.Numeric(24, 4), nullable=True),
        sa.Column("buy_volume", sa.Numeric(24, 8), nullable=True),
        sa.Column("sell_volume", sa.Numeric(24, 8), nullable=True),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.PrimaryKeyConstraint("id", "timestamp"),
    )
    op.execute("CREATE SEQUENCE IF NOT EXISTS order_flow_data_id_seq")
    op.execute("ALTER TABLE order_flow_data ALTER COLUMN id SET DEFAULT nextval('order_flow_data_id_seq')")
    op.create_index("ix_order_flow_timestamp", "order_flow_data", ["timestamp"])

    # Convert order_flow_data to TimescaleDB hypertable (1-day chunks)
    op.execute(
        "SELECT create_hypertable('order_flow_data', 'timestamp', "
        "chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE)"
    )

    # ── social_sentiment ──────────────────────────────────────────────────────
    op.create_table(
        "social_sentiment",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("score", sa.Numeric(8, 4), nullable=False),
        sa.Column("mention_count", sa.Integer(), nullable=True),
        sa.Column("raw_data", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instrument_id", "source", "timestamp", name="uix_social_sentiment"),
    )
    op.create_index("ix_social_sentiment_timestamp", "social_sentiment", ["timestamp"])

    # ── economic_events ───────────────────────────────────────────────────────
    op.create_table(
        "economic_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("country", sa.String(8), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("event_name", sa.String(256), nullable=False),
        sa.Column("impact", sa.String(16), server_default="low"),
        sa.Column("previous", sa.Numeric(18, 4), nullable=True),
        sa.Column("estimate", sa.Numeric(18, 4), nullable=True),
        sa.Column("actual", sa.Numeric(18, 4), nullable=True),
        sa.Column("unit", sa.String(32), nullable=True),
        sa.Column("related_instruments", sa.Text(), nullable=True),
        sa.Column("source", sa.String(32), server_default="FMP"),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_date", "country", "event_name", name="uix_economic_event"),
    )
    op.create_index("ix_economic_events_date", "economic_events", ["event_date"])

    # ── news_events ───────────────────────────────────────────────────────────
    op.create_table(
        "news_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("headline", sa.String(512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("source", sa.String(64), nullable=True),
        sa.Column("url", sa.String(512), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sentiment_score", sa.Numeric(8, 4), nullable=True),
        sa.Column("importance", sa.String(16), server_default="low"),
        sa.Column("related_instruments", sa.Text(), nullable=True),
        sa.Column("category", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_news_events_published_at", "news_events", ["published_at"])

    # ── virtual_portfolio ─────────────────────────────────────────────────────
    op.create_table(
        "virtual_portfolio",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=False),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("size_pct", sa.Numeric(8, 4), nullable=False),
        sa.Column("entry_price", sa.Numeric(18, 8), nullable=False),
        sa.Column("current_price", sa.Numeric(18, 8), nullable=True),
        sa.Column("unrealized_pnl_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("realized_pnl_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("status", sa.String(16), server_default="open"),
        sa.ForeignKeyConstraint(["signal_id"], ["signals.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("signal_id"),
    )
    op.create_index("ix_virtual_portfolio_signal_id", "virtual_portfolio", ["signal_id"])

    # ── backtest_runs ─────────────────────────────────────────────────────────
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("instrument_ids", sa.Text(), nullable=True),
        sa.Column("in_sample_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("in_sample_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("oos_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("oos_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("optimal_weights", sa.Text(), nullable=True),
        sa.Column("oos_sharpe", sa.Numeric(8, 4), nullable=True),
        sa.Column("oos_profit_factor", sa.Numeric(8, 4), nullable=True),
        sa.Column("oos_win_rate", sa.Numeric(8, 4), nullable=True),
        sa.Column("oos_max_drawdown", sa.Numeric(8, 4), nullable=True),
        sa.Column("oos_total_trades", sa.Integer(), nullable=True),
        sa.Column("monte_carlo_ci_drawdown", sa.Numeric(8, 4), nullable=True),
        sa.Column("passed_validation", sa.Boolean(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── backtest_trades ───────────────────────────────────────────────────────
    op.create_table(
        "backtest_trades",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("entry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("entry_price", sa.Numeric(18, 8), nullable=False),
        sa.Column("exit_price", sa.Numeric(18, 8), nullable=True),
        sa.Column("pnl_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("result", sa.String(16), nullable=True),
        sa.Column("composite_score", sa.Numeric(8, 4), nullable=True),
        sa.Column("phase", sa.String(8), server_default="oos"),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["backtest_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backtest_trades_run_id", "backtest_trades", ["run_id"])
    op.create_index("ix_backtest_trades_instrument_id", "backtest_trades", ["instrument_id"])


def downgrade() -> None:
    op.drop_table("backtest_trades")
    op.drop_table("backtest_runs")
    op.drop_table("virtual_portfolio")
    op.drop_table("news_events")
    op.drop_table("economic_events")
    op.drop_table("social_sentiment")
    op.drop_table("order_flow_data")
    op.drop_table("regime_state")
    op.drop_table("onchain_data")
    op.drop_table("company_fundamentals")
    op.drop_table("central_bank_rates")
    op.drop_table("macro_data")
    op.drop_table("accuracy_stats")
    op.drop_table("signal_results")
    op.drop_table("signals")
    op.drop_table("price_data")
    op.drop_table("instruments")
