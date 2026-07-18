#!/usr/bin/env bash
# Generate / install optional HERMES_CHROME_BRIDGE_TOKEN for local bridge auth.
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
# Hermes Chrome bridge auth (local only). Loaded by launchd if configured.
export HERMES_CHROME_BRIDGE_TOKEN='$tok'
EOF
    chmod 600 "$ENV_FILE"
    echo "Wrote $ENV_FILE"
    echo "Token (also in file): $tok"
    echo
    echo "Next:"
    echo "  1) export HERMES_CHROME_BRIDGE_TOKEN from bridge.env before CLI, or:"
    echo "       set -a; source $ENV_FILE; set +a"
    echo "  2) Restart bridge with the same token:"
    echo "       set -a; source $ENV_FILE; set +a"
    echo "       $ROOT/scripts/hermes-chrome.sh bridge-restart"
    echo "       # or: $ROOT/scripts/install-launchd.sh install  (after EnvironmentVariables update)"
    echo "  3) CLI automatically sends X-Hermes-Chrome-Token when env is set."
    echo
    echo "Note: launchd plist template does not auto-load bridge.env yet — export in shell or add to plist EnvironmentVariables."
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
  clear)
    rm -f "$ENV_FILE"
    echo "removed $ENV_FILE — restart bridge without token"
    ;;
  *)
    echo "usage: $0 generate|show|clear" >&2
    exit 1
    ;;
esac
