"""
setup_supabase_raw_tables.py
一次性初始化腳本：
1. 在 Supabase 建立 daily_chips_raw 和 weekly_shareholders_raw 表
2. 將本機 SQLite 的全部原始資料同步上去

用法：python setup_supabase_raw_tables.py
"""

import os
import sys
import sqlite3
import time
import json

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://xjalllcvwbgnxwcruhzz.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "taiwan_stock.db")
BATCH_SIZE = 1000

if not SUPABASE_KEY:
    print("[ERROR] 找不到 SUPABASE_KEY，請確認 .env 檔案設定正確。")
    sys.exit(1)

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates",
}

# ─── Supabase 建表 SQL（透過 pg REST 建表） ────────────────────────────────
CREATE_DAILY_CHIPS_RAW_SQL = """
CREATE TABLE IF NOT EXISTS daily_chips_raw (
    date TEXT NOT NULL,
    stock_id TEXT NOT NULL,
    stock_name TEXT,
    close REAL,
    volume REAL,
    shares_issued REAL,
    foreign_buy_shares REAL,
    trust_buy_shares REAL,
    top15_buy_total REAL,
    top15_sell_total REAL,
    margin_purchase_balance REAL,
    short_sale_balance REAL,
    PRIMARY KEY (date, stock_id)
);
"""

CREATE_WEEKLY_RAW_SQL = """
CREATE TABLE IF NOT EXISTS weekly_shareholders_raw (
    date TEXT NOT NULL,
    stock_id TEXT NOT NULL,
    holder_over_1000 REAL,
    holder_over_400 REAL,
    PRIMARY KEY (date, stock_id)
);
"""

def create_tables_via_rpc():
    """
    透過 Supabase 的 pg_query RPC 或 SQL API 建表。
    Supabase 提供 /rest/v1/rpc/exec_sql (若有開啟)，
    若沒有，改用 REST API 先試插一筆看看是否表格已存在。
    """
    # 嘗試透過 Supabase SQL API（service role 才能用）
    url = f"{SUPABASE_URL}/rest/v1/rpc/query"
    
    # 先嘗試直接插入空資料，如果表格不存在會 404
    test_url = f"{SUPABASE_URL}/rest/v1/daily_chips_raw?limit=1"
    r = requests.get(test_url, headers=HEADERS, timeout=15)
    
    if r.status_code == 404 or (r.status_code == 200 and "relation" in r.text.lower() and "does not exist" in r.text.lower()):
        print("[INFO] daily_chips_raw 表格不存在，需要在 Supabase Dashboard 手動建立。")
        print("\n請到 Supabase Dashboard > SQL Editor 執行以下 SQL：")
        print("=" * 60)
        print(CREATE_DAILY_CHIPS_RAW_SQL)
        print(CREATE_WEEKLY_RAW_SQL)
        print("=" * 60)
        print("\n建立完成後，請重新執行此腳本。")
        return False
    elif r.status_code == 200:
        print("[INFO] daily_chips_raw 表格已存在，跳過建表步驟。")
        return True
    else:
        # 嘗試解析錯誤
        try:
            err = r.json()
            if "does not exist" in str(err):
                print("[INFO] 表格不存在，請到 Supabase Dashboard 手動建立：")
                print(CREATE_DAILY_CHIPS_RAW_SQL)
                print(CREATE_WEEKLY_RAW_SQL)
                return False
        except Exception:
            pass
        print(f"[WARNING] 無法確定表格狀態 (HTTP {r.status_code})，嘗試繼續...")
        return True


def check_or_create_table(table_name, create_sql):
    """檢查表格是否存在，不存在則顯示建表 SQL。"""
    url = f"{SUPABASE_URL}/rest/v1/{table_name}?limit=1"
    r = requests.get(url, headers=HEADERS, timeout=15)
    
    if r.status_code == 200:
        print(f"[OK] {table_name} 表格已存在。")
        return True
    else:
        print(f"[MISSING] {table_name} 表格不存在 (HTTP {r.status_code})。")
        try:
            err_detail = r.json()
            print(f"  Supabase 回應: {err_detail}")
        except Exception:
            pass
        return False


def upsert_batch(table_name, records):
    """批次 Upsert 資料到 Supabase 表格。"""
    url = f"{SUPABASE_URL}/rest/v1/{table_name}"
    
    for r in records:
        for k, v in list(r.items()):
            if isinstance(v, float):
                if pd.isna(v) or v in (float("inf"), float("-inf")):
                    r[k] = None
            elif hasattr(v, "item"):
                r[k] = v.item()
    
    resp = requests.post(url, headers=HEADERS, json=records, timeout=60)
    return resp.status_code in (200, 201)


def sync_daily_chips_raw():
    """將 SQLite daily_chips 全量同步到 Supabase daily_chips_raw。"""
    print("\n[STEP] 開始同步 daily_chips → Supabase daily_chips_raw ...")
    
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query("SELECT * FROM daily_chips ORDER BY date ASC, stock_id ASC", conn)
    finally:
        conn.close()
    
    if df.empty:
        print("[ERROR] daily_chips 資料表為空！")
        return
    
    total = len(df)
    print(f"  載入本機資料：{total:,} 筆 ({df['date'].nunique()} 個交易日)")
    
    # 查詢 Supabase 已有哪些日期（避免重複上傳）
    print("  查詢 Supabase 已有的日期...")
    existing_dates = set()
    try:
        url = f"{SUPABASE_URL}/rest/v1/daily_chips_raw?select=date&order=date.asc"
        offset = 0
        limit = 1000
        while True:
            h = HEADERS.copy()
            h["Range"] = f"{offset}-{offset+limit-1}"
            r = requests.get(url, headers=h, timeout=30)
            if r.status_code not in (200, 206):
                break
            data = r.json()
            if not data:
                break
            existing_dates.update(d["date"] for d in data)
            if len(data) < limit:
                break
            offset += limit
        print(f"  Supabase 已有 {len(existing_dates)} 個交易日資料")
    except Exception as e:
        print(f"  [WARNING] 無法查詢已有日期: {e}，將全量上傳")
    
    # 過濾出需要上傳的資料
    if existing_dates:
        df_upload = df[~df["date"].isin(existing_dates)].copy()
        print(f"  需要上傳：{len(df_upload):,} 筆 ({df_upload['date'].nunique()} 個交易日)")
    else:
        df_upload = df.copy()
        print(f"  需要上傳：{len(df_upload):,} 筆（全量）")
    
    if df_upload.empty:
        print("  [SKIP] 所有資料已同步，無需上傳。")
        return
    
    records = df_upload.to_dict(orient="records")
    total_upload = len(records)
    ok_count = 0
    err_count = 0
    t0 = time.time()
    
    for i in range(0, total_upload, BATCH_SIZE):
        batch = records[i:i + BATCH_SIZE]
        success = upsert_batch("daily_chips_raw", batch)
        if success:
            ok_count += len(batch)
        else:
            err_count += len(batch)
            print(f"  [ERROR] 批次 {i}~{i+len(batch)} 上傳失敗")
        
        if i % 10000 == 0 or i + BATCH_SIZE >= total_upload:
            elapsed = time.time() - t0
            pct = (i + len(batch)) / total_upload * 100
            spd = (i + len(batch)) / elapsed if elapsed > 0 else 0
            print(f"  [進度] {i+len(batch):>6}/{total_upload} ({pct:5.1f}%) | {spd:.0f} 筆/秒")
    
    print(f"  [完成] 成功: {ok_count:,} 筆 | 失敗: {err_count} 筆 | 耗時: {time.time()-t0:.1f}s")


def sync_weekly_shareholders_raw():
    """將 SQLite weekly_shareholders 全量同步到 Supabase weekly_shareholders_raw。"""
    print("\n[STEP] 開始同步 weekly_shareholders → Supabase weekly_shareholders_raw ...")
    
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query("SELECT * FROM weekly_shareholders ORDER BY date ASC, stock_id ASC", conn)
    finally:
        conn.close()
    
    if df.empty:
        print("  [SKIP] weekly_shareholders 資料表為空。")
        return
    
    total = len(df)
    print(f"  載入本機週資料：{total:,} 筆 ({df['date'].nunique()} 週)")
    
    # 查詢 Supabase 已有的日期
    existing_dates = set()
    try:
        url = f"{SUPABASE_URL}/rest/v1/weekly_shareholders_raw?select=date&order=date.asc"
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code == 200:
            existing_dates.update(d["date"] for d in r.json())
        print(f"  Supabase 已有 {len(existing_dates)} 週資料")
    except Exception as e:
        print(f"  [WARNING] 無法查詢已有週資料: {e}")
    
    df_upload = df[~df["date"].isin(existing_dates)].copy() if existing_dates else df.copy()
    
    if df_upload.empty:
        print("  [SKIP] 所有週資料已同步。")
        return
    
    print(f"  需要上傳：{len(df_upload):,} 筆")
    records = df_upload.to_dict(orient="records")
    ok_count = 0
    t0 = time.time()
    
    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i:i + BATCH_SIZE]
        if upsert_batch("weekly_shareholders_raw", batch):
            ok_count += len(batch)
    
    print(f"  [完成] 成功: {ok_count:,} 筆 | 耗時: {time.time()-t0:.1f}s")


def print_create_sql():
    """印出建表 SQL 供手動執行。"""
    print("\n" + "=" * 60)
    print("請到 Supabase Dashboard > SQL Editor 執行以下 SQL：")
    print("=" * 60)
    print(CREATE_DAILY_CHIPS_RAW_SQL)
    print(CREATE_WEEKLY_RAW_SQL)
    print("=" * 60)


def main():
    print("=" * 60)
    print("[START] Supabase 原始資料表初始化與全量同步")
    print("=" * 60)
    
    # 步驟 1: 檢查表格是否存在
    print("\n[STEP 1] 檢查 Supabase 表格狀態...")
    raw_ok = check_or_create_table("daily_chips_raw", CREATE_DAILY_CHIPS_RAW_SQL)
    weekly_ok = check_or_create_table("weekly_shareholders_raw", CREATE_WEEKLY_RAW_SQL)
    
    if not raw_ok or not weekly_ok:
        print_create_sql()
        print("\n[提示] 建立完成後，請重新執行此腳本進行資料同步。")
        sys.exit(1)
    
    # 步驟 2: 同步原始日資料
    sync_daily_chips_raw()
    
    # 步驟 3: 同步週資料
    sync_weekly_shareholders_raw()
    
    print("\n" + "=" * 60)
    print("[DONE] 初始化完成！Supabase 已有完整原始資料。")
    print("=" * 60)


if __name__ == "__main__":
    main()
