#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
NO_PROMPT="${NO_PROMPT:-0}"

touch "$ENV_FILE"
chmod 600 "$ENV_FILE" 2>/dev/null || true

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

prompt_if_missing() {
  local key="$1"
  local prompt="$2"
  local secret="${3:-0}"

  local current
  current="$(get_env_value "$key")"

  if [ -n "$current" ] || [ "$NO_PROMPT" = "1" ]; then
    return
  fi

  local value=""
  if [ "$secret" = "1" ]; then
    read -r -s -p "$prompt: " value
    echo
  else
    read -r -p "$prompt: " value
  fi

  if [ -n "$value" ]; then
    upsert_env "$key" "$value"
  fi
}

echo "Preparing $ENV_FILE"

# Runtime defaults
ensure_default "WEB_PORT" "8080"
ensure_default "WEB_BIND" "0.0.0.0"

# Internal Docker Compose service name, not container name.
ensure_default "REDIS_HOST" "redis"
ensure_default "REDIS_PORT" "6379"
normalize_value "REDIS_HOST" "flight-redis" "redis"
normalize_value "REDIS_HOST" "radar-redis" "redis"

# FAA/SWIM defaults
ensure_default "FAA_URL" "tcps://ems1.swim.faa.gov:55443"

# ADSB.lol primary air feed
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

# User-specific required/optional values
prompt_if_missing "FAA_USER" "FAA username / email"
prompt_if_missing "FAA_PASS" "FAA password" "1"
prompt_if_missing "QUEUE_SFDPS" "FAA SFDPS queue"
prompt_if_missing "QUEUE_STDDS" "FAA STDDS queue"
prompt_if_missing "QUEUE_TFMS" "FAA TFMS queue"

prompt_if_missing "OPENSKY_CLIENT_ID" "OpenSky client ID, optional"
prompt_if_missing "OPENSKY_CLIENT_SECRET" "OpenSky client secret, optional" "1"

echo ""
echo "Done. Review $ENV_FILE, then run:"
echo "  docker compose config --quiet"
echo "  docker compose up -d --build"
