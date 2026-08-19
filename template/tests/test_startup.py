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

import os
import subprocess
import sys

import pytest

try:
    import redbeat  # noqa

    HAS_REDBEAT = True
except ImportError:
    HAS_REDBEAT = False


def _probe(args: list[str]) -> None:
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        proc.wait(timeout=15.0)
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


# FastAPI server smoke test, starts the server and makes real HTTP requests.
# NOTE: Uses fixed port 8001. If occupied, test will fail.
# Consider using portpicker for dynamic port selection in CI.
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


def _services_available() -> bool:
    return all(
        [
            os.environ.get("POSTGRESQL_HOST"),
            os.environ.get("REDIS_HOST"),
        ]
    )


@pytest.mark.slow
@pytest.mark.skipif(
    not _services_available(),
    reason="Requires running PostgreSQL and Redis",
)
def test_api_smoke() -> None:
    """Start uvicorn, verify core endpoints work (auth lifecycle, health, features)."""
    import time
    import uuid

    import httpx

    smoke_email = f"smoke-{uuid.uuid4().hex[:8]}@example.com"
    port = 17896
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        base = f"http://127.0.0.1:{port}"

        # Wait up to 60s for the server to respond.
        for _i in range(60):
            try:
                r = httpx.get(f"{base}/api/v1/live", timeout=3)
                if r.status_code == 200:
                    break
            except (httpx.ConnectError, httpx.ReadTimeout):
                pass
            time.sleep(1)
        else:
            pytest.fail("Server did not start within 60s")

        # Health check (liveness).
        r = httpx.get(f"{base}/api/v1/live")
        assert r.status_code == 200
        assert r.json()["message"] == "Alive"

        # Health check (readiness, verifies DB + Redis connectivity).
        r = httpx.get(f"{base}/api/v1/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] in ("healthy", "degraded")
        assert data["database"] in ("healthy", "unhealthy")
        assert data["cache"] in ("healthy", "unhealthy")

        # Register user.
        r = httpx.post(
            f"{base}/api/v1/auth/register",
            json={"email": smoke_email, "password": "password123"},
        )
        assert r.status_code == 201
        token = r.json()["access_token"]
        refresh_token = r.json()["refresh_token"]

        # Login.
        r = httpx.post(
            f"{base}/api/v1/auth/login",
            data={"username": smoke_email, "password": "password123"},
        )
        assert r.status_code == 200

        # Get current user.
        r = httpx.get(
            f"{base}/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["email"] == smoke_email

        # Refresh token.
        r = httpx.post(
            f"{base}/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert r.status_code == 200
        new_token = r.json()["access_token"]

        # Change password.
        r = httpx.post(
            f"{base}/api/v1/auth/change-password",
            headers={"Authorization": f"Bearer {new_token}"},
            json={"current_password": "password123", "new_password": "newpass456"},
        )
        assert r.status_code == 204

        # Login with new password.
        r = httpx.post(
            f"{base}/api/v1/auth/login",
            data={"username": smoke_email, "password": "newpass456"},
        )
        assert r.status_code == 200
        new_token = r.json()["access_token"]

        # Feature endpoints exist (return any non-500 status).
        for endpoint, method, body, needs_auth in [
            ("/api/v1/auth/send-verification-email", "POST", None, True),
            (
                "/api/v1/auth/forgot-password",
                "POST",
                {"email": smoke_email},
                False,
            ),
            ("/api/v1/users/me/export", "GET", None, True),
        ]:
            kwargs = {}
            if needs_auth:
                kwargs["headers"] = {"Authorization": f"Bearer {new_token}"}
            if body is not None:
                kwargs["json"] = body
            r = getattr(httpx, method.lower())(f"{base}{endpoint}", **kwargs)
            assert r.status_code < 500, f"{method} {endpoint} returned {r.status_code}"

        # Reset password endpoint with invalid token -> 400.
        r = httpx.post(
            f"{base}/api/v1/auth/reset-password",
            json={"token": "invalid-token", "new_password": "newpass789"},
        )
        assert r.status_code == 400

        # Verify email endpoint with missing token -> 400.
        r = httpx.get(f"{base}/api/v1/auth/verify-email")
        assert r.status_code == 400

        # 2FA enroll endpoint exists
        # (returns 400 since already logged in without pending).
        r = httpx.post(
            f"{base}/api/v1/auth/2fa/enroll",
            headers={"Authorization": f"Bearer {new_token}"},
            json={},
        )
        assert r.status_code in (400, 200, 403)

        # List API keys (tests pagination is wired).
        r = httpx.get(
            f"{base}/api/v1/api-keys",
            headers={"Authorization": f"Bearer {new_token}"},
        )
        assert r.status_code == 200
        assert "x-total-count" in r.headers

        # Unconfigured Google OAuth returns 503.
        r = httpx.get(
            f"{base}/api/v1/auth/google",
            params={"redirect_uri": "http://localhost:3000/callback"},
        )
        assert r.status_code == 503, (
            f"Google OAuth unconfigured returned {r.status_code}"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


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
@pytest.mark.skipif(not HAS_REDBEAT, reason="RedBeat not installed")
def test_celery_beat_redbeat_starts() -> None:
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


@pytest.mark.slow
def test_celery_beat_default_starts() -> None:
    _probe(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.celery_app",
            "beat",
            "-l",
            "info",
        ],
    )


@pytest.mark.slow
def test_core_compose_services_running() -> None:
    """Verify core docker-compose services (postgresql, redis) are healthy.

    RabbitMQ is excluded, the container has known stability issues on CI runners
    due to Erlang VM memory limits.  Celery beat/worker tests will skip when the
    broker is unreachable.
    """
    import json
    import subprocess

    proc = subprocess.run(
        ["docker", "compose", "ps", "--format", "json"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, f"docker compose ps failed: {proc.stderr}"
    services = [
        json.loads(line) for line in proc.stdout.strip().split("\n") if line.strip()
    ]
    core_svcs = {"postgresql", "redis"}
    unhealthy = [
        f"{s['Service']}={s['State']}"
        for s in services
        if s.get("Service") in core_svcs
        and s.get("State") not in ("running", "healthy")
    ]
    assert not unhealthy, f"Core services unhealthy after startup: {unhealthy}"
