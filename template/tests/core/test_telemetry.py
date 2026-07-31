"""Tests for OpenTelemetry tracing setup and the tracing context manager."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# tests/core/test_database.py stubs sys.modules["app.core.telemetry"] with a
# bare MagicMock() at collection time (as a defensive placeholder for when
# the template hasn't been rendered yet), guarded by `if not already in
# sys.modules`. Because that file collects alphabetically before this one,
# it can win the race and leave the fake module cached here, silently
# breaking every import below. Evict any such stub so the imports below
# resolve the real module.
if isinstance(sys.modules.get("app.core.telemetry"), MagicMock):
    del sys.modules["app.core.telemetry"]

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.util._once import Once

from app.core.telemetry import setup_tracing
from app.core.telemetry import tracing


@pytest.fixture(autouse=True)
def _reset_tracer_provider():
    """Reset the global tracer provider around each test.

    setup_tracing() mutates process-global OpenTelemetry state. The SDK
    guards set_tracer_provider() with a `Once` latch that only allows the
    provider to be set a single time per process, so clearing the private
    slot alone is not enough: the latch itself must be replaced too, or
    every test after the first no-ops and silently keeps a stale provider
    left behind by an earlier test in the same worker process.
    """
    trace._TRACER_PROVIDER = None
    trace._TRACER_PROVIDER_SET_ONCE = Once()
    yield
    trace._TRACER_PROVIDER = None
    trace._TRACER_PROVIDER_SET_ONCE = Once()


@pytest.fixture
def memory_exporter():
    """Wire an in-memory span exporter into the global tracer provider.

    Lets tests assert on the spans produced by tracing() without a real
    OTLP/console backend.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield exporter
    exporter.clear()


def test_setup_tracing_returns_tracer() -> None:
    """setup_tracing() should return a usable Tracer instance."""
    tracer = setup_tracing(service_name="test-service")

    assert isinstance(tracer, trace.Tracer)


def test_setup_tracing_sets_service_name_resource() -> None:
    """setup_tracing() should register a TracerProvider with the given service name."""
    setup_tracing(service_name="my-custom-service")

    provider = trace.get_tracer_provider()
    resource = provider.resource  # type: ignore[union-attr]

    assert resource.attributes["service.name"] == "my-custom-service"


def test_setup_tracing_defaults_service_name() -> None:
    """setup_tracing() should default to 'fastapi-app' when no name is given."""
    setup_tracing()

    provider = trace.get_tracer_provider()
    resource = provider.resource  # type: ignore[union-attr]

    assert resource.attributes["service.name"] == "fastapi-app"


def test_tracing_creates_span_with_given_name(memory_exporter) -> None:
    """tracing() should create a span named after the provided span_name."""
    with tracing("my_operation"):
        pass

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "my_operation"


def test_tracing_span_has_no_error_attribute_on_success(memory_exporter) -> None:
    """A span for a block that completes normally should not carry error attributes."""
    with tracing("clean_operation"):
        pass

    spans = memory_exporter.get_finished_spans()
    assert spans[0].attributes.get("error") is None


def test_tracing_reraises_exception(memory_exporter) -> None:
    """tracing() should re-raise exceptions raised inside the block."""
    with pytest.raises(ValueError, match="boom"):
        with tracing("failing_operation"):
            raise ValueError("boom")


def test_tracing_sets_error_attributes_on_exception(memory_exporter) -> None:
    """tracing() should record error and error.message attributes on failure."""
    with pytest.raises(RuntimeError):
        with tracing("failing_operation"):
            raise RuntimeError("something broke")

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].attributes["error"] is True
    assert spans[0].attributes["error.message"] == "something broke"
