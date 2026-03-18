"""Signal Engine: orchestrates all analysis engines to generate trading signals."""

import datetime
import json
import logging
from decimal import Decimal
from typing import Any, Optional

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from src.analysis.correlation_engine import CorrelationEngine
from src.analysis.crypto_fa_engine import CryptoFAEngine
from src.analysis.fa_engine import FAEngine
from src.analysis.geo_engine_v2 import GeoEngineV2
from src.analysis.llm_engine import LLMEngine
from src.analysis.sentiment_engine_v2 import SentimentEngineV2
from src.analysis.ta_engine import TAEngine
from src.cache import cache
from src.config import settings
from src.database.crud import (
    cancel_open_signals,
    create_signal,
    get_all_instruments,
    get_instrument_by_symbol,
    get_latest_signal_for_instrument,
    get_macro_data,
    get_news_events,
    get_price_data,
    get_upcoming_economic_events,
)
from src.database.models import Instrument, Signal
from src.notifications.telegram import telegram
from src.signals.mtf_filter import MTFFilter
from src.signals.risk_manager import RiskManager
from src.tracker.trade_simulator import open_position_for_signal
from src.utils.event_logger import EventType, log_event_bg

logger = logging.getLogger(__name__)

# LLM call threshold: only invoke Claude if pre-LLM composite score exceeds this
LLM_SCORE_THRESHOLD = 25.0

# In-memory LLM result cache: key = (symbol, timeframe), value = (score, meta, expires_at)
_llm_cache: dict[tuple[str, str], tuple[float, dict, datetime.datetime]] = {}
LLM_CACHE_TTL_MINUTES = 30

# Signal cooldown per timeframe (minutes) — A. Cooldown
SIGNAL_COOLDOWN_MINUTES: dict[str, int] = {
    "M1": 1, "M5": 5, "M15": 15,
    "H1": 60, "H4": 240, "D1": 1440, "W1": 10080, "MN1": 43200,
}

# FIX-02: Adaptive TTL — how many candles a limit-entry signal should stay alive.
# A signal on H1 that hasn't filled in 24 candles is stale; W1 can wait 8 candles (≈8 wks).
_TF_CANDLE_HOURS: dict[str, float] = {
    "M1": 1 / 60, "M5": 5 / 60, "M15": 15 / 60,
    "H1": 1.0, "H4": 4.0, "D1": 24.0, "W1": 168.0, "MN1": 720.0,
}
_TF_EXPIRY_CANDLES: dict[str, int] = {
    "M1": 60, "M5": 48, "M15": 32,
    "H1": 24, "H4": 20, "D1": 10,
    "W1": 8,  "MN1": 3,
}


def _calculate_expiry(timeframe: str) -> datetime.datetime:
    """Return signal expiry timestamp based on timeframe (FIX-02).

    TTL = candle_hours × expiry_candles.  Falls back to settings.SIGNAL_EXPIRY_HOURS
    for unknown timeframes.
    """
    candle_hours = _TF_CANDLE_HOURS.get(timeframe)
    n_candles = _TF_EXPIRY_CANDLES.get(timeframe)
    if candle_hours is None or n_candles is None:
        hours = settings.SIGNAL_EXPIRY_HOURS
    else:
        hours = candle_hours * n_candles
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=hours)

# FIX-07: Session filter for European/NA forex pairs during Asian low-liquidity hours
# JPY, AUD, NZD pairs are excluded — they are actively traded in Asian session.
_FOREX_PAIRS_EU_NA: frozenset[str] = frozenset({
    "EURUSD=X", "GBPUSD=X", "USDCHF=X", "EURGBP=X",
    "EURCAD=X", "GBPCAD=X", "EURCHF=X", "GBPCHF=X",
})
_ASIAN_SESSION_UTC_START = 0   # 00:00 UTC
_ASIAN_SESSION_UTC_END   = 7   # 07:00 UTC (exclusive)


def _is_low_liquidity_session(
    symbol: str,
    market: str,
    now_utc: datetime.datetime,
) -> bool:
    """Return True if this symbol should be skipped due to low-liquidity Asian hours.

    Only blocks European/NA forex pairs during 00:00–06:59 UTC.
    Crypto, stocks, Asian pairs are never blocked.
    """
    if market != "forex":
        return False
    if symbol not in _FOREX_PAIRS_EU_NA:
        return False
    return _ASIAN_SESSION_UTC_START <= now_utc.hour < _ASIAN_SESSION_UTC_END


# Minimum price change as fraction of ATR to trigger new signal — B. Price-change trigger
ATR_PRICE_CHANGE_FACTOR = 0.3

# Redis cache key helpers for cooldown and price-change checks
def _cooldown_redis_key(instrument_id: int, timeframe: str) -> str:
    return f"cooldown:{instrument_id}:{timeframe}"


def _price_redis_key(instrument_id: int, timeframe: str) -> str:
    return f"last_price:{instrument_id}:{timeframe}"


# Fallback in-memory caches used when Redis is unavailable
_cooldown_cache: dict[tuple[int, str], datetime.datetime] = {}
_last_signal_price_cache: dict[tuple[int, str], float] = {}

# Telegram alert cooldown: don't send more than once per 4 hours per instrument
TELEGRAM_COOLDOWN_HOURS = 4
_telegram_sent_cache: dict[int, datetime.datetime] = {}  # instrument_id → last sent


def _can_send_telegram(instrument_id: int) -> bool:
    last = _telegram_sent_cache.get(instrument_id)
    if last is None:
        return True
    return (datetime.datetime.now(datetime.timezone.utc) - last).total_seconds() > TELEGRAM_COOLDOWN_HOURS * 3600


def _mark_telegram_sent(instrument_id: int) -> None:
    _telegram_sent_cache[instrument_id] = datetime.datetime.now(datetime.timezone.utc)


def _get_cached_llm(symbol: str, timeframe: str) -> Optional[tuple[float, dict]]:
    key = (symbol, timeframe)
    if key in _llm_cache:
        score, meta, expires_at = _llm_cache[key]
        if datetime.datetime.now(datetime.timezone.utc) < expires_at:
            return score, meta
        del _llm_cache[key]
    return None


def _set_cached_llm(symbol: str, timeframe: str, score: float, meta: dict) -> None:
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=LLM_CACHE_TTL_MINUTES)
    _llm_cache[(symbol, timeframe)] = (score, meta, expires_at)


def _refine_entry_point(
    direction: str,
    current_price: Decimal,
    ta_indicators: dict,
    atr: Optional[Decimal],
) -> Decimal:
    """
    Refine entry point using Fibonacci, Order Blocks, FVG, and VPOC.
    Returns a potentially better entry price (or current_price if no better level found).

    Strategy:
    - LONG: look for entry at nearest support (Fib 0.618, bull OB top, VPOC, PDL+buffer)
    - SHORT: look for entry at nearest resistance (Fib 0.382, bear OB bottom, VPOC, PDH-buffer)
    - Only use refined entry if it's within 1×ATR of current price (reachable)
    - If no good level found, return current_price (market entry)
    """
    if atr is None or atr == Decimal("0"):
        return current_price

    price = float(current_price)
    atr_f = float(atr)
    candidates = []

    if direction == "LONG":
        # Fibonacci 0.618 retracement (support)
        fib618 = ta_indicators.get("fib_618")
        if fib618 and fib618 < price and price - fib618 <= 2 * atr_f:
            candidates.append(fib618)

        # Bullish Order Block top (entry on retest)
        bull_ob_high = ta_indicators.get("bull_ob_high")
        if bull_ob_high and bull_ob_high < price and price - bull_ob_high <= 2 * atr_f:
            candidates.append(bull_ob_high)

        # VPOC (volume point of control — price magnet)
        vpoc = ta_indicators.get("vpoc")
        if vpoc and vpoc < price and price - vpoc <= atr_f:
            candidates.append(vpoc)

        # PDL + small buffer
        pdl = ta_indicators.get("pdl")
        if pdl and pdl > 0 and pdl < price and price - pdl <= 1.5 * atr_f:
            candidates.append(pdl + atr_f * 0.1)  # slight buffer above PDL

        # Choose the highest candidate (closest to current price from below)
        if candidates:
            best = max(candidates)
            if best < price:  # sanity check
                return Decimal(str(round(best, 8)))

    elif direction == "SHORT":
        # Fibonacci 0.382 retracement (resistance)
        fib382 = ta_indicators.get("fib_382")
        if fib382 and fib382 > price and fib382 - price <= 2 * atr_f:
            candidates.append(fib382)

        # Bearish Order Block bottom (entry on retest)
        bear_ob_low = ta_indicators.get("bear_ob_low")
        if bear_ob_low and bear_ob_low > price and bear_ob_low - price <= 2 * atr_f:
            candidates.append(bear_ob_low)

        # VPOC
        vpoc = ta_indicators.get("vpoc")
        if vpoc and vpoc > price and vpoc - price <= atr_f:
            candidates.append(vpoc)

        # PDH - small buffer
        pdh = ta_indicators.get("pdh")
        if pdh and pdh > 0 and pdh > price and pdh - price <= 1.5 * atr_f:
            candidates.append(pdh - atr_f * 0.1)

        # Choose the lowest candidate (closest to current price from above)
        if candidates:
            best = min(candidates)
            if best > price:
                return Decimal(str(round(best, 8)))

    return current_price


def _determine_direction(score: float) -> str:
    """Determine signal direction from composite score."""
    if score >= settings.STRONG_BUY_THRESHOLD or score >= settings.BUY_THRESHOLD:
        return "LONG"
    elif score <= settings.STRONG_SELL_THRESHOLD or score <= settings.SELL_THRESHOLD:
        return "SHORT"
    else:
        return "HOLD"


def _determine_signal_strength(score: float) -> str:
    """Determine signal strength label from composite score."""
    if score >= settings.STRONG_BUY_THRESHOLD:
        return "STRONG_BUY"
    elif score >= settings.BUY_THRESHOLD:
        return "BUY"
    elif score <= settings.STRONG_SELL_THRESHOLD:
        return "STRONG_SELL"
    elif score <= settings.SELL_THRESHOLD:
        return "SELL"
    else:
        return "HOLD"


def _price_data_to_df(price_records: list) -> Optional[pd.DataFrame]:
    """Convert price_data DB records to OHLCV DataFrame."""
    if not price_records:
        return None

    rows = []
    for p in price_records:
        rows.append({
            "open": float(p.open),
            "high": float(p.high),
            "low": float(p.low),
            "close": float(p.close),
            "volume": float(p.volume),
        })
    idx = [p.timestamp for p in price_records]
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(idx))
    return df


class SignalEngine:
    """
    Main signal generation engine.
    Orchestrates TA, FA, Sentiment, Geo analysis and produces trading signals.
    """

    def __init__(self) -> None:
        self.risk_manager = RiskManager()
        self.mtf_filter = MTFFilter()

    async def generate_signal(
        self,
        instrument: Instrument,
        timeframe: str,
        db: AsyncSession,
        higher_tf_signals: Optional[list[dict[str, Any]]] = None,
    ) -> Optional[Signal]:
        """
        Generate a trading signal for an instrument/timeframe combination.

        Steps:
            1. Fetch price data from DB
            2. Run TAEngine
            3. Run FAEngine (with macro/news data)
            4. Run SentimentEngine
            5. Run GeoEngine
            6. Get timeframe-specific weights
            7. Calculate composite_score
            8. Determine direction and signal_strength
            9. Calculate entry_price, SL, TP via RiskManager
            10. Apply MTF filter
            11. Save to DB and return
        """
        # A. Check signal cooldown before doing any heavy computation
        cooldown_key = (instrument.id, timeframe)
        cooldown_minutes = SIGNAL_COOLDOWN_MINUTES.get(timeframe, 60)
        now = datetime.datetime.now(datetime.timezone.utc)

        # Try Redis first; fall back to in-memory; then seed from DB on startup
        redis_val = await cache.get(_cooldown_redis_key(instrument.id, timeframe))
        last_signal_time: Optional[datetime.datetime] = None
        if redis_val is not None:
            try:
                last_signal_time = datetime.datetime.fromisoformat(redis_val)
            except (ValueError, TypeError):
                pass
        if last_signal_time is None:
            last_signal_time = _cooldown_cache.get(cooldown_key)
        if last_signal_time is None:
            # On startup, fall back to DB to seed the cache
            last_sig = await get_latest_signal_for_instrument(db, instrument.id, timeframe)
            if last_sig:
                last_signal_time = last_sig.created_at
                # SQLite returns naive datetimes — make UTC-aware
                if last_signal_time.tzinfo is None:
                    last_signal_time = last_signal_time.replace(tzinfo=datetime.timezone.utc)
                _cooldown_cache[cooldown_key] = last_signal_time
                await cache.set(
                    _cooldown_redis_key(instrument.id, timeframe),
                    last_signal_time.isoformat(),
                    ttl=cooldown_minutes * 60,
                )

        if last_signal_time:
            elapsed_minutes = (now - last_signal_time).total_seconds() / 60
            if elapsed_minutes < cooldown_minutes:
                logger.debug(
                    f"[SignalEngine] Cooldown for {instrument.symbol}/{timeframe}: "
                    f"{elapsed_minutes:.0f}/{cooldown_minutes} min elapsed — skipping"
                )
                return None

        # FIX-07: Session filter — skip EU/NA forex pairs during Asian low-liquidity hours
        market_type = getattr(instrument, "market_type", "") or ""
        if _is_low_liquidity_session(instrument.symbol, market_type, now):
            logger.info(
                f"[SignalEngine] Asian session filter: skipping {instrument.symbol} "
                f"(EU/NA forex, {now.hour:02d}:{now.minute:02d} UTC)"
            )
            return None

        # 1. Fetch price data
        price_records = await get_price_data(db, instrument.id, timeframe, limit=300)
        if len(price_records) < 30:
            logger.warning(
                f"[SignalEngine] Insufficient data for {instrument.symbol}/{timeframe}: "
                f"{len(price_records)} records (need ≥30)"
            )
            return None

        df = _price_data_to_df(price_records)
        if df is None or df.empty:
            logger.warning(f"[SignalEngine] Empty DataFrame for {instrument.symbol}")
            return None

        # 2. Run TA Engine
        try:
            ta_engine = TAEngine(df)
            ta_score = ta_engine.calculate_ta_score()
            ta_indicators = ta_engine.calculate_all_indicators()
            atr = ta_engine.get_atr(14)
        except Exception as exc:
            logger.error(f"[SignalEngine] TA engine error for {instrument.symbol}: {exc}")
            ta_score = 0.0
            ta_indicators = {}
            atr = None

        # B. Price-change trigger: skip if price hasn't moved enough since last signal
        current_close = float(df["close"].iloc[-1])
        cached_price = await cache.get(_price_redis_key(instrument.id, timeframe))
        last_price: Optional[float] = (
            float(cached_price) if cached_price is not None
            else _last_signal_price_cache.get(cooldown_key)
        )
        if last_price is not None and atr is not None and atr > Decimal("0"):
            price_change = abs(current_close - last_price)
            atr_threshold = float(atr) * ATR_PRICE_CHANGE_FACTOR
            if price_change < atr_threshold:
                logger.debug(
                    f"[SignalEngine] Price change too small for {instrument.symbol}/{timeframe}: "
                    f"{price_change:.6f} < {atr_threshold:.6f} (0.3×ATR) — skipping"
                )
                return None

        # 3. Fetch macro and news data
        macro_records = await get_macro_data(db, limit=200)
        news_records = await get_news_events(db, limit=30)

        # Economic calendar blocking — check for HIGH-impact events in next 4 hours
        calendar_block = False
        try:
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            upcoming = await get_upcoming_economic_events(
                db, from_dt=now_utc, to_dt=now_utc + datetime.timedelta(hours=4)
            )
            high_impact = [e for e in upcoming if getattr(e, "impact", "") == "HIGH"]
            if high_impact:
                calendar_block = True
                logger.info(
                    f"[SignalEngine] Calendar block active for {instrument.symbol}: "
                    f"{len(high_impact)} HIGH-impact events in next 4h"
                )
        except Exception as exc:
            logger.warning(f"[SignalEngine] Calendar check error: {exc}")

        # 4. Run FA Engine (CryptoFAEngine for crypto, FAEngine for forex/stocks)
        try:
            if instrument.market == "crypto":
                crypto_fa = CryptoFAEngine(db)
                crypto_result = await crypto_fa.analyze(instrument.id, instrument.symbol)
                fa_score = crypto_result["score"]
                logger.debug(
                    f"[SignalEngine] CryptoFA for {instrument.symbol}: {fa_score:.1f} "
                    f"(onchain={crypto_result['components'].get('onchain', 0):.1f}, "
                    f"cycle={crypto_result['components'].get('cycle', 0):.1f})"
                )
            else:
                fa_engine = FAEngine(instrument, macro_records, news_records)
                fa_score = fa_engine.calculate_fa_score()
        except Exception as exc:
            logger.error(f"[SignalEngine] FA engine error: {exc}")
            fa_score = 0.0

        # 5. Run Sentiment Engine V2 (FinBERT + multi-source, TextBlob fallback)
        try:
            sent_engine = SentimentEngineV2(news_events=news_records)
            sentiment_score = await sent_engine.calculate()
            logger.debug(
                f"[SignalEngine] SentimentV2 for {instrument.symbol}: {sentiment_score:.1f} "
                f"(sources: {sent_engine.get_summary()})"
            )
        except Exception as exc:
            logger.error(f"[SignalEngine] Sentiment V2 engine error: {exc}")
            sentiment_score = 0.0

        # 6. Run Geo Engine V2 (GDELT real-time geopolitical risk)
        try:
            geo_engine = GeoEngineV2()
            geo_score = await geo_engine.score(instrument.symbol)
            await geo_engine.close()
            logger.debug(f"[SignalEngine] GeoV2 for {instrument.symbol}: {geo_score:.1f}")
        except Exception as exc:
            logger.error(f"[SignalEngine] Geo V2 engine error: {exc}")
            geo_score = 0.0

        # Run Correlation Engine (DXY, VIX, TNX)
        try:
            corr_engine = CorrelationEngine(instrument, macro_records)
            correlation_score = corr_engine.calculate_correlation_score()
        except Exception as exc:
            logger.error(f"[SignalEngine] Correlation engine error: {exc}")
            correlation_score = 0.0

        # 7. Run LLM Engine (Claude API analysis)
        # Pre-check: calculate preliminary score without LLM to avoid wasting API calls
        weights = self.mtf_filter.get_timeframe_weights(timeframe)
        pre_score = abs(
            weights["ta"] * ta_score
            + weights["fa"] * fa_score
            + weights["sentiment"] * sentiment_score
            + weights["geo"] * geo_score
        )

        llm_score, llm_meta = 0.0, {}
        cached = _get_cached_llm(instrument.symbol, timeframe)
        if cached:
            llm_score, llm_meta = cached
            logger.debug(f"[SignalEngine] LLM cache hit for {instrument.symbol}/{timeframe}")
        elif pre_score >= LLM_SCORE_THRESHOLD:
            llm_engine = LLMEngine()
            try:
                llm_score, llm_meta = await llm_engine.calculate_llm_score(
                    instrument, timeframe,
                    ta_score, fa_score, sentiment_score, geo_score,
                    ta_indicators, macro_records, news_records,
                )
                _set_cached_llm(instrument.symbol, timeframe, llm_score, llm_meta)
            except Exception as exc:
                logger.warning(f"[SignalEngine] LLM engine error: {exc}")
        else:
            logger.debug(
                f"[SignalEngine] Skipping LLM for {instrument.symbol}/{timeframe} "
                f"(pre_score={pre_score:.1f} < {LLM_SCORE_THRESHOLD})"
            )

        # 8. Calculate composite score
        composite_score = (
            weights["ta"] * ta_score
            + weights["fa"] * fa_score
            + weights["sentiment"] * sentiment_score
            + weights["geo"] * geo_score
        )
        # Add correlation score as additive factor (5%)
        composite_score += 0.05 * correlation_score
        composite_score = max(-100.0, min(100.0, composite_score))

        # LLM as verifier: confirms or contradicts the composite direction
        if llm_score != 0.0:
            if llm_score * composite_score < 0:
                # LLM disagrees — reduce confidence
                composite_score *= 0.7
                logger.debug(
                    f"[SignalEngine] LLM contradicts signal for {instrument.symbol}/{timeframe} "
                    f"(llm={llm_score:+.1f}, composite={composite_score:+.1f}) — penalty ×0.7"
                )
            else:
                # LLM agrees — boost confidence, capped at 100
                composite_score = max(-100.0, min(100.0, composite_score * 1.1))
                logger.debug(
                    f"[SignalEngine] LLM confirms signal for {instrument.symbol}/{timeframe} "
                    f"(llm={llm_score:+.1f}) — boost ×1.1"
                )

        # Apply calendar block: reduce composite score by 30% but don't skip signal
        if calendar_block:
            composite_score *= 0.7

        # 9. Apply MTF filter
        if higher_tf_signals:
            composite_score = self.mtf_filter.apply(
                composite_score, timeframe, higher_tf_signals
            )

        # 10. TA quality gate — TA must meaningfully confirm the signal direction
        # Required: TA contribution ≥ 3.0 points AND TA must agree in direction
        ta_contribution = weights["ta"] * ta_score
        ta_disagrees = (composite_score > 0 and ta_score < 0) or (composite_score < 0 and ta_score > 0)
        if abs(ta_contribution) < 3.0 or ta_disagrees:
            logger.debug(
                f"[SignalEngine] TA gate failed for {instrument.symbol}/{timeframe}: "
                f"ta_score={ta_score:.1f}, ta_contribution={ta_contribution:.2f} "
                f"(need ≥3.0), ta_disagrees={ta_disagrees} — HOLD"
            )
            return None

        # 11. Determine direction and strength
        direction = _determine_direction(composite_score)
        signal_strength = _determine_signal_strength(composite_score)

        # 11. Calculate entry, SL, TP
        current_price = Decimal(str(ta_indicators.get("current_price", df["close"].iloc[-1])))
        entry_price = _refine_entry_point(direction, current_price, ta_indicators, atr)
        initial_status = "tracking" if entry_price == current_price else "created"

        levels = {"stop_loss": None, "take_profit_1": None, "take_profit_2": None}
        risk_reward = None
        position_size_pct = None

        if direction != "HOLD" and atr is not None and atr > Decimal("0"):
            levels = self.risk_manager.calculate_levels(entry_price, atr, direction)
            if levels["stop_loss"] and levels["take_profit_1"]:
                risk_reward = self.risk_manager.calculate_risk_reward(
                    entry_price, levels["stop_loss"], levels["take_profit_1"]
                )
                sl_distance = abs(entry_price - levels["stop_loss"])
                if sl_distance > Decimal("0"):
                    position_size_pct = self.risk_manager.calculate_position_size(
                        Decimal("10000"),  # Default $10k account
                        settings.MAX_RISK_PER_TRADE_PCT,
                        sl_distance,
                        entry_price,
                    )

        # 12. Calculate confidence (after entry refinement)
        # Normalize relative to threshold bands so strong signals start at ≥60%
        # and regular BUY/SELL start at 30%, scaling up within each band.
        score_abs = abs(composite_score)
        strong_thresh = abs(settings.STRONG_BUY_THRESHOLD)  # 15.0
        buy_thresh = abs(settings.BUY_THRESHOLD)  # 7.0
        if signal_strength in ("STRONG_BUY", "STRONG_SELL"):
            # 60% at threshold, +2% per unit above it, capped at 100%
            excess = score_abs - strong_thresh
            confidence = min(100.0, 60.0 + excess * 2.0)
        elif signal_strength in ("BUY", "SELL"):
            # 30–60% interpolated across the BUY band (7 → 15)
            band_progress = (score_abs - buy_thresh) / max(strong_thresh - buy_thresh, 1.0)
            confidence = 30.0 + band_progress * 30.0
        else:
            confidence = 0.0

        # 13. Build reasoning
        reasoning = {
            "ta_score": round(ta_score, 2),
            "fa_score": round(fa_score, 2),
            "sentiment_score": round(sentiment_score, 2),
            "geo_score": round(geo_score, 2),
            "correlation_score": round(correlation_score, 2),
            "llm_score": round(llm_score, 2),
            "llm_bias": llm_meta.get("bias", "N/A"),
            "llm_confidence": llm_meta.get("confidence", 0.0),
            "llm_reasoning": llm_meta.get("reasoning", ""),
            "weights": weights,
            "composite_raw": round(composite_score, 2),
            "calendar_block": calendar_block,
            "factors": [],
        }

        # Add key factors
        if abs(ta_score) > 20:
            reasoning["factors"].append(
                f"TA: {'Strong bullish' if ta_score > 0 else 'Strong bearish'} signal ({ta_score:.1f})"
            )
        if abs(fa_score) > 20:
            reasoning["factors"].append(
                f"FA: {'Positive' if fa_score > 0 else 'Negative'} fundamentals ({fa_score:.1f})"
            )
        if abs(sentiment_score) > 20:
            reasoning["factors"].append(
                f"Sentiment: {'Bullish' if sentiment_score > 0 else 'Bearish'} news ({sentiment_score:.1f})"
            )
        if llm_meta.get("key_factors"):
            reasoning["factors"].extend(
                [f"Claude: {f}" for f in llm_meta["key_factors"][:3]]
            )

        # 14. Do not save HOLD signals — no trading action
        if direction == "HOLD":
            logger.debug(
                f"[SignalEngine] HOLD signal for {instrument.symbol}/{timeframe} "
                f"(score={composite_score:.1f}) — not saved"
            )
            return None

        # 14a. Minimum confidence gate
        if confidence < settings.MIN_CONFIDENCE:
            logger.debug(
                f"[SignalEngine] Low confidence for {instrument.symbol}/{timeframe}: "
                f"{confidence:.1f}% < {settings.MIN_CONFIDENCE}% — skipping"
            )
            return None

        # 14b. Timeframe-specific composite minimum
        tf_min = settings.TF_MIN_COMPOSITE.get(timeframe, settings.BUY_THRESHOLD)
        if abs(composite_score) < tf_min:
            logger.debug(
                f"[SignalEngine] Composite too weak for {instrument.symbol}/{timeframe}: "
                f"|{composite_score:.1f}| < {tf_min} — skipping"
            )
            return None

        # 14c. H1 signals only for crypto and forex
        market_type = getattr(instrument, "market_type", "") or ""
        if timeframe == "H1" and market_type not in settings.H1_ALLOWED_MARKETS:
            logger.debug(
                f"[SignalEngine] H1 signal skipped for {instrument.symbol} "
                f"(market={market_type}, H1 only for crypto/forex)"
            )
            return None

        # 15. Cancel previous open signals for this instrument+timeframe
        cancelled = await cancel_open_signals(db, instrument.id, timeframe)
        if cancelled:
            logger.debug(f"[SignalEngine] Cancelled {cancelled} old signal(s) for {instrument.symbol}/{timeframe}")

        # 16. initial_status is set above during entry refinement:
        # entry = current_price → "tracking" (market order executed immediately)
        # entry ≠ current_price → "created" (waiting for limit entry to be reached)

        horizon = self.mtf_filter.get_horizon(timeframe)
        expires_at = _calculate_expiry(timeframe)  # FIX-02: adaptive TTL per timeframe

        signal_data = {
            "instrument_id": instrument.id,
            "timeframe": timeframe,
            "direction": direction,
            "signal_strength": signal_strength,
            "entry_price": entry_price,
            "stop_loss": levels.get("stop_loss"),
            "take_profit_1": levels.get("take_profit_1"),
            "take_profit_2": levels.get("take_profit_2"),
            "risk_reward": risk_reward,
            "position_size_pct": position_size_pct,
            "composite_score": Decimal(str(round(composite_score, 4))),
            "ta_score": Decimal(str(round(ta_score, 4))),
            "fa_score": Decimal(str(round(fa_score, 4))),
            "sentiment_score": Decimal(str(round(sentiment_score, 4))),
            "geo_score": Decimal(str(round(geo_score, 4))),
            "confidence": round(confidence, 2),
            "horizon": horizon,
            "llm_score": round(llm_score, 4) if llm_score != 0.0 else None,
            "llm_bias": llm_meta.get("bias") or None,
            "llm_confidence": round(llm_meta.get("confidence", 0.0), 2) if llm_meta else None,
            "reasoning": json.dumps(reasoning),
            "indicators_snapshot": json.dumps({
                k: round(float(v), 6) if isinstance(v, (float, Decimal)) else v
                for k, v in ta_indicators.items()
                if v is not None
            }),
            "status": initial_status,
            "expires_at": expires_at,
        }

        async with db.begin_nested():
            signal = await create_signal(db, signal_data)

        # Commit so the signal is persisted before Telegram alert fires
        await db.commit()

        # Open virtual position immediately (market order simulation)
        try:
            await open_position_for_signal(signal, db)
            await db.commit()
        except Exception as exc:
            logger.warning(f"[SignalEngine] Failed to open simulator position: {exc}")

        # Update Redis + in-memory caches so subsequent checks work immediately
        _cooldown_cache[cooldown_key] = now
        _last_signal_price_cache[cooldown_key] = current_close
        await cache.set(
            _cooldown_redis_key(instrument.id, timeframe),
            now.isoformat(),
            ttl=cooldown_minutes * 60,
        )
        await cache.set(
            _price_redis_key(instrument.id, timeframe),
            current_close,
            ttl=cooldown_minutes * 60,
        )

        logger.info(
            f"[SignalEngine] Generated signal for {instrument.symbol}/{timeframe}: "
            f"{direction} (score={composite_score:.2f}, strength={signal_strength})"
        )

        log_event_bg(
            EventType.SIGNAL_GENERATED,
            f"{direction} {instrument.symbol}/{timeframe} | score={composite_score:.1f} | {signal_strength}",
            source="signal_engine",
            symbol=instrument.symbol,
            timeframe=timeframe,
            details={
                "signal_id": signal.id,
                "direction": direction,
                "signal_strength": signal_strength,
                "composite_score": round(composite_score, 2),
                "ta_score": round(ta_score, 2),
                "fa_score": round(fa_score, 2),
                "sentiment_score": round(sentiment_score, 2),
                "geo_score": round(geo_score, 2),
            },
        )

        # Send Telegram alert: only STRONG_BUY/STRONG_SELL, max once per 4h per instrument
        is_strong = signal_strength in ("STRONG_BUY", "STRONG_SELL")
        if is_strong and _can_send_telegram(instrument.id):
            try:
                await telegram.send_signal_alert(signal, instrument.symbol, instrument.name)
                _mark_telegram_sent(instrument.id)
            except Exception as exc:
                logger.warning(f"[SignalEngine] Telegram alert failed: {exc}")
        else:
            logger.debug(
                f"[SignalEngine] Telegram skipped for {instrument.symbol}/{timeframe} "
                f"(strength={signal_strength}, strong={is_strong}, "
                f"cooldown={'active' if not _can_send_telegram(instrument.id) else 'ok'})"
            )

        return signal

    async def generate_signal_by_symbol(
        self,
        symbol: str,
        timeframe: str,
        db: AsyncSession,
    ) -> Optional[Signal]:
        """Convenience method to generate signal by symbol string."""
        instrument = await get_instrument_by_symbol(db, symbol)
        if not instrument:
            logger.error(f"[SignalEngine] Instrument not found: {symbol}")
            return None
        return await self.generate_signal(instrument, timeframe, db)
