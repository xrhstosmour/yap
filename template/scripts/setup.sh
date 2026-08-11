#!/usr/bin/env bash
set -euo pipefail

# Colored output functions.
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# Project detection.
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

echo ""
echo "────────────────────────────────────────────────────────────"
info "Setting up project in ${PROJECT_DIR}"

# Prerequisites check.
command -v python3 >/dev/null 2>&1 || error "python3 is required! Install from https://www.python.org/downloads/"

# Install uv if missing.
if ! command -v uv >/dev/null 2>&1; then
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# Install Docker if missing.
if ! command -v docker >/dev/null 2>&1; then
    info "Docker not found. Attempting install..."
    case "$OSTYPE" in
        darwin*)
            if command -v brew >/dev/null 2>&1; then
                brew install --cask docker
            fi
            ;;
        linux*)
            curl -fsSL https://get.docker.com | sh 2>/dev/null || true
            ;;
        msys*)
            if command -v winget >/dev/null 2>&1; then
                winget install Docker.DockerDesktop
            fi
            ;;
    esac
    if ! command -v docker >/dev/null 2>&1; then
        if [ "${YAP_SYNC:-0}" = "1" ]; then
            warn "Docker not found; continuing in sync mode."
        else
            error "Docker is required!"
        fi
    fi
fi

# Environment file setup. Creates .env if missing, and backfills any secret
# that is still empty or a placeholder. This self-heals a placeholder .env
# seeded by the sync path (a copy of .env.example), whose values would
# otherwise crash migrations (for example an invalid CRYPTO_KEY).
if [[ "$OSTYPE" == "darwin"* ]]; then
    SED_INPLACE=("sed" "-i" "")
else
    SED_INPLACE=("sed" "-i")
fi

SECRET_KEY="${SECRET_KEY:-$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")}"
CRYPTO_KEY="${CRYPTO_KEY:-$(python3 -c "import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())")}"
POSTGRESQL_PASSWORD="${POSTGRESQL_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")}"
FIRST_SUPERUSER_PASSWORD="${FIRST_SUPERUSER_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")}"
RABBITMQ_PASSWORD="${RABBITMQ_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")}"
REDIS_PASSWORD="${REDIS_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")}"
FLOWER_PASSWORD="${FLOWER_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_urlsafe(12))")}"
PGADMIN4_PASSWORD="${PGADMIN4_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_urlsafe(12))")}"
GLITCHTIP_SECRET_KEY="${GLITCHTIP_SECRET_KEY:-$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")}"
REDIS_COMMANDER_PASSWORD="${REDIS_COMMANDER_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_urlsafe(12))")}"
METABASE_READ_ONLY_PASSWORD="${METABASE_READ_ONLY_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")}"
STORAGE_SECRET_KEY="${STORAGE_SECRET_KEY:-$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")}"
GOOGLE_CLIENT_SECRET="${GOOGLE_CLIENT_SECRET:-$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")}"
SMTP_PASSWORD="${SMTP_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")}"

if [ ! -f .env ]; then
    info "Generating environment variables..."
    cp .env.example .env

    # Reset .env.example back to placeholders so real secrets never leak into it.
    "${SED_INPLACE[@]}" "s/SECRET_KEY=.*/SECRET_KEY=your-secret-key/" .env.example
    "${SED_INPLACE[@]}" "s/CRYPTO_KEY=.*/CRYPTO_KEY=your-32-bit-fernet-key==/" .env.example
    "${SED_INPLACE[@]}" "s/POSTGRESQL_PASSWORD=.*/POSTGRESQL_PASSWORD=your-postgresql-password/" .env.example
    "${SED_INPLACE[@]}" "s/FIRST_SUPERUSER_PASSWORD=.*/FIRST_SUPERUSER_PASSWORD=your-superuser-password/" .env.example
    "${SED_INPLACE[@]}" "s/RABBITMQ_PASSWORD=.*/RABBITMQ_PASSWORD=your-rabbitmq-password/" .env.example
    "${SED_INPLACE[@]}" "s/REDIS_PASSWORD=.*/REDIS_PASSWORD=your-redis-password/" .env.example
    "${SED_INPLACE[@]}" "s/FLOWER_PASSWORD=.*/FLOWER_PASSWORD=your-flower-password/" .env.example
    "${SED_INPLACE[@]}" "s/PGADMIN4_PASSWORD=.*/PGADMIN4_PASSWORD=your-pgadmin-password/" .env.example
    "${SED_INPLACE[@]}" "s/GLITCHTIP_SECRET_KEY=.*/GLITCHTIP_SECRET_KEY=your-glitchtip-secret-key/" .env.example
    "${SED_INPLACE[@]}" "s/REDIS_COMMANDER_PASSWORD=.*/REDIS_COMMANDER_PASSWORD=your-redis-commander-password/" .env.example
    "${SED_INPLACE[@]}" "s/METABASE_READ_ONLY_PASSWORD=.*/METABASE_READ_ONLY_PASSWORD=your-metabase-read-only-password/" .env.example
    "${SED_INPLACE[@]}" "s/MINIO_ROOT_PASSWORD=.*/MINIO_ROOT_PASSWORD=minioadmin/" .env.example
    "${SED_INPLACE[@]}" "s/SENTRY_DSN=.*/SENTRY_DSN=https:\/\/public:secret@sentry.com\/1/" .env.example
else
    info "Backfilling any placeholder secrets in existing .env..."
fi

# Replace KEY's value only when it is empty, a placeholder ("your-..."), or
# matches the optional extra placeholder (e.g. a well-known service default).
backfill_secret() {
    key="$1"
    value="$2"
    extra_placeholder="${3:-}"
    current="$(sed -n "s/^${key}=//p" .env | head -1)"
    case "$current" in
        "" | your-*)
            "${SED_INPLACE[@]}" "s|^${key}=.*|${key}=${value}|" .env
            ;;
        *)
            if [ -n "$extra_placeholder" ] && [ "$current" = "$extra_placeholder" ]; then
                "${SED_INPLACE[@]}" "s|^${key}=.*|${key}=${value}|" .env
            fi
            ;;
    esac
}

backfill_secret SECRET_KEY "${SECRET_KEY}"
backfill_secret CRYPTO_KEY "${CRYPTO_KEY}"
backfill_secret POSTGRESQL_PASSWORD "${POSTGRESQL_PASSWORD}"
backfill_secret FIRST_SUPERUSER_PASSWORD "${FIRST_SUPERUSER_PASSWORD}"
backfill_secret RABBITMQ_PASSWORD "${RABBITMQ_PASSWORD}"
backfill_secret REDIS_PASSWORD "${REDIS_PASSWORD}"
backfill_secret FLOWER_PASSWORD "${FLOWER_PASSWORD}"
backfill_secret PGADMIN4_PASSWORD "${PGADMIN4_PASSWORD}"
backfill_secret GLITCHTIP_SECRET_KEY "${GLITCHTIP_SECRET_KEY}"
backfill_secret REDIS_COMMANDER_PASSWORD "${REDIS_COMMANDER_PASSWORD}"
backfill_secret METABASE_READ_ONLY_PASSWORD "${METABASE_READ_ONLY_PASSWORD}"
backfill_secret STORAGE_SECRET_KEY "${STORAGE_SECRET_KEY}"
backfill_secret MINIO_ROOT_PASSWORD "${MINIO_ROOT_PASSWORD}" "minioadmin"
backfill_secret GOOGLE_CLIENT_SECRET "${GOOGLE_CLIENT_SECRET}"
backfill_secret SMTP_PASSWORD "${SMTP_PASSWORD}"

# Derive the connection URLs and Metabase password from the current, possibly
# just-backfilled, database and Redis passwords. Idempotent.
CURRENT_POSTGRESQL_PASSWORD="$(sed -n "s/^POSTGRESQL_PASSWORD=//p" .env | head -1)"
"${SED_INPLACE[@]}" "s|\(DATABASE_URL=postgres://[^:]*:\)[^@]*@|\1${CURRENT_POSTGRESQL_PASSWORD}@|" .env
"${SED_INPLACE[@]}" "s/METABASE_DATABASE_PASSWORD=.*/METABASE_DATABASE_PASSWORD=${CURRENT_POSTGRESQL_PASSWORD}/" .env
CURRENT_REDIS_PASSWORD="$(sed -n "s/^REDIS_PASSWORD=//p" .env | head -1)"
"${SED_INPLACE[@]}" "s|REDIS_URL=redis://:.*@redis|REDIS_URL=redis://:${CURRENT_REDIS_PASSWORD}@redis|" .env

# Generate self-signed TLS certificates for RabbitMQ and Traefik if they are
# missing. assemble.py creates them during synchronize.sh, but a fresh clone
# that runs setup.sh without a prior sync has empty certificate directories,
# which crashes RabbitMQ with failed_to_prepare_configuration.
rabbitmq_cert="$PROJECT_DIR/containers/distribute/brokers/rabbitmq/certificates/ca_certificate.pem"
if [ ! -f "$rabbitmq_cert" ] && [ -f "$PROJECT_DIR/scripts/assemble.py" ]; then
    info "Generating TLS certificates..."
    python3 "$PROJECT_DIR/scripts/assemble.py" >/dev/null 2>&1 || \
        warn "Certificate generation failed; RabbitMQ TLS may not start"
fi

# Merge certificate paths written by assemble.py into .env.
# Replaces the placeholder SSL_CERTIFICATE_PATH with the absolute path from assemble.py.
if [ -f containers/.certificates ]; then
    cert_path=$(cut -d= -f2- < containers/.certificates)
    "${SED_INPLACE[@]}" "s|SSL_CERTIFICATE_PATH=.*|SSL_CERTIFICATE_PATH=${cert_path}|" .env
    rm containers/.certificates
fi

# Dependencies installation.
if command -v uv >/dev/null 2>&1; then
    info "Installing dependencies..."
    unset VIRTUAL_ENV
    uv sync
    uv sync --extra dev 2>/dev/null || true
else
    warn "uv not available. Install it first, then run: uv sync"
fi

# Pre-commit hooks.
if [ -f .pre-commit-config.yaml ] && command -v uv >/dev/null 2>&1; then
    info "Installing pre-commit hooks..."
    if [ -d .git ]; then
        uv run pre-commit install || error "pre-commit hooks installation failed!"
    else
        warn "Skipping pre-commit install, no git repository in scaffold directory"
    fi
fi

# In synchronize mode, only a valid .env is needed.
# Skip services, migrations, and seeding so template updates never require Docker or Database.
if [ "${YAP_SYNC:-0}" = "1" ]; then
    info "Ensuring '.env', skipping services, migrations, and seeding..."
    exit 0
fi

# Start infrastructure services and run migrations.
if [ "${CI:-}" = "true" ]; then
    # CI: database is already available via service containers.
    info "CI detected, using database at ${POSTGRESQL_SERVER:-localhost}:${POSTGRESQL_PORT:-5432}..."
    if python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(3)
try:
    s.connect(('${POSTGRESQL_SERVER:-localhost}', ${POSTGRESQL_PORT:-5432}))
    s.close()
    exit(0)
except Exception:
    exit(1)
" 2>/dev/null; then
        if command -v uv >/dev/null 2>&1; then
            info "Running database migrations..."
            uv run alembic upgrade head || error "Database migrations failed!"

            info "Seeding initial PostgreSQL data..."
            uv run --env-file .env python app/initial_data.py || warn "Initial PostgreSQL data seeding failed!"
        fi
    else
        warn "Database not reachable, skipping migrations and seeding."
    fi
elif command -v docker >/dev/null 2>&1; then
    # Ensure containerization daemon is running.
    if ! docker info >/dev/null 2>&1; then
        info "Starting containerization daemon..."
        case "$OSTYPE" in
            darwin*) open -a Docker ;;
            linux*)  sudo systemctl start docker 2>/dev/null || true ;;
            msys*)   start "" "Docker Desktop" 2>/dev/null || true ;;
        esac

        for i in $(seq 1 30); do
            if docker info >/dev/null 2>&1; then
                break
            fi
            [ "$i" -eq 30 ] && warn "Containerization daemon did not become ready in time!"
            sleep 2
        done
    fi

    info "Starting core services..."
    docker network create internal 2>/dev/null || true
    if docker compose ps -q 2>/dev/null | grep -q .; then
        warn "Stopping any existing containers, volumes preserved..."
    fi
    docker compose down 2>/dev/null || true

    # Pull images first, with retries, to avoid transient Docker Hub failures.
    info "Pulling container images..."
    for i in $(seq 1 3); do
        if docker compose pull --ignore-pull-failures 2>/dev/null; then
            break
        fi
        warn "Image pull failed ($i/3), retrying in 5 s..."
        sleep 5
    done

    for i in $(seq 1 3); do
        if docker compose up -d postgresql redis --force-recreate; then
            break
        fi
        if [ "$i" -lt 3 ]; then
            warn "Core services start failed ($i/3), retrying in 5 s..."
            sleep 5
        fi
    done

    info "Waiting for database readiness..."
    sleep 5
    database_ready=false
    for i in $(seq 1 30); do
        if docker compose exec -T postgresql pg_isready 2>/dev/null; then
            database_ready=true
            break
        fi
        [ "$i" -eq 30 ] && warn "Database did not become ready in time"
        sleep 2
    done

    if [ "$database_ready" = true ]; then
        if command -v uv >/dev/null 2>&1; then
            info "Running database migrations..."
            uv run alembic upgrade head || error "Database migrations failed!"

            info "Seeding initial PostgreSQL data..."
            uv run --env-file .env python app/initial_data.py || warn "Initial PostgreSQL data seeding failed!"
        fi

        # Start all included services individually so one unhealthy container
        # does not block the others.  Services that fail (e.g. port conflict in
        # CI) do not halt the startup; their containers simply won't be running.
        info "Starting remaining services..."
        for svc in $(docker compose config --services 2>/dev/null); do
            if [ "$svc" = "postgresql" ]; then continue; fi
            for attempt in 1 2 3; do
                if docker compose up -d --no-deps "$svc" --force-recreate 2>/dev/null; then
                    break
                fi
                if [ "$attempt" -lt 3 ]; then
                    warn "Service $svc failed to start ($attempt/3), retrying..."
                    sleep 5
                fi
            done
        done

        # Wait for containers to report healthy status.
        info "Waiting for services to become healthy..."
        for _ in $(seq 1 30); do
            unhealthy=$(docker compose ps 2>/dev/null | awk 'NR>1 && $5!="healthy" && $5!="running"' | wc -l || echo 0)
            if [ "$unhealthy" -eq 0 ]; then break; fi
            sleep 2
        done
    else
        warn "Skipping migrations and seeding..."
    fi
else
    warn "Docker not found, skipping services, migrations, and seeding"
fi

echo -e "  ${GREEN}Setup complete!${NC}"
echo "────────────────────────────────────────────────────────────"
