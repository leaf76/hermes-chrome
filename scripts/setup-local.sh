#!/usr/bin/env bash
# One-shot local setup helper for Hermes Chrome.
# Prefer install-for-agent (bridge autostart + Grok MCP). This script remains
# as a thin wrapper for extension-first local dev.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXT="$ROOT/extension"
CLI="$ROOT/scripts/hermes-chrome.sh"

echo "== Hermes Chrome local setup =="
echo "repo: $ROOT"
echo "extension: $EXT"

# Agent-ready path (bridge KeepAlive + optional Grok MCP).
if [[ -f "$ROOT/scripts/install-for-agent.sh" ]]; then
  bash "$ROOT/scripts/install-for-agent.sh" || {
    echo "install-for-agent failed; falling back to bridge-start only" >&2
    "$CLI" bridge-stop >/dev/null 2>&1 || true
    "$CLI" bridge-start
    "$CLI" pair-open || true
  }
else
  if [[ "$(uname -s)" == "Darwin" ]]; then
    bash "$ROOT/scripts/install-launchd.sh" install || "$CLI" bridge-start
  else
    "$CLI" bridge-start
  fi
  "$CLI" pair-open || true
fi
"$CLI" bridge-status || true

# Open extensions page in background (user may still need to Load unpacked once)
if [[ "$(uname -s)" == "Darwin" ]]; then
  open -g -a "Google Chrome" "chrome://extensions" 2>/dev/null || true
  open -g -R "$EXT" 2>/dev/null || true
fi

cat <<EOF

Next (extension v1.5.0+ required for auth):
  1. Chrome → chrome://extensions → Developer mode ON
  2. Load unpacked (or Reload) →:
       $EXT
     (CWS install is fine if you already have Hermes Chrome)
  3. Click the Hermes Chrome icon once (starts long-poll / auto-pair)
  4. Popup → Pair if still disconnected
  5. Run:  $CLI --json bridge-status   # extension_connected:true
           $CLI --json ping
  6. Restart Grok / agent so hermes_chrome_* MCP tools load

Privacy (CWS): https://leaf76.github.io/hermes-chrome/privacy-policy
Security: bridge token is ON by default; treat bridge.env as a secret.
EOF
