"""Tests for LLM Engine."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.analysis.llm_engine import (
    LLMEngine,
    _build_prompt,
    _extract_macro_value,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_macro(indicator: str, value, country: str = "US") -> MagicMock:
    item = MagicMock()
    item.indicator_name = indicator
    item.value = value
    item.country = country
    return item


def make_news(headline: str, importance: str = "medium", sentiment=0.0) -> MagicMock:
    item = MagicMock()
    item.headline = headline
    item.importance = importance
    item.sentiment_score = Decimal(str(sentiment)) if sentiment is not None else None
    return item


def make_instrument(symbol: str = "EURUSD=X", name: str = "Euro/USD") -> MagicMock:
    inst = MagicMock()
    inst.symbol = symbol
    inst.name = name
    return inst


# ── _extract_macro_value ─────────────────────────────────────────────────────

class TestExtractMacroValue:

    def test_returns_float_for_existing_indicator(self):
        records = [make_macro("DXY", 103.5)]
        assert _extract_macro_value(records, "DXY") == pytest.approx(103.5)

    def test_returns_none_for_missing_indicator(self):
        records = [make_macro("VIX", 20.0)]
        assert _extract_macro_value(records, "DXY") is None

    def test_returns_none_for_empty_list(self):
        assert _extract_macro_value([], "VIX") is None

    def test_skips_none_value(self):
        records = [make_macro("DXY", None)]
        assert _extract_macro_value(records, "DXY") is None

    def test_skips_invalid_value(self):
        records = [make_macro("DXY", "bad")]
        assert _extract_macro_value(records, "DXY") is None


# ── _build_prompt ─────────────────────────────────────────────────────────────

class TestBuildPrompt:

    def test_returns_string(self):
        inst = make_instrument()
        result = _build_prompt(inst, "H1", 50.0, 30.0, 20.0, 10.0, {}, [], [])
        assert isinstance(result, str)

    def test_contains_symbol(self):
        inst = make_instrument("GBPUSD=X", "British Pound/USD")
        result = _build_prompt(inst, "H4", 0.0, 0.0, 0.0, 0.0, {}, [], [])
        assert "GBPUSD=X" in result
        assert "British Pound/USD" in result

    def test_contains_timeframe(self):
        inst = make_instrument()
        result = _build_prompt(inst, "D1", 0.0, 0.0, 0.0, 0.0, {}, [], [])
        assert "D1" in result

    def test_contains_engine_scores(self):
        inst = make_instrument()
        result = _build_prompt(inst, "H1", 75.5, -30.0, 15.0, 5.0, {}, [], [])
        assert "+75.50" in result or "75.50" in result
        assert "-30.00" in result or "30.00" in result

    def test_contains_ta_indicators(self):
        inst = make_instrument()
        ta = {"rsi_14": 55.5, "macd": 0.001, "atr_14": 0.0050}
        result = _build_prompt(inst, "H1", 0.0, 0.0, 0.0, 0.0, ta, [], [])
        assert "RSI(14)" in result
        assert "MACD" in result
        assert "ATR(14)" in result

    def test_contains_smc_levels(self):
        inst = make_instrument()
        ta = {
            "pdh": 1.1050, "pdl": 1.0980,
            "vpoc": 1.1020, "vah": 1.1040, "val": 1.1000,
            "fib_618": 1.0990, "fib_382": 1.1030,
        }
        result = _build_prompt(inst, "H1", 0.0, 0.0, 0.0, 0.0, ta, [], [])
        assert "PDH" in result
        assert "VPOC" in result
        assert "Fib" in result

    def test_contains_macro_data(self):
        inst = make_instrument()
        macro = [make_macro("FEDFUNDS", 5.25, "US")]
        result = _build_prompt(inst, "H1", 0.0, 0.0, 0.0, 0.0, {}, macro, [])
        assert "FEDFUNDS" in result

    def test_contains_news(self):
        inst = make_instrument()
        news = [make_news("Fed raises rates to 5.5%", "high", 0.3)]
        result = _build_prompt(inst, "H1", 0.0, 0.0, 0.0, 0.0, {}, [], news)
        assert "Fed raises rates" in result

    def test_no_indicators_shows_no_data(self):
        inst = make_instrument()
        result = _build_prompt(inst, "H1", 0.0, 0.0, 0.0, 0.0, {}, [], [])
        assert "no data" in result.lower() or "(no data)" in result

    def test_market_context_dxy_vix(self):
        inst = make_instrument()
        macro = [make_macro("DXY", 103.5), make_macro("VIX", 18.0)]
        result = _build_prompt(inst, "H1", 0.0, 0.0, 0.0, 0.0, {}, macro, [])
        assert "DXY" in result
        assert "VIX" in result

    def test_only_last_3_macro_records_shown(self):
        """Prompt should show only up to 3 macro records."""
        inst = make_instrument()
        macro = [make_macro(f"IND{i}", float(i)) for i in range(6)]
        result = _build_prompt(inst, "H1", 0.0, 0.0, 0.0, 0.0, {}, macro, [])
        # Only IND0, IND1, IND2 should appear (first 3)
        assert "IND0" in result
        assert "IND1" in result
        assert "IND2" in result

    def test_only_last_5_news_shown(self):
        """Prompt should show only up to 5 news records."""
        inst = make_instrument()
        news = [make_news(f"News headline {i}") for i in range(7)]
        result = _build_prompt(inst, "H1", 0.0, 0.0, 0.0, 0.0, {}, [], news)
        assert "News headline 0" in result
        assert "News headline 4" in result


# ── LLMEngine.__init__ ────────────────────────────────────────────────────────

class TestLLMEngineInit:

    def test_disabled_without_api_key(self):
        with patch("src.analysis.llm_engine.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = None
            engine = LLMEngine()
            assert engine.enabled is False

    def test_enabled_with_api_key(self):
        with patch("src.analysis.llm_engine.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = "sk-ant-test-key"
            engine = LLMEngine()
            assert engine.enabled is True


# ── LLMEngine.calculate_llm_score ─────────────────────────────────────────────

class TestCalculateLLMScore:

    @pytest.mark.asyncio
    async def test_returns_zero_when_disabled(self):
        with patch("src.analysis.llm_engine.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = None
            engine = LLMEngine()
            score, meta = await engine.calculate_llm_score(
                make_instrument(), "H1", 50.0, 30.0, 20.0, 10.0, {}, [], []
            )
            assert score == 0.0
            assert meta == {}

    @pytest.mark.asyncio
    async def test_returns_score_from_api(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"score": 65.0, "bias": "BULLISH", "confidence": 80.0, "key_factors": ["RSI bullish"], "reasoning": "Strong momentum."}')]

        with patch("src.analysis.llm_engine.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = "sk-ant-test"
            with patch("anthropic.AsyncAnthropic") as mock_anthropic:
                mock_client = MagicMock()
                mock_client.messages.create = AsyncMock(return_value=mock_response)
                mock_anthropic.return_value = mock_client

                engine = LLMEngine()
                score, meta = await engine.calculate_llm_score(
                    make_instrument(), "H1", 50.0, 30.0, 20.0, 10.0, {}, [], []
                )

                assert score == pytest.approx(65.0)
                assert meta["bias"] == "BULLISH"
                assert meta["confidence"] == pytest.approx(80.0)
                assert "RSI bullish" in meta["key_factors"]
                assert "Strong momentum" in meta["reasoning"]

    @pytest.mark.asyncio
    async def test_clamps_score_to_range(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"score": 150.0, "bias": "BULLISH", "confidence": 90.0, "key_factors": [], "reasoning": "extreme"}')]

        with patch("src.analysis.llm_engine.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = "sk-ant-test"
            with patch("anthropic.AsyncAnthropic") as mock_anthropic:
                mock_client = MagicMock()
                mock_client.messages.create = AsyncMock(return_value=mock_response)
                mock_anthropic.return_value = mock_client

                engine = LLMEngine()
                score, meta = await engine.calculate_llm_score(
                    make_instrument(), "H1", 0.0, 0.0, 0.0, 0.0, {}, [], []
                )
                assert score <= 100.0

    @pytest.mark.asyncio
    async def test_strips_markdown_code_block(self):
        json_content = '{"score": 42.0, "bias": "NEUTRAL", "confidence": 50.0, "key_factors": [], "reasoning": "Mixed signals."}'
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=f"```json\n{json_content}\n```")]

        with patch("src.analysis.llm_engine.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = "sk-ant-test"
            with patch("anthropic.AsyncAnthropic") as mock_anthropic:
                mock_client = MagicMock()
                mock_client.messages.create = AsyncMock(return_value=mock_response)
                mock_anthropic.return_value = mock_client

                engine = LLMEngine()
                score, meta = await engine.calculate_llm_score(
                    make_instrument(), "H1", 0.0, 0.0, 0.0, 0.0, {}, [], []
                )
                assert score == pytest.approx(42.0)
                assert meta["bias"] == "NEUTRAL"

    @pytest.mark.asyncio
    async def test_returns_zero_on_api_exception(self):
        with patch("src.analysis.llm_engine.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = "sk-ant-test"
            with patch("anthropic.AsyncAnthropic") as mock_anthropic:
                mock_client = MagicMock()
                mock_client.messages.create = AsyncMock(side_effect=Exception("API error"))
                mock_anthropic.return_value = mock_client

                engine = LLMEngine()
                score, meta = await engine.calculate_llm_score(
                    make_instrument(), "H1", 0.0, 0.0, 0.0, 0.0, {}, [], []
                )
                assert score == 0.0
                assert meta == {}

    @pytest.mark.asyncio
    async def test_returns_zero_on_invalid_json(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="not valid json")]

        with patch("src.analysis.llm_engine.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = "sk-ant-test"
            with patch("anthropic.AsyncAnthropic") as mock_anthropic:
                mock_client = MagicMock()
                mock_client.messages.create = AsyncMock(return_value=mock_response)
                mock_anthropic.return_value = mock_client

                engine = LLMEngine()
                score, meta = await engine.calculate_llm_score(
                    make_instrument(), "H1", 0.0, 0.0, 0.0, 0.0, {}, [], []
                )
                assert score == 0.0
                assert meta == {}
