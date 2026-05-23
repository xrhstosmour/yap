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
command -v python3 >/dev/null 2>&1 || error "python3 is required!"
command -v uv >/dev/null 2>&1 || warn "uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
command -v docker >/dev/null 2>&1 || warn "docker not found (optional, for containerized services)"

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

    cp .env.example .env

    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s/SECRET_KEY=.*/SECRET_KEY=${SECRET_KEY}/" .env
        sed -i '' "s/CRYPTO_KEY=.*/CRYPTO_KEY=${CRYPTO_KEY}/" .env
        sed -i '' "s/POSTGRESQL_PASSWORD=.*/POSTGRESQL_PASSWORD=${POSTGRESQL_PASSWORD}/" .env
        sed -i '' "s/FIRST_SUPERUSER_PASSWORD=.*/FIRST_SUPERUSER_PASSWORD=${FIRST_SUPERUSER_PASSWORD}/" .env
        sed -i '' "s/RABBITMQ_PASSWORD=.*/RABBITMQ_PASSWORD=${RABBITMQ_PASSWORD}/" .env

        sed -i '' "s/SECRET_KEY=.*/SECRET_KEY=your-secret-key/" .env.example
        sed -i '' "s/CRYPTO_KEY=.*/CRYPTO_KEY=your-32-bit-fernet-key==/" .env.example
        sed -i '' "s/POSTGRESQL_PASSWORD=.*/POSTGRESQL_PASSWORD=your-postgresql-password/" .env.example
        sed -i '' "s/FIRST_SUPERUSER_PASSWORD=.*/FIRST_SUPERUSER_PASSWORD=your-superuser-password/" .env.example
        sed -i '' "s/RABBITMQ_PASSWORD=.*/RABBITMQ_PASSWORD=your-rabbitmq-password/" .env.example
        sed -i '' "s/SENTRY_DSN=.*/SENTRY_DSN=https:\/\/public:secret@sentry.com\/1/" .env.example
    else
        sed -i "s/SECRET_KEY=.*/SECRET_KEY=${SECRET_KEY}/" .env
        sed -i "s/CRYPTO_KEY=.*/CRYPTO_KEY=${CRYPTO_KEY}/" .env
        sed -i "s/POSTGRESQL_PASSWORD=.*/POSTGRESQL_PASSWORD=${POSTGRESQL_PASSWORD}/" .env
        sed -i "s/FIRST_SUPERUSER_PASSWORD=.*/FIRST_SUPERUSER_PASSWORD=${FIRST_SUPERUSER_PASSWORD}/" .env
        sed -i "s/RABBITMQ_PASSWORD=.*/RABBITMQ_PASSWORD=${RABBITMQ_PASSWORD}/" .env

        sed -i "s/SECRET_KEY=.*/SECRET_KEY=your-secret-key/" .env.example
        sed -i "s/CRYPTO_KEY=.*/CRYPTO_KEY=your-32-bit-fernet-key==/" .env.example
        sed -i "s/POSTGRESQL_PASSWORD=.*/POSTGRESQL_PASSWORD=your-postgresql-password/" .env.example
        sed -i "s/FIRST_SUPERUSER_PASSWORD=.*/FIRST_SUPERUSER_PASSWORD=your-superuser-password/" .env.example
        sed -i "s/RABBITMQ_PASSWORD=.*/RABBITMQ_PASSWORD=your-rabbitmq-password/" .env.example
        sed -i "s/SENTRY_DSN=.*/SENTRY_DSN=https:\/\/public:secret@sentry.com\/1/" .env.example
    fi
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

    info "Starting infrastructure services..."
    docker compose down -v 2>/dev/null || true
    docker compose up -d --force-recreate postgresql redis rabbitmq 2>/dev/null || \
        warn "docker compose failed. Start services manually: docker compose up -d"

    info "Waiting for database readiness..."
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

            info "Seeding initial data..."
            uv run python app/initial_data.py || warn "Initial data seeding failed"
        fi
    else
        warn "Skipping migrations and seeding..."
    fi
else
    warn "Docker not found — skipping services, migrations, and seeding"
fi

echo -e "  ${GREEN}Setup complete!${NC}"
echo "────────────────────────────────────────────────────────────"
