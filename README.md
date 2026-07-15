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
2. **CLI** — `start` / `open` / `new-tab` / `status` / `stop` / `ping`
3. **Local bridge** — `127.0.0.1:19876` queue between CLI and extension
4. **Fallback** — named window helper if you cannot load the extension yet

## Planned / non-goals

- **Planned:** richer Chrome ops that stay agent-friendly (more status, safer defaults, optional auth on bridge)
- **Non-goal:** replace headless browser tools for public pages; replace Agent Chrome isolated profile when you do not need daily cookies

## Layout

| Path | Role |
|------|------|
| `extension/` | MV3 Chrome extension |
| `bridge.py` | Local HTTP bridge (`127.0.0.1:19876`) |
| `scripts/hermes-chrome.sh` | Main CLI |
| `scripts/daily-chrome-tabgroup.sh` | Backward-compatible alias → `hermes-chrome.sh` |
| `scripts/daily-chrome-agent-window.sh` | Named-window fallback (no extension) |
| `store/` | CWS listing, privacy policy, package script |

Runtime pid/log: `~/.hermes/run/hermes-chrome/` (not in git).

## Quick start

```bash
./scripts/hermes-chrome.sh bridge-start
# Chrome → Load unpacked → ./extension  (or install from CWS when published)
# Click the extension icon once
./scripts/hermes-chrome.sh ping
./scripts/hermes-chrome.sh start 'https://example.com/'
./scripts/hermes-chrome.sh open 'https://example.org/'
./scripts/hermes-chrome.sh status
./scripts/hermes-chrome.sh stop
```

Hermes wrapper (if present):

```bash
~/.hermes/scripts/hermes-chrome.sh …
# legacy alias still works:
~/.hermes/scripts/daily-chrome-tabgroup.sh …
```

Override root: `HERMES_CHROME_ROOT=/path/to/this/repo`

## Chrome Web Store

```bash
./store/package.sh
# → store/dist/hermes-chrome-vX.Y.Z.zip
```

See `store/UPLOAD_GUIDE.md`.

## Capture (TradingView / gold)

```bash
./scripts/hermes-chrome.sh list-tv
./scripts/hermes-chrome.sh capture --prefer gc --out /tmp/gc.png
```

Gold pipeline (`~/gold-usd-report`) auto order:

1. CDP (if up)
2. **Hermes Chrome** `captureVisibleTab` via bridge `:19876`
3. macOS screencapture window (fallback)

```bash
TV_CAPTURE_BACKEND=hermes-chrome ./.venv/bin/python -c \
  'from tv_capture import capture_tradingview; print(capture_tradingview(prefer="gc"))'
# skip in auto: TV_HERMES_CHROME=0
```

Requires extension **v1.1.0+** reloaded + icon clicked; GC!/NQ! tabs open.

## Related (outside this repo)

- **Headless** Hermes `browser_*` tools — default for public pages
- **Agent Chrome** isolated profile — `~/.hermes/scripts/agent-chrome.sh` + `~/.hermes/chrome-debug`
- Hermes prefs — AI-Memory `Memory/preferences.md`
