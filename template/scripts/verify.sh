#!/usr/bin/env bash
set -euo pipefail

# Every gate CI runs, in one local run.
# Usage: ./scripts/verify.sh
#
# The commands mirror `.github/workflows/ci.yml`, so a green run here is the
# same answer CI gives, without waiting for a push.

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

if [ ! -x .venv/bin/python ]; then
    echo "No virtual environment. Run ./scripts/setup.sh first." >&2
    exit 1
fi

echo "==> ruff check"
uv run ruff check app/ tests/

echo "==> ruff format"
uv run ruff format --check app/ tests/

# `mypy-ci.toml` rather than the default configuration, which resolves a
# different set of stubs and reports errors CI does not. The gate is this one.
echo "==> mypy"
uv run mypy app/ --config-file mypy-ci.toml

echo "==> tests"
./scripts/test.sh

# Catches a model changed without a migration, which is otherwise found only
# when a deployment runs.
echo "==> alembic check"
uv run alembic check

echo
echo "ALL GATES PASSED"
