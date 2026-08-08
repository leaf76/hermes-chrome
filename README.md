# hermes-chrome

**Repo:** https://github.com/leaf76/hermes-chrome  

Local agent companion that makes **Chrome easy for any local AI agent to operate**—open
**any website**, capture, list tabs, light DOM—without hijacking the tab you are using.

**Not locked to one site or product.** GitHub, docs, dashboards, news, charts… if
Chrome can open the URL, Hermes Chrome can drive that tab.

Tab Groups are one isolation tool, not the whole product.

## You need two parts (read this first)

**Installing the Chrome extension alone is not enough.** Chrome security does not
let an extension open a control port. Hermes Chrome is always:

| Half | What | Where |
|------|------|--------|
| **Companion** (machine half) | Local bridge on `127.0.0.1:19876` + Native Messaging host + token | This repo: `install-for-agent` / Windows installer |
| **Extension** (browser half) | Tab workspace, capture, DOM ops; talks to the bridge | Chrome Web Store or Load unpacked → `extension/` |

**Ready** means: Bridge **online** + Auth **ready** (popup), and CLI/MCP can `ping`.

```bash
# 1) Companion once per machine (macOS / Linux)
./scripts/hermes-chrome.sh install-for-agent
# Windows: powershell -ExecutionPolicy Bypass -File .\scripts\install-windows.ps1

# 2) Install / reload extension (v1.7+) → click icon once → Pair if needed

# 3) Smoke
./scripts/hermes-chrome.sh --json bridge-status   # extension_connected:true
./scripts/hermes-chrome.sh --json ping
```

Full walkthrough: [docs/GUIDE.md](docs/GUIDE.md) · [繁中 docs/GUIDE.zh-TW.md](docs/GUIDE.zh-TW.md) ·
extension popup → **Guide** (offline help).

**Privacy policy (Chrome Web Store):**  
https://leaf76.github.io/hermes-chrome/privacy-policy  

(also in-repo: `docs/privacy-policy.md` / `store/privacy-policy.md`)

## What it is for

| Goal | How |
|------|-----|
| Drive **any page** from CLI / agents | Local bridge + Chrome extension |
| Keep your active browsing | Agent work goes to a dedicated workspace (Tab Group by default) |
| Reuse your real cookies / SSO | Runs on **daily Chrome**, not a headless-only sandbox |
| Reduce focus steal | New tabs default to `active: false`; no AppleScript `activate` |
| Multi-agent (not one vendor) | HTTP bridge + optional MCP (`mcp_server.py`) |

## Features (today)

1. **Agent workspace** — native Chrome Tab Group (`Hermes` / configurable title)
2. **CLI** — `start` / `open` / `new-tab` / `navigate` / `list-tabs` / `status` / `stop` / `ping`
3. **Light DOM ops** — `eval` / `click` / `type` / `page-assets` (tabId)
4. **Capture** — any tab PNG via `captureVisibleTab` (default = workspace; optional finders available)
5. **URL check** — local `check-url` (scheme / redirect / heuristics; no cloud)
6. **Download + analyze** — `download` (optional `--cookies`) → `analyze` images/zip/tar (zip-bomb & path safety heuristics)
7. **Policy** — optional host allow/deny list (`~/.hermes/run/hermes-chrome/policy.json`)
8. **Agent JSON** — `--json` / `--json-only` (no bridge chatter on stdout)
9. **Local bridge** — `127.0.0.1:19876` queue + **extension last-seen** on `/v1/health`
10. **launchd (macOS)** — `install-launchd` for login + KeepAlive bridge; **token by default**
11. **Windows Scheduled Task** — `install-windows.ps1` keeps bridge on login
12. **Native Messaging host** — extension auto-starts bridge (`com.leaf76.hermes_chrome`)
13. **Stdio MCP** — `mcp_server.py` for any MCP client (`install-for-agent`)
14. **Auth + pairing** — shared token (`bridge.env`); extension Pair / Options; CORS locked to extension origins
15. **Fallback** — named window helper if you cannot load the extension yet

## Planned / non-goals

- **Planned:** optional allowlists, opt-in external threat intel, richer agent policies
- **Non-goal:** replace headless browser tools for public pages; replace Agent Chrome; cloud antivirus / tracking

## Layout

| Path | Role |
|------|------|
| `extension/` | MV3 Chrome extension (+ Native Messaging client) |
| `bridge.py` | Local HTTP bridge (`127.0.0.1:19876`) |
| `native_host/` | Chrome Native Messaging host (auto-start bridge) |
| `mcp_server.py` | Stdio MCP for any MCP client (stdlib only) |
| `lib/` | `bridge_runtime`, check_url, download, analyze (Python) |
| `scripts/hermes-chrome.sh` | Main CLI |
| `scripts/install-for-agent.sh` | One-shot: bridge + native host + optional MCP |
| `scripts/install-native-host.sh` / `.ps1` | Register Native Messaging host only |
| `scripts/install-windows.ps1` | Windows: Task + native host + optional MCP |
| `scripts/install-launchd.sh` | macOS bridge autostart |
| `scripts/daily-chrome-tabgroup.sh` | Backward-compatible alias → `hermes-chrome.sh` |
| `scripts/daily-chrome-agent-window.sh` | Named-window fallback (no extension) |
| `store/` | CWS listing, privacy policy, package script |

Runtime pid/log: `~/.hermes/run/hermes-chrome/` (not in git).

## Quick start (companion first, then extension)

Order matters: **companion → extension → pair → smoke**. Chrome Web Store alone
cannot run a local control plane. After companion install, the extension can
auto-start the bridge via **Native Messaging**. Works for **any** agent (CLI,
Grok, Cursor, Claude Desktop…).

```bash
# macOS / Linux — companion (once per machine)
./scripts/hermes-chrome.sh install-for-agent

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File .\scripts\install-windows.ps1
```

What the companion install does:

1. Starts the bridge + **autostart** (launchd / Windows Scheduled Task)
2. Registers **Chrome Native Messaging host** `com.leaf76.hermes_chrome`
   (extension click/reload → host ensures bridge on `:19876`)
3. Writes `~/.hermes/run/hermes-chrome/bridge.env` (token) + opens pairing
4. Optionally registers **stdio MCP** (`mcp_server.py`) for Grok if `~/.grok` exists  
   (same MCP file works for Cursor / Claude Desktop — point their config at it)

Then the browser half:

1. Install/enable **Hermes Chrome v1.7+** (CWS or Load unpacked → `./extension`)
2. Accept **nativeMessaging** if prompted → **Reload** → click icon once
3. Wait for auto-pair (or popup → **Pair**). If Bridge is offline, popup shows
   **Setup required** with the companion command — not a broken install alone.
4. **CLI users:** done — `hermes-chrome.sh --json ping`  
   **MCP users:** restart agent session so `hermes_chrome_*` tools load
5. Smoke: `hermes_chrome_status` / `capture` (MCP) or CLI equivalents

```bash
./scripts/hermes-chrome.sh --json bridge-status   # extension_connected:true
./scripts/hermes-chrome.sh --json ping
./scripts/hermes-chrome.sh install-native-host status
```

### CLI-only quick start (no MCP)

```bash
./scripts/hermes-chrome.sh install-launchd   # macOS recommended (creates token + KeepAlive)
# or: ./scripts/hermes-chrome.sh bridge-start
# Chrome → Load unpacked → ./extension  (or CWS install) — need v1.5.0+
# Extension v1.5.1+ auto-pairs when bridge pairing is open (reload is enough).
# Manual fallback: ./scripts/hermes-chrome.sh pair-open  then popup → Pair
./scripts/hermes-chrome.sh bridge-status     # auth:true, extension_connected:true
./scripts/hermes-chrome.sh ping              # waits/retries until extension is up; want 1.5.1+
./scripts/hermes-chrome.sh --json ping       # agent-friendly JSON only
./scripts/hermes-chrome.sh start 'https://example.com/'
./scripts/hermes-chrome.sh list-tabs         # workspace only; --all for every tab
./scripts/hermes-chrome.sh check-url 'https://example.com/'
./scripts/hermes-chrome.sh download 'https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf'
./scripts/hermes-chrome.sh analyze ~/.hermes/run/hermes-chrome/downloads/dummy.pdf
./scripts/hermes-chrome.sh open 'https://example.org/'
./scripts/hermes-chrome.sh status
./scripts/hermes-chrome.sh stop
```

### MCP for any client (manual)

Same stdio server works for **Grok, Cursor, Claude Desktop, Windsurf, VS Code**, etc.

```toml
# Example: ~/.grok/config.toml  (or Cursor mcp.json / Claude config equivalent)
[mcp_servers.hermes-chrome]
command = "/path/to/python3"
args = ["/path/to/hermes-chrome/mcp_server.py"]
enabled = true
startup_timeout_sec = 45
tool_timeout_sec = 120

[mcp_servers.hermes-chrome.env]
HERMES_CHROME_ROOT = "/path/to/hermes-chrome"
```

```bash
# Grok CLI helper
grok mcp add hermes-chrome -- /path/to/python3 /path/to/hermes-chrome/mcp_server.py
```

MCP tools: `hermes_chrome_status`, `hermes_chrome_ping`, `hermes_chrome_list_tabs`,
`hermes_chrome_list_tv`, `hermes_chrome_capture`, `hermes_chrome_open`,
`hermes_chrome_navigate`, `hermes_chrome_eval`, `hermes_chrome_click`,
`hermes_chrome_type`.

CLI-only agents can skip MCP and call `hermes-chrome.sh --json …` or HTTP `:19876`.

### Security model (read this)

Hermes Chrome is a **local control plane** for your daily browser. Treat the bridge token like a password to your Chrome session.

| Control | Default (v1.5+) |
|---------|-----------------|
| Bind | `127.0.0.1` only |
| Bridge token | **ON** (auto `~/.hermes/run/hermes-chrome/bridge.env`) |
| Auto-pair | Extension retries pair while disconnected; CLI waits/retries commands |
| CORS | `chrome-extension://…` only (not `*`) |
| `list-tabs` | Workspace group only (`--all` for everything) |
| `eval` / `click` / `type` / `capture` | Workspace tabs only (Options override) |
| `eval` world | ISOLATED by default (`world: "MAIN"` opt-in) |
| Private/IP hosts | Blocked for check-url / download / cookie fetch |
| Queue / body limits | Enforced on bridge |

**Threat model:** you trust this machine’s user + your agent CLI. You do **not** trust random websites or other local processes without the token.

Disable auth only if you accept the risk: `HERMES_CHROME_BRIDGE_ALLOW_NO_AUTH=1`.

### Safety / download helpers

```bash
# Local heuristics only (no third-party APIs)
./scripts/hermes-chrome.sh check-url 'https://example.com/a.pdf'
./scripts/hermes-chrome.sh download 'https://example.com/a.pdf'          # check → save → analyze
./scripts/hermes-chrome.sh download 'https://app.example/private' --cookies  # daily Chrome cookies
./scripts/hermes-chrome.sh analyze /path/to/file.zip
./scripts/hermes-chrome.sh check-tab-links --tab-id 123
# Downloads land in: ~/.hermes/run/hermes-chrome/downloads/
# Host policy: copy policy.example.json → ~/.hermes/run/hermes-chrome/policy.json
./scripts/hermes-chrome.sh policy-show
# Rotate token:
./scripts/hermes-chrome.sh token-setup generate
./scripts/hermes-chrome.sh bridge-restart
./scripts/hermes-chrome.sh pair-open   # then extension Pair
# Override size cap: HERMES_CHROME_DOWNLOAD_MAX_BYTES=10485760
```

Hermes wrapper (if present):

```bash
~/.hermes/scripts/hermes-chrome.sh …
# legacy alias still works:
~/.hermes/scripts/daily-chrome-tabgroup.sh …
```

Override root: `HERMES_CHROME_ROOT=/path/to/this/repo`

Bridge auth: CLI auto-loads `bridge.env`. Extension uses Pair or Options token field.

## Agent routing (recommended)

| Need | Tool |
|------|------|
| Real daily Chrome cookies / SSO / open tabs / capture | **Hermes Chrome** (this project) |
| Grok / Cursor tools list | `mcp_server.py` via `install-for-agent` |
| Public pages / multi-step DOM automation in isolated jar | Headless Hermes `browser_*` / Playwright |
| Headed but not daily Chrome | Agent Chrome `:9333` |

Always gate with `hermes_chrome_status` / `ping` (or `bridge-status` + `extension_connected`) before a command chain. Fail-fast on timeout.

**Same machine required:** Chrome, bridge, and the agent must share `localhost`. A Grok session on Windows cannot drive Chrome on a Mac.

## Chrome Web Store

```bash
./store/package.sh
# → store/dist/hermes-chrome-vX.Y.Z.zip
```

See `store/UPLOAD_GUIDE.md`.

## Capture (any page)

```bash
# Default: tab in the Hermes agent workspace
./scripts/hermes-chrome.sh capture --prefer auto --out /tmp/page.png

# Currently focused tab (privacy opt-in)
./scripts/hermes-chrome.sh capture --prefer active --out /tmp/active.png

# Filter by URL fragment
./scripts/hermes-chrome.sh list-tabs --url example.com
./scripts/hermes-chrome.sh open 'https://example.com/docs'
```

Also supports `tabId`, `urlIncludes`, `titleIncludes` on the JSON/bridge API.

Optional legacy finders (`prefer=gc|nq`, `list-tv`) exist for specific chart workflows;
they are **not** required and do not limit the product to those sites.

### Optional: external chart pipelines

Some private tools (e.g. a gold chart report) may call Hermes Chrome as one capture
backend. That integration lives **outside** this repo and must pass explicit URLs
or finder flags — it does not redefine Hermes Chrome as a single-site product.

## Related (outside this repo)

- **Headless** Hermes `browser_*` tools — public pages / DOM automation
- **Agent Chrome** isolated profile — `~/.hermes/scripts/agent-chrome.sh` + `~/.hermes/chrome-debug`
- Hermes prefs — AI-Memory `Memory/preferences.md`
