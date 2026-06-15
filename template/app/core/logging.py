"""Structured logging configuration using structlog.

This module configures structlog for consistent, machine-parseable JSON logs
with correlation ID support for distributed tracing.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

import structlog
from structlog.typing import EventDict
from structlog.typing import Processor
from structlog.typing import WrappedLogger

from app.core.settings import settings

if TYPE_CHECKING:
    pass


def add_correlation_id(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Add correlation_id to log entries if not present.

    Checks for existing correlation ID in the event dict, and if not found,
    generates a new UUID. This ensures every log entry can be traced
    through the request lifecycle.

    Args:
        logger: The logging logger instance (unused but required by structlog)
        method_name: Name of the log method being called
        event_dict: The structured log event dictionary

    Returns:
        Updated event dictionary with correlation_id
    """
    if "correlation_id" not in event_dict:
        import uuid

        try:
            request_id = str(uuid.uuid7())
        except AttributeError:
            # Python < 3.14 fallback
            request_id = str(uuid.uuid4())
        event_dict["correlation_id"] = request_id
    return event_dict


def add_service_context(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Add service context to all log entries.

    Includes service name and environment in every log entry for
    filtering in log aggregation systems.

    Args:
        logger: The logging logger instance
        method_name: Name of the log method being called
        event_dict: The structured log event dictionary

    Returns:
        Updated event dictionary with service context
    """
    event_dict["service"] = settings.PROJECT_NAME
    event_dict["environment"] = settings.ENVIRONMENT
    return event_dict


def rename_event_key(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Rename 'event' key to 'message' for compatibility with log systems.

    Many log aggregation tools expect 'message' as the primary field.
    This processor standardizes the output format.

    Args:
        logger: The logging logger instance
        method_name: Name of the log method being called
        event_dict: The structured log event dictionary

    Returns:
        Updated event dictionary with 'message' key
    """
    if "event" in event_dict:
        event_dict["message"] = event_dict.pop("event")
    elif "_record" in event_dict:
        event_dict["message"] = event_dict.pop("_record").get("msg", "")
    return event_dict


def setup_logging(log_level: str = "INFO") -> None:
    """Configure structlog with JSON output and appropriate processors.

    Sets up structured logging with the following features:
    - JSON output format for log aggregation
    - Correlation ID propagation
    - Service context
    - Consistent timestamp formatting
    - Proper log level mapping

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
    """
    # Determine if we're in production (JSON) or development (pretty).
    is_production = settings.ENVIRONMENT in ("staging", "production")

    # Base processors for all environments.
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        add_correlation_id,
        add_service_context,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if is_production:
        # Production: JSON output for log aggregation systems.
        shared_processors.extend(
            [
                rename_event_key,
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ]
        )
    else:
        # Development: Pretty console output.
        shared_processors.extend(
            [
                structlog.dev.ConsoleRenderer(colors=True),
            ]
        )

    # Configure structlog.
    structlog.configure(
        processors=shared_processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Validate and normalize log level.
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if log_level.upper() not in valid_levels:
        logging.warning("Invalid LOG_LEVEL '%s', defaulting to INFO", log_level)
        log_level = "INFO"
    level = getattr(logging, log_level.upper())

    # Configure standard library logging.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    # Reduce noise from third-party libraries.
    for logger_name in [
        "uvicorn",
        "uvicorn.access",
        "uvicorn.error",
        "httpx",
        "httpcore",
    ]:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a configured structlog logger instance.

    Retrieves a logger with the standard configuration applied.
    Optionally include a module or component name for better filtering.

    Args:
        name: Optional name to include in log entries (e.g., module name)

    Returns:
        Configured structlog logger instance

    Example:
        logger = get_logger(__name__)
        logger.info("user_login", user_id="123", success=True)
    """
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)  # type: ignore
    return logger


# Note: Pre-configured module-level loggers are intentionally removed.
# Each module should call get_logger(__name__) itself. This ensures
# structlog is properly configured via setup_logging() before any
# logger is created, preventing loggers from being initialized with
# default (non-structured) configuration.
#
# Usage in other modules:
#   from app.core.logging import get_logger
#   logger = get_logger(__name__)
