"""Tests for OpenTelemetry tracing setup and the tracing context manager."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
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

from app.core.telemetry import tracing


@pytest.fixture(autouse=True)
def _reset_tracer_provider():
    """Reset the global tracer provider around each test.

    The provider is process-global state. The SDK guards
    set_tracer_provider() with a `Once` latch that only allows it to be set
    a single time per process, so clearing the private slot alone is not
    enough: the latch itself must be replaced too, or every test after the
    first no-ops and silently keeps a stale provider left behind by an
    earlier test in the same worker process.

    That latch is also why the application must not install a provider of
    its own, which `TestNothingInstallsATracerProvider` below asserts.
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


class TestNothingInstallsATracerProvider:
    """FastAPI installs the provider. Nothing here may install another.

    `app/core/telemetry.py` used to build its own and call
    `trace.set_tracer_provider()`, and in staging and production it built one
    with no exporter at all. The SDK guards that call with a `Once` latch, so
    the first provider in a process wins: once FastAPI instruments itself,
    a second provider silences every automatic span and metric, in exactly
    the environments worth observing. This asserts the call is gone, rather
    than trusting that nobody adds it back.
    """

    def test_the_application_never_sets_a_provider(self) -> None:
        """Searched across the whole application, not just this module.

        Parsed rather than grepped. The first version of this matched the
        string anywhere and failed on the docstring in `telemetry.py` that
        explains why the call must not be there, which is the sort of false
        positive that gets a guard deleted rather than fixed.
        """
        root = Path(__file__).resolve().parents[2] / "app"
        offenders = []
        for source in sorted(root.rglob("*.py")):
            tree = ast.parse(source.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                target = node.func
                name = (
                    target.attr
                    if isinstance(target, ast.Attribute)
                    else getattr(target, "id", "")
                )
                if name == "set_tracer_provider":
                    offenders.append(f"{source.relative_to(root.parent)}:{node.lineno}")

        assert not offenders, (
            "set_tracer_provider wins a one-shot latch and would silence "
            f"FastAPI's own telemetry: {offenders}"
        )

    def test_no_setup_function_is_exported(self) -> None:
        """A `setup_tracing` that exists will eventually be called again."""
        import app.core.telemetry as telemetry

        assert not hasattr(telemetry, "setup_tracing")

    def test_the_span_helper_needs_no_setup(self, memory_exporter) -> None:
        """`tracing()` has to work against a provider it did not install.

        Args:
            memory_exporter: Fixture installing a provider and exporter.
        """
        with tracing("after-someone-else-installed-the-provider"):
            pass

        assert len(memory_exporter.get_finished_spans()) == 1
