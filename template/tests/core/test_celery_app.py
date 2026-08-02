"""Tests for Celery application configuration and tasks."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

# The celery_app module imports settings and task modules that may not exist
# in the template environment. Pre-populate sys.modules with stubs so the
# import succeeds after the template is rendered.

_mock_settings = MagicMock()
_mock_settings.CELERY_BROKER_URL = "amqp://guest:guest@localhost:5672//"
_mock_settings.CELERY_RESULT_BACKEND = "redis://localhost:6379/0"
_mock_settings.SSL_CERTIFICATE_PATH = None
_mock_settings.REDIS_URL = "redis://localhost:6379/0"
_mock_settings.CELERY_TASK_TIME_LIMIT = 300
_mock_settings.CELERY_TASK_SOFT_TIME_LIMIT = 240
_mock_settings.CELERY_WORKER_PREFETCH_MULTIPLIER = 4
_mock_settings.CELERY_RESULT_EXPIRES_SECONDS = 86400
_mock_settings.CELERY_CLEANUP_AUDIT_LOGS_HOUR = 3
_mock_settings.CELERY_CACHE_WARM_HOUR = 6
_mock_settings.CELERY_PURGE_GRAVEYARD_HOUR = 4

_stub_modules = {
    "app.tasks.email": MagicMock(),
    "app.tasks.cache": MagicMock(),
    "app.tasks.cleanup": MagicMock(),
    "app.tasks.outbox": MagicMock(),
    "app.tasks.storage": MagicMock(),
    "app.core.settings": MagicMock(settings=_mock_settings),
}

for _mod_name, _mod_value in _stub_modules.items():
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = _mod_value

try:
    from app.celery_app import celery_app  # noqa: E402
    from app.celery_app import health_check  # noqa: E402
except ImportError:
    celery_app = None  # type: ignore[assignment]
    health_check = None  # type: ignore[assignment]


@pytest.mark.skipif(
    celery_app is None, reason="celery_app module not available (template not rendered)"
)
class TestCeleryAppConfig:
    """Tests for the Celery application configuration."""

    def test_app_name_is_string(self) -> None:
        """The Celery app should have a non-empty name."""
        assert celery_app.main
        assert isinstance(celery_app.main, str)
        assert len(celery_app.main) > 0

    def test_broker_url_configured(self) -> None:
        """The Celery app should have a broker URL configured."""
        assert celery_app.conf.broker_url
        assert isinstance(celery_app.conf.broker_url, str)

    def test_result_backend_configured(self) -> None:
        """The Celery app should have a result backend configured."""
        assert celery_app.conf.result_backend
        assert isinstance(celery_app.conf.result_backend, str)

    def test_task_serializer_is_json(self) -> None:
        """Tasks should be serialized as JSON."""
        assert celery_app.conf.task_serializer == "json"

    def test_timezone_is_utc(self) -> None:
        """Celery should be configured for UTC timezone."""
        assert celery_app.conf.timezone == "UTC"
        assert celery_app.conf.enable_utc is True

    def test_task_time_limit_from_settings(self) -> None:
        """task_time_limit is threaded through from settings.CELERY_TASK_TIME_LIMIT."""
        assert celery_app.conf.task_time_limit == _mock_settings.CELERY_TASK_TIME_LIMIT

    def test_task_soft_time_limit_from_settings(self) -> None:
        """task_soft_time_limit comes from settings.CELERY_TASK_SOFT_TIME_LIMIT."""
        assert (
            celery_app.conf.task_soft_time_limit
            == _mock_settings.CELERY_TASK_SOFT_TIME_LIMIT
        )

    def test_worker_prefetch_multiplier_from_settings(self) -> None:
        """worker_prefetch_multiplier comes from settings.CELERY_WORKER_PREFETCH_MULTIPLIER."""
        assert (
            celery_app.conf.worker_prefetch_multiplier
            == _mock_settings.CELERY_WORKER_PREFETCH_MULTIPLIER
        )

    def test_result_expires_from_settings(self) -> None:
        """result_expires comes from settings.CELERY_RESULT_EXPIRES_SECONDS."""
        assert (
            celery_app.conf.result_expires
            == _mock_settings.CELERY_RESULT_EXPIRES_SECONDS
        )


@pytest.mark.skipif(
    celery_app is None, reason="celery_app module not available (template not rendered)"
)
class TestTaskRouting:
    """Tests for Celery task routing configuration."""

    def test_task_routes_configured(self) -> None:
        """Task routes should be defined for each worker queue."""
        routes = celery_app.conf.task_routes
        assert routes is not None
        assert isinstance(routes, dict)

    def test_email_tasks_routed_to_email_queue(self) -> None:
        """Email tasks should route to the 'email' queue."""
        routes = celery_app.conf.task_routes
        assert "app.tasks.email.*" in routes
        assert routes["app.tasks.email.*"]["queue"] == "email"

    def test_cache_tasks_routed_to_cache_queue(self) -> None:
        """Cache tasks should route to the 'cache' queue."""
        routes = celery_app.conf.task_routes
        assert "app.tasks.cache.*" in routes
        assert routes["app.tasks.cache.*"]["queue"] == "cache"

    def test_cleanup_tasks_routed_to_cleanup_queue(self) -> None:
        """Cleanup tasks should route to the 'cleanup' queue."""
        routes = celery_app.conf.task_routes
        assert "app.tasks.cleanup.*" in routes
        assert routes["app.tasks.cleanup.*"]["queue"] == "cleanup"

    def test_outbox_tasks_routed_to_events_queue(self) -> None:
        """Outbox tasks should route to the 'events' queue."""
        routes = celery_app.conf.task_routes
        assert "app.tasks.outbox.*" in routes
        assert routes["app.tasks.outbox.*"]["queue"] == "events"


@pytest.mark.skipif(
    celery_app is None, reason="celery_app module not available (template not rendered)"
)
class TestBeatSchedule:
    """Tests for the Celery beat schedule."""

    def test_beat_schedule_is_configured(self) -> None:
        """The beat schedule should be a non-empty dictionary."""
        schedule = celery_app.conf.beat_schedule
        assert schedule is not None
        assert isinstance(schedule, dict)
        assert len(schedule) > 0

    def test_cleanup_old_audit_logs_scheduled(self) -> None:
        """The cleanup-old-audit-logs periodic task should be configured."""
        schedule = celery_app.conf.beat_schedule
        assert "cleanup-old-audit-logs" in schedule
        entry = schedule["cleanup-old-audit-logs"]
        assert entry["task"] == "app.tasks.cleanup.cleanup_old_audit_logs"
        assert "schedule" in entry
        assert entry["schedule"].hour == {_mock_settings.CELERY_CLEANUP_AUDIT_LOGS_HOUR}

    def test_cache_warm_scheduled(self) -> None:
        """The cache-warm periodic task should be configured."""
        schedule = celery_app.conf.beat_schedule
        assert "cache-warm" in schedule
        entry = schedule["cache-warm"]
        assert entry["task"] == "app.tasks.cache.warm_cache"
        assert "schedule" in entry
        assert entry["schedule"].hour == {_mock_settings.CELERY_CACHE_WARM_HOUR}

    def test_purge_graveyard_scheduled(self) -> None:
        """The purge-graveyard periodic task should be configured."""
        schedule = celery_app.conf.beat_schedule
        assert "purge-graveyard" in schedule
        entry = schedule["purge-graveyard"]
        assert entry["task"] == "app.tasks.cleanup.purge_graveyard"
        assert "schedule" in entry
        assert entry["schedule"].hour == {_mock_settings.CELERY_PURGE_GRAVEYARD_HOUR}

    def test_process_outbox_scheduled(self) -> None:
        """The process-outbox periodic task should be configured."""
        schedule = celery_app.conf.beat_schedule
        assert "process-outbox" in schedule
        entry = schedule["process-outbox"]
        assert entry["task"] == "app.tasks.outbox.process_outbox"
        assert "schedule" in entry


@pytest.mark.skipif(
    celery_app is None, reason="celery_app module not available (template not rendered)"
)
class TestHealthCheck:
    """Tests for the health_check task."""

    def test_health_check_returns_healthy(self) -> None:
        """health_check() should return a dict with status 'healthy'."""
        result = health_check()
        assert isinstance(result, dict)
        assert result["status"] == "healthy"

    def test_health_check_includes_task_id(self) -> None:
        """health_check() should include the task_id in the response."""
        result = health_check()
        assert "task_id" in result


@pytest.mark.skipif(
    celery_app is None, reason="celery_app module not available (template not rendered)"
)
class TestSignalHandlers:
    """Smoke tests for Celery signal handler registration."""

    def test_task_failure_signal_registered(self) -> None:
        """A task_failure signal handler should be connected."""
        from celery.signals import task_failure

        # Signal.receivers is a list of connected handlers
        assert len(task_failure.receivers) > 0

    def test_worker_shutdown_signal_registered(self) -> None:
        """A worker_shutdown signal handler should be connected."""
        from celery.signals import worker_shutdown

        assert len(worker_shutdown.receivers) > 0
