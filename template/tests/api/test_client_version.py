"""Tests for turning away client builds older than the oldest one served."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.client_version import ClientVersionMiddleware
from app.api.client_version import parse_version


def _client(minimum: str) -> TestClient:
    """A bare app with only this middleware, and one route in and one out."""
    app = FastAPI()
    app.add_middleware(ClientVersionMiddleware, minimum=minimum, path_prefix="/api")

    @app.get("/api/ping")
    async def ping() -> dict:
        return {"ok": True}

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    return TestClient(app)


class TestClientVersionMiddleware:
    """An old build is told to update, and nothing else is."""

    def test_an_older_build_is_told_to_update(self) -> None:
        response = _client("1.4.0").get(
            "/api/ping", headers={"X-Client-Version": "1.3.9+12"}
        )

        assert response.status_code == 426
        assert response.json()["minimum_client_version"] == "1.4.0"

    @pytest.mark.parametrize("version", ["1.4.0", "1.4.0+2", "1.10.0", "2.0.0"])
    def test_the_minimum_and_anything_newer_is_served(self, version: str) -> None:
        """Compared as numbers, so `1.10.0` is newer than `1.4.0`."""
        response = _client("1.4.0").get(
            "/api/ping", headers={"X-Client-Version": version}
        )

        assert response.status_code == 200

    @pytest.mark.parametrize("headers", [{}, {"X-Client-Version": "not a version"}])
    def test_a_client_that_does_not_say_is_served(
        self, headers: dict[str, str]
    ) -> None:
        """A browser or script cannot be told apart from an unversioned build."""
        response = _client("1.4.0").get("/api/ping", headers=headers)

        assert response.status_code == 200

    def test_paths_outside_the_api_are_never_refused(self) -> None:
        response = _client("1.4.0").get(
            "/health", headers={"X-Client-Version": "0.1.0"}
        )

        assert response.status_code == 200

    def test_no_minimum_serves_every_build(self) -> None:
        response = _client("").get("/api/ping", headers={"X-Client-Version": "0.0.1"})

        assert response.status_code == 200

    def test_a_minimum_that_is_not_a_version_fails_the_start(self) -> None:
        with pytest.raises(ValueError, match="MINIMUM_CLIENT_VERSION"):
            _client("one point four").get("/api/ping")


def test_parse_version_ignores_build_and_prerelease_suffixes() -> None:
    assert parse_version("1.2.3+45") == (1, 2, 3)
    assert parse_version("1.2.3-beta") == (1, 2, 3)
    assert parse_version("1.2") is None
