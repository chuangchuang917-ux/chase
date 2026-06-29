# 籌碼雷達系統：移轉至 chase 子資料夾計畫

本計畫將與本系統（老手自動化高階籌碼鎖碼雷達）相關的所有程式檔案與資料庫遷移至 `chase/` 子資料夾，以保持根目錄整潔，並在遷移後重新配置並重啟伺服器。

---

## 🛠️ 預計遷移檔案清單

以下與本專案相關的檔案將會被移動到 [chase](file:///c:/Users/alber/Desktop/antigravity/chase) 資料夾中：

*   **程式主體檔案**：
    *   [app.py](file:///c:/Users/alber/Desktop/antigravity/app.py) -> `chase/app.py`
    *   [crawler.py](file:///c:/Users/alber/Desktop/antigravity/crawler.py) -> `chase/crawler.py`
    *   [database.py](file:///c:/Users/alber/Desktop/antigravity/database.py) -> `chase/database.py`
    *   [strategy.py](file:///c:/Users/alber/Desktop/antigravity/strategy.py) -> `chase/strategy.py`
    *   [scrape_weekly_norway.py](file:///c:/Users/alber/Desktop/antigravity/scrape_weekly_norway.py) -> `chase/scrape_weekly_norway.py`
    *   [scrape_weekly_tdcc_robust.py](file:///c:/Users/alber/Desktop/antigravity/scrape_weekly_tdcc_robust.py) -> `chase/scrape_weekly_tdcc_robust.py`

*   **資料與設定檔**：
    *   [taiwan_stock.db](file:///c:/Users/alber/Desktop/antigravity/taiwan_stock.db) -> `chase/taiwan_stock.db`
    *   [column_config.json](file:///c:/Users/alber/Desktop/antigravity/column_config.json) -> `chase/column_config.json`

*   **爬蟲暫存與除錯檔案** (選用移轉/清理)：
    *   [goodinfo_debug.html](file:///c:/Users/alber/Desktop/antigravity/goodinfo_debug.html) -> `chase/goodinfo_debug.html`
    *   [goodinfo_rows.json](file:///c:/Users/alber/Desktop/antigravity/goodinfo_rows.json) -> `chase/goodinfo_rows.json`
    *   [norway_debug.html](file:///c:/Users/alber/Desktop/antigravity/norway_debug.html) -> `chase/norway_debug.html`
    *   [norway_rows.json](file:///c:/Users/alber/Desktop/antigravity/norway_rows.json) -> `chase/norway_rows.json`
    *   [tdcc_response_debug.html](file:///c:/Users/alber/Desktop/antigravity/tdcc_response_debug.html) -> `chase/tdcc_response_debug.html`

---

## 🚀 執行步驟

### 1. 停止目前的 Streamlit 伺服器
*   終止目前在背景運作的 `streamlit run app.py` 工作任務 (任務 ID：`25012c53-430d-4731-8c36-1e523bb8c51c/task-1577`)。

### 2. 移動檔案至 chase 資料夾
*   使用 PowerShell 指令將上述清單中的檔案移入 `c:\Users\alber\Desktop\antigravity\chase\`。

### 3. 重啟 Streamlit 伺服器
*   在 `c:\Users\alber\Desktop\antigravity\chase\` 工作目錄下重新啟動伺服器：
    `streamlit run app.py --browser.gatherUsageStats=false`

---

## 驗證與測試計畫

1. 檢查 `c:\Users\alber\Desktop\antigravity\chase\` 資料夾，確認所有檔案已被正確搬移。
2. 啟動並存取 `http://localhost:8501/`，確認網頁介面正常載入。
3. 驗證資料庫讀取是否正常（應能顯示最新交易日選股結果與個股診斷面板）。
4. 驗證 sidebar 的自訂欄位控制項與持久化功能是否依然正常運作。
