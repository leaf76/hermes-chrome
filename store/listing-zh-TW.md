# Chrome 線上應用程式商店文案（繁中）

## 名稱
Hermes Chrome

## 簡短說明
需一次安裝本機 companion（GitHub）。Agent 分頁、截圖、輕量自動化，少搶焦點。

## 詳細說明
Hermes Chrome 是**本機 agent companion**：讓 AI agent / CLI 操作你真實的 Chrome，並盡量不打斷你正在看的分頁。

**重要：需要兩半。** 商店安裝的只是**瀏覽器端**。Chrome 安全模型不允許 extension 自行開控制 port。你還必須從 GitHub **一次**安裝**本機端 companion**（本機 bridge + Native Messaging host + token）。**光裝 extension 無法**讓 agent 控制 Chrome。

**不綁死單一網站或產品。** GitHub、文件、後台、新聞、圖表……Chrome 開得了的 URL 都能用。

**目前功能**
• 專用 Chrome 分頁群組工作區（預設標題 Hermes，可調）
• 新分頁預設 active:false，降低搶焦點
• 只連本機 bridge（127.0.0.1:19876）
• Native Messaging host：點 icon 可自動起 bridge
• 本機分頁截圖與輕量 DOM 輔助（PNG 只留在本機）
• Popup 狀態 + 離線教學（Guide）；未裝 companion 時顯示 Setup required

**適用對象**
使用 Hermes、Grok、Cursor、Claude Desktop 或其他本機 CLI、需要帶 cookie/SSO 瀏覽器流程的開發者。

**怎麼用（順序重要）**
1. 到 github.com/leaf76/hermes-chrome 做**一次** companion 安裝：`install-for-agent`（macOS/Linux）或 `scripts/install-windows.ps1`（Windows）。需要真的 Python 3。
2. 安裝本擴充（v1.7+）
3. 重新載入 extension、點一次 icon，需要時 Pair（popup：Bridge online、Auth ready）
4. 用 agent／CLI：開啟任意 URL、list-tabs、截圖、eval/click/type
5. 完整教學：popup → Guide，或 GitHub `docs/GUIDE.zh-TW.md`

**就緒檢查**
• Popup：Bridge online + Auth ready
• CLI：`hermes-chrome.sh --json ping` 成功

**隱私**
無雲端帳號、無分析追蹤；不會把瀏覽資料送到遠端，只與你本機 bridge 通訊。

**注意**
本擴充本身不執行 AI 模型，也**不能**單靠商店安裝完成設定。它是本機 agent 工作流的瀏覽器端元件。

## 分類
生產力 / 開發人員工具
