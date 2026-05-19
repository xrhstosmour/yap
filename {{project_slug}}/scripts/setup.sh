#!/usr/bin/env bash
set -euo pipefail

# ─── Color output ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ─── Project detection ───────────────────────────────────────────────────────
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

info "Setting up project in ${PROJECT_DIR}"

# ─── Prerequisites check ─────────────────────────────────────────────────────
command -v python3 >/dev/null 2>&1 || error "python3 is required"
command -v uv >/dev/null 2>&1 || warn "uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
command -v docker >/dev/null 2>&1 || warn "docker not found (optional, for containerized services)"

# ─── Environment file ────────────────────────────────────────────────────────
if [ -f .env ]; then
    warn ".env already exists. Skipping generation."
else
    info "Generating .env with secure secrets..."

    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    POSTGRES_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
    FIRST_SUPERUSER_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")
    RABBITMQ_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")

    cp .env.example .env

    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s/SECRET_KEY=.*/SECRET_KEY=${SECRET_KEY}/" .env
        sed -i '' "s/POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=${POSTGRES_PASSWORD}/" .env
        sed -i '' "s/FIRST_SUPERUSER_PASSWORD=.*/FIRST_SUPERUSER_PASSWORD=${FIRST_SUPERUSER_PASSWORD}/" .env
        sed -i '' "s/RABBITMQ_PASSWORD=.*/RABBITMQ_PASSWORD=${RABBITMQ_PASSWORD}/" .env
    else
        sed -i "s/SECRET_KEY=.*/SECRET_KEY=${SECRET_KEY}/" .env
        sed -i "s/POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=${POSTGRES_PASSWORD}/" .env
        sed -i "s/FIRST_SUPERUSER_PASSWORD=.*/FIRST_SUPERUSER_PASSWORD=${FIRST_SUPERUSER_PASSWORD}/" .env
        sed -i "s/RABBITMQ_PASSWORD=.*/RABBITMQ_PASSWORD=${RABBITMQ_PASSWORD}/" .env
    fi

    info "Generated .env with secure random secrets"
    warn "Review .env and customize as needed"
fi

# ─── Install dependencies ────────────────────────────────────────────────────
if command -v uv >/dev/null 2>&1; then
    info "Installing Python dependencies with uv..."
    uv sync
    uv sync --group dev 2>/dev/null || true
    info "Dependencies installed"
else
    warn "uv not available. Install it first, then run: uv sync"
fi

# ─── Pre-commit hooks ────────────────────────────────────────────────────────
if [ -f .pre-commit-config.yaml ] && command -v uv >/dev/null 2>&1; then
    info "Installing pre-commit hooks..."
    uv run pre-commit install 2>/dev/null || warn "pre-commit install failed (non-critical)"
fi

# ─── Start infrastructure ────────────────────────────────────────────────────
if command -v docker >/dev/null 2>&1; then
    info "Starting infrastructure services (postgres, redis, rabbitmq)..."
    docker compose up -d postgres redis rabbitmq 2>/dev/null || \
        warn "docker compose failed. Start services manually: docker compose up -d"
else
    warn "Docker not found. Start postgres, redis, and rabbitmq manually."
fi

# ─── Wait for database ───────────────────────────────────────────────────────
if command -v docker >/dev/null 2>&1; then
    info "Waiting for PostgreSQL to be ready..."
    for i in $(seq 1 30); do
        if docker compose exec -T postgres pg_isready -U postgres 2>/dev/null; then
            info "PostgreSQL is ready"
            break
        fi
        if [ "$i" -eq 30 ]; then
            warn "PostgreSQL did not become ready in time"
        fi
        sleep 2
    done

    info "Waiting for Redis to be ready..."
    for i in $(seq 1 15); do
        if docker compose exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then
            info "Redis is ready"
            break
        fi
        if [ "$i" -eq 15 ]; then
            warn "Redis did not become ready in time"
        fi
        sleep 1
    done
fi

# ─── Run migrations ──────────────────────────────────────────────────────────
if command -v uv >/dev/null 2>&1; then
    info "Running database migrations..."
    uv run alembic upgrade head || warn "Migrations failed (database might not be ready)"
fi

# ─── Seed initial data ───────────────────────────────────────────────────────
if command -v uv >/dev/null 2>&1; then
    info "Seeding initial data..."
    uv run python app/initial_data.py 2>/dev/null || warn "Initial data seeding skipped (may already exist)"
fi

# ─── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo "────────────────────────────────────────────────────────────"
echo -e "  ${GREEN}Setup complete!${NC}"
echo ""
echo "  Start the application:"
echo "    uv run fastapi dev app/main.py --host 0.0.0.0 --port 8000"
echo ""
echo "  Or with Docker Compose (all services):"
echo "    docker compose up -d"
echo ""
echo "  Access:"
echo "    API Docs:  http://localhost:8000/docs"
echo "    Health:    http://localhost:8000/api/v1/health"
echo "    Metrics:   http://localhost:8000/api/v1/metrics"
echo ""
echo "  First superuser:"
echo "    Email:    admin@example.com"
echo "    Password: (see FIRST_SUPERUSER_PASSWORD in .env)"
echo "────────────────────────────────────────────────────────────"
