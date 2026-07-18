# Privacy Policy — Hermes Chrome

**Last updated:** 2026-07-18

## Summary

Hermes Chrome is a Chrome extension that helps local AI agents (for example Hermes Agent CLI) operate the browser without hijacking the tab you are using. It is designed to run **entirely on your computer**.

A Chrome Tab Group is the default workspace isolation mechanism; the product purpose is broader agent-friendly Chrome control (navigate, organize, optional local tab capture, optional cookie-aware fetch for agent downloads). Companion CLI tools may also perform **local-only** URL heuristics and file/archive analysis on your machine (no cloud scanning by default).

## Data we collect

**We do not collect, sell, or transmit personal data to our servers.**

The extension:

- Does **not** include analytics, advertising, crash reporting to third parties, or account systems.
- Does **not** upload browsing history to a remote service operated by this extension.
- Only contacts a **user-run local bridge** at `http://127.0.0.1:19876` or `http://localhost:19876` (configurable to the same machine).

## Local processing

When the local bridge is running:

1. The extension long-polls the local bridge for commands.
2. Commands may create, update, or close tabs in the agent workspace (default: a Tab Group titled **Hermes**, configurable).
3. Optional **tab capture** may snapshot the visible viewport of a tab the user/CLI requests; the PNG is returned only to the local bridge/CLI on your machine.
4. Optional **fetch** of an http(s) URL using your browser cookies may run only when the local CLI/bridge issues that command; the response body is returned to the local bridge/CLI and may be saved under a local downloads directory on your machine.
5. Tab URLs and titles may temporarily appear in the extension popup status on your device.
6. Local CLI helpers (`check-url`, `analyze`) inspect URLs/files **on your computer only** using heuristics (schemes, redirects, zip member paths, compression ratio). They do not upload content to third-party scanners unless you separately configure such tools yourself.

If the local bridge is not running, the extension idles and performs no network activity beyond failed local requests.

## Permissions

| Permission | Why |
|---|---|
| `tabs` | Create/update/close agent workspace tabs; select tabs for local capture |
| `tabGroups` | Default workspace isolation via a native Tab Group |
| `storage` | Save settings on device |
| `alarms` | Wake the MV3 service worker so local bridge polling can continue |
| `scripting` | Optional on-device helpers for agent workflows when the local bridge issues a command |
| Host access to `127.0.0.1:19876` / `localhost:19876` | Talk to the local companion bridge only |
| Host access `<all_urls>` | Optional `captureVisibleTab` for local CLI tab snapshots (PNG stays on-device) |
| Host access (`<all_urls>`) | Required by Chrome for optional `tabs.captureVisibleTab` on normal pages the agent is asked to snapshot. Capture is only triggered by local CLI/bridge commands; images stay on your machine. Not limited to a single website. |

## Data retention

Settings are stored in Chrome’s local extension storage on your device. Session workspace IDs are stored in session storage and cleared when the workspace is stopped or the browser session ends. Capture images are not stored by the extension after they are sent to the local bridge.

## Children

This extension is not directed at children under 13.

## Contact

For privacy questions, open an issue at:  
https://github.com/leaf76/hermes-chrome/issues

Publisher: listing owner on the Chrome Web Store for **Hermes Chrome**.

## Changes

We may update this policy when the extension’s local behavior changes. The “Last updated” date above will change accordingly.
