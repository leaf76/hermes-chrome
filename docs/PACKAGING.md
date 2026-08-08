# Packaging & release

How Hermes Chrome ships the **companion** (machine half) and related installers.

## Channels

| Channel | Purpose |
|---------|---------|
| **Chrome Web Store** | Extension only (browser half) |
| **GitHub Release** (`v*` tag) | Companion tarball, extension zip, `install.sh`, optional `.pkg` |
| **curl one-liner** | `scripts/install.sh` from `main` or release asset |
| **npm** `hermes-chrome-companion` | Thin `npx` wrapper that runs GitHub install scripts |
| **macOS .pkg** | GUI installer wrapping the same companion tree |
| **macOS wizard** | `scripts/packaging/macos-install-wizard.command` (double-click) |

**Not shipped as:** PyPI package, full Node rewrite, remote cloud bridge.

## Fixed paths

| Path | Role |
|------|------|
| `~/.hermes/hermes-chrome` | Install root (code) |
| `~/.hermes/run/hermes-chrome` | Runtime (token, logs, native host) |
| `~/.local/bin/hermes-chrome` | CLI shim |

## Tag release (GitHub Actions)

```bash
# Ensure extension/manifest.json version matches the tag
# e.g. version 1.8.0 → tag v1.8.0
git tag v1.8.0
git push origin v1.8.0
```

Workflow: `.github/workflows/release.yml`

- Builds `dist/release/*` via `scripts/packaging/build-release-assets.sh`
- Builds macOS `.pkg` via `scripts/packaging/build-macos-pkg.sh` (unsigned unless secrets set)
- Creates GitHub Release with assets + notes

### Optional signing / notarization secrets

| Secret | Use |
|--------|-----|
| `HERMES_CHROME_SIGN_IDENTITY` | `productsign` identity, e.g. `Developer ID Installer: …` |
| `HERMES_CHROME_NOTARY_PROFILE` | `xcrun notarytool` keychain profile name |

Without secrets the `.pkg` is **unsigned** (users: right-click → Open).

## Local builds

```bash
# Release assets (tarball + extension zip + checksums)
./scripts/packaging/build-release-assets.sh

# macOS package
./scripts/packaging/build-macos-pkg.sh
# optional:
# HERMES_CHROME_SIGN_IDENTITY="Developer ID Installer: …" ./scripts/packaging/build-macos-pkg.sh

# Double-click wizard (from Finder)
open scripts/packaging/macos-install-wizard.command
```

## npm thin shell

```bash
npx hermes-chrome-companion install
```

Package lives in `npm/`. It only downloads/runs install scripts — it does **not** embed the bridge.

Publish:

```bash
cd npm && npm publish --access public
```

## Windows

No signed MSI in-tree yet. Use:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-windows.ps1
```

or download `install-windows.ps1` from the GitHub Release.

## Extension zip only

```bash
./store/package.sh
# → store/dist/hermes-chrome-v<ver>.zip
```
