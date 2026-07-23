# hermes-chrome

**Repo:** https://github.com/leaf76/hermes-chrome  

Local companion that makes **Chrome easy for Hermes / local AI agents to operate**—without hijacking the tab you are using.

Tab Groups are one isolation tool, not the whole product.

**Privacy policy (Chrome Web Store):**  
https://leaf76.github.io/hermes-chrome/privacy-policy  

(also in-repo: `docs/privacy-policy.md` / `store/privacy-policy.md`)

## What it is for

| Goal | How |
|------|-----|
| Drive Chrome from a CLI / Hermes | Local bridge + Chrome extension |
| Keep your active browsing | Agent work goes to a dedicated workspace (Tab Group by default) |
| Reuse your real cookies / SSO | Runs on **daily Chrome**, not a headless-only sandbox |
| Reduce focus steal | New tabs default to `active: false`; no AppleScript `activate` |

## Features (today)

1. **Agent workspace** — native Chrome Tab Group (`Hermes` / configurable title)
2. **CLI** — `start` / `open` / `new-tab` / `navigate` / `list-tabs` / `status` / `stop` / `ping`
3. **Light DOM ops** — `eval` / `click` / `type` / `page-assets` (tabId)
4. **Capture** — generic tab PNG via `captureVisibleTab` (`prefer=gc|nq` is gold finder only)
5. **URL check** — local `check-url` (scheme / redirect / heuristics; no cloud)
6. **Download + analyze** — `download` (optional `--cookies`) → `analyze` images/zip/tar (zip-bomb & path safety heuristics)
7. **Policy** — optional host allow/deny list (`~/.hermes/run/hermes-chrome/policy.json`)
8. **Agent JSON** — `--json` / `--json-only` (no bridge chatter on stdout)
9. **Local bridge** — `127.0.0.1:19876` queue + **extension last-seen** on `/v1/health`
10. **launchd (macOS)** — `install-launchd` for login + KeepAlive bridge; **token by default**
11. **Auth + pairing** — shared token (`bridge.env`); extension Pair / Options; CORS locked to extension origins
12. **Fallback** — named window helper if you cannot load the extension yet

## Planned / non-goals

- **Planned:** optional allowlists, opt-in external threat intel, richer agent policies
- **Non-goal:** replace headless browser tools for public pages; replace Agent Chrome; cloud antivirus / tracking

## Layout

| Path | Role |
|------|------|
| `extension/` | MV3 Chrome extension |
| `bridge.py` | Local HTTP bridge (`127.0.0.1:19876`) |
| `lib/` | Local `check_url` / `download_file` / `analyze_file` (Python) |
| `scripts/hermes-chrome.sh` | Main CLI |
| `scripts/install-launchd.sh` | macOS bridge autostart |
| `scripts/daily-chrome-tabgroup.sh` | Backward-compatible alias → `hermes-chrome.sh` |
| `scripts/daily-chrome-agent-window.sh` | Named-window fallback (no extension) |
| `store/` | CWS listing, privacy policy, package script |

Runtime pid/log: `~/.hermes/run/hermes-chrome/` (not in git).

## Quick start

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
| Public pages / multi-step DOM automation in isolated jar | Headless Hermes `browser_*` / Playwright |
| Headed but not daily Chrome | Agent Chrome `:9333` |

Always gate with `ping` (or `bridge-status` + `extension_connected`) before a command chain. Fail-fast on timeout.

## Chrome Web Store

```bash
./store/package.sh
# → store/dist/hermes-chrome-vX.Y.Z.zip
```

See `store/UPLOAD_GUIDE.md`.

## Capture (any page / gold helper)

```bash
./scripts/hermes-chrome.sh capture --prefer active --out /tmp/page.png
./scripts/hermes-chrome.sh capture --prefer gc --out /tmp/gc.png   # title hint only
./scripts/hermes-chrome.sh list-tv                                  # optional TV tab list
./scripts/hermes-chrome.sh list-tabs --url tradingview.com
```

`capture` is **generic** (any http/https tab via tabId / active / urlIncludes / title hints).  
Gold’s `prefer=gc|nq` is only a **finder hint**, not a product limit or hard-coded site permission.

Gold pipeline (`~/gold-usd-report`) auto order:

1. CDP (if up)
2. **Hermes Chrome** `captureVisibleTab` via bridge `:19876` (+ preflight open missing GC/NQ tabs)
3. macOS screencapture window (fallback; disabled when `TV_CAPTURE_BACKEND=hermes-chrome`)

```bash
TV_CAPTURE_BACKEND=hermes-chrome ./.venv/bin/python -c \
  'from tv_capture import capture_tradingview; print(capture_tradingview(prefer="gc"))'
# skip in auto: TV_HERMES_CHROME=0
# disable auto-open missing TV tabs: TV_AUTO_OPEN_TABS=0
```

Requires extension **v1.3.0+** reloaded + icon clicked for full CLI surface; capture since v1.2.0+.

## Related (outside this repo)

- **Headless** Hermes `browser_*` tools — public pages / DOM automation
- **Agent Chrome** isolated profile — `~/.hermes/scripts/agent-chrome.sh` + `~/.hermes/chrome-debug`
- Hermes prefs — AI-Memory `Memory/preferences.md`
