"""Tests for Phase 3.1.4 — Instrument-aware news filtering."""

import datetime
from decimal import Decimal
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.database.crud import _get_instrument_keywords, get_news_events_for_instrument


# ── _get_instrument_keywords ──────────────────────────────────────────────────


def test_forex_eurusd_keywords() -> None:
    """EURUSD maps to EUR and USD keyword lists including central bank names."""
    keywords = _get_instrument_keywords("EUR/USD", "forex")
    combined = " ".join(keywords).lower()
    assert "eur" in combined
    assert "ecb" in combined or "european central bank" in combined


def test_forex_usdjpy_keywords() -> None:
    """USDJPY maps to USD and JPY keywords."""
    keywords = _get_instrument_keywords("USD/JPY", "forex")
    combined = " ".join(keywords).lower()
    assert "jpy" in combined or "yen" in combined
    assert "usd" in combined or "dollar" in combined


def test_crypto_btcusdt_keywords() -> None:
    """BTC/USDT maps to bitcoin/BTC keywords."""
    keywords = _get_instrument_keywords("BTC/USDT", "crypto")
    combined = " ".join(keywords).lower()
    assert "bitcoin" in combined or "btc" in combined


def test_crypto_eth_keywords() -> None:
    """ETH maps to ethereum keywords."""
    keywords = _get_instrument_keywords("ETH/USDT", "crypto")
    combined = " ".join(keywords).lower()
    assert "ethereum" in combined or "eth" in combined


def test_stock_aapl_keywords() -> None:
    """AAPL stock maps to its ticker."""
    keywords = _get_instrument_keywords("AAPL", "stocks")
    assert "AAPL" in keywords


def test_commodity_gold_keywords() -> None:
    """XAUUSD maps to gold/XAU keywords."""
    keywords = _get_instrument_keywords("XAUUSD", "commodities")
    combined = " ".join(keywords).lower()
    assert "gold" in combined or "xau" in combined


def test_unknown_market_returns_symbol() -> None:
    """Unknown market type returns a list with the symbol itself."""
    keywords = _get_instrument_keywords("CUSTOM", "unknown")
    assert len(keywords) >= 1


# ── get_news_events_for_instrument ────────────────────────────────────────────


def _make_news(headline: str, importance: str = "medium") -> MagicMock:
    item = MagicMock()
    item.id = id(headline)
    item.headline = headline
    item.summary = None
    item.importance = importance
    item.published_at = datetime.datetime.now(datetime.timezone.utc)
    return item


@pytest.mark.asyncio
async def test_returns_instrument_specific_news() -> None:
    """Function queries with instrument keywords and returns matching rows."""
    mock_session = AsyncMock()

    eur_news = _make_news("ECB raises interest rates")
    eur_news.id = 1

    # Simulate execute returning EUR news
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [eur_news]
    mock_session.execute = AsyncMock(return_value=mock_result)

    rows = await get_news_events_for_instrument(
        mock_session, symbol="EUR/USD", market="forex", limit=10, hours_back=24
    )
    assert len(rows) >= 1
    # execute was called (at least once for main query)
    mock_session.execute.assert_called()


@pytest.mark.asyncio
async def test_fallback_adds_macro_news_when_few_results() -> None:
    """When instrument-specific results < fallback_limit, macro news is added."""
    mock_session = AsyncMock()

    # First call returns 2 instrument-specific results (< fallback_limit=5)
    specific_news = [_make_news("EUR ECB news"), _make_news("EUR bullish")]
    for i, n in enumerate(specific_news):
        n.id = i + 100

    macro_news = [_make_news("High impact macro event", importance="high")]
    macro_news[0].id = 999

    call_count = 0

    async def mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.scalars.return_value.all.return_value = specific_news
        else:
            result.scalars.return_value.all.return_value = macro_news
        return result

    mock_session.execute = mock_execute

    rows = await get_news_events_for_instrument(
        mock_session,
        symbol="EUR/USD",
        market="forex",
        limit=30,
        hours_back=24,
        fallback_limit=5,
    )
    # Should have 2 specific + 1 macro = 3 total
    assert len(rows) == 3
    assert call_count == 2  # main query + fallback query


@pytest.mark.asyncio
async def test_no_fallback_when_enough_results() -> None:
    """When enough instrument-specific results, no fallback query is issued."""
    mock_session = AsyncMock()

    many_news = [_make_news(f"EUR news {i}") for i in range(10)]
    for i, n in enumerate(many_news):
        n.id = i

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = many_news
    mock_session.execute = AsyncMock(return_value=mock_result)

    rows = await get_news_events_for_instrument(
        mock_session,
        symbol="EUR/USD",
        market="forex",
        limit=30,
        hours_back=24,
        fallback_limit=5,
    )
    assert len(rows) == 10
    # Only 1 execute call — no fallback needed
    assert mock_session.execute.call_count == 1
