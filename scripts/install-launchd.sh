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
  # Render plist with XML-escaped substitutions; bridge.py reads bridge.env directly (never bake tokens into plist).
  "$PYTHON3" - "$PLIST_SRC" "$out" "$PYTHON3" "$BRIDGE_PY" "$RUN_DIR" "$HOME" "$ROOT" <<'PY'
import os
import sys
from pathlib import Path
from xml.sax.saxutils import escape

src, dst, py3, bridge_py, run_dir, home_dir, root_dir = sys.argv[1:8]
text = Path(src).read_text(encoding="utf-8")
subs = {
    "__PYTHON3__": escape(py3),
    "__BRIDGE_PY__": escape(bridge_py),
    "__RUN_DIR__": escape(run_dir),
    "__HOME__": escape(home_dir),
    "__ROOT__": escape(root_dir),
}
for k, v in subs.items():
    text = text.replace(k, v)
Path(dst).write_text(text, encoding="utf-8")
os.chmod(dst, 0o600)
PY
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
