#!/usr/bin/env bash
set -e

RADAR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$RADAR_DIR/.env"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

find_open_port() {
    local port="$1"
    while ss -tuln | grep -q ":${port} "; do
        port=$((port + 1))
    done
    echo "$port"
}

set_env_value() {
    local key="$1"
    local value="$2"

    if grep -q "^${key}=" "$ENV_FILE"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
        echo "${key}=${value}" >> "$ENV_FILE"
    fi
}

echo ""
echo "============================================="
echo "   Radar Setup"
echo "============================================="
echo ""

if [ ! -f "$RADAR_DIR/docker-compose.yml" ]; then
    error "docker-compose.yml not found in $RADAR_DIR"
fi

if ! command -v docker >/dev/null 2>&1; then
    error "Docker is not installed."
fi

if ! docker compose version >/dev/null 2>&1; then
    error "Docker Compose v2 is not available."
fi

if ! command -v ss >/dev/null 2>&1; then
    error "The 'ss' command is missing. Install iproute2 first: sudo apt install -y iproute2"
fi

if [ ! -f "$ENV_FILE" ]; then
    error ".env not found. Create it with your FAA/OpenSky credentials first."
fi

echo "--- Step 1: Validate credentials ---"

required_vars=(
  FAA_USER
  FAA_PASS
  QUEUE_SFDPS
  QUEUE_STDDS
  QUEUE_TFMS
  OPENSKY_CLIENT_ID
  OPENSKY_CLIENT_SECRET
)

missing=0
for var in "${required_vars[@]}"; do
    if ! grep -q "^${var}=.\+" "$ENV_FILE"; then
        echo -e "${RED}[ERROR]${NC} Missing or empty $var in .env"
        missing=1
    fi
done

if [ "$missing" -ne 0 ]; then
    exit 1
fi

info "Credentials found."

echo ""
echo "--- Step 2: Assign open web port ---"

if grep -q "^WEB_PORT=.\+" "$ENV_FILE"; then
    WEB_PORT="$(grep '^WEB_PORT=' "$ENV_FILE" | cut -d= -f2-)"
    info "Using existing WEB_PORT=$WEB_PORT"
else
    WEB_PORT="$(find_open_port 8080)"
    set_env_value "WEB_PORT" "$WEB_PORT"
    info "Assigned WEB_PORT=$WEB_PORT"
fi

chmod 600 "$ENV_FILE"

echo ""
echo "--- Step 3: Build and start containers ---"

cd "$RADAR_DIR"
docker compose up -d --build

echo ""
echo "--- Step 4: Verify containers ---"

docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E 'NAMES|flight-|swim|redis' || true

echo ""
echo "============================================="
echo -e "${GREEN}   Radar setup complete${NC}"
echo "============================================="
echo ""
echo "  Web: http://localhost:${WEB_PORT}"
echo ""
echo "  Redis is internal only."
echo "  API is internal only and reached by the web container."
echo ""
echo "Useful commands:"
echo "  docker compose ps"
echo "  docker compose logs -f"
echo "  docker compose logs -f web"
echo "  docker compose logs -f api"
echo "  docker compose logs -f swim-ingestor"
echo "  docker compose restart"
echo "  docker compose down"
echo ""
