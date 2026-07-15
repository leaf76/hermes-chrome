# Privacy Policy — Hermes Agent Tab Group

**Last updated:** 2026-07-16

## Summary

Hermes Agent Tab Group is a Chrome extension that creates a dedicated browser **Tab Group** for local AI agent tooling (for example Hermes Agent CLI). It is designed to run **entirely on your computer**.

## Data we collect

**We do not collect, sell, or transmit personal data to our servers.**

The extension:

- Does **not** include analytics, advertising, crash reporting to third parties, or account systems.
- Does **not** upload browsing history to a remote service operated by this extension.
- Only contacts a **user-run local bridge** at `http://127.0.0.1:19876` or `http://localhost:19876` (configurable to the same host/port family in settings).

## Local processing

When the local bridge is running:

1. The extension long-polls the local bridge for commands.
2. Commands may create, update, or close tabs inside a Tab Group named **Hermes Agent** (or a title you set).
3. Tab URLs and titles may temporarily appear in the extension popup status for debugging on your device.

If the local bridge is not running, the extension idles and performs no network activity beyond failed local requests.

## Permissions

| Permission | Why |
|---|---|
| `tabs` | Create/update/close agent tabs without using your active tab |
| `tabGroups` | Create and manage the Hermes Agent Tab Group |
| `storage` | Save settings (bridge URL, group title/color) on device |
| Host access to `127.0.0.1:19876` / `localhost:19876` | Talk to the local companion bridge only |

## Data retention

Settings are stored in Chrome’s local extension storage on your device. Session group IDs are stored in session storage and cleared when the group is stopped or the browser session ends.

## Children

This extension is not directed at children under 13.

## Contact

For privacy questions about this extension package, contact the publisher listed on the Chrome Web Store listing for **Hermes Agent Tab Group**.

## Changes

We may update this policy when the extension’s local behavior changes. The “Last updated” date above will change accordingly.
