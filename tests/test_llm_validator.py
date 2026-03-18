"""Tests for src.analysis.llm_validator."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.analysis.llm_validator import (
    LLMValidator,
    _approved,
    _build_prompt,
    _cache_key,
    _parse_response,
)

_SAMPLE_SIGNAL = {
    "direction": "LONG",
    "signal_strength": "STRONG_BUY",
    "composite_score": 72.5,
    "ta_score": 65.0,
    "fa_score": 55.0,
    "sentiment_score": 40.0,
    "geo_score": 10.0,
    "of_score": 60.0,
    "confidence": 82.0,
    "regime": "STRONG_TREND_BULL",
    "entry_price": Decimal("1.08500"),
    "stop_loss": Decimal("1.08250"),
    "take_profit_1": Decimal("1.08875"),
    "risk_reward": Decimal("1.50"),
    "portfolio_heat": 3.5,
    "earnings_days_ahead": None,
}


class TestHelpers:
    def test_approved_returns_correct_shape(self):
        result = _approved()
        assert result["approved"] is True
        assert result["decision"] == "approve"
        assert result["adjusted_confidence"] is None

    def test_cache_key_is_deterministic(self):
        k1 = _cache_key("EURUSD", "H4", _SAMPLE_SIGNAL)
        k2 = _cache_key("EURUSD", "H4", _SAMPLE_SIGNAL)
        assert k1 == k2

    def test_cache_key_differs_by_symbol(self):
        k1 = _cache_key("EURUSD", "H4", _SAMPLE_SIGNAL)
        k2 = _cache_key("GBPUSD", "H4", _SAMPLE_SIGNAL)
        assert k1 != k2

    def test_build_prompt_contains_key_fields(self):
        prompt = _build_prompt("EUR/USD", "H4", _SAMPLE_SIGNAL)
        assert "EUR/USD" in prompt
        assert "LONG" in prompt
        assert "STRONG_TREND_BULL" in prompt
        assert "72.5" in prompt
        assert "R:R" in prompt

    def test_build_prompt_includes_earnings(self):
        sig = {**_SAMPLE_SIGNAL, "earnings_days_ahead": 3}
        prompt = _build_prompt("AAPL", "H4", sig)
        assert "Earnings" in prompt
        assert "3 days" in prompt


class TestParseResponse:
    def test_parse_approve(self):
        raw = '{"decision": "approve", "reason": "Strong trend alignment"}'
        result = _parse_response(raw, _SAMPLE_SIGNAL)
        assert result["approved"] is True
        assert result["decision"] == "approve"

    def test_parse_reject(self):
        raw = '{"decision": "reject", "reason": "R:R too low"}'
        result = _parse_response(raw, _SAMPLE_SIGNAL)
        assert result["approved"] is False
        assert result["decision"] == "reject"
        assert "R:R" in result["reason"]

    def test_parse_reduce(self):
        raw = '{"decision": "reduce", "reason": "Mixed signals", "adjusted_confidence": 55.0}'
        result = _parse_response(raw, _SAMPLE_SIGNAL)
        assert result["approved"] is True
        assert result["decision"] == "reduce"
        assert result["adjusted_confidence"] == 55.0

    def test_parse_markdown_fenced(self):
        raw = "```json\n{\"decision\": \"approve\", \"reason\": \"ok\"}\n```"
        result = _parse_response(raw, _SAMPLE_SIGNAL)
        assert result["approved"] is True

    def test_parse_invalid_json_approves(self):
        result = _parse_response("not valid json at all", _SAMPLE_SIGNAL)
        assert result["approved"] is True

    def test_parse_unknown_decision_approves(self):
        raw = '{"decision": "maybe", "reason": "hmm"}'
        result = _parse_response(raw, _SAMPLE_SIGNAL)
        assert result["approved"] is True
        assert result["decision"] == "approve"


class TestLLMValidator:
    def test_disabled_when_no_api_key(self):
        with patch("src.analysis.llm_validator.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = ""
            mock_settings.LLM_VALIDATION_ENABLED = True
            validator = LLMValidator()
            assert not validator.enabled

    def test_enabled_when_key_present(self):
        with patch("src.analysis.llm_validator.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = "sk-ant-test"
            mock_settings.LLM_VALIDATION_ENABLED = True
            validator = LLMValidator()
            assert validator.enabled

    @pytest.mark.asyncio
    async def test_disabled_validator_auto_approves(self):
        with patch("src.analysis.llm_validator.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = ""
            mock_settings.LLM_VALIDATION_ENABLED = True
            validator = LLMValidator()
            result = await validator.validate("EURUSD", "H4", _SAMPLE_SIGNAL)
            assert result["approved"] is True

    @pytest.mark.asyncio
    async def test_cache_hit_skips_api_call(self):
        import json

        cached_result = json.dumps({
            "approved": True,
            "decision": "approve",
            "reason": "cached",
            "adjusted_confidence": None,
        })

        with patch("src.analysis.llm_validator.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = "sk-ant-test"
            mock_settings.LLM_VALIDATION_ENABLED = True
            validator = LLMValidator()

            with patch("src.analysis.llm_validator.cache") as mock_cache:
                mock_cache.get = AsyncMock(return_value=cached_result)
                mock_cache.set = AsyncMock()

                result = await validator.validate("EURUSD", "H4", _SAMPLE_SIGNAL)
                assert result["approved"] is True
                assert result["reason"] == "cached"
                # API should NOT have been called
                mock_cache.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_api_error_approves_by_default(self):
        with patch("src.analysis.llm_validator.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = "sk-ant-test"
            mock_settings.LLM_VALIDATION_ENABLED = True
            validator = LLMValidator()

            with patch("src.analysis.llm_validator.cache") as mock_cache:
                mock_cache.get = AsyncMock(return_value=None)
                mock_cache.set = AsyncMock()

                with patch.object(validator, "_call_claude", side_effect=Exception("API error")):
                    result = await validator.validate("EURUSD", "H4", _SAMPLE_SIGNAL)
                    assert result["approved"] is True

    @pytest.mark.asyncio
    async def test_reject_response_blocks_signal(self):
        with patch("src.analysis.llm_validator.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = "sk-ant-test"
            mock_settings.LLM_VALIDATION_ENABLED = True
            validator = LLMValidator()

            with patch("src.analysis.llm_validator.cache") as mock_cache:
                mock_cache.get = AsyncMock(return_value=None)
                mock_cache.set = AsyncMock()

                reject_result = {
                    "approved": False,
                    "decision": "reject",
                    "reason": "Poor R:R ratio",
                    "adjusted_confidence": None,
                }
                with patch.object(validator, "_call_claude", return_value=reject_result):
                    result = await validator.validate("EURUSD", "H4", _SAMPLE_SIGNAL)
                    assert result["approved"] is False

    @pytest.mark.asyncio
    async def test_reduce_response_lowers_confidence(self):
        with patch("src.analysis.llm_validator.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = "sk-ant-test"
            mock_settings.LLM_VALIDATION_ENABLED = True
            validator = LLMValidator()

            with patch("src.analysis.llm_validator.cache") as mock_cache:
                mock_cache.get = AsyncMock(return_value=None)
                mock_cache.set = AsyncMock()

                reduce_result = {
                    "approved": True,
                    "decision": "reduce",
                    "reason": "Weak FA",
                    "adjusted_confidence": 55.0,
                }
                with patch.object(validator, "_call_claude", return_value=reduce_result):
                    result = await validator.validate("EURUSD", "H4", _SAMPLE_SIGNAL)
                    assert result["approved"] is True
                    assert result["adjusted_confidence"] == 55.0
