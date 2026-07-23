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
./scripts/hermes-chrome.sh install-launchd   # macOS preferred (token + KeepAlive)
./scripts/hermes-chrome.sh pair-open         # then extension popup → Pair
./scripts/hermes-chrome.sh bridge-status     # auth:true + extension_connected
./scripts/hermes-chrome.sh ping              # need extension v1.5.0+ reloaded
./scripts/hermes-chrome.sh list-tabs         # workspace only
# unauth probe must fail:
curl -sS -o /dev/null -w '%{http_code}\n' -H 'Content-Type: application/json' \
  -d '{"action":"ping"}' http://127.0.0.1:19876/v1/command   # expect 401
./scripts/hermes-chrome.sh check-url https://example.com/
./scripts/hermes-chrome.sh download https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf
./store/package.sh
```

## Paths

- Repo: this directory (also known historically as hermes-agent-tabgroup)
- CLI: `scripts/hermes-chrome.sh`
- Hermes wrappers: `~/.hermes/scripts/hermes-chrome.sh`
- Runtime: `~/.hermes/run/hermes-chrome/` (includes `bridge.env` token — chmod 600)
