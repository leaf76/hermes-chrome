#!/usr/bin/env bash
# One-shot: bridge autostart + Native Messaging host (+ optional MCP via install-mcp.py).
#
# Prefer the full product installer (fixed root + PATH + multi-client MCP):
#   curl -fsSL https://raw.githubusercontent.com/leaf76/hermes-chrome/main/scripts/install.sh | bash
#
# Usage (from any clone or ~/.hermes/hermes-chrome):
#   ./scripts/install-for-agent.sh
#   ./scripts/install-for-agent.sh --skip-mcp
#   ./scripts/install-for-agent.sh --uninstall
#
# Windows: prefer scripts/install-windows.ps1
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CLI="${SCRIPT_DIR}/hermes-chrome.sh"
MCP_PY="${ROOT}/mcp_server.py"
MCP_INSTALLER="${SCRIPT_DIR}/install-mcp.py"
RUN_DIR="${HERMES_CHROME_RUN:-$HOME/.hermes/run/hermes-chrome}"
SKIP_MCP=0
UNINSTALL=0

die() { echo "error: $*" >&2; exit 1; }
log() { echo "[hermes-chrome] $*"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-mcp|--skip-grok) SKIP_MCP=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help)
      sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) die "unknown arg: $1" ;;
  esac
done

# Windows Git Bash / MSYS → PowerShell installer
uname_s="$(uname -s 2>/dev/null || echo unknown)"
if [[ "$uname_s" == MINGW* || "$uname_s" == MSYS* || "$uname_s" == CYGWIN* ]]; then
  log "Windows detected — handing off to install-windows.ps1"
  ps_args=("-ExecutionPolicy" "Bypass" "-File" "${SCRIPT_DIR}/install-windows.ps1")
  [[ "$SKIP_MCP" == "1" ]] && ps_args+=("-SkipGrok")
  [[ "$UNINSTALL" == "1" ]] && ps_args+=("-Uninstall")
  exec powershell.exe "${ps_args[@]}"
fi

export HERMES_CHROME_ROOT="${HERMES_CHROME_ROOT:-$ROOT}"
export HERMES_CHROME_RUN="$RUN_DIR"

[[ -f "$MCP_PY" ]] || die "missing $MCP_PY"
[[ -f "$CLI" ]] || die "missing $CLI"
PYTHON3="$(command -v python3 || command -v python || true)"
[[ -n "$PYTHON3" ]] || die "python3 not found"
# Reject ancient python
"$PYTHON3" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' \
  || die "Python 3.9+ required"

if [[ "$UNINSTALL" == "1" ]]; then
  if [[ "$uname_s" == "Darwin" ]]; then
    bash "${SCRIPT_DIR}/install-launchd.sh" uninstall || true
  fi
  "$CLI" bridge-stop >/dev/null 2>&1 || true
  if [[ -f "${SCRIPT_DIR}/install-native-host.sh" ]]; then
    bash "${SCRIPT_DIR}/install-native-host.sh" uninstall || true
  fi
  if [[ -f "$MCP_INSTALLER" ]]; then
    "$PYTHON3" "$MCP_INSTALLER" --root "$ROOT" --uninstall || true
  fi
  log "bridge stopped / launchd / native host cleaned (if present)"
  log "MCP auto entries removed where install-mcp managed them"
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

# --- Multi-client MCP ---
if [[ "$SKIP_MCP" != "1" && -f "$MCP_INSTALLER" ]]; then
  log "registering MCP for Grok / Cursor / Claude Desktop (when present)…"
  "$PYTHON3" "$MCP_INSTALLER" --root "$ROOT" --python "$PYTHON3" || log "MCP register partial"
elif [[ "$SKIP_MCP" == "1" ]]; then
  log "skip MCP registration (--skip-mcp)"
fi

# Optional PATH shim when not using install.sh
BIN_DIR="${HERMES_CHROME_BIN:-$HOME/.local/bin}"
if [[ -w "$(dirname "$BIN_DIR")" || -d "$BIN_DIR" ]]; then
  mkdir -p "$BIN_DIR" 2>/dev/null || true
  if [[ -d "$BIN_DIR" ]]; then
    ln -sfn "$CLI" "${BIN_DIR}/hermes-chrome" 2>/dev/null \
      && log "PATH shim: ${BIN_DIR}/hermes-chrome" || true
  fi
fi

cat <<EOF

=== Companion (machine half) installed ===
Root:     ${ROOT}
Runtime:  ${RUN_DIR}
CLI:      ${CLI}
MCP:      ${MCP_PY}

This was step 1 of 2. Extension alone is never enough.
Not on npm — companion is this tree (or ~/.hermes/hermes-chrome via install.sh).

=== Next: browser half (extension v1.7+) ===
1. Install/enable Hermes Chrome
   - Chrome Web Store, or Load unpacked: ${ROOT}/extension
   - Accept nativeMessaging if prompted
2. Reload extension → click icon once
3. Wait for auto-pair (or popup → Pair)
   - Ready: popup says Connected
4. Restart MCP agents (Grok / Cursor / Claude Desktop) if you use tools
   CLI users: no MCP needed

Smoke:
  ${CLI} --json bridge-status
  ${CLI} --json ping
  ${PYTHON3} ${SCRIPT_DIR}/doctor.py

MCP snippet (any client):
  ${RUN_DIR}/mcp-snippet.json

Full one-liner install (fixed root):
  curl -fsSL https://raw.githubusercontent.com/leaf76/hermes-chrome/main/scripts/install.sh | bash

Docs: ${ROOT}/docs/GUIDE.md · popup → Guide
EOF
