import sqlite3
import time
import sys
import pandas as pd
from datetime import datetime
from FinMind.data import DataLoader

# Set standard output encoding to UTF-8
sys.stdout.reconfigure(encoding='utf-8')

DB_PATH = "taiwan_stock.db"

PRIMARY_API_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiYWxiZXJ0MDkxNyIsImVtYWlsIjoiYWxiZXJ0MDkxN0BnbWFpbC5jb20iLCJ0b2tlbl92ZXJzaW9uIjowLCJleHAiOjE3ODM0MTcwMjl9.snTeoVkjJqMb7m655PA_lA8yxPgdSE24Sfm0A9n-jxU"
FALLBACK_API_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiY2h1YW5nY2h1YW5nOTE3QGdtYWlsLmNvbSIsImVtYWlsIjoiY2h1YW5nY2h1YW5nOTE3QGdtYWlsLmNvbSIsInRva2VuX3ZlcnNpb24iOjIsImV4cCI6MTc4MzQxNzA0M30.IKH0tshNaAX_OAfXnFlzrygANbbGyo_KAs_M2JlO_tg"
THIRD_API_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiY2h1YW5na3VuNjlAZ21haWwuY29tIiwiZW1haWwiOiJjaHVhbmdrdW42OUBnbWFpbC5jb20iLCJ0b2tlbl92ZXJzaW9uIjowLCJleHAiOjE3ODM0MTcyMDF9.dmGveEOR8lEXdA2Wibx8DcOYoHrVWBc3X2w0s1RPQSU"

API_TOKENS = [PRIMARY_API_TOKEN, FALLBACK_API_TOKEN, THIRD_API_TOKEN]
TOKEN_CURSOR = 0
API_CLIENTS = []

def init_api_clients():
    global API_CLIENTS
    print("[INFO] 正在初始化 3 組 FinMind API 客戶端...")
    for idx, token in enumerate(API_TOKENS):
        for attempt in range(4):
            try:
                api = DataLoader()
                api.login_by_token(api_token=token)
                API_CLIENTS.append(api)
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
    print(f"[INFO] 切換至 API Token 序號: {TOKEN_CURSOR}")

def fetch_margin_with_retry(stock_id, start_date, end_date):
    """抓取信用交易資料，遇到錯誤或限速時自動輪替 Token 並重試"""
    for attempt in range(4):
        try:
            api = get_api_client()
            df = api.taiwan_stock_margin_purchase_short_sale(
                stock_id=stock_id,
                start_date=start_date,
                end_date=end_date
            )
            if df is not None:
                # 成功獲取，輪轉下一個 Token 備用並回傳
                rotate_token()
                return df
        except Exception as e:
            error_msg = str(e)
            print(f"  [WARNING] 股票 {stock_id} 抓取失敗 (嘗試 {attempt+1}): {error_msg}")
            # 如果是限速或其它 API 異常，切換 Token 並休眠
            rotate_token()
            time.sleep(15)
    return None
def main():
    print("=" * 60)
    print("🚀 Chase 歷史信用交易補件更新程式啟動")
    print(f"⏱️ 啟動時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    init_api_clients()

    # 1. 撈出所有需要更新的股票清單 (即存在 margin_purchase_balance = 0.0 的股票)
    conn = sqlite3.connect(DB_PATH)
    try:
        stocks = pd.read_sql_query(
            "SELECT DISTINCT stock_id FROM daily_chips WHERE margin_purchase_balance = 0.0 AND volume > 0.0",
            conn
        )["stock_id"].tolist()
    finally:
        conn.close()

    total_stocks = len(stocks)
    print(f"[INFO] 發現共 {total_stocks} 檔上市櫃股票需要進行信用交易補件。")

    if total_stocks == 0:
        print("[SUCCESS] 沒有需要補件的資料！資料庫已為最新狀態。")
        return

    # 2. 開始逐股進行補件更新
    t0 = time.time()
    success_count = 0
    updated_rows_total = 0

    for idx, sid in enumerate(stocks):
        conn = sqlite3.connect(DB_PATH)
        try:
            # 查詢這檔股票缺漏的日期範圍
            range_df = pd.read_sql_query(
                "SELECT MIN(date) as min_d, MAX(date) as max_d FROM daily_chips WHERE stock_id=? AND margin_purchase_balance = 0.0",
                conn, params=(sid,)
            )
            min_date = range_df.iloc[0]["min_d"]
            max_date = range_df.iloc[0]["max_d"]
        finally:
            conn.close()

        if not min_date or not max_date:
            continue

        # 向 FinMind 查詢該範圍的融資券數據
        print(f"[{idx+1}/{total_stocks}] 正在補件 {sid} (期間: {min_date} 至 {max_date}) ...")
        df_margin = fetch_margin_with_retry(sid, min_date, max_date)
        
        # 為了避免超出每小時 1800 次的額度限制，每次請求間隔 0.2 秒
        time.sleep(0.2)

        if df_margin is not None and not df_margin.empty:
            # 3. 寫入資料庫 (SQL UPDATE)
            conn = sqlite3.connect(DB_PATH)
            try:
                cur = conn.cursor()
                update_count = 0
                for _, row in df_margin.iterrows():
                    d = str(row["date"])
                    mp = float(row["MarginPurchaseTodayBalance"])
                    ss = float(row["ShortSaleTodayBalance"])
                    
                    cur.execute(
                        "UPDATE daily_chips SET margin_purchase_balance=?, short_sale_balance=? WHERE date=? AND stock_id=?",
                        (mp, ss, d, sid)
                    )
                    update_count += cur.rowcount
                conn.commit()
                success_count += 1
                updated_rows_total += update_count
                print(f"  [SUCCESS] 成功更新 {update_count} 筆歷史交易紀錄。")
            except Exception as db_err:
                conn.rollback()
                print(f"  [ERROR] 資料庫寫入失敗: {db_err}")
            finally:
                conn.close()
        else:
            print(f"  [WARNING] 無法取得 {sid} 的歷史信用交易資料。")

    elapsed = time.time() - t0
    print("\n" + "=" * 60)
    print(f"🎉 補件更新流程完成！")
    print(f"總共處理：{total_stocks} 檔股票")
    print(f"成功補件：{success_count} 檔")
    print(f"累計更新：{updated_rows_total} 筆資料庫紀錄")
    print(f"總耗時：{elapsed/60:.1f} 分鐘")
    print("=" * 60)

if __name__ == "__main__":
    main()
