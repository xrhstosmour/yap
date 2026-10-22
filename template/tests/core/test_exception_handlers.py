"""Tests for the application-wide exception handlers in `create_app`."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.core.security import TokenRateLimitError
from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    """An app with routes that raise, served without re-raising errors.

    Returns:
        A test client that returns the handled responses.
    """
    application = create_app()

    @application.get("/boom")
    async def boom() -> None:
        raise RuntimeError("database password is hunter2")

    @application.get("/too-soon")
    async def too_soon() -> None:
        raise TokenRateLimitError("Wait 60 seconds.")

    return TestClient(application, raise_server_exceptions=False)


class TestUnhandledErrors:
    """An unexpected error answers 500 without describing itself."""

    def test_the_message_never_reaches_the_client(self, client: TestClient) -> None:
        """Exception text can carry secrets, paths or query fragments."""
        response = client.get("/boom")

        assert response.status_code == 500
        assert response.json() == {"detail": "Internal server error"}
        assert "hunter2" not in response.text


class TestTokenRateLimit:
    """A token requested again inside its cooldown is a 429, not a 500."""

    def test_it_says_when_to_try_again(self, client: TestClient) -> None:
        """Clients back off by `Retry-After`."""
        response = client.get("/too-soon")

        assert response.status_code == 429
        assert response.headers["retry-after"] == "60"
        assert response.json() == {"detail": "Wait 60 seconds."}
