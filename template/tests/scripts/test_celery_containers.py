"""Tests the Celery containers can run where they are deployed.

Both inherited the image's health check, which asks the API on port 8000,
so they reported unhealthy forever and a deploy waiting on health never
finished.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.app.yml"
SERVICES = yaml.safe_load(COMPOSE_FILE.read_text())["services"]


class TestCeleryContainers:
    """Each container can report healthy."""

    @pytest.mark.parametrize("service", ["celery-worker", "celery-beat"])
    def test_no_worker_inherits_the_api_check(self, service: str) -> None:
        """A check against port 8000 fails in a container that serves nothing."""
        healthcheck = SERVICES[service].get("healthcheck")
        assert healthcheck is not None, f"{service} inherits the API check"
        assert "8000" not in str(healthcheck.get("test", ""))
