"""Tests what the container healthcheck probes.

Docker's health status is what `depends_on: service_healthy` waits on, so
it is a readiness signal. The healthcheck probed `/live`, which returns 200
for as long as the process is running, so a container whose database was
unreachable reported healthy and was handed traffic it could not serve.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from httpx import ASGITransport
from httpx import AsyncClient

DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"


def _healthcheck_path() -> str:
    """Read the URL path the container healthcheck requests.

    Returns:
        The path, without scheme or host.
    """
    match = re.search(
        r"HEALTHCHECK.*?curl -f http://[^/]+(\S+)", DOCKERFILE.read_text(), re.S
    )
    assert match is not None, "no curl-based HEALTHCHECK found in the Dockerfile"
    return match.group(1)


class TestHealthcheckProbesReadiness:
    """The probe has to be able to fail."""

    def test_it_does_not_probe_liveness(self) -> None:
        """`/live` cannot report a broken dependency."""
        assert not _healthcheck_path().endswith("/live")

    def test_it_probes_readiness(self) -> None:
        """`/ready` checks the database before answering."""
        assert _healthcheck_path().endswith("/ready")


class TestTheTwoEndpointsDiffer:
    """The distinction the healthcheck now relies on."""

    @pytest.mark.anyio
    @pytest.mark.usefixtures("override_get_async_session")
    async def test_both_are_healthy_when_the_database_is_reachable(self) -> None:
        """The normal path must not start failing."""
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/api/v1/live")).status_code == 200
            assert (await client.get("/api/v1/ready")).status_code == 200

    @pytest.mark.anyio
    async def test_readiness_fails_when_the_database_does_not_answer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """This is the case `/live` reported as healthy.

        Args:
            monkeypatch: Fixture used to break the session dependency.
        """
        from app.database import get_async_session
        from app.main import app

        class _UnreachableSession:
            """A session whose queries fail, as when the database goes away."""

            async def execute(self, *args: object, **kwargs: object) -> object:
                raise OSError("database is unreachable")

        async def _broken_session():
            yield _UnreachableSession()

        app.dependency_overrides[get_async_session] = _broken_session
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                assert (await client.get("/api/v1/live")).status_code == 200
                assert (await client.get("/api/v1/ready")).status_code == 503
        finally:
            app.dependency_overrides.pop(get_async_session, None)
