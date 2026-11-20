#!/usr/bin/env bash
# Hermes Chrome — operate daily Chrome from Hermes / local agent CLI.
#
# Requires:
#   1) Companion: install.sh / install-for-agent (bridge + native host)
#   2) Hermes Chrome extension (CWS or Load unpacked from extension/)
#   3) Click extension icon once so the service worker starts polling
#
# Usage:
#   hermes-chrome.sh start [url]
#   hermes-chrome.sh open <url>
#   hermes-chrome.sh new-tab <url>
#   hermes-chrome.sh navigate <url> [--tab-id N]
#   hermes-chrome.sh list-tabs [--all] [--url needle] [--title needle]
#   hermes-chrome.sh eval --tab-id N --expr 'document.title'
#   hermes-chrome.sh click --tab-id N --selector 'css'
#   hermes-chrome.sh type --tab-id N --selector 'css' --text '...'
#   hermes-chrome.sh status|stop|ping|list-tv|capture
#   hermes-chrome.sh check-url <url> [--no-follow]
#   hermes-chrome.sh download <url> [--out path] [--cookies] [--force] [--no-check] [--no-analyze]
#   hermes-chrome.sh analyze <path> [path...]
#   hermes-chrome.sh page-assets --tab-id N
#   hermes-chrome.sh check-tab-links --tab-id N
#   hermes-chrome.sh policy-show | token-setup | pair-open | show-token
#   hermes-chrome.sh bridge-start|bridge-stop|bridge-status|bridge-restart
#   hermes-chrome.sh install-launchd|uninstall-launchd|install-help
#   hermes-chrome.sh install           # full companion → ~/.hermes/hermes-chrome (recommended)
#   hermes-chrome.sh install-for-agent # bridge + native host + multi-client MCP (this tree)
#   hermes-chrome.sh install-native-host # Chrome Native Messaging only
#   hermes-chrome.sh install-mcp|doctor
#
# Global flags (before or after command):
#   --json / --json-only / -j   stdout = JSON only (quiet bridge messages)
#   --quiet / -q                suppress non-JSON chatter
#
# Env:
#   HERMES_CHROME_ROOT   — repo root (default: parent of scripts/)
#   HERMES_CHROME_BRIDGE_* or HERMES_TABGROUP_BRIDGE_*   — host/port for bridge
#   HERMES_CHROME_RUN    — runtime dir for pid/log (default ~/.hermes/run/hermes-chrome)
#   HERMES_CHROME_BRIDGE_TOKEN — shared token (auto-loaded from bridge.env)
#   HERMES_CHROME_BRIDGE_ALLOW_NO_AUTH=1 — insecure: disable bridge token
#   HERMES_CHROME_DOWNLOAD_MAX_BYTES — default 50MiB
#   HERMES_CHROME_JSON=1 — same as --json-only
#   HERMES_CHROME_POLICY — path to policy.json (allow/deny hosts)
#
# Policy: tabs created with active:false to reduce focus steal. Chrome may still
# briefly raise; we do NOT call AppleScript activate on daily Chrome.
#
set -euo pipefail

# Resolve symlinks so PATH shim (~/.local/bin/hermes-chrome) still finds scripts/doctor.py
_hc_src="${BASH_SOURCE[0]}"
while [[ -L "$_hc_src" ]]; do
  _hc_dir="$(cd "$(dirname "$_hc_src")" && pwd)"
  _hc_src="$(readlink "$_hc_src")"
  [[ "$_hc_src" != /* ]] && _hc_src="${_hc_dir}/${_hc_src}"
done
SCRIPT_DIR="$(cd "$(dirname "$_hc_src")" && pwd)"
unset _hc_src _hc_dir
ROOT="${HERMES_CHROME_ROOT:-${HERMES_DAILY_CHROME_ROOT:-$(cd "${SCRIPT_DIR}/.." && pwd)}}"
RUN_DIR="${HERMES_CHROME_RUN:-${HERMES_DAILY_CHROME_RUN:-$HOME/.hermes/run/hermes-chrome}}"
BRIDGE_PY="${ROOT}/bridge.py"
BRIDGE_HOST="${HERMES_CHROME_BRIDGE_HOST:-${HERMES_TABGROUP_BRIDGE_HOST:-127.0.0.1}}"
BRIDGE_PORT="${HERMES_CHROME_BRIDGE_PORT:-${HERMES_TABGROUP_BRIDGE_PORT:-19876}}"
BRIDGE_URL="http://${BRIDGE_HOST}:${BRIDGE_PORT}"
PID_FILE="${RUN_DIR}/bridge.pid"
LOG_FILE="${RUN_DIR}/bridge.log"
EXT_DIR="${ROOT}/extension"
JSON_ONLY="${HERMES_CHROME_JSON:-0}"
QUIET=0
[[ "$JSON_ONLY" == "1" || "$JSON_ONLY" == "true" ]] && QUIET=1
ENV_FILE="${RUN_DIR}/bridge.env"

mkdir -p "$RUN_DIR"

# Load shared bridge token when present (CLI + child python inherit).
if [[ -z "${HERMES_CHROME_BRIDGE_TOKEN:-}" && -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
fi

die() { echo "error: $*" >&2; exit 1; }
log() { [[ "${QUIET}" == "1" || "${JSON_ONLY}" == "1" ]] || echo "$@"; }
is_json() { [[ "${JSON_ONLY}" == "1" || "${JSON_ONLY}" == "true" ]]; }

find_python() {
  local c resolved
  for c in python3 python; do
    resolved="$(command -v "$c" 2>/dev/null || true)"
    if [[ -n "$resolved" && "$resolved" != *WindowsApps* ]]; then
      if "$resolved" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
        printf '%s\n' "$resolved"
        return 0
      fi
    fi
  done
  if command -v py >/dev/null 2>&1; then
    resolved="$(py -3 -c 'import sys; print(sys.executable)' 2>/dev/null || true)"
    if [[ -n "$resolved" && "$resolved" != *WindowsApps* ]]; then
      printf '%s\n' "$resolved"
      return 0
    fi
  fi
  return 1
}

PYTHON3="$(find_python || true)"
require_python() {
  [[ -n "$PYTHON3" ]] || die "python3 (>= 3.9) not found"
}

curl_with_auth() {
  if [[ -n "${HERMES_CHROME_BRIDGE_TOKEN:-}" ]]; then
    local safe_tok="${HERMES_CHROME_BRIDGE_TOKEN//[\"\$\'\\]/}"
    safe_tok="${safe_tok//$'\r'/}"
    safe_tok="${safe_tok//$'\n'/}"
    printf 'header = "X-Hermes-Chrome-Token: %s"\n' "$safe_tok" | curl -K - "$@"
  else
    curl "$@"
  fi
}

# Wait / retry tuning (override via env)
WAIT_TRIES="${HERMES_CHROME_WAIT_TRIES:-45}"          # ~45s default
WAIT_SLEEP_S="${HERMES_CHROME_WAIT_SLEEP:-1}"
CMD_RETRIES="${HERMES_CHROME_CMD_RETRIES:-3}"

# Prefer authenticated health (full detail); public health is liveness-only.
bridge_healthy() {
  curl_with_auth -fsS --max-time 1 "${BRIDGE_URL}/v1/health" >/dev/null 2>&1
}

bridge_health_json() {
  curl_with_auth -fsS --max-time 2 "${BRIDGE_URL}/v1/health" 2>/dev/null || echo '{}'
}

extension_connected() {
  require_python
  "$PYTHON3" - <<'PY' "$(bridge_health_json)"
import json, sys
try:
    h = json.loads(sys.argv[1] or "{}")
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if h.get("extension_connected") else 1)
PY
}

# Ensure token env is loaded (bridge may have just written bridge.env).
reload_bridge_token() {
  if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set -a
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    set +a
  fi
}

open_pairing_quiet() {
  reload_bridge_token
  curl_with_auth -fsS --max-time 3 -H 'Content-Type: application/json' -d '{}' "${BRIDGE_URL}/v1/pair-open" >/dev/null 2>&1 || true
}

# Wait for extension poll (auto-pair on extension side). Re-opens pairing only while disconnected.
wait_for_extension() {
  local tries="${1:-$WAIT_TRIES}"
  local i health
  reload_bridge_token
  if extension_connected; then
    return 0
  fi
  open_pairing_quiet
  for i in $(seq 1 "$tries"); do
    if extension_connected; then
      return 0
    fi
    # Periodically nudge pairing open + reload token (bridge auto-gen path).
    if (( i % 5 == 0 )); then
      open_pairing_quiet
      reload_bridge_token
    fi
    sleep "$WAIT_SLEEP_S"
  done
  health="$(bridge_health_json)"
  log "wait_for_extension: timed out after ${tries}s — health=${health}"
  return 1
}

cmd_bridge_status() {
  if bridge_healthy; then
    local health
    health="$(bridge_health_json)"
    if is_json; then
      echo "$health"
      return 0
    fi
    echo "bridge: running"
    echo "$health"
    # Human-readable extension gate from health payload.
    "$PYTHON3" - <<'PY' "$health" 2>/dev/null || true
import json, sys
h = json.loads(sys.argv[1])
conn = h.get("extension_connected")
age = h.get("extension_last_seen_s")
ver = h.get("extension_version")
print(
    f"extension: connected={conn} last_seen_s={age} version={ver!r} queued={h.get('queued')}"
)
if conn is False:
    print(
        "hint: extension not connected — Reload extension; it should auto-pair. "
        "Or: hermes-chrome.sh pair-open + popup Pair",
        file=sys.stderr,
    )
PY
    [[ -f "$PID_FILE" ]] && echo "pidfile: $(cat "$PID_FILE")"
    return 0
  fi
  if is_json; then
    echo '{"ok":false,"bridge":"stopped"}'
    return 1
  fi
  echo "bridge: stopped"
  return 1
}

cmd_bridge_start() {
  if bridge_healthy; then
    log "bridge already running"
    if ! is_json; then
      cmd_bridge_status || true
    fi
    return 0
  fi
  [[ -f "$BRIDGE_PY" ]] || die "missing $BRIDGE_PY"
  require_python
  # Ensure token env is visible to the bridge process (auto-generate if missing).
  if [[ -z "${HERMES_CHROME_BRIDGE_TOKEN:-}" && -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set -a
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    set +a
  fi
  # Launch detached without exposing secrets on command-line argv.
  (
    export HERMES_CHROME_RUN="${RUN_DIR}"
    export HERMES_CHROME_BRIDGE_HOST="${BRIDGE_HOST}"
    export HERMES_CHROME_BRIDGE_PORT="${BRIDGE_PORT}"
    if [[ -n "${HERMES_CHROME_BRIDGE_TOKEN:-}" ]]; then
      export HERMES_CHROME_BRIDGE_TOKEN
    fi
    if [[ -n "${HERMES_CHROME_BRIDGE_ALLOW_NO_AUTH:-}" ]]; then
      export HERMES_CHROME_BRIDGE_ALLOW_NO_AUTH
    fi
    exec "$PYTHON3" "$BRIDGE_PY" >>"$LOG_FILE" 2>&1
  ) &
  echo $! >"$PID_FILE"
  local i
  for i in $(seq 1 40); do
    if bridge_healthy; then
      # Reload token if bridge just generated bridge.env
      if [[ -z "${HERMES_CHROME_BRIDGE_TOKEN:-}" && -f "$ENV_FILE" ]]; then
        # shellcheck disable=SC1090
        set -a
        # shellcheck source=/dev/null
        source "$ENV_FILE"
        set +a
      fi
      log "bridge started: ${BRIDGE_URL}"
      return 0
    fi
    sleep 0.1
  done
  die "bridge failed to start — see $LOG_FILE"
}

cmd_bridge_stop() {
  # On macOS, unload launchd KeepAlive service if loaded so it doesn't respawn immediately.
  if [[ "$(uname -s 2>/dev/null || true)" == "Darwin" ]] && command -v launchctl >/dev/null 2>&1; then
    local uid
    uid="$(id -u 2>/dev/null || true)"
    if [[ -n "$uid" ]] && launchctl print "gui/${uid}/com.leaf76.hermes-chrome-bridge" >/dev/null 2>&1; then
      launchctl bootout "gui/${uid}/com.leaf76.hermes-chrome-bridge" 2>/dev/null \
        || launchctl unload "${HOME}/Library/LaunchAgents/com.leaf76.hermes-chrome-bridge.plist" 2>/dev/null \
        || true
    fi
  fi
  if [[ -f "$PID_FILE" ]]; then
    local file_pid
    file_pid="$(tr -d '[:space:]' <"$PID_FILE" 2>/dev/null || true)"
    if [[ "$file_pid" =~ ^[0-9]+$ ]]; then
      kill "$file_pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
  fi
  # best-effort by port (Unix)
  if command -v lsof >/dev/null 2>&1; then
    local p
    p="$(lsof -nP -iTCP:"${BRIDGE_PORT}" -sTCP:LISTEN -t 2>/dev/null | head -1 || true)"
    if [[ "$p" =~ ^[0-9]+$ ]]; then
      kill "$p" 2>/dev/null || true
    fi
  fi
  sleep 0.2
  if bridge_healthy; then
    die "bridge still healthy after stop"
  fi
  log "bridge stopped"
}

cmd_bridge_restart() {
  cmd_bridge_stop || true
  sleep 0.3
  cmd_bridge_start
}

# POST command and wait for extension result (with wait + retries).
# Usage: send_cmd <action> [url]
#        send_cmd_json '{"action":"capture","prefer":"gc"}'
send_cmd_json() {
  require_python
  local payload="$1"
  # Suppress bridge chatter for JSON consumers
  local _prev_quiet="$QUIET"
  QUIET=1
  cmd_bridge_start
  QUIET="$_prev_quiet"
  reload_bridge_token

  if ! wait_for_extension "$WAIT_TRIES"; then
    die "extension not connected after ${WAIT_TRIES}s.
  1) Chrome → Reload Hermes Chrome (unpacked: ${EXT_DIR})
  2) Wait a few seconds (auto-pair) or click Pair in Side Panel/Options
  3) Retry: $0 ping
Bridge: ${BRIDGE_URL}"
  fi

  local attempt resp id result curl_err
  for attempt in $(seq 1 "$CMD_RETRIES"); do
    reload_bridge_token
    if ! resp="$(curl_with_auth -fsS --max-time 10 -H 'Content-Type: application/json' -d "$payload" "${BRIDGE_URL}/v1/command" 2>/dev/null)"; then
      log "send_cmd: enqueue failed (attempt ${attempt}/${CMD_RETRIES}), retrying…"
      open_pairing_quiet
      wait_for_extension 15 || true
      sleep 0.5
      continue
    fi
    id="$("$PYTHON3" -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$resp" 2>/dev/null || true)"
    if [[ -z "$id" ]]; then
      log "send_cmd: bad enqueue response (attempt ${attempt}/${CMD_RETRIES})"
      sleep 0.5
      continue
    fi
    # capture payloads can be large — longer wait
    curl_err=0
    result="$(curl_with_auth -fsS --max-time 90 "${BRIDGE_URL}/v1/result/${id}?timeout=75" 2>/dev/null)" || curl_err=$?
    if [[ "$curl_err" -ne 0 || -z "$result" ]]; then
      log "send_cmd: no extension result (attempt ${attempt}/${CMD_RETRIES}), waiting…"
      open_pairing_quiet
      wait_for_extension 20 || true
      continue
    fi
    # Success path — parse and print
    local indent_flag="2"
    is_json && indent_flag="None"
    "$PYTHON3" - <<'PY' "$result" "$indent_flag"
import json, sys
r = json.loads(sys.argv[1])
indent = None if sys.argv[2] == "None" else 2
if not r.get("ok"):
    err = {"ok": False, "error": r.get("error") or r}
    print(json.dumps(err, indent=indent, ensure_ascii=False), file=sys.stderr)
    # still print machine-readable error on stdout in json mode
    if sys.argv[2] == "None":
        print(json.dumps(err, ensure_ascii=False))
    else:
        print("error:", r.get("error") or r, file=sys.stderr)
    sys.exit(1)
data = r.get("data")
print(json.dumps(data if data is not None else r, indent=indent, ensure_ascii=False))
PY
    return $?
  done

  die "no response from extension after ${CMD_RETRIES} attempts.
  Reload Hermes Chrome (auto-pair) or: $0 pair-open + Side Panel/Options Pair
  ${EXT_DIR}
Bridge: ${BRIDGE_URL}"
}

send_cmd() {
  require_python
  local action="$1"
  shift || true
  local url="${1:-}"
  local payload
  if [[ -n "$url" ]]; then
    payload="$("$PYTHON3" -c 'import json,sys,uuid; print(json.dumps({"id":str(uuid.uuid4()),"action":sys.argv[1],"url":sys.argv[2]}))' "$action" "$url")"
  else
    payload="$("$PYTHON3" -c 'import json,sys,uuid; print(json.dumps({"id":str(uuid.uuid4()),"action":sys.argv[1]}))' "$action")"
  fi
  send_cmd_json "$payload"
}

_workspace_open_cmd() {
  require_python
  local action="$1"
  shift || true
  local url="" separate_window=false width="" height="" new_tab=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --url) url="${2:-}"; shift 2 ;;
      --separate-window|--separate_window) separate_window=true; shift ;;
      --width) width="${2:-}"; shift 2 ;;
      --height) height="${2:-}"; shift 2 ;;
      --new-tab|--new_tab) new_tab=true; shift ;;
      *) url="$1"; shift ;;
    esac
  done
  if [[ "$action" != "start" && -z "$url" ]]; then
    die "usage: $0 ${action} <url> [--separate-window] [--width N] [--height N]"
  fi
  local payload
  payload="$("$PYTHON3" - <<'PY' "$action" "$url" "$separate_window" "$width" "$height" "$new_tab"
import json, sys, uuid
action, url, sep, width, height, new_tab = sys.argv[1:7]
obj = {"id": str(uuid.uuid4()), "action": action}
if url:
    obj["url"] = url
if sep == "true":
    obj["separateWindow"] = True
if new_tab == "true":
    obj["newTab"] = True
if width:
    obj["width"] = int(width)
if height:
    obj["height"] = int(height)
print(json.dumps(obj))
PY
)"
  send_cmd_json "$payload"
}

cmd_start() {
  log "mode: hermes-chrome (daily Chrome workspace)"
  _workspace_open_cmd start "$@"
}

cmd_open() {
  _workspace_open_cmd open "$@"
}

cmd_new_tab() {
  _workspace_open_cmd new_tab "$@"
}

cmd_status() {
  send_cmd status
}

cmd_stop() {
  require_python
  local close_tabs=true
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --keep-tabs|--keep_tabs) close_tabs=false; shift ;;
      *) die "usage: $0 stop [--keep-tabs]" ;;
    esac
  done
  local payload
  payload="$("$PYTHON3" -c 'import json,sys,uuid; print(json.dumps({"id":str(uuid.uuid4()),"action":"stop","closeTabs":sys.argv[1]=="true"}))' "$close_tabs")"
  send_cmd_json "$payload"
}

cmd_ping() {
  send_cmd ping
}

cmd_list_tv() {
  send_cmd list_tv
}

cmd_list_tabs() {
  require_python
  # Default: workspace group only. --all lists every tab (privacy-sensitive).
  local all_tabs=false url_includes="" title_includes="" limit=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --all|--every) all_tabs=true; shift ;;
      --group|--workspace|-g) all_tabs=false; shift ;; # explicit default
      --url) url_includes="${2:-}"; shift 2 ;;
      --title) title_includes="${2:-}"; shift 2 ;;
      --limit) limit="${2:-}"; shift 2 ;;
      *) die "usage: $0 list-tabs [--all] [--url needle] [--title needle] [--limit N]" ;;
    esac
  done
  local payload
  payload="$("$PYTHON3" - <<'PY' "$all_tabs" "$url_includes" "$title_includes" "$limit"
import json, sys, uuid
all_tabs = sys.argv[1] == "true"
url_includes, title_includes, limit = sys.argv[2], sys.argv[3], sys.argv[4]
obj = {"id": str(uuid.uuid4()), "action": "list_tabs"}
if all_tabs:
    obj["allTabs"] = True
    obj["groupOnly"] = False
else:
    obj["groupOnly"] = True
if url_includes:
    obj["urlIncludes"] = url_includes
if title_includes:
    obj["titleIncludes"] = title_includes
if limit:
    obj["limit"] = int(limit)
print(json.dumps(obj))
PY
)"
  send_cmd_json "$payload"
}

cmd_navigate() {
  require_python
  local url="" tab_id=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tab-id|--tabId) tab_id="${2:-}"; shift 2 ;;
      --url) url="${2:-}"; shift 2 ;;
      http*|*://*) url="$1"; shift ;;
      *) die "usage: $0 navigate <url> [--tab-id N]" ;;
    esac
  done
  [[ -n "$url" ]] || die "usage: $0 navigate <url> [--tab-id N]"
  local payload
  payload="$("$PYTHON3" - <<'PY' "$url" "$tab_id"
import json, sys, uuid
url, tab_id = sys.argv[1], sys.argv[2]
obj = {"id": str(uuid.uuid4()), "action": "navigate", "url": url, "active": False}
if tab_id:
    obj["tabId"] = int(tab_id)
print(json.dumps(obj))
PY
)"
  send_cmd_json "$payload"
}

cmd_read_page() {
  require_python
  local url="" tab_id="" wait_ms="" text_max="" separate_window=false width="" height=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tab-id|--tabId) tab_id="${2:-}"; shift 2 ;;
      --url) url="${2:-}"; shift 2 ;;
      --wait-ms|--waitMs) wait_ms="${2:-}"; shift 2 ;;
      --text-max|--textMax) text_max="${2:-}"; shift 2 ;;
      --separate-window|--separate_window) separate_window=true; shift ;;
      --width) width="${2:-}"; shift 2 ;;
      --height) height="${2:-}"; shift 2 ;;
      http*|*://*) url="$1"; shift ;;
      *) die "usage: $0 read-page [--url URL|--tab-id N] [--wait-ms N] [--text-max N] [--separate-window]" ;;
    esac
  done
  [[ -n "$url" || -n "$tab_id" ]] || die "usage: $0 read-page [--url URL|--tab-id N]"
  local payload
  payload="$("$PYTHON3" - <<'PY' "$url" "$tab_id" "$wait_ms" "$text_max" "$separate_window" "$width" "$height"
import json, sys, uuid
url, tab_id, wait_ms, text_max, sep, width, height = sys.argv[1:8]
obj = {"id": str(uuid.uuid4()), "action": "read_page"}
if url:
    obj["url"] = url
if tab_id:
    obj["tabId"] = int(tab_id)
if wait_ms:
    obj["waitMs"] = int(wait_ms)
if text_max:
    obj["textMax"] = int(text_max)
if sep == "true":
    obj["separateWindow"] = True
if width:
    obj["width"] = int(width)
if height:
    obj["height"] = int(height)
print(json.dumps(obj))
PY
)"
  send_cmd_json "$payload"
}

cmd_eval() {
  require_python
  local tab_id="" expr=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tab-id|--tabId) tab_id="${2:-}"; shift 2 ;;
      --expr|--expression|-e) expr="${2:-}"; shift 2 ;;
      *) die "usage: $0 eval --tab-id N --expr 'document.title'" ;;
    esac
  done
  [[ -n "$tab_id" && -n "$expr" ]] || die "usage: $0 eval --tab-id N --expr 'document.title'"
  local payload
  payload="$("$PYTHON3" -c 'import json,sys,uuid; print(json.dumps({"id":str(uuid.uuid4()),"action":"eval","tabId":int(sys.argv[1]),"expression":sys.argv[2]}))' "$tab_id" "$expr")"
  send_cmd_json "$payload"
}

cmd_click() {
  require_python
  local tab_id="" selector="" text="" text_includes="" index="" visible_only="" mouse_events=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tab-id|--tabId) tab_id="${2:-}"; shift 2 ;;
      --selector|-s) selector="${2:-}"; shift 2 ;;
      --text) text="${2:-}"; shift 2 ;;
      --text-includes|--textIncludes) text_includes="${2:-}"; shift 2 ;;
      --index) index="${2:-}"; shift 2 ;;
      --visible-only) visible_only=true; shift ;;
      --no-visible-only) visible_only=false; shift ;;
      --mouse-events) mouse_events=true; shift ;;
      *) die "usage: $0 click --tab-id N [--selector 'css'|--text S|--text-includes S] [--index N] [--mouse-events]" ;;
    esac
  done
  [[ -n "$tab_id" && ( -n "$selector" || -n "$text" || -n "$text_includes" ) ]] || \
    die "usage: $0 click --tab-id N (--selector 'css' | --text S | --text-includes S)"
  local payload
  payload="$("$PYTHON3" - <<'PY' "$tab_id" "$selector" "$text" "$text_includes" "$index" "$visible_only" "$mouse_events"
import json, sys, uuid
tab_id, sel, txt, sub, idx, vis, mouse = sys.argv[1:8]
obj = {"id": str(uuid.uuid4()), "action": "click", "tabId": int(tab_id)}
if sel:
    obj["selector"] = sel
if txt:
    obj["text"] = txt
if sub:
    obj["textIncludes"] = sub
if idx:
    obj["index"] = int(idx)
if vis == "true":
    obj["visibleOnly"] = True
elif vis == "false":
    obj["visibleOnly"] = False
if mouse == "true":
    obj["mouseEvents"] = True
print(json.dumps(obj))
PY
)"
  send_cmd_json "$payload"
}

cmd_dom_query() {
  require_python
  local tab_id="" selector="" text="" text_includes="" aria_label="" visible_only="true" limit="" text_max=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tab-id|--tabId) tab_id="${2:-}"; shift 2 ;;
      --selector|-s) selector="${2:-}"; shift 2 ;;
      --text) text="${2:-}"; shift 2 ;;
      --text-includes|--textIncludes) text_includes="${2:-}"; shift 2 ;;
      --aria-label|--ariaLabel) aria_label="${2:-}"; shift 2 ;;
      --all|--no-visible-only) visible_only="false"; shift ;;
      --visible-only) visible_only="true"; shift ;;
      --limit) limit="${2:-}"; shift 2 ;;
      --text-max|--textMax) text_max="${2:-}"; shift 2 ;;
      *) die "usage: $0 dom-query --tab-id N [--selector CSS] [--text S] [--text-includes S] [--aria-label S] [--all] [--limit N]" ;;
    esac
  done
  [[ -n "$tab_id" ]] || die "usage: $0 dom-query --tab-id N [...]"
  local payload
  payload="$("$PYTHON3" - <<'PY' "$tab_id" "$selector" "$text" "$text_includes" "$aria_label" "$visible_only" "$limit" "$text_max"
import json, sys, uuid
tab_id, sel, txt, sub, aria, vis, limit, txt_max = sys.argv[1:9]
obj = {"id": str(uuid.uuid4()), "action": "dom_query", "tabId": int(tab_id), "visibleOnly": vis == "true"}
if sel:
    obj["selector"] = sel
if txt:
    obj["text"] = txt
if sub:
    obj["textIncludes"] = sub
if aria:
    obj["ariaLabel"] = aria
if limit:
    obj["limit"] = int(limit)
if txt_max:
    obj["textMax"] = int(txt_max)
print(json.dumps(obj))
PY
)"
  send_cmd_json "$payload"
}

cmd_editor_get() {
  require_python
  local tab_id="" selector="" model_index="" max_chars="" list_models=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tab-id|--tabId) tab_id="${2:-}"; shift 2 ;;
      --selector|-s) selector="${2:-}"; shift 2 ;;
      --model-index|--modelIndex) model_index="${2:-}"; shift 2 ;;
      --max-chars|--maxChars) max_chars="${2:-}"; shift 2 ;;
      --list-models|--listModels) list_models=true; shift ;;
      *) die "usage: $0 editor-get --tab-id N [--selector CSS] [--model-index N] [--max-chars N] [--list-models]" ;;
    esac
  done
  [[ -n "$tab_id" ]] || die "usage: $0 editor-get --tab-id N"
  local payload
  payload="$("$PYTHON3" - <<'PY' "$tab_id" "$selector" "$model_index" "$max_chars" "$list_models"
import json, sys, uuid
tab_id, sel, midx, maxc, lm = sys.argv[1:6]
obj = {"id": str(uuid.uuid4()), "action": "editor_get", "tabId": int(tab_id)}
if sel:
    obj["selector"] = sel
if midx:
    obj["modelIndex"] = int(midx)
if maxc:
    obj["maxChars"] = int(maxc)
if lm == "true":
    obj["listModels"] = True
print(json.dumps(obj))
PY
)"
  send_cmd_json "$payload"
}

cmd_editor_set() {
  require_python
  local tab_id="" selector="" text="" file_path="" has_text=false model_index="" focus=true
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tab-id|--tabId) tab_id="${2:-}"; shift 2 ;;
      --selector|-s) selector="${2:-}"; shift 2 ;;
      --text|-t) text="${2:-}"; has_text=true; shift 2 ;;
      --file|-f) file_path="${2:-}"; shift 2 ;;
      --model-index|--modelIndex) model_index="${2:-}"; shift 2 ;;
      --no-focus) focus=false; shift ;;
      *) die "usage: $0 editor-set --tab-id N (--text STR | --file PATH) [--selector CSS] [--model-index N] [--no-focus]" ;;
    esac
  done
  [[ -n "$tab_id" && ( "$has_text" == true || -n "$file_path" ) ]] || \
    die "usage: $0 editor-set --tab-id N (--text STR | --file PATH)"
  if [[ -n "$file_path" ]]; then
    [[ -f "$file_path" ]] || die "missing file: $file_path"
  fi
  local payload
  payload="$("$PYTHON3" - <<'PY' "$tab_id" "$selector" "$text" "$file_path" "$model_index" "$focus"
import json, sys, uuid
from pathlib import Path
tab_id, sel, txt, fp, midx, focus = sys.argv[1:7]
if fp:
    txt = Path(fp).read_text(encoding="utf-8")
obj = {"id": str(uuid.uuid4()), "action": "editor_set", "tabId": int(tab_id), "text": txt, "focus": focus == "true"}
if sel:
    obj["selector"] = sel
if midx:
    obj["modelIndex"] = int(midx)
print(json.dumps(obj))
PY
)"
  send_cmd_json "$payload"
}

cmd_type() {
  require_python
  local tab_id="" selector="" text="" clear="true"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tab-id|--tabId) tab_id="${2:-}"; shift 2 ;;
      --selector|-s) selector="${2:-}"; shift 2 ;;
      --text|-t) text="${2:-}"; shift 2 ;;
      --append) clear="false"; shift ;;
      *) die "usage: $0 type --tab-id N --selector 'css' --text '...' [--append]" ;;
    esac
  done
  [[ -n "$tab_id" && -n "$selector" ]] || die "usage: $0 type --tab-id N --selector 'css' --text '...'"
  local payload
  payload="$("$PYTHON3" -c 'import json,sys,uuid; print(json.dumps({"id":str(uuid.uuid4()),"action":"type","tabId":int(sys.argv[1]),"selector":sys.argv[2],"text":sys.argv[3],"clear":sys.argv[4]=="true"}))' "$tab_id" "$selector" "$text" "$clear")"
  send_cmd_json "$payload"
}

cmd_check_url() {
  require_python
  local follow="--follow"
  local url=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --no-follow) follow="--no-follow"; shift ;;
      --follow) follow="--follow"; shift ;;
      http*|*://*) url="$1"; shift ;;
      *) url="$1"; shift ;;
    esac
  done
  [[ -n "$url" ]] || die "usage: $0 check-url <url> [--no-follow]"
  "$PYTHON3" "${ROOT}/lib/check_url.py" "$follow" "$url"
}

cmd_analyze() {
  require_python
  [[ $# -gt 0 ]] || die "usage: $0 analyze <path> [path...]"
  "$PYTHON3" "${ROOT}/lib/analyze_file.py" "$@"
}

cmd_download() {
  require_python
  local url="" out="" cookies=false force=false check=true analyze=true
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --out|-o) out="${2:-}"; shift 2 ;;
      --cookies|--with-cookies) cookies=true; shift ;;
      --force) force=true; shift ;;
      --no-check) check=false; shift ;;
      --no-analyze) analyze=false; shift ;;
      http*|*://*) url="$1"; shift ;;
      *) die "usage: $0 download <url> [--out path] [--cookies] [--force] [--no-check] [--no-analyze]" ;;
    esac
  done
  [[ -n "$url" ]] || die "usage: $0 download <url> [--out path] [--cookies] [--force] [--no-check] [--no-analyze]"
  local args=("$url")
  [[ -n "$out" ]] && args+=(--out "$out")
  [[ "$cookies" == true ]] && args+=(--cookies)
  [[ "$force" == true ]] && args+=(--force)
  [[ "$check" == false ]] && args+=(--no-check)
  [[ "$analyze" == false ]] && args+=(--no-analyze)
  if [[ "$cookies" == true ]]; then
    cmd_bridge_start
    export HERMES_CHROME_BRIDGE="${BRIDGE_URL}"
    export HERMES_CHROME_RUN="${RUN_DIR}"
  fi
  "$PYTHON3" "${ROOT}/lib/download_file.py" "${args[@]}"
}

cmd_page_assets() {
  require_python
  local tab_id="" limit=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tab-id|--tabId) tab_id="${2:-}"; shift 2 ;;
      --limit) limit="${2:-}"; shift 2 ;;
      *) die "usage: $0 page-assets --tab-id N [--limit N]" ;;
    esac
  done
  [[ -n "$tab_id" ]] || die "usage: $0 page-assets --tab-id N"
  local payload
  payload="$("$PYTHON3" - <<'PY' "$tab_id" "$limit"
import json, sys, uuid
tab_id, limit = sys.argv[1], sys.argv[2]
obj = {"id": str(uuid.uuid4()), "action": "page_assets", "tabId": int(tab_id)}
if limit:
    obj["limit"] = int(limit)
print(json.dumps(obj))
PY
)"
  send_cmd_json "$payload"
}

cmd_check_tab_links() {
  require_python
  local tab_id="" limit="50"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tab-id|--tabId) tab_id="${2:-}"; shift 2 ;;
      --limit) limit="${2:-}"; shift 2 ;;
      *) die "usage: $0 check-tab-links --tab-id N [--limit N]" ;;
    esac
  done
  [[ -n "$tab_id" ]] || die "usage: $0 check-tab-links --tab-id N"
  local payload assets
  payload="$("$PYTHON3" - <<'PY' "$tab_id" "$limit"
import json, sys, uuid
print(json.dumps({
  "id": str(uuid.uuid4()),
  "action": "page_assets",
  "tabId": int(sys.argv[1]),
  "limit": int(sys.argv[2] or 50),
}))
PY
)"
  JSON_ONLY=1
  QUIET=1
  assets="$(send_cmd_json "$payload")"
  "$PYTHON3" - <<'PY' "$ROOT" "$assets"
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "lib"))
from check_url import check_url

assets = json.loads(sys.argv[2])
links = assets.get("links") or []
seen = set()
results = []
worst = 0
for link in links:
    href = link.get("href") or ""
    if not href or href in seen:
        continue
    seen.add(href)
    r = check_url(href, follow=False)
    entry = {
        "href": href,
        "text": link.get("text"),
        "ok": r.get("ok"),
        "risk": r.get("risk"),
        "findings": r.get("findings") or [],
    }
    results.append(entry)
    if not r.get("ok"):
        worst = max(worst, 2)
    elif r.get("risk") == "medium":
        worst = max(worst, 1)

out = {
    "ok": worst < 2,
    "pageUrl": assets.get("pageUrl"),
    "title": assets.get("title"),
    "checked": len(results),
    "blocked": sum(1 for x in results if not x.get("ok")),
    "warn": sum(1 for x in results if x.get("risk") == "medium"),
    "links": results,
}
print(json.dumps(out, indent=2, ensure_ascii=False))
sys.exit(worst)
PY
}

cmd_policy_show() {
  require_python
  "$PYTHON3" "${ROOT}/lib/policy.py" show
}

cmd_token_setup() {
  bash "${ROOT}/scripts/token-setup.sh" "$@"
}

cmd_show_token() {
  if [[ -z "${HERMES_CHROME_BRIDGE_TOKEN:-}" && -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set -a
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    set +a
  fi
  if [[ -z "${HERMES_CHROME_BRIDGE_TOKEN:-}" ]]; then
    die "no token (start bridge once to auto-generate, or: $0 token-setup generate)"
  fi
  if is_json; then
    require_python
    "$PYTHON3" -c 'import json,os; print(json.dumps({"ok":True,"token_set":True,"len":len(os.environ.get("HERMES_CHROME_BRIDGE_TOKEN",""))}))'
  else
    echo "token_set=true len=${#HERMES_CHROME_BRIDGE_TOKEN} file=${ENV_FILE}"
    echo "Paste into extension Options, or click Pair while pairing window is open."
    echo "To print the secret: $0 token-setup show  (does not print raw secret)"
    echo "Raw secret is in: $ENV_FILE (chmod 600)"
  fi
}

cmd_pair_open() {
  cmd_bridge_start
  local resp
  resp="$(curl_with_auth -fsS --max-time 5 -H 'Content-Type: application/json' -d '{}' "${BRIDGE_URL}/v1/pair-open")"
  if is_json; then
    echo "$resp"
  else
    echo "$resp"
    echo "Next: open Hermes Chrome Side Panel/Options → Pair (within pairing window)."
  fi
}

cmd_capture() {
  # hermes-chrome.sh capture [--prefer gc|nq|auto|active] [--out path.png] [--tab-id N] [--url S] [--title S] [--settle-ms N] [--ensure-tf TF] [--tf-settle-ms N] [--allow-active]
  # Large PNG base64 must NOT go through bash vars / argv — write temp files.
  local prefer="auto"
  local out=""
  local tab_id=""
  local url_includes=""
  local title_includes=""
  local settle_ms="400"
  local ensure_tf=""
  local tf_settle_ms=""
  local allow_active="0"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --prefer|-p) prefer="${2:-auto}"; shift 2 ;;
      --out|-o) out="${2:-}"; shift 2 ;;
      --tab-id|--tabId) tab_id="${2:-}"; shift 2 ;;
      --url|--url-includes) url_includes="${2:-}"; shift 2 ;;
      --title|--title-includes) title_includes="${2:-}"; shift 2 ;;
      --settle-ms|--settleMs) settle_ms="${2:-400}"; shift 2 ;;
      --ensure-tf|--ensureTf) ensure_tf="${2:-}"; shift 2 ;;
      --tf-settle-ms|--tfSettleMs) tf_settle_ms="${2:-}"; shift 2 ;;
      --allow-active) allow_active="1"; shift ;;
      gc|nq|auto|active) prefer="$1"; shift ;;
      *.png|*.jpg|*.webp) out="$1"; shift ;;
      *) die "usage: $0 capture [--prefer gc|nq|auto|active] [--out path.png] [--tab-id N] [--url S] [--title S] [--settle-ms N] [--ensure-tf TF] [--tf-settle-ms N] [--allow-active]" ;;
    esac
  done
  [[ -n "$out" ]] || out="${RUN_DIR}/capture-${prefer}-$(date +%Y%m%d-%H%M%S).png"
  mkdir -p "$(dirname "$out")"
  cmd_bridge_start
  require_python

  "$PYTHON3" - <<'PY' "$BRIDGE_URL" "$prefer" "$out" "$tab_id" "$url_includes" "$title_includes" "$settle_ms" "$ensure_tf" "$tf_settle_ms" "$allow_active"
import base64, json, os, sys, uuid, urllib.error, urllib.request
from pathlib import Path

(
    bridge,
    prefer,
    out_str,
    tab_id,
    url_includes,
    title_includes,
    settle_ms,
    ensure_tf,
    tf_settle_ms,
    allow_active,
) = sys.argv[1:11]
out = Path(out_str)
cmd_id = str(uuid.uuid4())
try:
    settle_val = int(settle_ms)
    tab_id_val = int(tab_id) if tab_id else None
    tf_settle_val = int(tf_settle_ms) if tf_settle_ms else None
except ValueError as exc:
    print(f"error: invalid integer flag value: {exc}", file=sys.stderr)
    sys.exit(1)

obj = {"id": cmd_id, "action": "capture", "prefer": prefer, "settleMs": settle_val}
if tab_id_val is not None:
    obj["tabId"] = tab_id_val
if url_includes:
    obj["urlIncludes"] = url_includes
if title_includes:
    obj["titleIncludes"] = title_includes
if ensure_tf:
    obj["ensureTf"] = ensure_tf
if tf_settle_val is not None:
    obj["tfSettleMs"] = tf_settle_val
if allow_active == "1" or prefer == "active":
    obj["allowActiveCapture"] = True
    obj["allowCrossWorkspace"] = True
if prefer in ("gc", "nq"):
    obj["allowCrossWorkspace"] = True

payload = json.dumps(obj).encode()
headers = {"Content-Type": "application/json"}
tok = (os.environ.get("HERMES_CHROME_BRIDGE_TOKEN") or "").strip()
if tok:
    headers["X-Hermes-Chrome-Token"] = tok
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
req = urllib.request.Request(
    f"{bridge}/v1/command",
    data=payload,
    method="POST",
    headers=headers,
)
try:
    with opener.open(req, timeout=15) as r:
        enq = json.loads(r.read().decode())
except urllib.error.HTTPError as e:
    try:
        err_msg = e.read().decode(errors="replace")[:500]
    finally:
        e.close()
    print("error:", err_msg, file=sys.stderr)
    sys.exit(1)
rid = enq.get("id") or cmd_id
try:
    rreq = urllib.request.Request(
        f"{bridge}/v1/result/{rid}?timeout=75",
        headers={k: v for k, v in headers.items() if k != "Content-Type"},
        method="GET",
    )
    with opener.open(rreq, timeout=90) as r:
        body = r.read()
except urllib.error.HTTPError as e:
    try:
        err_msg = e.read().decode(errors="replace")[:500]
    finally:
        e.close()
    print("error:", err_msg, file=sys.stderr)
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

cmd_install_for_agent() {
  local helper="${SCRIPT_DIR}/install-for-agent.sh"
  [[ -f "$helper" ]] || die "missing $helper"
  bash "$helper" "$@"
}

cmd_install_full() {
  local helper="${SCRIPT_DIR}/install.sh"
  [[ -f "$helper" ]] || die "missing $helper"
  bash "$helper" "$@"
}

cmd_install_mcp() {
  require_python
  local helper="${SCRIPT_DIR}/install-mcp.py"
  [[ -f "$helper" ]] || die "missing $helper"
  "$PYTHON3" "$helper" --root "$ROOT" "$@"
}

cmd_doctor() {
  require_python
  local helper="${SCRIPT_DIR}/doctor.py"
  [[ -f "$helper" ]] || die "missing $helper"
  if [[ "${JSON_ONLY}" == "1" ]]; then
    "$PYTHON3" "$helper" --json "$@"
  else
    "$PYTHON3" "$helper" "$@"
  fi
}

cmd_self_update() {
  require_python
  local helper="${ROOT}/lib/self_update.py"
  [[ -f "$helper" ]] || die "missing $helper"
  local sub="${1:-now}"
  shift || true
  case "$sub" in
    enable|on) "$PYTHON3" "$helper" enable ;;
    disable|off) "$PYTHON3" "$helper" disable ;;
    status) "$PYTHON3" "$helper" status ;;
    now|run|"" ) "$PYTHON3" "$helper" --force --restart ;;
    maybe) "$PYTHON3" "$helper" --maybe --restart ;;
    *) die "usage: $0 self-update enable|disable|status|now" ;;
  esac
}

cmd_install_native_host() {
  local helper="${SCRIPT_DIR}/install-native-host.sh"
  [[ -f "$helper" ]] || die "missing $helper"
  bash "$helper" "${1:-install}"
}

cmd_install_help() {
  cat <<EOF
=== Recommended: full companion install (fixed root) ===

  # One-liner (no prior clone required):
  curl -fsSL https://raw.githubusercontent.com/leaf76/hermes-chrome/main/scripts/install.sh | bash

  # From this tree:
  $0 install
  # Windows PowerShell:
  #   powershell -ExecutionPolicy Bypass -File scripts/install-windows.ps1

Installs to ~/.hermes/hermes-chrome (+ runtime ~/.hermes/run/hermes-chrome):
  • local bridge (autostart on macOS/Windows)
  • Chrome Native Messaging host (extension auto-starts bridge)
  • PATH shim ~/.local/bin/hermes-chrome
  • MCP for Grok / Cursor / Claude Desktop when present (mcp_server.py — not npm)

Then:
  1. Enable Hermes Chrome v1.7+ (CWS or Load unpacked → ${EXT_DIR})
  2. Reload + click the extension icon once
  3. Wait for auto-pair (or popup → Pair) — Ready: Connected
  4. Restart MCP agent sessions for hermes_chrome_* tools
     (CLI users only need steps 1–3)

Smoke:
  hermes-chrome --json bridge-status
  hermes-chrome --json ping
  hermes-chrome doctor

=== Same-tree install (dev) ===

  $0 install-for-agent
  $0 install-mcp
  $0 doctor

=== Manual install (CLI only) ===

1. Start bridge (optional; CLI auto-starts; launchd recommended):
     $0 bridge-start
     $0 install-launchd   # macOS: start at login + KeepAlive
     # Windows: scripts/install-windows.ps1

2. Chrome → chrome://extensions
   - Enable "Developer mode"
   - "Load unpacked" → ${EXT_DIR}
     (or install from Chrome Web Store when published)
   - After upgrades: Reload + accept permissions + click icon

3. Auth (required by default since v1.5.0):
     $0 bridge-start      # auto-writes ~/.hermes/run/hermes-chrome/bridge.env
     $0 pair-open         # open pairing window
     # Extension popup/Options → Pair  (or paste token from bridge.env)

4. Pin the extension, click its icon once (starts long-poll).

5. Test:
     $0 bridge-status
     $0 ping
     $0 doctor

Notes:
- CWS extension alone cannot listen on :19876 — install companion once.
- NOT on npm / PyPI. Companion is GitHub + install.sh (or this tree).
- MCP is optional: python3 ${ROOT}/mcp_server.py  (snippet: ~/.hermes/run/hermes-chrome/mcp-snippet.json)
- Native host name: com.leaf76.hermes_chrome
- Guides: docs/GUIDE.md · docs/GUIDE.zh-TW.md · extension help.html
EOF
}

cmd_install_launchd() {
  local helper="${SCRIPT_DIR}/install-launchd.sh"
  [[ -x "$helper" || -f "$helper" ]] || die "missing $helper"
  bash "$helper" install
}

cmd_uninstall_launchd() {
  local helper="${SCRIPT_DIR}/install-launchd.sh"
  [[ -f "$helper" ]] || die "missing $helper"
  bash "$helper" uninstall
}

usage() {
  sed -n '2,35p' "$0" | sed 's/^# \{0,1\}//'
  echo
  echo "commands: start|open|new-tab|navigate|read-page|list-tabs|eval|click|type|dom-query|editor-get|editor-set|page-assets|check-tab-links|check-url|download|analyze|policy-show|token-setup|pair-open|show-token|status|stop|ping|list-tv|capture|bridge-start|bridge-stop|bridge-status|bridge-restart|install|install-for-agent|install-mcp|install-native-host|install-launchd|uninstall-launchd|install-help|doctor|self-update"
  echo "flags: --json|--json-only|-j  --quiet|-q"
}

main() {
  # Global flags anywhere in argv
  local args=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --json|--json-only|-j)
        JSON_ONLY=1
        QUIET=1
        export HERMES_CHROME_JSON=1
        shift
        ;;
      --quiet|-q)
        QUIET=1
        shift
        ;;
      *)
        args+=("$1")
        shift
        ;;
    esac
  done
  set -- "${args[@]+"${args[@]}"}"

  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    start)          cmd_start "$@" ;;
    open)           cmd_open "$@" ;;
    new-tab)        cmd_new_tab "$@" ;;
    navigate)       cmd_navigate "$@" ;;
    read-page|read_page) cmd_read_page "$@" ;;
    list-tabs|list_tabs) cmd_list_tabs "$@" ;;
    eval|evaluate)  cmd_eval "$@" ;;
    click)          cmd_click "$@" ;;
    type)           cmd_type "$@" ;;
    dom-query|dom_query) cmd_dom_query "$@" ;;
    editor-get|editor_get) cmd_editor_get "$@" ;;
    editor-set|editor_set) cmd_editor_set "$@" ;;
    page-assets|page_assets|list-assets) cmd_page_assets "$@" ;;
    check-tab-links|check_tab_links) cmd_check_tab_links "$@" ;;
    check-url|check_url) cmd_check_url "$@" ;;
    download|dl)    cmd_download "$@" ;;
    analyze|check-file|check_file) cmd_analyze "$@" ;;
    policy-show|policy) cmd_policy_show "$@" ;;
    token-setup)    cmd_token_setup "$@" ;;
    pair-open|pair_open) cmd_pair_open ;;
    show-token|show_token) cmd_show_token ;;
    status)         cmd_status ;;
    stop)           cmd_stop "$@" ;;
    ping)           cmd_ping ;;
    list-tv|list_tv) cmd_list_tv ;;
    capture)        cmd_capture "$@" ;;
    bridge-start)   cmd_bridge_start ;;
    bridge-stop)    cmd_bridge_stop ;;
    bridge-status)  cmd_bridge_status ;;
    bridge-restart) cmd_bridge_restart ;;
    install-launchd) cmd_install_launchd ;;
    uninstall-launchd) cmd_uninstall_launchd ;;
    install|install-full)
                    cmd_install_full "$@" ;;
    install-for-agent|install_for_agent|install-agent)
                    cmd_install_for_agent "$@" ;;
    install-mcp|install_mcp)
                    cmd_install_mcp "$@" ;;
    install-native-host|install_native_host|native-host)
                    cmd_install_native_host "$@" ;;
    install-help)   cmd_install_help ;;
    doctor)         cmd_doctor "$@" ;;
    self-update|self_update|auto-update)
                    cmd_self_update "$@" ;;
    -h|--help|help|"") usage; [[ -n "$cmd" ]] || exit 1 ;;
    *) die "unknown command: $cmd" ;;
  esac
}

main "$@"
