# Hermes Chrome — 使用教學

**適用任何網站。** Hermes Chrome 是本機 agent companion，讓 AI agent / CLI 操作**你日常的 Chrome**（開頁、列分頁、截圖、簡單 DOM），盡量不搶你正在看的分頁焦點。

- 倉庫：[github.com/leaf76/hermes-chrome](https://github.com/leaf76/hermes-chrome)
- 隱私：[privacy-policy.md](./privacy-policy.md)
- English: [GUIDE.md](./GUIDE.md)

---

## 需要兩半

**只從 Chrome 線上應用程式商店安裝 extension 不夠。**

| 半邊 | 角色 |
|------|------|
| **Companion**（本機端） | 本機 bridge `127.0.0.1:19876` + Native Messaging host + token — 從此 repo **一次**安裝 |
| **Extension**（瀏覽器端） | 工作區分頁、截圖、輕量 DOM — 只跟本機 bridge 通訊 |

**就緒** = popup 顯示 Bridge **online** + Auth **ready**，且 `hermes-chrome.sh --json ping` 成功。

若 popup 顯示 **Setup required**，代表還沒裝 companion 或 bridge 沒起來 — 照 popup 上的指令（或下方第 1 步）即可。**只裝商店版時出現這個畫面是正常的。**

---

## 它是什麼 / 不是什麼

| 是 | 不是 |
|----|------|
| **本機** bridge + Chrome extension | 雲端瀏覽器 / 遠端 VPS 瀏覽器 |
| **不綁站點** — 任何你開的 `http(s)` | 寫死某一個產品或某一個網站 |
| CLI、Grok、Cursor、Claude Desktop… **共用** | 只給 Grok 用 |
| **兩半**產品（companion + extension） | 只裝 extension 就能用 |

Chrome 安全限制：光裝 extension **不能**對外開控制 port。請**先**安裝 companion（Native Messaging host + 本機 bridge）。之後點 extension icon 就會維持 bridge 可用。

---

## 一次設定

### 1. 安裝 companion（每台機器一次）— 先做這步

```bash
# macOS / Linux
./scripts/hermes-chrome.sh install-for-agent

# Windows PowerShell
powershell -ExecutionPolicy Bypass -File .\scripts\install-windows.ps1
```

會安裝：

1. 本機 bridge `127.0.0.1:19876`（盡量登入自啟）
2. Chrome **Native Messaging** host：`com.leaf76.hermes_chrome`
3. 若偵測到 agent 設定目錄，可選註冊 MCP（例如 Grok）

需要系統上有真的 **Python 3**（不要用 Windows Store 假 python）。

### 2. 安裝 / 啟用 extension

- Chrome Web Store **或** Load unpacked → `extension/`（v1.7+）
- 需要時允許 **nativeMessaging**
- **重新載入** extension → **點一下 icon**
- 等 auto-pair，或按 popup 的 **Pair**

正常：Bridge **online**、Auth **ready**。

### 3. 接上你的 agent（可選）

同一支 MCP 給很多 client：

```text
python /path/to/hermes-chrome/mcp_server.py
```

或只用 CLI（不用 MCP）：

```bash
./scripts/hermes-chrome.sh --json ping
./scripts/hermes-chrome.sh open 'https://example.com/'
```

---

## 日常用法 — 任意頁面

以下都是**通用**範例，URL 請換成你要的。

### 開啟頁面（Agent 工作區 Tab Group）

```bash
./scripts/hermes-chrome.sh start 'https://example.com/'
./scripts/hermes-chrome.sh open 'https://github.com/'
./scripts/hermes-chrome.sh new-tab 'https://news.ycombinator.com/'
```

MCP：`hermes_chrome_open`，`{ "url": "https://example.com/" }`。

### 列出分頁

```bash
./scripts/hermes-chrome.sh list-tabs
./scripts/hermes-chrome.sh list-tabs --all
./scripts/hermes-chrome.sh list-tabs --url github.com
```

### 截圖

```bash
# 預設：Hermes 工作區內的分頁
./scripts/hermes-chrome.sh capture --prefer auto --out /tmp/page.png

# 目前焦點分頁（需允許，隱私 opt-in）
./scripts/hermes-chrome.sh capture --prefer active --out /tmp/active.png
```

也可用 `tabId` / `urlIncludes` / `titleIncludes`（JSON API）。

### 簡單 DOM

```bash
./scripts/hermes-chrome.sh eval --tab-id 123 --expr 'document.title'
./scripts/hermes-chrome.sh click --tab-id 123 --selector 'button.submit'
./scripts/hermes-chrome.sh type --tab-id 123 --selector 'input#q' --text 'hello'
```

---

## 架構（簡）

```text
Agent（CLI / MCP / HTTP）
        │
        ▼
  bridge.py  127.0.0.1:19876
        ▲
        │ long-poll
  Hermes Chrome extension
        │ connectNative（可自動起 bridge）
        ▼
  native_host  com.leaf76.hermes_chrome
```

Chrome、bridge、agent 必須在**同一台機器**的 localhost。

---

## 選用小工具（非主線）

`capture --prefer gc|nq`、`list-tv` 等是**可選 finder**（例如某些圖表工作流），不是產品限制。一般用途請用 `open <url>`、`tabId`、`urlIncludes`、工作區截圖。

---

## 疑難排解

| 現象 | 試試 |
|------|------|
| Bridge offline | 跑過 `install-for-agent`；點 icon；檢查 Python |
| Native host not found | 重跑 `install-native-host` / installer；reload extension |
| 要 Pair | popup → Pair，或 `pair-open` 再 Pair |
| 看不到分頁 | 不在 Hermes group → Options 允許跨 workspace，或把分頁移進 group |
| MCP 沒工具 | 註冊 `mcp_server.py` 後**重開** agent session |

---

## 安全

- Bridge 只綁 **127.0.0.1**；預設有 token（`~/.hermes/run/hermes-chrome/bridge.env`）
- Token 等同瀏覽器 session 密碼
- `list-tabs` 預設只列工作區
- 本專案不做雲端帳號、不做 analytics

---

## Extension 內教學

Popup → **Guide**，或  
`chrome-extension://<id>/help.html`（離線同一份說明）。
