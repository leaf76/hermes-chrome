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
#   hermes-chrome.sh navigate <url> [--tab-id N]
#   hermes-chrome.sh list-tabs [--group] [--url needle] [--title needle]
#   hermes-chrome.sh eval --tab-id N --expr 'document.title'
#   hermes-chrome.sh click --tab-id N --selector 'css'
#   hermes-chrome.sh type --tab-id N --selector 'css' --text '...'
#   hermes-chrome.sh status|stop|ping|list-tv|capture
#   hermes-chrome.sh check-url <url> [--no-follow]
#   hermes-chrome.sh download <url> [--out path] [--cookies] [--force] [--no-check] [--no-analyze]
#   hermes-chrome.sh analyze <path> [path...]
#   hermes-chrome.sh page-assets --tab-id N
#   hermes-chrome.sh check-tab-links --tab-id N
#   hermes-chrome.sh policy-show | token-setup
#   hermes-chrome.sh bridge-start|bridge-stop|bridge-status|bridge-restart
#   hermes-chrome.sh install-launchd|uninstall-launchd|install-help
#
# Global flags (before or after command):
#   --json / --json-only / -j   stdout = JSON only (quiet bridge messages)
#   --quiet / -q                suppress non-JSON chatter
#
# Env:
#   HERMES_CHROME_ROOT   — repo root (default: parent of scripts/)
#   HERMES_CHROME_BRIDGE_* or HERMES_TABGROUP_BRIDGE_*   — host/port for bridge
#   HERMES_CHROME_RUN    — runtime dir for pid/log (default ~/.hermes/run/hermes-chrome)
#   HERMES_CHROME_BRIDGE_TOKEN — optional shared token
#   HERMES_CHROME_DOWNLOAD_MAX_BYTES — default 50MiB
#   HERMES_CHROME_JSON=1 — same as --json-only
#   HERMES_CHROME_POLICY — path to policy.json (allow/deny hosts)
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
JSON_ONLY="${HERMES_CHROME_JSON:-0}"
QUIET=0
[[ "$JSON_ONLY" == "1" || "$JSON_ONLY" == "true" ]] && QUIET=1

mkdir -p "$RUN_DIR"

die() { echo "error: $*" >&2; exit 1; }
log() { [[ "${QUIET}" == "1" || "${JSON_ONLY}" == "1" ]] || echo "$@"; }
is_json() { [[ "${JSON_ONLY}" == "1" || "${JSON_ONLY}" == "true" ]]; }

bridge_healthy() {
  curl -fsS --max-time 1 "${BRIDGE_URL}/v1/health" >/dev/null 2>&1
}

cmd_bridge_status() {
  if bridge_healthy; then
    local health
    health="$(curl -fsS --max-time 2 "${BRIDGE_URL}/v1/health")"
    if is_json; then
      echo "$health"
      return 0
    fi
    echo "bridge: running"
    echo "$health"
    # Human-readable extension gate from health payload.
    python3 - <<'PY' "$health" 2>/dev/null || true
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
        "hint: Reload Hermes Chrome + click icon if last_seen is null/stale",
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
  # Launch detached without shell nohup wrapper restrictions when possible.
  python3 "$BRIDGE_PY" >>"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  local i
  for i in $(seq 1 30); do
    if bridge_healthy; then
      log "bridge started: ${BRIDGE_URL}"
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
  log "bridge stopped"
}

cmd_bridge_restart() {
  cmd_bridge_stop || true
  sleep 0.3
  cmd_bridge_start
}

# POST command and wait for extension result.
# Usage: send_cmd <action> [url]
#        send_cmd_json '{"action":"capture","prefer":"gc"}'
send_cmd_json() {
  local payload="$1"
  # Suppress bridge chatter for JSON consumers
  local _prev_quiet="$QUIET"
  QUIET=1
  cmd_bridge_start
  QUIET="$_prev_quiet"
  local resp id result
  local -a curl_hdrs=(-H 'Content-Type: application/json')
  if [[ -n "${HERMES_CHROME_BRIDGE_TOKEN:-}" ]]; then
    curl_hdrs+=(-H "X-Hermes-Chrome-Token: ${HERMES_CHROME_BRIDGE_TOKEN}")
  fi
  resp="$(curl -fsS --max-time 10 "${curl_hdrs[@]}" -d "$payload" "${BRIDGE_URL}/v1/command")"
  id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$resp")"
  # capture payloads can be large — longer wait
  if ! result="$(curl -fsS --max-time 90 "${curl_hdrs[@]}" "${BRIDGE_URL}/v1/result/${id}?timeout=75")"; then
    die "no response from extension (timeout). Load unpacked + click icon.
  ${EXT_DIR}
Bridge: ${BRIDGE_URL}"
  fi
  local indent_flag="2"
  is_json && indent_flag="None"
  python3 - <<'PY' "$result" "$indent_flag"
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
  log "mode: hermes-chrome (daily Chrome workspace)"
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

cmd_list_tabs() {
  local group_only=false url_includes="" title_includes="" limit=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --group|--workspace|-g) group_only=true; shift ;;
      --url) url_includes="${2:-}"; shift 2 ;;
      --title) title_includes="${2:-}"; shift 2 ;;
      --limit) limit="${2:-}"; shift 2 ;;
      *) die "usage: $0 list-tabs [--group] [--url needle] [--title needle] [--limit N]" ;;
    esac
  done
  local payload
  payload="$(python3 - <<'PY' "$group_only" "$url_includes" "$title_includes" "$limit"
import json, sys, uuid
group_only = sys.argv[1] == "true"
url_includes, title_includes, limit = sys.argv[2], sys.argv[3], sys.argv[4]
obj = {"id": str(uuid.uuid4()), "action": "list_tabs", "groupOnly": group_only}
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
  payload="$(python3 - <<'PY' "$url" "$tab_id"
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

cmd_eval() {
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
  payload="$(python3 -c 'import json,sys,uuid; print(json.dumps({"id":str(uuid.uuid4()),"action":"eval","tabId":int(sys.argv[1]),"expression":sys.argv[2]}))' "$tab_id" "$expr")"
  send_cmd_json "$payload"
}

cmd_click() {
  local tab_id="" selector=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tab-id|--tabId) tab_id="${2:-}"; shift 2 ;;
      --selector|-s) selector="${2:-}"; shift 2 ;;
      *) die "usage: $0 click --tab-id N --selector 'css'" ;;
    esac
  done
  [[ -n "$tab_id" && -n "$selector" ]] || die "usage: $0 click --tab-id N --selector 'css'"
  local payload
  payload="$(python3 -c 'import json,sys,uuid; print(json.dumps({"id":str(uuid.uuid4()),"action":"click","tabId":int(sys.argv[1]),"selector":sys.argv[2]}))' "$tab_id" "$selector")"
  send_cmd_json "$payload"
}

cmd_type() {
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
  payload="$(python3 -c 'import json,sys,uuid; print(json.dumps({"id":str(uuid.uuid4()),"action":"type","tabId":int(sys.argv[1]),"selector":sys.argv[2],"text":sys.argv[3],"clear":sys.argv[4]=="true"}))' "$tab_id" "$selector" "$text" "$clear")"
  send_cmd_json "$payload"
}

cmd_check_url() {
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
  python3 "${ROOT}/lib/check_url.py" $follow "$url"
}

cmd_analyze() {
  [[ $# -gt 0 ]] || die "usage: $0 analyze <path> [path...]"
  python3 "${ROOT}/lib/analyze_file.py" "$@"
}

cmd_download() {
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
  python3 "${ROOT}/lib/download_file.py" "${args[@]}"
}

cmd_page_assets() {
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
  payload="$(python3 - <<'PY' "$tab_id" "$limit"
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
  payload="$(python3 - <<'PY' "$tab_id" "$limit"
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
  python3 - <<'PY' "$ROOT" "$assets"
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
  python3 "${ROOT}/lib/policy.py" show
}

cmd_token_setup() {
  bash "${ROOT}/scripts/token-setup.sh" "$@"
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

1. Start bridge (optional; CLI auto-starts; launchd recommended):
     $0 bridge-start
     $0 install-launchd   # macOS: start at login + KeepAlive

2. Chrome → chrome://extensions
   - Enable "Developer mode"
   - "Load unpacked" → ${EXT_DIR}
     (or install from Chrome Web Store when published)
   - After upgrades: Reload + accept permissions + click icon

3. Pin the extension, click its icon once (starts long-poll).

4. Test:
     $0 bridge-status     # extension_connected should become true after icon click
     $0 ping              # require version >= 1.4.0 for fetch_url / page-assets
     $0 list-tabs --group
     $0 start https://example.com/
     $0 check-url https://example.com/
     $0 download https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf
     $0 analyze ~/.hermes/run/hermes-chrome/downloads/*
     $0 list-tv
     $0 capture --prefer gc --out /tmp/gc.png
     $0 status
     $0 stop

Notes:
- Default workspace: Chrome Tab Group titled "Hermes" (blue, configurable).
- capture uses chrome.tabs.captureVisibleTab (may briefly activate target tab).
- check-url / analyze / download (direct) are local Python — no cloud.
- download --cookies uses extension fetch_url (daily Chrome cookie jar).
- Not the same as Agent Chrome profile (~/.hermes/chrome-debug).
- Gold pipeline: TV_CAPTURE_BACKEND=hermes-chrome (default in gold-usd-report).
- Optional auth: export HERMES_CHROME_BRIDGE_TOKEN=...
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
  echo "commands: start|open|new-tab|navigate|list-tabs|eval|click|type|page-assets|check-tab-links|check-url|download|analyze|policy-show|token-setup|status|stop|ping|list-tv|capture|bridge-start|bridge-stop|bridge-status|bridge-restart|install-launchd|uninstall-launchd|install-help"
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
    start)          cmd_start "${1:-}" ;;
    open)           cmd_open "${1:-}" ;;
    new-tab)        cmd_new_tab "${1:-}" ;;
    navigate)       cmd_navigate "$@" ;;
    list-tabs|list_tabs) cmd_list_tabs "$@" ;;
    eval|evaluate)  cmd_eval "$@" ;;
    click)          cmd_click "$@" ;;
    type)           cmd_type "$@" ;;
    page-assets|page_assets|list-assets) cmd_page_assets "$@" ;;
    check-tab-links|check_tab_links) cmd_check_tab_links "$@" ;;
    check-url|check_url) cmd_check_url "$@" ;;
    download|dl)    cmd_download "$@" ;;
    analyze|check-file|check_file) cmd_analyze "$@" ;;
    policy-show|policy) cmd_policy_show "$@" ;;
    token-setup)    cmd_token_setup "$@" ;;
    status)         cmd_status ;;
    stop)           cmd_stop ;;
    ping)           cmd_ping ;;
    list-tv|list_tv) cmd_list_tv ;;
    capture)        cmd_capture "$@" ;;
    bridge-start)   cmd_bridge_start ;;
    bridge-stop)    cmd_bridge_stop ;;
    bridge-status)  cmd_bridge_status ;;
    bridge-restart) cmd_bridge_restart ;;
    install-launchd) cmd_install_launchd ;;
    uninstall-launchd) cmd_uninstall_launchd ;;
    install-help)   cmd_install_help ;;
    -h|--help|help|"") usage; [[ -n "$cmd" ]] || exit 1 ;;
    *) die "unknown command: $cmd" ;;
  esac
}

main "$@"
