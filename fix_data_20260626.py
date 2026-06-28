# -*- coding: utf-8 -*-
import sqlite3
import requests
import urllib3
import pandas as pd
import numpy as np
import json

urllib3.disable_warnings()

DB_PATH = "taiwan_stock.db"
TARGET_DATE = "2026-06-26"
RWD_DATE = "20260626"
HEADERS = {"User-Agent": "Mozilla/5.0"}

import sys

def main():
    import os
    sys.stdout.reconfigure(encoding='utf-8')
    print("============================================================")
    print("Executing:", os.path.abspath(__file__))
    print(f"🧹 開始重爬並修正 {TARGET_DATE} 的資料...")
    print("============================================================")

    # 1. 取得全市場股票代號對照 (從 daily_chips 中最新一天取得，以保持 stock_name/stock_id 對照)
    conn = sqlite3.connect(DB_PATH)
    df_prev = pd.read_sql_query(
        "SELECT stock_id, stock_name, shares_issued FROM daily_chips "
        "WHERE date = (SELECT MAX(date) FROM daily_chips WHERE date < '2026-06-26')",
        conn
    )
    conn.close()
    
    if df_prev.empty:
        print("[ERROR] 無法取得前一日的股票清單！")
        return
        
    stock_dict = dict(zip(df_prev["stock_id"], df_prev["stock_name"]))
    shares_dict = dict(zip(df_prev["stock_id"], df_prev["shares_issued"]))
    target_stocks = df_prev["stock_id"].tolist()
    print(f"[INFO] 取得前一日股票名單共 {len(target_stocks)} 檔。")

    # ------------------
    # A. 抓取上市日報價與信用交易與法人資料 (TWSE)
    # ------------------
    print("\n[INFO] 正在抓取 TWSE 日報價 (MI_INDEX)...")
    df_twse_price = pd.DataFrame()
    url_mi = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={RWD_DATE}&type=ALLBUT0999&response=json"
    r = requests.get(url_mi, headers=HEADERS, verify=False, timeout=20)
    if r.status_code == 200:
        json_data = r.json()
        if json_data.get("stat") == "OK" and "tables" in json_data and len(json_data["tables"]) > 8:
            table = json_data["tables"][8]
            rows = []
            for item in table.get("data", []):
                code = item[0].strip()
                if len(code) == 4:
                    try:
                        close_val = float(item[8].replace(",", ""))
                        vol_val = float(item[2].replace(",", "")) / 1000.0
                    except ValueError:
                        close_val = 0.0
                        vol_val = 0.0
                    rows.append({
                        "date": TARGET_DATE,
                        "stock_id": code,
                        "close": close_val,
                        "volume": vol_val
                    })
            df_twse_price = pd.DataFrame(rows)
            print(f"  ✅ 成功取得 TWSE 報價 {len(df_twse_price)} 筆。")
        else:
            print("  ❌ TWSE MI_INDEX 無法取得 OK 狀態。")
    else:
        print(f"  ❌ TWSE MI_INDEX 請求失敗 {r.status_code}。")

    print("\n[INFO] 正在抓取 TWSE 三大法人 (T86)...")
    df_twse_inst = pd.DataFrame()
    url_t86 = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={RWD_DATE}&selectType=ALL&response=json"
    r = requests.get(url_t86, headers=HEADERS, verify=False, timeout=20)
    if r.status_code == 200:
        json_data = json.loads(r.content.decode("utf-8"))
        if json_data.get("stat") == "OK" and "data" in json_data:
            # 動態從 fields 解析欄位索引
            fields = json_data.get("fields", [])
            foreign_idx = -1
            trust_idx = -1
            for idx, f_name in enumerate(fields):
                if "外" in f_name and "買賣超" in f_name:
                    if "不含外資自營商" in f_name:
                        foreign_idx = idx
                    elif foreign_idx == -1:
                        foreign_idx = idx
                elif "投信" in f_name and "買賣超" in f_name:
                    trust_idx = idx

            if foreign_idx == -1:
                foreign_idx = 4
            if trust_idx == -1:
                trust_idx = 10

            rows = []
            for item in json_data["data"]:
                code = item[0].strip()
                if len(code) == 4:
                    try:
                        # 防禦欄位長度不足的例外情況
                        f_val = item[foreign_idx] if foreign_idx < len(item) else "0"
                        t_val = item[trust_idx] if trust_idx < len(item) else "0"
                        foreign_net = float(str(f_val).replace(",", "")) / 1000.0
                        trust_net = float(str(t_val).replace(",", "")) / 1000.0
                    except (ValueError, IndexError):
                        foreign_net = 0.0
                        trust_net = 0.0
                    rows.append({
                        "stock_id": code,
                        "foreign_buy_shares": foreign_net,
                        "trust_buy_shares": trust_net
                    })
            df_twse_inst = pd.DataFrame(rows)
            print(f"  ✅ 成功取得 TWSE 三大法人 {len(df_twse_inst)} 筆。")
        else:
            print("  ❌ TWSE T86 無法取得 OK 狀態。")

    print("\n[INFO] 正在抓取 TWSE 信用交易 (MI_MARGN)...")
    df_twse_margin = pd.DataFrame()
    url_margin = f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={RWD_DATE}&selectType=ALL&response=json"
    r = requests.get(url_margin, headers=HEADERS, verify=False, timeout=20)
    if r.status_code == 200:
        json_data = r.json()
        if json_data.get("stat") == "OK" and "tables" in json_data and len(json_data["tables"]) > 1:
            table = json_data["tables"][1]
            rows = []
            for item in table.get("data", []):
                code = item[0].strip()
                if len(code) == 4:
                    try:
                        margin_bal = float(item[6].replace(",", ""))
                        short_bal = float(item[12].replace(",", ""))
                    except ValueError:
                        margin_bal = 0.0
                        short_bal = 0.0
                    rows.append({
                        "stock_id": code,
                        "margin_purchase_balance": margin_bal,
                        "short_sale_balance": short_bal
                    })
            df_twse_margin = pd.DataFrame(rows)
            print(f"  ✅ 成功取得 TWSE 信用交易 {len(df_twse_margin)} 筆。")
        else:
            print("  ❌ TWSE MI_MARGN 無法取得 OK 狀態。")

    # ------------------
    # B. 抓取上櫃日報價與信用交易與法人資料 (TPEx)
    # ------------------
    print("\n[INFO] 正在抓取 TPEx 日報價...")
    df_tpex_price = pd.DataFrame()
    url_tpex_close = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
    r = requests.get(url_tpex_close, headers=HEADERS, verify=False, timeout=20)
    if r.status_code == 200:
        raw_data = r.json()
        rows = []
        for item in raw_data:
            code = item.get("SecuritiesCompanyCode", "").strip()
            if len(code) == 4:
                raw_date = item.get("Date", "")
                if len(raw_date) >= 6:
                    year = int(raw_date[:-4]) + 1911
                    db_date = f"{year}-{raw_date[-4:-2]}-{raw_date[-2:]}"
                else:
                    continue
                if db_date != TARGET_DATE:
                    continue
                try:
                    close_val = float(item.get("Close", 0.0))
                    vol_val = float(item.get("TradingShares", 0.0)) / 1000.0
                except ValueError:
                    close_val = 0.0
                    vol_val = 0.0
                rows.append({
                    "date": TARGET_DATE,
                    "stock_id": code,
                    "close": close_val,
                    "volume": vol_val
                })
        df_tpex_price = pd.DataFrame(rows)
        print(f"  ✅ 成功取得 TPEx 報價 {len(df_tpex_price)} 筆。")

    print("\n[INFO] 正在抓取 TPEx 三大法人...")
    df_tpex_inst = pd.DataFrame()
    url_tpex_inst = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"
    r = requests.get(url_tpex_inst, headers=HEADERS, verify=False, timeout=20)
    if r.status_code == 200:
        raw_data = r.json()
        rows = []
        for item in raw_data:
            code = item.get("SecuritiesCompanyCode", "").strip()
            if len(code) == 4:
                try:
                    # 注意欄位名稱長度
                    foreign_net = float(item.get("Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference", 0.0)) / 1000.0
                    trust_net = float(item.get("SecuritiesInvestmentTrustCompanies-Difference", 0.0)) / 1000.0
                except ValueError:
                    foreign_net = 0.0
                    trust_net = 0.0
                rows.append({
                    "stock_id": code,
                    "foreign_buy_shares": foreign_net,
                    "trust_buy_shares": trust_net
                })
        df_tpex_inst = pd.DataFrame(rows)
        print(f"  ✅ 成功取得 TPEx 三大法人 {len(df_tpex_inst)} 筆。")

    print("\n[INFO] 正在抓取 TPEx 信用交易...")
    df_tpex_margin = pd.DataFrame()
    url_tpex_margin = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance"
    r = requests.get(url_tpex_margin, headers=HEADERS, verify=False, timeout=20)
    if r.status_code == 200:
        raw_data = r.json()
        rows = []
        for item in raw_data:
            code = item.get("SecuritiesCompanyCode", "").strip()
            if len(code) == 4:
                try:
                    margin_bal = float(item.get("MarginPurchaseBalance", "0").replace(",", ""))
                    short_bal = float(item.get("ShortSaleBalance", "0").replace(",", ""))
                except ValueError:
                    margin_bal = 0.0
                    short_bal = 0.0
                rows.append({
                    "stock_id": code,
                    "margin_purchase_balance": margin_bal,
                    "short_sale_balance": short_bal
                })
        df_tpex_margin = pd.DataFrame(rows)
        print(f"  ✅ 成功取得 TPEx 信用交易 {len(df_tpex_margin)} 筆。")

    # ------------------
    # C. 合併與清洗
    # ------------------
    print("\n[INFO] 正在合併全市場上市櫃資料...")
    df_price = pd.concat([df_twse_price, df_tpex_price], ignore_index=True)
    df_inst = pd.concat([df_twse_inst, df_tpex_inst], ignore_index=True)
    df_margin = pd.concat([df_twse_margin, df_tpex_margin], ignore_index=True)

    if df_price.empty:
        print("[ERROR] 合併後的股價日報表為空，無法進行修正！")
        return

    # 合併
    df_merged = pd.merge(df_price, df_inst, on="stock_id", how="left")
    df_merged = pd.merge(df_merged, df_margin, on="stock_id", how="left")
    
    # 僅保留 target_stocks
    df_merged = df_merged[df_merged["stock_id"].isin(target_stocks)].copy()
    
    df_merged["stock_name"] = df_merged["stock_id"].map(stock_dict)
    df_merged["shares_issued"] = df_merged["stock_id"].map(shares_dict).fillna(0.0)
    
    df_merged["foreign_buy_shares"] = df_merged["foreign_buy_shares"].fillna(0.0)
    df_merged["trust_buy_shares"] = df_merged["trust_buy_shares"].fillna(0.0)
    df_merged["margin_purchase_balance"] = df_merged["margin_purchase_balance"].fillna(0.0)
    df_merged["short_sale_balance"] = df_merged["short_sale_balance"].fillna(0.0)
    
    # 估算 top15 買賣超 (OpenAPI 備用估計)
    df_merged["top15_buy_total"] = df_merged["volume"] * 0.15
    df_merged["top15_sell_total"] = df_merged["volume"] * 0.13

    cols = [
        "date", "stock_id", "stock_name", "close", "volume", "shares_issued",
        "foreign_buy_shares", "trust_buy_shares", "top15_buy_total", "top15_sell_total",
        "margin_purchase_balance", "short_sale_balance"
    ]
    df_final = df_merged[cols].dropna(subset=["stock_name"])
    print("1101 in df_final:", df_final[df_final["stock_id"] == "1101"].to_dict("records"))
    
    # ------------------
    # D. 寫入 SQLite
    # ------------------
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. 刪除原有 2026-06-26 數據
    cursor.execute("DELETE FROM daily_chips WHERE date=?", (TARGET_DATE,))
    print(f"\n[INFO] 已從 SQLite 刪除舊的 {TARGET_DATE} 數據，共 {cursor.rowcount} 筆。")
    
    # 2. 插入全新正確數據
    records = df_final.to_dict(orient="records")
    insert_sql = """
        INSERT INTO daily_chips (
            date, stock_id, stock_name, close, volume, shares_issued,
            foreign_buy_shares, trust_buy_shares, top15_buy_total, top15_sell_total,
            margin_purchase_balance, short_sale_balance
        ) VALUES (
            :date, :stock_id, :stock_name, :close, :volume, :shares_issued,
            :foreign_buy_shares, :trust_buy_shares, :top15_buy_total, :top15_sell_total,
            :margin_purchase_balance, :short_sale_balance
        )
    """
    cursor.executemany(insert_sql, records)
    conn.commit()
    print(f"✅ 成功寫入全新正確的 {TARGET_DATE} 數據到 SQLite，共 {len(records)} 筆！")
    
    conn.close()

if __name__ == "__main__":
    main()
