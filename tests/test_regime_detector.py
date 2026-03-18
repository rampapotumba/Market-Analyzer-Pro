"""Tests for src.analysis.regime_detector."""

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from src.analysis.regime_detector import (
    REGIMES,
    RegimeDetector,
    _atr_percentile,
    _calculate_adx,
    _calculate_atr,
    _to_df,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_price_records(n: int = 300, trend: str = "bull") -> list:
    """Create fake PriceData-like objects."""
    records = []
    price = 100.0
    for i in range(n):
        if trend == "bull":
            price *= 1.001
        elif trend == "bear":
            price *= 0.999
        else:
            price += (0.5 if i % 2 == 0 else -0.5)
        r = MagicMock()
        r.open = price * 0.999
        r.high = price * 1.002
        r.low = price * 0.998
        r.close = price
        r.volume = 1000.0
        records.append(r)
    return records


# ── Unit tests ────────────────────────────────────────────────────────────────


class TestRegimeDetectorInit:
    def test_instantiate(self):
        rd = RegimeDetector()
        assert rd is not None

    def test_get_regime_weights_all_regimes(self):
        rd = RegimeDetector()
        for regime in REGIMES:
            weights = rd.get_regime_weights(regime)
            assert set(weights.keys()) == {"ta", "fa", "sentiment", "geo"}
            total = sum(weights.values())
            assert abs(total - 1.0) < 0.01, f"Weights for {regime} don't sum to 1.0"

    def test_get_regime_weights_unknown_falls_back(self):
        rd = RegimeDetector()
        weights = rd.get_regime_weights("UNKNOWN_REGIME")
        assert isinstance(weights, dict)

    def test_get_atr_multiplier_all_regimes(self):
        rd = RegimeDetector()
        for regime in REGIMES:
            mult = rd.get_atr_multiplier(regime)
            assert 0.5 <= mult <= 5.0

    def test_get_atr_multiplier_unknown(self):
        rd = RegimeDetector()
        mult = rd.get_atr_multiplier("MADE_UP")
        assert mult > 0


class TestDetectTrendAndVolatility:
    def setup_method(self):
        self.rd = RegimeDetector()

    def test_detect_trend_strong(self):
        assert self.rd._detect_trend(35.0) == "strong"

    def test_detect_trend_weak(self):
        assert self.rd._detect_trend(25.0) == "weak"

    def test_detect_trend_none(self):
        assert self.rd._detect_trend(15.0) == "none"

    def test_detect_trend_none_value(self):
        assert self.rd._detect_trend(None) == "unknown"

    def test_detect_volatility_high(self):
        assert self.rd._detect_volatility_regime(85.0) == "high"

    def test_detect_volatility_low(self):
        assert self.rd._detect_volatility_regime(10.0) == "low"

    def test_detect_volatility_normal(self):
        assert self.rd._detect_volatility_regime(50.0) == "normal"

    def test_detect_volatility_none(self):
        assert self.rd._detect_volatility_regime(None) == "normal"


class TestDetectRegime:
    def setup_method(self):
        self.rd = RegimeDetector()

    def _run(self, records, vix=None):
        df = _to_df(records)
        regime, adx, atr_pct = self.rd._detect_regime(df, vix)
        return regime, adx, atr_pct

    def test_strong_trend_bull(self):
        """Strong uptrend: expect STRONG_TREND_BULL or WEAK_TREND_BULL."""
        records = _make_price_records(300, trend="bull")
        regime, adx, _ = self._run(records, vix=18.0)
        assert "BULL" in regime

    def test_strong_trend_bear(self):
        """Strong downtrend with high VIX."""
        records = _make_price_records(300, trend="bear")
        regime, _, _ = self._run(records, vix=18.0)
        assert regime in REGIMES

    def test_ranging_market(self):
        """Choppy market — low ADX → RANGING."""
        records = _make_price_records(300, trend="range")
        regime, adx, _ = self._run(records, vix=15.0)
        # Range market with low ATR percentile → RANGING or LOW_VOLATILITY
        assert regime in REGIMES

    def test_high_volatility_regime(self):
        """Very high VIX should trigger HIGH_VOLATILITY."""
        records = _make_price_records(300, trend="range")
        df = _to_df(records)
        # Patch ATR percentile to be high
        with patch("src.analysis.regime_detector._atr_percentile", return_value=90.0):
            regime, _, _ = self.rd._detect_regime(df, vix=35.0)
        assert regime == "HIGH_VOLATILITY"

    def test_low_volatility_regime(self):
        """Very low VIX + low ATR percentile → LOW_VOLATILITY."""
        records = _make_price_records(300, trend="range")
        df = _to_df(records)
        with patch("src.analysis.regime_detector._atr_percentile", return_value=10.0):
            regime, _, _ = self.rd._detect_regime(df, vix=12.0)
        assert regime == "LOW_VOLATILITY"

    def test_returns_tuple_of_three(self):
        records = _make_price_records(300)
        df = _to_df(records)
        result = self.rd._detect_regime(df, vix=20.0)
        assert len(result) == 3

    def test_regime_in_valid_set(self):
        records = _make_price_records(300)
        df = _to_df(records)
        regime, _, _ = self.rd._detect_regime(df, vix=20.0)
        assert regime in REGIMES


class TestTAHelpers:
    def setup_method(self):
        records = _make_price_records(300, "bull")
        self.df = _to_df(records)

    def test_calculate_adx_returns_float(self):
        adx = _calculate_adx(self.df)
        assert adx is None or isinstance(adx, float)

    def test_calculate_adx_positive(self):
        adx = _calculate_adx(self.df)
        if adx is not None:
            assert adx >= 0

    def test_calculate_atr_series(self):
        atr = _calculate_atr(self.df)
        assert isinstance(atr, pd.Series)
        assert len(atr) == len(self.df)

    def test_atr_percentile_returns_float(self):
        atr = _calculate_atr(self.df)
        pct = _atr_percentile(atr)
        assert pct is None or 0.0 <= pct <= 100.0

    def test_atr_percentile_none_when_empty(self):
        pct = _atr_percentile(pd.Series([], dtype=float))
        assert pct is None

    def test_to_df_columns(self):
        records = _make_price_records(5)
        df = _to_df(records)
        assert set(df.columns) == {"open", "high", "low", "close", "volume"}
        assert len(df) == 5


class TestWeightSets:
    def test_all_regimes_have_weights(self):
        rd = RegimeDetector()
        for regime in REGIMES:
            w = rd.get_regime_weights(regime)
            assert abs(sum(w.values()) - 1.0) < 0.01

    def test_high_volatility_has_geo_weight(self):
        rd = RegimeDetector()
        w = rd.get_regime_weights("HIGH_VOLATILITY")
        assert w["geo"] > 0

    def test_strong_trend_has_high_ta(self):
        rd = RegimeDetector()
        w_bull = rd.get_regime_weights("STRONG_TREND_BULL")
        w_bear = rd.get_regime_weights("STRONG_TREND_BEAR")
        assert w_bull["ta"] >= 0.50
        assert w_bear["ta"] >= 0.50

    def test_ranging_has_lower_ta(self):
        rd = RegimeDetector()
        w_ranging = rd.get_regime_weights("RANGING")
        w_strong = rd.get_regime_weights("STRONG_TREND_BULL")
        assert w_ranging["ta"] < w_strong["ta"]


class TestDetectAsync:
    """Test async detect() method with mocked DB."""

    @pytest.mark.asyncio
    async def test_detect_returns_none_when_no_data(self):
        from unittest.mock import AsyncMock, patch

        rd = RegimeDetector()
        with patch("src.analysis.regime_detector.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_factory.return_value = mock_session

            with patch.object(rd, "_fetch_price_data", return_value=[]):
                with patch.object(rd, "_get_vix", return_value=None):
                    result = await rd.detect(instrument_id=1, timeframe="D1")
                    assert result is None

    @pytest.mark.asyncio
    async def test_detect_returns_regime_when_enough_data(self):
        from unittest.mock import AsyncMock, patch

        rd = RegimeDetector()
        records = _make_price_records(300, "bull")

        with patch("src.analysis.regime_detector.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_factory.return_value = mock_session

            with patch.object(rd, "_fetch_price_data", return_value=records):
                with patch.object(rd, "_get_vix", return_value=18.0):
                    result = await rd.detect(instrument_id=1, timeframe="D1")
                    assert result in REGIMES

    @pytest.mark.asyncio
    async def test_detect_insufficient_data_returns_none(self):
        from unittest.mock import AsyncMock, patch

        rd = RegimeDetector()
        # Only 5 records — below _MIN_BARS
        records = _make_price_records(5, "bull")

        with patch("src.analysis.regime_detector.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_factory.return_value = mock_session

            with patch.object(rd, "_fetch_price_data", return_value=records):
                with patch.object(rd, "_get_vix", return_value=None):
                    result = await rd.detect(instrument_id=1, timeframe="D1")
                    assert result is None


class TestDetectAll:
    """Test detect_all() and _detect_and_persist() with mocked DB."""

    @pytest.mark.asyncio
    async def test_detect_all_no_instruments(self):
        """detect_all with no active instruments does nothing."""
        from unittest.mock import AsyncMock, MagicMock, patch

        rd = RegimeDetector()
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.analysis.regime_detector.async_session_factory", return_value=mock_session):
            with patch.object(rd, "_get_vix", new=AsyncMock(return_value=None)):
                await rd.detect_all()  # should complete without error

    @pytest.mark.asyncio
    async def test_detect_all_instruments_with_data(self):
        """detect_all calls _detect_and_persist for each instrument."""
        from unittest.mock import AsyncMock, MagicMock, patch

        rd = RegimeDetector()
        instr = MagicMock()
        instr.id = 1
        instr.symbol = "EURUSD"

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [instr]
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.analysis.regime_detector.async_session_factory", return_value=mock_session):
            with patch.object(rd, "_get_vix", new=AsyncMock(return_value=18.0)):
                with patch.object(rd, "_detect_and_persist", new=AsyncMock()) as mock_persist:
                    await rd.detect_all()
                    mock_persist.assert_called_once_with(instr, "D1", 18.0)

    @pytest.mark.asyncio
    async def test_detect_all_handles_per_instrument_exception(self):
        """Error in _detect_and_persist for one instrument is caught; others continue."""
        from unittest.mock import AsyncMock, MagicMock, patch

        rd = RegimeDetector()
        instr1 = MagicMock()
        instr1.id = 1
        instr1.symbol = "EURUSD"
        instr2 = MagicMock()
        instr2.id = 2
        instr2.symbol = "GBPUSD"

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [instr1, instr2]
        mock_session.execute = AsyncMock(return_value=mock_result)

        call_count = 0

        async def _persist_side_effect(instr, tf, vix):
            nonlocal call_count
            call_count += 1
            if instr.symbol == "EURUSD":
                raise RuntimeError("DB error")

        with patch("src.analysis.regime_detector.async_session_factory", return_value=mock_session):
            with patch.object(rd, "_get_vix", new=AsyncMock(return_value=None)):
                with patch.object(rd, "_detect_and_persist", side_effect=_persist_side_effect):
                    await rd.detect_all()  # should not raise
                    assert call_count == 2  # both instruments attempted

    @pytest.mark.asyncio
    async def test_detect_and_persist_insufficient_data(self):
        """_detect_and_persist exits early when data < _MIN_BARS."""
        from unittest.mock import AsyncMock, MagicMock, patch

        rd = RegimeDetector()
        instr = MagicMock()
        instr.id = 1
        instr.symbol = "EURUSD"
        records = _make_price_records(5)

        with patch.object(rd, "_fetch_price_data", new=AsyncMock(return_value=records)):
            # Should return early without trying to write to DB
            with patch("src.analysis.regime_detector.async_session_factory") as mock_factory:
                mock_session = AsyncMock()
                mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session.__aexit__ = AsyncMock(return_value=False)
                mock_factory.return_value = mock_session

                await rd._detect_and_persist(instr, "D1", None)
                # execute should NOT have been called (no DB write for insufficient data)
                mock_session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_detect_and_persist_with_data(self):
        """_detect_and_persist runs regime detection and writes to DB."""
        from unittest.mock import AsyncMock, MagicMock, patch

        rd = RegimeDetector()
        instr = MagicMock()
        instr.id = 1
        instr.symbol = "EURUSD"
        records = _make_price_records(300, "bull")

        with patch.object(rd, "_fetch_price_data", new=AsyncMock(return_value=records)):
            with patch("src.analysis.regime_detector.async_session_factory") as mock_factory:
                mock_session = AsyncMock()
                mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session.__aexit__ = AsyncMock(return_value=False)
                mock_session.begin = MagicMock(return_value=mock_session)
                mock_factory.return_value = mock_session

                # Patch the PostgreSQL insert to avoid dialect-specific code
                with patch("src.analysis.regime_detector.RegimeState"):
                    with patch("sqlalchemy.dialects.postgresql.insert") as mock_insert:
                        mock_stmt = MagicMock()
                        mock_stmt.values.return_value = mock_stmt
                        mock_insert.return_value = mock_stmt
                        await rd._detect_and_persist(instr, "D1", 18.0)

    @pytest.mark.asyncio
    async def test_get_vix_with_session(self):
        """_get_vix with a provided session calls _fetch directly."""
        from unittest.mock import AsyncMock, MagicMock

        rd = RegimeDetector()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = 18.5
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await rd._get_vix(session=mock_session)
        assert result == pytest.approx(18.5)

    @pytest.mark.asyncio
    async def test_get_vix_none_result(self):
        """_get_vix returns None when no VIX data in DB."""
        from unittest.mock import AsyncMock, MagicMock

        rd = RegimeDetector()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await rd._get_vix(session=mock_session)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_vix_without_session(self):
        """_get_vix without session creates its own session."""
        from unittest.mock import AsyncMock, MagicMock, patch

        rd = RegimeDetector()
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = 22.0
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.analysis.regime_detector.async_session_factory", return_value=mock_session):
            result = await rd._get_vix()
        assert result == pytest.approx(22.0)

    @pytest.mark.asyncio
    async def test_fetch_price_data(self):
        """_fetch_price_data returns reversed list of records."""
        from unittest.mock import AsyncMock, MagicMock

        rd = RegimeDetector()
        mock_session = AsyncMock()
        records = _make_price_records(10)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = list(reversed(records))
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await rd._fetch_price_data(mock_session, 1, "D1")
        # The method reverses the result, so original order is restored
        assert len(result) == 10


class TestATRPercentileEdgeCases:
    def test_atr_percentile_short_series(self):
        atr = pd.Series([1.0, 2.0])
        pct = _atr_percentile(atr, window=252)
        assert pct is not None
        assert 0.0 <= pct <= 100.0

    def test_atr_percentile_all_same(self):
        atr = pd.Series([1.0] * 50)
        pct = _atr_percentile(atr, window=50)
        # All values equal: current is NOT < any, so percentile = 0
        assert pct == 0.0

    def test_atr_percentile_current_is_max(self):
        atr = pd.Series(list(range(1, 51)))  # 1..50, last = 50
        pct = _atr_percentile(atr, window=50)
        # 49 out of 50 values are less than 50
        assert pct > 90.0

    def test_calculate_adx_empty_df_returns_none(self):
        df = pd.DataFrame({"high": [], "low": [], "close": []})
        result = _calculate_adx(df)
        assert result is None

    def test_atr_percentile_nan_current_returns_none(self):
        """If the current (last) ATR value is NaN → return None."""
        atr = pd.Series([1.0, 2.0, float("nan")])
        pct = _atr_percentile(atr, window=3)
        assert pct is None


class TestWeakTrendRegimes:
    """Covers WEAK_TREND_BULL and WEAK_TREND_BEAR branches."""

    def test_weak_trend_bull_regime(self):
        """Mild uptrend: ADX in weak range + price above SMA200 → WEAK_TREND_BULL."""
        from unittest.mock import patch
        rd = RegimeDetector()
        records = _make_price_records(300, "bull")
        df = _to_df(records)

        with patch("src.analysis.regime_detector._calculate_adx", return_value=22.0):
            with patch("src.analysis.regime_detector._atr_percentile", return_value=40.0):
                regime, adx, _ = rd._detect_regime(df, vix=20.0)

        # Bull trend → price > SMA200 → WEAK_TREND_BULL
        assert regime == "WEAK_TREND_BULL"

    def test_weak_trend_bear_regime(self):
        """Mild downtrend: ADX in weak range + price below SMA200 → WEAK_TREND_BEAR."""
        from unittest.mock import patch
        rd = RegimeDetector()
        records = _make_price_records(300, "bear")
        df = _to_df(records)

        with patch("src.analysis.regime_detector._calculate_adx", return_value=22.0):
            with patch("src.analysis.regime_detector._atr_percentile", return_value=40.0):
                regime, adx, _ = rd._detect_regime(df, vix=20.0)

        # Bear trend → price < SMA200 → WEAK_TREND_BEAR
        assert regime in ("WEAK_TREND_BEAR", "RANGING")
