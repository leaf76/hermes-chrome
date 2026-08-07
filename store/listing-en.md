# Chrome Web Store listing (English)

## Name
Hermes Chrome

## Short description (≤132 chars)
Local AI agent companion for any website—workspace tabs, capture, light automation; less focus-stealing.

## Detailed description

Hermes Chrome is a **local companion** so AI agents and CLIs can operate your real Chrome on **any website**—without hijacking the tab you are using.

It is **not locked to one product or domain**. Open GitHub, docs, dashboards, news, charts—any http(s) URL.

**What it does**
• Dedicated Chrome Tab Group workspace (default title “Hermes”, configurable)
• Open / navigate agent tabs with active:false to reduce focus stealing
• Talks only to a local companion bridge on 127.0.0.1:19876
• Optional Native Messaging host so the bridge can auto-start when you click the icon
• Tab capture (PNG stays on-device) and light DOM helpers for agent workflows
• Popup status + offline Guide (English / 繁中)

**Who it is for**
Developers using Hermes Agent, Grok, Cursor, Claude Desktop, or any local CLI that needs authenticated browser flows while keeping personal browsing separate from agent work.

**How to use**
1. Install this extension (v1.7+)
2. One-time: install the companion from github.com/leaf76/hermes-chrome (`install-for-agent` / Windows installer) — extension alone cannot open a control port
3. Reload extension, click the icon once, Pair if needed
4. From your agent/CLI: open any URL, list-tabs, capture, eval/click/type
5. Full guide: extension popup → Guide, or docs/GUIDE.md on GitHub

**Privacy**
No cloud account. No analytics. The extension does not send browsing data to remote servers—only to a bridge process you run locally.

**Note**
This extension alone does not run an AI model. It is the browser half of a local agent workflow.

## Category
Productivity / Developer Tools

## Language
English
