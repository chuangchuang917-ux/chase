"""
daily_update.py
每日自動化流程：
1. 抓取昨日（或最近一個交易日）的全市場日資料寫入 SQLite
2. 重新計算策略指標並批次同步至 Supabase
"""

import os
import sys
import sqlite3
import datetime
import importlib
import time
import database as _db_mod

# ── 路徑設定 ─────────────────────────────────────────────
# 確保能 import 同目錄的 crawler 與 sync_to_supabase_bulk
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

# 載入環境變數（GitHub Actions 中由 Secrets 注入）
FINMIND_TOKEN   = os.environ.get("FINMIND_TOKEN", "")
SUPABASE_URL    = os.environ.get("SUPABASE_URL", "https://xjalllcvwbgnxwcruhzz.supabase.co")
SUPABASE_KEY    = os.environ.get("SUPABASE_KEY", "")

# ── 動態注入設定給子模組 ──────────────────────────────────
# crawler.py 讀取模組層 API_TOKEN / DB_PATH
import crawler as _crawler_mod
if FINMIND_TOKEN:
    _crawler_mod.API_TOKEN = FINMIND_TOKEN
_crawler_mod.DB_PATH = os.path.join(ROOT, "taiwan_stock.db")

# sync_to_supabase_bulk.py 讀取模組層 SUPABASE_URL / SUPABASE_KEY / DB_PATH
import sync_to_supabase_bulk as _sync_mod
if SUPABASE_URL:
    _sync_mod.SUPABASE_URL = SUPABASE_URL
if SUPABASE_KEY:
    _sync_mod.SUPABASE_KEY = SUPABASE_KEY
    _sync_mod.HEADERS["apikey"] = SUPABASE_KEY
    _sync_mod.HEADERS["Authorization"] = f"Bearer {SUPABASE_KEY}"
_sync_mod.DB_PATH = os.path.join(ROOT, "taiwan_stock.db")

# ── 工具函式 ──────────────────────────────────────────────
def get_latest_trading_day():
    """
    取得「上一個交易日」的日期字串 YYYY-MM-DD (基於台灣時間 UTC+8)。
    台灣交易日為週一到週五（簡單判斷，不考慮國定假日）。
    """
    # 取得台灣時間 (UTC+8) 的今日日期，以相容 GitHub Actions 伺服器 UTC 時區
    utc_now = datetime.datetime.utcnow()
    tw_now = utc_now + datetime.timedelta(hours=8)
    today = tw_now.date()
    
    # 往前推到最近的平日
    delta = 1
    candidate = today - datetime.timedelta(days=delta)
    while candidate.weekday() >= 5:   # 0=Mon … 4=Fri, 5=Sat, 6=Sun
        delta += 1
        candidate = today - datetime.timedelta(days=delta)
    return str(candidate)


def date_already_in_db(target_date: str) -> bool:
    """
    檢查 SQLite 中 daily_chips 是否已有 target_date 的資料。
    """
    db = os.path.join(ROOT, "taiwan_stock.db")
    if not os.path.exists(db):
        return False
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM daily_chips WHERE date=?", (target_date,))
    count = cur.fetchone()[0]
    conn.close()
    return count > 0


# ── 主流程 ────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"[START] Chase 每日自動更新流程啟動")
    print(f"[TIME]  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 確保資料庫結構存在
    _db_mod.init_db(os.path.join(ROOT, "taiwan_stock.db"))

    target_date = get_latest_trading_day()
    print(f"[INFO]  目標更新日期：{target_date}")

    # ── 步驟 1：抓取當日資料 ──────────────────────────────
    if date_already_in_db(target_date):
        print(f"[SKIP]  {target_date} 的資料已存在於 SQLite，跳過爬取步驟。")
    else:
        print(f"[STEP1] 開始從 FinMind 抓取 {target_date} 的資料...")
        try:
            _crawler_mod.fetch_and_save_data(target_date, target_date)
            print(f"[STEP1] ✅ 成功寫入 {target_date} 的資料至 SQLite。")
        except Exception as e:
            print(f"[ERROR] STEP1 失敗：{e}")
            sys.exit(1)

    # ── 步驟 2：重算策略指標並同步至 Supabase ────────────
    print(f"[STEP2] 開始計算策略指標並同步至 Supabase...")
    t0 = time.time()
    try:
        _sync_mod.sync_data_bulk()
        elapsed = time.time() - t0
        print(f"[STEP2] ✅ Supabase 同步完成！耗時 {elapsed:.1f} 秒。")
    except Exception as e:
        print(f"[ERROR] STEP2 失敗：{e}")
        sys.exit(1)

    print("=" * 60)
    print("[DONE]  每日更新流程全部完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
