#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Aviation Radar — Credential Configuration Helper
#
# Safely update optional API credentials in deploy.env.
# Never prints secret values. Shows only whether each field is set or not.
#
# Usage:
#   ./scripts/configure-credentials.sh
# =============================================================================

RADAR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_FILE="$RADAR_DIR/deploy.env"
DEPLOY_EXAMPLE="$RADAR_DIR/deploy.env.example"
ENV_FILE="$RADAR_DIR/.env"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

CHANGES_MADE=false

# ── Helpers ───────────────────────────────────────────────────────────────────

# Returns 0 if key has a non-empty value in deploy.env
is_set() {
    local key="$1"
    local val
    val="$(grep -E "^${key}=.+" "$DEPLOY_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2-)" || true
    [ -n "$val" ]
}

# Read a value from deploy.env — used only for internal logic, NEVER printed to user
get_deploy_value() {
    local key="$1"
    grep -E "^${key}=" "$DEPLOY_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- || true
}

# Write or update a key in deploy.env, preserving all comments and blank lines
upsert_deploy() {
    local key="$1"
    local value="$2"

    KEY="$key" VALUE="$value" FILE="$DEPLOY_FILE" python3 - <<'PY'
from pathlib import Path
import os

path = Path(os.environ["FILE"])
key  = os.environ["KEY"]
value = os.environ["VALUE"]

lines = path.read_text().splitlines() if path.exists() else []
out   = []
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

# Remove a key line entirely from deploy.env
remove_deploy_key() {
    local key="$1"

    KEY="$key" FILE="$DEPLOY_FILE" python3 - <<'PY'
from pathlib import Path
import os

path = Path(os.environ["FILE"])
key  = os.environ["KEY"]

if not path.exists():
    raise SystemExit

lines = path.read_text().splitlines()
out   = [l for l in lines if not l.startswith(key + "=")]
path.write_text("\n".join(out).rstrip() + "\n")
PY
}

# Prompt for a credential.
# - Shows [set] / [not set] without revealing the value.
# - Empty Enter  → keep existing (no change).
# - Any input    → set as new value.
# - Single dash  → clear with confirmation.
# hidden=true uses read -rs (no echo) for passwords/secrets.
prompt_credential() {
    local key="$1"
    local label="$2"
    local hidden="${3:-true}"

    if is_set "$key"; then
        echo -e "  ${BOLD}${label}${NC}: ${GREEN}[set]${NC}"
    else
        echo -e "  ${BOLD}${label}${NC}: ${YELLOW}[not set]${NC}"
    fi

    printf "    New value  (Enter=keep, -=clear): "

    local val=""
    if [ "$hidden" = "true" ]; then
        read -rs val < /dev/tty || true
        echo ""
    else
        read -r val < /dev/tty || true
    fi

    if [ -z "$val" ]; then
        echo "    No change."
        return
    fi

    if [ "$val" = "-" ]; then
        if is_set "$key"; then
            printf "    Clear this field? [y/N] "
            local confirm=""
            read -r confirm < /dev/tty || true
            if [[ "$confirm" =~ ^[Yy]$ ]]; then
                upsert_deploy "$key" ""
                echo -e "    ${YELLOW}Cleared.${NC}"
                CHANGES_MADE=true
            else
                echo "    Kept existing."
            fi
        else
            echo "    Field is already empty."
        fi
        return
    fi

    upsert_deploy "$key" "$val"
    echo -e "    ${GREEN}Updated.${NC}"
    CHANGES_MADE=true
}

# ── Ensure deploy.env exists ──────────────────────────────────────────────────

ensure_deploy_env() {
    if [ ! -f "$DEPLOY_FILE" ]; then
        if [ -f "$DEPLOY_EXAMPLE" ]; then
            cp "$DEPLOY_EXAMPLE" "$DEPLOY_FILE"
            info "Created deploy.env from deploy.env.example."
        else
            cat > "$DEPLOY_FILE" <<'EOF'
# Aviation Radar — deploy.env
# Add optional credentials here to enable enhanced data sources.

# ── FAA SWIM credentials (optional) ──────────────────────────────────────────
FAA_USER=
FAA_PASS=
QUEUE_SFDPS=
QUEUE_STDDS=
QUEUE_TFMS=

# ── OpenSky Network credentials (optional) ────────────────────────────────────
OPENSKY_CLIENT_ID=
OPENSKY_CLIENT_SECRET=
EOF
            info "Created a new deploy.env."
        fi
        chmod 600 "$DEPLOY_FILE"
    fi
}

# ── FAA SWIM section ──────────────────────────────────────────────────────────

configure_faa_swim() {
    echo ""
    echo -e "${CYAN}── FAA SWIM Credentials (optional) ──────────────────────────────────────${NC}"
    echo "   Enables FAA SWIM ingestor (flight plan + track data)."
    echo "   Set FAA_USER, FAA_PASS, and at least one QUEUE to enable automatically."
    echo "   Leave all empty to run on public ADS-B data only."
    echo ""

    prompt_credential "FAA_USER" "FAA Username (email)" "false"
    prompt_credential "FAA_PASS" "FAA Password          " "true"

    echo ""
    echo "  SWIM Queues — set the queues your FAA account has access to."
    echo "  Enter to keep, - to clear, or paste your full queue name."
    echo "  (Queue names are treated as secrets and will not be displayed.)"
    echo ""

    prompt_credential "QUEUE_SFDPS" "SFDPS Queue" "true"
    prompt_credential "QUEUE_STDDS" "STDDS Queue" "true"
    prompt_credential "QUEUE_TFMS"  "TFMS Queue " "true"

    echo ""
    _show_swim_override_status
}

_show_swim_override_status() {
    local override
    override="$(get_deploy_value ENABLE_SWIM_INGESTOR)" || true

    if [ "$override" = "false" ]; then
        echo -e "  ${YELLOW}[!] ENABLE_SWIM_INGESTOR=false is set in deploy.env.${NC}"
        echo "      This force-disables SWIM even when FAA credentials are present."
        echo ""
        printf "  Remove this override and restore auto-detection? [y/N] "
        local ans=""
        read -r ans < /dev/tty || true
        if [[ "$ans" =~ ^[Yy]$ ]]; then
            remove_deploy_key "ENABLE_SWIM_INGESTOR"
            info "Override removed. SWIM will auto-enable when credentials are complete."
            CHANGES_MADE=true
        else
            echo "  Override kept — SWIM will remain disabled."
        fi
    elif [ "$override" = "true" ]; then
        echo -e "  ${YELLOW}[!] ENABLE_SWIM_INGESTOR=true is set explicitly in deploy.env.${NC}"
        echo "      setup.sh / update.sh require full FAA credentials when this is set."
        echo "      Consider removing it and letting auto-detection handle SWIM."
        echo ""
        printf "  Remove the explicit flag and use auto-detection instead? [y/N] "
        local ans=""
        read -r ans < /dev/tty || true
        if [[ "$ans" =~ ^[Yy]$ ]]; then
            remove_deploy_key "ENABLE_SWIM_INGESTOR"
            info "Removed explicit flag. SWIM will auto-enable when credentials are complete."
            CHANGES_MADE=true
        else
            echo "  Explicit flag kept."
        fi
    else
        echo -e "  SWIM override: ${GREEN}auto-detect${NC} (enabled automatically when credentials are complete)"
    fi

    echo ""
    _summarize_swim_state
}

_summarize_swim_state() {
    local user pass q1 q2 q3
    if is_set "FAA_USER"; then user=1; else user=0; fi
    if is_set "FAA_PASS"; then pass=1; else pass=0; fi
    if is_set "QUEUE_SFDPS" || is_set "QUEUE_STDDS" || is_set "QUEUE_TFMS"; then
        q1=1
    else
        q1=0
    fi

    if [ "$user" = "1" ] && [ "$pass" = "1" ] && [ "$q1" = "1" ]; then
        echo -e "  SWIM will be: ${GREEN}enabled${NC} on next setup/update (credentials complete)"
    elif [ "$user" = "0" ] && [ "$pass" = "0" ] && [ "$q1" = "0" ]; then
        echo -e "  SWIM will be: ${YELLOW}disabled${NC} (no FAA credentials configured)"
    else
        echo -e "  SWIM will be: ${RED}disabled${NC} (credentials incomplete — check FAA_USER, FAA_PASS, and at least one QUEUE)"
    fi
}

# ── OpenSky section ───────────────────────────────────────────────────────────

configure_opensky() {
    echo ""
    echo -e "${CYAN}── OpenSky Network Credentials (optional) ───────────────────────────────${NC}"
    echo "   Enables authenticated OpenSky coverage (better rate limits)."
    echo "   Anonymous mode is used if not set."
    echo ""

    prompt_credential "OPENSKY_CLIENT_ID"     "OpenSky Client ID    " "false"
    prompt_credential "OPENSKY_CLIENT_SECRET" "OpenSky Client Secret" "true"
}

# ── Post-update prompt ────────────────────────────────────────────────────────

offer_update() {
    if [ "$CHANGES_MADE" = "false" ]; then
        return
    fi

    echo ""
    echo "──────────────────────────────────────────────────────────────────────────"
    echo -e "${GREEN}Credentials saved to deploy.env.${NC}"
    echo ""
    echo "  Changes take effect after running setup.sh or update.sh, which"
    echo "  rebuilds .env and restarts the containers."
    echo ""

    if [ -f "$RADAR_DIR/update.sh" ] && [ -f "$ENV_FILE" ]; then
        printf "  Run update.sh now to apply changes? [y/N] "
        local ans=""
        read -r ans < /dev/tty || true
        if [[ "$ans" =~ ^[Yy]$ ]]; then
            echo ""
            bash "$RADAR_DIR/update.sh"
        else
            echo ""
            echo "  Run when ready:  ./update.sh"
            echo "  First setup:     ./setup.sh"
        fi
    elif [ -f "$RADAR_DIR/setup.sh" ]; then
        echo "  Run when ready:  ./setup.sh    (first install)"
        echo "                   ./update.sh   (if already installed)"
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────

echo ""
echo "================================================"
echo "   Aviation Radar — Configure Credentials"
echo "================================================"
echo ""
echo "  Shows whether each optional credential is set."
echo "  Enter a new value to update. Press Enter to keep."
echo "  Type a single dash (-) and Enter to clear a field."
echo "  Secrets are never displayed."
echo ""

ensure_deploy_env
configure_faa_swim
configure_opensky
offer_update

echo ""
echo "================================================"
echo "   Done."
echo "================================================"
echo ""
