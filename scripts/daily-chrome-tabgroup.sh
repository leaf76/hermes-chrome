#!/usr/bin/env bash
# Daily Chrome — REAL Tab Group via Hermes Chrome extension + local bridge.
#
# Requires:
#   1) Bridge: auto-starts <repo>/bridge.py
#   2) Extension loaded (unpacked) once from <repo>/extension
#        chrome://extensions → Developer mode → Load unpacked
#   3) Click extension icon once so the service worker starts polling
#
# Usage:
#   daily-chrome-tabgroup.sh start [url]
#   daily-chrome-tabgroup.sh open <url>
#   daily-chrome-tabgroup.sh new-tab <url>
#   daily-chrome-tabgroup.sh status
#   daily-chrome-tabgroup.sh stop
#   daily-chrome-tabgroup.sh bridge-start|bridge-stop|bridge-status
#   daily-chrome-tabgroup.sh ping
#
# Env:
#   HERMES_DAILY_CHROME_ROOT   — repo root (default: parent of scripts/)
#   HERMES_TABGROUP_BRIDGE_*   — host/port for bridge
#   HERMES_DAILY_CHROME_RUN    — runtime dir for pid/log (default ~/.hermes/run/daily-chrome-agent)
#
# Policy: tabs created with active:false to reduce focus steal. Chrome may still
# briefly raise; we do NOT call AppleScript activate on daily Chrome.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${HERMES_DAILY_CHROME_ROOT:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
RUN_DIR="${HERMES_DAILY_CHROME_RUN:-$HOME/.hermes/run/daily-chrome-agent}"
BRIDGE_PY="${ROOT}/bridge.py"
BRIDGE_HOST="${HERMES_TABGROUP_BRIDGE_HOST:-127.0.0.1}"
BRIDGE_PORT="${HERMES_TABGROUP_BRIDGE_PORT:-19876}"
BRIDGE_URL="http://${BRIDGE_HOST}:${BRIDGE_PORT}"
PID_FILE="${RUN_DIR}/bridge.pid"
LOG_FILE="${RUN_DIR}/bridge.log"
EXT_DIR="${ROOT}/extension"

mkdir -p "$RUN_DIR"

die() { echo "error: $*" >&2; exit 1; }

bridge_healthy() {
  curl -fsS --max-time 1 "${BRIDGE_URL}/v1/health" >/dev/null 2>&1
}

cmd_bridge_status() {
  if bridge_healthy; then
    echo "bridge: running"
    curl -fsS --max-time 2 "${BRIDGE_URL}/v1/health"
    echo
    [[ -f "$PID_FILE" ]] && echo "pidfile: $(cat "$PID_FILE")"
    return 0
  fi
  echo "bridge: stopped"
  return 1
}

cmd_bridge_start() {
  if bridge_healthy; then
    echo "bridge already running"
    cmd_bridge_status || true
    return 0
  fi
  [[ -f "$BRIDGE_PY" ]] || die "missing $BRIDGE_PY"
  # Launch detached without shell nohup wrapper restrictions when possible.
  python3 "$BRIDGE_PY" >>"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  local i
  for i in $(seq 1 30); do
    if bridge_healthy; then
      echo "bridge started: ${BRIDGE_URL}"
      return 0
    fi
    sleep 0.1
  done
  die "bridge failed to start — see $LOG_FILE"
}

cmd_bridge_stop() {
  if [[ -f "$PID_FILE" ]]; then
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    rm -f "$PID_FILE"
  fi
  # best-effort by port
  local p
  p="$(lsof -nP -iTCP:"${BRIDGE_PORT}" -sTCP:LISTEN -t 2>/dev/null | head -1 || true)"
  if [[ -n "$p" ]]; then
    kill "$p" 2>/dev/null || true
  fi
  sleep 0.2
  if bridge_healthy; then
    die "bridge still healthy after stop"
  fi
  echo "bridge stopped"
}

# POST command and wait for extension result.
send_cmd() {
  local action="$1"
  shift || true
  local url="${1:-}"
  cmd_bridge_start

  local payload id
  if [[ -n "$url" ]]; then
    payload="$(python3 -c 'import json,sys,uuid; print(json.dumps({"id":str(uuid.uuid4()),"action":sys.argv[1],"url":sys.argv[2]}))' "$action" "$url")"
  else
    payload="$(python3 -c 'import json,sys,uuid; print(json.dumps({"id":str(uuid.uuid4()),"action":sys.argv[1]}))' "$action")"
  fi

  local resp
  resp="$(curl -fsS --max-time 5 -H 'Content-Type: application/json' -d "$payload" "${BRIDGE_URL}/v1/command")"
  id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$resp")"

  # Wait for extension (must be installed + service worker polling)
  local result
  if ! result="$(curl -fsS --max-time 35 "${BRIDGE_URL}/v1/result/${id}?timeout=30")"; then
    die "no response from extension (timeout). Load unpacked extension from:
  ${EXT_DIR}
Then open the extension popup once so polling starts. Bridge is at ${BRIDGE_URL}"
  fi

  python3 - <<'PY' "$result"
import json, sys
r = json.loads(sys.argv[1])
if not r.get("ok"):
    print("error:", r.get("error") or r, file=sys.stderr)
    sys.exit(1)
data = r.get("data")
print(json.dumps(data if data is not None else r, indent=2, ensure_ascii=False))
PY
}

cmd_start() {
  local url="${1:-}"
  echo "mode: daily-chrome-tabgroup (native chrome.tabGroups)"
  if [[ -n "$url" ]]; then
    send_cmd start "$url"
  else
    send_cmd start
  fi
}

cmd_open() {
  local url="${1:-}"; [[ -n "$url" ]] || die "usage: $0 open <url>"
  send_cmd open "$url"
}

cmd_new_tab() {
  local url="${1:-}"; [[ -n "$url" ]] || die "usage: $0 new-tab <url>"
  send_cmd new_tab "$url"
}

cmd_status() {
  send_cmd status
}

cmd_stop() {
  send_cmd stop
}

cmd_ping() {
  send_cmd ping
}

cmd_install_help() {
  cat <<EOF
Install Hermes Agent Tab Group extension (one-time):

1. Start bridge (optional; CLI auto-starts):
     $0 bridge-start

2. Chrome → chrome://extensions
   - Enable "Developer mode"
   - "Load unpacked"
   - Select: ${EXT_DIR}

3. Pin the extension, click its icon once (starts long-poll).

4. Test:
     $0 ping
     $0 start https://example.com/
     $0 status
     $0 stop

Notes:
- Uses REAL Chrome Tab Groups titled "Hermes Agent" (blue).
- Tabs open with active:false to reduce focus steal.
- Not the same as Agent Chrome profile (~/.hermes/chrome-debug).
- Fallback without extension: daily-chrome-agent-window.sh (named window only).
EOF
}

usage() {
  sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
  echo
  echo "commands: start|open|new-tab|status|stop|ping|bridge-start|bridge-stop|bridge-status|install-help"
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    start)          cmd_start "${1:-}" ;;
    open)           cmd_open "${1:-}" ;;
    new-tab)        cmd_new_tab "${1:-}" ;;
    status)         cmd_status ;;
    stop)           cmd_stop ;;
    ping)           cmd_ping ;;
    bridge-start)   cmd_bridge_start ;;
    bridge-stop)    cmd_bridge_stop ;;
    bridge-status)  cmd_bridge_status ;;
    install-help)   cmd_install_help ;;
    -h|--help|help|"") usage; [[ -n "$cmd" ]] || exit 1 ;;
    *) die "unknown command: $cmd" ;;
  esac
}

main "$@"
