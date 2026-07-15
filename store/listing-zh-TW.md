# Chrome 線上應用程式商店文案（繁中）

## 名稱
Hermes Agent Tab Group

## 簡短說明
為 Hermes／本機 AI agent 建立專用 Chrome 分頁群組，不佔用你正在看的分頁。

## 詳細說明
Hermes Agent Tab Group 讓本機 AI agent 擁有真正的 Chrome「分頁群組」工作區：在背景開頁、導覽、收尾，盡量不搶你目前正在使用的分頁。

**功能**
• 建立名為 Hermes Agent 的原生分頁群組（顏色可調）
• 新分頁預設 active:false，降低搶焦點
• 只連本機 bridge（127.0.0.1:19876）
• Popup 顯示 bridge 狀態與群組分頁

**適用**
使用 Hermes Agent 或其他本機 CLI、需要沿用 Chrome 既有登入 cookie，又希望 agent 分頁與日常瀏覽分開的開發者。

**隱私**
無雲端帳號、無分析追蹤；不會把瀏覽資料送到遠端伺服器，只與你本機執行的 bridge 通訊。

**注意**
本擴充本身不執行 AI 模型，是本機 agent 工作流的瀏覽器端元件。
