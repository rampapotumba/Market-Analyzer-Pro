"""Signal Engine v2 — Regime-aware composite signal generation.

Improvements over v1:
  - Regime-adaptive weights (from RegimeDetector)
  - Order-flow modifier for crypto (TAEngineV2 OF score)
  - Earnings discount for stocks (EarningsCollector)
  - Confidence v2: weight coverage × score magnitude × regime alignment
  - Portfolio heat check before emitting
  - Optional LLM validation gate (Claude)
"""

import datetime
import json
import logging
from decimal import Decimal
from typing import Any, Optional

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from src.analysis.llm_validator import llm_validator
from src.analysis.regime_detector import (
    RegimeDetector,
    _REGIME_WEIGHTS,
)
from src.cache import cache
from src.config import settings
from src.database.crud import (
    create_signal,
    create_virtual_position,
    get_all_instruments,
    get_instrument_by_symbol,
    get_latest_signal_for_instrument,
    get_news_events_for_instrument,
    get_price_data,
)
from src.database.engine import async_session_factory
from src.database.models import Instrument, Signal
from src.signals.portfolio_risk import OpenPosition, PortfolioRiskManager
from src.signals.risk_manager_v2 import RiskManagerV2

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
# v3 calibrated to real composite range ≈ ±25 (was ±60/±30 in legacy comments)
_STRONG_BUY = settings.STRONG_BUY_THRESHOLD   # 15.0
_BUY = settings.BUY_THRESHOLD                  # 7.0
_SELL = settings.SELL_THRESHOLD                 # -7.0
_STRONG_SELL = settings.STRONG_SELL_THRESHOLD   # -15.0
_MIN_CONFIDENCE = settings.MIN_CONFIDENCE        # 10.0

# Earnings discount: composite × (1 - discount)
_EARNINGS_DISCOUNT_FACTOR = 0.7  # -30% if in discount window

# Order-flow weight for crypto (additional modifier)
_OF_WEIGHT = 0.15  # 15% blend from OF score when available

# Cooldown per timeframe (minutes)
_COOLDOWN: dict[str, int] = {
    "M1": 1, "M5": 5, "M15": 15,
    "H1": 60, "H4": 240, "D1": 1440,
}

# Default weights (fallback if regime unknown)
_DEFAULT_WEIGHTS = {
    "ta": float(settings.DEFAULT_TA_WEIGHT),
    "fa": float(settings.DEFAULT_FA_WEIGHT),
    "sentiment": float(settings.DEFAULT_SENTIMENT_WEIGHT),
    "geo": float(settings.DEFAULT_GEO_WEIGHT),
}


class SignalEngineV2:
    """
    Orchestrates v2 analysis pipeline into regime-aware signals.

    Usage:
        engine = SignalEngineV2()
        signal = await engine.generate(
            symbol="BTC/USDT",
            timeframe="H4",
            ta_score=65.0,
            fa_score=40.0,
            sentiment_score=30.0,
            geo_score=-10.0,
            regime="STRONG_TREND_BULL",
            market_type="crypto",
            entry_price=Decimal("50000"),
            atr=Decimal("1200"),
            of_score=55.0,           # for crypto
            earnings_days=None,      # for stocks
        )
    """

    def __init__(self, portfolio: Optional[PortfolioRiskManager] = None) -> None:
        self._rm = RiskManagerV2()
        self._portfolio = portfolio or PortfolioRiskManager()

    # ── Main entry point ──────────────────────────────────────────────────────

    async def generate(
        self,
        symbol: str,
        timeframe: str,
        ta_score: float,
        fa_score: float,
        sentiment_score: float,
        geo_score: float,
        regime: str,
        market_type: str,  # forex / crypto / stocks
        entry_price: Decimal,
        atr: Decimal,
        of_score: Optional[float] = None,
        earnings_days: Optional[int] = None,
        support_levels: Optional[list[Decimal]] = None,
        resistance_levels: Optional[list[Decimal]] = None,
        risk_pct: float = float(settings.MAX_RISK_PER_TRADE_PCT),
        account: Decimal = Decimal(str(settings.SIGNAL_ACCOUNT_SIZE_USD)),
        db: Optional[AsyncSession] = None,
        instrument_id: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        """
        Compute composite score, apply modifiers, validate constraints,
        and return a signal dict (or None if filters reject it).
        """
        # ── 1. Regime-adaptive weights ────────────────────────────────────────
        weights = _REGIME_WEIGHTS.get(regime, _DEFAULT_WEIGHTS)

        # ── 2. Base composite ─────────────────────────────────────────────────
        composite = (
            weights["ta"] * ta_score
            + weights["fa"] * fa_score
            + weights["sentiment"] * sentiment_score
            + weights["geo"] * geo_score
        )

        # ── 3. Order-flow modifier (crypto) ───────────────────────────────────
        if market_type == "crypto" and of_score is not None:
            composite = composite * (1 - _OF_WEIGHT) + of_score * _OF_WEIGHT

        of_score_saved = of_score

        # ── 4. Earnings discount (stocks) ─────────────────────────────────────
        if market_type == "stocks" and earnings_days is not None:
            skip_days = settings.EARNINGS_SKIP_DAYS
            disc_days = settings.EARNINGS_DISCOUNT_DAYS
            if earnings_days <= skip_days:
                logger.info("%s: skipping — earnings in %d days", symbol, earnings_days)
                return None
            if earnings_days <= disc_days:
                composite *= _EARNINGS_DISCOUNT_FACTOR

        # ── 5. Confidence ─────────────────────────────────────────────────────
        confidence = self._calculate_confidence(
            composite=composite,
            ta_score=ta_score,
            fa_score=fa_score,
            sentiment_score=sentiment_score,
            regime=regime,
        )

        # ── 6. Composite threshold ────────────────────────────────────────────
        if abs(composite) < abs(_BUY):
            logger.debug(
                "%s: composite %.1f below threshold, skipping", symbol, composite
            )
            return None

        if confidence < _MIN_CONFIDENCE:
            logger.debug(
                "%s: confidence %.1f below minimum, skipping", symbol, confidence
            )
            return None

        # ── 7. Direction and strength ─────────────────────────────────────────
        direction, strength = self._classify(composite)
        if direction == "HOLD":
            return None

        # ── 7.5. Cooldown check with direction-reversal bypass (v3: 3.4.1) ───────
        if db is not None and instrument_id is not None:
            is_blocked = await self._check_cooldown(
                db=db,
                instrument_id=instrument_id,
                timeframe=timeframe,
                current_direction=direction,
            )
            if is_blocked:
                return None

        # ── 8. Portfolio heat check ───────────────────────────────────────────
        allowed, reason = self._portfolio.can_open(symbol, market_type, risk_pct)
        if not allowed:
            logger.info("%s: portfolio gate — %s", symbol, reason)
            return None

        # ── 9. Risk levels — v3: regime-adaptive R:R based calculation ──────────
        levels = self._rm.calculate_levels_for_regime(
            entry=entry_price,
            atr=atr,
            direction=direction,
            regime=regime,
            support_levels=support_levels,
            resistance_levels=resistance_levels,
        )

        sl = levels["stop_loss"]
        tp1 = levels["take_profit_1"]
        if sl is None or tp1 is None:
            return None

        valid, val_reason = self._rm.validate(entry_price, sl, tp1, direction, regime)
        if not valid:
            logger.info("%s: risk validation failed — %s", symbol, val_reason)
            return None

        sl_dist = abs(entry_price - sl)
        position_pct = self._rm.calculate_position_size(
            account=account,
            risk_pct=risk_pct,
            sl_distance=sl_dist,
            entry_price=entry_price,
        )

        # ── 10. Build signal dict ─────────────────────────────────────────────
        signal: dict[str, Any] = {
            "direction": direction,
            "signal_strength": strength,
            "composite_score": round(composite, 4),
            "ta_score": round(ta_score, 4),
            "fa_score": round(fa_score, 4),
            "sentiment_score": round(sentiment_score, 4),
            "geo_score": round(geo_score, 4),
            "of_score": round(of_score_saved, 4) if of_score_saved is not None else None,
            "confidence": round(confidence, 2),
            "regime": regime,
            "entry_price": entry_price,
            "stop_loss": sl,
            "take_profit_1": tp1,
            "take_profit_2": levels["take_profit_2"],
            "take_profit_3": levels["take_profit_3"],
            "risk_reward": levels["risk_reward_1"],
            "position_size_pct": position_pct,
            "earnings_days_ahead": earnings_days,
            "portfolio_heat": round(self._portfolio.portfolio_heat() + risk_pct, 2),
        }

        # ── 10.5. LLM validation gate (optional) ─────────────────────────────
        # v3: only call LLM when composite is strong enough (≥ LLM_SCORE_THRESHOLD).
        # Previously threshold was 25.0 (hardcoded) = above real max composite ≈ 24.75,
        # so LLM was never called. Now defaults to 10.0.
        if abs(composite) < settings.LLM_SCORE_THRESHOLD:
            return signal
        validation = await llm_validator.validate(symbol, timeframe, signal)
        if not validation["approved"]:
            logger.info(
                "%s: LLM rejected signal — %s", symbol, validation.get("reason", "")
            )
            return None
        if validation["decision"] == "reduce" and validation.get("adjusted_confidence"):
            signal["confidence"] = min(signal["confidence"], validation["adjusted_confidence"])
            signal["llm_note"] = validation.get("reason", "")
            logger.info(
                "%s: LLM reduced confidence to %.1f — %s",
                symbol, signal["confidence"], validation.get("reason", ""),
            )

        return signal

    # ── Cooldown (v3: 3.4.1) ─────────────────────────────────────────────────

    async def _check_cooldown(
        self,
        db: AsyncSession,
        instrument_id: int,
        timeframe: str,
        current_direction: str,
    ) -> bool:
        """Check signal cooldown with direction-reversal bypass.

        Returns True if signal should be BLOCKED (cooldown active, same direction).
        Returns False if signal is allowed (outside cooldown, or direction reversed).

        v3 logic:
          - If last signal is within cooldown window:
              - Same direction → block (return True)
              - Reversed direction → bypass cooldown (return False) + log
          - If outside cooldown → allow (return False)
        """
        import datetime as _dt  # noqa: PLC0415
        cooldown_minutes = _COOLDOWN.get(timeframe, 60)
        cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=cooldown_minutes)

        last = await get_latest_signal_for_instrument(
            db, instrument_id=instrument_id, timeframe=timeframe
        )
        if last is None:
            return False  # no previous signal → no cooldown

        signal_time = last.created_at
        if signal_time.tzinfo is None:
            signal_time = signal_time.replace(tzinfo=_dt.timezone.utc)

        if signal_time < cutoff:
            return False  # outside cooldown window → allow

        # Within cooldown: check direction
        last_direction = last.direction  # "LONG" or "SHORT"
        if last_direction == current_direction:
            logger.debug(
                "[Cooldown] %s/%s: blocked — same direction %s within %dm",
                instrument_id, timeframe, current_direction, cooldown_minutes,
            )
            return True  # block

        # Direction reversed → bypass
        logger.info(
            "[Cooldown] %s/%s: bypassed — direction reversal %s → %s",
            instrument_id, timeframe, last_direction, current_direction,
        )
        return False

    # ── FA routing (v3) ──────────────────────────────────────────────────────

    async def resolve_fa_score(
        self,
        symbol: str,
        market: str,
        db: AsyncSession,
        instrument_id: Optional[int] = None,
    ) -> float:
        """Route FA computation to the appropriate engine for the market type.

        v3 fix: previously legacy FAEngine was used for all instruments,
        giving irrelevant US-macro scores for EUR/USD, BTC, etc.

        Args:
            symbol:        e.g. "EUR/USD", "AAPL", "BTC/USDT"
            market:        "forex" | "stocks" | "crypto" | "commodities"
            db:            async DB session
            instrument_id: required for stocks/crypto FA engines

        Returns:
            FA score in [-100, +100]
        """
        try:
            if market == "forex":
                from src.analysis.forex_fa_engine import ForexFAEngine  # noqa: PLC0415
                engine = ForexFAEngine(db)
                result = await engine.analyze(symbol)
                score = float(result.get("score", 0.0))
                # ForexFAEngine already returns [-100, +100]
                return score

            elif market == "stocks":
                if instrument_id is None:
                    logger.warning("resolve_fa_score: instrument_id required for stocks, returning 0")
                    return 0.0
                from src.analysis.stock_fa_engine import StockFAEngine  # noqa: PLC0415
                engine = StockFAEngine(db)
                result = await engine.calculate_stock_fa_score(instrument_id)
                return float(result.get("score", 0.0))

            elif market == "crypto":
                if instrument_id is None:
                    logger.warning("resolve_fa_score: instrument_id required for crypto, returning 0")
                    return 0.0
                from src.analysis.crypto_fa_engine import CryptoFAEngine  # noqa: PLC0415
                engine = CryptoFAEngine(db)
                result = await engine.analyze(instrument_id, symbol)
                return float(result.get("score", 0.0))

            else:
                # Commodities / unknown → legacy FAEngine fallback
                from src.analysis.fa_engine import FAEngine  # noqa: PLC0415
                engine = FAEngine(db)
                result = await engine.analyze(symbol)
                return float(result.get("score", 0.0))

        except Exception as exc:
            logger.warning("resolve_fa_score failed for %s (%s): %s", symbol, market, exc)
            return 0.0

    # ── News fetching (v3) ────────────────────────────────────────────────────

    async def fetch_news_for_instrument(
        self,
        db: AsyncSession,
        symbol: str,
        market: str,
        limit: int = 30,
        hours_back: int = 24,
    ) -> list:
        """Fetch news events filtered by instrument.

        v3 fix: previously get_news_events(db, limit=30) returned generic
        last-30 news regardless of instrument, causing cross-instrument noise.
        Now filters by instrument keywords.
        """
        return await get_news_events_for_instrument(
            db, symbol=symbol, market=market, limit=limit, hours_back=hours_back
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _classify(self, composite: float) -> tuple[str, str]:
        """Map composite score to (direction, strength)."""
        if composite >= _STRONG_BUY:
            return "LONG", "STRONG_BUY"
        elif composite >= _BUY:
            return "LONG", "BUY"
        elif composite <= _STRONG_SELL:
            return "SHORT", "STRONG_SELL"
        elif composite <= _SELL:
            return "SHORT", "SELL"
        else:
            return "HOLD", "NEUTRAL"

    def _calculate_confidence(
        self,
        composite: float,
        ta_score: float,
        fa_score: float,
        sentiment_score: float,
        regime: str,
    ) -> float:
        """
        Confidence v2: weighted combination of:
          - Score magnitude (how far from 0): 40%
          - Signal alignment (do TA, FA, sentiment agree?): 40%
          - Regime clarity (is it a strong/clear regime?): 20%
        Range: [0, 100]
        """
        # Score magnitude: |composite| / 100 → [0, 1]
        magnitude = min(abs(composite) / 100.0, 1.0)

        # Agreement: count how many sources agree with composite sign
        sources = [ta_score, fa_score, sentiment_score]
        if composite > 0:
            agreeing = sum(1 for s in sources if s > 0)
        elif composite < 0:
            agreeing = sum(1 for s in sources if s < 0)
        else:
            agreeing = 0
        alignment = agreeing / len(sources)

        # Regime clarity
        regime_clarity = {
            "STRONG_TREND_BULL": 1.0,
            "STRONG_TREND_BEAR": 1.0,
            "WEAK_TREND_BULL":   0.7,
            "WEAK_TREND_BEAR":   0.7,
            "RANGING":           0.5,
            "HIGH_VOLATILITY":   0.4,
            "LOW_VOLATILITY":    0.6,
        }.get(regime, 0.5)

        confidence = (
            0.40 * magnitude * 100.0
            + 0.40 * alignment * 100.0
            + 0.20 * regime_clarity * 100.0
        )
        return min(round(confidence, 2), 100.0)
