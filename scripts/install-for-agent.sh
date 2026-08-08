#!/usr/bin/env bash
# One-shot: bridge autostart + MCP registration for Grok / Cursor-style agents.
#
# Usage:
#   ./scripts/install-for-agent.sh              # bridge + try Grok MCP
#   ./scripts/install-for-agent.sh --skip-grok
#   ./scripts/install-for-agent.sh --uninstall
#
# Windows: prefer scripts/install-windows.ps1 (this script will redirect if uname is MINGW/MSYS).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CLI="${SCRIPT_DIR}/hermes-chrome.sh"
MCP_PY="${ROOT}/mcp_server.py"
RUN_DIR="${HERMES_CHROME_RUN:-$HOME/.hermes/run/hermes-chrome}"
GROK_CONFIG="${GROK_CONFIG:-$HOME/.grok/config.toml}"
SKIP_GROK=0
UNINSTALL=0

die() { echo "error: $*" >&2; exit 1; }
log() { echo "[hermes-chrome] $*"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-grok) SKIP_GROK=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) die "unknown arg: $1" ;;
  esac
done

# Windows Git Bash / MSYS → PowerShell installer
uname_s="$(uname -s 2>/dev/null || echo unknown)"
if [[ "$uname_s" == MINGW* || "$uname_s" == MSYS* || "$uname_s" == CYGWIN* ]]; then
  log "Windows detected — handing off to install-windows.ps1"
  ps_args=@("-ExecutionPolicy", "Bypass", "-File", "${SCRIPT_DIR}/install-windows.ps1")
  [[ "$SKIP_GROK" == "1" ]] && ps_args+=("-SkipGrok")
  [[ "$UNINSTALL" == "1" ]] && ps_args+=("-Uninstall")
  exec powershell.exe "${ps_args[@]}"
fi

[[ -f "$MCP_PY" ]] || die "missing $MCP_PY"
[[ -f "$CLI" ]] || die "missing $CLI"
PYTHON3="$(command -v python3 || command -v python || true)"
[[ -n "$PYTHON3" ]] || die "python3 not found"

if [[ "$UNINSTALL" == "1" ]]; then
  if [[ "$uname_s" == "Darwin" ]]; then
    bash "${SCRIPT_DIR}/install-launchd.sh" uninstall || true
  fi
  "$CLI" bridge-stop >/dev/null 2>&1 || true
  log "bridge stopped / launchd uninstalled (if present)"
  log "Grok MCP entry left in place; remove [mcp_servers.hermes-chrome] manually if desired"
  exit 0
fi

# --- Bridge autostart ---
if [[ "$uname_s" == "Darwin" ]]; then
  log "installing launchd KeepAlive bridge…"
  bash "${SCRIPT_DIR}/install-launchd.sh" install
else
  log "non-macOS: starting bridge now (add your own systemd unit for reboot persistence)"
  "$CLI" bridge-start
fi

# --- Native Messaging host (extension auto-starts bridge) ---
if [[ -f "${SCRIPT_DIR}/install-native-host.sh" ]]; then
  log "installing Chrome Native Messaging host…"
  bash "${SCRIPT_DIR}/install-native-host.sh" install || log "native host install failed (non-fatal)"
fi

"$CLI" pair-open || true
"$CLI" bridge-status || true

# --- Grok MCP ---
install_grok_mcp() {
  local block conf dir
  conf="$GROK_CONFIG"
  dir="$(dirname "$conf")"
  if [[ ! -d "$dir" ]]; then
    log "no ~/.grok — skip Grok MCP (install Grok Build, re-run this script)"
    return 0
  fi
  mkdir -p "$dir"
  block=$(
    cat <<EOF

# --- hermes-chrome (auto by scripts/install-for-agent.sh) ---
[mcp_servers.hermes-chrome]
command = "${PYTHON3}"
args = ["${MCP_PY}"]
enabled = true
startup_timeout_sec = 45
tool_timeout_sec = 120

[mcp_servers.hermes-chrome.env]
HERMES_CHROME_ROOT = "${ROOT}"
# --- end hermes-chrome ---
EOF
  )
  if [[ -f "$conf" ]] && grep -q '\[mcp_servers\.hermes-chrome\]' "$conf"; then
    if grep -q 'auto by scripts/install-for-agent.sh' "$conf" || grep -q 'auto by scripts/install-windows.ps1' "$conf"; then
      # strip previous auto block
      python3 - <<'PY' "$conf"
from pathlib import Path
import re, sys
path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
text2 = re.sub(
    r"\n# --- hermes-chrome \(auto by scripts/install-(?:for-agent\.sh|windows\.ps1)\) ---.*?# --- end hermes-chrome ---\n?",
    "\n",
    text,
    flags=re.S,
)
path.write_text(text2.rstrip() + "\n", encoding="utf-8")
PY
    else:
      log "Grok config already has [mcp_servers.hermes-chrome] — not overwriting custom entry"
      return 0
    fi
  fi
  if [[ ! -f "$conf" ]]; then
    printf '%s\n' "$block" | sed '1d' >"$conf"
  else
    printf '%s\n' "$(cat "$conf")" "$block" >"${conf}.tmp"
    mv "${conf}.tmp" "$conf"
  fi
  log "Grok MCP registered in $conf"
  log "Restart Grok Build session to load hermes-chrome tools"
}

if [[ "$SKIP_GROK" != "1" ]]; then
  install_grok_mcp
fi

cat <<EOF

=== Companion (machine half) installed ===
This was step 1 of 2. Extension alone is never enough.

=== Next: browser half (extension v1.7.1+) ===
1. Install/enable Hermes Chrome (CWS or Load unpacked: ${ROOT}/extension)
   - Accept nativeMessaging if prompted
2. Reload extension → click icon once
   - Native host should auto-start the bridge on :19876
3. Wait for auto-pair (or popup → Pair)
   - Ready: Bridge online + Auth ready
   - If popup shows "Setup required", companion/host is still missing — re-run this script
4. Optional: Options → allow ops outside Hermes workspace (tabs not in the group)
5. Restart MCP agents (Grok / Cursor / Claude Desktop) for hermes_chrome_* tools
   CLI users: no MCP needed — use ${CLI}

Smoke:
  ${CLI} --json bridge-status
  ${CLI} --json ping
  ${CLI} capture --prefer active --out /tmp/hermes-chrome-smoke.png

MCP entrypoint (any MCP client — not Grok-only):
  ${PYTHON3} ${MCP_PY}

Docs: ${ROOT}/docs/GUIDE.md · popup → Guide
EOF
