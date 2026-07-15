# hermes-chrome — agent notes

## Scope

Chrome companion for Hermes / local agents: operate the **user daily Chrome**
safely (workspace isolation, CLI bridge). Tab Groups are a feature, not the
product boundary.

## Rules

- Prefer agent-friendly Chrome ops that do **not** hijack the user's active tab.
- Keep host permissions localhost-only unless product decision changes.
- Do not commit runtime pid/log or browser profiles.
- Bump `extension/manifest.json` version for CWS updates; run `store/package.sh`.
- User-facing plans: Traditional Chinese. Code / store EN / UI strings: English.

## Validate

```bash
./scripts/hermes-chrome.sh bridge-start
./scripts/hermes-chrome.sh ping   # needs extension loaded + icon click
./store/package.sh
```

## Paths

- Repo: this directory (also known historically as hermes-agent-tabgroup)
- CLI: `scripts/hermes-chrome.sh`
- Hermes wrappers: `~/.hermes/scripts/hermes-chrome.sh`
- Runtime: `~/.hermes/run/hermes-chrome/`
