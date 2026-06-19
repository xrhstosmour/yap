"""Circuit breaker implementation for external API integrations.

This module provides circuit breaker pattern implementation using pybreaker
to prevent cascading failures when external services are unavailable.
"""

from __future__ import annotations

import asyncio
import functools
import threading
from collections.abc import Callable
from enum import StrEnum
from typing import Any
from typing import TypeVar
from typing import cast

import pybreaker
from pybreaker import CircuitBreaker
from pybreaker import CircuitBreakerListener

from app.core.logging import get_logger

logger = get_logger(__name__)


class CircuitState(StrEnum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open."""

    def __init__(self, service: str, state: CircuitState) -> None:
        self.service = service
        self.state = state
        super().__init__(f"Circuit breaker for {service} is {state.value}")


class CircuitBreakerService:
    """Manages circuit breakers for external services."""

    _breakers: dict[str, CircuitBreaker] = {}
    _lock = threading.Lock()

    @classmethod
    def get_breaker(
        cls,
        name: str,
        fail_max: int = 5,
        reset_timeout: int = 60,
    ) -> CircuitBreaker:
        """Get or create a circuit breaker for a service.

        Args:
            name: Name of the service
            fail_max: Number of failures before opening circuit
            reset_timeout: Seconds before attempting recovery

        Returns:
            Configured circuit breaker instance
        """
        if name not in cls._breakers:
            with cls._lock:
                if name not in cls._breakers:
                    cls._breakers[name] = pybreaker.CircuitBreaker(
                        fail_max=fail_max,
                        reset_timeout=reset_timeout,
                        listeners=[_CircuitBreakerLogger(name)],
                    )
        return cls._breakers[name]

    @classmethod
    def get_state(cls, name: str) -> CircuitState:
        """Get current state of a circuit breaker."""
        if name not in cls._breakers:
            return CircuitState.CLOSED
        breaker = cls._breakers[name]
        if breaker.current_state == pybreaker.STATE_OPEN:
            return CircuitState.OPEN
        if breaker.current_state == pybreaker.STATE_HALF_OPEN:
            return CircuitState.HALF_OPEN
        return CircuitState.CLOSED


class _CircuitBreakerLogger(CircuitBreakerListener):
    """Logs circuit breaker state changes."""

    def __init__(self, service_name: str) -> None:
        self.service_name = service_name

    def state_change(
        self,
        breaker: CircuitBreaker,
        old_state: pybreaker.CircuitBreakerState | None,
        new_state: pybreaker.CircuitBreakerState,
    ) -> None:
        """Log state transitions."""
        logger.warning(
            "circuit_breaker_state_change",
            service=self.service_name,
            from_state=old_state.name if old_state is not None else None,
            to_state=new_state.name,
        )

    def failure(self, breaker: CircuitBreaker, exception: BaseException) -> None:
        """Log failures."""
        logger.warning(
            "circuit_breaker_failure",
            service=self.service_name,
            error=str(exception),
            fail_count=breaker.fail_counter,
        )


F = TypeVar("F", bound=Callable[..., Any])


def circuit_breaker(name: str, **kwargs: int) -> Callable[[F], F]:
    """Decorator to apply circuit breaker to a function.

    Args:
        name: Service name for the circuit breaker
        **kwargs: Additional circuit breaker parameters

    Returns:
        Circuit breaker decorator
    """
    breaker = CircuitBreakerService.get_breaker(name, **kwargs)

    def decorator(func: F) -> F:
        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(  # noqa: ANN401
                *args: Any,  # noqa: ANN401
                **kwargs: Any,  # noqa: ANN401
            ) -> Any:  # noqa: ANN401
                try:
                    result = await func(*args, **kwargs)
                    breaker.success()  # type: ignore[attr-defined]
                    return result
                except Exception as e:
                    breaker.failure(e)  # type: ignore[attr-defined]
                    raise

            return cast(F, async_wrapper)
        else:
            return cast(F, breaker(func))

    return decorator
