#!/usr/bin/env bash
# Hermes Chrome — operate daily Chrome from Hermes / local agent CLI.
#
# Requires:
#   1) Bridge: auto-starts <repo>/bridge.py
#   2) Extension loaded (unpacked) once from <repo>/extension
#        chrome://extensions → Developer mode → Load unpacked
#   3) Click extension icon once so the service worker starts polling
#
# Usage:
#   hermes-chrome.sh start [url]
#   hermes-chrome.sh open <url>
#   hermes-chrome.sh new-tab <url>
#   hermes-chrome.sh status
#   hermes-chrome.sh stop
#   hermes-chrome.sh bridge-start|bridge-stop|bridge-status
#   hermes-chrome.sh ping
#
# Env:
#   HERMES_CHROME_ROOT   — repo root (default: parent of scripts/)
#   HERMES_CHROME_BRIDGE_* or HERMES_TABGROUP_BRIDGE_*   — host/port for bridge
#   HERMES_CHROME_RUN    — runtime dir for pid/log (default ~/.hermes/run/hermes-chrome)
#
# Policy: tabs created with active:false to reduce focus steal. Chrome may still
# briefly raise; we do NOT call AppleScript activate on daily Chrome.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${HERMES_CHROME_ROOT:-${HERMES_DAILY_CHROME_ROOT:-$(cd "${SCRIPT_DIR}/.." && pwd)}}"
RUN_DIR="${HERMES_CHROME_RUN:-${HERMES_DAILY_CHROME_RUN:-$HOME/.hermes/run/hermes-chrome}}"
BRIDGE_PY="${ROOT}/bridge.py"
BRIDGE_HOST="${HERMES_CHROME_BRIDGE_HOST:-${HERMES_TABGROUP_BRIDGE_HOST:-127.0.0.1}}"
BRIDGE_PORT="${HERMES_CHROME_BRIDGE_PORT:-${HERMES_TABGROUP_BRIDGE_PORT:-19876}}"
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
# Usage: send_cmd <action> [url]
#        send_cmd_json '{"action":"capture","prefer":"gc"}'
send_cmd_json() {
  local payload="$1"
  cmd_bridge_start
  local resp id result
  resp="$(curl -fsS --max-time 10 -H 'Content-Type: application/json' -d "$payload" "${BRIDGE_URL}/v1/command")"
  id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$resp")"
  # capture payloads can be large — longer wait
  if ! result="$(curl -fsS --max-time 90 "${BRIDGE_URL}/v1/result/${id}?timeout=75")"; then
    die "no response from extension (timeout). Load unpacked + click icon.
  ${EXT_DIR}
Bridge: ${BRIDGE_URL}"
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

send_cmd() {
  local action="$1"
  shift || true
  local url="${1:-}"
  local payload
  if [[ -n "$url" ]]; then
    payload="$(python3 -c 'import json,sys,uuid; print(json.dumps({"id":str(uuid.uuid4()),"action":sys.argv[1],"url":sys.argv[2]}))' "$action" "$url")"
  else
    payload="$(python3 -c 'import json,sys,uuid; print(json.dumps({"id":str(uuid.uuid4()),"action":sys.argv[1]}))' "$action")"
  fi
  send_cmd_json "$payload"
}

cmd_start() {
  local url="${1:-}"
  echo "mode: hermes-chrome (daily Chrome workspace)"
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

cmd_list_tv() {
  send_cmd list_tv
}

cmd_capture() {
  # hermes-chrome.sh capture [--prefer gc|nq|auto|active] [--out path.png]
  # Large PNG base64 must NOT go through bash vars / argv — write temp files.
  local prefer="auto"
  local out=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --prefer|-p) prefer="${2:-auto}"; shift 2 ;;
      --out|-o) out="${2:-}"; shift 2 ;;
      gc|nq|auto|active) prefer="$1"; shift ;;
      *.png|*.jpg|*.webp) out="$1"; shift ;;
      *) die "usage: $0 capture [--prefer gc|nq|auto|active] [--out path.png]" ;;
    esac
  done
  [[ -n "$out" ]] || out="${RUN_DIR}/capture-${prefer}-$(date +%Y%m%d-%H%M%S).png"
  mkdir -p "$(dirname "$out")"
  cmd_bridge_start

  python3 - <<'PY' "$BRIDGE_URL" "$prefer" "$out"
import base64, json, sys, uuid, urllib.error, urllib.request
from pathlib import Path

bridge, prefer, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
cmd_id = str(uuid.uuid4())
payload = json.dumps({"id": cmd_id, "action": "capture", "prefer": prefer, "settleMs": 400}).encode()
req = urllib.request.Request(
    f"{bridge}/v1/command",
    data=payload,
    method="POST",
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=15) as r:
    enq = json.loads(r.read().decode())
rid = enq.get("id") or cmd_id
try:
    with urllib.request.urlopen(f"{bridge}/v1/result/{rid}?timeout=75", timeout=90) as r:
        body = r.read()
except urllib.error.HTTPError as e:
    print("error:", e.read().decode(errors="replace")[:500], file=sys.stderr)
    sys.exit(1)
if not body:
    print("error: empty result from extension", file=sys.stderr)
    sys.exit(1)
resp = json.loads(body)
if not resp.get("ok"):
    print("error:", resp.get("error") or resp, file=sys.stderr)
    sys.exit(1)
data = resp.get("data") or {}
b64 = data.get("pngBase64") or ""
if not b64:
    print("error: no pngBase64 in response", file=sys.stderr)
    sys.exit(1)
raw = base64.b64decode(b64)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_bytes(raw)
meta = {k: data.get(k) for k in ("title", "url", "tabId", "prefer", "bytes") if k in data}
meta["path"] = str(out.resolve())
meta["saved_bytes"] = len(raw)
# do not print base64
print(json.dumps(meta, indent=2, ensure_ascii=False))
PY
}

cmd_install_help() {
  cat <<EOF
Install Hermes Chrome extension (one-time):

1. Start bridge (optional; CLI auto-starts):
     $0 bridge-start

2. Chrome → chrome://extensions
   - Enable "Developer mode"
   - "Load unpacked"
   - Select: ${EXT_DIR}

3. Pin the extension, click its icon once (starts long-poll).
   After upgrades, click Reload on the extension card.

4. Test:
     $0 ping
     $0 list-tv
     $0 capture --prefer gc --out /tmp/gc.png
     $0 start https://example.com/
     $0 status
     $0 stop

Notes:
- Default workspace: Chrome Tab Group titled "Hermes" (blue, configurable).
- capture uses chrome.tabs.captureVisibleTab (needs tradingview host permission).
- Not the same as Agent Chrome profile (~/.hermes/chrome-debug).
- Gold pipeline: set TV_CAPTURE_BACKEND=hermes-chrome (optional; falls back).
EOF
}

usage() {
  sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
  echo
  echo "commands: start|open|new-tab|status|stop|ping|list-tv|capture|bridge-start|bridge-stop|bridge-status|install-help"
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
    list-tv|list_tv) cmd_list_tv ;;
    capture)        cmd_capture "$@" ;;
    bridge-start)   cmd_bridge_start ;;
    bridge-stop)    cmd_bridge_stop ;;
    bridge-status)  cmd_bridge_status ;;
    install-help)   cmd_install_help ;;
    -h|--help|help|"") usage; [[ -n "$cmd" ]] || exit 1 ;;
    *) die "unknown command: $cmd" ;;
  esac
}

main "$@"
