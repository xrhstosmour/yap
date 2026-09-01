"""Tests the readiness gate at the end of `setup.sh`.

The gate used to read `docker compose ps` by column position. `$5` landed
on the CREATED column, "13 seconds ago", so it was a number and never
matched "healthy" or "running". Every container counted as pending, the
loop always ran all thirty iterations, and setup ended with a flat
sixty-second sleep that gated on nothing and reported nothing.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

SETUP = Path(__file__).resolve().parents[2] / "scripts" / "setup.sh"

# Real `docker compose ps --format '{{.State}}|{{.Health}}'` output.
HEALTHY = "running|healthy"
NO_HEALTHCHECK = "running|"
STILL_STARTING = "running|starting"
UNHEALTHY = "running|unhealthy"
STOPPED = "exited|"


def _pending_expression() -> str:
    """Pull the readiness filter out of `setup.sh`.

    Testing the expression the script actually runs, rather than a copy of
    it, is the whole point: the bug was in the expression.

    Returns:
        The awk program used to count services that are not ready.
    """
    text = SETUP.read_text()
    match = re.search(
        r"pending=\$\(docker compose ps[^\n]*\n\s*\|\s*awk -F'\|' '([^']+)'", text
    )
    assert match is not None, "could not find the readiness filter in setup.sh"
    return match.group(1)


def _count_pending(lines: list[str]) -> int:
    """Run the script's own filter over some `docker compose ps` output.

    Args:
        lines: Lines as `docker compose ps --format` would emit them.

    Returns:
        How many services the filter considers not ready.
    """
    result = subprocess.run(
        ["awk", "-F|", _pending_expression()],
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        check=True,
    )
    return len([line for line in result.stdout.splitlines() if line.strip()])


class TestReadinessFilter:
    """What the gate counts as ready."""

    @pytest.mark.parametrize("line", [HEALTHY, NO_HEALTHCHECK])
    def test_a_ready_service_is_not_pending(self, line: str) -> None:
        """Passing its healthcheck, or not having one, is ready.

        Args:
            line: A `State|Health` pair that should count as ready.
        """
        assert _count_pending([line]) == 0

    @pytest.mark.parametrize("line", [STILL_STARTING, UNHEALTHY, STOPPED])
    def test_a_service_that_is_not_up_is_pending(self, line: str) -> None:
        """Starting, failing, or exited all have to hold the gate.

        Args:
            line: A `State|Health` pair that should count as pending.
        """
        assert _count_pending([line]) == 1

    def test_a_fully_healthy_stack_releases_the_gate(self) -> None:
        """This is the case the old expression got wrong.

        Every container was healthy and the loop still waited the full
        sixty seconds, because it was comparing against a timestamp.
        """
        assert _count_pending([HEALTHY, HEALTHY, NO_HEALTHCHECK]) == 0

    def test_one_bad_service_holds_the_gate(self) -> None:
        """A mixed stack waits for the one that is not ready."""
        assert _count_pending([HEALTHY, STILL_STARTING, NO_HEALTHCHECK]) == 1

    def test_empty_output_is_not_counted(self) -> None:
        """`docker compose ps` prints nothing when there are no containers."""
        assert _count_pending([""]) == 0


class TestGateShape:
    """The loop around the filter."""

    def test_the_gate_does_not_parse_by_column_position(self) -> None:
        """Column positions shift with docker's table formatting."""
        text = SETUP.read_text()

        assert '$5!="healthy"' not in text
        assert "--format '{{.State}}|{{.Health}}'" in text

    def test_an_unmet_gate_says_which_service(self) -> None:
        """Waiting a minute and then saying nothing helps nobody."""
        text = SETUP.read_text()

        assert "services_ready" in text
        assert "Some services are not healthy:" in text
