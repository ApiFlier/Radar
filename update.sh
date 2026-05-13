#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Aviation Radar — Production Update Script
#
# Usage:
#   chmod +x update.sh
#   ./update.sh [--force]
#
# This script pulls the latest code, rebuilds containers, and preserves volumes.
# =============================================================================

RADAR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$RADAR_DIR/docker-compose.yml"
ENV_FILE="$RADAR_DIR/.env"
DEPLOY_FILE="$RADAR_DIR/deploy.env"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

FORCE=false
if [[ "${1:-}" == "--force" ]]; then
    FORCE=true
fi

echo ""
echo "================================================"
echo "   Aviation Radar — Update Script"
echo "================================================"
echo ""

# ── Verification ──────────────────────────────────────────────────────────────

if [ ! -d "$RADAR_DIR/.git" ]; then
    error "Current directory is not a git repository. update.sh requires the repo folder."
fi

if [ ! -f "$ENV_FILE" ]; then
    error ".env not found. Please run ./setup.sh first."
fi

if [ ! -f "$COMPOSE_FILE" ]; then
    error "docker-compose.yml not found in $RADAR_DIR."
fi

if ! command -v docker >/dev/null 2>&1; then
    error "Docker is not installed."
fi

if ! docker compose version >/dev/null 2>&1; then
    error "Docker Compose v2 is not available."
fi

# ── Helpers ───────────────────────────────────────────────────────────────────

get_env_value() {
    local key="$1"
    grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- || true
}

get_deploy_value() {
    local key="$1"
    if [ -f "$DEPLOY_FILE" ]; then
        grep -E "^${key}=" "$DEPLOY_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- || true
    fi
}

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
        if [ "$FORCE" = false ]; then
            printf "Continue anyway? [y/N] "
            read -r CONTINUE < /dev/tty || true
            if [[ ! "$CONTINUE" =~ ^[Yy]$ ]]; then
                error "Update aborted due to low disk space."
            fi
        fi
    elif [ "$free_gb" -lt "$min_warn" ]; then
        warn "Low disk space available: ${free_gb}GB (Threshold: ${min_warn}GB)"
    else
        info "Disk space check passed: ${free_gb}GB available."
    fi
}

# ── Git Update ────────────────────────────────────────────────────────────────

update_repo() {
    info "Checking repository state..."

    local is_dirty=false
    if [ -n "$(git status --porcelain)" ]; then
        is_dirty=true
    fi

    if [ "$is_dirty" = true ] && [ "$FORCE" = false ]; then
        error "Working tree is dirty. Commit, stash, or run with --force to discard changes."
    fi

    local upstream
    upstream=$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null || echo "")

    if [ -z "$upstream" ]; then
        warn "No upstream branch detected. Falling back to origin/main or origin/dev."
        if git rev-parse --verify origin/main >/dev/null 2>&1; then
            upstream="origin/main"
        elif git rev-parse --verify origin/dev >/dev/null 2>&1; then
            upstream="origin/dev"
        else
            error "Could not determine upstream branch."
        fi
        info "Using upstream: $upstream"
    fi

    info "Fetching latest code from origin..."
    git fetch origin

    if [ "$FORCE" = true ]; then
        warn "FORCE enabled: Resetting to $upstream. Any local changes will be lost!"
        git reset --hard "$upstream"
    else
        info "Pulling latest code (fast-forward only) from $upstream..."
        if ! git merge-base --is-ancestor HEAD "$upstream"; then
             error "Local branch has diverged from $upstream. Run with --force to overwrite, or merge manually."
        fi
        git merge --ff-only "$upstream"
    fi
}

# ── Docker Update ─────────────────────────────────────────────────────────────

update_containers() {
    info "Detecting SWIM configuration..."

    local swim_enabled
    swim_enabled="$(get_env_value ENABLE_SWIM_INGESTOR)"
    swim_enabled="${swim_enabled:-false}"

    # Auto-detect if SWIM should be enabled if not already
    if [ "$swim_enabled" != "true" ]; then
        local faa_user faa_pass q1 q2 q3
        faa_user="$(get_deploy_value FAA_USER)"
        faa_pass="$(get_deploy_value FAA_PASS)"
        q1="$(get_deploy_value QUEUE_SFDPS)"
        q2="$(get_deploy_value QUEUE_STDDS)"
        q3="$(get_deploy_value QUEUE_TFMS)"

        if [ -n "$faa_user" ] && [ -n "$faa_pass" ] && ([ -n "$q1" ] || [ -n "$q2" ] || [ -n "$q3" ]); then
            info "FAA credentials detected in deploy.env. Enabling SWIM profile..."
            swim_enabled="true"
        fi
    fi

    info "Rebuilding and updating containers..."
    if [ "$swim_enabled" = "true" ]; then
        docker compose --profile swim up -d --build --remove-orphans
    else
        docker compose up -d --build --remove-orphans
    fi
}

# ── Health Check ──────────────────────────────────────────────────────────────

check_health() {
    local port
    port="$(get_env_value WEB_PORT)"
    port="${port:-8080}"

    info "Waiting for application to become healthy (http://localhost:${port}/health)..."

    local attempts=15
    local count=0
    local success=false

    while [ "$count" -lt "$attempts" ]; do
        if curl -s "http://localhost:${port}/health" > /dev/null; then
            success=true
            break
        fi
        count=$((count + 1))
        echo -n "."
        sleep 2
    done
    echo ""

    if [ "$success" = true ]; then
        info "Application is healthy."
        
        # Optional worker status check
        if curl -s "http://localhost:${port}/api/workers/status" | grep -q "status"; then
            info "Worker status check passed."
        else
            warn "Worker status check returned unexpected response or is unavailable."
        fi
    else
        error "Application failed to become healthy. Check logs:\n  docker logs --tail=150 aviation-radar-app\n  docker logs --tail=150 aviation-radar-swim-ingestor\n  docker logs --tail=150 aviation-radar-redis"
    fi
}

# ── Final Status ──────────────────────────────────────────────────────────────

show_status() {
    local port
    port="$(get_env_value WEB_PORT)"
    port="${port:-8080}"

    echo ""
    info "Final Status:"
    docker compose ps --filter "name=aviation-radar"
    echo ""
    info "URL: http://localhost:${port}"
    
    local swim_enabled
    swim_enabled="$(get_env_value ENABLE_SWIM_INGESTOR)"
    swim_enabled="${swim_enabled:-false}"
    if [ "$swim_enabled" = "true" ]; then
        info "SWIM: Enabled"
    else
        info "SWIM: Disabled"
    fi

    check_disk_space

    echo ""
    info "Running optional resource cleanup..."
    if [ -f "$RADAR_DIR/scripts/docker-cleanup.sh" ]; then
        bash "$RADAR_DIR/scripts/docker-cleanup.sh"
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────

check_disk_space
update_repo
update_containers
check_health
show_status
