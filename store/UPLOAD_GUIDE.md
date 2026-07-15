# Chrome Web Store upload guide

Package ready at: `store/dist/` (see package step)

## Before upload

1. Host **privacy policy** publicly (required if you touch host permissions / tabs):
   - Already in this repo: https://github.com/leaf76/hermes-agent-tabgroup/blob/main/store/privacy-policy.md
   - Or raw: https://raw.githubusercontent.com/leaf76/hermes-agent-tabgroup/main/store/privacy-policy.md
   - Optional: enable GitHub Pages for a cleaner URL later.

2. Optional: create a support email / homepage for the listing.

3. Reload unpacked extension once and smoke-test:
   ```bash
   ~/.hermes/scripts/daily-chrome-tabgroup.sh bridge-start
   # click extension icon
   ~/.hermes/scripts/daily-chrome-tabgroup.sh ping
   ```

## Developer Dashboard

1. https://chrome.google.com/webstore/devconsole
2. **New item** → upload the zip from `store/dist/`
3. Fill listing from `listing-en.md` (and zh-TW if you add locale)
4. Privacy:
   - Single purpose: see listing doc
   - Privacy policy URL: your hosted policy
   - Certify: does not sell data; only local bridge
5. Upload screenshots from `store/screenshots/` (1280x800)
6. Category: Productivity or Developer Tools
7. Distribution: Public (or Unlisted first for soak test)
8. Submit for review

## Review tips

- Call out that host permissions are **localhost only**.
- Provide a short demo script in “Notes for reviewers”:

```
1. Install extension
2. python3 bridge.py  # listen 127.0.0.1:19876
3. Click extension icon
4. curl -X POST http://127.0.0.1:19876/v1/command -H 'Content-Type: application/json' -d '{"id":"r1","action":"ping"}'
5. Extension popup should show bridge online; ping returns via /v1/result/r1 when extension polls
```

Or ship bridge.py + README in a public repo and link it.

## After publish

- Users still need the local bridge from Hermes (`daily-chrome-tabgroup.sh`).
- Document store URL in Hermes skill / README when live.

## Version bumps

Edit `extension/manifest.json` version, then re-run `store/package.sh`.
