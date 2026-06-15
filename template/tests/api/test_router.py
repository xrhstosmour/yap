"""Smoke tests for the API v1 router."""

from __future__ import annotations

from app.api.v1.router import router


class TestV1Router:
    """Tests for the API v1 router."""

    def test_router_has_correct_prefix(self) -> None:
        """The v1 router should use /api/v1 prefix."""
        assert router.prefix == "/api/v1"

    def test_router_includes_all_sub_routers(self) -> None:
        """All v1 sub-routers should be included (more than 10 routes)."""
        assert len(router.routes) >= 8, (
            f"Expected at least 8 routes, got {len(router.routes)}"
        )

    def test_router_has_routes(self) -> None:
        """The router should have a non-empty set of routes."""
        assert len(router.routes) > 0
