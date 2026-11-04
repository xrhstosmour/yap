"""Tests the deploy workflow a project generated with it ships with.

The workflow holds the server's SSH key and production settings, so what is
checked is how those secrets are handled, not that a deploy works. A nested
project's workflow is moved to the repository root, where these tests do not
look, so they are skipped there.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy.yml"


def _steps() -> list[dict[str, str]]:
    """Every step of every job in the workflow."""
    workflow = yaml.safe_load(WORKFLOW.read_text())
    return [step for job in workflow["jobs"].values() for step in job["steps"]]


@pytest.mark.skipif(not WORKFLOW.exists(), reason="generated only on request")
class TestGeneratedDeployWorkflow:
    """Secrets stay out of scripts and a deploy is never run twice at once."""

    def test_it_runs_on_a_push_to_main_and_by_hand_only(self) -> None:
        """A branch or a pull request must never reach the server."""
        workflow = yaml.safe_load(WORKFLOW.read_text())
        triggers = workflow[True]
        assert set(triggers) == {"workflow_dispatch", "push"}
        assert triggers["push"]["branches"] == ["main"]

    def test_no_secret_is_pasted_into_a_script(self) -> None:
        """An expression in a `run` is substituted into the shell text.

        A secret with a quote or a newline in it would end up as commands.
        They reach the shell as environment variables instead.
        """
        for step in _steps():
            assert "${{" not in step.get("run", ""), step

    def test_a_run_started_by_hand_still_only_deploys_main(self) -> None:
        """A manual run can pick any branch, and the job holds the SSH key."""
        workflow = yaml.safe_load(WORKFLOW.read_text())
        guards = [job.get("if", "") for job in workflow["jobs"].values()]
        assert guards == ["github.ref == 'refs/heads/main'"]

    def test_projects_in_one_repository_do_not_share_a_queue(self) -> None:
        """GitHub keeps one pending run a group and cancels the older ones."""
        workflow = yaml.safe_load(WORKFLOW.read_text())
        assert workflow["concurrency"]["group"] != "deploy"
        assert workflow["concurrency"]["group"].startswith("deploy-")

    def test_a_running_deploy_is_not_cancelled(self) -> None:
        """Half a rsync followed by a rebuild leaves the server in between."""
        workflow = yaml.safe_load(WORKFLOW.read_text())
        assert workflow["concurrency"]["cancel-in-progress"] is False

    def test_every_action_is_pinned_to_a_commit(self) -> None:
        """A retagged action would run with the server's SSH key."""
        for step in _steps():
            if "uses" in step:
                assert re.search(r"@[0-9a-f]{40}$", step["uses"]), step["uses"]
