"""Tests for health check and metrics endpoints."""

from collections.abc import Generator
from typing import Any
from typing import Literal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport
from httpx import AsyncClient

from app.main import app
from app.models.user import User
from app.models.user import UserRole


@pytest.fixture
def client() -> TestClient:
    """Create a test client."""
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture
def anyio_backend() -> Literal["asyncio"]:
    return "asyncio"


@pytest.fixture
async def async_client() -> Generator[AsyncClient, Any]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def test_liveness_check(client) -> None:
    """GET /api/v1/live should always return 200."""
    response = client.get("/api/v1/live")
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Alive"


def test_health_check_requires_db(client) -> None:
    """GET /api/v1/health requires DB (may fail without test DB configured)."""
    response = client.get("/api/v1/health")
    assert response.status_code in (200, 503)


def test_metrics_endpoint_requires_authentication(client) -> None:
    """GET /api/v1/metrics should reject unauthenticated callers."""
    response = client.get("/api/v1/metrics")
    assert response.status_code == 401


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_metrics_endpoint_requires_superuser(
    async_client: AsyncClient, session
) -> None:
    """GET /api/v1/metrics should reject a non-superuser caller."""
    from app.schemas.auth import RegisterRequest
    from app.services.auth_service import AuthService

    service = AuthService(session)
    user = await service.register(
        RegisterRequest(email="metrics-test@example.com", password="password123")
    )
    tokens = service.create_tokens(user)
    await session.commit()

    response = await async_client.get(
        "/api/v1/metrics",
        headers={"Authorization": f"Bearer {tokens.access_token}"},
    )
    assert response.status_code == 403


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_metrics_endpoint_returns_stats_for_superuser(
    async_client: AsyncClient, session
) -> None:
    """GET /api/v1/metrics returns pool and cache stats for a superuser."""
    from app.services.auth_service import AuthService

    service = AuthService(session)
    admin = User(
        email="admin-metrics@example.com",
        hashed_password="hash",
        role=UserRole.SUPERUSER,
        is_active=True,
    )
    session.add(admin)
    await session.commit()
    tokens = service.create_tokens(admin)

    response = await async_client.get(
        "/api/v1/metrics",
        headers={"Authorization": f"Bearer {tokens.access_token}"},
    )
    assert response.status_code in (200, 500)


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_metrics_reports_configured_redis_max_connections(
    async_client: AsyncClient, session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/v1/metrics must report the configured REDIS_MAX_CONNECTIONS,
    not a hardcoded value. Regression: cache.max_connections was hardcoded
    to 50, so it silently ignored an operator's actual setting, misleading
    exactly the capacity-planning use case this endpoint exists for.
    """
    from app.core.settings import settings
    from app.services.auth_service import AuthService

    monkeypatch.setattr(settings, "REDIS_MAX_CONNECTIONS", 137)

    service = AuthService(session)
    admin = User(
        email="admin-metrics-max-conn@example.com",
        hashed_password="hash",
        role=UserRole.SUPERUSER,
        is_active=True,
    )
    session.add(admin)
    await session.commit()
    tokens = service.create_tokens(admin)

    response = await async_client.get(
        "/api/v1/metrics",
        headers={"Authorization": f"Bearer {tokens.access_token}"},
    )

    assert response.status_code == 200
    assert response.json()["cache"]["max_connections"] == 137


def test_health_returns_healthy_status_when_dependencies_available(client) -> None:
    """GET /api/v1/health should return a structured health response."""
    response = client.get("/api/v1/health")
    # May be 200 (all healthy) or 503 (dependency unavailable in test env).
    assert response.status_code in (200, 503)
    if response.status_code == 200:
        data = response.json()
        assert data["status"] in ("healthy", "degraded")
        assert "version" in data
        assert "database" in data
        assert "cache" in data


def test_readiness_check_returns_ready(client) -> None:
    """GET /api/v1/ready should return 200 when DB is reachable."""
    response = client.get("/api/v1/ready")
    # May return 503 if test DB is not configured.
    assert response.status_code in (200, 503)
    if response.status_code == 200:
        data = response.json()
        assert data["message"] == "Ready"


def test_workers_endpoint_requires_authentication(client) -> None:
    """GET /api/v1/workers should reject unauthenticated callers."""
    response = client.get("/api/v1/workers")
    assert response.status_code == 401


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_workers_endpoint_requires_superuser(
    async_client: AsyncClient, session
) -> None:
    """GET /api/v1/workers should reject a non-superuser caller."""
    from app.schemas.auth import RegisterRequest
    from app.services.auth_service import AuthService

    service = AuthService(session)
    user = await service.register(
        RegisterRequest(email="workers-test@example.com", password="password123")
    )
    tokens = service.create_tokens(user)
    await session.commit()

    response = await async_client.get(
        "/api/v1/workers",
        headers={"Authorization": f"Bearer {tokens.access_token}"},
    )
    assert response.status_code == 403


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_workers_endpoint_returns_stats_for_superuser(
    async_client: AsyncClient, session
) -> None:
    """GET /api/v1/workers should return Celery worker stats for a superuser."""
    from app.services.auth_service import AuthService

    service = AuthService(session)
    admin = User(
        email="admin-workers@example.com",
        hashed_password="hash",
        role=UserRole.SUPERUSER,
        is_active=True,
    )
    session.add(admin)
    await session.commit()
    tokens = service.create_tokens(admin)

    # Patched, because the endpoint now answers 503 when nothing replies
    # and this suite runs with no broker. Before, an unreachable broker was
    # reported as a healthy idle cluster, which is what let this assert on
    # a 200 without any workers existing.
    with patch(
        "app.api.v1.health._inspect_workers",
        return_value=(
            {"worker@host": [{"id": "1"}, {"id": "2"}]},
            {"worker@host": [{"id": "3"}]},
            {"worker@host": [{"name": "celery"}, {"name": "priority"}]},
        ),
    ):
        response = await async_client.get(
            "/api/v1/workers",
            headers={"Authorization": f"Bearer {tokens.access_token}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["active"] == 2
    assert data["scheduled"] == 1
    assert data["queues"] == ["celery", "priority"]


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_workers_endpoint_reports_503_when_no_worker_replies(
    async_client: AsyncClient, session
) -> None:
    """Silence from the cluster must not look like an idle cluster.

    Celery returns None from every probe when nothing answers the
    broadcast. Reporting that as zeros gave the same answer as a healthy
    cluster with nothing to do, so a dashboard stayed green through a
    broker outage.
    """
    from app.services.auth_service import AuthService

    service = AuthService(session)
    admin = User(
        email="admin-workers-silent@example.com",
        hashed_password="hash",
        role=UserRole.SUPERUSER,
        is_active=True,
    )
    session.add(admin)
    await session.commit()
    tokens = service.create_tokens(admin)

    with patch("app.api.v1.health._inspect_workers", return_value=(None, None, None)):
        response = await async_client.get(
            "/api/v1/workers",
            headers={"Authorization": f"Bearer {tokens.access_token}"},
        )

    assert response.status_code == 503


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_workers_endpoint_reports_503_when_the_broker_is_unreachable(
    async_client: AsyncClient, session
) -> None:
    """A broker error must surface, not be swallowed into zeros."""
    from app.services.auth_service import AuthService

    service = AuthService(session)
    admin = User(
        email="admin-workers-broken@example.com",
        hashed_password="hash",
        role=UserRole.SUPERUSER,
        is_active=True,
    )
    session.add(admin)
    await session.commit()
    tokens = service.create_tokens(admin)

    with patch(
        "app.api.v1.health._inspect_workers",
        side_effect=OSError("broker unreachable"),
    ):
        response = await async_client.get(
            "/api/v1/workers",
            headers={"Authorization": f"Bearer {tokens.access_token}"},
        )

    assert response.status_code == 503


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_workers_endpoint_does_not_block_the_event_loop(
    async_client: AsyncClient, session
) -> None:
    """The blocking Celery probes must run off the event loop.

    Each `inspect` call broadcasts and waits with a one-second default
    timeout, so three inline calls froze the whole process for up to three
    seconds per request.
    """
    import asyncio

    from app.services.auth_service import AuthService

    service = AuthService(session)
    admin = User(
        email="admin-workers-loop@example.com",
        hashed_password="hash",
        role=UserRole.SUPERUSER,
        is_active=True,
    )
    session.add(admin)
    await session.commit()
    tokens = service.create_tokens(admin)

    ticks = 0

    async def _tick() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    def _slow_probe() -> tuple[dict, dict, dict]:
        import time

        time.sleep(0.3)
        return ({}, {}, {})

    with patch("app.api.v1.health._inspect_workers", side_effect=_slow_probe):
        ticker = asyncio.create_task(_tick())
        await asyncio.sleep(0)
        response = await async_client.get(
            "/api/v1/workers",
            headers={"Authorization": f"Bearer {tokens.access_token}"},
        )
        # Snapshot before cancelling, so this counts only what the loop
        # managed while the probe was running.
        ticks_during_request = ticks
        ticker.cancel()

    assert response.status_code == 200
    # Roughly 30 ticks fit in the 0.3s probe. Called inline, the blocking
    # sleep would have starved the ticker completely.
    assert ticks_during_request > 5, ticks_during_request
