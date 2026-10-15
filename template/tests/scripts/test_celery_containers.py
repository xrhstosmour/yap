"""Tests the Celery containers can run where they are deployed.

Both inherited the image's health check, which asks the API on port 8000,
so they reported unhealthy forever and a deploy waiting on health never
finished. Every container also read the broker's CA from a path on the
machine that ran `setup.sh`, which does not exist inside a container, so
none of them could open a TLS connection to the broker.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.app.yml"
SERVICES = yaml.safe_load(COMPOSE_FILE.read_text())["services"]
BROKER_CLIENTS = ["app", "celery-worker", "celery-beat"]


def _environment(service: str) -> dict[str, str]:
    """Read the `environment` entries a service sets in the compose file.

    Args:
        service: The compose service name.

    Returns:
        The variables as a mapping.
    """
    entries = SERVICES[service].get("environment", [])
    return dict(entry.split("=", 1) for entry in entries)


class TestCeleryContainers:
    """Each container can be healthy, and can reach the broker."""

    @pytest.mark.parametrize("service", ["celery-worker", "celery-beat"])
    def test_no_worker_inherits_the_api_check(self, service: str) -> None:
        """A check against port 8000 fails in a container that serves nothing."""
        healthcheck = SERVICES[service].get("healthcheck")
        assert healthcheck is not None, f"{service} inherits the API check"
        assert "8000" not in str(healthcheck.get("test", ""))

    @pytest.mark.parametrize("service", BROKER_CLIENTS)
    def test_the_broker_ca_is_inside_the_container(self, service: str) -> None:
        """The CA path has to name a file mounted into the same container."""
        path = _environment(service).get("SSL_CERTIFICATE_PATH")
        assert path, f"{service} reads the CA path from .env, a host path"
        targets = [
            volume.split(":")[1]
            for volume in SERVICES[service].get("volumes", [])
            if volume.count(":") >= 1
        ]
        assert path in targets, f"{service} does not mount {path}"
