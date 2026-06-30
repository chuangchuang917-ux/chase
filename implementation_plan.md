# 歷史 OTC (上櫃) 股籌碼與信用交易資料回溯計畫

## 1. 背景與現況分析
1. **資料缺失確認**：
   - 經分析本地 SQLite 資料庫（[taiwan_stock.db](file:///c:/Users/alber/Desktop/antigravity/chase/taiwan_stock.db)），在 **2026-06-19** 之前，共有 **894** 檔上櫃 (TPEx) 股票的 `foreign_buy_shares`（外資買賣超）與 `trust_buy_shares`（投信買賣超）完全為 `0.0`，且信用交易（融資融券）資料亦為 `0.0`。
   - **根因**：歷史回溯腳本 [backfill_market.py](file:///c:/Users/alber/Desktop/antigravity/chase/backfill_market.py) 中，對上櫃 (TPEx) 股票的法人買賣超與信用交易欄位採用了硬編碼 `0.0`，导致歷史資料存在大片空白。
2. **FinMind API 資料驗證**：
   - 經對比測試，FinMind 官方 API 確實擁有完整的 OTC 歷史法人與信用交易數據（例如 `8064` 東捷在 `2026-04-14` 的外資買賣超資料完備）。
3. **金鑰與額度**：
   - 官方金鑰存放於 [api_sources.md](file:///c:/Users/alber/Desktop/antigravity/chase/api_sources.md)。共有 3 個授權 Token，每 Token 每小時限制為 600 次請求，合計每小時可調用 **1,800** 次。
   - 補齊 894 檔股票在 `2025-12-01 ~ 2026-06-22` 區間的法人與信用交易數據，每檔需呼叫 2 次 API（一次法人，一次信用交易），共需呼叫 **1,788** 次。我們可以在一小時的限制額度內一次性完成全部補登。

---

## 2. 實作計畫

### 2.1 新建回溯更新腳本 `backfill_tpex_finmind.py`
我們將建立一個專用的非同步（Async）回溯腳本，利用現有的 3 組 FinMind 憑證輪轉，高效率地補齊這 894 檔上櫃股的歷史資料。
- **目標區間**：`2025-12-01` 至 `2026-06-22`。
- **目標欄位**：
  - `foreign_buy_shares` (外資買賣超張數，換算為張數單位)
  - `trust_buy_shares` (投信買賣超張數，換算為張數單位)
  - `margin_purchase_balance` (融資餘額)
  - `short_sale_balance` (融券餘額)
- **非同步與限速處理**：
  - 使用 `asyncio` 與 `aiohttp`，限制併發數以避免觸發 429 Rate Limit。
  - 三組 Token 輪流調用；若遇 429 則自動切換並安全等待。

### 2.2 更新 SQLite 資料庫
- 腳本將抓取到的資料透過 SQLite 的 `UPDATE` 指令，動態更新 `daily_chips` 中已存在但數值為 `0.0` 的列。
  ```sql
  UPDATE daily_chips 
  SET foreign_buy_shares = ?, trust_buy_shares = ?, margin_purchase_balance = ?, short_sale_balance = ? 
  WHERE date = ? AND stock_id = ?
  ```

### 2.3 雲端 Supabase 同步與滾動指標重算
由於法人欄位改變，所有的滾動指標（如 `ratio_foreign_trust_20d` 等）需要重新計算，並同步上傳至 Supabase。
- 補登完成後，我們將執行單日/批次同步腳本，將 `2025-12-01` 至 `2026-06-22` 期間所有已補齊上櫃股的日期重寫 upsert 至 Supabase 中。

---

## 3. 驗證與測試計畫

### 3.1 欄位正確性驗證
- 執行腳本後，隨機查詢 5-10 檔上櫃熱門股（如 8064 東捷、8048 德勝）在 2026-04-14 的 `foreign_buy_shares` 是否已被填入非零數值。
- 檢查 SQLite 資料庫中仍為 `0.0` 的上櫃股比例是否大幅降低。

### 3.2 線上系統驗證
- 同步上傳後，刷新網頁端並切換至 `2026-04-14`，檢查選股結果中的「20日法人佔量比」、「60日法人佔量比」等指標是否已不再全數為 `0.00%`。
