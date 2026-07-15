#!/usr/bin/env bash
# Daily Chrome — agent "window group" (no focus steal)
#
# When Hermes MUST use the user's everyday Chrome profile (cookies/SSO),
# do NOT hijack the active tab. Instead:
#   1. Open a NEW window (our pseudo tab-group)
#   2. Name it via Chrome AppleScript `given name` = "Hermes Agent …"
#   3. Restore the previously frontmost app (no lasting focus steal)
#   4. Only navigate/close tabs inside that window
#
# Native Chrome Tab Groups:
#   - `chrome.tabGroups` is an **extension-only** API.
#   - Google Chrome's AppleScript dictionary has NO tab-group classes
#     (verified via sdef: window/tab only). Page JS also cannot call it.
#   - So we use a dedicated named window as the practical group substitute.
#   - A real Tab Group would require a small Chrome extension + native messaging.
#
# Usage:
#   daily-chrome-agent-window.sh start [url]
#   daily-chrome-agent-window.sh status
#   daily-chrome-agent-window.sh open <url>          # active tab in agent window
#   daily-chrome-agent-window.sh new-tab <url>       # extra tab in agent window
#   daily-chrome-agent-window.sh urls                # list tabs in agent window
#   daily-chrome-agent-window.sh stop                # close agent window only
#
# State: ~/.hermes/run/daily-chrome-agent/state.env (runtime, not in git)
# Never touches Agent Chrome profile (~/.hermes/chrome-debug).
#
set -euo pipefail

STATE_DIR="${HERMES_DAILY_CHROME_STATE:-$HOME/.hermes/run/daily-chrome-agent}"
STATE_FILE="${STATE_DIR}/state.env"
MARKER_PREFIX="hermes-agent-window"
DEFAULT_URL="about:blank"

mkdir -p "$STATE_DIR"

die() { echo "error: $*" >&2; exit 1; }

require_macos_chrome() {
  [[ "$(uname -s)" == "Darwin" ]] || die "daily Chrome agent window is macOS-only"
  osascript -e 'id of application "Google Chrome"' >/dev/null 2>&1 \
    || die "Google Chrome is not available / not running well under AppleScript"
}

# Write KEY=VALUE into state file (simple replace-or-append).
state_set() {
  local key="$1" val="$2"
  mkdir -p "$STATE_DIR"
  touch "$STATE_FILE"
  if grep -q "^${key}=" "$STATE_FILE" 2>/dev/null; then
    # portable-ish in-place via temp
    local tmp
    tmp="$(mktemp)"
    awk -v k="$key" -v v="$val" 'BEGIN{FS=OFS="="} $1==k{$0=k"="v} {print}' "$STATE_FILE" >"$tmp"
    mv "$tmp" "$STATE_FILE"
  else
    printf '%s=%s\n' "$key" "$val" >>"$STATE_FILE"
  fi
}

state_get() {
  local key="$1"
  [[ -f "$STATE_FILE" ]] || return 1
  # shellcheck disable=SC1090
  grep -E "^${key}=" "$STATE_FILE" 2>/dev/null | tail -1 | cut -d= -f2-
}

chrome_running() {
  osascript -e 'application "Google Chrome" is running' 2>/dev/null | grep -qi true
}

# Create a new Chrome window without leaving Chrome frontmost.
# Returns window id on stdout.
# Labels the window with AppleScript `given name` (Window → Name Window…)
# so it is human-visible as the agent "group" substitute.
create_agent_window() {
  local url="${1:-$DEFAULT_URL}"
  local marker="${2:-}"
  local given_name="Hermes Agent"
  # Append marker as query/hash when possible so we can re-find the window.
  if [[ -n "$marker" ]]; then
    given_name="Hermes Agent ${marker}"
    if [[ "$url" == "about:blank" ]]; then
      # about:blank can't carry a reliable marker; use a harmless public page.
      url="https://example.com/?${MARKER_PREFIX}=${marker}"
    elif [[ "$url" == *\?* ]]; then
      url="${url}&${MARKER_PREFIX}=${marker}"
    else
      url="${url}?${MARKER_PREFIX}=${marker}"
    fi
  fi

  # Escape for AppleScript string
  local url_as name_as
  url_as="$(printf '%s' "$url" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  name_as="$(printf '%s' "$given_name" | sed 's/\\/\\\\/g; s/"/\\"/g')"

  osascript <<APPLESCRIPT
set targetURL to "${url_as}"
set agentName to "${name_as}"
-- Remember who was frontmost so we can restore focus (anti focus-steal).
tell application "System Events"
  set frontApp to name of first application process whose frontmost is true
end tell

tell application "Google Chrome"
  if not (application "Google Chrome" is running) then launch
  set w to make new window
  set URL of active tab of w to targetURL
  -- Visible label (Window menu / some window lists); not a native Tab Group.
  try
    set given name of w to agentName
  end try
  set wid to id of w
end tell

-- Restore previous front app (best-effort; may fail for some apps).
try
  tell application frontApp to activate
end try

return wid as text
APPLESCRIPT
}

window_exists() {
  local wid="$1"
  osascript -e "tell application \"Google Chrome\" to (exists (first window whose id is ${wid}))" 2>/dev/null | grep -qi true
}

# Navigate active tab of agent window (no activate).
set_active_url() {
  local wid="$1" url="$2"
  local url_as
  url_as="$(printf '%s' "$url" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  osascript <<APPLESCRIPT
tell application "Google Chrome"
  if not (exists (first window whose id is ${wid})) then error "agent window ${wid} not found"
  set URL of active tab of (first window whose id is ${wid}) to "${url_as}"
end tell
APPLESCRIPT
}

add_tab() {
  local wid="$1" url="$2"
  local url_as
  url_as="$(printf '%s' "$url" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  osascript <<APPLESCRIPT
tell application "Google Chrome"
  if not (exists (first window whose id is ${wid})) then error "agent window ${wid} not found"
  tell (first window whose id is ${wid})
    set t to make new tab with properties {URL:"${url_as}"}
  end tell
end tell
APPLESCRIPT
}

list_urls() {
  local wid="$1"
  osascript <<APPLESCRIPT
tell application "Google Chrome"
  if not (exists (first window whose id is ${wid})) then error "agent window ${wid} not found"
  set out to {}
  tell (first window whose id is ${wid})
    repeat with t in tabs
      set end of out to (URL of t)
    end repeat
  end tell
  set AppleScript's text item delimiters to linefeed
  return out as text
end tell
APPLESCRIPT
}

close_window() {
  local wid="$1"
  osascript <<APPLESCRIPT
tell application "Google Chrome"
  if exists (first window whose id is ${wid}) then
    close (first window whose id is ${wid})
  end if
end tell
APPLESCRIPT
}

cmd_start() {
  require_macos_chrome
  local url="${1:-$DEFAULT_URL}"
  local marker existing

  existing="$(state_get WINDOW_ID 2>/dev/null || true)"
  if [[ -n "${existing}" ]] && window_exists "$existing"; then
    echo "status: already running"
    echo "window_id: ${existing}"
    echo "mode: daily-chrome-agent-window"
    state_get MARKER 2>/dev/null | sed 's/^/marker: /' || true
    echo "tip: use open/new-tab/urls/stop — never activates daily Chrome by default"
    return 0
  fi

  marker="$(uuidgen | tr '[:upper:]' '[:lower:]' | tr -d '-')"
  marker="${marker:0:12}"

  # Ensure Chrome is at least launched (may briefly appear once if cold start).
  if ! chrome_running; then
    open -g -a "Google Chrome" --args --no-startup-window >/dev/null 2>&1 || open -g -a "Google Chrome" >/dev/null 2>&1 || true
    sleep 1.2
  fi

  local wid
  wid="$(create_agent_window "$url" "$marker")"
  [[ -n "$wid" ]] || die "failed to create Chrome window"

  : >"$STATE_FILE"
  state_set WINDOW_ID "$wid"
  state_set MARKER "$marker"
  state_set CREATED_AT "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  state_set START_URL "$url"

  echo "status: started"
  echo "window_id: ${wid}"
  echo "marker: ${marker}"
  echo "mode: daily-chrome-agent-window (new window, focus restored)"
  echo "policy: do not activate / do not touch other Chrome windows"
  echo "next: $0 open <url>  |  $0 new-tab <url>  |  $0 stop"
}

cmd_status() {
  require_macos_chrome
  local wid gname
  wid="$(state_get WINDOW_ID 2>/dev/null || true)"
  if [[ -z "$wid" ]]; then
    echo "status: stopped"
    echo "mode: daily-chrome-agent-window"
    return 1
  fi
  if window_exists "$wid"; then
    echo "status: running"
    echo "window_id: ${wid}"
    state_get MARKER 2>/dev/null | sed 's/^/marker: /' || true
    gname="$(osascript -e "tell application \"Google Chrome\" to get given name of (first window whose id is ${wid})" 2>/dev/null || true)"
    [[ -n "$gname" ]] && echo "given_name: ${gname}"
    echo "tabs:"
    list_urls "$wid" | sed 's/^/  - /' || true
    return 0
  fi
  echo "status: stale (window_id=${wid} gone)"
  return 1
}

cmd_open() {
  require_macos_chrome
  local url="${1:-}"; [[ -n "$url" ]] || die "usage: $0 open <url>"
  local wid
  wid="$(state_get WINDOW_ID 2>/dev/null || true)"
  [[ -n "$wid" ]] || die "no agent window — run: $0 start"
  window_exists "$wid" || die "agent window ${wid} missing — run: $0 start"
  set_active_url "$wid" "$url"
  echo "ok: navigated agent window ${wid}"
  echo "url: ${url}"
}

cmd_new_tab() {
  require_macos_chrome
  local url="${1:-}"; [[ -n "$url" ]] || die "usage: $0 new-tab <url>"
  local wid
  wid="$(state_get WINDOW_ID 2>/dev/null || true)"
  [[ -n "$wid" ]] || die "no agent window — run: $0 start"
  window_exists "$wid" || die "agent window ${wid} missing — run: $0 start"
  add_tab "$wid" "$url"
  echo "ok: new tab in agent window ${wid}"
  echo "url: ${url}"
}

cmd_urls() {
  require_macos_chrome
  local wid
  wid="$(state_get WINDOW_ID 2>/dev/null || true)"
  [[ -n "$wid" ]] || die "no agent window — run: $0 start"
  window_exists "$wid" || die "agent window ${wid} missing"
  list_urls "$wid"
}

cmd_stop() {
  require_macos_chrome
  local wid
  wid="$(state_get WINDOW_ID 2>/dev/null || true)"
  if [[ -z "$wid" ]]; then
    echo "status: already stopped"
    return 0
  fi
  if window_exists "$wid"; then
    close_window "$wid"
    echo "closed window_id: ${wid}"
  else
    echo "window already gone: ${wid}"
  fi
  rm -f "$STATE_FILE"
  echo "status: stopped"
}

usage() {
  sed -n '2,35p' "$0" | sed 's/^# \{0,1\}//'
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    start)   cmd_start "${1:-}" ;;
    status)  cmd_status ;;
    open)    cmd_open "${1:-}" ;;
    new-tab) cmd_new_tab "${1:-}" ;;
    urls)    cmd_urls ;;
    stop)    cmd_stop ;;
    -h|--help|help|"") usage; [[ -n "$cmd" ]] || exit 1 ;;
    *) die "unknown command: $cmd (start|status|open|new-tab|urls|stop)" ;;
  esac
}

main "$@"
