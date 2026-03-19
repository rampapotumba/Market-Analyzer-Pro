"""Fundamental Analysis Engine."""

import logging
from decimal import Decimal
from typing import Any, Optional

logger = logging.getLogger(__name__)


class FAEngine:
    """
    Fundamental Analysis Engine.
    Analyzes macro data and fundamentals to generate FA score.
    """

    FOREX_INDICATORS = ["FEDFUNDS", "CPIAUCSL", "UNRATE", "GDPC1"]
    STOCK_INDICATORS = ["GDPC1", "UNRATE", "CPIAUCSL"]
    CRYPTO_INDICATORS = []  # Crypto relies more on sentiment

    def __init__(
        self,
        instrument: Any,
        macro_data: list[Any],
        news_data: list[Any],
    ) -> None:
        self.instrument = instrument
        self.news_data = news_data
        # Build latest + previous maps from sorted records (desc by release_date)
        # so the FAEngine can compute deltas even when previous_value is NULL in DB
        self.macro_data = macro_data
        self._latest: dict[str, float] = {}
        self._previous: dict[str, float] = {}
        seen: dict[str, int] = {}  # indicator → how many times we've seen it
        for item in macro_data:
            name = item.indicator_name if hasattr(item, "indicator_name") else item.get("indicator_name", "")
            val = item.value if hasattr(item, "value") else item.get("value")
            if val is None:
                continue
            val_f = float(val)
            count = seen.get(name, 0)
            if count == 0:
                self._latest[name] = val_f
            elif count == 1:
                self._previous[name] = val_f
            seen[name] = count + 1

    def _delta(self, indicator: str) -> Optional[float]:
        """Return (latest - previous) for an indicator, or None if insufficient data."""
        val = self._latest.get(indicator)
        prev = self._previous.get(indicator)
        if val is None or prev is None:
            return None
        return val - prev

    def _pct_change(self, indicator: str) -> Optional[float]:
        """Return % change for an indicator, or None if insufficient data."""
        val = self._latest.get(indicator)
        prev = self._previous.get(indicator)
        if val is None or prev is None or prev == 0:
            return None
        return (val - prev) / prev * 100

    def _analyze_forex_fundamentals(self) -> float:
        """Analyze fundamentals for forex instruments."""
        score = 0.0
        count = 0

        if (d := self._delta("FEDFUNDS")) is not None:
            score -= d * 10  # Rate hike → USD stronger → bearish EUR/USD
            count += 1
        elif "FEDFUNDS" in self._latest:
            count += 1  # Have data but no prev — neutral contribution

        if (p := self._pct_change("CPIAUCSL")) is not None:
            score -= p * 5  # Higher CPI may signal rate hikes → USD stronger
            count += 1
        elif "CPIAUCSL" in self._latest:
            count += 1

        if (d := self._delta("UNRATE")) is not None:
            score += d * 15  # Unemployment drop (delta<0) → USD stronger → negative score
            count += 1
        elif "UNRATE" in self._latest:
            count += 1

        if (p := self._pct_change("GDPC1")) is not None:
            score -= p * 8  # Stronger GDP = USD stronger
            count += 1
        elif "GDPC1" in self._latest:
            count += 1

        # Symbol-specific adjustments
        symbol = self.instrument.symbol if hasattr(self.instrument, 'symbol') else ""
        if "JPY" in symbol:
            score = -score * 0.5  # Reverse for JPY pairs
        elif "GBP" in symbol:
            score = score * 0.8
        elif "AUD" in symbol:
            score = score * 0.9

        return max(-100.0, min(100.0, score / max(count, 1)))

    def _analyze_stock_fundamentals(self) -> float:
        """Analyze fundamentals for stock instruments."""
        score = 0.0
        count = 0

        if (p := self._pct_change("GDPC1")) is not None:
            score += p * 10  # Better GDP = positive for stocks
            count += 1
        elif "GDPC1" in self._latest:
            count += 1

        if (d := self._delta("UNRATE")) is not None:
            score -= d * 20  # Lower unemployment = bullish stocks
            count += 1
        elif "UNRATE" in self._latest:
            count += 1

        if (d := self._delta("FEDFUNDS")) is not None:
            score -= d * 15  # Rate hikes = bearish stocks
            count += 1
        elif "FEDFUNDS" in self._latest:
            count += 1

        if (p := self._pct_change("CPIAUCSL")) is not None:
            if p > 0.5:
                score -= p * 5  # High inflation = bearish
            count += 1
        elif "CPIAUCSL" in self._latest:
            count += 1

        return max(-100.0, min(100.0, score / max(count, 1)))

    def _analyze_crypto_fundamentals(self) -> float:
        """Crypto has minimal fundamental analysis in Phase 1."""
        logger.warning("[SIM-17] fa_score returned fallback 0.0: crypto FA not yet implemented")
        return 0.0

    def _news_sentiment_adjustment(self) -> float:
        """Additional adjustment from news sentiment."""
        if not self.news_data:
            return 0.0

        recent_news = self.news_data[:10]  # Last 10 news items
        sentiment_sum = 0.0
        count = 0

        for news in recent_news:
            score = news.sentiment_score if hasattr(news, 'sentiment_score') else news.get('sentiment_score')
            if score is not None:
                sentiment_sum += float(score)
                count += 1

        if count == 0:
            return 0.0

        avg_sentiment = sentiment_sum / count
        return avg_sentiment * 20  # Scale to [-20, 20]

    def calculate_fa_score(self) -> float:
        """
        Calculate Fundamental Analysis score.
        Returns float in [-100, +100].
        """
        market = self.instrument.market if hasattr(self.instrument, 'market') else "stocks"

        if market == "forex":
            base_score = self._analyze_forex_fundamentals()
        elif market == "stocks":
            base_score = self._analyze_stock_fundamentals()
        elif market == "crypto":
            base_score = self._analyze_crypto_fundamentals()
        else:
            logger.warning("[SIM-17] fa_score returned fallback 0.0: unknown market type %r", market)
            base_score = 0.0

        # Apply news adjustment (small weight)
        news_adj = self._news_sentiment_adjustment()
        final_score = base_score * 0.8 + news_adj * 0.2

        # SIM-41: COT data adjustment (forex only — COT non-commercials net positions)
        try:
            from src.collectors.cot_collector import get_cot_fa_adjustment
            symbol = self.instrument.symbol if hasattr(self.instrument, "symbol") else ""
            cot_indicator = f"COT_NET_{symbol}"
            cot_values = [
                item for item in self.macro_data
                if (
                    item.indicator_name if hasattr(item, "indicator_name")
                    else item.get("indicator_name", "")
                ) == cot_indicator
            ]
            if len(cot_values) >= 2:
                latest_val = cot_values[0].value if hasattr(cot_values[0], "value") else cot_values[0].get("value", 0)
                prev_val = cot_values[1].value if hasattr(cot_values[1], "value") else cot_values[1].get("value", 0)
                latest = float(latest_val)
                previous = float(prev_val)
                change = latest - previous
                cot_adj = get_cot_fa_adjustment(latest, change)
                if cot_adj != 0:
                    final_score += cot_adj
                    logger.debug("[SIM-41] COT adjustment: %+.0f for %s", cot_adj, symbol)
        except Exception as exc:
            logger.debug("[SIM-41] COT error: %s", exc)

        return max(-100.0, min(100.0, final_score))
