# hermes-chrome-companion (npm thin shell)

**Not the Chrome extension and not a reimplementation of the Python bridge.**

This package only helps you run the official GitHub installers:

| Platform | What it runs |
|----------|----------------|
| macOS / Linux | `scripts/install.sh` (downloaded or local `--dev`) |
| Windows | `scripts/install-windows.ps1` |

## Usage

```bash
# Install companion → ~/.hermes/hermes-chrome
npx hermes-chrome-companion

# Same
npx hermes-chrome-companion install

# From a local git clone of leaf76/hermes-chrome
npx hermes-chrome-companion install --dev

# Skip MCP registration
npx hermes-chrome-companion install --skip-mcp

# After install
npx hermes-chrome-companion doctor
# or (PATH shim):
hermes-chrome doctor
```

## What you still need

1. **Chrome extension** — Chrome Web Store or Load unpacked  
2. **Python 3.9+** (and **git** for the full one-liner path)  
3. Click extension icon → **Connected** / Pair  

Optional **MCP** (`mcp_server.py`) is registered by the real installer when Grok / Cursor / Claude Desktop are present — not by a separate npm MCP package.

## Publish (maintainers)

From repo root (version must match `extension/manifest.json` / companion):

```bash
cd npm
npm version 1.8.0 --no-git-tag-version
npm publish --access public
```

Prefer releasing companion assets via **GitHub Releases** (tag `v*`); this npm package is a convenience entrypoint only.
