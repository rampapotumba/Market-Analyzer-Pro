"""Tests for Phase 3.1.3 — FA engine routing by market type.

These tests verify that resolve_fa_score() routes to the correct FA engine
based on market type (forex → ForexFAEngine, stocks → StockFAEngine, etc.).
"""

import sys
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Stub heavy transitive dependencies before importing signal_engine_v2 ──────
# asyncpg and redis are not installed in this dev environment; mock them out.

_stub_asyncpg = MagicMock()
sys.modules.setdefault("asyncpg", _stub_asyncpg)

# redis needs a proper package-like stub so redis.exceptions works
_redis_exceptions = MagicMock()
_redis_exceptions.RedisError = Exception
_redis_mod = MagicMock()
_redis_mod.exceptions = _redis_exceptions
_redis_asyncio = MagicMock()
sys.modules.setdefault("redis", _redis_mod)
sys.modules.setdefault("redis.asyncio", _redis_asyncio)
sys.modules.setdefault("redis.exceptions", _redis_exceptions)

_stub_engine = MagicMock()
_stub_engine.async_session_factory = MagicMock()
_stub_engine.init_db = AsyncMock()
sys.modules.setdefault("src.database.engine", _stub_engine)

# Now it's safe to import the signal engine
from src.signals.signal_engine_v2 import SignalEngineV2  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> SignalEngineV2:
    portfolio_mock = MagicMock()
    portfolio_mock.can_open.return_value = (True, "")
    portfolio_mock.portfolio_heat.return_value = 0.0
    return SignalEngineV2(portfolio=portfolio_mock)


@pytest.fixture
def mock_db() -> MagicMock:
    return MagicMock()


# ── Forex routing ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_fa_score_forex_calls_forex_fa_engine(
    engine: SignalEngineV2, mock_db: MagicMock
) -> None:
    """ForexFAEngine.analyze() is called for forex market."""
    mock_instance = AsyncMock()
    mock_instance.analyze = AsyncMock(return_value={"score": 12.5, "components": {}})

    with patch("src.analysis.forex_fa_engine.ForexFAEngine", return_value=mock_instance):
        score = await engine.resolve_fa_score("EUR/USD", "forex", mock_db)

    assert isinstance(score, float)
    assert score == pytest.approx(12.5)
    mock_instance.analyze.assert_called_once_with("EUR/USD")


@pytest.mark.asyncio
async def test_resolve_fa_score_forex_score_is_float(
    engine: SignalEngineV2, mock_db: MagicMock
) -> None:
    """resolve_fa_score returns float for any forex symbol."""
    mock_instance = AsyncMock()
    mock_instance.analyze = AsyncMock(return_value={"score": -5.3})

    with patch("src.analysis.forex_fa_engine.ForexFAEngine", return_value=mock_instance):
        score = await engine.resolve_fa_score("USD/JPY", "forex", mock_db)

    assert isinstance(score, float)
    assert score == pytest.approx(-5.3)


# ── Stocks routing ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_fa_score_stocks_calls_stock_fa_engine(
    engine: SignalEngineV2, mock_db: MagicMock
) -> None:
    """StockFAEngine.calculate_stock_fa_score() is called for stocks market."""
    mock_instance = AsyncMock()
    mock_instance.calculate_stock_fa_score = AsyncMock(
        return_value={"score": 18.0, "components": {}}
    )

    with patch("src.analysis.stock_fa_engine.StockFAEngine", return_value=mock_instance):
        score = await engine.resolve_fa_score("AAPL", "stocks", mock_db, instrument_id=42)

    assert isinstance(score, float)
    assert score == pytest.approx(18.0)
    mock_instance.calculate_stock_fa_score.assert_called_once_with(42)


@pytest.mark.asyncio
async def test_resolve_fa_score_stocks_without_instrument_id_returns_zero(
    engine: SignalEngineV2, mock_db: MagicMock
) -> None:
    """Stocks without instrument_id → graceful 0.0 (no crash)."""
    score = await engine.resolve_fa_score("AAPL", "stocks", mock_db, instrument_id=None)
    assert score == 0.0


# ── Crypto routing ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_fa_score_crypto_without_instrument_id_returns_zero(
    engine: SignalEngineV2, mock_db: MagicMock
) -> None:
    """Crypto without instrument_id → graceful 0.0."""
    score = await engine.resolve_fa_score("BTC/USDT", "crypto", mock_db, instrument_id=None)
    assert score == 0.0


@pytest.mark.asyncio
async def test_resolve_fa_score_crypto_calls_crypto_fa_engine(
    engine: SignalEngineV2, mock_db: MagicMock
) -> None:
    """CryptoFAEngine.analyze() is called for crypto market."""
    mock_instance = AsyncMock()
    mock_instance.analyze = AsyncMock(return_value={"score": 25.0, "components": {}})

    with patch("src.analysis.crypto_fa_engine.CryptoFAEngine", return_value=mock_instance):
        score = await engine.resolve_fa_score("BTC/USDT", "crypto", mock_db, instrument_id=7)

    assert isinstance(score, float)
    assert score == pytest.approx(25.0)
    mock_instance.analyze.assert_called_once_with(7, "BTC/USDT")


# ── Error handling ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_fa_score_returns_zero_on_exception(
    engine: SignalEngineV2, mock_db: MagicMock
) -> None:
    """resolve_fa_score swallows exceptions and returns 0.0 (graceful degradation)."""
    mock_instance = AsyncMock()
    mock_instance.analyze = AsyncMock(side_effect=ConnectionError("API timeout"))

    with patch("src.analysis.forex_fa_engine.ForexFAEngine", return_value=mock_instance):
        score = await engine.resolve_fa_score("EUR/USD", "forex", mock_db)

    assert score == 0.0
