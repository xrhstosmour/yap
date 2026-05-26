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
    command -v docker >/dev/null 2>&1 || error "Docker is required!"
fi

# Environment file setup.
if [ -f .env ]; then
    warn ".env already exists. Skipping generation."
else
    info "Generating environment variables..."

    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    CRYPTO_KEY=$(python3 -c "import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())")
    POSTGRESQL_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
    FIRST_SUPERUSER_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")
    RABBITMQ_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")
    REDIS_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")
    FLOWER_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(12))")
    PGADMIN4_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(12))")
    GLITCHTIP_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    REDIS_COMMANDER_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(12))")
    METABASE_READ_ONLY_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")

    cp .env.example .env

    if [[ "$OSTYPE" == "darwin"* ]]; then
        SED_INPLACE=("sed" "-i" "")
    else
        SED_INPLACE=("sed" "-i")
    fi

    "${SED_INPLACE[@]}" "s/SECRET_KEY=.*/SECRET_KEY=${SECRET_KEY}/" .env
    "${SED_INPLACE[@]}" "s/CRYPTO_KEY=.*/CRYPTO_KEY=${CRYPTO_KEY}/" .env
    "${SED_INPLACE[@]}" "s/POSTGRESQL_PASSWORD=.*/POSTGRESQL_PASSWORD=${POSTGRESQL_PASSWORD}/" .env
    "${SED_INPLACE[@]}" "s|\(DATABASE_URL=postgres://[^:]*:\)[^@]*@|\1${POSTGRESQL_PASSWORD}@|" .env
    "${SED_INPLACE[@]}" "s/METABASE_DATABASE_PASSWORD=.*/METABASE_DATABASE_PASSWORD=${POSTGRESQL_PASSWORD}/" .env
    "${SED_INPLACE[@]}" "s/FIRST_SUPERUSER_PASSWORD=.*/FIRST_SUPERUSER_PASSWORD=${FIRST_SUPERUSER_PASSWORD}/" .env
    "${SED_INPLACE[@]}" "s/RABBITMQ_PASSWORD=.*/RABBITMQ_PASSWORD=${RABBITMQ_PASSWORD}/" .env
    "${SED_INPLACE[@]}" "s/REDIS_PASSWORD=.*/REDIS_PASSWORD=${REDIS_PASSWORD}/" .env
    "${SED_INPLACE[@]}" "s|REDIS_URL=redis://:.*@redis|REDIS_URL=redis://:${REDIS_PASSWORD}@redis|" .env
    "${SED_INPLACE[@]}" "s/FLOWER_PASSWORD=.*/FLOWER_PASSWORD=${FLOWER_PASSWORD}/" .env
    "${SED_INPLACE[@]}" "s/PGADMIN4_PASSWORD=.*/PGADMIN4_PASSWORD=${PGADMIN4_PASSWORD}/" .env
    "${SED_INPLACE[@]}" "s/GLITCHTIP_SECRET_KEY=.*/GLITCHTIP_SECRET_KEY=${GLITCHTIP_SECRET_KEY}/" .env
    "${SED_INPLACE[@]}" "s/REDIS_COMMANDER_PASSWORD=.*/REDIS_COMMANDER_PASSWORD=${REDIS_COMMANDER_PASSWORD}/" .env
    "${SED_INPLACE[@]}" "s/METABASE_READ_ONLY_PASSWORD=.*/METABASE_READ_ONLY_PASSWORD=${METABASE_READ_ONLY_PASSWORD}/" .env

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
    "${SED_INPLACE[@]}" "s/SENTRY_DSN=.*/SENTRY_DSN=https:\/\/public:secret@sentry.com\/1/" .env.example
fi

# Dependencies installation.
if command -v uv >/dev/null 2>&1; then
    info "Installing dependencies..."
    uv sync
    uv sync --extra dev 2>/dev/null || true
else
    warn "uv not available. Install it first, then run: uv sync"
fi

# Pre-commit hooks.
if [ -f .pre-commit-config.yaml ] && command -v uv >/dev/null 2>&1; then
    info "Installing pre-commit hooks..."
    if [ ! -d .git ]; then
        git init
    fi
    uv run pre-commit install || error "pre-commit hooks installation failed!"
fi

# Start infrastructure services and run migrations.
if command -v docker >/dev/null 2>&1; then
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

    info "Starting all services..."
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
        if docker compose up -d --force-recreate; then
            break
        fi
        if [ "$i" -lt 3 ]; then
            warn "Compose start failed ($i/3), retrying in 5 s..."
            sleep 5
        fi
    done

    info "Waiting for database readiness..."
    sleep 5
    for i in $(seq 1 30); do
        if docker compose exec -T postgresql pg_isready 2>/dev/null; then
            break
        fi
        [ "$i" -eq 30 ] && warn "Database did not become ready in time"
        sleep 2
    done

    if docker compose exec -T postgresql pg_isready 2>/dev/null; then
        if command -v uv >/dev/null 2>&1; then
            info "Running database migrations..."
            uv run alembic upgrade head || error "Database migrations failed!"

            info "Seeding initial PostgreSQL data..."
            uv run python app/initial_data.py || warn "Initial PostgreSQL data seeding failed!"

            info "Setting up Metabase database..."
            source .env 2>/dev/null || true
            docker compose exec -T postgresql psql -U "${POSTGRESQL_USER}" -c "CREATE DATABASE metabase;" 2>/dev/null || true
            docker compose exec -T postgresql psql -U "${POSTGRESQL_USER}" -v "pw=${METABASE_READ_ONLY_PASSWORD}" -c "CREATE USER metabase_readonly WITH PASSWORD :'pw';" 2>/dev/null || true
            docker compose exec -T postgresql psql -U "${POSTGRESQL_USER}" -v "db=${POSTGRESQL_DATABASE}" -c "GRANT CONNECT ON DATABASE :\"db\" TO metabase_readonly;" 2>/dev/null || true
            docker compose exec -T postgresql psql -U "${POSTGRESQL_USER}" -d "${POSTGRESQL_DATABASE}" -c "GRANT SELECT ON ALL TABLES IN SCHEMA public TO metabase_readonly;" 2>/dev/null || true
        fi
    else
        warn "Skipping migrations and seeding..."
    fi
else
    warn "Docker not found — skipping services, migrations, and seeding"
fi

echo -e "  ${GREEN}Setup complete!${NC}"
echo "────────────────────────────────────────────────────────────"
