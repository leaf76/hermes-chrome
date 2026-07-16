# Chrome Web Store — paste pack (Hermes Chrome v1.0.0)

Use with zip: `hermes-chrome-v1.0.0.zip`  
Privacy: https://leaf76.github.io/hermes-chrome/privacy-policy  
Support / homepage: https://github.com/leaf76/hermes-chrome

## Product

| Field | Value |
|-------|--------|
| **Name** | Hermes Chrome |
| **Summary** (≤132 chars) | Make Chrome easy for Hermes & local AI agents—dedicated workspace, less focus-stealing, local CLI only. |
| **Category** | Productivity *(or Developer Tools if offered)* |
| **Language** | English (Primary). Optional: Chinese (Traditional) |

### Detailed description (English)

```
Hermes Chrome is a local companion that helps AI agents (Hermes and other CLIs) operate your real Chrome safely.

It is not limited to a single UI trick: the goal is agent-friendly Chrome control—keep your active browsing, reuse cookies/SSO when needed, and give the agent a clear workspace.

What it does today
• Creates a dedicated Chrome Tab Group workspace (default title “Hermes”, configurable)
• Opens/updates agent tabs with active:false to reduce focus stealing
• Talks only to a local companion bridge on 127.0.0.1:19876
• Popup shows bridge online/offline and current agent workspace tabs

Who it is for
Developers using Hermes Agent or other local CLIs that need authenticated browser flows while keeping personal browsing separate from agent work.

How to use
1. Install this extension
2. Run the local bridge from the hermes-chrome repo (bridge.py / hermes-chrome.sh)
3. Click the extension icon once so polling starts
4. From your terminal: start / open / new-tab / status / stop

Privacy
No cloud account. No analytics. The extension does not send browsing data to remote servers—only to a bridge process you run locally.

Note
This extension alone does not run an AI model. It is the browser half of a local agent workflow. Tab Groups are the default isolation mechanism; the product scope is broader agent Chrome control.

Repo: https://github.com/leaf76/hermes-chrome
```

### Detailed description (繁中，選填)

```
Hermes Chrome 是本機 companion：讓 AI agent 在「你的真實 Chrome」上工作，同時盡量不打斷你正在看的分頁。

產品目標是 agent 友善的 Chrome 操作，不只綁死在單一功能。目前預設用分頁群組當工作區隔離，之後可擴充更多操作能力。

目前功能
• 建立專用 Chrome 分頁群組工作區（預設標題 Hermes，可調）
• 新分頁預設 active:false，降低搶焦點
• 只連本機 bridge（127.0.0.1:19876）
• Popup 顯示 bridge 狀態與工作區分頁

隱私：無雲端帳號、無分析追蹤；不會把瀏覽資料送到遠端，只與你本機 bridge 通訊。

注意：本擴充本身不執行 AI 模型，是本機 agent 工作流的瀏覽器端元件。

Repo: https://github.com/leaf76/hermes-chrome
```

## Graphic assets (upload these)

**Important (CWS):** JPEG or **24-bit PNG without alpha**. Files below are RGB-only.

| Asset | File (prefer PNG or JPG) |
|-------|--------------------------|
| Store icon 128×128 | `../icons/icon128.png` |
| Screenshot 1 | `screenshots/screenshot-1-workspace-1280x800.png` |
| Screenshot 2 | `screenshots/screenshot-2-cli-bridge-1280x800.png` |
| Screenshot 3 | `screenshots/screenshot-3-privacy-local-1280x800.png` |
| Screenshot 4 (optional) | `screenshots/screenshot-4-stay-in-flow-1280x800.png` |
| Screenshot 5 (optional) | `screenshots/screenshot-5-permissions-1280x800.png` |
| Small promo 440×280 | `screenshots/promo-small-440x280.png` *(or `.jpg`)* |
| Marquee 1400×560 | `screenshots/marquee-1400x560.png` *(or `.jpg`)* |

Suggested upload order for screenshots: 1 → 2 → 3 (then 4/5 if you want all five).

## Privacy & single purpose

| Field | Value |
|-------|--------|
| **Privacy policy URL** | https://leaf76.github.io/hermes-chrome/privacy-policy |
| **Single purpose** | Provide a local CLI/agent companion that operates Chrome in a dedicated workspace without hijacking the user’s active tab. |
| **Data usage** | Does not sell or transfer user data to third parties. Does not use remote code. |
| **Host permission justification** | (1) Localhost `127.0.0.1:19876` / `localhost:19876` — long-poll a user-run companion bridge. (2) `<all_urls>` — required by Chrome for optional `tabs.captureVisibleTab` when the local CLI asks to snapshot a tab (any normal page). Capture is triggered only by local bridge commands; PNG stays on-device. Not limited to any single third-party site. |
| **tabs** | Create/update/close agent workspace tabs without relying on the user’s active tab when possible. |
| **tabGroups** | Default workspace isolation via a native Chrome Tab Group. |
| **storage** | On-device settings only (bridge URL, title/color, polling). |
| **alarms** | Wake MV3 service worker to continue local bridge polling. |

## Distribution

| Field | Suggested |
|-------|-----------|
| Visibility | **Unlisted** first soak, then Public — or Public if you want review now |
| Regions | All regions |
| Pricing | Free |

## Notes for reviewers

```
Hermes Chrome is a local agent companion.
Host permissions: localhost bridge + http(s) for optional local tab capture (any site the CLI requests; not site-locked).
1. Install extension
2. python3 bridge.py  (listen 127.0.0.1:19876)
3. Click extension icon
4. Use CLI: hermes-chrome.sh ping / start / stop
Repo: https://github.com/leaf76/hermes-chrome
Privacy: https://leaf76.github.io/hermes-chrome/privacy-policy
```

## Your last step

After fields + images + zip are set: click **Submit for review**.
