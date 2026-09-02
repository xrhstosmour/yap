"""Tests the workflow a generated project ships with.

The generated CI ran the whole suite including the `slow` markers, which
drive real processes and the assembled compose stack. The workflow does not
stand that stack up, so a new project's very first push went red on
`test_core_compose_services_running`, before its author had written any of
their own code.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def _run_steps() -> list[str]:
    """Every `run` command in the workflow, flattened.

    Returns:
        The command strings, one per step that has one.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text())
    commands = []
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if "run" in step:
                commands.append(step["run"])
    return commands


def _pytest_steps() -> list[str]:
    """The steps that invoke pytest.

    Returns:
        Every run command containing a pytest invocation.
    """
    return [command for command in _run_steps() if "pytest" in command]


@pytest.mark.skipif(not WORKFLOW.exists(), reason="nested projects move the workflow")
class TestGeneratedWorkflow:
    """What a fresh project's first CI run actually executes."""

    def test_the_suite_excludes_the_slow_markers(self) -> None:
        """Otherwise the first push fails on infrastructure it has no way to have."""
        suite = [
            command
            for command in _pytest_steps()
            if re.search(r"pytest tests/\s", command)
        ]
        assert suite, "no full-suite pytest step found"

        for command in suite:
            assert '-m "not slow"' in command

    def test_the_smoke_test_is_still_run(self) -> None:
        """It is selected by node ID, which `-m` does not filter."""
        assert any(
            "tests/test_startup.py::test_api_smoke" in command
            for command in _pytest_steps()
        )

    def test_every_action_is_pinned_to_a_commit(self) -> None:
        """A tag can be repointed by whoever owns the action.

        Pinning by tag means a compromised or simply retagged upstream runs
        in this project's CI, with its `GITHUB_TOKEN`, without anything
        changing here. A commit SHA cannot be moved.
        """
        workflow = yaml.safe_load(WORKFLOW.read_text())

        used = [
            step["uses"]
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
            if "uses" in step
        ]
        assert used, "no actions found in the workflow"

        for reference in used:
            _, _, version = reference.partition("@")
            assert re.fullmatch(r"[0-9a-f]{40}", version), (
                f"{reference} is pinned by tag, not by commit"
            )
