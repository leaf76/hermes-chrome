#!/usr/bin/env bash
# Backward-compatible alias → hermes-chrome.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/hermes-chrome.sh" "$@"
