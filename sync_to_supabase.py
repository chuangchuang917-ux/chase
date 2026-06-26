import sqlite3
import pandas as pd
import requests
import json
import time
import sys
import os

# Set standard output encoding to UTF-8
sys.stdout.reconfigure(encoding='utf-8')

# Import strategy engine
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from strategy import run_chip_strategy

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

def get_weekly_growth_map():
    """
    計算每週大戶持股連續成長週數
    回傳字典: { (weekly_date, stock_id): growth_weeks }
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        df_weekly = pd.read_sql_query(
            "SELECT date, stock_id, holder_over_1000 FROM weekly_shareholders", conn
        )
        if df_weekly.empty:
            return {}
            
        df_weekly = df_weekly.sort_values(by=["stock_id", "date"])
        df_weekly["increased"] = df_weekly.groupby("stock_id")["holder_over_1000"].diff() > 0
        
        # 考慮各股票第一週因為沒有前一週資料，diff() 為 NaN / False，growth_weeks 為 0
        df_weekly["growth_weeks"] = df_weekly.groupby("stock_id")["increased"].transform(calculate_consecutive_growth)
        
        growth_map = {}
        for _, row in df_weekly.iterrows():
            growth_map[(row["date"], row["stock_id"])] = int(row["growth_weeks"])
        return growth_map
    finally:
        conn.close()

def sync_data():
    conn = sqlite3.connect(DB_PATH)
    try:
        # 取得所有集保日期的 mapping 用以加速查詢最新集保日期
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT date FROM weekly_shareholders ORDER BY date ASC")
        weekly_dates = sorted([r[0] for r in cursor.fetchall()])
        
        # 取得所有的大戶連續成長週數資料
        print("[INFO] 計算每週大戶持股連續吸貨週數...")
        weekly_growth_map = get_weekly_growth_map()
        print(f"[INFO] 計算完成，共取得 {len(weekly_growth_map)} 筆大戶歷史週資料對照。")
        
        # 取得所有交易日
        cursor.execute("SELECT DISTINCT date FROM daily_chips ORDER BY date ASC")
        dates = [r[0] for r in cursor.fetchall()]
        print(f"[INFO] 資料庫中共有 {len(dates)} 個交易日需要處理。")
        
        for idx, target_date in enumerate(dates):
            print(f"[{idx+1}/{len(dates)}] 正在計算與上傳日期 {target_date}...")
            
            # 執行策略 (min_trade_value=0 代表計算所有股票)
            df = run_chip_strategy(target_date, weekly_trend_weeks=0, min_trade_value=0, db_path=DB_PATH)
            if df.empty:
                print(f"  該日期無資料")
                continue
            
            # 找出截至 target_date 最新的集保日期
            latest_w_date = None
            for w_date in reversed(weekly_dates):
                if w_date <= target_date:
                    latest_w_date = w_date
                    break
            
            # 建立大戶連續吸貨週數欄位
            growth_weeks_list = []
            for _, row in df.iterrows():
                sid = row["stock_id"]
                key = (latest_w_date, sid)
                growth_weeks_list.append(weekly_growth_map.get(key, 0))
            df["holder_growth_weeks"] = growth_weeks_list
            
            # 整理與重新命名欄位，以對應 Supabase 表格
            # Supabase 表格與 strategy.py output_cols 相同
            # 但 close 欄位需對應到資料表 schema 中的 close
            df_upload = df.copy()
            
            # 轉換為字典格式
            records = df_upload.to_dict(orient="records")
            
            # 分批上傳 (批次大小 500)
            batch_size = 500
            for i in range(0, len(records), batch_size):
                batch = records[i:i+batch_size]
                
                # 處理 NaN, inf 等 JSON 無法支援的值，將其轉換為 None
                for r in batch:
                    for k, v in list(r.items()):
                        # 轉換數值以防止 pandas.isna 等引發的問題
                        if pd.isna(v) or v == float('inf') or v == float('-inf'):
                            r[k] = None
                
                url = f"{SUPABASE_URL}/rest/v1/chase_strategy_results"
                response = requests.post(url, headers=HEADERS, json=batch)
                if response.status_code not in (200, 201):
                    print(f"  [ERROR] 上傳失敗 (批次 {i//batch_size})：{response.text}")
                    response.raise_for_status()
                    
            print(f"  成功上傳 {len(records)} 筆股票資料。")
            
    finally:
        conn.close()

if __name__ == "__main__":
    start_time = time.time()
    try:
        sync_data()
        print(f"[SUCCESS] 所有數據已成功同步至 Supabase！耗時 {time.time() - start_time:.2f} 秒。")
    except Exception as e:
        print(f"[ERROR] 同步失敗: {e}")
