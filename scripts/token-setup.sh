#!/usr/bin/env bash
# Generate / install HERMES_CHROME_BRIDGE_TOKEN for local bridge auth (default ON).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUN_DIR="${HERMES_CHROME_RUN:-$HOME/.hermes/run/hermes-chrome}"
ENV_FILE="${RUN_DIR}/bridge.env"
LABEL="com.leaf76.hermes-chrome-bridge"

mkdir -p "$RUN_DIR"

cmd="${1:-generate}"

generate_token() {
  python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
}

case "$cmd" in
  generate|gen)
    tok="$(generate_token)"
    umask 077
    cat >"$ENV_FILE" <<EOF
# Hermes Chrome bridge auth (local only). Loaded by CLI and launchd.
export HERMES_CHROME_BRIDGE_TOKEN='$tok'
EOF
    chmod 600 "$ENV_FILE"
    echo "Wrote $ENV_FILE (chmod 600)"
    echo "Token length: ${#tok}"
    echo
    echo "Next:"
    echo "  1) Restart bridge so it picks up the token:"
    echo "       $ROOT/scripts/hermes-chrome.sh bridge-restart"
    echo "       # or reinstall launchd: $ROOT/scripts/install-launchd.sh install"
    echo "  2) Open pairing window and Pair the extension:"
    echo "       $ROOT/scripts/hermes-chrome.sh pair-open"
    echo "       # Extension popup → Pair  (or paste token into Options)"
    echo "  3) CLI auto-sources bridge.env for X-Hermes-Chrome-Token."
    echo
    echo "Security: never commit bridge.env; never share the token."
    ;;
  show)
    if [[ -f "$ENV_FILE" ]]; then
      # shellcheck disable=SC1090
      set -a
      # shellcheck source=/dev/null
      source "$ENV_FILE"
      set +a
      if [[ -n "${HERMES_CHROME_BRIDGE_TOKEN:-}" ]]; then
        echo "token_set=true file=$ENV_FILE len=${#HERMES_CHROME_BRIDGE_TOKEN}"
      else
        echo "token_set=false file=$ENV_FILE"
      fi
    else
      echo "token_set=false (no $ENV_FILE)"
    fi
    ;;
  print)
    # Explicit secret print (for paste into Options). Prefer Pair when possible.
    if [[ -f "$ENV_FILE" ]]; then
      # shellcheck disable=SC1090
      set -a
      # shellcheck source=/dev/null
      source "$ENV_FILE"
      set +a
    fi
    if [[ -z "${HERMES_CHROME_BRIDGE_TOKEN:-}" ]]; then
      echo "error: no token" >&2
      exit 1
    fi
    echo "$HERMES_CHROME_BRIDGE_TOKEN"
    ;;
  clear)
    rm -f "$ENV_FILE"
    echo "removed $ENV_FILE — restart bridge (will regenerate unless ALLOW_NO_AUTH=1)"
    ;;
  *)
    echo "usage: $0 generate|show|print|clear" >&2
    exit 1
    ;;
esac
