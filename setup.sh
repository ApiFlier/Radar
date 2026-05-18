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
echo "  Tip: Most users should run ./menu.sh instead."
echo "  This script is called by menu option [1], or can"
echo "  be run directly for advanced or automated use."
echo ""
echo "Repo: $RADAR_DIR"
echo ""

if [ ! -f "$COMPOSE_FILE" ]; then
    error "docker-compose.yml not found in $RADAR_DIR. Did you clone correctly?"
fi

if [ ! -f "$DEPLOY_FILE" ]; then
    warn "deploy.env not found — running with defaults only. To add credentials, copy deploy.env.example to deploy.env."
fi

if ! command -v docker >/dev/null 2>&1; then
    error "Docker is not installed. Install it first: https://docs.docker.com/engine/install/"
fi

if ! docker compose version >/dev/null 2>&1; then
    error "Docker Compose v2 is not available. Install Docker Desktop or the docker-compose-plugin."
fi

# ── Helpers ───────────────────────────────────────────────────────────────────

check_disk_space() {
    local min_warn
    local min_critical
    local free_gb

    min_warn="$(get_env_value MIN_FREE_GB_WARN)"
    min_critical="$(get_env_value MIN_FREE_GB_CRITICAL)"
    min_warn="${min_warn:-25}"
    min_critical="${min_critical:-10}"

    free_gb=$(df -BG "$RADAR_DIR" | awk 'NR==2 {print $4}' | sed 's/G//')

    if [ "$free_gb" -lt "$min_critical" ]; then
        warn "CRITICAL: Very low disk space available: ${free_gb}GB (Threshold: ${min_critical}GB)"
        printf "Continue anyway? [y/N] "
        read -r CONTINUE < /dev/tty || true
        if [[ ! "$CONTINUE" =~ ^[Yy]$ ]]; then
            error "Setup aborted due to low disk space."
        fi
    elif [ "$free_gb" -lt "$min_warn" ]; then
        warn "Low disk space available: ${free_gb}GB (Threshold: ${min_warn}GB)"
    else
        info "Disk space check passed: ${free_gb}GB available."
    fi
}

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
    : > "$ENV_FILE"
    chmod 600 "$ENV_FILE"

    if [ ! -f "$DEPLOY_FILE" ]; then
        info "No deploy.env — using generated defaults only."
        return
    fi

    info "Creating runtime .env from deploy.env..."

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
    info "Checking system resources..."
    check_disk_space

    echo ""
    info "Adding defaults..."

    ensure_default "WEB_BIND" "0.0.0.0"
    ensure_default "WEB_PORT" "8080"

    ensure_default "REDIS_HOST" "redis"
    ensure_default "REDIS_PORT" "6379"
    ensure_default "REDIS_MAXMEMORY" "512mb"
    ensure_default "REDIS_MAXMEMORY_POLICY" "allkeys-lru"

    ensure_default "MIN_FREE_GB_WARN" "25"
    ensure_default "MIN_FREE_GB_CRITICAL" "10"
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

    # FAA SWIM: auto-detect from credentials; ENABLE_SWIM_INGESTOR=false force-disables.
    local _swim_flag _faa_user _faa_pass _q1 _q2 _q3
    _swim_flag="$(get_env_value ENABLE_SWIM_INGESTOR)"
    _faa_user="$(get_env_value FAA_USER)"
    _faa_pass="$(get_env_value FAA_PASS)"
    _q1="$(get_env_value QUEUE_SFDPS)"
    _q2="$(get_env_value QUEUE_STDDS)"
    _q3="$(get_env_value QUEUE_TFMS)"

    if [ "$_swim_flag" = "false" ]; then
        SWIM_ENABLED="false"
        info "FAA SWIM ingestor disabled (ENABLE_SWIM_INGESTOR=false)."
    elif [ -n "$_faa_user" ] && [ -n "$_faa_pass" ] && ([ -n "$_q1" ] || [ -n "$_q2" ] || [ -n "$_q3" ]); then
        SWIM_ENABLED="true"
        info "FAA credentials detected — enabling SWIM ingestor automatically."
    elif [ "$_swim_flag" = "true" ]; then
        echo -e "${RED}[ERROR]${NC} ENABLE_SWIM_INGESTOR=true but FAA credentials are incomplete."
        echo -e "${RED}[ERROR]${NC} Set FAA_USER, FAA_PASS, and at least one QUEUE_* in deploy.env."
        MISSING_REQUIRED=1
        SWIM_ENABLED="false"
    else
        SWIM_ENABLED="false"
        info "FAA SWIM ingestor not configured. To enable, add FAA_USER, FAA_PASS, and at least one QUEUE_* to deploy.env."
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

    # Credentials already validated in prepare_env; re-derive for container launch.
    local _swim_flag _faa_user _faa_pass _q1 _q2 _q3
    _swim_flag="$(get_env_value ENABLE_SWIM_INGESTOR)"
    _faa_user="$(get_env_value FAA_USER)"
    _faa_pass="$(get_env_value FAA_PASS)"
    _q1="$(get_env_value QUEUE_SFDPS)"
    _q2="$(get_env_value QUEUE_STDDS)"
    _q3="$(get_env_value QUEUE_TFMS)"

    if [ "$_swim_flag" = "false" ]; then
        SWIM_ENABLED="false"
    elif [ -n "$_faa_user" ] && [ -n "$_faa_pass" ] && ([ -n "$_q1" ] || [ -n "$_q2" ] || [ -n "$_q3" ]); then
        SWIM_ENABLED="true"
    else
        SWIM_ENABLED="false"
    fi

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

verify_health() {
    local port
    port="$(get_env_value WEB_PORT)"

    echo ""
    info "Waiting for app to become healthy at http://localhost:${port}/health ..."

    local attempts=20
    local count=0
    local success=false

    while [ "$count" -lt "$attempts" ]; do
        if curl -fsS -o /dev/null \
               --connect-timeout 2 --max-time 4 \
               "http://127.0.0.1:${port}/health" 2>/dev/null; then
            success=true
            break
        fi
        count=$((count + 1))
        echo -n "."
        sleep 3
    done
    echo ""

    if [ "$success" = true ]; then
        info "App is healthy."
        # Non-blocking aircraft count for confirmation
        local plane_count
        plane_count=$(curl -s --connect-timeout 3 --max-time 10 \
            "http://127.0.0.1:${port}/api?action=Planes&_=$(date +%s)" 2>/dev/null \
            | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    planes = d.get('response', {}).get('data', {}).get('planes', [])
    print(len(planes))
except Exception:
    pass
" 2>/dev/null || true)
        if [ -n "$plane_count" ]; then
            info "Aircraft visible: $plane_count"
        fi
    else
        warn "App did not respond within $(( attempts * 3 )) seconds."
        warn "It may still be starting. Check:"
        warn "  docker compose logs --tail=80 app"
        warn "  ./menu.sh → option [5] Troubleshoot"
    fi
}

# ── Summary ───────────────────────────────────────────────────────────────────

show_summary() {
    local port
    port="$(get_env_value WEB_PORT)"

    local _swim_flag _faa_user _faa_pass _q1 _q2 _q3
    _swim_flag="$(get_env_value ENABLE_SWIM_INGESTOR)"
    _faa_user="$(get_env_value FAA_USER)"
    _faa_pass="$(get_env_value FAA_PASS)"
    _q1="$(get_env_value QUEUE_SFDPS)"
    _q2="$(get_env_value QUEUE_STDDS)"
    _q3="$(get_env_value QUEUE_TFMS)"

    if [ "$_swim_flag" = "false" ]; then
        SWIM_ENABLED="false"
    elif [ -n "$_faa_user" ] && [ -n "$_faa_pass" ] && ([ -n "$_q1" ] || [ -n "$_q2" ] || [ -n "$_q3" ]); then
        SWIM_ENABLED="true"
    else
        SWIM_ENABLED="false"
    fi

    echo ""
    echo "================================================"
    echo -e "${GREEN}   Aviation Radar — Setup complete!${NC}"
    echo "================================================"
    echo ""
    echo "  Web UI:   http://localhost:${port}"
    echo "  LAN:      http://SERVER_IP:${port}"
    echo ""
    if [ "$SWIM_ENABLED" = "true" ]; then
        echo "  SWIM ingestor: running (FAA credentials detected)"
    else
        echo "  SWIM ingestor: not started (no FAA credentials configured)"
        echo "                 Add credentials via: ./menu.sh → option [3]"
    fi
    echo ""
    echo "  ADSB.lol: re-api requires this server's public IP to be"
    echo "            a registered feeder on adsb.lol."
    echo ""
    echo "  ── Next steps ───────────────────────────────────────────"
    echo ""
    echo "  ./menu.sh             — manage the app (recommended)"
    echo "  ./menu.sh → [2]       — update the app"
    echo "  ./menu.sh → [3]       — update API keys / credentials"
    echo "  ./menu.sh → [4]       — check status and live links"
    echo "  ./menu.sh → [5]       — troubleshoot problems"
    echo ""
    echo "  ─────────────────────────────────────────────────────────"
    echo ""
}

# ── Cleanup Prompt ────────────────────────────────────────────────────────────

cleanup_prompt() {
    REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    echo ""
    echo "  ── Optional: delete source files ────────────────────────"
    echo ""
    echo "  The containers are running and will restart automatically"
    echo "  after reboots. The source folder is no longer required."
    echo ""
    warn "  Deleting source files removes menu.sh, setup.sh, update.sh,"
    warn "  and all scripts from this machine. You will not be able to"
    warn "  run ./menu.sh or ./update.sh until you re-clone the repo."
    warn "  Docker volumes and running containers are NOT affected."
    echo ""
    printf "  Delete source files? [y/N] "
    DEL_CHOICE=""
    read -r DEL_CHOICE < /dev/tty || true
    if [ "${DEL_CHOICE}" = "y" ] || [ "${DEL_CHOICE}" = "Y" ]; then
        echo ""
        echo "  Removing source files..."
        cd "$HOME" 2>/dev/null || cd / 2>/dev/null || true
        rm -rf "$REPO_DIR"
        echo "  Done. Containers and Docker volumes are preserved."
        echo "  Manage without source:  docker ps"
        echo "                          docker logs aviation-radar-app"
        echo "                          docker stop/start aviation-radar-app"
    else
        echo "  Source files preserved. Run ./menu.sh to manage the app."
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────

prepare_env
start_containers
verify_health
show_summary
cleanup_prompt
