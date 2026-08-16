#!/usr/bin/env bash
# Local equivalent of .github/workflows/ci.yml (no Chrome required).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python3 -m compileall -q \
  bridge.py mcp_server.py native_host lib tests scripts/doctor.py scripts/install-mcp.py

python3 -m unittest discover -s tests -v
echo "ci-check: ok"
