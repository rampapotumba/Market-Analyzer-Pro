"""Tests for src.utils.circuit_breaker."""

import asyncio

import pytest

from src.utils.circuit_breaker import (
    CBState,
    CircuitBreaker,
    CircuitBreakerOpen,
)


async def _ok():
    return "ok"


async def _fail():
    raise RuntimeError("external failure")


class TestCircuitBreakerClosed:
    @pytest.mark.asyncio
    async def test_initial_state_closed(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        assert cb.state == CBState.CLOSED

    @pytest.mark.asyncio
    async def test_success_passes_through(self):
        cb = CircuitBreaker("test")
        result = await cb.call(_ok)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_failure_increments_count(self):
        cb = CircuitBreaker("test", failure_threshold=5)
        try:
            await cb.call(_fail)
        except RuntimeError:
            pass
        assert cb._failure_count == 1
        assert cb.state == CBState.CLOSED

    @pytest.mark.asyncio
    async def test_failure_re_raises(self):
        cb = CircuitBreaker("test")
        with pytest.raises(RuntimeError, match="external failure"):
            await cb.call(_fail)

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self):
        cb = CircuitBreaker("test", failure_threshold=5)
        for _ in range(2):
            try:
                await cb.call(_fail)
            except RuntimeError:
                pass
        await cb.call(_ok)
        assert cb._failure_count == 0


class TestCircuitBreakerOpen:
    @pytest.mark.asyncio
    async def test_opens_after_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=3, reset_timeout=999)
        for _ in range(3):
            try:
                await cb.call(_fail)
            except RuntimeError:
                pass
        assert cb.state == CBState.OPEN

    @pytest.mark.asyncio
    async def test_rejects_calls_when_open(self):
        cb = CircuitBreaker("test", failure_threshold=2, reset_timeout=999)
        for _ in range(2):
            try:
                await cb.call(_fail)
            except RuntimeError:
                pass
        with pytest.raises(CircuitBreakerOpen):
            await cb.call(_ok)

    @pytest.mark.asyncio
    async def test_is_open_property(self):
        cb = CircuitBreaker("test", failure_threshold=1, reset_timeout=999)
        try:
            await cb.call(_fail)
        except RuntimeError:
            pass
        assert cb.is_open is True


class TestCircuitBreakerHalfOpen:
    @pytest.mark.asyncio
    async def test_transitions_to_half_open_after_timeout(self):
        cb = CircuitBreaker("test", failure_threshold=1, reset_timeout=0.01)
        try:
            await cb.call(_fail)
        except RuntimeError:
            pass
        assert cb.state == CBState.OPEN

        await asyncio.sleep(0.02)  # wait for reset_timeout
        # Next call should move to HALF_OPEN and attempt
        result = await cb.call(_ok)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_half_open_returns_to_open_on_failure(self):
        cb = CircuitBreaker("test", failure_threshold=1, reset_timeout=0.01)
        try:
            await cb.call(_fail)
        except RuntimeError:
            pass
        await asyncio.sleep(0.02)

        try:
            await cb.call(_fail)
        except RuntimeError:
            pass
        assert cb.state == CBState.OPEN

    @pytest.mark.asyncio
    async def test_closes_after_success_threshold(self):
        cb = CircuitBreaker(
            "test", failure_threshold=1, reset_timeout=0.01, success_threshold=2
        )
        try:
            await cb.call(_fail)
        except RuntimeError:
            pass
        await asyncio.sleep(0.02)

        # First success — still HALF_OPEN
        await cb.call(_ok)
        assert cb.state == CBState.HALF_OPEN

        # Second success — CLOSED
        await cb.call(_ok)
        assert cb.state == CBState.CLOSED


class TestCircuitBreakerReset:
    @pytest.mark.asyncio
    async def test_manual_reset(self):
        cb = CircuitBreaker("test", failure_threshold=1, reset_timeout=999)
        try:
            await cb.call(_fail)
        except RuntimeError:
            pass
        assert cb.state == CBState.OPEN

        await cb.reset()
        assert cb.state == CBState.CLOSED
        assert cb._failure_count == 0

    @pytest.mark.asyncio
    async def test_stats(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        s = cb.stats()
        assert s["state"] == "closed"
        assert s["failure_count"] == 0
        assert s["name"] == "test"


class TestCircuitBreakerGuard:
    @pytest.mark.asyncio
    async def test_guard_decorator(self):
        cb = CircuitBreaker("test")

        @cb.guard
        async def my_func(x: int) -> int:
            return x * 2

        result = await my_func(5)
        assert result == 10

    @pytest.mark.asyncio
    async def test_guard_propagates_error(self):
        cb = CircuitBreaker("test")

        @cb.guard
        async def bad_func():
            raise ValueError("bad")

        with pytest.raises(ValueError):
            await bad_func()
