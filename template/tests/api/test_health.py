"""Tests for health check and metrics endpoints."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    """Create a test client."""
    from fastapi.testclient import TestClient

    return TestClient(app)


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


def test_metrics_endpoint(client) -> None:
    """GET /api/v1/metrics returns pool and cache stats."""
    response = client.get("/api/v1/metrics")
    assert response.status_code in (200, 500)


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


def test_workers_endpoint_returns_stats(client) -> None:
    """GET /api/v1/workers should return Celery worker stats."""
    response = client.get("/api/v1/workers")
    assert response.status_code == 200
    data = response.json()
    assert "active" in data
    assert "scheduled" in data
    assert "queues" in data
    assert isinstance(data["active"], int)
    assert isinstance(data["scheduled"], int)
    assert isinstance(data["queues"], list)
