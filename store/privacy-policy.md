# Privacy Policy — Hermes Chrome

**Last updated:** 2026-07-16

## Summary

Hermes Chrome is a Chrome extension that helps local AI agents (for example Hermes Agent CLI) operate the browser without hijacking the tab you are using. It is designed to run **entirely on your computer**.

A Chrome Tab Group is the default workspace isolation mechanism; the product purpose is broader agent-friendly Chrome control.

## Data we collect

**We do not collect, sell, or transmit personal data to our servers.**

The extension:

- Does **not** include analytics, advertising, crash reporting to third parties, or account systems.
- Does **not** upload browsing history to a remote service operated by this extension.
- Only contacts a **user-run local bridge** at `http://127.0.0.1:19876` or `http://localhost:19876` (same host/port family in settings).

## Local processing

When the local bridge is running:

1. The extension long-polls the local bridge for commands.
2. Commands may create, update, or close tabs in the agent workspace (default: a Tab Group titled **Hermes**, configurable).
3. Tab URLs and titles may temporarily appear in the extension popup status on your device.

If the local bridge is not running, the extension idles and performs no network activity beyond failed local requests.

## Permissions

| Permission | Why |
|---|---|
| `tabs` | Create/update/close agent workspace tabs |
| `tabGroups` | Default workspace isolation via a native Tab Group |
| `storage` | Save settings on device |
| Host access to `127.0.0.1:19876` / `localhost:19876` | Talk to the local companion bridge only |

## Data retention

Settings are stored in Chrome’s local extension storage on your device. Session workspace IDs are stored in session storage and cleared when the workspace is stopped or the browser session ends.

## Children

This extension is not directed at children under 13.

## Contact

For privacy questions, contact the publisher listed on the Chrome Web Store listing for **Hermes Chrome**.

## Changes

We may update this policy when the extension’s local behavior changes. The “Last updated” date above will change accordingly.
