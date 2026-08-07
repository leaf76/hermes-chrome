# Hermes Chrome — User Guide

**Works on any website.** Hermes Chrome is a local companion so AI agents and CLIs can operate **your daily Chrome** (open pages, list tabs, capture, light DOM ops) without stealing focus from the tab you are using.

- Repository: [github.com/leaf76/hermes-chrome](https://github.com/leaf76/hermes-chrome)
- Privacy: [privacy-policy.md](./privacy-policy.md)
- 繁中版: [GUIDE.zh-TW.md](./GUIDE.zh-TW.md)

---

## What it is (and is not)

| It is | It is not |
|------|-----------|
| A **local** bridge + Chrome extension for agents | A cloud browser / remote VPS browser |
| **Site-agnostic** — any `http(s)` URL you open | Hard-wired to one product or one website |
| Shared by **CLI, Grok, Cursor, Claude Desktop**, … | Grok-only |

Chrome security means the **extension alone** cannot expose a control port. Install the **companion once** (Native Messaging host + local bridge). After that, clicking the extension icon keeps the bridge available.

---

## One-time setup

### 1. Install the companion (once per machine)

```bash
# macOS / Linux (from a clone of this repo)
./scripts/hermes-chrome.sh install-for-agent

# Windows PowerShell
powershell -ExecutionPolicy Bypass -File .\scripts\install-windows.ps1
```

This installs:

1. Local bridge on `127.0.0.1:19876` (login autostart when possible)
2. Chrome **Native Messaging** host `com.leaf76.hermes_chrome`
3. Optional MCP registration (e.g. Grok) when an agent config folder exists

Requires a real **Python 3** on `PATH` (not the Windows Store stub).

### 2. Install / enable the extension

- Chrome Web Store **or** Load unpacked → `extension/` (v1.7+)
- Accept **nativeMessaging** if prompted
- **Reload** the extension → **click the icon once**
- Wait for auto-pair, or press **Pair** in the popup

Healthy popup: Bridge **online**, Auth **ready**.

### 3. Point your agent (optional)

Same MCP server works for many clients:

```text
python /path/to/hermes-chrome/mcp_server.py
```

Or use the CLI only (no MCP):

```bash
./scripts/hermes-chrome.sh --json ping
./scripts/hermes-chrome.sh open 'https://example.com/'
```

---

## Everyday use — any page

All examples are **generic**. Replace URLs with whatever you need.

### Open a page (agent workspace Tab Group)

```bash
./scripts/hermes-chrome.sh start 'https://example.com/'
./scripts/hermes-chrome.sh open 'https://github.com/'
./scripts/hermes-chrome.sh new-tab 'https://news.ycombinator.com/'
```

MCP: `hermes_chrome_open` with `{ "url": "https://example.com/" }`.

### List tabs

```bash
./scripts/hermes-chrome.sh list-tabs                 # workspace only
./scripts/hermes-chrome.sh list-tabs --all           # every tab (sensitive)
./scripts/hermes-chrome.sh list-tabs --url github.com
```

### Capture a screenshot

```bash
# Default: first/active tab in the Hermes workspace
./scripts/hermes-chrome.sh capture --prefer auto --out /tmp/page.png

# Explicit tab / URL needle
./scripts/hermes-chrome.sh capture --prefer auto --out /tmp/gh.png
# via JSON API fields: tabId, urlIncludes, titleIncludes

# User's currently focused tab (opt-in privacy)
./scripts/hermes-chrome.sh capture --prefer active --out /tmp/active.png
```

### Light DOM ops

```bash
./scripts/hermes-chrome.sh eval --tab-id 123 --expr 'document.title'
./scripts/hermes-chrome.sh click --tab-id 123 --selector 'button.submit'
./scripts/hermes-chrome.sh type --tab-id 123 --selector 'input#q' --text 'hello'
```

### Agent JSON mode

```bash
./scripts/hermes-chrome.sh --json ping
./scripts/hermes-chrome.sh --json list-tabs --url example.com
```

---

## Architecture (short)

```text
Agent (CLI / MCP / HTTP)
        │
        ▼
  bridge.py  127.0.0.1:19876
        ▲
        │ long-poll
  Hermes Chrome extension
        │ connectNative (optional auto-start)
        ▼
  native_host  com.leaf76.hermes_chrome
```

Same machine only: Chrome, bridge, and agent must share `localhost`.

---

## Optional helpers (not required)

Some flags exist for specific workflows (e.g. `capture --prefer gc|nq`, `list-tv`). They are **optional finders**, not product limits. Prefer `tabId`, `urlIncludes`, `open <url>`, and workspace capture for general use.

---

## Troubleshooting

| Symptom | What to try |
|--------|-------------|
| Bridge **offline** | Run `install-for-agent` once; click extension icon; check Python |
| Native host not found | `install-native-host` / re-run installer; reload extension |
| Auth need pair | Popup → **Pair**, or `pair-open` then Pair |
| Cannot see tab | Tab outside Hermes group → Options → allow cross-workspace, or move tab into group |
| MCP tools missing | Restart the agent session after registering `mcp_server.py` |

```bash
./scripts/hermes-chrome.sh --json bridge-status
./scripts/hermes-chrome.sh install-native-host status
```

---

## Security

- Bridge binds **127.0.0.1 only**; token on by default (`~/.hermes/run/hermes-chrome/bridge.env`)
- Treat the token like a password to your browser session
- `list-tabs` defaults to the agent workspace only
- No cloud account, no analytics from this project

---

## In-extension guide

After install, open the extension popup → **Guide**, or  
`chrome-extension://<id>/help.html` (same content, offline).
