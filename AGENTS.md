# hermes-chrome — agent notes

## Scope

Chrome companion for Hermes / local agents: operate the **user daily Chrome**
safely (workspace isolation, CLI bridge). Tab Groups are a feature, not the
product boundary.

**Two halves:** machine **companion** (bridge + native host + token) + browser
**extension**. Never claim CWS-only install is enough. UX copy: companion first;
popup **Setup needed** when companion/bridge is missing.

**Canonical install:**
- One-liner: `curl -fsSL …/scripts/install.sh | bash` → `~/.hermes/hermes-chrome`
- Runtime: `~/.hermes/run/hermes-chrome` · CLI shim: `~/.local/bin/hermes-chrome`
- MCP: `scripts/install-mcp.py` (Grok + Cursor + Claude Desktop); not npm
- Doctor: `hermes-chrome doctor` / `scripts/doctor.py`
- Not on npm/PyPI.

## Rules

- Prefer agent-friendly Chrome ops that do **not** hijack the user's active tab.
- Bridge is **localhost-only** with **auth on by default** (v1.5+). Do not ship
  with `HERMES_CHROME_BRIDGE_ALLOW_NO_AUTH=1` as the product default.
- Manifest still needs `<all_urls>` for capture/scripting on normal pages; bridge
  host_permissions stay on `127.0.0.1` / `localhost:19876`.
- Do not commit runtime pid/log, `bridge.env`, or browser profiles.
- Bump `extension/manifest.json` version for CWS updates; run `store/package.sh`.
- User-facing plans: Traditional Chinese. Code / store EN / UI strings: English.
- Sensitive defaults: workspace-only tabs, private-host block, token required.

## Validate

```bash
# Preferred product path (fixed root)
./scripts/install.sh --dev          # or: curl …/install.sh | bash
./scripts/hermes-chrome.sh doctor
./scripts/hermes-chrome.sh bridge-status       # auth:true + extension_connected
./scripts/hermes-chrome.sh ping
./scripts/hermes-chrome.sh list-tabs           # workspace only
python3 scripts/install-mcp.py --status
# MCP smoke (stdio — usually spawned by agent, not interactive)
python3 -c "import mcp_server; print(mcp_server.ensure_bridge())"
# unauth probe must fail:
curl -sS -o /dev/null -w '%{http_code}\n' -H 'Content-Type: application/json' \
  -d '{"action":"ping"}' http://127.0.0.1:19876/v1/command   # expect 401
./store/package.sh
```

## Agent integration rules

- CWS extension alone **cannot** listen on `:19876`. Ship **Native Messaging host + bridge**.
- Prefer `install-for-agent` / `install-windows.ps1` (includes native host registration).
- Host name is fixed: `com.leaf76.hermes_chrome`. CWS extension id:
  `mkoaoadlkijccmmbkioagnlngbbeocfa` (manifest `key` pins unpacked id).
- `mcp_server.py` + HTTP bridge are **agent-agnostic** — not Grok-only.
- `mcp_server.py` / `native_host/host.py` / `lib/bridge_runtime.py` stay **stdlib-only**.
- Do not claim “install CWS only”; honest UX is “companion once + CWS”.
- Same-host only: do not invent remote bridge tunneling without explicit scope.

## Paths

- Repo: this directory (also known historically as hermes-agent-tabgroup)
- CLI: `scripts/hermes-chrome.sh`
- Native host: `native_host/host.py` + `scripts/install-native-host.*`
- MCP: `mcp_server.py`
- Shared runtime: `lib/bridge_runtime.py`
- Hermes wrappers: `~/.hermes/scripts/hermes-chrome.sh`
- Runtime: `~/.hermes/run/hermes-chrome/` (includes `bridge.env` token — chmod 600)
