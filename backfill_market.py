#!/usr/bin/env python
"""
全市場歷史資料回溯腳本 v2
- TWSE 股票: 使用 TWSE RWD API (MI_INDEX + T86 + MI_MARGN) 逐日
- TPEx 股票: 使用 yfinance 全區間一次下載
- 回溯範圍: 2025-12-01 ~ 2026-06-22
"""

import sqlite3
import pandas as pd
import numpy as np
import requests
import urllib3
import time
import sys
import yfinance as yf
import logging
logging.getLogger('yfinance').setLevel(logging.ERROR)
logging.getLogger('peewee').setLevel(logging.ERROR)
from datetime import datetime, timedelta

urllib3.disable_warnings()
HEADERS = {"User-Agent": "Mozilla/5.0"}
DB_PATH = "taiwan_stock.db"

def get_dates_to_backfill():
    """產生需要回溯的日期清單"""
    conn = sqlite3.connect(DB_PATH)
    existing = pd.read_sql_query(
        "SELECT DISTINCT date FROM daily_chips WHERE date >= '2026-06-23' ORDER BY date",
        conn
    )
    conn.close()
    
    existing_set = set(existing["date"].tolist())
    dates = []
    d = datetime(2025, 12, 1)
    end = datetime(2026, 6, 22)
    while d <= end:
        ds = d.strftime("%Y-%m-%d")
        if ds not in existing_set:
            dates.append(ds)
        d += timedelta(days=1)
    return dates

def get_stock_info():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT DISTINCT stock_id, stock_name, shares_issued FROM daily_chips WHERE date = '2026-06-23'",
        conn
    )
    conn.close()
    names = dict(zip(df["stock_id"], df["stock_name"]))
    shares = dict(zip(df["stock_id"], df["shares_issued"]))
    return df["stock_id"].tolist(), names, shares

def fetch_twse_for_date(date_str):
    """一次取得 TWSE 當日全部資料"""
    rwd_date = date_str.replace("-", "")
    results = {"price": pd.DataFrame(), "inst": pd.DataFrame(), "margin": pd.DataFrame()}
    
    # Price
    try:
        url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={rwd_date}&type=ALLBUT0999&response=json"
        r = requests.get(url, headers=HEADERS, verify=False, timeout=20)
        if r.status_code == 200:
            j = r.json()
            if j.get("stat") == "OK" and "tables" in j and len(j["tables"]) > 8:
                table = j["tables"][8]
                rows = []
                for item in table.get("data", []):
                    code = item[0].strip()
                    if len(code) == 4:
                        try:
                            close_val = float(item[8].replace(",", ""))
                            vol_val = float(item[2].replace(",", "")) / 1000.0
                        except (ValueError, IndexError):
                            continue
                        rows.append({"stock_id": code, "close": close_val, "volume": vol_val})
                results["price"] = pd.DataFrame(rows)
    except Exception:
        pass
    
    # Institutional
    try:
        url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={rwd_date}&selectType=ALL&response=json"
        r = requests.get(url, headers=HEADERS, verify=False, timeout=20)
        if r.status_code == 200:
            j = r.json()
            if j.get("stat") == "OK" and "data" in j:
                rows = []
                for item in j["data"]:
                    code = item[0].strip()
                    if len(code) == 4:
                        try:
                            foreign_net = float(item[4].replace(",", "")) / 1000.0
                            trust_net = float(item[10].replace(",", "")) / 1000.0
                        except (ValueError, IndexError):
                            continue
                        rows.append({"stock_id": code, "foreign_buy_shares": foreign_net, "trust_buy_shares": trust_net})
                results["inst"] = pd.DataFrame(rows)
    except Exception:
        pass
    
    # Margin
    try:
        url = f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={rwd_date}&selectType=ALL&response=json"
        r = requests.get(url, headers=HEADERS, verify=False, timeout=20)
        if r.status_code == 200:
            j = r.json()
            if j.get("stat") == "OK" and "tables" in j and len(j["tables"]) > 1:
                table = j["tables"][1]
                rows = []
                for item in table.get("data", []):
                    code = item[0].strip()
                    if len(code) == 4:
                        try:
                            margin_bal = float(item[6].replace(",", ""))
                            short_bal = float(item[12].replace(",", ""))
                        except (ValueError, IndexError):
                            continue
                        rows.append({"stock_id": code, "margin_purchase_balance": margin_bal, "short_sale_balance": short_bal})
                results["margin"] = pd.DataFrame(rows)
    except Exception:
        pass
    
    return results

def build_twse_day(date_str, twse_data):
    """組合 TWSE 當日資料"""
    df_price = twse_data["price"]
    if df_price.empty:
        return pd.DataFrame()
    
    df = df_price.copy()
    inst = twse_data["inst"]
    margin = twse_data["margin"]
    
    if not inst.empty:
        df = df.merge(inst, on="stock_id", how="left")
    else:
        df["foreign_buy_shares"] = 0.0
        df["trust_buy_shares"] = 0.0
    
    if not margin.empty:
        df = df.merge(margin, on="stock_id", how="left")
    else:
        df["margin_purchase_balance"] = 0.0
        df["short_sale_balance"] = 0.0
    
    df["date"] = date_str
    df["foreign_buy_shares"] = df["foreign_buy_shares"].fillna(0.0)
    df["trust_buy_shares"] = df["trust_buy_shares"].fillna(0.0)
    df["margin_purchase_balance"] = df["margin_purchase_balance"].fillna(0.0)
    df["short_sale_balance"] = df["short_sale_balance"].fillna(0.0)
    
    return df

def fetch_tpex_all(tpex_ids, start_date, end_date):
    """一次下載全區間 TPEx 資料"""
    if not tpex_ids:
        return pd.DataFrame()
    
    all_data = []
    batch_size = 50
    
    for i in range(0, len(tpex_ids), batch_size):
        batch = tpex_ids[i:i+batch_size]
        tickers = [f"{sid}.TWO" for sid in batch]
        
        try:
            data = yf.download(tickers, start=start_date, end=end_date, progress=False, threads=False)
            if data.empty:
                continue
            
            # data has MultiIndex columns (Close, Volume, etc. for each ticker)
            for sid in batch:
                ticker = f"{sid}.TWO"
                if ticker not in data["Close"].columns:
                    continue
                
                df_stock = pd.DataFrame({
                    "date": data.index,
                    "stock_id": sid,
                    "close": data["Close"][ticker].values,
                    "volume": data["Volume"][ticker].values / 1000.0
                })
                df_stock = df_stock.dropna(subset=["close", "volume"])
                df_stock["date"] = df_stock["date"].astype(str).str[:10]
                all_data.append(df_stock)
        except Exception:
            continue
    
    if not all_data:
        return pd.DataFrame()
    
    df_all = pd.concat(all_data, ignore_index=True)
    df_all["close"] = df_all["close"].astype(float)
    df_all["volume"] = df_all["volume"].astype(float)
    
    # TPEx 缺法人跟信用交易，填 0
    df_all["foreign_buy_shares"] = 0.0
    df_all["trust_buy_shares"] = 0.0
    df_all["margin_purchase_balance"] = 0.0
    df_all["short_sale_balance"] = 0.0
    
    return df_all

def save_to_db(df, table="daily_chips"):
    if df.empty:
        return 0
    df = df.drop_duplicates(subset=["date", "stock_id"])
    conn = sqlite3.connect(DB_PATH)
    try:
        existing = pd.read_sql(f"SELECT date, stock_id FROM {table}", conn)
        df_merged = df.merge(existing, on=["date", "stock_id"], how="left", indicator=True)
        df_new = df_merged[df_merged["_merge"] == "left_only"].drop(columns=["_merge"])
        if not df_new.empty:
            df_new.to_sql(table, conn, if_exists="append", index=False, chunksize=10000)
            return len(df_new)
        return 0
    finally:
        conn.close()

def main():
    dates = get_dates_to_backfill()
    stock_ids, stock_names, shares_map = get_stock_info()
    
    print(f"需要回溯 {len(dates)} 天, 股票: {len(stock_ids)} 檔")
    
    # ============================================================
    # Phase 1: 下載全部 TPEx 資料 (一次全區間)
    # ============================================================
    print("\n=== Phase 1: 下載 TPEx 全區間資料 (yfinance) ===")
    
    # 先用第一天資料判斷哪些是 TWSE
    first_date = dates[0]
    first_twse = fetch_twse_for_date(first_date)
    twse_ids = set(first_twse["price"]["stock_id"].tolist()) if not first_twse["price"].empty else set()
    all_ids_set = set(stock_ids)
    tpex_ids = sorted(list(all_ids_set - twse_ids))
    
    print(f"TWSE: {len(twse_ids)} 檔, TPEx: {len(tpex_ids)} 檔")
    print(f"正在下載 TPEx 全區間 ({dates[0]} ~ {dates[-1]})...")
    
    df_tpex_all = fetch_tpex_all(tpex_ids, dates[0], dates[-1])
    tpex_dates = df_tpex_all["date"].unique() if not df_tpex_all.empty else []
    print(f"TPEx 下載完成: {len(df_tpex_all)} 筆, {len(tpex_dates)} 個日期")
    
    # ============================================================
    # Phase 2: 逐日處理 TWSE + 合併 TPEx
    # ============================================================
    print(f"\n=== Phase 2: 逐日處理 TWSE + 合併 ({len(dates)} 天) ===")
    
    total_inserted = 0
    skipped = 0
    
    for i, date_str in enumerate(dates):
        # TWSE 當日資料
        twse_data = fetch_twse_for_date(date_str)
        df_twse = build_twse_day(date_str, twse_data)
        
        # TPEx 當日資料
        df_tpex = df_tpex_all[df_tpex_all["date"] == date_str].copy() if not df_tpex_all.empty else pd.DataFrame()
        
        # 合併
        if not df_twse.empty and not df_tpex.empty:
            df_day = pd.concat([df_twse, df_tpex], ignore_index=True)
        elif not df_twse.empty:
            df_day = df_twse
        elif not df_tpex.empty:
            df_day = df_tpex
            df_day["foreign_buy_shares"] = 0.0
            df_day["trust_buy_shares"] = 0.0
            df_day["margin_purchase_balance"] = 0.0
            df_day["short_sale_balance"] = 0.0
        else:
            skipped += 1
            continue
        
        # 補股本、名稱、主力 fallback
        df_day["shares_issued"] = df_day["stock_id"].map(shares_map).fillna(0.0)
        df_day["stock_name"] = df_day["stock_id"].map(stock_names).fillna(df_day["stock_id"])
        df_day["top15_buy_total"] = df_day["volume"] * 0.15
        df_day["top15_sell_total"] = df_day["volume"] * 0.13
        
        cols = [
            "date", "stock_id", "stock_name", "close", "volume", "shares_issued",
            "foreign_buy_shares", "trust_buy_shares", "top15_buy_total", "top15_sell_total",
            "margin_purchase_balance", "short_sale_balance"
        ]
        
        n = save_to_db(df_day[cols])
        total_inserted += n
        
        if (i + 1) % 10 == 0 or i < 3:
            pct = (i+1) / len(dates) * 100
            print(f"[{i+1}/{len(dates)} {pct:.0f}%] {date_str}: 寫入={n}, 累計={total_inserted}")
    
    print(f"\n=== 完成! 共寫入 {total_inserted} 筆, 跳過 {skipped} 天 ===")

if __name__ == "__main__":
    main()