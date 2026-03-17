"""Correlation engine: market context from DXY, VIX, TNX."""

import logging
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)

# Forex pairs where USD is the quote currency (DXY up → bearish for pair)
USD_QUOTE_PAIRS = {"EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "EURUSD=X", "GBPUSD=X", "AUDUSD=X", "NZDUSD=X"}

# Forex pairs where USD is the base currency (DXY up → bullish for pair)
USD_BASE_PAIRS = {"USDJPY", "USDCHF", "USDCAD", "USDJPY=X", "USDCHF=X", "USDCAD=X"}

# Crypto instruments
CRYPTO_KEYWORDS = {"BTC", "ETH", "SOL", "USDT", "CRYPTO"}


def _extract_macro_value(macro_records: Sequence, indicator_name: str) -> Optional[float]:
    """Extract the most recent value of a given indicator from macro_records."""
    for record in macro_records:
        if getattr(record, "indicator_name", "") == indicator_name:
            val = getattr(record, "value", None)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
    return None


class CorrelationEngine:
    """
    Calculates a correlation/macro-context score for an instrument
    based on DXY, VIX, TNX, and Binance funding rates stored in macro_records.
    """

    def __init__(self, instrument: Any, macro_records: Sequence) -> None:
        """
        Args:
            instrument: ORM Instrument object with .symbol and .market attributes.
            macro_records: Sequence of MacroData ORM records.
        """
        self.instrument = instrument
        self.macro_records = macro_records

        self.dxy = _extract_macro_value(macro_records, "DXY")
        self.vix = _extract_macro_value(macro_records, "VIX")
        self.tnx = _extract_macro_value(macro_records, "TNX")
        self.funding_btc = _extract_macro_value(macro_records, "FUNDING_RATE_BTC")
        self.funding_eth = _extract_macro_value(macro_records, "FUNDING_RATE_ETH")
        self.funding_sol = _extract_macro_value(macro_records, "FUNDING_RATE_SOL")

    def _get_market(self) -> str:
        """Return the market type for this instrument."""
        return getattr(self.instrument, "market", "unknown").lower()

    def _get_symbol(self) -> str:
        """Return the instrument symbol."""
        return getattr(self.instrument, "symbol", "").upper()

    def _is_forex(self) -> bool:
        return self._get_market() == "forex"

    def _is_stock(self) -> bool:
        return self._get_market() in ("stocks", "stock", "equities")

    def _is_crypto(self) -> bool:
        symbol = self._get_symbol()
        market = self._get_market()
        return market == "crypto" or any(k in symbol for k in CRYPTO_KEYWORDS)

    def _vix_modifier(self, base_score: float) -> float:
        """Apply VIX-based risk modifier to a base score."""
        if self.vix is None:
            return base_score

        if self.vix > 35:
            # Extreme fear: reduce magnitude by 40%
            return base_score * 0.60
        elif self.vix > 25:
            # Elevated risk: reduce magnitude by 20%
            return base_score * 0.80
        return base_score

    def calculate_correlation_score(self) -> float:
        """
        Calculate the correlation score for the instrument.
        Returns float in [-100, +100].
        """
        score = 0.0
        symbol = self._get_symbol()

        if self._is_forex():
            score = self._score_forex(symbol)
        elif self._is_stock():
            score = self._score_stock()
        elif self._is_crypto():
            score = self._score_crypto(symbol)
        else:
            # Unknown market: use generic VIX-based modifier only
            logger.debug(
                f"[CorrelationEngine] Unknown market type for {symbol}, returning 0"
            )
            return 0.0

        # Clamp to [-100, +100]
        score = max(-100.0, min(100.0, score))
        logger.debug(f"[CorrelationEngine] {symbol} correlation score: {score:.2f}")
        return score

    def _score_forex(self, symbol: str) -> float:
        """Calculate correlation score for a forex instrument."""
        score = 0.0

        # DXY impact
        if self.dxy is not None:
            # We compute relative strength based on DXY level
            # DXY > 104: strong USD (score +/- 40 depending on pair direction)
            # DXY between 96-104: moderate
            # DXY < 96: weak USD
            if self.dxy > 104:
                dxy_strength = 40.0
            elif self.dxy > 100:
                dxy_strength = 20.0
            elif self.dxy > 96:
                dxy_strength = 0.0
            else:
                dxy_strength = -30.0  # weak dollar

            if symbol in USD_QUOTE_PAIRS:
                # EURUSD etc: DXY up → pair falls → bearish
                score -= dxy_strength
            elif symbol in USD_BASE_PAIRS:
                # USDJPY etc: DXY up → pair rises → bullish
                score += dxy_strength
            else:
                # Cross pair or unknown: DXY has less direct impact
                pass

        # VIX modifier (risk-off reduces magnitude)
        score = self._vix_modifier(score)

        return score

    def _score_stock(self) -> float:
        """Calculate correlation score for a stock/equity instrument."""
        score = 0.0

        # VIX > 20: risk-off → bearish for stocks
        if self.vix is not None:
            if self.vix > 30:
                score -= 50.0
            elif self.vix > 25:
                score -= 35.0
            elif self.vix > 20:
                score -= 20.0
            elif self.vix < 15:
                # Low VIX: risk-on → mild bullish
                score += 10.0

        # TNX (10-Year Treasury Yield): rising rates → bearish for stocks
        if self.tnx is not None:
            if self.tnx > 5.0:
                score -= 40.0
            elif self.tnx > 4.5:
                score -= 25.0
            elif self.tnx > 4.0:
                score -= 10.0
            elif self.tnx < 3.0:
                # Low rates: supportive for stocks
                score += 15.0

        return score

    def _score_crypto(self, symbol: str) -> float:
        """Calculate correlation score for a crypto instrument."""
        score = 0.0

        # VIX > 25: risk-off → bearish for crypto
        if self.vix is not None:
            if self.vix > 35:
                score -= 50.0
            elif self.vix > 25:
                score -= 30.0
            elif self.vix < 15:
                # Risk-on: mild bullish
                score += 10.0

        # Funding rates: high positive funding → overbought → bearish signal
        # Determine which funding rate to use based on symbol
        funding_rate = None
        if "BTC" in symbol:
            funding_rate = self.funding_btc
        elif "ETH" in symbol:
            funding_rate = self.funding_eth
        elif "SOL" in symbol:
            funding_rate = self.funding_sol
        else:
            # Use BTC funding as proxy for overall crypto sentiment
            funding_rate = self.funding_btc

        if funding_rate is not None:
            # Funding rate in decimal form (e.g., 0.0005 = 0.05%)
            funding_pct = funding_rate * 100.0  # convert to percent

            if funding_pct > 0.1:
                # Very high positive funding: extremely overbought → strong bearish
                score -= 40.0
            elif funding_pct > 0.05:
                # High positive funding: overbought → bearish
                score -= 25.0
            elif funding_pct > 0.01:
                # Moderately positive funding: slight bearish
                score -= 10.0
            elif funding_pct < -0.05:
                # Negative funding: shorts dominate → contrarian bullish
                score += 25.0
            elif funding_pct < -0.01:
                # Mildly negative: slight bullish
                score += 10.0

        return score
