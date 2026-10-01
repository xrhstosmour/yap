"""Tests that the worker count reaches the app container and is honoured."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from uvicorn.config import Config

COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.app.yml"


def _configured_value() -> str:
    """Read the `WEB_CONCURRENCY` default out of the compose file.

    Returns:
        The default after `:-`, without the surrounding substitution.
    """
    text = COMPOSE_FILE.read_text()
    match = re.search(r"WEB_CONCURRENCY=\$\{WEB_CONCURRENCY:-([^}]+)\}", text)
    assert match is not None, "WEB_CONCURRENCY is not set for the app service"
    return match.group(1)


class TestWebConcurrency:
    """`fastapi run` passes no worker count, so one process serves everything.

    Nothing named the count, so scaling the container's CPU quota bought
    nothing: the extra cores had no process to run on.
    """

    def test_the_compose_file_sets_it(self) -> None:
        """The variable has to reach the app container at all."""
        assert int(_configured_value()) >= 1

    def test_uvicorn_reads_it_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Setting it in compose is enough, no CLI flag needed."""
        monkeypatch.setenv("WEB_CONCURRENCY", "4")
        assert Config("app.main:app").workers == 4

    def test_the_default_is_one_process(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Named rather than left to chance, so the CPU limit can match it."""
        monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
        assert Config("app.main:app").workers == 1
