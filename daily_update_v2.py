"""
daily_update_v2.py
重構版每日自動化流程（新架構）：
  1. 從 Supabase daily_chips_raw 下載近 N 天歷史 → 重建暫存 SQLite（不依賴 GitHub Cache）
  2. 從 Supabase weekly_shareholders_raw 下載歷史週資料 → 寫入暫存 SQLite
  3. 爬蟲抓取當日原始籌碼資料 → 寫入暫存 SQLite + Upsert 到 daily_chips_raw
  4. 若為週五，抓取集保週資料 → 寫入暫存 SQLite + Upsert 到 weekly_shareholders_raw
  5. 計算滾動指標（只算當日）→ Upsert 今日結果到 chase_strategy_results

新架構優點：
  - 完全不依賴 GitHub Actions cache（每次重建工作區）
  - 滾動指標的歷史窗口由 Supabase 補齊（固定下載 HISTORY_DAYS 天）
  - 每次只 Upsert 當日 ~2000 筆結果，執行快速（< 60 秒）
"""

import os
import sys
import sqlite3
import datetime
import time

import pandas as pd
import numpy as np
import requests

# ── 路徑設定 ─────────────────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

# 載入環境變數
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")
SUPABASE_URL  = os.environ.get("SUPABASE_URL", "https://xjalllcvwbgnxwcruhzz.supabase.co")
SUPABASE_KEY  = os.environ.get("SUPABASE_KEY", "")

# 暫存 SQLite 路徑（GitHub Actions 每次重建，本地可指向正式 DB）
DB_PATH = os.environ.get("DB_PATH", os.path.join(ROOT, "taiwan_stock.db"))

# 從 Supabase 下載多少天的歷史資料用於滾動計算（120天最長窗口 + 緩衝）
HISTORY_DAYS = 140
BATCH_SIZE   = 1000

# ── Supabase 設定 ─────────────────────────────────────────
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates",
}

# ── 動態注入設定給子模組 ──────────────────────────────────
import crawler as _crawler_mod
import database as _db_mod

if FINMIND_TOKEN:
    _crawler_mod.API_TOKEN = FINMIND_TOKEN
_crawler_mod.DB_PATH = DB_PATH

# ── 工具函式 ──────────────────────────────────────────────

def get_tw_now():
    """取得台灣時間 (UTC+8)。"""
    return datetime.datetime.utcnow() + datetime.timedelta(hours=8)


def get_latest_trading_day():
    """
    取得最新交易日日期字串 YYYY-MM-DD（優先自 TWSE/TPEx OpenAPI 取得真實最新開盤日）。
    若設定了 OVERRIDE_DATE 環境變數，優先使用。
    """
    override = os.environ.get("OVERRIDE_DATE", "").strip()
    if override:
        print(f"[INFO]  使用手動指定日期：{override}")
        return override

    headers = {"User-Agent": "Mozilla/5.0"}
    
    # 1. 嘗試從 TPEx OpenAPI 獲取最新交易日 (更新最快且穩定)
    try:
        r = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", headers=headers, verify=False, timeout=10)
        if r.status_code == 200:
            data = r.json()
            dates = []
            for item in data:
                raw_date = item.get("Date", "")
                if len(raw_date) >= 6:
                    year = int(raw_date[:-4]) + 1911
                    db_date = f"{year}-{raw_date[-4:-2]}-{raw_date[-2:]}"
                    dates.append(db_date)
            if dates:
                latest_date = max(dates)
                print(f"[INFO]  從 TPEx OpenAPI 偵測到最新交易日：{latest_date}")
                return latest_date
    except Exception as e:
        print(f"[WARNING] 無法從 TPEx OpenAPI 取得最新交易日: {e}")

    # 2. 嘗試從 TWSE OpenAPI 獲取最新交易日
    try:
        r = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=headers, verify=False, timeout=10)
        if r.status_code == 200:
            data = r.json()
            dates = []
            for item in data:
                raw_date = item.get("Date", "")
                if len(raw_date) >= 6:
                    year = int(raw_date[:-4]) + 1911
                    db_date = f"{year}-{raw_date[-4:-2]}-{raw_date[-2:]}"
                    dates.append(db_date)
            if dates:
                latest_date = max(dates)
                print(f"[INFO]  從 TWSE OpenAPI 偵測到最新交易日：{latest_date}")
                return latest_date
    except Exception as e:
        print(f"[WARNING] 無法從 TWSE OpenAPI 取得最新交易日: {e}")

    # 3. 備用機制：若 API 均失敗，採用基於時間的 fallback 估算
    tw_now = get_tw_now()
    tw_today = tw_now.date()
    
    # 若今天已收盤（16:30 之後）且今天是週一至週五，預設為今天，否則為昨天
    if tw_now.hour >= 16 and tw_today.weekday() < 5:
        candidate = tw_today
    else:
        candidate = tw_today - datetime.timedelta(days=1)
        
    while candidate.weekday() >= 5:
        candidate -= datetime.timedelta(days=1)
        
    print(f"[INFO]  OpenAPI 查詢失敗，採用時間估算最新交易日：{candidate}")
    return str(candidate)


def supabase_fetch_all(url, extra_headers=None):
    """分頁抓取 Supabase 所有資料（突破預設 1000 筆限制）。"""
    records = []
    offset = 0
    limit  = BATCH_SIZE
    while True:
        h = HEADERS.copy()
        if extra_headers:
            h.update(extra_headers)
        h["Range"] = f"{offset}-{offset+limit-1}"
        r = requests.get(url, headers=h, timeout=30)
        if r.status_code not in (200, 206):
            print(f"  [WARNING] Supabase 查詢失敗: {r.status_code} {r.text[:100]}")
            break
        data = r.json()
        if not data:
            break
        records.extend(data)
        if len(data) < limit:
            break
        offset += limit
    return records


def upsert_to_supabase(table, records):
    """批次 Upsert 到 Supabase 表格。"""
    if not records:
        return 0
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    ok_count = 0
    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i:i + BATCH_SIZE]
        for r in batch:
            for k, v in list(r.items()):
                if isinstance(v, float) and (pd.isna(v) or v in (float("inf"), float("-inf"))):
                    r[k] = None
                elif hasattr(v, "item"):
                    r[k] = v.item()
        resp = requests.post(url, headers=HEADERS, json=batch, timeout=60)
        if resp.status_code in (200, 201):
            ok_count += len(batch)
        else:
            print(f"  [ERROR] Upsert 失敗 ({table} {i}~{i+len(batch)}): {resp.status_code} {resp.text[:150]}")
    return ok_count


# ── 步驟 1：從 Supabase 重建暫存 SQLite ──────────────────

def rebuild_sqlite_from_supabase(target_date):
    """
    從 Supabase daily_chips_raw 下載最近 HISTORY_DAYS 天的歷史原始資料，
    重建（或補齊）暫存 SQLite 的 daily_chips 表格。
    """
    print(f"\n[STEP 1] 從 Supabase 下載最近 {HISTORY_DAYS} 天歷史原始資料...")

    # 計算起始日期
    start_dt = datetime.date.fromisoformat(target_date) - datetime.timedelta(days=HISTORY_DAYS)
    start_str = str(start_dt)

    url = (
        f"{SUPABASE_URL}/rest/v1/daily_chips_raw"
        f"?date=gte.{start_str}&order=date.asc,stock_id.asc"
    )
    records = supabase_fetch_all(url)

    if not records:
        print("  [WARNING] 無法從 Supabase 取得歷史原始資料（表格可能是空的）。")
        print("  將繼續執行，但滾動指標可能不準確（首次初始化屬正常現象）。")
        return

    df = pd.DataFrame(records)
    print(f"  下載完成：{len(df):,} 筆 ({df['date'].nunique()} 個交易日 from {df['date'].min()} to {df['date'].max()})")

    # 寫入暫存 SQLite（先確認資料庫與表格存在）
    _db_mod.init_db(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    try:
        # 查詢 SQLite 中已有哪些日期，只補充缺漏的
        existing = pd.read_sql_query("SELECT DISTINCT date FROM daily_chips", conn)
        existing_dates = set(existing["date"].tolist()) if not existing.empty else set()

        df_new = df[~df["date"].isin(existing_dates)].copy()
        if df_new.empty:
            print(f"  [SKIP] SQLite 已有所有歷史資料，無需補充。")
            return

        # 確保欄位一致
        cols = [
            "date", "stock_id", "stock_name", "close", "volume", "shares_issued",
            "foreign_buy_shares", "trust_buy_shares", "top15_buy_total",
            "top15_sell_total", "margin_purchase_balance", "short_sale_balance"
        ]
        for c in cols:
            if c not in df_new.columns:
                df_new[c] = 0.0

        df_new[cols].to_sql("daily_chips", conn, if_exists="append", index=False, chunksize=5000)
        print(f"  [OK] 補充 {len(df_new):,} 筆歷史原始資料至 SQLite。")
    finally:
        conn.close()


def rebuild_weekly_from_supabase(target_date):
    """
    從 Supabase weekly_shareholders_raw 下載週集保歷史，
    補齊暫存 SQLite 的 weekly_shareholders 表格。
    """
    print("\n[STEP 1.5] 從 Supabase 下載週集保歷史...")

    url = f"{SUPABASE_URL}/rest/v1/weekly_shareholders_raw?order=date.asc,stock_id.asc"
    records = supabase_fetch_all(url)

    if not records:
        print("  [WARNING] 無法取得週集保歷史（可能尚未建立表格）。")
        return

    df = pd.DataFrame(records)
    print(f"  下載完成：{len(df):,} 筆 ({df['date'].nunique()} 週)")

    conn = sqlite3.connect(DB_PATH)
    try:
        existing = pd.read_sql_query("SELECT DISTINCT date FROM weekly_shareholders", conn)
        existing_dates = set(existing["date"].tolist()) if not existing.empty else set()
        df_new = df[~df["date"].isin(existing_dates)].copy()
        if df_new.empty:
            print("  [SKIP] SQLite 已有所有週資料。")
            return
        cols = ["date", "stock_id", "holder_over_1000", "holder_over_400"]
        for c in cols:
            if c not in df_new.columns:
                df_new[c] = 0.0
        df_new[cols].to_sql("weekly_shareholders", conn, if_exists="append", index=False)
        print(f"  [OK] 補充 {len(df_new):,} 筆週資料至 SQLite。")
    finally:
        conn.close()


# ── 步驟 2：爬蟲抓當日資料 ───────────────────────────────

def crawl_and_save_daily(target_date):
    """
    呼叫 crawler 抓取當日籌碼原始資料，寫入 SQLite，
    再將當日原始資料 Upsert 到 Supabase daily_chips_raw。
    """
    print(f"\n[STEP 2] 爬蟲抓取 {target_date} 原始籌碼資料...")

    # 檢查 SQLite 是否已有當日資料
    conn = sqlite3.connect(DB_PATH)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM daily_chips WHERE date=?", (target_date,)
        ).fetchone()[0]
    finally:
        conn.close()

    if count > 0:
        print(f"  [SKIP] SQLite 已有 {count} 筆 {target_date} 資料，跳過爬蟲步驟。")
    else:
        _crawler_mod.fetch_and_save_data(target_date, target_date)

    # 不論是否爬取，都 Upsert 當日資料到 Supabase daily_chips_raw
    conn = sqlite3.connect(DB_PATH)
    try:
        df_day = pd.read_sql_query(
            "SELECT * FROM daily_chips WHERE date=?", conn, params=(target_date,)
        )
    finally:
        conn.close()

    if df_day.empty:
        print(f"  [WARNING] 爬蟲後 SQLite 仍無 {target_date} 資料！")
        return False

    ok = upsert_to_supabase("daily_chips_raw", df_day.to_dict(orient="records"))
    print(f"  [OK] Upsert {ok:,} 筆原始資料到 Supabase daily_chips_raw。")
    return True


# ── 步驟 3：週集保資料（週五才執行）───────────────────────

def crawl_and_save_weekly(target_date):
    """抓取最新週集保資料，寫入 SQLite + Upsert 到 weekly_shareholders_raw。"""
    print(f"\n[STEP 3] 爬蟲抓取週集保資料...")

    before_conn = sqlite3.connect(DB_PATH)
    try:
        max_week_before = before_conn.execute(
            "SELECT MAX(date) FROM weekly_shareholders"
        ).fetchone()[0] or ""
    finally:
        before_conn.close()

    _crawler_mod.fetch_and_save_weekly_data(target_date, target_date)

    # 找出新寫入的週資料
    conn = sqlite3.connect(DB_PATH)
    try:
        df_new_weekly = pd.read_sql_query(
            "SELECT * FROM weekly_shareholders WHERE date > ?",
            conn, params=(max_week_before,)
        )
    finally:
        conn.close()

    if df_new_weekly.empty:
        print("  [INFO] 無新週資料需要同步。")
        return

    ok = upsert_to_supabase("weekly_shareholders_raw", df_new_weekly.to_dict(orient="records"))
    print(f"  [OK] Upsert {ok:,} 筆週資料到 Supabase weekly_shareholders_raw。")


# ── 步驟 4：計算指標並 Upsert 當日結果 ──────────────────

def calculate_and_upsert_strategy(target_date):
    """
    載入 SQLite 全量資料，計算滾動指標，
    只取 target_date 這一天的結果 Upsert 到 chase_strategy_results。
    直接複用 sync_single_date.py 的邏輯（避免重複維護）。
    """
    print(f"\n[STEP 4] 計算 {target_date} 策略指標並 Upsert 到 Supabase...")

    import sync_single_date as _ssd
    _ssd.TARGET_DATE = target_date
    _ssd.DB_PATH = DB_PATH
    _ssd.SUPABASE_URL = SUPABASE_URL
    _ssd.SUPABASE_KEY = SUPABASE_KEY
    _ssd.HEADERS = HEADERS.copy()

    _ssd.main()


# ── 主流程 ────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("[START] Chase 每日自動更新流程（v2 新架構）")
    print(f"[TIME]  {get_tw_now().strftime('%Y-%m-%d %H:%M:%S')} (台灣時間)")
    print("=" * 60)

    # 確保資料庫結構存在
    _db_mod.init_db(DB_PATH)

    # 決定目標日期
    target_date = get_latest_trading_day()
    print(f"[INFO]  目標更新日期：{target_date}")

    t_total = time.time()

    # 步驟 1：從 Supabase 重建暫存 SQLite 歷史資料（不再依賴 GitHub Cache）
    rebuild_sqlite_from_supabase(target_date)
    rebuild_weekly_from_supabase(target_date)

    # 步驟 2：爬蟲抓當日資料 + 同步到 Supabase daily_chips_raw
    daily_ok = crawl_and_save_daily(target_date)
    if not daily_ok:
        print(f"[ERROR] 無法取得 {target_date} 的日籌碼資料，流程中止。")
        sys.exit(1)

    # 步驟 3：若今天是週五（或特殊集保更新日），抓週集保資料
    tw_weekday = get_tw_now().weekday()  # 0=Mon, 4=Fri
    if tw_weekday == 4:
        print("[INFO]  今天是週五，執行週集保抓取...")
        try:
            crawl_and_save_weekly(target_date)
        except Exception as e:
            print(f"[WARNING] 週集保抓取失敗（不中斷主流程）：{e}")

    # 步驟 4：計算策略指標並 Upsert 到 chase_strategy_results
    try:
        calculate_and_upsert_strategy(target_date)
    except Exception as e:
        print(f"[ERROR] 策略計算/同步失敗：{e}")
        sys.exit(1)

    elapsed = time.time() - t_total
    print("=" * 60)
    print(f"[DONE]  每日更新流程全部完成！總耗時：{elapsed:.1f} 秒")
    print("=" * 60)


if __name__ == "__main__":
    main()
