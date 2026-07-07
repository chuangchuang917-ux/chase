# Chase App — 工作交接文件

> 上次工作時間：2026-07-07 20:15（台灣時間）
> 狀態：所有法人機構最低門檻的 radio 按鈕已改為垂直排列並重新部署至 Cloud Run

---

## 一、本次工作摘要（0706 session）

### 已完成的工作

#### 1. 修正「千張大戶增幅顯示異常」Bug
**問題**：個股詳情卡片顯示「+1週 (+32.36%)」，但 32.36% 根本是持股比例本身，不是增幅。
**根本原因**：集保週資料每週五才完整公佈，若 GitHub Actions 在週中執行，當週比例已寫入但上週基準值被存成 0，導致「增幅 = 本週比例 - 0 = 本週比例」。
**修正方式**：在 app.py 和 app_mobile.py 中加入防禦邏輯：增幅超過 5% 判定為異常，改顯示 N/A。

---

#### 2. 設計並實作新的「固定視窗持股增幅」篩選系統

**舊設計（已移除）**：
- 篩選條件：連買幾週（1/2/3/4+週）
- 問題：容易歸零、受未完結週影響、無幅度感

**新設計（已實作）**：
- 篩選條件：觀察視窗（2週/3週/4週/8週）x 增幅門檻（不限/3%/5%/8%）
- 計算方式：holder_Nw_change = 本週 holder_over_1000 - N週前 holder_over_1000（純 diff，不受連買中斷影響）

---

#### 3. 更新程式碼（已 push 至 GitHub，最新 commit: 80253ee）

| 檔案 | 變更內容 |
|---|---|
| sync_to_supabase_bulk.py | 新增 holder_2w/3w/4w/8w_change 的計算和 output_cols |
| app.py | 更新篩選 UI（觀察視窗+增幅門檻）、個股詳情新增 2週/4週/8週淨增幅 |
| app_mobile.py | 修正 NameError: selected_window_col not defined、更新篩選 UI 和卡片 HTML |

---

#### 4. Supabase 資料庫欄位已新增

以下欄位已在 chase_strategy_results 資料表中建立：
- holder_2w_change FLOAT DEFAULT 0
- holder_3w_change FLOAT DEFAULT 0
- holder_4w_change FLOAT DEFAULT 0
- holder_8w_change FLOAT DEFAULT 0

---

## 二、0707 Session — 已完成所有交接任務

### [完成] 任務 1：Cloud Run 重新部署

- 進入 GCP Cloud Shell
- 切換專案至 chase-app-501611
- 執行 git pull origin master（拉取最新 commit 80253ee）
- 執行 gcloud run deploy chase-app --source . --region asia-east1 --allow-unauthenticated
- 部署成功，服務 URL：https://chase-app-281337033355.asia-east1.run.app

### [完成] 任務 2：填充新欄位歷史資料

- 在 Cloud Shell 設定環境變數（SUPABASE_URL 和 SUPABASE_KEY）
- 執行 python daily_update.py（2026-07-06 日期）
- 成功同步 1,971 筆策略結果至 Supabase

### [完成] 任務 3：驗證新功能

- 訪問 https://chase-app-281337033355.asia-east1.run.app
- 桌面版正常載入，顯示 1971 檔 2026-07-06 的資料
- 手機版 NameError 已修復，不再 crash

### [完成] 任務 5：調整法人買超比例按鈕為垂直排列

- 移除 `app.py` 中 `20日法人買超比` 及 `60日法人買超比` radio 元件的 `horizontal=True` 參數。
- 將最新程式碼 push 到 GitHub 倉庫（Commit: `6a2ba6f`）。
- 透過 Cloud Shell 完成新版 Cloud Run 部署。
- 已驗證：網頁版中「20日法人買超比」與「60日法人買超比」的 radio 按鈕已成功垂直排列，與「股票成交熱度門檻」風格一致。

---

## 三、尚未完成的工作（可選，未來再做）

### 任務 4（可選）：移除舊欄位

在確認新功能穩定後，可清理舊邏輯：

程式碼中待移除：
- app.py 中 add_growth_metrics() 函式（holder_growth_pct 已不再被 filter 使用）
- cached_run_chip_strategy() 中 weekly_trend_weeks 參數（目前 hardcode 為 0）
- sync_to_supabase_bulk.py 中 holder_growth_weeks 的計算邏輯

Supabase 中待移除（確認穩定後）：
ALTER TABLE chase_strategy_results
  DROP COLUMN IF EXISTS holder_growth_weeks;

---

## 四、關鍵技術細節

### 固定視窗計算邏輯（sync_to_supabase_bulk.py 約第 124 行）

for w in [2, 3, 4, 8]:
    df_weekly[f"holder_{w}w_change"] = df_weekly.groupby("stock_id")["holder_over_1000"].diff(w)

### 篩選邏輯位置
- 桌面版：app.py 約 1065-1075 行
- 手機版：app_mobile.py 約 688-695 行

### Supabase 連線資訊
- URL：https://xjalllcvwbgnxwcruhzz.supabase.co
- Key：已遮蔽 (請自環境變數或 Secrets 存取)

### GitHub Repo
- https://github.com/chuangchuang917-ux/chase.git
- 主分支：master
- 最新 commit：80253ee

### Cloud Run 服務
- 專案：chase-app-501611
- 區域：asia-east1
- 服務 URL：https://chase-app-281337033355.asia-east1.run.app
