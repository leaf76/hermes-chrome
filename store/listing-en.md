# Chrome Web Store listing (English)

## Name
Hermes Chrome

## Short description (≤132 chars)
Make Chrome easy for Hermes & local AI agents—dedicated workspace, less focus-stealing, local CLI only.

## Detailed description

Hermes Chrome is a local companion that helps AI agents (Hermes and other CLIs) operate your real Chrome safely.

It is not limited to a single UI trick: the goal is **agent-friendly Chrome control**—keep your active browsing, reuse cookies/SSO when needed, and give the agent a clear workspace.

**What it does today**
• Creates a dedicated Chrome Tab Group workspace (default title “Hermes”, configurable)
• Opens/updates agent tabs with active:false to reduce focus stealing
• Talks only to a local companion bridge on 127.0.0.1:19876
• Optional local tab capture (PNG stays on-device) and light DOM helpers for agent workflows
• Popup shows bridge online/offline, extension version, and current agent workspace tabs

**Who it is for**
Developers using Hermes Agent or other local CLIs that need authenticated browser flows while keeping personal browsing separate from agent work.

**How to use**
1. Install this extension
2. Run the local bridge from the hermes-chrome repo (`bridge.py` / `hermes-chrome.sh`; macOS may use launchd)
3. Click the extension icon once so polling starts
4. From your terminal: ping / start / open / new-tab / list-tabs / navigate / status / stop / capture

**Privacy**
No cloud account. No analytics. The extension does not send browsing data to remote servers—only to a bridge process you run locally.

**Note**
This extension alone does not run an AI model. It is the browser half of a local agent workflow. Tab Groups are the default isolation mechanism; the product scope is broader agent Chrome control.

## Category
Productivity / Developer Tools

## Language
English

## Single purpose statement (for review)
Provide a local CLI/agent companion that operates Chrome in a dedicated workspace without hijacking the user’s active tab.

## Permission justifications

**tabs**  
Create, navigate, and close tabs for the agent workspace without relying on the user’s currently active tab when possible.

**tabGroups**  
Create and maintain a native Chrome Tab Group as the default agent workspace isolation mechanism.

**storage**  
Store on-device settings (local bridge URL, workspace title/color, polling enabled).

**Host permission http://127.0.0.1:19876/* and http://localhost:19876/***  
Long-poll a user-run local companion bridge that queues agent commands. No remote hosts.

**Host permission &lt;all_urls&gt;**  
Required by Chrome for optional `tabs.captureVisibleTab` when the local CLI requests a tab snapshot (any normal page). Triggered only by local bridge commands; PNG stays on-device. Not limited to a single third-party site.

**scripting**  
Optional on-device helpers for agent workflows (e.g. read title / light DOM ops) only when the local bridge issues a command; results stay on-device.

**alarms**  
Wake the MV3 service worker so local bridge polling can continue; no ads or cloud jobs.
