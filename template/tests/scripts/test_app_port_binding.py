"""Tests where the app container publishes its port.

`"8000:8000"` publishes on 0.0.0.0. With Traefik in front, the app stayed
reachable directly on port 8000 from anywhere that could route to the host,
in plaintext, skipping the proxy's TLS and every middleware attached to it.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.app.yml"


def _app_ports() -> list[str]:
    """Read the app service's published ports.

    Returns:
        The raw port entries, before variable substitution.
    """
    compose = yaml.safe_load(COMPOSE_FILE.read_text())
    return list(compose["services"]["app"]["ports"])


class TestAppPortBinding:
    """A published port is a way past the reverse proxy."""

    def test_the_port_is_not_published_on_every_interface(self) -> None:
        """A bare `host:container` mapping binds 0.0.0.0."""
        for entry in _app_ports():
            assert not re.fullmatch(r"\d+:\d+", entry), (
                f"{entry} publishes on every interface"
            )

    def test_the_default_binding_is_loopback(self) -> None:
        """Reaching the app from the host still works."""
        entries = _app_ports()
        assert len(entries) == 1

        match = re.match(r"\$\{APP_BIND_ADDRESS:-([^}]+)\}:", entries[0])
        assert match is not None, f"{entries[0]} has no configurable host address"
        assert match.group(1) == "127.0.0.1"

    def test_the_binding_stays_overridable(self) -> None:
        """A deployment fronted some other way can still publish it."""
        assert "APP_BIND_ADDRESS" in _app_ports()[0]

    def test_the_container_port_is_unchanged(self) -> None:
        """Traefik reaches the app over the internal network on 8000."""
        assert _app_ports()[0].endswith(":8000:8000")
