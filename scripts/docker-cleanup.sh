#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Aviation Radar — Docker Cleanup Helper
#
# This script prunes build cache and unused images to free up space.
# It does NOT remove volumes or running containers.
# =============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }

info "Cleaning up Docker resources..."

# Prune build cache (can be quite large)
info "Pruning Docker build cache..."
docker builder prune -f --filter "until=24h"

# Prune dangling images
info "Pruning dangling images..."
docker image prune -f

info "Docker cleanup complete."
