"""Tests what the reverse proxy exposes of the app.

`/admin` answers only to private networks, and the proxy is the one hop
that turns a request from anywhere into one from a private address. The
router rule is what keeps it, and the `/try` documentation, off the
internet.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.app.yml"


def _labels() -> list[str]:
    """Read the labels set on the app service.

    Returns:
        The labels, empty when the project has no reverse proxy.
    """
    compose = yaml.safe_load(COMPOSE_FILE.read_text())
    return compose["services"]["app"].get("labels", [])


def _router_rules() -> list[str]:
    """Read every `Traefik` router rule set on the app service.

    Returns:
        The rule values, empty when the project has no reverse proxy.
    """
    labels = _labels()
    return [
        label.split("=", 1)[1]
        for label in labels
        if re.fullmatch(r"traefik\.http\.routers\.[^.]+\.rule=.*", label)
    ]


@pytest.fixture
def rules() -> list[str]:
    """Skip when the project was generated without `Traefik`."""
    found = _router_rules()
    if not found:
        pytest.skip("No reverse proxy routes to the app.")
    return found


class TestTraefikAppRouting:
    """Only the public paths go through the proxy."""

    @pytest.mark.parametrize("path", ["/admin", "/admin/login", "/try"])
    def test_private_paths_are_not_routed(self, rules: list[str], path: str) -> None:
        """No path prefix in any rule covers a private path."""
        for rule in rules:
            prefixes = re.findall(r"PathPrefix\(`([^`]*)`\)", rule)
            # A rule with no path at all routes every path.
            assert prefixes, f"{rule} routes {path}"
            for prefix in prefixes:
                assert not path.startswith(prefix), f"{rule} routes {path}"

    def test_every_rule_names_its_host(self, rules: list[str]) -> None:
        """A rule without a host answers for any name pointed at the server."""
        for rule in rules:
            assert "Host(`" in rule, f"{rule} has no host"

    def test_every_router_takes_the_configured_tls(self, rules: list[str]) -> None:
        """A router left off the resolver serves the default certificate, and
        one with fixed TLS options answers 421 to a request by IP address."""
        labels = _labels()
        routers = [
            match.group(1)
            for label in labels
            if (
                match := re.fullmatch(
                    r"traefik\.http\.routers\.([^.]+)\.rule=.*", label
                )
            )
        ]
        for router in routers:
            expected = (
                f"traefik.http.routers.{router}.tls.certresolver="
                "${TRAEFIK_CERTIFICATE_RESOLVER:-}"
            )
            assert expected in labels, f"{router} names no certificate resolver"
            expected = (
                f"traefik.http.routers.{router}.tls.options="
                "${TRAEFIK_TLS_OPTIONS:-tls-opts@file}"
            )
            assert expected in labels, f"{router} fixes its TLS options"
