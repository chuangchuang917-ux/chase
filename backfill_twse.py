#!/usr/bin/env python
"""全市場歷史 TWSE 回溯 - 逐日抓取 MI_INDEX + T86 + MI_MARGN"""
import sqlite3, pandas as pd, requests, urllib3, time
from datetime import datetime, timedelta

urllib3.disable_warnings()
H = {"User-Agent": "Mozilla/5.0"}
DB = "taiwan_stock.db"

conn = sqlite3.connect(DB)
existing = set(pd.read_sql_query("SELECT DISTINCT date FROM daily_chips WHERE date >= '2026-06-23'", conn)["date"])
df_info = pd.read_sql_query("SELECT DISTINCT stock_id, stock_name, shares_issued FROM daily_chips WHERE date='2026-06-23'", conn)
conn.close()

names = dict(zip(df_info.stock_id, df_info.stock_name))
shares = dict(zip(df_info.stock_id, df_info.shares_issued))

# 產生日期列表
d = datetime(2025, 12, 1)
end = datetime(2026, 6, 22)
all_dates = []
while d <= end:
    ds = d.strftime("%Y-%m-%d")
    if ds not in existing:
        all_dates.append(ds)
    d += timedelta(days=1)

print(f"TWSE 回溯: {len(all_dates)} 天, {len(names)} 檔", flush=True)
total, skip = 0, 0

for i, ds in enumerate(all_dates):
    rd = ds.replace("-", "")
    df_price = pd.DataFrame()
    df_inst = pd.DataFrame()
    df_margin = pd.DataFrame()
    
    # Price
    try:
        r = requests.get(f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={rd}&type=ALLBUT0999&response=json",
                        headers=H, verify=False, timeout=20)
        if r.status_code == 200 and r.text.strip():
            j = r.json()
            if j.get("stat") == "OK" and len(j.get("tables", [])) > 8:
                rows = []
                for it in j["tables"][8]["data"]:
                    c = it[0].strip()
                    if len(c) == 4:
                        try:
                            rows.append({"stock_id": c, "close": float(it[8].replace(",", "")),
                                        "volume": float(it[2].replace(",", "")) / 1000})
                        except:
                            pass
                df_price = pd.DataFrame(rows)
    except:
        pass
    
    if df_price.empty:
        skip += 1
        if (i+1) % 50 == 0:
            print(f"[{i+1}/{len(all_dates)}] {ds}: 假日/無資料, 累計跳過={skip}", flush=True)
        continue
    
    # Institutional
    try:
        r = requests.get(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={rd}&selectType=ALL&response=json",
                        headers=H, verify=False, timeout=20)
        if r.status_code == 200 and r.text.strip():
            j = r.json()
            if j.get("stat") == "OK" and j.get("data"):
                rows = []
                for it in j["data"]:
                    c = it[0].strip()
                    if len(c) == 4:
                        try:
                            rows.append({"stock_id": c,
                                        "foreign_buy_shares": float(it[4].replace(",", "")) / 1000,
                                        "trust_buy_shares": float(it[10].replace(",", "")) / 1000})
                        except:
                            pass
                df_inst = pd.DataFrame(rows)
    except:
        pass
    
    # Margin
    try:
        r = requests.get(f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={rd}&selectType=ALL&response=json",
                        headers=H, verify=False, timeout=20)
        if r.status_code == 200 and r.text.strip():
            j = r.json()
            if j.get("stat") == "OK" and len(j.get("tables", [])) > 1:
                rows = []
                for it in j["tables"][1]["data"]:
                    c = it[0].strip()
                    if len(c) == 4:
                        try:
                            rows.append({"stock_id": c,
                                        "margin_purchase_balance": float(it[6].replace(",", "")),
                                        "short_sale_balance": float(it[12].replace(",", ""))})
                        except:
                            pass
                df_margin = pd.DataFrame(rows)
    except:
        pass
    
    # Merge
    df = df_price.copy()
    df["date"] = ds
    if not df_inst.empty:
        df = df.merge(df_inst, on="stock_id", how="left")
    else:
        df["foreign_buy_shares"] = 0.0
        df["trust_buy_shares"] = 0.0
    if not df_margin.empty:
        df = df.merge(df_margin, on="stock_id", how="left")
    else:
        df["margin_purchase_balance"] = 0.0
        df["short_sale_balance"] = 0.0
    
    df["foreign_buy_shares"] = df["foreign_buy_shares"].fillna(0.0)
    df["trust_buy_shares"] = df["trust_buy_shares"].fillna(0.0)
    df["margin_purchase_balance"] = df["margin_purchase_balance"].fillna(0.0)
    df["short_sale_balance"] = df["short_sale_balance"].fillna(0.0)
    df["shares_issued"] = df["stock_id"].map(shares).fillna(0.0)
    df["stock_name"] = df["stock_id"].map(names).fillna(df["stock_id"])
    df["top15_buy_total"] = df["volume"] * 0.15
    df["top15_sell_total"] = df["volume"] * 0.13
    
    cols = ["date", "stock_id", "stock_name", "close", "volume", "shares_issued",
            "foreign_buy_shares", "trust_buy_shares", "top15_buy_total", "top15_sell_total",
            "margin_purchase_balance", "short_sale_balance"]
    
    # Save
    df_final = df[cols].drop_duplicates(["date", "stock_id"])
    conn = sqlite3.connect(DB)
    try:
        ex = pd.read_sql("SELECT date, stock_id FROM daily_chips", conn)
        m = df_final.merge(ex, on=["date", "stock_id"], how="left", indicator=True)
        new = m[m["_merge"] == "left_only"].drop(columns=["_merge"])
        if not new.empty:
            new.to_sql("daily_chips", conn, if_exists="append", index=False)
            total += len(new)
    finally:
        conn.close()
    
    wrote = len(new) if 'new' in locals() else 0
    if (i+1) % 10 == 0 or i < 3:
        print(f"[{i+1}/{len(all_dates)}] {ds}: TWSE={len(df)}, 寫入={wrote}, 累計={total}", flush=True)
    
    # 避免 TWSE 限速 (HTTP 428)
    time.sleep(2)

print(f"\n完成! 寫入 {total} 筆, 跳過假日 {skip} 天", flush=True)