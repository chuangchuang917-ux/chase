# -*- coding: utf-8 -*-
import sqlite3
import random
import time
import sys
import pandas as pd
from FinMind.data import DataLoader

# 設定標準輸出為 UTF-8 以防 Windows 終端機編碼錯誤
sys.stdout.reconfigure(encoding='utf-8')

DB_PATH = "taiwan_stock.db"
TOKENS = [
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiYWxiZXJ0MDkxNyIsImVtYWlsIjoiYWxiZXJ0MDkxN0BnbWFpbC5jb20iLCJ0b2tlbl92ZXJzaW9uIjowfQ.NigTcrEmzoH4Ntj3RDzfcRCT2a397hsERMydNZuy05c",
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiY2h1YW5nY2h1YW5nOTE3QGdtYWlsLmNvbSIsImVtYWlsIjoiY2h1YW5nY2h1YW5nOTE3QGdtYWlsLmNvbSIsInRva2VuX3ZlcnNpb24iOjF9.SlWtLQstQJGUCVKl42NxUG8wfqNt6tWD-reyP3xcyBY",
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiY2h1YW5na3VuNjlAZ21haWwuY29tIiwiZW1haWwiOiJjaHVhbmdrdW42OUBnbWFpbC5jb20iLCJ0b2tlbl92ZXJzaW9uIjowfQ.HsULDchhy4vlVfoKipk-JEjDMv34OndMN8M4SVXEp3w"
]

API_CLIENTS = []
TOKEN_CURSOR = 0

def init_api_clients():
    print("[INFO] 正在初始化 3 組 FinMind API 客戶端...")
    for idx, token in enumerate(TOKENS):
        for attempt in range(3):
            try:
                client = DataLoader()
                client.login_by_token(api_token=token)
                API_CLIENTS.append(client)
                print(f"  API 客戶端 {idx} 初始化成功。")
                break
            except Exception as e:
                print(f"  [WARNING] API 客戶端 {idx} 初始化失敗 (嘗試 {attempt+1}): {e}")
                time.sleep(5)
        else:
            raise Exception(f"無法初始化 FinMind API 客戶端 {idx}")

def get_api_client():
    global TOKEN_CURSOR
    return API_CLIENTS[TOKEN_CURSOR]

def rotate_token():
    global TOKEN_CURSOR
    TOKEN_CURSOR = (TOKEN_CURSOR + 1) % len(API_CLIENTS)

def fetch_margin_with_retry(stock_id, start_date, end_date):
    """抓取信用交易資料，自動輪替 Token 並重試"""
    for attempt in range(4):
        try:
            api = get_api_client()
            df = api.taiwan_stock_margin_purchase_short_sale(
                stock_id=stock_id,
                start_date=start_date,
                end_date=end_date
            )
            if df is not None:
                rotate_token()
                return df
        except Exception as e:
            rotate_token()
            time.sleep(10)
    return None

def main():
    print("============================================================")
    init_api_clients()
    
    # 1. 從資料庫取得所有有信用交易紀錄的股票代號
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT stock_id FROM daily_chips WHERE margin_purchase_balance > 0 OR short_sale_balance > 0")
    stocks = [row[0] for row in cursor.fetchall()]
    conn.close()

    if not stocks:
        print("[ERROR] 資料庫中找不到任何有信用交易資料的股票！")
        return

    print(f"[INFO] 找到 {len(stocks)} 檔具有信用交易紀錄的股票。")
    
    # 隨機挑選 100 檔
    sample_size = min(100, len(stocks))
    sample_stocks = random.sample(stocks, sample_size)
    print(f"[INFO] 已隨機抽樣 {sample_size} 檔股票進行比對。")

    matched_count = 0
    mismatched_count = 0
    total_checked_rows = 0

    print("------------------------------------------------------------")
    print(f"{'股票代號':<10}{'比對期間':<25}{'資料庫筆數':<10}{'API筆數':<10}{'完全一致':<10}")
    print("------------------------------------------------------------")

    conn = sqlite3.connect(DB_PATH)
    
    for idx, sid in enumerate(sample_stocks):
        # 取得該股近 120 天在本地資料庫的融資券餘額
        df_db = pd.read_sql_query(
            "SELECT date, margin_purchase_balance, short_sale_balance FROM daily_chips "
            "WHERE stock_id = ? AND date >= date('now', '-120 days') ORDER BY date ASC",
            conn, params=(sid,)
        )
        
        if df_db.empty:
            continue
            
        start_date = df_db["date"].iloc[0]
        end_date = df_db["date"].iloc[-1]
        
        # 抓取 API
        df_api = fetch_margin_with_retry(sid, start_date, end_date)
        time.sleep(0.3) # 每次請求完睡眠 0.3 秒，防限速

        if df_api is None or df_api.empty:
            # 如果 API 沒資料，但 DB 有，若 DB 全是 0 則算一致
            db_sum = df_db["margin_purchase_balance"].sum() + df_db["short_sale_balance"].sum()
            is_match = (db_sum == 0)
            status = "一致(無資券)" if is_match else "不一致(API無資料)"
            if is_match:
                matched_count += 1
            else:
                mismatched_count += 1
            print(f"{sid:<10}{f'{start_date} ~ {end_date}':<25}{len(df_db):<10}{0:<10}{status:<10}")
            continue

        # 整理 API 欄位
        df_api = df_api.rename(columns={
            "MarginPurchaseTodayBalance": "margin_balance",
            "ShortSaleTodayBalance": "short_balance"
        })
        # 轉成 numeric 以免 string 比較有誤
        df_api["margin_balance"] = pd.to_numeric(df_api["margin_balance"], errors='coerce').fillna(0.0)
        df_api["short_balance"] = pd.to_numeric(df_api["short_balance"], errors='coerce').fillna(0.0)
        
        df_api = df_api[["date", "margin_balance", "short_balance"]].drop_duplicates("date")
        
        # 合併比對
        df_merged = pd.merge(df_db, df_api, on="date", how="inner")
        
        if df_merged.empty:
            print(f"{sid:<10}{f'{start_date} ~ {end_date}':<25}{len(df_db):<10}{len(df_api):<10}{'不一致(合併為空)':<10}")
            mismatched_count += 1
            continue

        # 比對餘額 (允許誤差 <= 1.0 張)
        diff_margin = (df_merged["margin_purchase_balance"] - df_merged["margin_balance"]).abs().max()
        diff_short = (df_merged["short_sale_balance"] - df_merged["short_balance"]).abs().max()
        
        is_identical = (diff_margin <= 1.01) and (diff_short <= 1.01)
        total_checked_rows += len(df_merged)

        if is_identical:
            matched_count += 1
            status = "Yes"
        else:
            mismatched_count += 1
            status = f"No (MargDiff: {diff_margin:.1f}, ShortDiff: {diff_short:.1f})"
            
        print(f"{sid:<10}{f'{start_date} ~ {end_date}':<25}{len(df_db):<10}{len(df_api):<10}{status:<10}")

    conn.close()
    
    print("============================================================")
    print("📊 抽樣一致性比對結果報告")
    print(f"  總抽樣檔數: {sample_size} 檔")
    print(f"  完全一致檔數: {matched_count} 檔")
    print(f"  不一致檔數: {mismatched_count} 檔")
    print(f"  累計核對資料列數: {total_checked_rows} 列")
    if sample_size > 0:
        print(f"  股票比對一致率: {matched_count / sample_size * 100:.2f}%")
    print("============================================================")

if __name__ == "__main__":
    main()
