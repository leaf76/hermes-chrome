# hermes-agent-tabgroup — agent notes

## Scope

- Chrome MV3 extension + local bridge for Hermes daily-Chrome Tab Groups.
- Not the isolated Agent Chrome profile (`~/.hermes/chrome-debug`).

## Rules

- Keep host permissions localhost-only unless product decision changes.
- Do not commit runtime pid/log or browser profiles.
- Bump `extension/manifest.json` version when shipping CWS updates; re-run `store/package.sh`.
- Prefer Traditional Chinese for user-facing plans; English for code/UI strings/store EN listing.

## Validate

```bash
./scripts/daily-chrome-tabgroup.sh bridge-start
./scripts/daily-chrome-tabgroup.sh ping   # needs extension loaded + icon click
./store/package.sh
```

## Paths

- Repo: this directory
- Hermes wrappers: `~/.hermes/scripts/daily-chrome-tabgroup.sh` (thin)
- Runtime: `~/.hermes/run/daily-chrome-agent/`
