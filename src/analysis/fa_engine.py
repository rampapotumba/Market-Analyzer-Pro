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
        self.macro_data = macro_data
        self.news_data = news_data

    def _analyze_forex_fundamentals(self) -> float:
        """Analyze fundamentals for forex instruments."""
        score = 0.0
        count = 0

        for item in self.macro_data:
            ind_name = item.indicator_name if hasattr(item, 'indicator_name') else item.get('indicator_name', '')
            value = float(item.value) if hasattr(item, 'value') and item.value is not None else None
            prev = float(item.previous_value) if hasattr(item, 'previous_value') and item.previous_value is not None else None

            if value is None:
                continue

            if ind_name == "FEDFUNDS":
                # Higher rates = stronger USD = bearish for EUR/USD
                if prev is not None:
                    delta = value - prev
                    score -= delta * 10  # Rate hike → USD stronger → bearish EUR/USD
                count += 1
            elif ind_name == "CPIAUCSL":
                # Higher CPI may signal rate hikes ahead
                if prev is not None and prev > 0:
                    pct_change = (value - prev) / prev * 100
                    score -= pct_change * 5
                count += 1
            elif ind_name == "UNRATE":
                # Lower unemployment = stronger economy = stronger USD = bearish for EUR/USD
                if prev is not None:
                    delta = value - prev
                    score += delta * 15  # Unemployment drop (delta<0) → USD stronger → negative score
                count += 1
            elif ind_name == "GDPC1":
                # Higher GDP = stronger economy
                if prev is not None and prev > 0:
                    pct_change = (value - prev) / prev * 100
                    score -= pct_change * 8  # Stronger GDP = USD stronger
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

        for item in self.macro_data:
            ind_name = item.indicator_name if hasattr(item, 'indicator_name') else item.get('indicator_name', '')
            value = float(item.value) if hasattr(item, 'value') and item.value is not None else None
            prev = float(item.previous_value) if hasattr(item, 'previous_value') and item.previous_value is not None else None

            if value is None:
                continue

            if ind_name == "GDPC1":
                if prev is not None and prev > 0:
                    pct_change = (value - prev) / prev * 100
                    score += pct_change * 10  # Better GDP = positive for stocks
                count += 1
            elif ind_name == "UNRATE":
                if prev is not None:
                    delta = value - prev
                    score -= delta * 20  # Lower unemployment = bullish stocks
                count += 1
            elif ind_name == "FEDFUNDS":
                if prev is not None:
                    delta = value - prev
                    score -= delta * 15  # Rate hikes = bearish stocks
                count += 1
            elif ind_name == "CPIAUCSL":
                if prev is not None and prev > 0:
                    pct_change = (value - prev) / prev * 100
                    if pct_change > 0.5:
                        score -= pct_change * 5  # High inflation = bearish
                    count += 1

        return max(-100.0, min(100.0, score / max(count, 1)))

    def _analyze_crypto_fundamentals(self) -> float:
        """Crypto has minimal fundamental analysis in Phase 1."""
        # In Phase 2, integrate on-chain metrics, dominance, etc.
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
            base_score = 0.0

        # Apply news adjustment (small weight)
        news_adj = self._news_sentiment_adjustment()
        final_score = base_score * 0.8 + news_adj * 0.2

        return max(-100.0, min(100.0, final_score))
