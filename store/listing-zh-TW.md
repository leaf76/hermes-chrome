# Chrome 線上應用程式商店文案（繁中）

## 名稱
Hermes Chrome

## 簡短說明
讓 Hermes／本機 AI agent 更好操作 Chrome：獨立工作區、少搶焦點、只連本機 CLI。

## 詳細說明
Hermes Chrome 是本機 companion：讓 AI agent 在「你的真實 Chrome」上工作，同時盡量不打斷你正在看的分頁。

產品目標是 **agent 友善的 Chrome 操作**，不只綁死在單一功能。目前預設用分頁群組當工作區隔離，之後可擴充更多操作能力。

**目前功能**
• 建立專用 Chrome 分頁群組工作區（預設標題 Hermes，可調）
• 新分頁預設 active:false，降低搶焦點
• 只連本機 bridge（127.0.0.1:19876）
• Popup 顯示 bridge 狀態與工作區分頁

**隱私**
無雲端帳號、無分析追蹤；不會把瀏覽資料送到遠端，只與你本機 bridge 通訊。

**注意**
本擴充本身不執行 AI 模型，是本機 agent 工作流的瀏覽器端元件。
