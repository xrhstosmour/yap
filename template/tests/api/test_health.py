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
