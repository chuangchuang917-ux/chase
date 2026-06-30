import sqlite3
import pandas as pd
import requests
import json
import time
import sys
import os
import numpy as np

# Set standard output encoding to UTF-8
sys.stdout.reconfigure(encoding='utf-8')

SUPABASE_URL = "https://xjalllcvwbgnxwcruhzz.supabase.co"
SUPABASE_KEY = "sb_publishable_4jXrUcO-DXpwGu4QklflXg_v7w4IYNt"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "taiwan_stock.db")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates"
}

def calculate_consecutive_growth(series):
    counts = []
    current_count = 0
    for val in series:
        if val:
            current_count += 1
        else:
            current_count = 0
        counts.append(current_count)
    return counts

def get_consec_days(series):
    consec = []
    current_count = 0
    current_sign = 0
    for val in series:
        if val > 0.00001:
            sign = 1
        elif val < -0.00001:
            sign = -1
        else:
            sign = 0
            
        if sign == 0:
            current_count = 0
            current_sign = 0
        elif sign == current_sign:
            current_count += sign
        else:
            current_sign = sign
            current_count = sign
        consec.append(current_count)
    return consec

def sync_data_bulk():
    print("[INFO] 連接 SQLite 資料庫並載入完整資料...")
    conn = sqlite3.connect(DB_PATH)
    try:
        # 1. 載入 daily_chips 完整資料
        df = pd.read_sql_query(
            "SELECT * FROM daily_chips ORDER BY stock_id ASC, date ASC", conn
        )
        if df.empty:
            print("[ERROR] daily_chips 資料表為空！")
            return
            
        print(f"[INFO] 成功載入 {len(df)} 筆日資料。開始進行策略滾動指標計算...")
        
        # 2. 進行策略滾動指標計算 (比照 strategy.py，但在全資料集上大量加速)
        grouped = df.groupby("stock_id")
        
        df["foreign_20d"] = grouped["foreign_buy_shares"].rolling(window=20, min_periods=1).sum().reset_index(level=0, drop=True)
        df["trust_20d"] = grouped["trust_buy_shares"].rolling(window=20, min_periods=1).sum().reset_index(level=0, drop=True)
        df["foreign_60d"] = grouped["foreign_buy_shares"].rolling(window=60, min_periods=1).sum().reset_index(level=0, drop=True)
        df["trust_60d"] = grouped["trust_buy_shares"].rolling(window=60, min_periods=1).sum().reset_index(level=0, drop=True)
        
        df["vol_20d"] = grouped["volume"].rolling(window=20, min_periods=1).sum().reset_index(level=0, drop=True)
        df["vol_60d"] = grouped["volume"].rolling(window=60, min_periods=1).sum().reset_index(level=0, drop=True)
        
        df["ratio_foreign_trust_20d"] = ((df["foreign_20d"] + df["trust_20d"]) / df["vol_20d"]).fillna(0.0).replace([np.inf, -np.inf], 0.0) * 100
        df["ratio_foreign_trust_20d_capital"] = ((df["foreign_20d"] + df["trust_20d"]) / df["shares_issued"]).fillna(0.0).replace([np.inf, -np.inf], 0.0) * 100
        df["ratio_foreign_trust_60d"] = ((df["foreign_60d"] + df["trust_60d"]) / df["vol_60d"]).fillna(0.0).replace([np.inf, -np.inf], 0.0) * 100
        df["ratio_foreign_trust_60d_capital"] = ((df["foreign_60d"] + df["trust_60d"]) / df["shares_issued"]).fillna(0.0).replace([np.inf, -np.inf], 0.0) * 100
        
        for w in [1, 5, 10, 20, 60, 120]:
            df[f"buy_{w}d"] = grouped["top15_buy_total"].rolling(window=w, min_periods=1).sum().reset_index(level=0, drop=True)
            df[f"sell_{w}d"] = grouped["top15_sell_total"].rolling(window=w, min_periods=1).sum().reset_index(level=0, drop=True)
            df[f"vol_{w}d"] = grouped["volume"].rolling(window=w, min_periods=1).sum().reset_index(level=0, drop=True)
            df[f"concentration_{w}d"] = ((df[f"buy_{w}d"] - df[f"sell_{w}d"]) / df[f"vol_{w}d"]).fillna(0.0).replace([np.inf, -np.inf], 0.0) * 100
            
        df["is_long_lock"] = (df["concentration_60d"] > 5.0) & (df["concentration_120d"] > 3.0)
        df["is_buy_accelerate"] = (df["concentration_5d"] > df["concentration_20d"]) & (df["concentration_20d"] > df["concentration_60d"])
        
        df["close_60d_ago"] = grouped["close"].shift(60)
        df["price_change_60d"] = ((df["close"] - df["close_60d_ago"]) / df["close_60d_ago"]).fillna(0.0).replace([np.inf, -np.inf], 0.0) * 100
        
        df["margin_purchase_change_20d"] = df["margin_purchase_balance"] - grouped["margin_purchase_balance"].shift(20).reset_index(level=0, drop=True)
        df["short_sale_change_20d"] = df["short_sale_balance"] - grouped["short_sale_balance"].shift(20).reset_index(level=0, drop=True)
        df["margin_purchase_change_20d"] = df["margin_purchase_change_20d"].fillna(0.0)
        df["short_sale_change_20d"] = df["short_sale_change_20d"].fillna(0.0)
        
        df["inst_daily"] = df["foreign_buy_shares"] + df["trust_buy_shares"]
        df["inst_consec_days"] = grouped["inst_daily"].transform(get_consec_days).astype(int)
        
        print("[INFO] 策略計算完成。載入並計算每週大戶持股資料...")
        
        # 3. 載入並計算每週大戶持股成長週數
        df_weekly = pd.read_sql_query(
            "SELECT date, stock_id, holder_over_1000, holder_over_400 FROM weekly_shareholders", conn
        )
        if df_weekly.empty:
            df["holder_over_1000"] = 0.0
            df["holder_over_400"] = 0.0
            df["holder_growth_weeks"] = 0
        else:
            df_weekly = df_weekly.sort_values(by=["stock_id", "date"])
            df_weekly["increased"] = df_weekly.groupby("stock_id")["holder_over_1000"].diff() > 0
            df_weekly["growth_weeks"] = df_weekly.groupby("stock_id")["increased"].transform(calculate_consecutive_growth)
            
            # 4. 用 pd.merge_asof 將週資料(最新的 w_date <= date) 整合進日資料中
            df["dt"] = pd.to_datetime(df["date"])
            df_weekly["dt"] = pd.to_datetime(df_weekly["date"])
            
            df = df.sort_values(by="dt")
            df_weekly = df_weekly.sort_values(by="dt")
            
            df = pd.merge_asof(
                df,
                df_weekly[["dt", "stock_id", "holder_over_1000", "holder_over_400", "growth_weeks"]],
                on="dt",
                by="stock_id",
                direction="backward"
            )
            
            # 補空值
            df["holder_over_1000"] = df["holder_over_1000"].fillna(0.0)
            df["holder_over_400"] = df["holder_over_400"].fillna(0.0)
            df["holder_growth_weeks"] = df["growth_weeks"].fillna(0).astype(int)
            
        # 5. 篩選與重整欄位
        output_cols = [
            "date", "stock_id", "stock_name", "close", "volume", "shares_issued",
            "ratio_foreign_trust_20d", "ratio_foreign_trust_20d_capital",
            "ratio_foreign_trust_60d", "ratio_foreign_trust_60d_capital",
            "concentration_1d", "concentration_5d", "concentration_10d",
            "concentration_20d", "concentration_60d", "concentration_120d",
            "is_long_lock", "is_buy_accelerate", "price_change_60d",
            "holder_over_1000", "holder_over_400",
            "margin_purchase_balance", "short_sale_balance",
            "margin_purchase_change_20d", "short_sale_change_20d",
            "vol_20d", "holder_growth_weeks", "inst_consec_days"
        ]
        
        # 補足缺失欄位
        for col in output_cols:
            if col not in df.columns:
                df[col] = 0.0 if col != "is_long_lock" and col != "is_buy_accelerate" else False
                
        df_result = df[output_cols].copy()
        
        # 轉換為字典清單並上傳
        records = df_result.to_dict(orient="records")
        total_records = len(records)
        print(f"[INFO] 計算與合併完成！準備上傳共 {total_records} 筆資料至 Supabase...")
        
        # 分批上傳 (批次大小 1000 提升頻寬效率)
        batch_size = 1000
        start_upload_time = time.time()
        for idx in range(0, total_records, batch_size):
            batch = records[idx:idx+batch_size]
            
            # 處理 NaN / inf
            for r in batch:
                for k, v in list(r.items()):
                    if pd.isna(v) or v == float('inf') or v == float('-inf'):
                        r[k] = None
                        
            url = f"{SUPABASE_URL}/rest/v1/chase_strategy_results"
            response = requests.post(url, headers=HEADERS, json=batch)
            if response.status_code not in (200, 201):
                # 如果因為 inst_consec_days 欄位不存在而失敗，嘗試排除該欄位後重試
                if "inst_consec_days" in response.text:
                    print("[WARN] Supabase 資料表缺少 'inst_consec_days' 欄位，排除該欄位並重試...")
                    for r in batch:
                        r.pop("inst_consec_days", None)
                    response = requests.post(url, headers=HEADERS, json=batch)
                
                if response.status_code not in (200, 201):
                    print(f"[ERROR] 上傳失敗 (第 {idx} 筆到第 {idx+batch_size} 筆)：{response.text}")
                    response.raise_for_status()
                
            if idx % 10000 == 0 or idx + batch_size >= total_records:
                elapsed = time.time() - start_upload_time
                speed = idx / elapsed if elapsed > 0 else 0
                progress_pct = (idx / total_records) * 100
                print(f"  [進度] 已成功同步 {idx}/{total_records} 筆 ({progress_pct:.1f}%) | 速度: {speed:.1f} 筆/秒")
                
        print(f"[SUCCESS] 所有數據順利批次上傳完畢！")
        
    finally:
        conn.close()

if __name__ == "__main__":
    start_time = time.time()
    try:
        sync_data_bulk()
        print(f"[SUCCESS] 全量歷史資料已成功同步至 Supabase！總耗時 {time.time() - start_time:.2f} 秒。")
    except Exception as e:
        print(f"[ERROR] 同步失敗: {e}")
