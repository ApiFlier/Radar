#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Aviation Radar — Deployment Setup Script
#
# Quick start:
#   git clone https://github.com/ApiFlier/aviation-radar.git radar
#   cd radar
#   cp deploy.env.example deploy.env
#   nano deploy.env
#   chmod +x setup.sh
#   ./setup.sh
#
# Put only user-supplied values (API keys, credentials) in deploy.env.
# setup.sh generates .env with production-ready defaults and starts Docker.
# =============================================================================

RADAR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$RADAR_DIR/docker-compose.yml"
DEPLOY_FILE="$RADAR_DIR/deploy.env"
ENV_FILE="$RADAR_DIR/.env"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

echo ""
echo "================================================"
echo "   Aviation Radar — Setup Script"
echo "================================================"
echo ""
echo "Repo: $RADAR_DIR"
echo ""

if [ ! -f "$COMPOSE_FILE" ]; then
    error "docker-compose.yml not found in $RADAR_DIR. Did you clone correctly?"
fi

if [ ! -f "$DEPLOY_FILE" ]; then
    error "deploy.env not found. Run: cp deploy.env.example deploy.env && nano deploy.env"
fi

if ! command -v docker >/dev/null 2>&1; then
    error "Docker is not installed. Install it first: https://docs.docker.com/engine/install/"
fi

if ! docker compose version >/dev/null 2>&1; then
    error "Docker Compose v2 is not available. Install Docker Desktop or the docker-compose-plugin."
fi

# ── Helpers ───────────────────────────────────────────────────────────────────

get_file_value() {
    local file="$1"
    local key="$2"
    grep -E "^${key}=" "$file" 2>/dev/null | tail -n 1 | cut -d= -f2- || true
}

upsert_file() {
    local file="$1"
    local key="$2"
    local value="$3"

    KEY="$key" VALUE="$value" FILE="$file" python3 - <<'PY'
from pathlib import Path
import os

path = Path(os.environ["FILE"])
key = os.environ["KEY"]
value = os.environ["VALUE"]

lines = path.read_text().splitlines() if path.exists() else []
out = []
found = False

for line in lines:
    if line.startswith(key + "="):
        out.append(f"{key}={value}")
        found = True
    else:
        out.append(line)

if not found:
    if out and out[-1].strip():
        out.append("")
    out.append(f"{key}={value}")

path.write_text("\n".join(out).rstrip() + "\n")
PY
}

get_env_value() {
    get_file_value "$ENV_FILE" "$1"
}

upsert_env() {
    upsert_file "$ENV_FILE" "$1" "$2"
}

ensure_default() {
    local key="$1"
    local value="$2"
    local current
    current="$(get_env_value "$key")"

    if [ -z "$current" ]; then
        upsert_env "$key" "$value"
        info "Added default: $key=$value"
    fi
}

normalize_value() {
    local key="$1"
    local old="$2"
    local new="$3"
    local current
    current="$(get_env_value "$key")"

    if [ "$current" = "$old" ]; then
        upsert_env "$key" "$new"
        info "Updated stale value: $key=$new"
    fi
}

import_deploy_env() {
    info "Creating runtime .env from deploy.env..."

    : > "$ENV_FILE"
    chmod 600 "$ENV_FILE"

    while IFS= read -r line || [ -n "$line" ]; do
        trimmed="${line#"${line%%[![:space:]]*}"}"

        [ -z "$trimmed" ] && continue
        [[ "$trimmed" == \#* ]] && continue
        [[ "$trimmed" != *=* ]] && continue

        key="${trimmed%%=*}"
        value="${trimmed#*=}"

        if [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
            upsert_env "$key" "$value"
        fi
    done < "$DEPLOY_FILE"
}

require_env() {
    local key="$1"
    local value
    value="$(get_env_value "$key")"

    if [ -z "$value" ]; then
        echo -e "${RED}[ERROR]${NC} Missing required value in deploy.env: $key"
        MISSING_REQUIRED=1
    fi
}

port_is_current_app() {
    local port="$1"

    docker ps --format '{{.Names}} {{.Ports}}' \
        | grep -E '^aviation-radar-app ' \
        | grep -q ":${port}->8081/tcp"
}

port_available() {
    local bind="$1"
    local port="$2"

    python3 - "$bind" "$port" <<'PY'
import socket
import sys

bind = sys.argv[1]
port = int(sys.argv[2])

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

try:
    s.bind((bind, port))
except OSError:
    sys.exit(1)
finally:
    s.close()

sys.exit(0)
PY
}

choose_web_port() {
    local bind
    local requested
    local port

    bind="$(get_env_value WEB_BIND)"
    requested="$(get_env_value WEB_PORT)"

    bind="${bind:-0.0.0.0}"
    requested="${requested:-8080}"
    port="$requested"

    while true; do
        if port_available "$bind" "$port"; then
            if [ "$port" != "$requested" ]; then
                warn "WEB_PORT $requested is busy. Using available port $port."
            else
                info "WEB_PORT $port is available."
            fi

            upsert_env "WEB_PORT" "$port"
            break
        fi

        if port_is_current_app "$port"; then
            info "WEB_PORT $port is already used by existing aviation-radar-app and will be reused."
            upsert_env "WEB_PORT" "$port"
            break
        fi

        warn "WEB_PORT $port is busy."
        port=$((port + 1))

        if [ "$port" -gt 65535 ]; then
            error "Could not find an available web port."
        fi
    done
}

# ── Prepare .env ──────────────────────────────────────────────────────────────

prepare_env() {
    import_deploy_env

    echo ""
    info "Adding defaults..."

    ensure_default "WEB_BIND" "0.0.0.0"
    ensure_default "WEB_PORT" "8080"

    ensure_default "REDIS_HOST" "redis"
    ensure_default "REDIS_PORT" "6379"
    normalize_value "REDIS_HOST" "flight-redis" "redis"
    normalize_value "REDIS_HOST" "radar-redis" "redis"
    normalize_value "REDIS_HOST" "aviation-radar-redis" "redis"

    ensure_default "FAA_URL" "tcps://ems1.swim.faa.gov:55443"

    ensure_default "ADSBLOL_REAPI_URL" "https://re-api.adsb.lol/"
    ensure_default "ADSBLOL_REAPI_BASE" "https://re-api.adsb.lol"
    ensure_default "ADSBLOL_REAPI_CIRCLES" "40.491389,-80.232778,250"
    ensure_default "ADSBLOL_REAPI_INTERVAL_SECONDS" "10"
    ensure_default "ADSBLOL_REAPI_TTL_SECONDS" "45"
    ensure_default "ADSBLOL_REAPI_REQUEST_SPACING_SECONDS" "1.2"
    ensure_default "ADSBLOL_HEALTH_MAX_AGE_SECONDS" "45"

    ensure_default "ENABLE_INTERNAL_WORKERS" "true"
    ensure_default "ENABLE_INTERNAL_ADSBLOL_REAPI" "true"
    ensure_default "ENABLE_INTERNAL_ADSBLOL_GROUND" "true"
    ensure_default "ENABLE_INTERNAL_SWIM" "false"

    ensure_default "GROUND_SWEEP_CLUSTER_INTERVAL_SECONDS" "300"
    ensure_default "GROUND_SWEEP_REQUEST_SPACING_SECONDS" "5"
    ensure_default "GROUND_SWEEP_TTL_SECONDS" "900"
    ensure_default "GROUND_SWEEP_MAX_SEEN_POS_SECONDS" "120"
    ensure_default "GROUND_SWEEP_RADIUS_NM" "250"

    echo ""
    info "Checking required values..."

    MISSING_REQUIRED=0

    # FAA SWIM credentials are only required when the SWIM ingestor is enabled.
    SWIM_ENABLED="$(get_env_value ENABLE_SWIM_INGESTOR)"
    SWIM_ENABLED="${SWIM_ENABLED:-false}"

    if [ "$SWIM_ENABLED" = "true" ]; then
        info "FAA SWIM ingestor enabled — validating FAA credentials..."
        require_env "FAA_USER"
        require_env "FAA_PASS"
        require_env "QUEUE_SFDPS"
        require_env "QUEUE_STDDS"
        require_env "QUEUE_TFMS"
    else
        info "FAA SWIM ingestor disabled (ENABLE_SWIM_INGESTOR=false). FAA credentials not required."
        info "To enable SWIM, set ENABLE_SWIM_INGESTOR=true and add FAA credentials in deploy.env."
    fi

    if [ "$MISSING_REQUIRED" = "1" ]; then
        echo ""
        error "Fix deploy.env and run setup again."
    fi

    echo ""
    info "Checking web port..."
    choose_web_port
}

# ── Start Containers ──────────────────────────────────────────────────────────

start_containers() {
    echo ""
    info "Validating Docker Compose configuration..."
    cd "$RADAR_DIR"
    docker compose config --quiet

    SWIM_ENABLED="$(get_env_value ENABLE_SWIM_INGESTOR)"
    SWIM_ENABLED="${SWIM_ENABLED:-false}"

    echo ""
    if [ "$SWIM_ENABLED" = "true" ]; then
        info "Building and starting containers (including FAA SWIM ingestor)..."
        docker compose --profile swim up -d --build --remove-orphans
    else
        info "Building and starting containers (SWIM ingestor not started)..."
        docker compose up -d --build --remove-orphans
    fi

    echo ""
    info "Container status:"
    docker compose ps
}

# ── Health Check ──────────────────────────────────────────────────────────────

verify_api() {
    echo ""
    info "Waiting 20 seconds for services to warm up..."
    sleep 20

    local port
    port="$(get_env_value WEB_PORT)"

    echo ""
    info "Checking API aircraft response..."

    if curl -sS "http://127.0.0.1:${port}/api?action=Planes&_=$(date +%s)" -o /tmp/aviation_radar_setup_planes.json; then
        python3 - <<'PY' || true
import json
from pathlib import Path
from collections import Counter

try:
    data = json.loads(Path("/tmp/aviation_radar_setup_planes.json").read_text())
except Exception as exc:
    print("Could not parse API response:", exc)
    raise SystemExit

planes = data.get("response", {}).get("data", {}).get("planes", [])

print("total:", len(planes))
print("sources:", Counter(p.get("source") or "unknown" for p in planes).most_common(10))
PY
    else
        warn "API did not respond yet. Check: docker compose logs app"
    fi
}

# ── Summary ───────────────────────────────────────────────────────────────────

show_summary() {
    local port
    port="$(get_env_value WEB_PORT)"

    SWIM_ENABLED="$(get_env_value ENABLE_SWIM_INGESTOR)"
    SWIM_ENABLED="${SWIM_ENABLED:-false}"

    echo ""
    echo "================================================"
    echo -e "${GREEN}   Aviation Radar setup complete!${NC}"
    echo "================================================"
    echo ""
    echo "  Web UI:     http://localhost:${port}"
    echo "  Local/LAN:  http://SERVER_IP:${port}"
    echo ""
    echo "  Running containers:"
    echo "    aviation-radar-app"
    echo "    aviation-radar-redis"
    if [ "$SWIM_ENABLED" = "true" ]; then
        echo "    aviation-radar-swim-ingestor"
    else
        echo "    (aviation-radar-swim-ingestor not started — ENABLE_SWIM_INGESTOR=false)"
    fi
    echo ""
    echo "  ADSB.lol note:"
    echo "    re-api access requires this server's public IP to have feeder access."
    echo ""
    echo "  Useful commands while this repo exists:"
    echo "    docker compose ps"
    echo "    docker compose logs -f"
    echo "    docker compose restart"
    echo "    docker compose down"
    echo ""
    echo "  If you delete this repo, containers keep running."
    echo "  Manage them by container name:"
    echo "    docker ps"
    echo "    docker logs aviation-radar-app"
    echo "    docker stop aviation-radar-app aviation-radar-redis"
    echo "    docker start aviation-radar-redis aviation-radar-app"
    echo ""
}

# ── Cleanup Prompt ────────────────────────────────────────────────────────────

cleanup_prompt() {
    echo ""
    read -p "Delete local source files now? [y/N] " CLEANUP

    if [[ "$CLEANUP" =~ ^[Yy]$ ]]; then
        cd /
        rm -rf "$RADAR_DIR"
        echo ""
        info "Local source files removed. Containers and Docker volumes are still running."
    else
        info "Local source files kept at $RADAR_DIR"
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────

prepare_env
start_containers
verify_api
show_summary
cleanup_prompt
