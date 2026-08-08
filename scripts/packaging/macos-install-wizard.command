#!/bin/bash
# Double-clickable macOS install wizard (osascript UI) for Hermes Chrome companion.
# Runs install.sh from this repo (dev) or downloads from GitHub.
set -euo pipefail

export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
INSTALL_SH_REMOTE="https://raw.githubusercontent.com/leaf76/hermes-chrome/main/scripts/install.sh"
CWS_URL="https://chromewebstore.google.com/detail/hermes-chrome/mkoaoadlkijccmmbkioagnlngbbeocfa"

# Open a Terminal-friendly log window context when double-clicked
echo "=== Hermes Chrome companion install wizard ==="

BTN="$(osascript -e 'button returned of (display dialog "Hermes Chrome companion installer

Installs the local machine half (bridge + Native Messaging).
You still need the Chrome extension from the Web Store.

Requirements: Python 3.9+ and git.

Continue?" buttons {"Cancel", "Install"} default button "Install" with title "Hermes Chrome")' 2>/dev/null || echo Cancel)"

if [[ "$BTN" != "Install" ]]; then
  echo "Cancelled."
  exit 0
fi

if [[ -f "${REPO_ROOT}/scripts/install.sh" && -f "${REPO_ROOT}/bridge.py" ]]; then
  echo "Installing from local clone: $REPO_ROOT"
  osascript -e 'display notification "Installing from local clone…" with title "Hermes Chrome"' 2>/dev/null || true
  if ! bash "${REPO_ROOT}/scripts/install.sh" --dev; then
    osascript -e 'display dialog "Install failed. Ensure Python 3.9+ is on PATH." buttons {"OK"} with icon stop with title "Hermes Chrome"' 2>/dev/null || true
    exit 1
  fi
else
  echo "Downloading install.sh from GitHub…"
  osascript -e 'display notification "Downloading install.sh from GitHub…" with title "Hermes Chrome"' 2>/dev/null || true
  if ! curl -fsSL "$INSTALL_SH_REMOTE" | bash; then
    osascript -e 'display dialog "Install failed. Check network / Python 3 / git." buttons {"OK"} with icon stop with title "Hermes Chrome"' 2>/dev/null || true
    exit 1
  fi
fi

if command -v hermes-chrome >/dev/null 2>&1; then
  hermes-chrome doctor || true
elif [[ -x "${HOME}/.local/bin/hermes-chrome" ]]; then
  "${HOME}/.local/bin/hermes-chrome" doctor || true
fi

DONE_BTN="$(osascript -e 'button returned of (display dialog "Companion install finished.

Next:
1. Install / open Hermes Chrome extension
2. Click the icon → Connected (Pair if asked)
3. Optional: restart Grok / Cursor / Claude for MCP

Open Chrome Web Store?" buttons {"Done", "Open Store"} default button "Open Store" with title "Hermes Chrome")' 2>/dev/null || echo Done)"

if [[ "$DONE_BTN" == "Open Store" ]]; then
  open "$CWS_URL" 2>/dev/null || true
fi

echo "Done."
exit 0
