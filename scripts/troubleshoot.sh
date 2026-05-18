#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Aviation Radar — Troubleshoot & Repair
#
# Diagnoses common problems and offers safe repair options.
# Does NOT delete volumes, reset Redis, or expose secrets.
#
# Usage:
#   ./scripts/troubleshoot.sh
# =============================================================================

RADAR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$RADAR_DIR/.env"
DEPLOY_FILE="$RADAR_DIR/deploy.env"
COMPOSE_FILE="$RADAR_DIR/docker-compose.yml"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

PASS="${GREEN}OK${NC}"
FAIL="${RED}FAIL${NC}"
WARN="${YELLOW}WARN${NC}"
SKIP="${YELLOW}SKIP${NC}"

ok()   { echo -e "  ${GREEN}[OK]${NC}   $1"; }
fail() { echo -e "  ${RED}[FAIL]${NC} $1"; }
warn() { echo -e "  ${YELLOW}[WARN]${NC} $1"; }
info() { echo -e "  ${GREEN}[INFO]${NC} $1"; }

# ── Helpers ───────────────────────────────────────────────────────────────────

get_env_value() {
    grep -E "^${1}=" "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- || true
}

confirm() {
    local prompt="$1"
    printf "  %s [y/N] " "$prompt"
    local ans=""
    read -r ans < /dev/tty || true
    [[ "$ans" =~ ^[Yy]$ ]]
}

# ── Diagnostics ───────────────────────────────────────────────────────────────

run_diagnostics() {
    echo ""
    echo -e "${CYAN}── Diagnostics ──────────────────────────────────────────────────────────${NC}"
    echo ""

    # Docker CLI
    if command -v docker >/dev/null 2>&1; then
        ok "Docker CLI available"
    else
        fail "Docker CLI not found — install Docker first"
        echo ""
        echo "  Nothing else can be checked without Docker. Exiting."
        exit 1
    fi

    # Docker daemon
    if docker info >/dev/null 2>&1; then
        ok "Docker daemon running"
    else
        fail "Docker daemon is not running — start it with: sudo systemctl start docker"
    fi

    # Docker Compose v2
    if docker compose version >/dev/null 2>&1; then
        ok "Docker Compose v2 available"
    else
        fail "Docker Compose v2 not found — install docker-compose-plugin"
    fi

    # docker-compose.yml
    if [ -f "$COMPOSE_FILE" ]; then
        ok "docker-compose.yml found"
        if docker compose -f "$COMPOSE_FILE" config --quiet 2>/dev/null; then
            ok "docker-compose.yml validates cleanly"
        else
            fail "docker-compose.yml has a syntax or config error"
        fi
    else
        fail "docker-compose.yml not found in $RADAR_DIR"
    fi

    # .env
    if [ -f "$ENV_FILE" ]; then
        ok ".env found"
    else
        fail ".env not found — run ./setup.sh to generate it"
    fi

    # deploy.env
    if [ -f "$DEPLOY_FILE" ]; then
        ok "deploy.env found"
    else
        warn "deploy.env not found (optional — app runs on public ADS-B without it)"
    fi

    echo ""
    echo -e "${CYAN}── Container Status ─────────────────────────────────────────────────────${NC}"
    echo ""

    _check_container "aviation-radar-app"           "App"
    _check_container "aviation-radar-redis"         "Redis"
    _check_container "aviation-radar-swim-ingestor" "SWIM ingestor"

    echo ""
    echo -e "${CYAN}── App Health ───────────────────────────────────────────────────────────${NC}"
    echo ""

    _check_health_endpoint

    echo ""
    echo -e "${CYAN}── Recent Log Summary ───────────────────────────────────────────────────${NC}"
    echo ""

    _check_logs_for_errors
}

_check_container() {
    local name="$1"
    local label="$2"

    local status
    status=$(docker ps --filter "name=^${name}$" --format "{{.Status}}" 2>/dev/null || true)

    if [ -n "$status" ]; then
        ok "$label ($name): $status"
    else
        local exited
        exited=$(docker ps -a --filter "name=^${name}$" --format "{{.Status}}" 2>/dev/null || true)
        if [ -n "$exited" ]; then
            fail "$label ($name): $exited"
        else
            warn "$label ($name): not found / not started"
        fi
    fi
}

_check_health_endpoint() {
    if [ ! -f "$ENV_FILE" ]; then
        warn "Skipping health check — .env not found"
        return
    fi

    local port
    port="$(get_env_value WEB_PORT)"
    port="${port:-8080}"

    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${port}/health" 2>/dev/null || echo "000")

    if [ "$http_code" = "200" ]; then
        ok "Health endpoint http://localhost:${port}/health → HTTP $http_code"
    elif [ "$http_code" = "000" ]; then
        fail "Health endpoint http://localhost:${port}/health → no response (app not reachable)"
    else
        fail "Health endpoint http://localhost:${port}/health → HTTP $http_code"
    fi

    # Worker status
    local ws_code
    ws_code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${port}/api/workers/status" 2>/dev/null || echo "000")
    if [ "$ws_code" = "200" ]; then
        local ws_body
        ws_body=$(curl -s "http://localhost:${port}/api/workers/status" 2>/dev/null || true)
        local running degraded
        running=$(echo "$ws_body" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('running_workers','-'))" 2>/dev/null || echo "-")
        degraded=$(echo "$ws_body" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('degraded_workers','-'))" 2>/dev/null || echo "-")
        ok "Workers: ${running} running, ${degraded} degraded"
    fi
}

_check_logs_for_errors() {
    local app_errors redis_errors swim_errors

    app_errors=$(docker logs --tail=80 aviation-radar-app 2>&1 \
        | grep -Ec "ERROR|Traceback|Exception|CRITICAL" || true)
    redis_errors=$(docker logs --tail=40 aviation-radar-redis 2>&1 \
        | grep -Ec "ERROR|CRITICAL|OOM" || true)
    swim_errors=$(docker logs --tail=40 aviation-radar-swim-ingestor 2>&1 \
        | grep -Ec "ERROR|Traceback|Exception|CRITICAL|disconnect" 2>/dev/null || true)

    if [ "${app_errors:-0}" -gt 0 ]; then
        warn "App logs: ${app_errors} error-level lines in last 80 — check with: docker logs --tail=80 aviation-radar-app"
    else
        ok "App logs: no error-level lines in last 80"
    fi

    if [ "${redis_errors:-0}" -gt 0 ]; then
        warn "Redis logs: ${redis_errors} error-level lines in last 40"
    else
        ok "Redis logs: clean"
    fi

    if docker ps --filter "name=^aviation-radar-swim-ingestor$" --format "{{.Names}}" | grep -q "swim"; then
        if [ "${swim_errors:-0}" -gt 0 ]; then
            warn "SWIM logs: ${swim_errors} error-level lines in last 40"
        else
            ok "SWIM ingestor logs: clean"
        fi
    fi
}

# ── Repair Menu ───────────────────────────────────────────────────────────────

repair_menu() {
    echo ""
    echo -e "${CYAN}── Safe Repair Options ──────────────────────────────────────────────────${NC}"
    echo ""
    echo "  1) Restart app container"
    echo "  2) Restart Redis + app containers"
    echo "  3) Rebuild app container (brief service interruption)"
    echo "  4) Show recent app logs (last 80 lines)"
    echo "  5) Show recent Redis logs (last 40 lines)"
    echo "  6) Show recent SWIM ingestor logs (last 40 lines)"
    echo "  7) Re-run diagnostics"
    echo "  8) Return / exit"
    echo ""
    printf "  Choose [1-8]: "

    local choice=""
    read -r choice < /dev/tty || true

    case "$choice" in
        1) _repair_restart_app ;;
        2) _repair_restart_redis_app ;;
        3) _repair_rebuild_app ;;
        4) _show_app_logs ;;
        5) _show_redis_logs ;;
        6) _show_swim_logs ;;
        7) run_diagnostics ; repair_menu ;;
        8|"") echo "  Returning." ; return ;;
        *) warn "Unknown option." ; repair_menu ;;
    esac
}

_repair_restart_app() {
    echo ""
    warn "This will briefly interrupt the app."
    if confirm "Restart aviation-radar-app?"; then
        cd "$RADAR_DIR"
        docker compose restart app
        info "App container restarted."
        sleep 3
        _check_health_endpoint
    else
        echo "  Cancelled."
    fi
    repair_menu
}

_repair_restart_redis_app() {
    echo ""
    warn "Redis ephemeral state will be lost (in-memory TTL data only)."
    warn "Named volume (redis_data) is NOT deleted. Persistent data is safe."
    if confirm "Restart Redis and app containers?"; then
        cd "$RADAR_DIR"
        docker compose restart redis app
        info "Redis and app restarted."
        sleep 5
        _check_health_endpoint
    else
        echo "  Cancelled."
    fi
    repair_menu
}

_repair_rebuild_app() {
    echo ""
    warn "This will rebuild the app image and recreate the container."
    warn "Brief service interruption. Redis data is NOT affected."
    if confirm "Rebuild and restart app container?"; then
        cd "$RADAR_DIR"
        docker compose up -d --build --no-deps app
        info "App rebuilt and restarted."
        sleep 5
        _check_health_endpoint
    else
        echo "  Cancelled."
    fi
    repair_menu
}

_show_app_logs() {
    echo ""
    echo -e "${CYAN}── App Logs (last 80 lines) ─────────────────────────────────────────────${NC}"
    echo ""
    docker logs --tail=80 aviation-radar-app 2>&1 || true
    echo ""
    repair_menu
}

_show_redis_logs() {
    echo ""
    echo -e "${CYAN}── Redis Logs (last 40 lines) ───────────────────────────────────────────${NC}"
    echo ""
    docker logs --tail=40 aviation-radar-redis 2>&1 || true
    echo ""
    repair_menu
}

_show_swim_logs() {
    echo ""
    echo -e "${CYAN}── SWIM Ingestor Logs (last 40 lines) ───────────────────────────────────${NC}"
    echo ""
    if docker ps -a --filter "name=^aviation-radar-swim-ingestor$" --format "{{.Names}}" | grep -q "swim"; then
        docker logs --tail=40 aviation-radar-swim-ingestor 2>&1 || true
    else
        warn "SWIM ingestor container not found. It only runs when FAA credentials are configured."
    fi
    echo ""
    repair_menu
}

# ── Main ──────────────────────────────────────────────────────────────────────

echo ""
echo "================================================"
echo "   Aviation Radar — Troubleshoot & Repair"
echo "================================================"

run_diagnostics
repair_menu

echo ""
