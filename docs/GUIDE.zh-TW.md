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

### 更新

**擴充**由 Chrome 線上應用程式商店自動更新。

**Companion**（非 `--dev` 安裝）每天會從官方 GitHub remote **fast-forward** 更新。
關閉：`hermes-chrome self-update disable`。立刻更新：`hermes-chrome self-update now`。
兩半版本不一致時 popup 仍會提醒。

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

### 🤖 讓你的 AI 助理一鍵安裝（One-Shot Prompt）

若你正在使用 AI 輔助開發工具（如 Cursor、Claude Desktop、Windsurf、Roo Code、ChatGPT、Antigravity 等），可以直接將下方 Prompt 貼給你的 AI：

```text
請幫我在這台電腦上安裝並配置 Hermes Chrome companion，以便你（AI）可以操作我的日常 Chrome：
1. 偵測我的作業系統（macOS / Linux / Windows）。
2. 安裝 Companion：
   - macOS / Linux：執行 `curl -fsSL https://raw.githubusercontent.com/leaf76/hermes-chrome/main/scripts/install.sh | bash`
   - Windows：clone https://github.com/leaf76/hermes-chrome.git 並執行 `powershell -ExecutionPolicy Bypass -File .\scripts\install-windows.ps1`
3. 確保 `~/.local/bin` 已加入 PATH（若無，請將 `export PATH="$HOME/.local/bin:$PATH"` 寫入我的 shell rc 檔案）。
4. 執行 `hermes-chrome doctor` 驗證本地 Bridge 與 Companion 健康狀態。
5. 若我使用 Cursor、Claude Desktop 或 Grok，請確認 MCP 設定已註冊（可參考 `~/.hermes/run/hermes-chrome/mcp-snippet.json`）。
6. 最後提醒我到 Chrome 商店安裝 Hermes Chrome 擴充功能，並點擊一次圖示以完成配對。
```

### 1. 手動安裝 companion（每台機器一次）— 先做這步

**建議（不必先 clone）：**

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/leaf76/hermes-chrome/main/scripts/install.sh | bash
```

安裝位置：

| 路徑 | 用途 |
|------|------|
| `~/.hermes/hermes-chrome` | 程式（bridge、MCP、scripts、extension/） |
| `~/.hermes/run/hermes-chrome` | 執行期（token、pid、native host） |
| `~/.local/bin/hermes-chrome` | CLI |

**已有 git clone：**

```bash
./scripts/install.sh
./scripts/install.sh --dev   # 直接用這個 clone
```

**Windows PowerShell**（在 clone 目錄）：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-windows.ps1
```

會安裝：

1. 本機 bridge `127.0.0.1:19876`（盡量登入自啟）
2. Chrome **Native Messaging** host：`com.leaf76.hermes_chrome`
3. 可選 **MCP**（Grok / Cursor / Claude Desktop 有裝才寫設定）
4. PATH shim + `doctor` 健康檢查

需要 **Python 3.9+** 與 **git**。沒有 npm。

MCP 片段：`~/.hermes/run/hermes-chrome/mcp-snippet.json`

### 2. 安裝 / 啟用 extension

- [Chrome Web Store](https://chromewebstore.google.com/detail/hermes-chrome/mkoaoadlkijccmmbkioagnlngbbeocfa) **或** Load unpacked → `extension/`（v1.8.3）
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

### 用完關閉工作區（不是關 companion）

**Hermes Tab Group 用完可關。** 任務結束後關掉 agent 分頁或 stop 工作區即可；  
**本機 companion / bridge 請保留**，下次不用重裝、重 Pair。

```bash
# 關閉 agent 工作區分頁並清掉 group（CLI）
./scripts/hermes-chrome.sh stop

# 或在 Chrome 手動關分頁 / 整個 group — 一樣
```

MCP：`hermes_chrome_stop`（可選 `close_tabs: false` 只取消分組、不關分頁）。

**不要**為了清分頁去 uninstall companion 或移除 extension。

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
