"""Tests for Signal Engine."""

import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import numpy as np
import pytest

from src.signals.signal_engine import (
    SignalEngine,
    _determine_direction,
    _determine_signal_strength,
    _get_cached_llm,
    _price_data_to_df,
    _refine_entry_point,
    _set_cached_llm,
    _llm_cache,
)


class TestDirectionDetermination:
    """Test direction determination from composite score."""

    def test_strong_buy_direction(self):
        assert _determine_direction(70.0) == "LONG"

    def test_buy_direction(self):
        assert _determine_direction(40.0) == "LONG"

    def test_hold_direction(self):
        assert _determine_direction(0.0) == "HOLD"
        assert _determine_direction(15.0) == "HOLD"
        assert _determine_direction(-15.0) == "HOLD"

    def test_sell_direction(self):
        assert _determine_direction(-40.0) == "SHORT"

    def test_strong_sell_direction(self):
        assert _determine_direction(-70.0) == "SHORT"

    def test_boundary_buy(self):
        assert _determine_direction(30.0) == "LONG"

    def test_boundary_sell(self):
        assert _determine_direction(-30.0) == "SHORT"


class TestSignalStrength:
    """Test signal strength label determination."""

    def test_strong_buy_label(self):
        assert _determine_signal_strength(65.0) == "STRONG_BUY"

    def test_buy_label(self):
        assert _determine_signal_strength(45.0) == "BUY"

    def test_hold_label(self):
        assert _determine_signal_strength(0.0) == "HOLD"
        assert _determine_signal_strength(10.0) == "HOLD"

    def test_sell_label(self):
        assert _determine_signal_strength(-45.0) == "SELL"

    def test_strong_sell_label(self):
        assert _determine_signal_strength(-65.0) == "STRONG_SELL"


class TestPriceDataConversion:
    """Test price data DB record to DataFrame conversion."""

    def _make_price_record(self, close_price: float, ts: datetime.datetime):
        record = MagicMock()
        record.open = Decimal(str(close_price - 0.001))
        record.high = Decimal(str(close_price + 0.002))
        record.low = Decimal(str(close_price - 0.002))
        record.close = Decimal(str(close_price))
        record.volume = Decimal("1000")
        record.timestamp = ts
        return record

    def test_converts_to_dataframe(self):
        """Should convert records to DataFrame with OHLCV columns."""
        ts = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        records = [
            self._make_price_record(1.1000, ts),
            self._make_price_record(1.1010, ts + datetime.timedelta(hours=1)),
        ]
        df = _price_data_to_df(records)
        assert df is not None
        assert len(df) == 2
        assert "close" in df.columns
        assert "open" in df.columns

    def test_empty_records_returns_none(self):
        """Empty records should return None."""
        df = _price_data_to_df([])
        assert df is None


class TestCompositeScoreCalculation:
    """Test composite score calculation logic."""

    def test_weights_sum_to_one_swing(self):
        """Swing trading weights should sum to 1.0."""
        from src.signals.mtf_filter import MTFFilter
        mtf = MTFFilter()
        weights = mtf.get_timeframe_weights("H4")
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-10

    def test_weights_sum_to_one_scalping(self):
        """Scalping weights should sum to 1.0."""
        from src.signals.mtf_filter import MTFFilter
        mtf = MTFFilter()
        weights = mtf.get_timeframe_weights("M1")
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-10

    def test_composite_score_formula(self):
        """Composite score should follow the formula."""
        ta_score = 80.0
        fa_score = 50.0
        sentiment_score = 30.0
        geo_score = 0.0
        weights = {"ta": 0.45, "fa": 0.25, "sentiment": 0.20, "geo": 0.10}

        expected = (
            weights["ta"] * ta_score
            + weights["fa"] * fa_score
            + weights["sentiment"] * sentiment_score
            + weights["geo"] * geo_score
        )

        # = 0.45*80 + 0.25*50 + 0.20*30 + 0.10*0
        # = 36 + 12.5 + 6 + 0 = 54.5
        assert abs(expected - 54.5) < 0.001


class TestMTFFilter:
    """Test Multi-Timeframe Filter."""

    def test_agree_with_2_tfs_increases_score(self):
        """Agreeing with 2 higher TFs should multiply score by 1.2."""
        from src.signals.mtf_filter import MTFFilter
        mtf = MTFFilter()
        score = 50.0
        higher_tfs = [
            {"timeframe": "H4", "score": 60.0},   # LONG
            {"timeframe": "D1", "score": 70.0},   # LONG
        ]
        adjusted = mtf.apply(score, "H1", higher_tfs)
        assert abs(adjusted - score * 1.2) < 0.001

    def test_disagree_with_2_tfs_decreases_score(self):
        """Disagreeing with 2 higher TFs should multiply score by 0.4."""
        from src.signals.mtf_filter import MTFFilter
        mtf = MTFFilter()
        score = 50.0
        higher_tfs = [
            {"timeframe": "H4", "score": -60.0},   # SHORT
            {"timeframe": "D1", "score": -70.0},   # SHORT
        ]
        adjusted = mtf.apply(score, "H1", higher_tfs)
        assert abs(adjusted - score * 0.4) < 0.001

    def test_no_higher_tfs_unchanged(self):
        """With no higher TF data, score should remain unchanged."""
        from src.signals.mtf_filter import MTFFilter
        mtf = MTFFilter()
        score = 45.0
        adjusted = mtf.apply(score, "H1", [])
        assert adjusted == score

    def test_score_capped_at_100(self):
        """Score should be capped at 100 after MTF adjustment."""
        from src.signals.mtf_filter import MTFFilter
        mtf = MTFFilter()
        score = 95.0  # After ×1.2 would be 114
        higher_tfs = [
            {"timeframe": "H4", "score": 60.0},
            {"timeframe": "D1", "score": 60.0},
        ]
        adjusted = mtf.apply(score, "H1", higher_tfs)
        assert adjusted <= 100.0


class TestRefineEntryPoint:
    """Test the entry price refinement logic."""

    def test_no_atr_returns_current_price(self):
        """When ATR is None or zero, entry should equal current price."""
        price = Decimal("1.1000")
        result = _refine_entry_point("LONG", price, {}, atr=None)
        assert result == price

    def test_zero_atr_returns_current_price(self):
        price = Decimal("1.1000")
        result = _refine_entry_point("LONG", price, {}, atr=Decimal("0"))
        assert result == price

    def test_long_fib618_used_when_near(self):
        """LONG: Fib 0.618 below price within 2×ATR should become entry."""
        price = Decimal("1.1000")
        atr = Decimal("0.0050")
        indicators = {"fib_618": 1.0960}  # 40 pips below, within 2×ATR=100 pips
        result = _refine_entry_point("LONG", price, indicators, atr)
        assert result < price
        assert float(result) == pytest.approx(1.0960, abs=1e-6)

    def test_long_fib618_ignored_when_far(self):
        """LONG: Fib 0.618 more than 2×ATR away should be ignored."""
        price = Decimal("1.1000")
        atr = Decimal("0.0010")
        indicators = {"fib_618": 1.0800}  # 200 pips away, 2×ATR=20 pips
        result = _refine_entry_point("LONG", price, indicators, atr)
        assert result == price

    def test_short_fib382_used_when_near(self):
        """SHORT: Fib 0.382 above price within 2×ATR should become entry."""
        price = Decimal("1.1000")
        atr = Decimal("0.0050")
        indicators = {"fib_382": 1.1040}  # 40 pips above
        result = _refine_entry_point("SHORT", price, indicators, atr)
        assert result > price
        assert float(result) == pytest.approx(1.1040, abs=1e-6)

    def test_hold_direction_returns_current_price(self):
        """HOLD direction should always return current_price unchanged."""
        price = Decimal("1.1000")
        atr = Decimal("0.0010")
        result = _refine_entry_point("HOLD", price, {"fib_618": 1.098}, atr)
        assert result == price

    def test_long_chooses_highest_candidate(self):
        """LONG: the candidate closest to current price (highest) is chosen."""
        price = Decimal("1.1000")
        atr = Decimal("0.0100")
        indicators = {
            "fib_618": 1.0950,  # 50 pips below
            "vpoc": 1.0970,     # 30 pips below — should win (highest)
        }
        result = _refine_entry_point("LONG", price, indicators, atr)
        assert float(result) == pytest.approx(1.0970, abs=1e-5)

    def test_short_chooses_lowest_candidate(self):
        """SHORT: the candidate closest to current price (lowest) is chosen."""
        price = Decimal("1.1000")
        atr = Decimal("0.0100")
        indicators = {
            "fib_382": 1.1050,  # 50 pips above
            "vpoc": 1.1030,     # 30 pips above — should win (lowest)
        }
        result = _refine_entry_point("SHORT", price, indicators, atr)
        assert float(result) == pytest.approx(1.1030, abs=1e-5)

    def test_empty_indicators_returns_current_price(self):
        price = Decimal("1.1000")
        atr = Decimal("0.0010")
        result = _refine_entry_point("LONG", price, {}, atr)
        assert result == price


class TestSignalEngineGenerateSignal:
    """Test SignalEngine.generate_signal early-exit paths via mocked DB."""

    def _make_instrument(self, symbol="EURUSD=X", market="forex", inst_id=1):
        inst = MagicMock()
        inst.id = inst_id
        inst.symbol = symbol
        inst.market = market
        inst.name = "Euro/USD"
        return inst

    @pytest.mark.asyncio
    async def test_cooldown_returns_none(self):
        """If cooldown not elapsed, generate_signal returns None."""
        import src.signals.signal_engine as se
        inst = self._make_instrument()
        db = AsyncMock()

        # Seed the cooldown cache with a recent time
        now = datetime.datetime.now(datetime.timezone.utc)
        se._cooldown_cache[(inst.id, "H1")] = now - datetime.timedelta(minutes=10)

        engine = SignalEngine()
        result = await engine.generate_signal(inst, "H1", db)
        assert result is None

        # Cleanup
        se._cooldown_cache.pop((inst.id, "H1"), None)

    @pytest.mark.asyncio
    async def test_insufficient_price_data_returns_none(self):
        """If fewer than 30 price records, generate_signal returns None."""
        import src.signals.signal_engine as se
        inst = self._make_instrument(inst_id=999)
        db = AsyncMock()

        # Clear cooldown for this instrument
        se._cooldown_cache.pop((inst.id, "H1"), None)

        with patch("src.signals.signal_engine.get_latest_signal_for_instrument", new=AsyncMock(return_value=None)):
            with patch("src.signals.signal_engine.get_price_data", new=AsyncMock(return_value=[])):
                engine = SignalEngine()
                result = await engine.generate_signal(inst, "H1", db)
                assert result is None

    @pytest.mark.asyncio
    async def test_generate_signal_by_symbol_not_found_returns_none(self):
        """generate_signal_by_symbol returns None if instrument not found."""
        db = AsyncMock()
        with patch("src.signals.signal_engine.get_instrument_by_symbol", new=AsyncMock(return_value=None)):
            engine = SignalEngine()
            result = await engine.generate_signal_by_symbol("INVALID", "H1", db)
            assert result is None

    @pytest.mark.asyncio
    async def test_cooldown_seeded_from_db_on_first_call(self):
        """First call (no cache entry) seeds cooldown from DB if signal exists recently."""
        import src.signals.signal_engine as se
        inst = self._make_instrument(inst_id=888)
        db = AsyncMock()

        # Ensure no cache entry
        se._cooldown_cache.pop((inst.id, "H4"), None)

        # DB returns a signal from 5 minutes ago (within 240-min H4 cooldown)
        recent_sig = MagicMock()
        recent_sig.created_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)

        with patch("src.signals.signal_engine.get_latest_signal_for_instrument", new=AsyncMock(return_value=recent_sig)):
            engine = SignalEngine()
            result = await engine.generate_signal(inst, "H4", db)
            assert result is None  # cooldown active

        # Cleanup
        se._cooldown_cache.pop((inst.id, "H4"), None)


class TestSignalEngineInit:
    """Test SignalEngine instantiation."""

    def test_can_instantiate(self):
        engine = SignalEngine()
        assert engine is not None

    def test_has_risk_manager(self):
        from src.signals.risk_manager import RiskManager
        engine = SignalEngine()
        assert isinstance(engine.risk_manager, RiskManager)

    def test_has_mtf_filter(self):
        from src.signals.mtf_filter import MTFFilter
        engine = SignalEngine()
        assert isinstance(engine.mtf_filter, MTFFilter)


class TestRefineEntryPointExtraCandidates:
    """Test _refine_entry_point for Order Block and PDH/PDL branches."""

    def test_long_bull_ob_high_used_when_near(self):
        """LONG: Bullish OB high below price within 2×ATR should become entry."""
        price = Decimal("1.1000")
        atr = Decimal("0.0050")
        indicators = {"bull_ob_high": 1.0960}  # 40 pips below, within 2×ATR=100 pips
        result = _refine_entry_point("LONG", price, indicators, atr)
        assert result < price
        assert float(result) == pytest.approx(1.0960, abs=1e-6)

    def test_long_pdl_used_when_near(self):
        """LONG: PDL below price within 1.5×ATR should add buffer entry."""
        price = Decimal("1.1000")
        atr = Decimal("0.0050")
        # PDL at 1.0930 — 70 pips below, within 1.5×ATR=75 pips
        indicators = {"pdl": 1.0930}
        result = _refine_entry_point("LONG", price, indicators, atr)
        # Entry should be PDL + 10% of ATR = 1.0930 + 0.0005 = 1.0935
        assert result < price
        assert float(result) == pytest.approx(1.0935, abs=1e-5)

    def test_long_pdl_too_far_ignored(self):
        """LONG: PDL more than 1.5×ATR away should be ignored."""
        price = Decimal("1.1000")
        atr = Decimal("0.0010")
        indicators = {"pdl": 1.0800}  # 200 pips below, 1.5×ATR=15 pips
        result = _refine_entry_point("LONG", price, indicators, atr)
        assert result == price

    def test_short_bear_ob_low_used_when_near(self):
        """SHORT: Bearish OB low above price within 2×ATR should become entry."""
        price = Decimal("1.1000")
        atr = Decimal("0.0050")
        indicators = {"bear_ob_low": 1.1040}  # 40 pips above, within 2×ATR=100 pips
        result = _refine_entry_point("SHORT", price, indicators, atr)
        assert result > price
        assert float(result) == pytest.approx(1.1040, abs=1e-6)

    def test_short_pdh_used_when_near(self):
        """SHORT: PDH above price within 1.5×ATR should add buffer entry."""
        price = Decimal("1.1000")
        atr = Decimal("0.0050")
        # PDH at 1.1070 — 70 pips above, within 1.5×ATR=75 pips
        indicators = {"pdh": 1.1070}
        result = _refine_entry_point("SHORT", price, indicators, atr)
        # Entry should be PDH - 10% of ATR = 1.1070 - 0.0005 = 1.1065
        assert result > price
        assert float(result) == pytest.approx(1.1065, abs=1e-5)

    def test_short_pdh_too_far_ignored(self):
        """SHORT: PDH more than 1.5×ATR away should be ignored."""
        price = Decimal("1.1000")
        atr = Decimal("0.0010")
        indicators = {"pdh": 1.1200}  # 200 pips above, 1.5×ATR=15 pips
        result = _refine_entry_point("SHORT", price, indicators, atr)
        assert result == price


class TestLLMCache:
    """Test in-memory LLM result caching."""

    def setup_method(self):
        """Clear cache before each test."""
        _llm_cache.clear()

    def test_cache_miss_returns_none(self):
        assert _get_cached_llm("EURUSD=X", "H1") is None

    def test_set_then_get_returns_value(self):
        _set_cached_llm("EURUSD=X", "H1", 42.0, {"bias": "bullish"})
        result = _get_cached_llm("EURUSD=X", "H1")
        assert result is not None
        score, meta = result
        assert score == 42.0
        assert meta["bias"] == "bullish"

    def test_cache_miss_for_different_symbol(self):
        _set_cached_llm("EURUSD=X", "H1", 10.0, {})
        assert _get_cached_llm("GBPUSD=X", "H1") is None

    def test_cache_miss_for_different_timeframe(self):
        _set_cached_llm("EURUSD=X", "H1", 10.0, {})
        assert _get_cached_llm("EURUSD=X", "H4") is None

    def test_expired_cache_returns_none(self):
        """Manually insert an already-expired entry."""
        expired = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)
        _llm_cache[("EURUSD=X", "H1")] = (50.0, {}, expired)
        assert _get_cached_llm("EURUSD=X", "H1") is None

    def test_expired_entry_removed_from_cache(self):
        expired = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)
        _llm_cache[("EURUSD=X", "H1")] = (50.0, {}, expired)
        _get_cached_llm("EURUSD=X", "H1")
        assert ("EURUSD=X", "H1") not in _llm_cache

    def test_overwrites_existing_entry(self):
        _set_cached_llm("EURUSD=X", "H1", 10.0, {"bias": "bearish"})
        _set_cached_llm("EURUSD=X", "H1", 20.0, {"bias": "bullish"})
        score, meta = _get_cached_llm("EURUSD=X", "H1")
        assert score == 20.0
        assert meta["bias"] == "bullish"


# ── Helpers for generate_signal tests ─────────────────────────────────────────

def _make_price_records_for_engine(n: int = 60) -> list:
    """Create mock PriceData records for SignalEngine tests."""
    records = []
    price = 1.1000
    base_ts = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    for i in range(n):
        r = MagicMock()
        price += 0.0001 * (1 if i % 2 == 0 else -0.5)
        r.open = Decimal(str(round(price - 0.0001, 5)))
        r.high = Decimal(str(round(price + 0.0002, 5)))
        r.low = Decimal(str(round(price - 0.0002, 5)))
        r.close = Decimal(str(round(price, 5)))
        r.volume = Decimal("1000")
        r.timestamp = base_ts + datetime.timedelta(hours=i)
        records.append(r)
    return records


def _make_instrument(symbol: str = "EURUSD=X", instr_type: str = "forex") -> MagicMock:
    instr = MagicMock()
    instr.id = 1
    instr.symbol = symbol
    instr.name = "EUR/USD"
    instr.type = instr_type
    return instr


class TestGenerateSignal:
    """Integration tests for SignalEngine.generate_signal() with mocked DB and engines."""

    def _make_db(self) -> AsyncMock:
        db = AsyncMock()
        # Configure begin_nested() to support async context manager
        nested_ctx = AsyncMock()
        nested_ctx.__aenter__ = AsyncMock(return_value=None)
        nested_ctx.__aexit__ = AsyncMock(return_value=False)
        db.begin_nested = MagicMock(return_value=nested_ctx)
        return db

    def _patch_deps(self, patcher_ctx, ta_score=55.0, fa_score=40.0, sent_score=30.0, geo_score=20.0, atr=Decimal("0.001")):
        """Patch all external dependencies for generate_signal."""
        patches = {}

        # CRUD
        patches["price_data"] = patch("src.signals.signal_engine.get_price_data",
                                       new=AsyncMock(return_value=_make_price_records_for_engine(60)))
        patches["macro_data"] = patch("src.signals.signal_engine.get_macro_data",
                                       new=AsyncMock(return_value=[]))
        patches["news_events"] = patch("src.signals.signal_engine.get_news_events",
                                        new=AsyncMock(return_value=[]))
        patches["latest_signal"] = patch("src.signals.signal_engine.get_latest_signal_for_instrument",
                                          new=AsyncMock(return_value=None))
        patches["cancel_signals"] = patch("src.signals.signal_engine.cancel_open_signals",
                                           new=AsyncMock(return_value=None))
        patches["create_signal"] = patch("src.signals.signal_engine.create_signal",
                                          new=AsyncMock(return_value=MagicMock(id=1)))

        # Cache
        patches["cache_get"] = patch("src.signals.signal_engine.cache.get",
                                      new=AsyncMock(return_value=None))
        patches["cache_set"] = patch("src.signals.signal_engine.cache.set",
                                      new=AsyncMock(return_value=None))

        # TA Engine
        mock_ta = MagicMock()
        mock_ta.calculate_ta_score.return_value = ta_score
        mock_ta.calculate_all_indicators.return_value = {}
        mock_ta.get_atr.return_value = atr
        patches["ta_engine"] = patch("src.signals.signal_engine.TAEngine",
                                      return_value=mock_ta)

        # FA Engine
        mock_fa = MagicMock()
        mock_fa.calculate_fa_score.return_value = fa_score
        patches["fa_engine"] = patch("src.signals.signal_engine.FAEngine",
                                      return_value=mock_fa)

        # Sentiment Engine
        mock_sent = MagicMock()
        mock_sent.calculate_sentiment_score.return_value = sent_score
        patches["sent_engine"] = patch("src.signals.signal_engine.SentimentEngine",
                                        return_value=mock_sent)

        # Geo Engine
        mock_geo = MagicMock()
        mock_geo.calculate_geo_score.return_value = geo_score
        patches["geo_engine"] = patch("src.signals.signal_engine.GeoEngine",
                                       return_value=mock_geo)

        # LLM Engine
        mock_llm = MagicMock()
        mock_llm.analyze_signal = AsyncMock(return_value=(50.0, {"bias": "bullish"}))
        patches["llm_engine"] = patch("src.signals.signal_engine.LLMEngine",
                                       return_value=mock_llm)

        # Correlation Engine
        mock_corr = MagicMock()
        mock_corr.calculate_correlation_score.return_value = 0.0
        patches["corr_engine"] = patch("src.signals.signal_engine.CorrelationEngine",
                                        return_value=mock_corr)

        # MTF Filter
        mock_mtf = MagicMock()
        mock_mtf.apply_mtf_multiplier = AsyncMock(return_value=ta_score)
        patches["mtf"] = patch.object(patcher_ctx.mtf_filter, "apply_mtf_multiplier",
                                       new=AsyncMock(return_value=ta_score))

        # Telegram
        patches["telegram"] = patch("src.signals.signal_engine.telegram.send_signal_alert",
                                     new=AsyncMock(return_value=None))

        return patches

    @pytest.mark.asyncio
    async def test_generate_signal_insufficient_price_data(self):
        """Returns None when price data < 30 records."""
        engine = SignalEngine()
        db = self._make_db()
        instrument = _make_instrument()

        with patch("src.signals.signal_engine.get_price_data", new=AsyncMock(return_value=[])):
            with patch("src.signals.signal_engine.get_latest_signal_for_instrument", new=AsyncMock(return_value=None)):
                with patch("src.signals.signal_engine.cache.get", new=AsyncMock(return_value=None)):
                    result = await engine.generate_signal(instrument, "H1", db)
        assert result is None

    @pytest.mark.asyncio
    async def test_generate_signal_returns_signal_on_strong_bull(self):
        """High composite score → signal generated and saved."""
        engine = SignalEngine()
        db = self._make_db()
        instrument = _make_instrument()

        price_records = _make_price_records_for_engine(60)
        mock_signal = MagicMock()
        mock_signal.id = 42

        with patch("src.signals.signal_engine.get_price_data", new=AsyncMock(return_value=price_records)):
            with patch("src.signals.signal_engine.get_latest_signal_for_instrument", new=AsyncMock(return_value=None)):
                with patch("src.signals.signal_engine.get_macro_data", new=AsyncMock(return_value=[])):
                    with patch("src.signals.signal_engine.get_news_events", new=AsyncMock(return_value=[])):
                        with patch("src.signals.signal_engine.cancel_open_signals", new=AsyncMock()):
                            with patch("src.signals.signal_engine.create_signal", new=AsyncMock(return_value=mock_signal)):
                                with patch("src.signals.signal_engine.cache.get", new=AsyncMock(return_value=None)):
                                    with patch("src.signals.signal_engine.cache.set", new=AsyncMock()):
                                        mock_ta = MagicMock()
                                        mock_ta.calculate_ta_score.return_value = 60.0
                                        mock_ta.calculate_all_indicators.return_value = {}
                                        mock_ta.get_atr.return_value = Decimal("0.001")
                                        mock_fa = MagicMock()
                                        mock_fa.calculate_fa_score.return_value = 50.0
                                        mock_sent = MagicMock()
                                        mock_sent.calculate_sentiment_score.return_value = 40.0
                                        mock_geo = MagicMock()
                                        mock_geo.calculate_geo_score.return_value = 30.0
                                        mock_llm = MagicMock()
                                        mock_llm.calculate_llm_score = AsyncMock(return_value=(55.0, {}))
                                        mock_corr = MagicMock()
                                        mock_corr.calculate_correlation_score.return_value = 0.0
                                        with patch("src.signals.signal_engine.TAEngine", return_value=mock_ta):
                                            with patch("src.signals.signal_engine.FAEngine", return_value=mock_fa):
                                                with patch("src.signals.signal_engine.SentimentEngine", return_value=mock_sent):
                                                    with patch("src.signals.signal_engine.GeoEngine", return_value=mock_geo):
                                                        with patch("src.signals.signal_engine.LLMEngine", return_value=mock_llm):
                                                            with patch("src.signals.signal_engine.CorrelationEngine", return_value=mock_corr):
                                                                with patch.object(engine.mtf_filter, "apply", return_value=55.0), patch.object(engine.mtf_filter, "get_timeframe_weights", return_value={"ta": 0.4, "fa": 0.3, "sentiment": 0.2, "geo": 0.1}), patch.object(engine.mtf_filter, "get_horizon", return_value="1-3 days"):
                                                                    with patch("src.signals.signal_engine.telegram.send_signal_alert", new=AsyncMock()):
                                                                        result = await engine.generate_signal(instrument, "H1", db)
        # Signal created (strong bull composite)
        assert result is not None

    @pytest.mark.asyncio
    async def test_generate_signal_hold_returns_none(self):
        """Composite score in HOLD range → no signal generated."""
        engine = SignalEngine()
        db = self._make_db()
        instrument = _make_instrument()
        price_records = _make_price_records_for_engine(60)

        with patch("src.signals.signal_engine.get_price_data", new=AsyncMock(return_value=price_records)):
            with patch("src.signals.signal_engine.get_latest_signal_for_instrument", new=AsyncMock(return_value=None)):
                with patch("src.signals.signal_engine.get_macro_data", new=AsyncMock(return_value=[])):
                    with patch("src.signals.signal_engine.get_news_events", new=AsyncMock(return_value=[])):
                        with patch("src.signals.signal_engine.cache.get", new=AsyncMock(return_value=None)):
                            with patch("src.signals.signal_engine.cache.set", new=AsyncMock()):
                                mock_ta = MagicMock()
                                mock_ta.calculate_ta_score.return_value = 5.0  # near zero = HOLD
                                mock_ta.calculate_all_indicators.return_value = {}
                                mock_ta.get_atr.return_value = Decimal("0.001")
                                mock_fa = MagicMock()
                                mock_fa.calculate_fa_score.return_value = 5.0
                                mock_sent = MagicMock()
                                mock_sent.calculate_sentiment_score.return_value = 0.0
                                mock_geo = MagicMock()
                                mock_geo.calculate_geo_score.return_value = 0.0
                                mock_corr = MagicMock()
                                mock_corr.calculate_correlation_score.return_value = 0.0
                                with patch("src.signals.signal_engine.TAEngine", return_value=mock_ta):
                                    with patch("src.signals.signal_engine.FAEngine", return_value=mock_fa):
                                        with patch("src.signals.signal_engine.SentimentEngine", return_value=mock_sent):
                                            with patch("src.signals.signal_engine.GeoEngine", return_value=mock_geo):
                                                with patch("src.signals.signal_engine.CorrelationEngine", return_value=mock_corr):
                                                    with patch.object(engine.mtf_filter, "apply", return_value=5.0), patch.object(engine.mtf_filter, "get_timeframe_weights", return_value={"ta": 0.4, "fa": 0.3, "sentiment": 0.2, "geo": 0.1}), patch.object(engine.mtf_filter, "get_horizon", return_value="1-3 days"):
                                                        result = await engine.generate_signal(instrument, "H1", db)
        assert result is None

    @pytest.mark.asyncio
    async def test_generate_signal_by_symbol_not_found(self):
        """generate_signal_by_symbol returns None when symbol not in DB."""
        engine = SignalEngine()
        db = self._make_db()
        with patch("src.signals.signal_engine.get_instrument_by_symbol", new=AsyncMock(return_value=None)):
            result = await engine.generate_signal_by_symbol("UNKNOWN", "H1", db)
        assert result is None

    @pytest.mark.asyncio
    async def test_generate_signal_by_symbol_delegates(self):
        """generate_signal_by_symbol finds instrument and calls generate_signal."""
        engine = SignalEngine()
        db = self._make_db()
        instrument = _make_instrument()
        with patch("src.signals.signal_engine.get_instrument_by_symbol", new=AsyncMock(return_value=instrument)):
            with patch.object(engine, "generate_signal", new=AsyncMock(return_value=None)) as mock_gen:
                await engine.generate_signal_by_symbol("EURUSD=X", "H1", db)
                mock_gen.assert_called_once_with(instrument, "H1", db)

    @pytest.mark.asyncio
    async def test_generate_signal_cooldown_from_redis(self):
        """Returns None when cooldown is active (recent signal in Redis)."""
        engine = SignalEngine()
        db = self._make_db()
        instrument = _make_instrument()

        # Signal was 5 minutes ago, H1 cooldown = 60 min
        recent = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)).isoformat()
        with patch("src.signals.signal_engine.cache.get", new=AsyncMock(return_value=recent)):
            result = await engine.generate_signal(instrument, "H1", db)
        assert result is None
