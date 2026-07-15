#!/usr/bin/env bash
# One-shot local setup helper for Hermes Chrome.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXT="$ROOT/extension"
CLI="$ROOT/scripts/hermes-chrome.sh"

echo "== Hermes Chrome local setup =="
echo "repo: $ROOT"
echo "extension: $EXT"

"$CLI" bridge-stop >/dev/null 2>&1 || true
"$CLI" bridge-start
"$CLI" bridge-status

# Open extensions page in background (user may still need to Load unpacked once)
if [[ "$(uname -s)" == "Darwin" ]]; then
  open -g -a "Google Chrome" "chrome://extensions" 2>/dev/null || true
  open -g -R "$EXT" 2>/dev/null || true
fi

cat <<EOF

Next (if extension not loaded yet):
  1. Chrome → chrome://extensions → Developer mode ON
  2. Load unpacked → select:
       $EXT
  3. Click the Hermes Chrome icon once
  4. Run:  $CLI ping

Privacy (CWS): https://leaf76.github.io/hermes-chrome/privacy-policy
EOF
