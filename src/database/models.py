"""SQLAlchemy 2.0 ORM models — v2 schema."""

import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── Core market instruments ───────────────────────────────────────────────────

class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    market: Mapped[str] = mapped_column(String(16), nullable=False)  # forex/stocks/crypto
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    pip_size: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0.0001"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # v2 additions
    sector: Mapped[Optional[str]] = mapped_column(String(64))          # for stocks
    base_currency: Mapped[Optional[str]] = mapped_column(String(8))    # for forex/crypto
    quote_currency: Mapped[Optional[str]] = mapped_column(String(8))   # for forex/crypto
    central_bank: Mapped[Optional[str]] = mapped_column(String(16))    # ECB/FED/BOJ/…

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    price_data: Mapped[list["PriceData"]] = relationship("PriceData", back_populates="instrument")
    signals: Mapped[list["Signal"]] = relationship("Signal", back_populates="instrument")


# ── OHLCV price data (TimescaleDB hypertable on timestamp) ───────────────────

class PriceData(Base):
    __tablename__ = "price_data"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.id"), nullable=False, index=True
    )
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    open: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))

    instrument: Mapped["Instrument"] = relationship("Instrument", back_populates="price_data")

    __table_args__ = (
        UniqueConstraint("instrument_id", "timeframe", "timestamp", name="uix_price_data"),
        Index("ix_price_data_instrument_timeframe", "instrument_id", "timeframe"),
        Index("ix_price_data_timestamp", "timestamp"),
    )


# ── Trading signals ───────────────────────────────────────────────────────────

class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.id"), nullable=False, index=True
    )
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    direction: Mapped[str] = mapped_column(String(8), nullable=False)        # LONG/SHORT/HOLD
    signal_strength: Mapped[str] = mapped_column(String(16), nullable=False)  # STRONG_BUY/…

    entry_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    stop_loss: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    take_profit_1: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    take_profit_2: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    take_profit_3: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))  # v2
    risk_reward: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    position_size_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))

    composite_score: Mapped[float] = mapped_column(Numeric(8, 4), default=0.0)
    ta_score: Mapped[float] = mapped_column(Numeric(8, 4), default=0.0)
    fa_score: Mapped[float] = mapped_column(Numeric(8, 4), default=0.0)
    sentiment_score: Mapped[float] = mapped_column(Numeric(8, 4), default=0.0)
    geo_score: Mapped[float] = mapped_column(Numeric(8, 4), default=0.0)

    # v2 score components
    of_score: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))         # order-flow
    correlation_score: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    regime: Mapped[Optional[str]] = mapped_column(String(32))                # detected regime
    earnings_days_ahead: Mapped[Optional[int]] = mapped_column(Integer)      # for stocks
    portfolio_heat: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))   # % of account at risk

    # Market context at signal generation time
    market_price_at_signal: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))  # spot price when signal fired

    # LLM (Claude) analysis fields
    llm_score: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))        # -100..+100
    llm_bias: Mapped[Optional[str]] = mapped_column(String(10))              # BULLISH/BEARISH/NEUTRAL
    llm_confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))   # 0..100

    confidence: Mapped[float] = mapped_column(Numeric(5, 2), default=0.0)
    horizon: Mapped[Optional[str]] = mapped_column(String(32))
    reasoning: Mapped[Optional[str]] = mapped_column(Text)         # JSON
    indicators_snapshot: Mapped[Optional[str]] = mapped_column(Text)  # JSON
    status: Mapped[str] = mapped_column(String(16), default="created", index=True)
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True))

    instrument: Mapped["Instrument"] = relationship("Instrument", back_populates="signals")
    result: Mapped[Optional["SignalResult"]] = relationship(
        "SignalResult", back_populates="signal", uselist=False
    )


class SignalResult(Base):
    __tablename__ = "signal_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("signals.id"), unique=True, nullable=False, index=True
    )
    entry_filled_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True))
    entry_actual_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    exit_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True))
    exit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    exit_reason: Mapped[Optional[str]] = mapped_column(String(16))  # sl_hit/tp1_hit/tp2_hit/tp3_hit/expired/manual
    pnl_pips: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    pnl_percent: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    result: Mapped[Optional[str]] = mapped_column(String(16))  # win/loss/breakeven
    max_favorable_excursion: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    max_adverse_excursion: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    price_at_expiry: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    duration_minutes: Mapped[Optional[int]] = mapped_column(Integer)

    # v2 simulator fields (SIM-06, SIM-07)
    pnl_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    partial_close_pnl_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    full_close_pnl_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))

    # v3 simulator fields (SIM-09, SIM-10, SIM-13, SIM-14)
    candle_high_at_exit: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    candle_low_at_exit: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    exit_slippage_pips: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    swap_pips: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    swap_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    composite_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))

    signal: Mapped["Signal"] = relationship("Signal", back_populates="result")


# ── Accuracy stats ────────────────────────────────────────────────────────────

class AccuracyStats(Base):
    __tablename__ = "accuracy_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    period: Mapped[str] = mapped_column(String(32), nullable=False)  # all_time/monthly/weekly
    period_start: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True))
    instrument_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("instruments.id"), nullable=True
    )
    market: Mapped[Optional[str]] = mapped_column(String(16))
    timeframe: Mapped[Optional[str]] = mapped_column(String(8))
    total_signals: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    breakevens: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    profit_factor: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    avg_win_pips: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    avg_loss_pips: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    sharpe_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    max_drawdown_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    expectancy: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ── Macro-economic data ───────────────────────────────────────────────────────

class MacroData(Base):
    __tablename__ = "macro_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    indicator_name: Mapped[str] = mapped_column(String(64), nullable=False)
    country: Mapped[str] = mapped_column(String(8), nullable=False)
    value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    previous_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    forecast_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    release_date: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True))
    source: Mapped[Optional[str]] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint("indicator_name", "country", "release_date", name="uix_macro_data"),
    )


# ── Central bank interest rates ───────────────────────────────────────────────

class CentralBankRate(Base):
    __tablename__ = "central_bank_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bank: Mapped[str] = mapped_column(String(16), nullable=False)   # FED/ECB/BOJ/BOE/RBA/BOC/SNB/RBNZ
    currency: Mapped[str] = mapped_column(String(8), nullable=False)  # USD/EUR/JPY/…
    rate: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)  # % e.g. 5.25
    effective_date: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    next_meeting_date: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True))
    bias: Mapped[Optional[str]] = mapped_column(String(16))  # hawkish/neutral/dovish
    source: Mapped[Optional[str]] = mapped_column(String(64))
    collected_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("bank", "effective_date", name="uix_central_bank_rates"),
        Index("ix_cbr_bank_date", "bank", "effective_date"),
    )


# ── Company fundamentals (stocks) ────────────────────────────────────────────

class CompanyFundamentals(Base):
    __tablename__ = "company_fundamentals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.id"), nullable=False, index=True
    )
    period: Mapped[str] = mapped_column(String(16), nullable=False)  # YYYY-QN or YYYY
    pe_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    eps: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    revenue_growth_yoy: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))  # %
    gross_margin: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))        # %
    net_margin: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))          # %
    debt_to_equity: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    roe: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))                 # %
    analyst_rating: Mapped[Optional[str]] = mapped_column(String(16))            # buy/hold/sell
    analyst_target: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    earnings_surprise_avg: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))  # % 4Q avg
    insider_net_shares: Mapped[Optional[int]] = mapped_column(BigInteger)         # >0 buying
    source: Mapped[Optional[str]] = mapped_column(String(32))
    collected_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("instrument_id", "period", name="uix_company_fundamentals"),
    )


# ── On-chain data (crypto) ────────────────────────────────────────────────────

class OnchainData(Base):
    __tablename__ = "onchain_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.id"), nullable=False, index=True
    )
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    nvt_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    active_addresses: Mapped[Optional[int]] = mapped_column(BigInteger)
    mvrv_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    exchange_inflow: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 8))
    exchange_outflow: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 8))
    funding_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    open_interest: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 4))
    dominance: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))  # BTC dominance %
    source: Mapped[Optional[str]] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint("instrument_id", "timestamp", name="uix_onchain_data"),
        Index("ix_onchain_timestamp", "timestamp"),
    )


# ── Market regime state ───────────────────────────────────────────────────────

class RegimeState(Base):
    __tablename__ = "regime_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.id"), nullable=False, index=True
    )
    detected_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    regime: Mapped[str] = mapped_column(String(32), nullable=False)  # STRONG_TREND_BULL/…
    adx: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    atr_percentile: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    vix: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    ta_weight: Mapped[Decimal] = mapped_column(Numeric(5, 3))
    fa_weight: Mapped[Decimal] = mapped_column(Numeric(5, 3))
    sentiment_weight: Mapped[Decimal] = mapped_column(Numeric(5, 3))
    geo_weight: Mapped[Decimal] = mapped_column(Numeric(5, 3))
    sl_atr_multiplier: Mapped[Decimal] = mapped_column(Numeric(5, 3))

    __table_args__ = (
        Index("ix_regime_instrument_date", "instrument_id", "detected_at"),
    )


# ── Order flow data (TimescaleDB hypertable on timestamp) ─────────────────────

class OrderFlowData(Base):
    __tablename__ = "order_flow_data"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.id"), nullable=False, index=True
    )
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    cvd: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 8))          # Cumulative Volume Delta
    funding_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6)) # 8h funding %
    open_interest: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 4))
    long_liquidations: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 4))
    short_liquidations: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 4))
    buy_volume: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 8))
    sell_volume: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 8))

    __table_args__ = (
        UniqueConstraint("instrument_id", "timestamp", name="uix_order_flow_data"),
        Index("ix_order_flow_timestamp", "timestamp"),
    )


# ── Social sentiment ──────────────────────────────────────────────────────────

class SocialSentiment(Base):
    __tablename__ = "social_sentiment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.id"), nullable=False, index=True
    )
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # reddit/stocktwits/fear_greed/combined
    score: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))  # -100 to +100
    mention_count: Mapped[Optional[int]] = mapped_column(Integer)
    raw_data: Mapped[Optional[str]] = mapped_column(Text)  # JSON

    # Extended fields written by SocialCollector (source="combined")
    fear_greed_index: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    reddit_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    stocktwits_bullish_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    put_call_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))

    __table_args__ = (
        UniqueConstraint("instrument_id", "source", "timestamp", name="uix_social_sentiment"),
        Index("ix_social_sentiment_timestamp", "timestamp"),
    )


# ── Economic calendar events ──────────────────────────────────────────────────

class EconomicEvent(Base):
    __tablename__ = "economic_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_date: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    country: Mapped[str] = mapped_column(String(8), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    event_name: Mapped[str] = mapped_column(String(256), nullable=False)
    impact: Mapped[str] = mapped_column(String(16), default="low")  # low/medium/high
    previous: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    estimate: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    actual: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    unit: Mapped[Optional[str]] = mapped_column(String(32))
    related_instruments: Mapped[Optional[str]] = mapped_column(Text)  # JSON list of symbols
    source: Mapped[str] = mapped_column(String(32), default="FMP")
    collected_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("event_date", "country", "event_name", name="uix_economic_event"),
        Index("ix_economic_events_date", "event_date"),
    )


# ── News ──────────────────────────────────────────────────────────────────────

class NewsEvent(Base):
    __tablename__ = "news_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    headline: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    source: Mapped[Optional[str]] = mapped_column(String(64))
    url: Mapped[Optional[str]] = mapped_column(String(512))
    published_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), index=True
    )
    sentiment_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    importance: Mapped[str] = mapped_column(String(16), default="low")  # low/medium/high/critical
    related_instruments: Mapped[Optional[str]] = mapped_column(Text)  # JSON
    category: Mapped[Optional[str]] = mapped_column(String(64))


# ── Virtual portfolio ─────────────────────────────────────────────────────────

class VirtualPortfolio(Base):
    __tablename__ = "virtual_portfolio"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("signals.id"), unique=True, nullable=False, index=True
    )
    opened_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    closed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True))
    size_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    current_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    unrealized_pnl_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    realized_pnl_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    status: Mapped[str] = mapped_column(String(16), default="open")  # open/closed/partial

    # v2 simulator fields (SIM-01..SIM-07)
    mfe: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    mae: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    breakeven_moved: Mapped[bool] = mapped_column(Boolean, default=False)
    partial_closed: Mapped[bool] = mapped_column(Boolean, default=False)
    trailing_stop: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    current_stop_loss: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    size_remaining_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=Decimal("1.0"))
    partial_close_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    partial_close_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True))
    partial_pnl_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    entry_filled_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True))

    # v3 simulator fields (SIM-12, SIM-13, SIM-16)
    unrealized_pnl_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    accrued_swap_pips: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4), default=Decimal("0"))
    accrued_swap_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4), default=Decimal("0"))
    last_swap_date: Mapped[Optional[datetime.date]] = mapped_column(Date())
    account_balance_at_entry: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))

    # Analysis enrichment fields
    spread_pips_applied: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))   # spread cost recorded at open
    breakeven_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))      # exact price level of BE stop


# ── Virtual account (SIM-16: dynamic balance) ────────────────────────────────

class VirtualAccount(Base):
    __tablename__ = "virtual_account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    initial_balance: Mapped[Decimal] = mapped_column(
        Numeric(14, 4), nullable=False, default=Decimal("1000.0")
    )
    current_balance: Mapped[Decimal] = mapped_column(
        Numeric(14, 4), nullable=False, default=Decimal("1000.0")
    )
    peak_balance: Mapped[Decimal] = mapped_column(
        Numeric(14, 4), nullable=False, default=Decimal("1000.0")
    )
    total_realized_pnl: Mapped[Decimal] = mapped_column(
        Numeric(14, 4), nullable=False, default=Decimal("0.0")
    )
    total_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


# ── Backtesting v4 (SIM-22) ───────────────────────────────────────────────────
# Isolated from live tables: signal_results / virtual_portfolio are NEVER
# written to by the backtest engine.

class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    # UUID stored as VARCHAR(36) for cross-DB compatibility (Text on SQLite, UUID on PG)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    params: Mapped[Optional[str]] = mapped_column(Text)            # JSON: BacktestParams
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True))
    summary: Mapped[Optional[str]] = mapped_column(Text)           # JSON: BacktestResult summary

    trades: Mapped[list["BacktestTrade"]] = relationship(
        "BacktestTrade", back_populates="run", cascade="all, delete-orphan"
    )


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)       # LONG/SHORT
    entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    exit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    exit_reason: Mapped[Optional[str]] = mapped_column(String(32))
    pnl_pips: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    pnl_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    result: Mapped[Optional[str]] = mapped_column(String(16))               # win/loss/breakeven
    composite_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    entry_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True))
    exit_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True))
    duration_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    mfe: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    mae: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))

    run: Mapped["BacktestRun"] = relationship("BacktestRun", back_populates="trades")

    __table_args__ = (
        Index("ix_backtest_trades_run_id", "run_id"),
    )


# ── System event log (3-day retention) ───────────────────────────────────────

class SystemEvent(Base):
    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(8), nullable=False, default="INFO")   # INFO/WARNING/ERROR
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)             # SIGNAL_GENERATED/…
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="")    # module / job name
    symbol: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    timeframe: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)             # JSON

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_system_events_created_at", "created_at"),
        Index("ix_system_events_event_type", "event_type"),
        Index("ix_system_events_level", "level"),
    )
