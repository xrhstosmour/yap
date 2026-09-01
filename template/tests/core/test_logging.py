"""Tests for structured logging configuration."""

from __future__ import annotations

import logging
from unittest.mock import patch

from app.core.logging import add_correlation_id
from app.core.logging import get_logger
from app.core.logging import rename_event_key
from app.core.logging import setup_logging


class TestGetLogger:
    """Tests for get_logger()."""

    def test_returns_structlog_bound_logger(self) -> None:
        """get_logger() should return a structlog-compatible logger."""
        logger = get_logger("test")
        # May be a BoundLoggerLazyProxy before configuration; must be a structlog logger.
        assert hasattr(logger, "info")
        assert callable(logger.info)

    def test_returns_logger_with_correct_name(self) -> None:
        """The returned logger should carry the requested name."""
        logger = get_logger("my.module")
        assert "my.module" in str(logger)


class TestSetupLogging:
    """Tests for setup_logging()."""

    def test_called_with_different_log_levels(self) -> None:
        """setup_logging() should accept DEBUG, INFO, WARNING, ERROR, CRITICAL."""
        for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            with patch("structlog.configure") as mock_configure:
                setup_logging(log_level=level)
                mock_configure.assert_called()

    def test_invalid_log_level_defaults_to_info(self) -> None:
        """setup_logging() with an invalid level should warn and fall back to INFO."""
        with patch("structlog.configure") as mock_configure:
            with patch("logging.warning") as mock_warning:
                setup_logging(log_level="INVALID")
                mock_warning.assert_called_once()
                mock_configure.assert_called()

    def test_pre_configured_loggers_are_suppressed(self) -> None:
        """Third-party loggers should be set to WARNING after setup."""
        # Capture state before setup for a specific logger
        with patch("structlog.configure"):
            setup_logging(log_level="INFO")

        for name in ("uvicorn", "uvicorn.access", "httpx", "httpcore"):
            lgr = logging.getLogger(name)
            assert lgr.level == logging.WARNING

    def test_pre_configured_loggers_are_suppressed_warning(self) -> None:
        """Pre-configured loggers should be set to WARNING after setup."""
        with patch("structlog.configure"):
            setup_logging(log_level="INFO")

        assert logging.getLogger("uvicorn.access").level == logging.WARNING
        assert logging.getLogger("httpx").level == logging.WARNING


class TestCorrelationId:
    """Tests for add_correlation_id processor."""

    def test_adds_correlation_id_when_missing(self) -> None:
        """When correlation_id is not in event_dict, a UUID should be added."""
        event_dict: dict = {}
        result = add_correlation_id(None, "info", event_dict)  # type: ignore[arg-type]
        assert "correlation_id" in result
        assert len(result["correlation_id"]) > 0

    def test_preserves_existing_correlation_id(self) -> None:
        """An existing correlation_id should not be overwritten."""
        event_dict: dict = {"correlation_id": "existing-123"}
        result = add_correlation_id(None, "info", event_dict)  # type: ignore[arg-type]
        assert result["correlation_id"] == "existing-123"

    def test_generates_uuid_format(self) -> None:
        """The generated correlation_id should be a valid UUID string."""
        event_dict: dict = {}
        result = add_correlation_id(None, "info", event_dict)  # type: ignore[arg-type]
        correlation_id = result["correlation_id"]
        # UUID format: 8-4-4-4-12 hex characters separated by hyphens
        parts = correlation_id.split("-")
        assert len(parts) == 5


class TestRenameEventKey:
    """Tests for rename_event_key processor."""

    def test_renames_event_to_message(self) -> None:
        """'event' key should be renamed to 'message'."""
        event_dict: dict = {"event": "user_login", "user": "alice"}
        result = rename_event_key(None, "info", event_dict)  # type: ignore[arg-type]
        assert "event" not in result
        assert result["message"] == "user_login"
        assert result["user"] == "alice"

    def test_no_event_key_leaves_dict_unchanged(self) -> None:
        """If 'event' is not present, the dict should be unchanged."""
        event_dict: dict = {"other": "value"}
        result = rename_event_key(None, "info", event_dict)  # type: ignore[arg-type]
        assert result == {"other": "value"}


class TestLoggingIsConfiguredBeforeAnythingLogs:
    """`setup_logging()` has to run before the first log call.

    `app/core/logging.py` says so in as many words, "This ensures structlog
    is properly configured via setup_logging() before any logger is
    created". The lifespan handler broke it by one line: it logged
    `application_starting` first, so in production that line came out as
    coloured console text in the middle of the JSON stream, with no
    correlation ID and no service context.
    """

    def test_the_lifespan_configures_logging_first(self) -> None:
        """Whatever else the lifespan does, logging is set up before it logs.

        Walks the parsed function rather than the file text, so a comment
        or docstring mentioning either call cannot satisfy the check.
        """
        import ast
        from pathlib import Path

        source = (Path(__file__).resolve().parents[2] / "app" / "main.py").read_text()
        lifespan = next(
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan"
        )

        setup_at: int | None = None
        first_log_at: int | None = None
        for node in ast.walk(lifespan):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if isinstance(function, ast.Name) and function.id == "setup_logging":
                setup_at = min(setup_at or node.lineno, node.lineno)
            if (
                isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Name)
                and function.value.id == "logger"
            ):
                first_log_at = min(first_log_at or node.lineno, node.lineno)

        assert setup_at is not None, "lifespan never calls setup_logging()"
        assert first_log_at is not None, "expected the lifespan to log something"
        assert setup_at < first_log_at, (
            "lifespan logs before setup_logging(), so that line bypasses the "
            "configured renderer"
        )
