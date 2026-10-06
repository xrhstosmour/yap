"""A span helper over whatever tracer provider FastAPI installed.

FastAPI 0.142 ships OpenTelemetry in its `standard` extra and instruments
itself: request and WebSocket spans, request and latency metrics, and logs
for validation failures and unhandled exceptions, all without application
code. Two environment variables point it somewhere, `OTEL_SERVICE_NAME` and
`OTEL_EXPORTER_OTLP_ENDPOINT`.

This module used to build its own `TracerProvider` and install it with
`trace.set_tracer_provider()`. That has to stay gone. The SDK guards that
call with a `Once` latch, so the first provider installed in a process wins
and every later call is ignored with a warning. Installing one here, and in
staging and production installing one with no exporter at all, would silence
every automatic span and metric in exactly the environments worth observing.

What is left is the one thing FastAPI does not do: naming a span around a
block of our own code. `get_tracer` resolves against the installed provider,
so this works with FastAPI's and needs no setup of its own.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from opentelemetry import trace


@contextmanager
def tracing(span_name: str) -> Generator[None]:
    """Create a traced span as a context manager.

    Args:
        span_name: Name for the new span

    Yields:
        None

    Raises:
        Captures and re-raises exceptions with error attributes on the span.
    """
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span(span_name) as span:
        try:
            yield
        except Exception as e:
            span.set_attribute("error", True)
            span.set_attribute("error.message", str(e))
            raise
