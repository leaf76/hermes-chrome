# hermes-agent-tabgroup

Chrome extension + local bridge that gives Hermes / local CLIs a **real**
Chrome Tab Group workspace on the user's everyday Chrome profile.

**Repo:** https://github.com/leaf76/hermes-agent-tabgroup  

**Privacy policy (for Chrome Web Store):**  
https://github.com/leaf76/hermes-agent-tabgroup/blob/main/store/privacy-policy.md

## Layout

| Path | Role |
|------|------|
| `extension/` | MV3 Chrome extension (CWS package source) |
| `bridge.py` | Local HTTP queue on `127.0.0.1:19876` |
| `scripts/daily-chrome-tabgroup.sh` | CLI |
| `scripts/daily-chrome-agent-window.sh` | Fallback: named window (no real group) |
| `store/` | CWS listing, privacy policy, package script |

Runtime state (pid/log) defaults to `~/.hermes/run/daily-chrome-agent/` — not in this repo.

## Quick start

```bash
# bridge + CLI (from repo)
./scripts/daily-chrome-tabgroup.sh bridge-start
# Load unpacked: ./extension  (or install from CWS when published)
# Click extension icon once
./scripts/daily-chrome-tabgroup.sh ping
./scripts/daily-chrome-tabgroup.sh start 'https://example.com/'
./scripts/daily-chrome-tabgroup.sh stop
```

Hermes thin wrappers (if installed):

```bash
~/.hermes/scripts/daily-chrome-tabgroup.sh …
```

Override root: `HERMES_DAILY_CHROME_ROOT=/path/to/this/repo`

## Chrome Web Store

```bash
./store/package.sh
# → store/dist/hermes-agent-tabgroup-vX.Y.Z.zip
```

See `store/UPLOAD_GUIDE.md`. Host `store/privacy-policy.md` publicly before submit.

## Related (not in this repo)

- Agent Chrome isolated profile: `~/.hermes/scripts/agent-chrome.sh` + `~/.hermes/chrome-debug`
- Hermes prefs: AI-Memory `Memory/preferences.md` (browser two/three-mode policy)
