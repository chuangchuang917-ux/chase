# -*- coding: utf-8 -*-
import sqlite3
import requests
import urllib3
import pandas as pd
import sys

sys.stdout.reconfigure(encoding='utf-8')
urllib3.disable_warnings()

DB_PATH = "taiwan_stock.db"
TARGET_DATE = "2026-06-26"
RWD_DATE = "20260626"
HEADERS = {"User-Agent": "Mozilla/5.0"}

def main():
    print("============================================================")
    print(f"📊 開始對比 SQLite 與官方交易所數據 ({TARGET_DATE}) ...")
    print("============================================================")

    # 1. 取得 TWSE T86 官方數據 (上市)
    twse_dict = {}
    url_t86 = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={RWD_DATE}&selectType=ALL&response=json"
    r = requests.get(url_t86, headers=HEADERS, verify=False, timeout=20)
    print(f"[DEBUG] TWSE Status Code: {r.status_code}")
    if r.status_code == 200:
        json_data = r.json()
        print(f"[DEBUG] TWSE stat: {json_data.get('stat')}, has data: {'data' in json_data}")
        if json_data.get("stat") == "OK" and "data" in json_data:
            for item in json_data["data"]:
                code = item[0].strip()
                if len(code) == 4:
                    try:
                        foreign_net = float(item[4].replace(",", "")) / 1000.0
                        trust_net = float(item[10].replace(",", "")) / 1000.0
                    except ValueError:
                        foreign_net = 0.0
                        trust_net = 0.0
                    twse_dict[code] = {"foreign": foreign_net, "trust": trust_net}

    # 2. 取得 TPEx 三大法人官方數據 (上櫃)
    tpex_dict = {}
    url_tpex_inst = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"
    r = requests.get(url_tpex_inst, headers=HEADERS, verify=False, timeout=20)
    if r.status_code == 200:
        raw_data = r.json()
        for item in raw_data:
            code = item.get("SecuritiesCompanyCode", "").strip()
            if len(code) == 4:
                try:
                    foreign_net = float(item.get("Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference", 0.0)) / 1000.0
                    trust_net = float(item.get("SecuritiesInvestmentTrustCompanies-Difference", 0.0)) / 1000.0
                except ValueError:
                    foreign_net = 0.0
                    trust_net = 0.0
                tpex_dict[code] = {"foreign": foreign_net, "trust": trust_net}

    official_dict = {**twse_dict, **tpex_dict}
    print(f"[INFO] 成功從官方交易所取得 {len(official_dict)} 檔股票法人買賣超數據。")

    # 3. 讀取 SQLite 當天法人數據
    conn = sqlite3.connect(DB_PATH)
    df_db = pd.read_sql_query(
        "SELECT stock_id, stock_name, foreign_buy_shares, trust_buy_shares FROM daily_chips WHERE date=?",
        conn, params=(TARGET_DATE,)
    )
    conn.close()
    
    if df_db.empty:
        print("[ERROR] SQLite 資料庫中沒有 2026-06-26 的資料！")
        return

    print(f"[INFO] SQLite 資料庫中共有 {len(df_db)} 檔股票紀錄。")

    # 4. 開始逐一核對
    matched_count = 0
    mismatched_count = 0
    mismatch_details = []

    for idx, row in df_db.iterrows():
        sid = row["stock_id"]
        db_foreign = row["foreign_buy_shares"]
        db_trust = row["trust_buy_shares"]
        
        # 取得官方對應數據，如官方無資料則預設為 0.0
        off_data = official_dict.get(sid, {"foreign": 0.0, "trust": 0.0})
        off_foreign = off_data["foreign"]
        off_trust = off_data["trust"]
        
        # 比對 (包容小數點些微精確度差異 <= 0.001)
        foreign_match = (abs(db_foreign - off_foreign) <= 0.001)
        trust_match = (abs(db_trust - off_trust) <= 0.001)
        
        if foreign_match and trust_match:
            matched_count += 1
        else:
            mismatched_count += 1
            mismatch_details.append({
                "stock_id": sid,
                "name": row["stock_name"],
                "db_foreign": db_foreign,
                "off_foreign": off_foreign,
                "db_trust": db_trust,
                "off_trust": off_trust
            })

    print("============================================================")
    print("📊 2026-06-26 全市場法人數據核對報告")
    print(f"  總核對檔數: {len(df_db)} 檔")
    print(f"  完全一致檔數: {matched_count} 檔")
    print(f"  不一致檔數: {mismatched_count} 檔")
    
    error_rate = (mismatched_count / len(df_db)) * 100
    print(f"  數據比對錯誤率: {error_rate:.2f}%")
    print(f"  數據完全匹配率 (Accuracy): {100 - error_rate:.2f}%")
    print("============================================================")
    
    if mismatched_count > 0:
        print("\n❌ 不一致明細 (前 10 筆):")
        for i, detail in enumerate(mismatch_details[:10]):
            print(f"  股票: {detail['stock_id']} ({detail['name']}) | "
                  f"外資 (DB: {detail['db_foreign']:.3f} vs 官方: {detail['off_foreign']:.3f}) | "
                  f"投信 (DB: {detail['db_trust']:.3f} vs 官方: {detail['off_trust']:.3f})")

if __name__ == "__main__":
    main()
