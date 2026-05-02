#!/usr/bin/env bash
set -euo pipefail

APP_NAME="Radar"
DEFAULT_INSTALL_DIR="/opt/radar"

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${CONFIG_FILE:-}"
INSTALL_DIR="${INSTALL_DIR:-}"
DELETE_SOURCE="${DELETE_SOURCE:-ask}"

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
# Preferred: deploy.env
# Fallback: existing .env, useful for this current server and older installs.
if [ -z "$CONFIG_FILE" ]; then
  if [ -f "$SRC_DIR/deploy.env" ]; then
    CONFIG_FILE="$SRC_DIR/deploy.env"
  elif [ -f "$SRC_DIR/.env" ]; then
    CONFIG_FILE="$SRC_DIR/.env"
  else
    CONFIG_FILE="$SRC_DIR/deploy.env"
  fi
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
  echo "Use something like:"
  echo "  INSTALL_DIR=/opt/radar ./setup.sh"
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

get_env_value() {
  local key="$1"
  grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- || true
}

upsert_env() {
  local key="$1"
  local value="$2"

  KEY="$key" VALUE="$value" ENV_FILE="$ENV_FILE" python3 - <<'PY'
from pathlib import Path
import os

path = Path(os.environ["ENV_FILE"])
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

ensure_default() {
  local key="$1"
  local value="$2"
  local current
  current="$(get_env_value "$key")"

  if [ -z "$current" ]; then
    upsert_env "$key" "$value"
    echo "Added default: $key"
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

  if [ ! -f "$file" ]; then
    echo "ERROR: Deployment config not found: $file"
    echo ""
    echo "Create deploy.env from deploy.env.example, then run setup again."
    exit 1
  fi

  echo "Importing deployment config..."

  while IFS= read -r line || [ -n "$line" ]; do
    # Trim leading whitespace for comment/blank detection.
    trimmed="${line#"${line%%[![:space:]]*}"}"

    [ -z "$trimmed" ] && continue
    [[ "$trimmed" == \#* ]] && continue
    [[ "$trimmed" != *=* ]] && continue

    key="${trimmed%%=*}"
    value="${trimmed#*=}"

    if [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      # Runtime-only setup keys should not be written to Docker .env.
      case "$key" in
        INSTALL_DIR|DELETE_SOURCE)
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
    echo "ERROR: Missing required value: $key"
    missing_required=1
  fi
}

prepare_env() {
  ENV_FILE="$INSTALL_DIR/.env"

  touch "$ENV_FILE"
  chmod 600 "$ENV_FILE" 2>/dev/null || true

  import_config_file "$CONFIG_FILE"

  echo ""
  echo "Adding defaults..."

  # Runtime defaults
  ensure_default "WEB_PORT" "8080"
  ensure_default "WEB_BIND" "0.0.0.0"

  # Internal Docker service name
  ensure_default "REDIS_HOST" "redis"
  ensure_default "REDIS_PORT" "6379"
  normalize_value "REDIS_HOST" "flight-redis" "redis"
  normalize_value "REDIS_HOST" "radar-redis" "redis"

  # FAA/SWIM
  ensure_default "FAA_URL" "tcps://ems1.swim.faa.gov:55443"

  # ADSB.lol primary airborne feed
  ensure_default "ADSBLOL_REAPI_URL" "https://re-api.adsb.lol/"
  ensure_default "ADSBLOL_REAPI_BASE" "https://re-api.adsb.lol"
  ensure_default "ADSBLOL_REAPI_CIRCLES" "40.491389,-80.232778,250"
  ensure_default "ADSBLOL_REAPI_INTERVAL_SECONDS" "10"
  ensure_default "ADSBLOL_REAPI_TTL_SECONDS" "45"
  ensure_default "ADSBLOL_REAPI_REQUEST_SPACING_SECONDS" "1.2"
  ensure_default "ADSBLOL_HEALTH_MAX_AGE_SECONDS" "45"

  # ADSB.lol airport ground sweep
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
}

start_containers() {
  echo ""
  echo "Validating Docker Compose..."
  cd "$INSTALL_DIR"
  docker compose config --quiet

  echo ""
  echo "Building and starting Radar containers..."
  docker compose up -d --build

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

  case "$DELETE_SOURCE" in
    1|yes|YES|true|TRUE)
      cd /
      rm -rf --one-file-system "$SRC_DIR"
      echo "Deleted source checkout: $SRC_DIR"
      ;;
    0|no|NO|false|FALSE)
      echo "Kept source checkout: $SRC_DIR"
      ;;
    ask|ASK)
      if [ -t 0 ]; then
        echo "Original source checkout:"
        echo "  $SRC_DIR"
        echo ""
        read -r -p "Delete the original source checkout now? Type DELETE to confirm: " confirm
        if [ "$confirm" = "DELETE" ]; then
          cd /
          rm -rf --one-file-system "$SRC_DIR"
          echo "Deleted $SRC_DIR"
        else
          echo "Kept source checkout."
        fi
      else
        echo "Kept source checkout: $SRC_DIR"
      fi
      ;;
    *)
      echo "Unknown DELETE_SOURCE value '$DELETE_SOURCE'; kept source checkout."
      ;;
  esac
}

make_runtime_dir
copy_project
prepare_env
start_containers
show_summary
maybe_delete_source
