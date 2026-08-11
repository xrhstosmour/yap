"""Tests that CORS wraps every other middleware, including early returns."""

from __future__ import annotations

from starlette.testclient import TestClient

from app.core.settings import settings
from app.main import app


class TestCorsWrapsEarlyReturns:
    """CORS must be the outermost middleware.

    A response returned directly by another middleware, without calling
    `call_next` (e.g. a validation failure), must still carry CORS headers,
    or a browser sees an opaque network failure instead of that response.
    """

    def test_database_mode_middleware_early_return_carries_cors_headers(self) -> None:
        """The 400 from an unknown X-Database-Mode still has CORS headers."""
        origin = settings.all_cors_origins[0]
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get(
            "/",
            headers={"X-Database-Mode": "not-a-real-mode", "Origin": origin},
        )

        assert response.status_code == 400
        assert response.headers.get("access-control-allow-origin") == origin
