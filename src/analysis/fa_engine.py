"""Fundamental Analysis Engine."""

import logging
from decimal import Decimal
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Maps forex pair symbol → (base_bank, quote_bank).
# base_bank rate minus quote_bank rate gives the interest rate differential.
# Positive differential → base currency has higher rates → bearish for pair
# (e.g. FED > ECB means USD stronger than EUR → EURUSD bearish).
_PAIR_BANK_MAP: dict[str, tuple[str, str]] = {
    "EURUSD=X": ("FED", "ECB"),
    "GBPUSD=X": ("FED", "BOE"),
    "USDJPY=X": ("FED", "BOJ"),
    "AUDUSD=X": ("FED", "RBA"),
    "USDCAD=X": ("FED", "BOC"),
    "USDCHF=X": ("FED", "SNB"),
    "NZDUSD=X": ("FED", "RBNZ"),
}

# Multiplier to scale rate differential percentage points into score units.
# 1 pp differential → 10 score points.
_RATE_DIFF_SCORE_MULTIPLIER = 10.0


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
        central_bank_rates: Optional[dict[str, float]] = None,
    ) -> None:
        self.instrument = instrument
        self.news_data = news_data
        self.central_bank_rates: dict[str, float] = central_bank_rates or {}
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

    def _analyze_rate_differential(self) -> float:
        """Compute interest rate differential score for forex pairs (TASK-V7-03).

        Looks up the two central banks for the instrument symbol in _PAIR_BANK_MAP,
        calculates differential = base_rate - quote_rate, and scales by
        _RATE_DIFF_SCORE_MULTIPLIER.

        Semantics for pairs where USD is the quote (EURUSD, GBPUSD, AUDUSD, NZDUSD):
          FED rate > foreign rate → USD stronger → pair is bearish → negative score
          FED rate < foreign rate → USD weaker  → pair is bullish → positive score

        Semantics for pairs where USD is the base (USDJPY, USDCAD, USDCHF):
          FED rate > foreign rate → USD stronger → pair is bullish → positive score
          (same formula: base_rate - quote_rate, which here equals FED - foreign)

        Returns 0.0 if:
          - instrument symbol not in _PAIR_BANK_MAP
          - central_bank_rates is empty or missing one of the two banks
        """
        symbol = self.instrument.symbol if hasattr(self.instrument, "symbol") else ""
        banks = _PAIR_BANK_MAP.get(symbol)
        if banks is None:
            return 0.0

        base_bank, quote_bank = banks
        base_rate = self.central_bank_rates.get(base_bank)
        quote_rate = self.central_bank_rates.get(quote_bank)

        if base_rate is None or quote_rate is None:
            logger.warning(
                "[V7-03] Rate differential unavailable for %s: "
                "missing rate for %s or %s",
                symbol,
                base_bank,
                quote_bank,
            )
            return 0.0

        differential = base_rate - quote_rate

        # For pairs where USD is the quote (EUR/USD, GBP/USD, AUD/USD, NZD/USD):
        # higher FED rate → stronger USD → pair goes DOWN → bearish → negate the score
        _usd_as_quote = {"EURUSD=X", "GBPUSD=X", "AUDUSD=X", "NZDUSD=X"}
        if symbol in _usd_as_quote:
            score = -differential * _RATE_DIFF_SCORE_MULTIPLIER
        else:
            # USD is base (USD/JPY, USD/CAD, USD/CHF):
            # higher FED rate → stronger USD → pair goes UP → bullish → positive score
            score = differential * _RATE_DIFF_SCORE_MULTIPLIER

        logger.debug(
            "[V7-03] Rate differential for %s: %s=%.2f %s=%.2f diff=%.2f score=%.1f",
            symbol, base_bank, base_rate, quote_bank, quote_rate, differential, score,
        )
        return float(score)

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
            rate_diff_score = self._analyze_rate_differential()
            news_adj = self._news_sentiment_adjustment()
            # TASK-V7-03: rate differential is the dominant fundamental driver for forex
            final_score = base_score * 0.6 + rate_diff_score * 0.3 + news_adj * 0.1
        elif market == "stocks":
            base_score = self._analyze_stock_fundamentals()
            news_adj = self._news_sentiment_adjustment()
            final_score = base_score * 0.8 + news_adj * 0.2
        elif market == "crypto":
            base_score = self._analyze_crypto_fundamentals()
            news_adj = self._news_sentiment_adjustment()
            final_score = base_score * 0.8 + news_adj * 0.2
        else:
            logger.warning("[SIM-17] fa_score returned fallback 0.0: unknown market type %r", market)
            base_score = 0.0
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
