#!/usr/bin/env bash
set -euo pipefail

# ─── Database backup script ──────────────────────────────────────────────────
# Usage: ./scripts/backup.sh
# Supports: PostgreSQL via pg_dump (docker) or pg_dump (local)
#
# Environment variables (from .env):
#   POSTGRES_SERVER, POSTGRES_PORT, POSTGRES_USER, POSTGRES_DB
#   POSTGRES_PASSWORD (required)
#   BACKUP_DIR (default: ./backups)
#   BACKUP_RETENTION_DAYS (default: 30)
#
# Cron example (daily at 2 AM):
#   0 2 * * * /app/scripts/backup.sh >> /var/log/backup.log 2>&1

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[BACKUP]${NC} $(date '+%Y-%m-%d %H:%M:%S') $*"; }
warn()  { echo -e "${YELLOW}[BACKUP]${NC} $(date '+%Y-%m-%d %H:%M:%S') $*"; }
error() { echo -e "${RED}[BACKUP]${NC} $(date '+%Y-%m-%d %H:%M:%S') $*"; exit 0; }

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

# ─── Load environment ────────────────────────────────────────────────────────
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    . "$PROJECT_DIR/.env"
    set +a
fi

DB_HOST="${POSTGRES_SERVER:-localhost}"
DB_PORT="${POSTGRES_PORT:-5432}"
DB_USER="${POSTGRES_USER:-postgres}"
DB_PASS="${POSTGRES_PASSWORD:-}"
DB_NAME="${POSTGRES_DB:-postgres}"

if [ -z "$DB_PASS" ]; then
    error "POSTGRES_PASSWORD not set. Cannot connect to database."
fi

BACKUP_FILE="$BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.sql.gz"

info "Starting backup of $DB_NAME from $DB_HOST:$DB_PORT"

# ─── Run pg_dump ─────────────────────────────────────────────────────────────
if command -v docker &>/dev/null && docker compose ps --quiet postgres &>/dev/null 2>&1; then
    # Use docker exec to run pg_dump inside the container
    info "Running pg_dump via Docker..."
    docker compose -f "$PROJECT_DIR/docker-compose.yml" exec -T postgres \
        pg_dump -U "$DB_USER" -d "$DB_NAME" --no-owner --no-acl | gzip > "$BACKUP_FILE"
else
    # Use local pg_dump
    if command -v pg_dump &>/dev/null; then
        info "Running pg_dump locally..."
        PGPASSWORD="$DB_PASS" pg_dump \
            -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
            --no-owner --no-acl | gzip > "$BACKUP_FILE"
    else
        error "pg_dump not found and Docker postgres not running. Cannot backup."
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
DELETED=$(find "$BACKUP_DIR" -name "${DB_NAME}_*.sql.gz" -mtime "+$RETENTION_DAYS" -delete -print | wc -l)
info "Removed $DELETED old backup(s)"

# ─── Summary ─────────────────────────────────────────────────────────────────
CURRENT_COUNT=$(find "$BACKUP_DIR" -name "${DB_NAME}_*.sql.gz" | wc -l)
CURRENT_SIZE=$(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1)
info "Backup directory: $BACKUP_DIR ($CURRENT_SIZE, $CURRENT_COUNT files)"
