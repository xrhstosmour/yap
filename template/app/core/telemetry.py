"""OpenTelemetry tracing setup for distributed tracing.

Provides tracer configuration and a context manager for
creating spans within the application.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.export import ConsoleSpanExporter


def setup_tracing(service_name: str = "fastapi-app") -> trace.Tracer:
    """Initialize OpenTelemetry tracing with console exporter.

    Args:
        service_name: Name of this service for span attribution

    Returns:
        Configured tracer instance
    """
    resource = Resource.create({SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)

    return trace.get_tracer(__name__)


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
