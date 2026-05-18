#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Aviation Radar — Main Menu
#
# The primary entry point for managing Aviation Radar.
#
# Usage:
#   chmod +x menu.sh
#   ./menu.sh
# =============================================================================

RADAR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$RADAR_DIR/.env"
DEPLOY_FILE="$RADAR_DIR/deploy.env"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ── Status helpers ─────────────────────────────────────────────────────────────

get_env_value() {
    grep -E "^${1}=" "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- || true
}

deploy_is_set() {
    local val
    val="$(grep -E "^${1}=.+" "$DEPLOY_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2-)" || true
    [ -n "$val" ]
}

container_status() {
    docker ps --filter "name=^${1}$" --format "{{.Status}}" 2>/dev/null || true
}

# ── Status block ───────────────────────────────────────────────────────────────

show_status() {
    echo ""
    echo -e "${CYAN}  ── Live Status ─────────────────────────────────────────────────────────${NC}"

    # Docker
    if ! command -v docker >/dev/null 2>&1; then
        echo -e "  Docker:      ${RED}not found${NC} — install Docker to use this app"
        echo ""
        return
    fi

    if ! docker info >/dev/null 2>&1; then
        echo -e "  Docker:      ${RED}daemon not running${NC}"
        echo ""
        return
    fi

    echo -e "  Docker:      ${GREEN}running${NC}"

    # App container
    local app_st
    app_st="$(container_status "aviation-radar-app")"
    if [ -n "$app_st" ]; then
        echo -e "  App:         ${GREEN}${app_st}${NC}"
    else
        local app_ex
        app_ex="$(docker ps -a --filter "name=^aviation-radar-app$" --format "{{.Status}}" 2>/dev/null || true)"
        if [ -n "$app_ex" ]; then
            echo -e "  App:         ${RED}stopped — ${app_ex}${NC}"
        else
            echo -e "  App:         ${YELLOW}not created${NC} — run option 1 to set up"
        fi
    fi

    # Redis container
    local redis_st
    redis_st="$(container_status "aviation-radar-redis")"
    if [ -n "$redis_st" ]; then
        echo -e "  Redis:       ${GREEN}${redis_st}${NC}"
    else
        echo -e "  Redis:       ${YELLOW}not running${NC}"
    fi

    # SWIM ingestor
    local swim_st
    swim_st="$(container_status "aviation-radar-swim-ingestor")"
    if [ -n "$swim_st" ]; then
        echo -e "  SWIM:        ${GREEN}${swim_st}${NC}"
    else
        # Check whether credentials are configured (without showing values)
        if [ -f "$DEPLOY_FILE" ] && \
           deploy_is_set "FAA_USER" && \
           deploy_is_set "FAA_PASS" && \
           { deploy_is_set "QUEUE_SFDPS" || deploy_is_set "QUEUE_STDDS" || deploy_is_set "QUEUE_TFMS"; }; then
            echo -e "  SWIM:        ${YELLOW}not running${NC} (credentials configured — run setup/update)"
        else
            echo -e "  SWIM:        ${YELLOW}not started${NC} (no FAA credentials configured)"
        fi
    fi

    # Port and health
    if [ -f "$ENV_FILE" ]; then
        local port
        port="$(get_env_value WEB_PORT)"
        port="${port:-8080}"

        local http_code
        http_code=$(curl -s -o /dev/null -w "%{http_code}" \
            --connect-timeout 2 --max-time 4 \
            "http://localhost:${port}/health" 2>/dev/null || echo "000")

        if [ "$http_code" = "200" ]; then
            echo -e "  URL:         ${GREEN}http://localhost:${port}${NC}  (health: OK)"
        elif [ "$http_code" = "000" ]; then
            echo -e "  URL:         http://localhost:${port}  (health: ${RED}no response${NC})"
        else
            echo -e "  URL:         http://localhost:${port}  (health: ${YELLOW}HTTP ${http_code}${NC})"
        fi
    else
        echo -e "  URL:         ${YELLOW}unknown${NC} — run setup first (.env not found)"
    fi

    # Optional sources summary (no values printed)
    local sources=""
    if [ -f "$DEPLOY_FILE" ]; then
        if deploy_is_set "OPENSKY_CLIENT_ID" && deploy_is_set "OPENSKY_CLIENT_SECRET"; then
            sources="OpenSky (configured)"
        else
            sources="OpenSky (not configured)"
        fi
    fi
    if [ -n "$sources" ]; then
        echo -e "  Sources:     ${sources}"
    fi

    echo ""
}

# ── Banner ─────────────────────────────────────────────────────────────────────

show_banner() {
    clear
    echo ""
    echo -e "${BOLD}  ╔══════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}  ║         Aviation Radar                  ║${NC}"
    echo -e "${BOLD}  ╚══════════════════════════════════════════╝${NC}"
    show_status
    echo -e "${CYAN}  ── Main Menu ──────────────────────────────────────────────────────────${NC}"
    echo ""
    echo "    1)  Set up the app for the first time"
    echo "    2)  Update the app"
    echo "    3)  Update API keys / credentials"
    echo "    4)  Check app status and links"
    echo "    5)  Troubleshoot / repair common problems"
    echo "    6)  Advanced tools"
    echo "    7)  Quit"
    echo ""
}

# ── Option: Setup ─────────────────────────────────────────────────────────────

do_setup() {
    echo ""
    if [ -f "$ENV_FILE" ]; then
        echo -e "  ${YELLOW}Note:${NC} .env already exists. setup.sh will regenerate it from deploy.env."
        echo "  Your credentials in deploy.env are preserved. Containers will be rebuilt."
        echo ""
        printf "  Continue with setup? [y/N] "
        local ans=""
        read -r ans < /dev/tty || true
        [[ "$ans" =~ ^[Yy]$ ]] || { echo "  Cancelled."; return; }
    fi
    echo ""
    bash "$RADAR_DIR/setup.sh"
}

# ── Option: Update ────────────────────────────────────────────────────────────

do_update() {
    echo ""
    if [ ! -f "$ENV_FILE" ]; then
        echo -e "  ${RED}Error:${NC} .env not found. Run option 1 (Set up) first."
        echo ""
        printf "  Press Enter to return to menu."
        read -r < /dev/tty || true
        return
    fi
    bash "$RADAR_DIR/update.sh"
}

# ── Option: Credentials ───────────────────────────────────────────────────────

do_credentials() {
    echo ""
    bash "$RADAR_DIR/scripts/configure-credentials.sh"
}

# ── Option: Status ────────────────────────────────────────────────────────────

do_status() {
    echo ""
    echo "================================================"
    echo "   Aviation Radar — Status"
    echo "================================================"
    echo ""

    if ! command -v docker >/dev/null 2>&1; then
        echo -e "  ${RED}Docker not found.${NC} Install Docker to run this app."
        echo ""
        printf "  Press Enter to return to menu."
        read -r < /dev/tty || true
        return
    fi

    echo -e "${CYAN}  Containers:${NC}"
    docker ps --filter "name=aviation-radar" \
        --format "  {{.Names}}\t{{.Status}}" 2>/dev/null || true

    if [ -f "$ENV_FILE" ]; then
        local port
        port="$(get_env_value WEB_PORT)"
        port="${port:-8080}"

        echo ""
        echo -e "${CYAN}  App URL:${NC}     http://localhost:${port}"
        echo -e "${CYAN}  LAN URL:${NC}     http://SERVER_IP:${port}"

        echo ""
        echo -e "${CYAN}  Health endpoint:${NC}"
        local http_code
        http_code=$(curl -s -o /dev/null -w "%{http_code}" \
            --connect-timeout 3 --max-time 6 \
            "http://localhost:${port}/health" 2>/dev/null || echo "000")

        if [ "$http_code" = "200" ]; then
            echo -e "    http://localhost:${port}/health → ${GREEN}HTTP 200 OK${NC}"
        else
            echo -e "    http://localhost:${port}/health → ${RED}HTTP ${http_code}${NC}"
        fi

        echo ""
        echo -e "${CYAN}  Worker status:${NC}"
        local ws_body ws_code
        ws_code=$(curl -s -o /dev/null -w "%{http_code}" \
            --connect-timeout 3 --max-time 6 \
            "http://localhost:${port}/api/workers/status" 2>/dev/null || echo "000")

        if [ "$ws_code" = "200" ]; then
            ws_body=$(curl -s --connect-timeout 3 --max-time 6 \
                "http://localhost:${port}/api/workers/status" 2>/dev/null || true)
            echo "$ws_body" | python3 - <<'PY' 2>/dev/null || echo "    (could not parse worker status)"
import json, sys
d = json.load(sys.stdin)
total   = d.get("total_workers", "?")
running = d.get("running_workers", "?")
degrad  = d.get("degraded_workers", "?")
errors  = d.get("error_workers", "?")
print(f"    total={total}  running={running}  degraded={degrad}  errors={errors}")
for name, w in d.get("workers", {}).items():
    status = w.get("status", "?")
    print(f"    • {name}: {status}")
PY
        else
            echo "    Worker status endpoint not reachable."
        fi
    else
        echo ""
        echo -e "  ${YELLOW}Setup not complete:${NC} .env not found. Run option 1 first."
    fi

    echo ""
    printf "  Press Enter to return to menu."
    read -r < /dev/tty || true
}

# ── Option: Troubleshoot ──────────────────────────────────────────────────────

do_troubleshoot() {
    echo ""
    bash "$RADAR_DIR/scripts/troubleshoot.sh"
    echo ""
    printf "  Press Enter to return to menu."
    read -r < /dev/tty || true
}

# ── Option: Advanced ──────────────────────────────────────────────────────────

do_advanced() {
    clear
    echo ""
    echo -e "${BOLD}  ╔══════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}  ║         Advanced Tools                  ║${NC}"
    echo -e "${BOLD}  ╚══════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${YELLOW}These options are for experienced users.${NC}"
    echo ""
    echo "    1)  Run setup.sh directly"
    echo "    2)  Run update.sh directly"
    echo "    3)  Rebuild app container only"
    echo "    4)  Show app logs (live — Ctrl+C to stop)"
    echo "    5)  Show Redis logs (last 60 lines)"
    echo "    6)  Show SWIM ingestor logs (last 60 lines)"
    echo "    7)  Run Docker cleanup (prune build cache / dangling images)"
    echo "    8)  Return to main menu"
    echo ""
    printf "  Choose [1-8]: "

    local choice=""
    read -r choice < /dev/tty || true

    case "$choice" in
        1)
            echo ""
            bash "$RADAR_DIR/setup.sh"
            printf "  Press Enter to return."
            read -r < /dev/tty || true
            do_advanced
            ;;
        2)
            echo ""
            bash "$RADAR_DIR/update.sh"
            printf "  Press Enter to return."
            read -r < /dev/tty || true
            do_advanced
            ;;
        3)
            echo ""
            echo -e "  ${YELLOW}Rebuilding app container. Brief service interruption.${NC}"
            printf "  Continue? [y/N] "
            local ans=""
            read -r ans < /dev/tty || true
            if [[ "$ans" =~ ^[Yy]$ ]]; then
                cd "$RADAR_DIR"
                docker compose up -d --build --no-deps app
                echo -e "  ${GREEN}Done.${NC}"
            else
                echo "  Cancelled."
            fi
            printf "  Press Enter to return."
            read -r < /dev/tty || true
            do_advanced
            ;;
        4)
            echo ""
            echo "  Streaming app logs. Press Ctrl+C to stop."
            echo ""
            cd "$RADAR_DIR"
            docker compose logs -f --tail=40 app 2>/dev/null || true
            do_advanced
            ;;
        5)
            echo ""
            cd "$RADAR_DIR"
            docker compose logs --tail=60 redis 2>/dev/null || true
            printf "  Press Enter to return."
            read -r < /dev/tty || true
            do_advanced
            ;;
        6)
            echo ""
            if docker ps -a --filter "name=^aviation-radar-swim-ingestor$" --format "{{.Names}}" 2>/dev/null | grep -q "swim"; then
                cd "$RADAR_DIR"
                docker compose logs --tail=60 swim-ingestor 2>/dev/null || true
            else
                echo -e "  ${YELLOW}SWIM ingestor not running.${NC} Starts automatically when FAA credentials are set."
            fi
            printf "  Press Enter to return."
            read -r < /dev/tty || true
            do_advanced
            ;;
        7)
            echo ""
            if [ -f "$RADAR_DIR/scripts/docker-cleanup.sh" ]; then
                bash "$RADAR_DIR/scripts/docker-cleanup.sh"
            else
                echo -e "  ${YELLOW}docker-cleanup.sh not found.${NC}"
            fi
            printf "  Press Enter to return."
            read -r < /dev/tty || true
            do_advanced
            ;;
        8|"")
            return
            ;;
        *)
            echo -e "  ${YELLOW}Unknown option.${NC}"
            sleep 1
            do_advanced
            ;;
    esac
}

# ── Main loop ─────────────────────────────────────────────────────────────────

while true; do
    show_banner
    printf "  Choose [1-7]: "
    CHOICE=""
    read -r CHOICE < /dev/tty || true

    case "$CHOICE" in
        1) do_setup ;;
        2) do_update ;;
        3) do_credentials ;;
        4) do_status ;;
        5) do_troubleshoot ;;
        6) do_advanced ;;
        7|q|Q|quit|exit)
            echo ""
            echo "  Goodbye."
            echo ""
            exit 0
            ;;
        *)
            echo ""
            echo -e "  ${YELLOW}Please choose 1–7.${NC}"
            sleep 1
            ;;
    esac
done
