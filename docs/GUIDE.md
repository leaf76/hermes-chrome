# Hermes Chrome — User Guide

**Works on any website.** Hermes Chrome is a local agent companion so AI agents and CLIs can operate **your daily Chrome** (open pages, list tabs, capture, light DOM ops) without stealing focus from the tab you are using.

- Repository: [github.com/leaf76/hermes-chrome](https://github.com/leaf76/hermes-chrome)
- Privacy: [privacy-policy.md](./privacy-policy.md)
- 繁中版: [GUIDE.zh-TW.md](./GUIDE.zh-TW.md)

---

## You need two parts

**Installing the Chrome extension from the Web Store is not enough.**

| Half | Role |
|------|------|
| **Companion** (machine) | Local bridge `127.0.0.1:19876` + Native Messaging host + token — install once from this repo |
| **Extension** (browser) | Workspace tabs, capture, light DOM — talks only to the local bridge |

**Ready** = popup shows Bridge **online** + Auth **ready**, and `hermes-chrome.sh --json ping` succeeds.

If the popup shows **Setup needed**, the companion is missing or the bridge is down — follow the command in the popup (or section 1 below). That is expected when only the store package is installed.

### Updates

**Extension** updates via the Chrome Web Store.

**Companion** (non `--dev` installs) auto-updates daily from the official GitHub
remote (fast-forward only). Disable: `hermes-chrome self-update disable`.
Manual: `hermes-chrome self-update now`. Popup still warns if the two halves drift.

---

## What it is (and is not)

| It is | It is not |
|------|-----------|
| A **local** bridge + Chrome extension for agents | A cloud browser / remote VPS browser |
| **Site-agnostic** — any `http(s)` URL you open | Hard-wired to one product or one website |
| Shared by **CLI, Grok, Cursor, Claude Desktop**, … | Grok-only |
| A **two-half** product (companion + extension) | An extension-only install that works out of the box |

Chrome security means the **extension alone** cannot expose a control port. Install the **companion first** (Native Messaging host + local bridge). After that, clicking the extension icon keeps the bridge available.

---

## One-time setup

### 🤖 Ask Your AI to Set It Up (One-Shot Prompt)

If you are using an AI coding assistant (Cursor, Claude Desktop, Windsurf, Roo Code, ChatGPT, Antigravity, etc.), paste this prompt into your AI chat:

```text
Please set up the Hermes Chrome companion on my machine for Chrome automation:
1. Detect my OS (macOS/Linux/Windows).
2. Install the companion:
   - macOS / Linux: run `curl -fsSL https://raw.githubusercontent.com/leaf76/hermes-chrome/main/scripts/install.sh | bash`
   - Windows: clone https://github.com/leaf76/hermes-chrome.git and run `powershell -ExecutionPolicy Bypass -File .\scripts\install-windows.ps1`
3. Ensure `~/.local/bin` is in my PATH (add `export PATH="$HOME/.local/bin:$PATH"` to my shell profile if missing).
4. Run `hermes-chrome doctor --skip-update` to verify companion + bridge (install extension afterward for ready=true).
5. If I use Cursor, Claude Desktop, or Grok, check that MCP is registered (~/.hermes/run/hermes-chrome/mcp-snippet.json).
6. Remind me to install the Hermes Chrome extension from Chrome Web Store and click the extension icon once to connect.
```

### 1. Manual install (once per machine) — do this first

**Recommended (no prior clone):**

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/leaf76/hermes-chrome/main/scripts/install.sh | bash
```

Installs to:

| Path | Purpose |
|------|---------|
| `~/.hermes/hermes-chrome` | Code (bridge, MCP, scripts, extension/) |
| `~/.hermes/run/hermes-chrome` | Runtime (token, pid, native host wrapper) |
| `~/.local/bin/hermes-chrome` | CLI PATH shim |

**From a git clone:**

```bash
./scripts/install.sh              # sync into ~/.hermes/hermes-chrome
./scripts/install.sh --dev        # use this clone in-place
# or: ./scripts/hermes-chrome.sh install-for-agent
```

**Windows PowerShell** (from a clone):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-windows.ps1
```

This installs:

1. Local bridge on `127.0.0.1:19876` (login autostart when possible)
2. Chrome **Native Messaging** host `com.leaf76.hermes_chrome`
3. Optional **MCP** for Grok / Cursor / Claude Desktop when present (`install-mcp.py`)
4. PATH shim + `doctor` ready-check

Requires **Python 3.9+** and **git** (for the one-liner). Not on PyPI.

Other install channels:

```bash
npx hermes-chrome-companion          # thin npm wrapper → same install.sh
# macOS double-click: scripts/packaging/macos-install-wizard.command
# macOS .pkg: GitHub Releases (tag v*) or ./scripts/packaging/build-macos-pkg.sh
```

MCP snippet (any client): `~/.hermes/run/hermes-chrome/mcp-snippet.json`  
Packaging notes: [PACKAGING.md](./PACKAGING.md)

### 2. Install / enable the extension

- [Chrome Web Store](https://chromewebstore.google.com/detail/hermes-chrome/mkoaoadlkijccmmbkioagnlngbbeocfa) **or** Load unpacked → `extension/` (v1.8.7)
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

### Close the workspace when done (not the companion)

The **Hermes Tab Group is disposable**. When the task is finished, close agent tabs
or stop the workspace — **keep the local companion / bridge running** for next time.

```bash
# Close agent workspace tabs + clear the group (CLI)
./scripts/hermes-chrome.sh stop

# Or close tabs/group manually in Chrome — same idea
```

MCP: `hermes_chrome_stop` (optional `close_tabs: false` only ungroups, leaves tabs open).

Do **not** uninstall the companion or extension just to clean up tabs.

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
