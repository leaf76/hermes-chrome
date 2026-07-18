#!/usr/bin/env bash
# One-shot local setup helper for Hermes Chrome.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXT="$ROOT/extension"
CLI="$ROOT/scripts/hermes-chrome.sh"

echo "== Hermes Chrome local setup =="
echo "repo: $ROOT"
echo "extension: $EXT"

# Prefer launchd on macOS so bridge survives reboot.
if [[ "$(uname -s)" == "Darwin" ]]; then
  if bash "$ROOT/scripts/install-launchd.sh" install; then
    echo "launchd: installed com.leaf76.hermes-chrome-bridge"
  else
    echo "launchd install failed; falling back to foreground-style bridge-start" >&2
    "$CLI" bridge-stop >/dev/null 2>&1 || true
    "$CLI" bridge-start
  fi
else
  "$CLI" bridge-stop >/dev/null 2>&1 || true
  "$CLI" bridge-start
fi
"$CLI" bridge-status || true

# Open extensions page in background (user may still need to Load unpacked once)
if [[ "$(uname -s)" == "Darwin" ]]; then
  open -g -a "Google Chrome" "chrome://extensions" 2>/dev/null || true
  open -g -R "$EXT" 2>/dev/null || true
fi

cat <<EOF

Next (if extension not loaded / not on v1.3.0 yet):
  1. Chrome → chrome://extensions → Developer mode ON
  2. Load unpacked (or Reload CWS build) →:
       $EXT
  3. Click the Hermes Chrome icon once (required after Reload)
  4. Run:  $CLI ping
           $CLI bridge-status   # extension_connected should be true

Privacy (CWS): https://leaf76.github.io/hermes-chrome/privacy-policy
EOF
