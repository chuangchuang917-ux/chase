import requests
from bs4 import BeautifulSoup
import sqlite3
import time
import random

# TAIWAN 50 STOCKS list from crawler.py
TAIWAN_50_STOCKS = [
    "2330", "2454", "2308", "2317", "3711", "2303", "2383", "2327", "3037", "2345",
    "2891", "2881", "2882", "2382", "2360", "3017", "2885", "1303", "2887", "2344",
    "2408", "2412", "2884", "2357", "2886", "2890", "6669", "3008", "3231", "2368",
    "2883", "4958", "2301", "2059", "7769", "3443", "2449", "1216", "2892", "3665",
    "2880", "3661", "3653", "5880", "2395", "2603", "4904", "8046", "3045", "6505"
]

DB_PATH = "taiwan_stock.db"
GET_URL = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"

def get_existing_records():
    """
    Get set of (date, stock_id) already in the database to prevent duplicate queries
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS weekly_shareholders (date TEXT, stock_id TEXT, holder_over_1000 REAL, holder_over_400 REAL, PRIMARY KEY (date, stock_id))")
    cursor.execute("SELECT date, stock_id FROM weekly_shareholders")
    rows = cursor.fetchall()
    conn.close()
    return set(rows)

def get_dates_and_token(session):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    r = session.get(GET_URL, headers=headers, timeout=20)
    soup = BeautifulSoup(r.text, 'html.parser')
    token_elem = soup.find("input", {"name": "SYNCHRONIZER_TOKEN"})
    token = token_elem.get("value") if token_elem else None
    
    dates_select = soup.find("select", {"id": "scaDate"})
    all_dates = []
    if dates_select:
        all_dates = [opt.get("value") for opt in dates_select.find_all("option")]
    
    return token, all_dates

def fetch_stock_date(session, token, stock_id, date_str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": GET_URL
    }
    post_data = {
        "SYNCHRONIZER_TOKEN": token,
        "SYNCHRONIZER_URI": "/portal/zh/smWeb/qryStock",
        "method": "submit",
        "firDate": date_str,
        "scaDate": date_str,
        "sqlMethod": "StockNo",
        "stockNo": stock_id,
        "stockName": "",
    }
    
    for attempt in range(5):
        try:
            r = session.post(GET_URL, headers=headers, data=post_data, timeout=20)
            if r.status_code != 200:
                raise Exception(f"HTTP Status {r.status_code}")
                
            post_soup = BeautifulSoup(r.text, 'html.parser')
            
            # Extract new token for next request
            token_elem = post_soup.find("input", {"name": "SYNCHRONIZER_TOKEN"})
            new_token = token_elem.get("value") if token_elem else None
            
            table = post_soup.find("table", {"class": "table"})
            if not table:
                tables = post_soup.find_all("table")
                if tables:
                    table = tables[0]
                else:
                    if "查詢過於頻繁" in r.text or "Too Many Requests" in r.text or "安全偵測" in r.text:
                        print(f"[BLOCKED] Hit TDCC rate limit. Sleeping for 15s...")
                        time.sleep(15)
                    raise Exception("No table found in response HTML")
                    
            rows = table.find_all("tr")[1:]  # skip header
            
            holder_over_1000 = 0.0
            holder_over_400 = 0.0
            total_holders = 0.0
            
            parsed_rows = 0
            for row in rows:
                cols = [td.text.strip() for td in row.find_all("td")]
                if len(cols) >= 5:
                    level_str = cols[0]
                    if "合計" in level_str or "總計" in level_str:
                        continue
                    try:
                        level = int(level_str)
                        people = int(cols[2].replace(",", ""))
                        shares = int(cols[3].replace(",", ""))
                        percent = float(cols[4])
                        
                        if level == 15:
                            holder_over_1000 = percent
                        if 12 <= level <= 15:
                            holder_over_400 += percent
                        parsed_rows += 1
                    except ValueError:
                        pass
            
            if parsed_rows == 0:
                raise Exception("Zero rows successfully parsed")
                
            db_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            res_dict = {
                "date": db_date,
                "stock_id": stock_id,
                "holder_over_1000": holder_over_1000,
                "holder_over_400": holder_over_400
            }
            return res_dict, new_token
        except Exception as e:
            if attempt == 4:
                print(f"[ERROR] Failed to fetch {stock_id} on {date_str}: {e}")
                return None, None
            # Exponential backoff
            sleep_time = 3 + attempt * 4
            print(f"[WARNING] Retry {attempt + 1} for {stock_id} on {date_str} in {sleep_time}s due to error: {e}")
            time.sleep(sleep_time)

def main():
    session = requests.Session()
    print("Fetching available dates and token from TDCC...")
    token = None
    all_dates = []
    for attempt in range(3):
        try:
            token, all_dates = get_dates_and_token(session)
            if token and all_dates:
                break
        except Exception as e:
            print(f"Failed to load TDCC page (attempt {attempt + 1}): {e}")
            time.sleep(3)
            
    if not token or not all_dates:
        print("Failed to get available dates or token. Exiting.")
        return
        
    filtered_dates = [d for d in all_dates if "20251201" <= d <= "20260623"]
    print(f"Total available weeks: {len(filtered_dates)}")
    
    # Load existing records to skip
    existing = get_existing_records()
    print(f"Already have {len(existing)} records in database.")
    
    # Generate tasks
    tasks = []
    for stock_id in TAIWAN_50_STOCKS:
        for date_str in filtered_dates:
            db_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            if (db_date, stock_id) not in existing:
                tasks.append((stock_id, date_str))
                
    total_tasks = len(tasks)
    print(f"Remaining tasks to fetch: {total_tasks}")
    
    if total_tasks == 0:
        print("All records already in database!")
        return
        
    # Sort tasks by date_str descending so latest dates are populated first
    tasks.sort(key=lambda x: (x[1], x[0]), reverse=True)
    
    success_count = 0
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    current_token = token
    
    try:
        for idx, (stock_id, date_str) in enumerate(tasks):
            print(f"[{idx+1}/{total_tasks}] Querying {stock_id} for {date_str}...")
            
            # If token is None/lost, do a fresh GET to refresh session and get token
            if not current_token:
                print("Token is None, refreshing session via GET request...")
                session = requests.Session()
                for refresh_attempt in range(3):
                    try:
                        current_token, _ = get_dates_and_token(session)
                        if current_token:
                            print("Fresh token obtained:", current_token)
                            break
                    except Exception as re:
                        print(f"Failed to refresh session (attempt {refresh_attempt + 1}): {re}")
                        time.sleep(4)
                if not current_token:
                    print("[ERROR] Could not obtain token after session refresh. Skipping this task.")
                    continue
            
            res, new_token = fetch_stock_date(session, current_token, stock_id, date_str)
            
            if res:
                # Write incrementally
                cursor.execute("""
                    INSERT OR REPLACE INTO weekly_shareholders 
                    (date, stock_id, holder_over_1000, holder_over_400)
                    VALUES (?, ?, ?, ?)
                """, (res["date"], res["stock_id"], res["holder_over_1000"], res["holder_over_400"]))
                conn.commit()
                success_count += 1
                
                # Update token with the one returned in response
                if new_token:
                    current_token = new_token
                else:
                    current_token = None
            else:
                current_token = None
            
            # Sleep to be polite
            time.sleep(0.3 + random.random() * 0.3)
            
    finally:
        conn.close()
        print(f"\nExecution ended. Successfully added {success_count} records to database.")

if __name__ == "__main__":
    main()
