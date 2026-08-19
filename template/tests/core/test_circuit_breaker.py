"""Tests for the circuit breaker utility."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock
from unittest.mock import patch

import pybreaker
import pytest

from app.core.circuit_breaker import CircuitBreakerError
from app.core.circuit_breaker import CircuitBreakerService
from app.core.circuit_breaker import CircuitState
from app.core.circuit_breaker import _CircuitBreakerLogger
from app.core.circuit_breaker import circuit_breaker


@pytest.fixture(autouse=True)
def _clear_circuit_breakers() -> None:
    """Clear circuit breaker registry between tests for isolation."""
    CircuitBreakerService._breakers.clear()
    yield
    CircuitBreakerService._breakers.clear()


#  CircuitBreakerService.get_breaker()


def test_get_breaker_returns_same_instance() -> None:
    """Same name returns the identical CircuitBreaker instance (singleton)."""
    breaker1 = CircuitBreakerService.get_breaker("test_service")
    breaker2 = CircuitBreakerService.get_breaker("test_service")

    assert breaker1 is breaker2


def test_get_breaker_different_names_different_instances() -> None:
    """Different names return distinct CircuitBreaker instances."""
    breaker1 = CircuitBreakerService.get_breaker("service_a")
    breaker2 = CircuitBreakerService.get_breaker("service_b")

    assert breaker1 is not breaker2


def test_get_breaker_default_parameters() -> None:
    """When no overrides are given, fail_max=5 and reset_timeout=60."""
    breaker = CircuitBreakerService.get_breaker("test")

    assert breaker.fail_max == 5
    assert breaker.reset_timeout == 60


def test_get_breaker_custom_parameters() -> None:
    """Custom fail_max and reset_timeout should be forwarded to pybreaker."""
    breaker = CircuitBreakerService.get_breaker("test", fail_max=10, reset_timeout=30)

    assert breaker.fail_max == 10
    assert breaker.reset_timeout == 30


def test_get_breaker_second_call_preserves_original_params() -> None:
    """A second call with different kwargs returns the cached breaker unchanged."""
    breaker1 = CircuitBreakerService.get_breaker("test", fail_max=10, reset_timeout=30)
    breaker2 = CircuitBreakerService.get_breaker("test", fail_max=99, reset_timeout=99)

    assert breaker1 is breaker2
    assert breaker2.fail_max == 10
    assert breaker2.reset_timeout == 30


#  CircuitBreakerService.get_state()


def test_get_state_unknown_service_returns_closed() -> None:
    """Querying state for an unregistered service returns CLOSED."""
    state = CircuitBreakerService.get_state("nonexistent")

    assert state == CircuitState.CLOSED


def test_get_state_returns_closed_initially() -> None:
    """A freshly created breaker starts in CLOSED state."""
    CircuitBreakerService.get_breaker("test", fail_max=5)

    assert CircuitBreakerService.get_state("test") == CircuitState.CLOSED


def test_get_state_returns_open_after_tripping() -> None:
    """After exceeding fail_max consecutive failures the circuit opens."""
    breaker = CircuitBreakerService.get_breaker("test", fail_max=1, reset_timeout=60)

    def _fail() -> None:
        raise ValueError("failure")

    # Cause enough failures to trip the breaker (fail_max + 1 attempts).
    for _ in range(2):
        try:
            breaker.call(_fail)
        except (ValueError, pybreaker.CircuitBreakerError):
            pass

    assert CircuitBreakerService.get_state("test") == CircuitState.OPEN


def test_get_state_returns_half_open_after_timeout() -> None:
    """After reset_timeout passes, an OPEN breaker transitions to HALF_OPEN on next call."""
    breaker = CircuitBreakerService.get_breaker("test", fail_max=1, reset_timeout=0.01)
    # Set success_threshold > 1 so the breaker stays in HALF_OPEN after one
    # successful post-timeout call instead of jumping straight to CLOSED.
    breaker.success_threshold = 2

    def _fail() -> None:
        raise ValueError("failure")

    # Trip the breaker.
    for _ in range(2):
        try:
            breaker.call(_fail)
        except (ValueError, pybreaker.CircuitBreakerError):
            pass

    # Verify it is OPEN immediately after tripping.
    assert CircuitBreakerService.get_state("test") == CircuitState.OPEN

    # Wait longer than reset_timeout so the breaker allows the next call
    # through the HALF_OPEN gate.
    time.sleep(0.05)

    # One successful call transitions OPEN → HALF_OPEN (but not yet CLOSED).
    breaker.call(lambda: "ok")

    assert CircuitBreakerService.get_state("test") == CircuitState.HALF_OPEN


#  CircuitBreakerError


def test_circuit_breaker_error_stores_service_and_state() -> None:
    """Exception stores the service name and circuit state it represents."""
    error = CircuitBreakerError("api_service", CircuitState.OPEN)

    assert error.service == "api_service"
    assert error.state == CircuitState.OPEN


@pytest.mark.parametrize(
    "service, state, expected_state_text",
    [
        ("api", CircuitState.OPEN, "open"),
        ("db", CircuitState.HALF_OPEN, "half_open"),
        ("cache", CircuitState.CLOSED, "closed"),
    ],
)
def test_circuit_breaker_error_message(
    service: str, state: CircuitState, expected_state_text: str
) -> None:
    """Exception message contains the service name and state value."""
    error = CircuitBreakerError(service, state)
    message = str(error)

    assert service in message
    assert expected_state_text in message


#  circuit_breaker decorator, sync functions


def test_circuit_breaker_sync_decorator_calls_function() -> None:
    """Sync decorated function returns the expected result."""

    @circuit_breaker("test_sync", fail_max=5)
    def double(x: int) -> int:
        return x * 2

    assert double(5) == 10


def test_circuit_breaker_sync_decorator_preserves_metadata() -> None:
    """Sync decorated function preserves __name__ and __doc__."""

    @circuit_breaker("test_meta", fail_max=5)
    def my_func(x: int) -> int:
        """Docstring for my_func."""
        return x

    assert my_func.__name__ == "my_func"
    assert my_func.__doc__ == "Docstring for my_func."


def test_circuit_breaker_sync_decorator_trips_breaker_on_failure() -> None:
    """After fail_max failures the circuit opens; next call raises CircuitBreakerError."""
    call_count = 0

    @circuit_breaker("test_sync_fail", fail_max=2)
    def my_func() -> None:
        nonlocal call_count
        call_count += 1
        raise ValueError("fail")

    # First call: original ValueError is propagated.
    with pytest.raises(ValueError, match="fail"):
        my_func()

    # Second call: this one reaches fail_max, so pybreaker wraps the
    # ValueError in a CircuitBreakerError to signal the circuit just opened.
    with pytest.raises(pybreaker.CircuitBreakerError):
        my_func()
    assert call_count == 2

    # Third call: circuit is already open, pybreaker blocks it immediately.
    with pytest.raises(pybreaker.CircuitBreakerError):
        my_func()

    assert call_count == 2  # Not called again
    assert CircuitBreakerService.get_state("test_sync_fail") == CircuitState.OPEN


def test_circuit_breaker_sync_decorator_kwargs_pass_through() -> None:
    """Decorator kwargs (fail_max, reset_timeout) configure the underlying breaker."""

    @circuit_breaker("test_kwargs", fail_max=3, reset_timeout=120)
    def my_func(x: int) -> int:
        return x

    breaker = CircuitBreakerService._breakers["test_kwargs"]
    assert breaker.fail_max == 3
    assert breaker.reset_timeout == 120


#  circuit_breaker decorator, async functions
#
# These exercise the real (non-mocked) pybreaker.CircuitBreaker, unlike the
# previous version of this section: mocking breaker.success()/breaker.failure()
# hid that neither method exists on this installed pybreaker version, so the
# async decorator raised AttributeError on every real call. See the
# `async_wrapper` docstring in app/core/circuit_breaker.py for the fix.


@pytest.mark.asyncio
async def test_circuit_breaker_async_decorator_calls_function() -> None:
    """Async decorated function returns the expected result and stays closed."""

    @circuit_breaker("test_async", fail_max=5)
    async def double(x: int) -> int:
        return x * 2

    assert await double(5) == 10
    assert CircuitBreakerService.get_state("test_async") == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_circuit_breaker_async_decorator_propagates_exception_below_threshold() -> (
    None
):
    """Below fail_max, the original exception propagates and the circuit stays closed."""

    @circuit_breaker("test_async_fail", fail_max=5)
    async def failing_func() -> None:
        raise ValueError("async fail")

    with pytest.raises(ValueError, match="async fail"):
        await failing_func()

    assert CircuitBreakerService.get_state("test_async_fail") == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_circuit_breaker_async_decorator_trips_breaker_on_failure() -> None:
    """After fail_max failures the circuit opens; next call raises CircuitBreakerError.

    Mirrors test_circuit_breaker_sync_decorator_trips_breaker_on_failure for
    the async path.
    """
    call_count = 0

    @circuit_breaker("test_async_trip", fail_max=2)
    async def my_func() -> None:
        nonlocal call_count
        call_count += 1
        raise ValueError("fail")

    # First call: original ValueError is propagated.
    with pytest.raises(ValueError, match="fail"):
        await my_func()

    # Second call: this one reaches fail_max, so pybreaker wraps the
    # ValueError in a CircuitBreakerError to signal the circuit just opened.
    with pytest.raises(pybreaker.CircuitBreakerError):
        await my_func()
    assert call_count == 2

    # Third call: circuit is already open, blocked without calling my_func.
    with pytest.raises(pybreaker.CircuitBreakerError):
        await my_func()

    assert call_count == 2
    assert CircuitBreakerService.get_state("test_async_trip") == CircuitState.OPEN


@pytest.mark.asyncio
async def test_circuit_breaker_async_decorator_half_open_after_timeout() -> None:
    """After reset_timeout passes, an OPEN async breaker allows one trial call through."""
    breaker = CircuitBreakerService.get_breaker(
        "test_async_half_open", fail_max=1, reset_timeout=0.01
    )
    # success_threshold > 1 so the breaker stays HALF_OPEN after one success
    # instead of jumping straight to CLOSED.
    breaker.success_threshold = 2

    @circuit_breaker("test_async_half_open", fail_max=1, reset_timeout=0.01)
    async def flaky(*, should_fail: bool) -> str:
        if should_fail:
            raise ValueError("failure")
        return "ok"

    # Trip the breaker (fail_max=1, so a single failure opens it and
    # pybreaker wraps it in a CircuitBreakerError instead of propagating
    # the original ValueError).
    with pytest.raises(pybreaker.CircuitBreakerError):
        await flaky(should_fail=True)

    assert CircuitBreakerService.get_state("test_async_half_open") == CircuitState.OPEN

    # Wait longer than reset_timeout so the breaker allows the next call
    # through the HALF_OPEN gate.
    await asyncio.sleep(0.05)

    result = await flaky(should_fail=False)

    assert result == "ok"
    assert (
        CircuitBreakerService.get_state("test_async_half_open")
        == CircuitState.HALF_OPEN
    )


@pytest.mark.asyncio
async def test_circuit_breaker_async_decorator_preserves_metadata() -> None:
    """Async decorated function preserves __name__ and __doc__."""

    @circuit_breaker("test_meta_async", fail_max=5)
    async def my_func(x: int) -> int:
        """Async docstring."""
        return x

    assert my_func.__name__ == "my_func"
    assert my_func.__doc__ == "Async docstring."


#  _CircuitBreakerLogger


def test_circuit_breaker_logger_state_change() -> None:
    """state_change handler logs a warning with transition details."""
    breaker = CircuitBreakerService.get_breaker("test_logger", fail_max=5)
    listener = _CircuitBreakerLogger("test_service")

    old = pybreaker.CircuitClosedState(breaker, 0)
    new = pybreaker.CircuitOpenState(breaker, 0)

    with patch("app.core.circuit_breaker.logger") as mock_logger:
        listener.state_change(breaker, old, new)

        mock_logger.warning.assert_called_once_with(
            "circuit_breaker_state_change",
            service="test_service",
            from_state="closed",
            to_state="open",
        )


def test_circuit_breaker_logger_state_change_initial() -> None:
    """When old_state is None (initial transition), from_state logs as None."""
    breaker = CircuitBreakerService.get_breaker("test_logger", fail_max=5)
    listener = _CircuitBreakerLogger("test_service")

    new = pybreaker.CircuitClosedState(breaker, 0)

    with patch("app.core.circuit_breaker.logger") as mock_logger:
        listener.state_change(breaker, None, new)

        mock_logger.warning.assert_called_once_with(
            "circuit_breaker_state_change",
            service="test_service",
            from_state=None,
            to_state="closed",
        )


def test_circuit_breaker_logger_failure() -> None:
    """failure handler logs a warning with error details and fail count."""
    listener = _CircuitBreakerLogger("test_service")
    mock_breaker = MagicMock()
    mock_breaker.fail_counter = 3

    exc = ValueError("test error")

    with patch("app.core.circuit_breaker.logger") as mock_logger:
        listener.failure(mock_breaker, exc)

        mock_logger.warning.assert_called_once_with(
            "circuit_breaker_failure",
            service="test_service",
            error="test error",
            fail_count=3,
        )
