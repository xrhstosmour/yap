"""Tests that the app trusts proxy headers from the reverse proxy only."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from uvicorn.config import Config
from uvicorn.middleware.proxy_headers import _TrustedHosts

COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.app.yml"


def _configured_value() -> str:
    """Read the `FORWARDED_ALLOW_IPS` default out of the compose file.

    Returns:
        The default after `:-`, without the surrounding substitution.
    """
    text = COMPOSE_FILE.read_text()
    match = re.search(r"FORWARDED_ALLOW_IPS=\$\{FORWARDED_ALLOW_IPS:-([^}]+)\}", text)
    assert match is not None, "FORWARDED_ALLOW_IPS is not set for the app service"
    return match.group(1)


class TestForwardedAllowIps:
    """The per-IP limiter is only per-IP if `X-Forwarded-For` is honoured.

    `uvicorn` reads `FORWARDED_ALLOW_IPS` and defaults it to `127.0.0.1`.
    The reverse proxy runs in its own container, so its address never
    matched and the header was dropped: every request looked like it came
    from the proxy. That collapses `check_auth_rate_limit` into one bucket
    for the whole internet and writes a single address into every log line.
    """

    def test_the_compose_file_sets_it(self) -> None:
        """The variable has to reach the app container at all."""
        assert _configured_value()

    def test_the_proxy_is_trusted_but_the_internet_is_not(self) -> None:
        """Docker's bridge range and loopback, and nothing else."""
        trusted = _TrustedHosts(_configured_value())

        # A sibling container, which is what the reverse proxy is.
        assert "172.18.0.5" in trusted
        assert "172.31.255.254" in trusted
        # A proxy on the host itself.
        assert "127.0.0.1" in trusted

        # `*` would have let any of these forge their own address, and
        # port 8000 is published, so they can reach the container directly.
        assert "203.0.113.7" not in trusted
        assert "192.168.1.4" not in trusted
        assert "10.0.0.9" not in trusted

    def test_uvicorn_reads_it_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Setting it in compose is enough, no CLI flag needed.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", _configured_value())

        assert Config("app.main:app").forwarded_allow_ips == _configured_value()

    def test_the_uvicorn_default_would_not_trust_the_proxy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The bug this guards against, stated directly.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)

        assert "172.18.0.5" not in _TrustedHosts(
            Config("app.main:app").forwarded_allow_ips
        )
