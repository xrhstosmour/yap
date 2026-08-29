#!/usr/bin/env bash
set -euo pipefail

# ─── Database backup script ──────────────────────────────────────────────────
# Usage: ./scripts/backup.sh
# Supports: PostgreSQL via pg_dump (docker) or pg_dump (local)
#
# Environment variables (from .env):
#   POSTGRESQL_SERVER, POSTGRESQL_PORT, POSTGRESQL_USER, POSTGRESQL_DATABASE
#   POSTGRESQL_PASSWORD (required)
#   BACKUP_DIRECTORY (default: ./backups)
#   BACKUP_RETENTION_DAYS (default: 30)
#
# Cron example (daily at 2 AM):
#   0 2 * * * /app/scripts/backup.sh >> /var/log/backup.log 2>&1

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[BACKUP]${NC} $(date '+%Y-%m-%d %H:%M:%S') $*"; }
warn()  { echo -e "${YELLOW}[BACKUP]${NC} $(date '+%Y-%m-%d %H:%M:%S') $*"; }
error() { echo -e "${RED}[BACKUP]${NC} $(date '+%Y-%m-%d %H:%M:%S') $*"; exit 1; }

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# ─── Load environment ────────────────────────────────────────────────────────
# Reads the handful of keys needed with plain grep+cut rather than
# `source .env`, matching scripts/synchronize.sh. Sourcing requires every
# line of .env to be valid shell: a free-text value containing a space, and
# FIRST_SUPERUSER_FULL_NAME is exactly that, makes bash read the second word
# as a command, which under `set -e` kills the script. A nightly cron backup
# would stop running and say nothing. Worse, `$(...)` or a backtick in any
# value executes on every run.
#
# None of the keys below are free text, so a plain grep+cut is exact.
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

ENVIRONMENT_FILE="$PROJECT_DIR/.env"
if [ ! -f "$ENVIRONMENT_FILE" ]; then
    ENVIRONMENT_FILE=/dev/null
fi

# The environment still wins, so a cron entry or container can override any
# of these without editing .env, which sourcing also allowed.
DATABASE_HOST="${POSTGRESQL_SERVER:-$(read_env_scalar POSTGRESQL_SERVER "$ENVIRONMENT_FILE" localhost)}"
DATABASE_PORT="${POSTGRESQL_PORT:-$(read_env_scalar POSTGRESQL_PORT "$ENVIRONMENT_FILE" 5432)}"
DATABASE_USER="${POSTGRESQL_USER:-$(read_env_scalar POSTGRESQL_USER "$ENVIRONMENT_FILE" postgresql)}"
DATABASE_PASSWORD="${POSTGRESQL_PASSWORD:-$(read_env_scalar POSTGRESQL_PASSWORD "$ENVIRONMENT_FILE")}"
DATABASE_NAME="${POSTGRESQL_DATABASE:-$(read_env_scalar POSTGRESQL_DATABASE "$ENVIRONMENT_FILE" postgresql)}"

# Read after .env, not before it. Set earlier, `mkdir` ran against the
# default while everything downstream used whatever .env said, so a project
# that configured BACKUP_DIRECTORY got an empty ./backups created next to
# its real one.
BACKUP_DIRECTORY="${BACKUP_DIRECTORY:-$(read_env_scalar BACKUP_DIRECTORY "$ENVIRONMENT_FILE" "$PROJECT_DIR/backups")}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-$(read_env_scalar BACKUP_RETENTION_DAYS "$ENVIRONMENT_FILE" 30)}"

mkdir -p "$BACKUP_DIRECTORY"

if [ -z "$DATABASE_PASSWORD" ]; then
    error "POSTGRESQL_PASSWORD not set. Cannot connect to database."
fi

BACKUP_FILE="$BACKUP_DIRECTORY/${DATABASE_NAME}_${TIMESTAMP}.sql.gz"

info "Starting backup of $DATABASE_NAME from $DATABASE_HOST:$DATABASE_PORT"

# ─── Run pg_dump ─────────────────────────────────────────────────────────────
if command -v docker &>/dev/null && docker compose ps --quiet postgresql &>/dev/null 2>&1; then
    # Use docker exec to run pg_dump inside the container
    info "Running pg_dump via Docker..."
    docker compose -f "$PROJECT_DIR/docker-compose.yml" exec -T postgresql \
        pg_dump -U "$DATABASE_USER" -d "$DATABASE_NAME" --no-owner --no-acl | gzip > "$BACKUP_FILE"
else
    # Use local pg_dump
    if command -v pg_dump &>/dev/null; then
        info "Running pg_dump locally..."
        PGPASSWORD="$DATABASE_PASSWORD" pg_dump \
            -h "$DATABASE_HOST" -p "$DATABASE_PORT" -U "$DATABASE_USER" -d "$DATABASE_NAME" \
            --no-owner --no-acl | gzip > "$BACKUP_FILE"
    else
        error "pg_dump not found and Docker postgresql not running. Cannot backup."
    fi
fi

# ─── Verify backup ───────────────────────────────────────────────────────────
if [ -f "$BACKUP_FILE" ] && [ -s "$BACKUP_FILE" ]; then
    BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    info "Backup complete: $BACKUP_FILE ($BACKUP_SIZE)"
else
    error "Backup file is empty or missing. Backup failed."
fi

# ─── Cleanup old backups ─────────────────────────────────────────────────────
info "Cleaning backups older than $RETENTION_DAYS days..."
DELETED=$(find "$BACKUP_DIRECTORY" -name "${DATABASE_NAME}_*.sql.gz" -mtime "+$RETENTION_DAYS" -delete -print | wc -l)
info "Removed $DELETED old backup(s)"

# ─── Summary ─────────────────────────────────────────────────────────────────
CURRENT_COUNT=$(find "$BACKUP_DIRECTORY" -name "${DATABASE_NAME}_*.sql.gz" | wc -l)
CURRENT_SIZE=$(du -sh "$BACKUP_DIRECTORY" 2>/dev/null | cut -f1)
info "Backup directory: $BACKUP_DIRECTORY ($CURRENT_SIZE, $CURRENT_COUNT files)"
