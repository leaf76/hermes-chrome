# hermes-chrome — agent notes

## Scope

Chrome companion for Hermes / local agents: operate the **user daily Chrome**
safely (workspace isolation, CLI bridge). Tab Groups are a feature, not the
product boundary.

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
# Preferred agent-ready path
./scripts/hermes-chrome.sh install-for-agent   # or Windows: scripts/install-windows.ps1
./scripts/hermes-chrome.sh bridge-status       # auth:true + extension_connected
./scripts/hermes-chrome.sh ping                # need extension v1.5.0+ reloaded
./scripts/hermes-chrome.sh list-tabs           # workspace only
# MCP smoke (stdio — usually spawned by Grok, not interactive)
python3 -c "import mcp_server; print(mcp_server.ensure_bridge())"
# unauth probe must fail:
curl -sS -o /dev/null -w '%{http_code}\n' -H 'Content-Type: application/json' \
  -d '{"action":"ping"}' http://127.0.0.1:19876/v1/command   # expect 401
./scripts/hermes-chrome.sh check-url https://example.com/
./scripts/hermes-chrome.sh download https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf
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
