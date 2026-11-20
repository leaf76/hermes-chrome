# Privacy Policy — Hermes Chrome

**Last updated:** 2026-11-20

## Summary

Hermes Chrome is a Chrome extension that helps local AI agents (for example Hermes Agent CLI) operate the browser without hijacking the tab you are using. It is designed to run **entirely on your computer**.

A Chrome Tab Group is the default workspace isolation mechanism; the product purpose is broader agent-friendly Chrome control (navigate, organize, optional local tab capture, optional cookie-aware fetch for agent downloads, and an optional local Side Panel UI). Companion CLI tools may also perform **local-only** URL heuristics and file/archive analysis on your machine (no cloud scanning by default).

## Data we collect

**We do not collect, sell, or transmit personal data to our servers.**

The extension:

- Does **not** include analytics, advertising, crash reporting to third parties, or account systems.
- Does **not** upload browsing history to a remote service operated by this extension.
- Only contacts a **user-run local companion bridge** on loopback (`http://127.0.0.1:19876` or `http://localhost:19876`, restricted to loopback hosts on the same machine) and optionally the local Hermes Agent WebSocket gateway (`ws://127.0.0.1:9119/api/ws` or `:8642`) when you use Side Panel chat.

## Local security model

- The local bridge requires a **shared token** by default. Commands and polling without the token are rejected.
- The extension obtains the token via a short loopback **pairing** step (or paste into Options). The token is stored in Chrome local extension storage on your device and in a user-owned file under `~/.hermes/run/hermes-chrome/` for the CLI.
- Anyone who obtains that token on your machine can instruct the extension to act with your browser privileges. Protect the token like a local password; do not commit or share it.
- Default command scope is the agent **workspace Tab Group**; listing or operating on tabs outside the workspace requires enabling **Allow cross-workspace ops** in Extension Options.
- `fetch_url` runs only when the local CLI/bridge issues a command, and both cookie-aware fetch and companion downloads block private/loopback/metadata hosts by default unless explicitly opted in under Extension Options.

## Local processing

When the local bridge is running:

1. The extension long-polls the local bridge on loopback for commands (authenticated).
2. Commands may create, update, or close tabs in the agent workspace (default: a Tab Group titled **Hermes**, configurable).
3. Optional **tab capture** may snapshot the visible viewport of a tab the user/CLI requests; the PNG is returned only to the local bridge/CLI on your machine.
4. Optional **fetch** of an `http(s)` URL using your browser cookies may run only when the local CLI/bridge issues that command; the response body is returned to the local bridge/CLI and may be saved under a local downloads directory on your machine.
5. Tab URLs and titles may temporarily appear in the extension Side Panel / workspace view on your device, and if you click a Side Panel context button (Inject Context / Summarize / Inspect), active-tab title/URL/selection is added only to the Side Panel chat prompt you submit to your local Hermes companion/gateway.
6. Local CLI helpers (`check-url`, `analyze`) inspect URLs/files **on your computer only** using heuristics (schemes, redirects, zip member paths, compression ratio). They do not upload content to third-party scanners unless you separately configure such tools yourself.

If the local bridge is not running, the extension idles and performs no remote network activity.

## Permissions

| Permission | Why |
|---|---|
| `tabs` | Create/update/close agent workspace tabs; select tabs for local capture |
| `tabGroups` | Default workspace isolation via a native Tab Group |
| `storage` | Save local settings and pairing token on device (and bounded session action history) |
| `alarms` | Wake the MV3 service worker so local bridge polling can continue |
| `scripting` | Optional on-device helpers (`read_page`, `dom_query`, `eval`, `click`, `type`, editor ops) only when the local bridge issues a command or when you trigger Side Panel page-context extraction |
| `nativeMessaging` | Talk only to the user-installed local host `com.leaf76.hermes_chrome` on the same machine so the bridge on `127.0.0.1:19876` can auto-start |
| `sidePanel` | Display the Hermes Chrome Side Panel (Action Inspector, local Hermes chat, and Workspace Manager) alongside your browser tabs |
| Host access to `http://127.0.0.1/*` & `http://localhost/*` | Talk to the user-run local companion bridge (`:19876`) only |
| Host access `<all_urls>` | Required by Chrome for optional `tabs.captureVisibleTab`, workspace DOM/script helpers, and cookie-aware local fetch only when triggered by local bridge commands or explicit Side Panel actions; outputs stay on your machine and are not limited to a single website |

## Data retention

Settings are stored in Chrome’s local extension storage on your device. Session workspace IDs and bounded action-inspector metadata (up to 80 recent action headers without raw screenshot blobs) are stored in Chrome session storage and cleared when the workspace is stopped or the browser session ends. Raw capture images are not stored in extension storage after they are sent to the local bridge.

## Children

This extension is not directed at children under 13.

## Contact

For privacy questions, open an issue at:  
https://github.com/leaf76/hermes-chrome/issues

Publisher: listing owner on the Chrome Web Store for **Hermes Chrome**.

## Changes

We may update this policy when the extension’s local behavior changes. The “Last updated” date above will change accordingly.
