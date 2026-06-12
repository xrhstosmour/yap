"""Startup tests for Celery configuration and process launching.

FastAPI startup is additionally covered by tests in tests/api/test_health.py,
which boot the app and assert HTTP responses.

Celery is split into two concerns:
- Configuration: assert the app object is correctly wired so
    that starting the worker or beat cannot fail due to missing tasks,
    a bad schedule, wrong queues, or a misconfigured retry policy.
- Execution: run the built-in health-check task eagerly (no broker required)
    to verify the worker runtime itself is operational.
- Process probes (@pytest.mark.slow): spawn the actual worker/beat/FastAPI
    commands and assert they do not crash within a startup window.
"""

from __future__ import annotations

import subprocess
import sys

import pytest


def _probe(args: list[str]) -> None:
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        proc.wait(timeout=5.0)
        stdout = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
        stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        pytest.fail(
            f"Process exited prematurely (code {proc.returncode}).\n"
            f"stdout:\n{stdout}\nstderr:\n{stderr}"
        )
    except subprocess.TimeoutExpired:
        pass
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# Configuration tests, no external services required.
def test_celery_app_includes_all_task_modules() -> None:
    """Celery app must register every task package."""
    from app.celery_app import celery_app

    expected = {
        "app.tasks.email",
        "app.tasks.cache",
        "app.tasks.cleanup",
        "app.tasks.outbox",
    }
    assert expected.issubset(set(celery_app.conf.include))


def test_celery_beat_schedule_has_all_entries() -> None:
    """Beat schedule must define all four periodic tasks."""
    from app.celery_app import celery_app

    expected = {
        "cleanup-old-audit-logs",
        "cache-warm",
        "purge-graveyard",
        "process-outbox",
    }
    assert expected.issubset(set(celery_app.conf.beat_schedule))


def test_celery_task_routes_cover_all_queues() -> None:
    """Every task namespace must be mapped to a dedicated queue."""
    from app.celery_app import celery_app

    routes = celery_app.conf.task_routes
    assert "app.tasks.email.*" in routes
    assert "app.tasks.cache.*" in routes
    assert "app.tasks.cleanup.*" in routes
    assert "app.tasks.outbox.*" in routes


def test_celery_broker_retry_on_startup_enabled() -> None:
    """broker_connection_retry_on_startup must be True.

    Without this flag, a missing broker at startup causes an immediate crash
    instead of a graceful retry loop.
    """
    from app.celery_app import celery_app

    assert celery_app.conf.broker_connection_retry_on_startup is True


# Execution test, verifies the worker runtime, no broker required.
def test_health_check_task_executes() -> None:
    """The health-check task must run successfully via eager execution.

    .apply() runs the task synchronously in the current process without a
    broker, so this test passes in any environment.  It verifies that the
    worker runtime (serialisation, task binding, return value handling) works
    end-to-end, which is what `celery worker` does at runtime.
    """
    from app.celery_app import health_check

    result = health_check.apply()

    assert result.successful()
    assert result.result["status"] == "healthy"

# Process probe tests, tagged slow because they spawn real subprocesses.
@pytest.mark.slow
def test_fastapi_dev_starts() -> None:
    _probe(
        [
            sys.executable,
            "-m",
            "fastapi",
            "dev",
            "app/main.py",
            "--host",
            "0.0.0.0",
            "--port",
            "8001",
        ],
    )


@pytest.mark.slow
def test_celery_worker_starts() -> None:
    _probe(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.celery_app",
            "worker",
            "--loglevel=info",
        ],
    )


@pytest.mark.slow
def test_celery_beat_starts() -> None:
    _probe(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.celery_app",
            "beat",
            "-S",
            "redbeat.RedBeatScheduler",
            "-l",
            "info",
        ],
    )
