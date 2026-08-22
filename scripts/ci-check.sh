#!/usr/bin/env bash
# Local equivalent of .github/workflows/ci.yml (no Chrome required).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python3 -m compileall -q \
  bridge.py mcp_server.py native_host lib tests scripts/doctor.py scripts/install-mcp.py

python3 -m unittest discover -s tests -v

if command -v zip >/dev/null 2>&1; then
  chmod +x store/package.sh
  bash store/package.sh
else
  echo "ci-check: skip store/package.sh (zip not installed)"
fi

echo "ci-check: ok"
