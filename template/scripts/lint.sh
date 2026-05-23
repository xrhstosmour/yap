#!/usr/bin/env bash
set -euo pipefail

# Lint and auto-fix the project.
# Usage: ./scripts/lint.sh          # check only
#        ./scripts/lint.sh --fix    # auto-fix

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

if [ "${1:-}" = "--fix" ]; then
    echo "Auto-fixing with ruff..."
    uv run ruff check --fix app/ tests/
    uv run ruff format app/ tests/
    echo "Done."
else
    echo "Checking with ruff..."
    uv run ruff check app/ tests/
    uv run ruff format --check app/ tests/
    echo "All checks passed."
fi
