import sqlite3
import random
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from FinMind.data import DataLoader

sys.stdout.reconfigure(encoding='utf-8')

DB_PATH = "taiwan_stock.db"
API_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiYWxiZXJ0MDkxNyIsImVtYWlsIjoiYWxiZXJ0MDkxN0BnbWFpbC5jb20iLCJ0b2tlbl92ZXJzaW9uIjowfQ.NigTcrEmzoH4Ntj3RDzfcRCT2a397hsERMydNZuy05c"

def main():
    print("=" * 60)
    print("🔎 開始進行信用交易資料 100 檔隨機抽樣比對...")
    print("=" * 60)
    
    # 1. 連接資料庫並選出所有有交易量且代號為 4 碼的股票清單
    conn = sqlite3.connect(DB_PATH)
    try:
        stocks_df = pd.read_sql_query(
            "SELECT DISTINCT stock_id FROM daily_chips WHERE volume > 0 AND length(stock_id) = 4", conn
        )
    finally:
        conn.close()
        
    all_stocks = stocks_df["stock_id"].tolist()
    if not all_stocks:
        print("[ERROR] 資料庫中無符合條件的股票！")
        return
        
    print(f"[INFO] 全市場共有 {len(all_stocks)} 檔普通股。")
    
    # 2. 隨機選出 100 檔
    sample_stocks = random.sample(all_stocks, min(100, len(all_stocks)))
    print(f"[INFO] 隨機選出 {len(sample_stocks)} 檔進行比對...")
    
    # 3. 初始化 FinMind DataLoader
    api = DataLoader()
    api.login_by_token(api_token=API_TOKEN)
    
    total_compared = 0
    margin_matched = 0
    short_matched = 0
    
    t_start = datetime.now()
    
    for idx, sid in enumerate(sample_stocks):
        # 取得資料庫中該股最新 120 筆資料的日期範圍
        conn = sqlite3.connect(DB_PATH)
        try:
            db_df = pd.read_sql_query(
                "SELECT date, margin_purchase_balance, short_sale_balance FROM daily_chips "
                "WHERE stock_id = ? AND volume > 0 ORDER BY date DESC LIMIT 120",
                conn, params=(sid,)
            )
        finally:
            conn.close()
            
        if db_df.empty:
            continue
            
        # 取得日期範圍
        min_date = db_df["date"].min()
        max_date = db_df["date"].max()
        
        # 呼叫 FinMind API 獲取官方資料
        try:
            api_df = api.taiwan_stock_margin_purchase_short_sale(
                stock_id=sid,
                start_date=min_date,
                end_date=max_date
            )
        except Exception as e:
            print(f"[{idx+1}/100] ⚠️ 股票 {sid} API 讀取失敗: {e}")
            continue
            
        if api_df is None or api_df.empty:
            print(f"[{idx+1}/100] ⚠️ 股票 {sid} API 未回傳資料。")
            continue
            
        # 合併比對
        # API 欄位：date, MarginPurchaseTodayBalance, ShortSaleTodayBalance
        api_df = api_df.rename(columns={
            "MarginPurchaseTodayBalance": "api_margin",
            "ShortSaleTodayBalance": "api_short"
        })[["date", "api_margin", "api_short"]]
        
        merged = pd.merge(db_df, api_df, on="date", how="inner")
        
        if merged.empty:
            continue
            
        # 進行比對
        merged["margin_ok"] = np.isclose(merged["margin_purchase_balance"], merged["api_margin"])
        merged["short_ok"] = np.isclose(merged["short_sale_balance"], merged["api_short"])
        
        matches_margin = merged["margin_ok"].sum()
        matches_short = merged["short_ok"].sum()
        compared_count = len(merged)
        
        total_compared += compared_count
        margin_matched += matches_margin
        short_matched += matches_short
        
        margin_rate = (matches_margin / compared_count) * 100
        short_rate = (matches_short / compared_count) * 100
        
        print(f"[{idx+1}/100] 股票 {sid} | 比對數: {compared_count:3d} | 融資一致率: {margin_rate:6.2f}% | 融券一致率: {short_rate:6.2f}%")
        
    print("\n" + "=" * 60)
    print("📊 信用交易資料抽樣比對總結")
    print(f"比對總筆數: {total_compared} 筆")
    if total_compared > 0:
        print(f"融資餘額一致率: {margin_matched / total_compared * 100:.2f}% ({margin_matched}/{total_compared})")
        print(f"融券餘額一致率: {short_matched / total_compared * 100:.2f}% ({short_matched}/{total_compared})")
    else:
        print("沒有比對到任何資料。")
    print(f"總耗時: {(datetime.now() - t_start).total_seconds():.1f} 秒")
    print("=" * 60)

if __name__ == "__main__":
    main()
