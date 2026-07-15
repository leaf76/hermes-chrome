# Chrome Web Store upload guide — Hermes Chrome

Package: `store/dist/` (run `./store/package.sh`)

## Before upload

1. **Privacy policy** (public):
   - GitHub Pages: https://leaf76.github.io/hermes-chrome/privacy-policy
   - Blob: https://github.com/leaf76/hermes-chrome/blob/main/store/privacy-policy.md
   - Docs: https://github.com/leaf76/hermes-chrome/blob/main/docs/privacy-policy.md

2. Reload unpacked extension from this repo’s `extension/` and smoke-test:
   ```bash
   ./scripts/hermes-chrome.sh bridge-start
   # click extension icon
   ./scripts/hermes-chrome.sh ping
   ```

## Developer Dashboard

1. https://chrome.google.com/webstore/devconsole  
2. **New item** → upload `store/dist/hermes-chrome-v*.zip`  
3. Listing: `store/listing-en.md` (+ `listing-zh-TW.md` if needed)  
4. Privacy policy URL → GitHub blob/raw above  
5. Screenshots: `store/screenshots/` (prefer real UI captures)  
6. Category: Productivity or Developer Tools  
7. Submit (or Unlisted first)

## Reviewer notes (paste)

```
Hermes Chrome is a local agent companion.
Host permissions are localhost-only (127.0.0.1:19876).
1. Install extension
2. python3 bridge.py
3. Click extension icon
4. Use CLI hermes-chrome.sh ping / start / stop
Tab Groups are the default workspace; product scope is agent Chrome control.
```

## Version bumps

Edit `extension/manifest.json` version → `./store/package.sh` → upload new zip.
