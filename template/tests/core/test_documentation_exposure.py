"""Tests that interactive docs are not served outside local development."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

DOCUMENTATION_PATHS = ("/try", "/documentation")


def _paths(environment: str, monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Build a fresh app for `environment` and return its registered paths."""
    monkeypatch.setattr("app.main.settings.ENVIRONMENT", environment)
    application = create_app()
    return {getattr(route, "path", "") for route in application.routes}


class TestDocumentationExposure:
    """Swagger, ReDoc and the OpenAPI schema describe every endpoint.

    Serving them from a deployed environment hands the full API surface to
    anonymous callers, so the routes must not be registered at all there.
    """

    def test_documentation_is_served_locally(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Local development keeps the docs and the schema."""
        paths = _paths("local", monkeypatch)

        for path in DOCUMENTATION_PATHS:
            assert path in paths
        assert any(path.endswith("/openapi.json") for path in paths)

    @pytest.mark.parametrize("environment", ["staging", "production"])
    def test_documentation_is_absent_when_deployed(
        self, environment: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deployed environments register neither the docs nor the schema."""
        paths = _paths(environment, monkeypatch)

        for path in DOCUMENTATION_PATHS:
            assert path not in paths
        assert not any(path.endswith("/openapi.json") for path in paths)


class TestRootPointer:
    """The root points a new developer at the docs, only where the docs exist."""

    def test_local_root_redirects_to_the_docs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`/` is registered locally and sends the caller to `/try`."""
        monkeypatch.setattr("app.main.settings.ENVIRONMENT", "local")
        client = TestClient(create_app())

        response = client.get("/", follow_redirects=False)

        assert response.status_code == 307
        assert response.headers["location"] == "/try"

    def test_deployed_root_stays_unregistered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A deployed app does not answer `/` at all."""
        assert "/" not in _paths("production", monkeypatch)
