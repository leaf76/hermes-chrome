# Chrome Web Store upload guide — Hermes Chrome

Package: `store/dist/` (run `./store/package.sh`)  
**Submission pack (preferred):** `store/submission-pack/` — zip + FILL.md + assets + 送審檢查表.txt

Current version: **1.8.7** → `hermes-chrome-v1.8.7.zip`
(GitHub Release asset: `hermes-chrome-extension-v1.8.7.zip` — same package)

## Before upload

1. **Privacy policy** (public):
   - GitHub Pages: https://leaf76.github.io/hermes-chrome/privacy-policy
   - Blob: https://github.com/leaf76/hermes-chrome/blob/main/store/privacy-policy.md
   - Docs: https://github.com/leaf76/hermes-chrome/blob/main/docs/privacy-policy.md

2. Reload unpacked extension from this repo’s `extension/` and smoke-test:
   ```bash
   hermes-chrome --json bridge-status   # extension_connected + version 1.8.7
   hermes-chrome --json ping
   hermes-chrome stop                   # closes agent workspace only
   ```

3. Listing copy: `store/listing-en.md` + `store/listing-zh-TW.md`  
   (optional pack: `store/submission-pack/`)

## Developer Dashboard (manual — cannot automate)

1. Open https://chrome.google.com/webstore/devconsole (login as the publisher account)
2. Click existing **Hermes Chrome** item (or Create new item)
3. **Package** → Upload new package:
   - Local: `store/dist/hermes-chrome-v1.8.7.zip`
   - Or download: https://github.com/leaf76/hermes-chrome/releases/download/v1.8.7/hermes-chrome-extension-v1.8.7.zip
4. **Store listing**
   - Short description → from `listing-en.md` (≤132 chars)
   - Detailed description → full detailed block from `listing-en.md`
   - Add zh-TW listing if the console has a locale tab → `listing-zh-TW.md`
5. Privacy policy URL → https://leaf76.github.io/hermes-chrome/privacy-policy
6. Screenshots: keep existing or `store/screenshots/` / `store/submission-pack/screenshots/`
7. Category: Productivity or Developer Tools
8. Single purpose / permissions: Remote code = **No**; paste justifications below (especially **nativeMessaging**)
9. **Submit for review** (or Unlisted first)

## Permission justifications (paste into CWS console)

### `nativeMessaging` (required explanation)

```
nativeMessaging is required so the extension can talk to a user-installed local
Native Messaging host (com.leaf76.hermes_chrome) that lives only on the same machine.

Purpose:
• Ensure the optional local companion bridge (127.0.0.1:19876) is running when the
  user clicks the extension icon or reloads the extension.
• Chrome security does not allow extensions to open a listening control port
  themselves; the companion process is installed separately by the user from our
  open-source repo (GitHub install scripts: install.sh / install-native-host).

What it is NOT:
• No remote native host, no downloading or executing remote code.
• No cloud messaging. The host path is registered locally by the user.
• Once the bridge is already up, command traffic uses localhost HTTP; native
  messaging is for auto-start / first-run reliability and does not leave the machine.

Host name: com.leaf76.hermes_chrome
Official extension id: mkoaoadlkijccmmbkioagnlngbbeocfa
(Bridge pairing/CORS allowlist this id only — see docs/THREAT-MODEL.md.)
Repo: https://github.com/leaf76/hermes-chrome
```

### Host permissions

```
(1) http://127.0.0.1:19876/* and http://localhost:19876/*
    Talk to the user-run local companion bridge (command queue, pair, health).
    Bind is localhost-only; not a remote server.

(2) <all_urls>
    Required by Chrome for optional tabs.captureVisibleTab and light scripting on
    normal pages the local CLI/agent asks to operate (any http(s) site). Capture is
    triggered only via the local bridge; PNG stays on-device. Not limited to one
    third-party product domain.
```

### Other permissions (short)

```
tabs, tabGroups — agent workspace Tab Group (open / list / stop workspace tabs).
storage — local settings and pairing token (device only).
alarms — reconnect / ensure native host on an interval.
scripting — eval / click / type on workspace tabs when requested by local CLI/agent.
```

### Single purpose

```
Local companion so developers’ AI agents and CLIs can operate their daily Chrome
(open workspace tabs, capture, light DOM) without hijacking the active tab.
Requires a separately installed local bridge; this package is the browser half only.
```

## Reviewer notes (paste)

```
Hermes Chrome is a local agent companion (v1.8.7).
Two parts: this extension (browser half) + one-time local companion from GitHub
(install.sh registers Native Messaging host com.leaf76.hermes_chrome + bridge).
Extension alone cannot open a control port (Chrome security) — that is why
nativeMessaging exists: auto-start the user-installed local host so the bridge
on 127.0.0.1:19876 is available when the user clicks the icon.

Host permissions: 127.0.0.1:19876 bridge + <all_urls> for optional local tab capture
(any site the CLI requests; PNG stays on-device; not site-locked).
No remote code, no analytics, no cloud account.
1. curl -fsSL https://raw.githubusercontent.com/leaf76/hermes-chrome/main/scripts/install.sh | bash
2. Install/reload this extension, click icon, Pair if needed
3. CLI: hermes-chrome ping / open / list-tabs / capture / stop
Repo: https://github.com/leaf76/hermes-chrome
Privacy: https://leaf76.github.io/hermes-chrome/privacy-policy
Release: https://github.com/leaf76/hermes-chrome/releases/tag/v1.8.7
```

## Version bumps

Edit `extension/manifest.json` version → `./store/package.sh` → upload the new zip from `store/dist/`.
