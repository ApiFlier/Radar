#!/usr/bin/env bash
set -euo pipefail

APP_NAME="Radar"
DEFAULT_INSTALL_DIR="/opt/radar"

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

if [ -z "$INSTALL_DIR" ]; then
  if [ -t 0 ]; then
    read -r -p "Runtime install directory [${DEFAULT_INSTALL_DIR}]: " INSTALL_DIR
    INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
  else
    INSTALL_DIR="$DEFAULT_INSTALL_DIR"
  fi
fi

INSTALL_DIR="$(realpath -m "$INSTALL_DIR")"

echo "Source directory:  $SRC_DIR"
echo "Runtime directory: $INSTALL_DIR"
echo ""

if [ "$SRC_DIR" = "$INSTALL_DIR" ]; then
  echo "ERROR: Runtime directory must be separate from the source checkout."
  echo "Choose a different INSTALL_DIR, for example:"
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
      --exclude='__pycache__' \
      --exclude='*.pyc' \
      --exclude='.pytest_cache' \
      --exclude='node_modules' \
      --exclude='data/airports.csv' \
      --exclude='data/airport-frequencies.csv' \
      -C "$SRC_DIR" -cf - . | tar -C "$INSTALL_DIR" -xf -
  fi
}

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

prompt_required() {
  local key="$1"
  local prompt="$2"
  local secret="${3:-0}"

  local current
  current="$(get_env_value "$key")"

  if [ -n "$current" ]; then
    return
  fi

  if [ ! -t 0 ]; then
    echo "ERROR: Missing required value: $key"
    echo "Set it in $ENV_FILE or run setup interactively."
    exit 1
  fi

  local value=""
  while [ -z "$value" ]; do
    if [ "$secret" = "1" ]; then
      read -r -s -p "$prompt: " value
      echo
    else
      read -r -p "$prompt: " value
    fi

    if [ -z "$value" ]; then
      echo "$key is required."
    fi
  done

  upsert_env "$key" "$value"
}

prompt_optional() {
  local key="$1"
  local prompt="$2"
  local secret="${3:-0}"

  local current
  current="$(get_env_value "$key")"

  if [ -n "$current" ] || [ ! -t 0 ]; then
    return
  fi

  local value=""
  if [ "$secret" = "1" ]; then
    read -r -s -p "$prompt, optional, press Enter to skip: " value
    echo
  else
    read -r -p "$prompt, optional, press Enter to skip: " value
  fi

  if [ -n "$value" ]; then
    upsert_env "$key" "$value"
  fi
}

prepare_env() {
  ENV_FILE="$INSTALL_DIR/.env"

  if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$SRC_DIR/.env" ] && [ -t 0 ]; then
      read -r -p "Use existing source .env as a starting point? [Y/n]: " use_existing
      use_existing="${use_existing:-Y}"

      if [[ "$use_existing" =~ ^[Yy]$ ]]; then
        cp "$SRC_DIR/.env" "$ENV_FILE"
        echo "Copied existing .env into runtime directory."
      fi
    fi
  fi

  if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$INSTALL_DIR/.env.example" ]; then
      cp "$INSTALL_DIR/.env.example" "$ENV_FILE"
      echo "Created .env from .env.example."
    else
      touch "$ENV_FILE"
      echo "Created empty .env."
    fi
  fi

  chmod 600 "$ENV_FILE" 2>/dev/null || true

  echo ""
  echo "Preparing runtime .env..."

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

  # ADSB.lol ground sweep
  ensure_default "GROUND_SWEEP_CLUSTER_INTERVAL_SECONDS" "300"
  ensure_default "GROUND_SWEEP_REQUEST_SPACING_SECONDS" "5"
  ensure_default "GROUND_SWEEP_TTL_SECONDS" "900"
  ensure_default "GROUND_SWEEP_MAX_SEEN_POS_SECONDS" "120"
  ensure_default "GROUND_SWEEP_RADIUS_NM" "250"

  echo ""
  echo "Required FAA/SWIM values:"
  prompt_required "FAA_USER" "FAA username / email"
  prompt_required "FAA_PASS" "FAA password" "1"
  prompt_required "QUEUE_SFDPS" "FAA SFDPS queue"
  prompt_required "QUEUE_STDDS" "FAA STDDS queue"
  prompt_required "QUEUE_TFMS" "FAA TFMS queue"

  echo ""
  echo "Optional OpenSky values:"
  prompt_optional "OPENSKY_CLIENT_ID" "OpenSky client ID"
  prompt_optional "OPENSKY_CLIENT_SECRET" "OpenSky client secret" "1"
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
  echo "Reminder:"
  echo "  ADSB.lol re-api access requires the server/public IP to be feeding ADSB.lol."
  echo ""
}

maybe_delete_source() {
  if [ "$SRC_DIR" = "$INSTALL_DIR" ]; then
    return
  fi

  if [ ! -t 0 ]; then
    return
  fi

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
}

make_runtime_dir
copy_project
prepare_env
start_containers
show_summary
maybe_delete_source
