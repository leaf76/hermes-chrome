# Chrome Web Store listing (English)

## Name
Hermes Agent Tab Group

## Short description (132 chars max)
Dedicated Chrome Tab Group for Hermes & local AI agents—work in the background without hijacking your active tab.

## Detailed description

Hermes Agent Tab Group gives local AI agents a real Chrome Tab Group to work in—similar in spirit to how modern coding agents isolate browser work—without taking over the tab you are reading.

**What it does**
• Creates a native Tab Group titled “Hermes Agent” (color configurable)
• Opens and updates tabs with active:false to reduce focus stealing
• Talks only to a local companion bridge on 127.0.0.1:19876
• Status popup shows bridge online/offline and current group tabs

**Who it is for**
Developers using Hermes Agent or other local CLIs that need authenticated browser flows (cookies already in Chrome) while keeping research tabs separate from the agent’s workspace.

**How to use**
1. Install this extension
2. Run the local bridge that ships with Hermes (`bridge.py` / `daily-chrome-tabgroup.sh`)
3. Click the extension icon once so polling starts
4. From your terminal: start / open / new-tab / status / stop

**Privacy**
No cloud account. No analytics. The extension does not send your browsing data to remote servers—only to a bridge process you run locally.

**Note**
This extension alone does not run an AI model. It is the browser half of a local agent workflow.

## Category
Productivity / Developer Tools

## Language
English

## Single purpose statement (for review)
Provide a dedicated Chrome Tab Group controlled by a local agent bridge for Hermes/local automation.

## Permission justifications (paste into CWS)

**tabs**  
Required to create, navigate, and close tabs that belong to the agent workspace without altering the user’s currently active tab when possible.

**tabGroups**  
Required to create and update a native Chrome Tab Group that visually and operationally isolates agent tabs.

**storage**  
Required to store user settings (local bridge URL, group title, group color, polling enabled) on the device only.

**Host permission http://127.0.0.1:19876/* and http://localhost:19876/***  
Required to long-poll a user-run local companion bridge that queues agent commands. No remote hosts.
