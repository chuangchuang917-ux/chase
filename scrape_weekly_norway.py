import requests
from bs4 import BeautifulSoup
import sqlite3
import time
import random
import pandas as pd

TAIWAN_50_STOCKS = [
    "2330", "2454", "2308", "2317", "3711", "2303", "2383", "2327", "3037", "2345",
    "2891", "2881", "2882", "2382", "2360", "3017", "2885", "1303", "2887", "2344",
    "2408", "2412", "2884", "2357", "2886", "2890", "6669", "3008", "3231", "2368",
    "2883", "4958", "2301", "2059", "7769", "3443", "2449", "1216", "2892", "3665",
    "2880", "3661", "3653", "5880", "2395", "2603", "4904", "8046", "3045", "6505"
]

DB_PATH = "taiwan_stock.db"

def scrape_stock_history(session, stock_id):
    url = f"https://norway.twsthr.info/StockHolders.aspx?stock={stock_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://norway.twsthr.info/"
    }
    
    for attempt in range(3):
        try:
            r = session.get(url, headers=headers, timeout=15)
            if r.status_code != 200:
                raise Exception(f"HTTP Status {r.status_code}")
                
            soup = BeautifulSoup(r.text, 'html.parser')
            tables = soup.find_all("table")
            
            target_table = None
            for t in tables:
                rows = t.find_all("tr")
                if len(rows) > 100:
                    first_row_cols = rows[0].find_all(["td", "th"])
                    if 12 <= len(first_row_cols) < 50:
                        target_table = t
                        break
                        
            if not target_table:
                raise Exception("Data table not found in response HTML")
                
            rows = target_table.find_all("tr")[1:]  # skip header
            
            records = []
            for row in rows:
                cols = [td.text.strip().replace('\xa0', '').replace('\n', ' ') for td in row.find_all(["td", "th"], recursive=False)]
                if len(cols) >= 14:
                    raw_date = cols[2]
                    if len(raw_date) == 8 and raw_date.isdigit():
                        db_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
                        
                        # Only keep dates in our required history range: 2025-12-01 to 2026-06-23
                        if "2025-12-01" <= db_date <= "2026-06-23":
                            try:
                                holder_over_1000 = float(cols[13].replace(",", ""))
                                holder_over_400 = float(cols[7].replace(",", ""))
                                records.append({
                                    "date": db_date,
                                    "stock_id": stock_id,
                                    "holder_over_1000": holder_over_1000,
                                    "holder_over_400": holder_over_400
                                })
                            except ValueError:
                                pass
            return records
        except Exception as e:
            print(f"[WARNING] Stock {stock_id} attempt {attempt+1} failed: {e}")
            if attempt == 2:
                return None
            time.sleep(2)

def main():
    session = requests.Session()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Recreate weekly_shareholders table if needed, just to make sure
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS weekly_shareholders (
            date TEXT NOT NULL,
            stock_id TEXT NOT NULL,
            holder_over_1000 REAL,
            holder_over_400 REAL,
            PRIMARY KEY (date, stock_id)
        )
    """)
    conn.commit()
    
    # 動態獲取全市場普通股清單
    try:
        df_stocks = pd.read_sql_query("SELECT DISTINCT stock_id FROM daily_chips", conn)
        all_stocks = df_stocks["stock_id"].tolist()
    except Exception:
        print("[ERROR] 無法從 daily_chips 獲取股票清單，回退至預設清單。")
        all_stocks = TAIWAN_50_STOCKS.copy()
        
    # 獲取已存在週資料的股票，以進行增量爬取避免重複請求
    try:
        df_existing = pd.read_sql_query("SELECT DISTINCT stock_id FROM weekly_shareholders", conn)
        existing_stocks = set(df_existing["stock_id"].tolist())
    except Exception:
        existing_stocks = set()
        
    stocks_to_scrape = [sid for sid in all_stocks if sid not in existing_stocks]
    
    total_stocks = len(stocks_to_scrape)
    success_count = 0
    total_records = 0
    
    print(f"已在庫股票數: {len(existing_stocks)}。剩餘需要爬取的普通股數: {total_stocks}。")
    if total_stocks == 0:
        print("所有股票的週歷史資料均已就緒，無需爬取！")
        conn.close()
        return
        
    print(f"Starting historical scrape for {total_stocks} stocks from norway.twsthr.info...")
    
    for idx, stock_id in enumerate(stocks_to_scrape):
        print(f"[{idx+1}/{total_stocks}] Scraping history for stock {stock_id}...")
        records = scrape_stock_history(session, stock_id)
        
        if records:
            data_tuples = [
                (r["date"], r["stock_id"], r["holder_over_1000"], r["holder_over_400"])
                for r in records
            ]
            cursor.executemany("""
                INSERT OR REPLACE INTO weekly_shareholders 
                (date, stock_id, holder_over_1000, holder_over_400)
                VALUES (?, ?, ?, ?)
            """, data_tuples)
            conn.commit()
            
            print(f"  Successfully wrote {len(records)} records for stock {stock_id}.")
            success_count += 1
            total_records += len(records)
        else:
            print(f"  [ERROR/EMPTY] Failed to scrape stock {stock_id}.")
            
        # 禮貌延遲
        time.sleep(0.05 + random.random() * 0.05)
        
    conn.close()
    print(f"\n[FINISHED] Scraped {success_count}/{total_stocks} stocks successfully. Total records written: {total_records}")

if __name__ == "__main__":
    main()
