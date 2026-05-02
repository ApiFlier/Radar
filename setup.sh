#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Radar quick setup
# ============================================================
#
# Recommended install:
#
#   git clone <private repo>
#   cd Radar
#   cp deploy.env.example deploy.env
#   nano deploy.env
#   chmod +x setup.sh
#   ./setup.sh
#
# Required values go in deploy.env before running setup.sh.
# Setup does not prompt for credentials. It reads deploy.env,
# creates a runtime copy under /opt/radar by default, generates
# the runtime .env, starts containers, then asks whether to
# delete the local source checkout.
#
# Credential / access links:
#
#   FAA API Portal:
#     https://portal.apic4e.faa.gov/
#
#   FAA SWIM / NAS Enterprise Messaging:
#     https://www.faa.gov/air_traffic/technology/swim
#
#   OpenSky Network:
#     https://opensky-network.org/
#
#   ADSB.lol feeder / re-api docs:
#     https://www.adsb.lol/docs/
#
# ============================================================

APP_NAME="Radar"
DEFAULT_INSTALL_DIR="/opt/radar"
DEFAULT_WEB_PORT="8080"

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${CONFIG_FILE:-}"
INSTALL_DIR="${INSTALL_DIR:-}"

echo "========================================"
echo " ${APP_NAME} setup"
echo "========================================"
echo ""

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is not installed or not in PATH."
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: docker compose is not available."
  exit 1
fi

# Deployment config source.
if [ -z "$CONFIG_FILE" ]; then
  CONFIG_FILE="$SRC_DIR/deploy.env"
fi

if [ ! -f "$CONFIG_FILE" ]; then
  echo "ERROR: Deployment config not found: $CONFIG_FILE"
  echo ""
  echo "Create it first:"
  echo "  cp deploy.env.example deploy.env"
  echo "  nano deploy.env"
  echo "  ./setup.sh"
  exit 1
fi

if [ -z "$INSTALL_DIR" ]; then
  INSTALL_DIR="$(grep -E '^INSTALL_DIR=' "$CONFIG_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- || true)"
  INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
fi

INSTALL_DIR="$(realpath -m "$INSTALL_DIR")"

echo "Source directory:   $SRC_DIR"
echo "Runtime directory:  $INSTALL_DIR"
echo "Deployment config:  $CONFIG_FILE"
echo ""

if [ "$SRC_DIR" = "$INSTALL_DIR" ]; then
  echo "ERROR: Runtime directory must be separate from the source checkout."
  echo "Use INSTALL_DIR=/opt/radar or another separate runtime directory."
  exit 1
fi

make_runtime_dir() {
  if mkdir -p "$INSTALL_DIR" 2>/dev/null; then
    return
  fi

  if command -v sudo >/dev/null 2>&1; then
    echo "Creating $INSTALL_DIR with sudo..."
    sudo mkdir -p "$INSTALL_DIR"
    sudo chown "$(id -u):$(id -g)" "$INSTALL_DIR"
  else
    echo "ERROR: Could not create $INSTALL_DIR and sudo is not available."
    exit 1
  fi
}

copy_project() {
  echo "Copying project files into runtime directory..."

  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
      --exclude '.git/' \
      --exclude '.env' \
      --exclude 'deploy.env' \
      --exclude '__pycache__/' \
      --exclude '*.pyc' \
      --exclude '.pytest_cache/' \
      --exclude 'node_modules/' \
      --exclude 'data/airports.csv' \
      --exclude 'data/airport-frequencies.csv' \
      "$SRC_DIR/" "$INSTALL_DIR/"
  else
    echo "rsync not found; using tar fallback."
    tar \
      --exclude='.git' \
      --exclude='.env' \
      --exclude='deploy.env' \
      --exclude='__pycache__' \
      --exclude='*.pyc' \
      --exclude='.pytest_cache' \
      --exclude='node_modules' \
      --exclude='data/airports.csv' \
      --exclude='data/airport-frequencies.csv' \
      -C "$SRC_DIR" -cf - . | tar -C "$INSTALL_DIR" -xf -
  fi
}

ENV_FILE=""

get_file_value() {
  local file="$1"
  local key="$2"
  grep -E "^${key}=" "$file" 2>/dev/null | tail -n 1 | cut -d= -f2- || true
}

get_env_value() {
  get_file_value "$ENV_FILE" "$1"
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

upsert_env() {
  upsert_file "$ENV_FILE" "$1" "$2"
}

upsert_config_if_possible() {
  local key="$1"
  local value="$2"

  if [ -w "$CONFIG_FILE" ]; then
    upsert_file "$CONFIG_FILE" "$key" "$value"
  fi
}

ensure_default() {
  local key="$1"
  local value="$2"
  local current
  current="$(get_env_value "$key")"

  if [ -z "$current" ]; then
    upsert_env "$key" "$value"
    echo "Added default: $key=$value"
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
    echo "Updated stale value: $key=$new"
  fi
}

import_config_file() {
  local file="$1"

  echo "Importing deployment config..."

  while IFS= read -r line || [ -n "$line" ]; do
    trimmed="${line#"${line%%[![:space:]]*}"}"

    [ -z "$trimmed" ] && continue
    [[ "$trimmed" == \#* ]] && continue
    [[ "$trimmed" != *=* ]] && continue

    key="${trimmed%%=*}"
    value="${trimmed#*=}"

    if [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      case "$key" in
        INSTALL_DIR)
          ;;
        *)
          upsert_env "$key" "$value"
          ;;
      esac
    fi
  done < "$file"
}

require_env() {
  local key="$1"
  local value
  value="$(get_env_value "$key")"

  if [ -z "$value" ]; then
    echo "ERROR: Missing required value in deploy.env: $key"
    missing_required=1
  fi
}

port_is_current_radar_web() {
  local port="$1"

  docker ps --format '{{.Names}} {{.Ports}}' \
    | grep -E '^radar-web ' \
    | grep -q ":${port}->8080/tcp"
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
  requested="${requested:-$DEFAULT_WEB_PORT}"

  port="$requested"

  while true; do
    if port_available "$bind" "$port"; then
      if [ "$port" != "$requested" ]; then
        echo "WEB_PORT $requested is busy. Using available port $port."
      else
        echo "WEB_PORT $port is available."
      fi

      upsert_env "WEB_PORT" "$port"
      upsert_config_if_possible "WEB_PORT" "$port"
      break
    fi

    if port_is_current_radar_web "$port"; then
      echo "WEB_PORT $port is currently used by existing radar-web and will be reused."
      upsert_env "WEB_PORT" "$port"
      break
    fi

    echo "WEB_PORT $port is busy."
    port=$((port + 1))

    if [ "$port" -gt 65535 ]; then
      echo "ERROR: Could not find an available web port."
      exit 1
    fi
  done
}

prepare_env() {
  ENV_FILE="$INSTALL_DIR/.env"

  touch "$ENV_FILE"
  chmod 600 "$ENV_FILE" 2>/dev/null || true

  import_config_file "$CONFIG_FILE"

  echo ""
  echo "Adding defaults..."

  ensure_default "WEB_PORT" "$DEFAULT_WEB_PORT"
  ensure_default "WEB_BIND" "0.0.0.0"

  ensure_default "REDIS_HOST" "redis"
  ensure_default "REDIS_PORT" "6379"
  normalize_value "REDIS_HOST" "flight-redis" "redis"
  normalize_value "REDIS_HOST" "radar-redis" "redis"

  ensure_default "FAA_URL" "tcps://ems1.swim.faa.gov:55443"

  ensure_default "ADSBLOL_REAPI_URL" "https://re-api.adsb.lol/"
  ensure_default "ADSBLOL_REAPI_BASE" "https://re-api.adsb.lol"
  ensure_default "ADSBLOL_REAPI_CIRCLES" "40.491389,-80.232778,250"
  ensure_default "ADSBLOL_REAPI_INTERVAL_SECONDS" "10"
  ensure_default "ADSBLOL_REAPI_TTL_SECONDS" "45"
  ensure_default "ADSBLOL_REAPI_REQUEST_SPACING_SECONDS" "1.2"
  ensure_default "ADSBLOL_HEALTH_MAX_AGE_SECONDS" "45"

  ensure_default "GROUND_SWEEP_CLUSTER_INTERVAL_SECONDS" "300"
  ensure_default "GROUND_SWEEP_REQUEST_SPACING_SECONDS" "5"
  ensure_default "GROUND_SWEEP_TTL_SECONDS" "900"
  ensure_default "GROUND_SWEEP_MAX_SEEN_POS_SECONDS" "120"
  ensure_default "GROUND_SWEEP_RADIUS_NM" "250"

  echo ""
  echo "Checking required values..."

  missing_required=0
  require_env "FAA_USER"
  require_env "FAA_PASS"
  require_env "QUEUE_SFDPS"
  require_env "QUEUE_STDDS"
  require_env "QUEUE_TFMS"

  if [ "$missing_required" = "1" ]; then
    echo ""
    echo "Fix $CONFIG_FILE and run setup again."
    exit 1
  fi

  echo ""
  echo "Checking web port..."
  choose_web_port
}

start_containers() {
  echo ""
  echo "Validating Docker Compose..."
  cd "$INSTALL_DIR"
  docker compose config --quiet

  echo ""
  echo "Pulling and starting Radar containers..."
  docker compose pull
  docker compose up -d

  echo ""
  echo "Container status:"
  docker compose ps
}

show_summary() {
  echo ""
  echo "========================================"
  echo " Setup complete"
  echo "========================================"
  echo ""
  echo "Runtime directory:"
  echo "  $INSTALL_DIR"
  echo ""
  echo "Runtime env:"
  echo "  $INSTALL_DIR/.env"
  echo ""
  echo "Web UI:"
  echo "  http://<server-ip>:$(get_env_value WEB_PORT)"
  echo ""
  echo "ADSB.lol note:"
  echo "  re-api access requires this server/public IP to have feeder access."
  echo ""
}

maybe_delete_source() {
  if [ "$SRC_DIR" = "$INSTALL_DIR" ]; then
    return
  fi

  if [ ! -t 0 ]; then
    echo "Kept source checkout: $SRC_DIR"
    return
  fi

  echo "Original source checkout:"
  echo "  $SRC_DIR"
  echo ""
  read -r -p "Delete the original source checkout now? Type DELETE to confirm: " confirm

  if [ "$confirm" = "DELETE" ]; then
    cd /
    rm -rf --one-file-system "$SRC_DIR"
    echo "Deleted source checkout: $SRC_DIR"
  else
    echo "Kept source checkout."
  fi
}

make_runtime_dir
copy_project
prepare_env
start_containers
show_summary
maybe_delete_source
