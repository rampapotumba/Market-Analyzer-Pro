"""SQLAlchemy 2.0 ORM models."""

import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
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


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    market: Mapped[str] = mapped_column(String(16), nullable=False)  # forex/stocks/crypto
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    pip_size: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0.0001"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    price_data: Mapped[list["PriceData"]] = relationship("PriceData", back_populates="instrument")
    signals: Mapped[list["Signal"]] = relationship("Signal", back_populates="instrument")


class PriceData(Base):
    __tablename__ = "price_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.id"), nullable=False, index=True
    )
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)  # M1/M5/M15/H1/H4/D1/W1
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
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # LONG/SHORT/HOLD
    signal_strength: Mapped[str] = mapped_column(String(16), nullable=False)  # STRONG_BUY/BUY/HOLD/SELL/STRONG_SELL
    entry_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    stop_loss: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    take_profit_1: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    take_profit_2: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    risk_reward: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    position_size_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    composite_score: Mapped[float] = mapped_column(Numeric(8, 4), default=0.0)
    ta_score: Mapped[float] = mapped_column(Numeric(8, 4), default=0.0)
    fa_score: Mapped[float] = mapped_column(Numeric(8, 4), default=0.0)
    sentiment_score: Mapped[float] = mapped_column(Numeric(8, 4), default=0.0)
    geo_score: Mapped[float] = mapped_column(Numeric(8, 4), default=0.0)
    confidence: Mapped[float] = mapped_column(Numeric(5, 2), default=0.0)
    horizon: Mapped[Optional[str]] = mapped_column(String(32))
    reasoning: Mapped[Optional[str]] = mapped_column(Text)  # JSON
    indicators_snapshot: Mapped[Optional[str]] = mapped_column(Text)  # JSON
    status: Mapped[str] = mapped_column(String(16), default="created", index=True)
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True))

    instrument: Mapped["Instrument"] = relationship("Instrument", back_populates="signals")
    result: Mapped[Optional["SignalResult"]] = relationship("SignalResult", back_populates="signal", uselist=False)


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
    exit_reason: Mapped[Optional[str]] = mapped_column(String(16))  # sl_hit/tp1_hit/tp2_hit/expired/manual
    pnl_pips: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4))
    pnl_percent: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    result: Mapped[Optional[str]] = mapped_column(String(16))  # win/loss/breakeven
    max_favorable_excursion: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    max_adverse_excursion: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    price_at_expiry: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8))
    duration_minutes: Mapped[Optional[int]] = mapped_column(Integer)

    signal: Mapped["Signal"] = relationship("Signal", back_populates="result")


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


class EconomicEvent(Base):
    """Upcoming economic calendar events from FMP."""

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
