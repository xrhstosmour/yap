#!/usr/bin/env bash
set -euo pipefail

# This script updates itself: `copier update` further down overwrites this
# very file, since it is part of what gets synced from the template. Bash
# does not fully buffer a script before executing it, it reads and executes
# incrementally, so a change to the file underneath a running process can
# corrupt execution (a "syntax error near unexpected token" partway through,
# from a read landing mid-edit, or later lines silently never running).
# Re-exec once from a private, stable copy so the rest of this run reads
# bytes that can never change out from under it, regardless of what
# `copier update` does to the real file on disk.
if [ -z "${SYNCHRONIZE_SH_REEXECUTED:-}" ]; then
    PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
    STABLE_COPY="$(mktemp)"
    cp "$0" "$STABLE_COPY"
    chmod +x "$STABLE_COPY"
    cd "$PROJECT_DIR"
    SYNCHRONIZE_SH_REEXECUTED="$STABLE_COPY" exec "$STABLE_COPY" "$@"
fi
trap 'rm -f "$SYNCHRONIZE_SH_REEXECUTED"' EXIT

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

read_yaml_scalar() {
    local key="$1"
    local file="$2"
    grep "^${key}:" "$file" 2>/dev/null | head -1 | cut -d: -f2- \
        | sed 's/^ *//; s/^"//; s/"$//'
}

read_env_scalar() {
    local key="$1"
    local file="$2"
    local fallback="${3:-}"
    local value
    value=$(grep "^${key}=" "$file" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' || true)
    if [ -n "$value" ]; then
        echo "$value"
    else
        echo "$fallback"
    fi
}

PROJECT_DIR="$(pwd)"

echo ""
echo "────────────────────────────────────────────────────────────"
info "Synchronizing with upstream 'YAP' template"

command -v copier >/dev/null 2>&1 || error "copier is required. Install with: uv tool install copier"
command -v git >/dev/null 2>&1 || error "git is required"

[ -f .env ] || error ".env not found. Run ./scripts/setup.sh first to create it."

# Extract only the secrets the answers file below actually needs, through
# read_env_scalar's plain grep+cut, rather than `source .env`. Sourcing the
# whole file as bash script requires every line to be valid shell syntax,
# but free-text fields like FIRST_SUPERUSER_FULL_NAME are user-supplied and
# can contain a space or a shell-special character (a name with a space is
# enough to crash this script under `set -e`, since bash reads the second
# word as a command). None of the keys below are free text, every one is a
# generated secret, so this is exactly as safe and needs no quoting in .env.
SECRET_KEY="$(read_env_scalar "SECRET_KEY" ".env")"
CRYPTO_KEY="$(read_env_scalar "CRYPTO_KEY" ".env")"
POSTGRESQL_PASSWORD="$(read_env_scalar "POSTGRESQL_PASSWORD" ".env")"
FIRST_SUPERUSER_PASSWORD="$(read_env_scalar "FIRST_SUPERUSER_PASSWORD" ".env")"
RABBITMQ_PASSWORD="$(read_env_scalar "RABBITMQ_PASSWORD" ".env")"
RABBITMQ_ERLANG_COOKIE="$(read_env_scalar "RABBITMQ_ERLANG_COOKIE" ".env")"
REDIS_PASSWORD="$(read_env_scalar "REDIS_PASSWORD" ".env")"
FLOWER_PASSWORD="$(read_env_scalar "FLOWER_PASSWORD" ".env")"
PGADMIN4_PASSWORD="$(read_env_scalar "PGADMIN4_PASSWORD" ".env")"
GLITCHTIP_SECRET_KEY="$(read_env_scalar "GLITCHTIP_SECRET_KEY" ".env")"
REDIS_COMMANDER_PASSWORD="$(read_env_scalar "REDIS_COMMANDER_PASSWORD" ".env")"
METABASE_READ_ONLY_PASSWORD="$(read_env_scalar "METABASE_READ_ONLY_PASSWORD" ".env")"

# Reconstruct answers at every sync from project files and .env.
# The answers file is intentionally gitignored to avoid committing secrets.
warn "Reconstructing copier answers from project files and .env..."

project_name=$(read_env_scalar "PROJECT_NAME" ".env.example")
project_slug=$(read_env_scalar "POSTGRESQL_USER" ".env.example")
description=$(python3 - <<'PY'
import tomllib
from pathlib import Path

path = Path("pyproject.toml")
if not path.exists():
    print("")
    raise SystemExit(0)
data = tomllib.loads(path.read_text())
print((data.get("project", {}) or {}).get("description", ""))
PY
)
author_name=$(python3 - <<'PY'
import tomllib
from pathlib import Path

path = Path("pyproject.toml")
if not path.exists():
    print("")
    raise SystemExit(0)
data = tomllib.loads(path.read_text())
authors = (data.get("project", {}) or {}).get("authors", [])
if authors and isinstance(authors[0], dict):
    print(authors[0].get("name", ""))
else:
    print("")
PY
)
author_email=$(python3 - <<'PY'
import tomllib
from pathlib import Path

path = Path("pyproject.toml")
if not path.exists():
    print("")
    raise SystemExit(0)
data = tomllib.loads(path.read_text())
authors = (data.get("project", {}) or {}).get("authors", [])
if authors and isinstance(authors[0], dict):
    print(authors[0].get("email", ""))
else:
    print("")
PY
)
superuser=$(read_env_scalar "FIRST_SUPERUSER_FULL_NAME" ".env.example")
traefik_host=$(read_env_scalar "TRAEFIK_HOST" ".env.example")
timezone=$(read_env_scalar "TIMEZONE" ".env.example" "UTC")
storage_region=$(read_env_scalar "STORAGE_REGION" ".env.example" "eu-central-1")

has_extra() { grep -q "containers/.*/$1/docker-compose.yml" docker-compose.yml 2>/dev/null && echo "true" || echo "false"; }
include_traefik=$(has_extra "traefik")
include_glitchtip=$(has_extra "glitchtip")
include_metabase=$(has_extra "metabase")
include_pgadmin4=$(has_extra "pgadmin4")
include_mailpit=$(has_extra "mailpit")
include_redis_commander=$(has_extra "redis_commander")
include_flower=$(has_extra "flower")
include_minio=$(has_extra "minio")
# Read from the compose command, not from pyproject.toml. `celery-redbeat` is
# an unconditional dependency, so grepping pyproject matched every project and
# flipped this to true on every sync, whatever the user had chosen. The
# scheduler named in docker-compose.app.yml is the thing that actually varies.
include_redbeat=$(grep -q "RedBeatScheduler" docker-compose.app.yml 2>/dev/null && echo "true" || echo "false")

# Resolve nested mode from repository layout.
nested="false"
git_root=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
if [ -n "$git_root" ] && [ "$git_root" != "$PROJECT_DIR" ]; then
    nested="true"
fi

mkdir -p .copier

# Resolve template commit from tracked .copier/.version, fall back to remote HEAD.
_commit=""
if [ -f .copier/.version ]; then
    _commit=$(read_yaml_scalar "_commit" ".copier/.version" || true)
fi
if [ -z "$_commit" ]; then
    info "Fetching latest 'YAP' commit for version tracking..."
    _commit=$(git ls-remote "https://github.com/xrhstosmour/yap.git" HEAD | cut -f1) || _commit=""
fi

# Resolve the template's current default-branch HEAD. We update to and record
# this exact commit: copier defaults to the latest git tag, this template is
# untagged, and copier does not reliably write the new commit back to a custom
# answers file, so relying on either leaves the recorded version stale.
_target=$(git ls-remote "https://github.com/xrhstosmour/yap.git" HEAD | cut -f1) || _target=""
if [ -z "$_target" ]; then
    error "Could not resolve the latest 'YAP' commit to sync to"
fi

cat > .copier/.answers.yml << YAML
_src_path: gh:xrhstosmour/yap
_commit: $_commit
project_name: "${project_name}"
project_slug: "${project_slug}"
description: "${description}"
author_name: "${author_name}"
author_email: "${author_email}"
first_superuser_full_name: "${superuser}"
include_traefik: $include_traefik
traefik_host: "${traefik_host}"
timezone: "${timezone}"
include_glitchtip: $include_glitchtip
glitchtip_secret_key: "${GLITCHTIP_SECRET_KEY:-}"
include_metabase: $include_metabase
metabase_read_only_password: "${METABASE_READ_ONLY_PASSWORD:-}"
include_pgadmin4: $include_pgadmin4
pgadmin4_password: "${PGADMIN4_PASSWORD:-}"
include_mailpit: $include_mailpit
include_redis_commander: $include_redis_commander
redis_commander_password: "${REDIS_COMMANDER_PASSWORD:-}"
include_flower: $include_flower
flower_password: "${FLOWER_PASSWORD:-}"
include_redbeat: $include_redbeat
include_minio: $include_minio
storage_region: "${storage_region}"
nested: $nested
jwt_secret_key: "${SECRET_KEY:-}"
postgresql_password: "${POSTGRESQL_PASSWORD:-}"
first_superuser_password: "${FIRST_SUPERUSER_PASSWORD:-}"
rabbitmq_password: "${RABBITMQ_PASSWORD:-}"
rabbitmq_erlang_cookie: "${RABBITMQ_ERLANG_COOKIE:-}"
redis_password: "${REDIS_PASSWORD:-}"
crypto_key: "${CRYPTO_KEY:-}"
YAML

ANSWERS_FILE=".copier/.answers.yml"
info "Answers file reconstructed for copier update."

# Ensure the secret-bearing answers file is never left behind, on success
# or on any failure path below. Setting a new EXIT trap replaces the one
# from the re-exec guard above rather than adding to it, so this one
# cleans up both files, or the stable copy would leak from here on.
trap 'rm -f "$ANSWERS_FILE" "$SYNCHRONIZE_SH_REEXECUTED"' EXIT

if [ -n "$(git status --porcelain -- .)" ]; then
    warn "Uncommitted changes detected."
    warn "Commit or stash before running sync to avoid merge conflicts."
    exit 1
fi

info "Pulling upstream template changes..."

# Copier requires the destination to be a git repository.  If we are inside
# a subdirectory of a larger repo (monorepo), initialise a temporary git
# repo so copier's diff algorithm can work.
TEMP_GIT=false
if ! git rev-parse --git-dir >/dev/null 2>&1; then
    warn "Not inside a git repo, initialising temporary git repo for copier."
    git init --initial-branch=main . >/dev/null 2>&1
    git add -A >/dev/null 2>&1
    git commit --no-verify -m "Temporary baseline for copier update" >/dev/null 2>&1
    TEMP_GIT=true
fi

# Ensure a stale cache from a previous run does not block assemble.py from
# cloning fresh. Both paths are cleared: the old shared `/tmp/containers`,
# which the template used before the cache moved per-user, and the current
# one, since the version of assemble.py that runs here is whichever the
# project is still on.
python3 - <<'PY'
import os
import shutil

cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(
    os.path.expanduser("~"), ".cache"
)
for path in ("/tmp/containers", os.path.join(cache, "yap", "containers")):
    shutil.rmtree(path, ignore_errors=True)
PY

# Sync mode: the setup.sh task only ensures .env and skips services/migrations,
# so a template synchronize never requires Docker or a live database.
export YAP_SYNC=1
info "Running copier update..."
(
    copier update --trust --conflict inline --defaults --vcs-ref "$_target" \
        --answers-file "$ANSWERS_FILE" 2>&1 || exit 2

    # Check for unresolved merge conflicts from copier update.
    if grep -rq "^<<<<<<< \|^>>>>>>> " --include="*.py" --include="*.yml" --include="*.yaml" --include="*.sh" --include="*.toml" . 2>/dev/null; then
        warn "Inline merge conflicts found. Search for '<<<<<<<' markers and resolve them before committing."
        exit 3
    fi

    # Clean up temporary git repo if we created one.
    if [ "$TEMP_GIT" = true ]; then
        rm -rf .git
        info "Removed temporary git repo."
    fi

    if [ -f scripts/assemble.py ]; then
        info "Reassembling container configurations..."
        python3 scripts/assemble.py
    fi

    info "Installing dependencies..."
    uv sync --extra dev 2>/dev/null || uv sync

    echo ""
    info "Checking for changes..."
    if git diff --stat; then
        echo ""
        info "Review changes, resolve any inline conflict markers (<<<<<<<), then commit."
        echo "  git diff"
        echo "  git add .copier/.version && git add -A && git commit -m \"Synchronize upstream \`YAP\` template changes\""
    else
        info "No changes, project is up to date."
    fi

    echo ""
    info "If services need restarting or secrets regenerating run:"
    echo "  ./scripts/setup.sh"
    echo ""
    echo "────────────────────────────────────────────────────────────"
    echo -e "  ${GREEN}Synchronization complete.${NC}"
) || {
    rc=$?

    # Persist version metadata even on error.
    cat > .copier/.version << YAML
_src_path: gh:xrhstosmour/yap
_commit: $_target
YAML
    info "Recorded target 'YAP' commit in '.copier/.version'."

    case $rc in
        2) error "copier update failed";;
        3) error "Inline merge conflicts found. Search for '<<<<<<<' markers and resolve them before committing.";;
        *) error "Synchronization failed (exit $rc). See errors above.";;
    esac
}

# Record version on success too.
cat > .copier/.version << YAML
_src_path: gh:xrhstosmour/yap
_commit: $_target
YAML
info "Recorded target 'YAP' commit in '.copier/.version'."
