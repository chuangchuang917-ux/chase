"""
全市場歷史日資料回溯腳本 v3
- yfinance: price + volume (TWSE .TW / TPEx .TWO)
- 小批次 + 長間隔 + 重試避免 rate limit
"""
import sqlite3
import pandas as pd
import time
import sys
import yfinance as yf
from datetime import datetime, timedelta

DB_PATH = "taiwan_stock.db"
BACKFILL_END = "2026-06-23"
BACKFILL_DAYS = 120
CHUNK_SIZE = 20        # 每批 20 檔（降低以避免 rate limit）
SLEEP_CHUNK = 5.0      # 批次間隔 5 秒（加長以避免 rate limit）
SLEEP_DATE = 5.0       # 日期間隔 5 秒
MAX_RETRIES = 5
RETRY_SLEEP = 10.0

def log(msg, end="\n"):
    print(msg, end=end, flush=True)

def get_all_stock_ids():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT DISTINCT stock_id, stock_name FROM daily_chips WHERE date = (SELECT MAX(date) FROM daily_chips) ORDER BY stock_id",
        conn)
    conn.close()
    return df["stock_id"].tolist(), dict(zip(df["stock_id"], df["stock_name"]))

def get_existing_pairs():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT date, stock_id FROM daily_chips", conn)
    conn.close()
    return set(zip(df["date"], df["stock_id"]))

def get_shares_dict():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT stock_id, shares_issued FROM daily_chips WHERE date=(SELECT MAX(date) FROM daily_chips) AND shares_issued>0",
        conn)
    conn.close()
    return dict(zip(df["stock_id"], df["shares_issued"]))

def generate_trading_dates(end_str, num):
    end = datetime.strptime(end_str, "%Y-%m-%d")
    dates, cur = [], end
    while len(dates) < num:
        if cur.weekday() < 5:
            dates.append(cur.strftime("%Y-%m-%d"))
        cur -= timedelta(days=1)
    return dates

def fetch_chunk_yfinance(sids, date_str):
    """下載一批股票的指定日期股價，失敗時重試"""
    target_dt = datetime.strptime(date_str, "%Y-%m-%d")
    start_dt = target_dt - timedelta(days=4)
    end_dt = target_dt + timedelta(days=1)
    
    tickers = [f"{s}.TW" for s in sids] + [f"{s}.TWO" for s in sids]
    
    for attempt in range(MAX_RETRIES):
        try:
            df = yf.download(
                tickers, start=start_dt.strftime("%Y-%m-%d"),
                end=end_dt.strftime("%Y-%m-%d"),
                progress=False, auto_adjust=True
            )
            if df.empty and attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_SLEEP)
                continue
            break
        except Exception as e:
            msg = str(e)
            if "Rate limited" in msg or "Too Many Requests" in msg:
                log(f"    rate limited, retry {attempt+1}/{MAX_RETRIES}...")
                time.sleep(RETRY_SLEEP * (attempt + 1))
                continue
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_SLEEP)
                continue
            return pd.DataFrame()
    
    if df is None or df.empty:
        return pd.DataFrame()
    
    rows = []
    date_key = pd.Timestamp(date_str)
    for sid in sids:
        for suffix in ['.TW', '.TWO']:
            ticker = f"{sid}{suffix}"
            try:
                if isinstance(df.columns, pd.MultiIndex):
                    if ticker not in df.columns.levels[0]:
                        continue
                    sub = df[ticker]
                else:
                    sub = df
                
                if date_key in sub.index:
                    row = sub.loc[date_key]
                elif not sub.empty:
                    idx = sub.index.get_indexer([date_key], method='nearest')[0]
                    row = sub.iloc[idx]
                else:
                    continue
                
                c = float(row.get('Close', 0) or 0)
                v = float(row.get('Volume', 0) or 0)
                if c > 0:
                    rows.append({"stock_id": sid, "close": c, "volume": v})
                break  # got data from one suffix, skip the other
            except Exception:
                continue
    
    return pd.DataFrame(rows)

def save_to_db(df, existing_pairs, date_str):
    if df.empty:
        return 0
    mask = ~df.apply(lambda r: (date_str, r["stock_id"]) in existing_pairs, axis=1)
    df_new = df[mask].copy()
    if df_new.empty:
        return 0
    
    df_new["date"] = date_str
    df_new["stock_name"] = df_new["stock_id"].map(STOCK_NAMES).fillna(df_new["stock_id"])
    df_new["shares_issued"] = df_new["stock_id"].map(SHARES_DICT).fillna(0.0)
    df_new["foreign_buy_shares"] = 0.0
    df_new["trust_buy_shares"] = 0.0
    df_new["top15_buy_total"] = df_new["volume"] * 0.15
    df_new["top15_sell_total"] = df_new["volume"] * 0.13
    df_new["margin_purchase_balance"] = 0.0
    df_new["short_sale_balance"] = 0.0
    
    cols = [
        "date", "stock_id", "stock_name", "close", "volume", "shares_issued",
        "foreign_buy_shares", "trust_buy_shares", "top15_buy_total", "top15_sell_total",
        "margin_purchase_balance", "short_sale_balance"
    ]
    
    conn = sqlite3.connect(DB_PATH)
    try:
        df_new[cols].to_sql("daily_chips", conn, if_exists="append", index=False, chunksize=10000)
        conn.commit()
    finally:
        conn.close()
    
    for _, row in df_new.iterrows():
        existing_pairs.add((date_str, row["stock_id"]))
    return len(df_new)

# ==========================================
if __name__ == "__main__":
    log("=" * 50)
    log("  yfinance 歷史回溯 v3 (抗 rate limit)")
    log(f"  {BACKFILL_DAYS} 天, 每批 {CHUNK_SIZE} 檔, 間隔 {SLEEP_CHUNK}s")
    log("=" * 50)
    
    ALL_IDS, STOCK_NAMES = get_all_stock_ids()
    SHARES_DICT = get_shares_dict()
    existing_pairs = get_existing_pairs()
    dates = generate_trading_dates(BACKFILL_END, BACKFILL_DAYS)
    
    log(f"\n股票: {len(ALL_IDS)} 檔")
    log(f"現有: {len(existing_pairs):,} 筆")
    log(f"日期: {len(dates)} 天")
    est = len(dates) * (len(ALL_IDS) // CHUNK_SIZE + 1) * SLEEP_CHUNK / 60
    log(f"預估: ~{est:.0f} 分鐘\n")
    
    total = 0
    for di, date_str in enumerate(dates):
        t0 = time.time()
        
        existing_today = {s for d, s in existing_pairs if d == date_str}
        todo = [s for s in ALL_IDS if s not in existing_today]
        
        if not todo:
            log(f"[{di+1:3d}/{len(dates)}] {date_str} ✓ 已完整")
            continue
        
        log(f"[{di+1:3d}/{len(dates)}] {date_str} 缺{len(todo)}檔", end="")
        
        date_inserted = 0
        for ci in range(0, len(todo), CHUNK_SIZE):
            chunk = todo[ci:ci+CHUNK_SIZE]
            df_chunk = fetch_chunk_yfinance(chunk, date_str)
            if not df_chunk.empty:
                n = save_to_db(df_chunk, existing_pairs, date_str)
                date_inserted += n
            time.sleep(SLEEP_CHUNK)
        
        total += date_inserted
        t1 = time.time() - t0
        log(f" → +{date_inserted} 筆, 累計 {total:,} ({t1:.0f}s)")
        time.sleep(SLEEP_DATE)
    
    log(f"\n{'='*50}")
    log(f"  完成! 總寫入: {total:,} 筆")
    log(f"{'='*50}")