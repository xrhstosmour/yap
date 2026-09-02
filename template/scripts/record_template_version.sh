#!/usr/bin/env bash
set -euo pipefail

# Copier runs this as the last generation task. It stamps the template commit
# the project was generated from into `.copier/.version`, which
# `scripts/synchronize.sh` later reads back to know what to update from.
#
# The remote is not always reachable: no network, DNS failure, GitHub down, a
# proxy that blocks git. That must never fail generation. Copier aborts the
# whole `copier copy` when a task exits non-zero, so an unreachable remote
# used to throw away an otherwise complete project at its very last step.
# Keep the placeholder instead, say so, and exit clean.

cd "$(cd "$(dirname "$0")/.." && pwd)"

VERSION_FILE=".copier/.version"

if [ ! -f "$VERSION_FILE" ]; then
    echo "[WARN] '$VERSION_FILE' is missing, there is nothing to record."
    exit 0
fi

sha=$(git ls-remote "https://github.com/xrhstosmour/yap.git" HEAD 2>/dev/null | cut -f1) || sha=""

if [ -z "$sha" ]; then
    echo "[WARN] Could not reach the 'YAP' remote, '$VERSION_FILE' keeps its current value."
    echo "[WARN] Run 'scripts/synchronize.sh' once online to record the template commit."
    exit 0
fi

sed -i.bak "s|^_commit: .*|_commit: ${sha}|" "$VERSION_FILE"
rm -f "${VERSION_FILE}.bak"
