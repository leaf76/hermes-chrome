# Chrome Web Store upload guide — Hermes Chrome

Package: `store/dist/` (run `./store/package.sh`)  
**Submission pack (preferred):** `store/submission-pack/` — zip + FILL.md + assets + 送審檢查表.txt

Current version: **1.4.2** → `hermes-chrome-v1.4.2.zip`

## Before upload

1. **Privacy policy** (public):
   - GitHub Pages: https://leaf76.github.io/hermes-chrome/privacy-policy
   - Blob: https://github.com/leaf76/hermes-chrome/blob/main/store/privacy-policy.md
   - Docs: https://github.com/leaf76/hermes-chrome/blob/main/docs/privacy-policy.md

2. Reload unpacked extension from this repo’s `extension/` and smoke-test:
   ```bash
   ./scripts/hermes-chrome.sh bridge-status   # extension_connected + version 1.4.2
   # click extension icon if needed
   ./scripts/hermes-chrome.sh ping
   ```

3. Prefer paste pack: `store/submission-pack/FILL.md` + `送審檢查表.txt`

## Developer Dashboard

1. https://chrome.google.com/webstore/devconsole  
2. **Update item** (or New item) → upload `store/submission-pack/hermes-chrome-v1.4.2.zip`  
3. Listing: `FILL.md` / `store/listing-en.md` (+ `listing-zh-TW.md` if needed)  
4. Privacy policy URL → https://leaf76.github.io/hermes-chrome/privacy-policy  
5. Screenshots: `store/submission-pack/screenshots/`  
6. Category: Productivity or Developer Tools  
7. Remote code = **No**; data types unchecked; host justification includes localhost + `<all_urls>` for local capture  
8. Submit (or Unlisted first)

## Reviewer notes (paste)

```
Hermes Chrome is a local agent companion (v1.4.2).
Host permissions: localhost bridge + <all_urls> for optional local tab capture
(any site the CLI requests; PNG stays on-device; not site-locked).
No remote code, no analytics, no cloud account.
1. Install extension
2. python3 bridge.py  (listen 127.0.0.1:19876)
3. Click extension icon
4. Use CLI: hermes-chrome.sh ping / start / list-tabs / capture / stop
Repo: https://github.com/leaf76/hermes-chrome
Privacy: https://leaf76.github.io/hermes-chrome/privacy-policy
```

## Version bumps

Edit `extension/manifest.json` version → `./store/package.sh` → copy zip into `store/submission-pack/` → upload.
