# -*- coding: utf-8 -*-
import sqlite3
import requests
import io
import pandas as pd
import random
import sys
import urllib3

sys.stdout.reconfigure(encoding='utf-8')
urllib3.disable_warnings()

DB_PATH = "taiwan_stock.db"
HEADERS = {"User-Agent": "Mozilla/5.0"}
url_tdcc = "https://smart.tdcc.com.tw/opendata/getOD.ashx?id=1-5"

def main():
    print("============================================================")
    print("📊 開始從 TDCC 官網獲取最新股權分散 CSV...")
    print("============================================================")
    
    r = requests.get(url_tdcc, headers=HEADERS, verify=False, timeout=30)
    if r.status_code != 200 or len(r.text) < 10000:
        print(f"[ERROR] 無法取得 TDCC CSV！Status: {r.status_code}, Length: {len(r.text) if r.text else 0}")
        return
        
    print("  ✅ 成功下載 TDCC 集保 CSV。")
    
    # 解析 CSV
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = [c.strip() for c in df.columns]
    df["證券代號"] = df["證券代號"].astype(str).str.strip()
    df["持股分級"] = df["持股分級"].astype(int)
    df["占集保庫存數比例%"] = df["占集保庫存數比例%"].astype(float)
    
    raw_date = str(df["資料日期"].iloc[0]).strip()
    target_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
    print(f"[INFO] 官網最新集保資料日期為：{target_date}")

    # 計算千張與四百張比例
    # 千張大戶: level = 15
    df_15 = df[df["持股分級"] == 15][["證券代號", "占集保庫存數比例%"]].copy()
    df_15 = df_15.rename(columns={"占集保庫存數比例%": "csv_over_1000", "證券代號": "stock_id"})
    
    # 四百張大戶: level between 12 and 15
    df_12_15 = df[df["持股分級"].between(12, 15)].groupby("證券代號")["占集保庫存數比例%"].sum().reset_index()
    df_12_15 = df_12_15.rename(columns={"占集保庫存數比例%": "csv_over_400", "證券代號": "stock_id"})
    
    df_csv = pd.merge(df_15, df_12_15, on="stock_id", how="outer").fillna(0.0)

    # 從 SQLite 讀取當天數據
    conn = sqlite3.connect(DB_PATH)
    df_db = pd.read_sql_query(
        "SELECT stock_id, holder_over_1000, holder_over_400 FROM weekly_shareholders WHERE date=?",
        conn, params=(target_date,)
    )
    conn.close()

    if df_db.empty:
        print(f"[ERROR] SQLite 資料庫中找不到 {target_date} 的集保大戶資料！")
        return
        
    print(f"[INFO] SQLite 資料庫中共有 {len(df_db)} 檔股票的 {target_date} 集保紀錄。")

    # 隨機抽樣 100 檔
    sample_size = min(100, len(df_db))
    sample_stocks = df_db.sample(sample_size, random_state=42) # 隨機抽樣 100 檔
    
    # 合併比對
    df_merged = pd.merge(sample_stocks, df_csv, on="stock_id", how="inner")
    
    print("------------------------------------------------------------")
    print(f"{'股票代號':<10}{'DB千張%':<12}{'CSV千張%':<12}{'DB四百%':<12}{'CSV四百%':<12}{'完全一致':<10}")
    print("------------------------------------------------------------")

    matched_count = 0
    mismatched_count = 0
    
    for idx, row in df_merged.iterrows():
        sid = row["stock_id"]
        db_1000 = row["holder_over_1000"]
        db_400 = row["holder_over_400"]
        csv_1000 = row["csv_over_1000"]
        csv_400 = row["csv_over_400"]
        
        # 允許微小精確度差異 <= 0.001%
        match_1000 = (abs(db_1000 - csv_1000) <= 0.001)
        match_400 = (abs(db_400 - csv_400) <= 0.001)
        
        is_match = match_1000 and match_400
        
        if is_match:
            matched_count += 1
            status = "Yes"
        else:
            mismatched_count += 1
            status = f"No (Diff: {max(abs(db_1000-csv_1000), abs(db_400-csv_400)):.3f})"
            
        print(f"{sid:<10}{db_1000:<12.3f}{csv_1000:<12.3f}{db_400:<12.3f}{csv_400:<12.3f}{status:<10}")

    print("============================================================")
    print(f"📊 {target_date} 集保大戶隨機抽樣比對報告")
    print(f"  總抽樣檔數: {sample_size} 檔")
    print(f"  完全一致檔數: {matched_count} 檔")
    print(f"  不一致檔數: {mismatched_count} 檔")
    if sample_size > 0:
        error_rate = (mismatched_count / sample_size) * 100
        print(f"  集保大戶資料比對錯誤率: {error_rate:.2f}%")
        print(f"  資料完全匹配率 (Accuracy): {100 - error_rate:.2f}%")
    print("============================================================")

if __name__ == "__main__":
    main()
