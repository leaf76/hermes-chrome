# Chrome Web Store listing (English)

## Name
Hermes Chrome

## Short description (≤132 chars)
Needs one-time local companion (GitHub). Agent tabs, capture, light automation; less focus-steal.

## Detailed description

Hermes Chrome is a **local agent companion** so AI agents and CLIs can operate your real Chrome on **any website**—without hijacking the tab you are using.

**Important: two parts required.** The Chrome Web Store package is only the **browser half**. Chrome security does not let an extension open a control port by itself. You must also install the **machine half** (companion) once from GitHub: local bridge on 127.0.0.1 + Native Messaging host + auth token. Extension alone cannot control Chrome for agents.

It is **not locked to one product or domain**. Open GitHub, docs, dashboards, news, charts—any http(s) URL.

**What it does**
• Dedicated Chrome Tab Group workspace (default title “Hermes”, configurable)
• Open / navigate agent tabs with active:false to reduce focus stealing
• Talks only to a local companion bridge on 127.0.0.1:19876
• Native Messaging host so the bridge can auto-start when you click the icon
• Tab capture (PNG stays on-device) and light DOM helpers for agent workflows
• Popup status + offline Guide; offline popup shows Setup required when companion is missing

**Who it is for**
Developers using Hermes Agent, Grok, Cursor, Claude Desktop, or any local CLI that needs authenticated browser flows while keeping personal browsing separate from agent work.

**How to use (order matters)**
1. One-time companion from github.com/leaf76/hermes-chrome — run `install-for-agent` (macOS/Linux) or `scripts/install-windows.ps1` (Windows). Needs real Python 3.
2. Install this extension (v1.7+)
3. Reload extension, click the icon once, Pair if needed (popup: Bridge online, Auth ready)
4. From your agent/CLI: open any URL, list-tabs, capture, eval/click/type
5. Full guide: extension popup → Guide, or docs/GUIDE.md on GitHub

**Ready check**
• Popup: Bridge online + Auth ready
• CLI: `hermes-chrome.sh --json ping` succeeds

**Privacy**
No cloud account. No analytics. The extension does not send browsing data to remote servers—only to a bridge process you run locally.

**Note**
This extension alone does not run an AI model and does not complete setup by itself. It is the browser half of a local agent workflow.

## Category
Productivity / Developer Tools

## Language
English
