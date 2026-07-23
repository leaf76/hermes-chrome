#!/usr/bin/env bash
# Install / uninstall macOS launchd job for hermes-chrome bridge.
# Label: com.leaf76.hermes-chrome-bridge
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LABEL="com.leaf76.hermes-chrome-bridge"
PLIST_SRC="${SCRIPT_DIR}/launchd/${LABEL}.plist.template"
PLIST_DST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
RUN_DIR="${HERMES_CHROME_RUN:-$HOME/.hermes/run/hermes-chrome}"
BRIDGE_PY="${ROOT}/bridge.py"
PYTHON3="$(command -v python3 || true)"

die() { echo "error: $*" >&2; exit 1; }

[[ "$(uname -s)" == "Darwin" ]] || die "launchd install is macOS-only"
[[ -f "$PLIST_SRC" ]] || die "missing template $PLIST_SRC"
[[ -f "$BRIDGE_PY" ]] || die "missing $BRIDGE_PY"
[[ -n "$PYTHON3" ]] || die "python3 not found"

mkdir -p "$RUN_DIR" "$(dirname "$PLIST_DST")"

render_plist() {
  local out="$1"
  local token_xml=""
  # Optional token from bridge.env
  if [[ -f "${RUN_DIR}/bridge.env" ]]; then
    # shellcheck disable=SC1091
    set -a
    # shellcheck source=/dev/null
    source "${RUN_DIR}/bridge.env"
    set +a
  fi
  if [[ -n "${HERMES_CHROME_BRIDGE_TOKEN:-}" ]]; then
    token_xml="    <key>HERMES_CHROME_BRIDGE_TOKEN</key>
    <string>${HERMES_CHROME_BRIDGE_TOKEN}</string>"
  fi
  sed \
    -e "s|__PYTHON3__|${PYTHON3}|g" \
    -e "s|__BRIDGE_PY__|${BRIDGE_PY}|g" \
    -e "s|__RUN_DIR__|${RUN_DIR}|g" \
    -e "s|__HOME__|${HOME}|g" \
    -e "s|__ROOT__|${ROOT}|g" \
    "$PLIST_SRC" >"$out"
  if [[ -n "$token_xml" ]]; then
    # Insert token env before closing </dict> of EnvironmentVariables
    python3 - <<'PY' "$out" "$token_xml"
from pathlib import Path
import sys
path, token_xml = Path(sys.argv[1]), sys.argv[2]
text = path.read_text()
needle = "    <key>PATH</key>"
if needle in text and "HERMES_CHROME_BRIDGE_TOKEN" not in text:
    text = text.replace(
        needle,
        token_xml + "\n" + needle,
        1,
    )
    path.write_text(text)
PY
  fi
}

cmd_install() {
  # Ensure bridge.env token exists (auth on by default).
  if [[ ! -f "${RUN_DIR}/bridge.env" ]]; then
    if [[ -x "${SCRIPT_DIR}/token-setup.sh" || -f "${SCRIPT_DIR}/token-setup.sh" ]]; then
      bash "${SCRIPT_DIR}/token-setup.sh" generate
    fi
  fi
  # Stop manual bridge if listening so launchd owns the port.
  if [[ -x "${SCRIPT_DIR}/hermes-chrome.sh" ]]; then
    "${SCRIPT_DIR}/hermes-chrome.sh" bridge-stop >/dev/null 2>&1 || true
  fi
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  launchctl unload "$PLIST_DST" 2>/dev/null || true

  render_plist "$PLIST_DST"
  # Prefer modern bootstrap; fall back to load.
  if launchctl bootstrap "gui/$(id -u)" "$PLIST_DST" 2>/dev/null; then
    launchctl enable "gui/$(id -u)/${LABEL}" 2>/dev/null || true
    launchctl kickstart -k "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  else
    launchctl load -w "$PLIST_DST"
  fi

  local i
  for i in $(seq 1 40); do
    if curl -fsS --max-time 1 "http://127.0.0.1:19876/v1/health" >/dev/null 2>&1; then
      echo "launchd installed: $PLIST_DST"
      echo "bridge health:"
      curl -fsS --max-time 2 "http://127.0.0.1:19876/v1/health"
      echo
      return 0
    fi
    sleep 0.15
  done
  die "bridge did not become healthy after launchd install — see ${RUN_DIR}/bridge.launchd.*.log"
}

cmd_uninstall() {
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  launchctl unload "$PLIST_DST" 2>/dev/null || true
  rm -f "$PLIST_DST"
  echo "launchd uninstalled: $LABEL"
}

case "${1:-}" in
  install) cmd_install ;;
  uninstall) cmd_uninstall ;;
  *)
    echo "usage: $0 install|uninstall" >&2
    exit 1
    ;;
esac
