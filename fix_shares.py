"""一次性補齊全市場發行張數 (shares_issued)"""
import sqlite3
import requests
import urllib3
import pandas as pd
urllib3.disable_warnings()

DB_PATH = "taiwan_stock.db"
headers = {"User-Agent": "Mozilla/5.0"}

# 1. TWSE 上市股 ── 從 t187ap03_L 抓已發行普通股數
print("[1/2] 抓取 TWSE 上市股發行張數...")
try:
    r = requests.get(
        "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
        headers=headers, verify=False, timeout=15
    )
    twse_data = r.json()
    twse_shares = {}
    for item in twse_data:
        code = item.get("公司代號", "").strip()
        if len(code) == 4:
            try:
                raw = item.get("已發行普通股數或TDR原股發行股數", "0")
                shares = float(str(raw).replace(",", ""))
                twse_shares[code] = shares / 1000.0  # 股 → 張
            except (ValueError, TypeError):
                pass
    print(f"  取得 {len(twse_shares)} 檔上市股發行張數")
except Exception as e:
    print(f"  TWSE 失敗: {e}")
    twse_shares = {}

# 2. TPEx 上櫃股 ── 從 tpex_mainboard_daily_close_quotes 抓 Capitals
print("[2/2] 抓取 TPEx 上櫃股發行張數...")
try:
    r = requests.get(
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
        headers=headers, verify=False, timeout=15
    )
    tpex_data = r.json()
    tpex_shares = {}
    for item in tpex_data:
        code = item.get("SecuritiesCompanyCode", "").strip()
        if len(code) == 4 and not code.startswith("00"):
            try:
                raw = item.get("Capitals", "0")
                shares = float(str(raw).replace(",", ""))
                tpex_shares[code] = shares / 1000.0  # 股 → 張
            except (ValueError, TypeError):
                pass
    print(f"  取得 {len(tpex_shares)} 檔上櫃股發行張數")
except Exception as e:
    print(f"  TPEx 失敗: {e}")
    tpex_shares = {}

# 合併
all_shares = {**twse_shares, **tpex_shares}
print(f"\n合計 {len(all_shares)} 檔股票有發行張數資料")

# 寫入資料庫
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
updated = 0
for stock_id, shares in all_shares.items():
    cursor.execute(
        "UPDATE daily_chips SET shares_issued = ? WHERE stock_id = ?",
        (shares, stock_id)
    )
    updated += cursor.rowcount

conn.commit()

# 檢查結果
remaining = cursor.execute(
    "SELECT count(*) FROM daily_chips WHERE date = (SELECT MAX(date) FROM daily_chips) AND shares_issued = 0"
).fetchone()[0]

conn.close()
print(f"更新了 {updated} 筆記錄")
print(f"剩餘 shares_issued = 0 的股票: {remaining} 檔")