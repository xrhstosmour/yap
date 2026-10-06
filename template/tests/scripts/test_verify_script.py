"""Tests that the local gate runs what CI runs.

`verify.sh` exists so a push is not the way you discover a gate fails. That
only holds while it runs the same commands, so a gate added to the workflow
and not to the script leaves the local run reporting a pass CI will not give.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

PROJECT_DIR = Path(__file__).resolve().parents[2]
VERIFY = PROJECT_DIR / "scripts" / "verify.sh"
WORKFLOW = PROJECT_DIR / ".github" / "workflows" / "ci.yml"

# The lint and migration gates, which need no services of their own. The test
# suite is covered too, through `test.sh`, which the script calls instead of
# spelling out a pytest invocation.
GATES = (
    "ruff check",
    "ruff format --check",
    "mypy app/ --config-file mypy-ci.toml",
    "alembic check",
)


class TestVerifyScript:
    """One command, the same answer CI gives."""

    def test_the_script_is_executable(self) -> None:
        """Copier does not preserve a mode the template never had."""
        assert VERIFY.exists()
        assert VERIFY.stat().st_mode & 0o111, f"{VERIFY} is not executable"

    @pytest.mark.parametrize("gate", GATES)
    def test_the_gate_is_run_locally(self, gate: str) -> None:
        """A gate only CI runs is a gate found late."""
        assert gate in VERIFY.read_text(), f"verify.sh does not run {gate}"

    @pytest.mark.skipif(
        not WORKFLOW.exists(), reason="nested projects move the workflow"
    )
    @pytest.mark.parametrize("gate", GATES)
    def test_the_gate_is_the_one_the_workflow_runs(self, gate: str) -> None:
        """Pinning both sides to the same string is what catches drift."""
        workflow = yaml.safe_load(WORKFLOW.read_text())
        commands = [
            step["run"]
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
            if "run" in step
        ]
        assert any(gate in command for command in commands), (
            f"the workflow does not run {gate}"
        )

    def test_the_suite_runs_through_the_test_script(self) -> None:
        """Two spellings of the pytest invocation drift apart."""
        assert "./scripts/test.sh" in VERIFY.read_text()
