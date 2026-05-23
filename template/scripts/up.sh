#!/usr/bin/env bash
set -euo pipefail

# ─── Full-stack startup with containers repo services ─────────────────────────
# Usage: ./scripts/up.sh
# Requires containers repo cloned at ../containers

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONTAINERS_DIR="${CONTAINERS_DIR:-$PROJECT_DIR/../containers}"

RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'

if [ ! -d "$CONTAINERS_DIR" ]; then
    echo -e "${RED}Containers repo not found at $CONTAINERS_DIR${NC}"
    echo "Clone it with: git clone https://github.com/xrhstosmour/containers.git $CONTAINERS_DIR"
    exit 1
fi

# Create shared network once.
docker network create internal 2>/dev/null || true

# Build compose command with all services.
COMPOSE_FILES=(
    -f "$PROJECT_DIR/docker-compose.yml"
    -f "$CONTAINERS_DIR/networking/proxies/traefik/docker-compose.yml"
    -f "$CONTAINERS_DIR/monitoring/glitchtip/docker-compose.yml"
    -f "$CONTAINERS_DIR/databases/manage/metabase/docker-compose.yml"
    -f "$CONTAINERS_DIR/databases/manage/pgadmin4/docker-compose.yml"
    -f "$CONTAINERS_DIR/development/mailpit/docker-compose.yml"
)

echo -e "${GREEN}Starting all services...${NC}"
docker compose "${COMPOSE_FILES[@]}" up -d "$@"

echo ""
echo -e "${GREEN}Services running:${NC}"
echo "  API Docs:    http://localhost:8000/documentation"
echo "  Health:      http://localhost:8000/api/v1/live"
echo "  Traefik:     http://localhost:8080/dashboard/"
echo "  GlitchTip:   http://localhost:8080"
echo "  Metabase:    http://localhost:3000"
echo "  pgAdmin4:    http://localhost:5050"
echo "  Mailpit:     http://localhost:8025"
echo "  RabbitMQ:    http://localhost:15672"
