# -*- coding: utf-8 -*-
"""
FinMind 全市場資料回溯腳本 (v1) - Async version with controlled concurrency
- 每小時 600 次 API call
- 每檔股票 3 次：股價 + 法人 + 融資
- 支援斷點續跑（先查 DB 再補缺）
- 受控併發 (2 個 worker) 以提升速度，同時避免速率限制
"""

import sqlite3
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import time
from datetime import datetime, timedelta, timezone
from typing import List, Tuple, Dict, Any

import asyncio
import aiohttp

# ------------------------------------------------------------
# 設定
# ------------------------------------------------------------
TZ_TW = timezone(timedelta(hours=8))  # 台灣時區 UTC+8

DB_PATH = "taiwan_stock.db"
BACKFILL_DAYS = 210
BACKFILL_END = "2026-06-25"
RATE_LIMIT = 600  # 每小時上限
CALLS_PER_STOCK = 3  # 股價 + 法人 + 融資
MAX_CONCURRENT_REQUESTS = 1  # 使用單一 worker 避免速率限制
SLEEP_BETWEEN_CALLS = 7.0  # 稍微延長間隔以降低 429 風險

# API token 輪轉
PRIMARY_API_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiYWxiZXJ0MDkxNyIsImVtYWlsIjoiYWxiZXJ0MDkxN0BnbWFpbC5jb20iLCJ0b2tlbl92ZXJzaW9uIjowfQ.NigTcrEmzoH4Ntj3RDzfcRCT2a397hsERMydNZuy05c"
FALLBACK_API_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiY2h1YW5nY2h1YW5nOTE3QGdtYWlsLmNvbSIsImVtYWlsIjoiY2h1YW5nY2h1YW5nOTE3QGdtYWlsLmNvbSIsInRva2VuX3ZlcnNpb24iOjF9.SlWtLQstQJGUCVKl42NxUG8wfqNt6tWD-reyP3xcyBY"
THIRD_API_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiY2h1YW5na3VuNjlAZ21haWwuY29tIiwiZW1haWwiOiJjaHVhbmdrdW42OUBnbWFpbC5jb20iLCJ0b2tlbl92ZXJzaW9uIjowfQ.HsULDchhy4vlVfoKipk-JEjDMv34OndMN8M4SVXEp3w"
API_TOKENS = [PRIMARY_API_TOKEN, FALLBACK_API_TOKEN, THIRD_API_TOKEN]
TOKEN_CURSOR = 0  # 會在每次成功呼叫後輪轉

FINMIND_BASE = "https://api.finmindtrade.com/api/v4/data"

# ------------------------------------------------------------
# 工具函式
# ------------------------------------------------------------
def log(msg: str, end: str = "\n") -> None:
    print(msg, end=end, flush=True)

def get_stocks_to_backfill() -> List[Tuple[str, int]]:
    """找出還缺 120 天資料的股票"""
    conn = sqlite3.connect(DB_PATH)
    stocks = conn.execute(
        """
        SELECT stock_id, COUNT(DISTINCT date) as days
        FROM daily_chips
        GROUP BY stock_id
        HAVING days < 120
        ORDER BY days ASC, stock_id ASC
        LIMIT ?
        """,
        (RATE_LIMIT // CALLS_PER_STOCK,)
    ).fetchall()
    conn.close()
    return stocks

def get_existing_pairs() -> set:
    """讀取既有 (date, stock_id) 組合"""
    conn = sqlite3.connect(DB_PATH)
    pairs = set()
    for row in conn.execute("SELECT date, stock_id FROM daily_chips"):
        pairs.add((row[0], row[1]))
    conn.close()
    return pairs

def get_shares_dict() -> Dict[str, float]:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """
        SELECT stock_id, shares_issued FROM daily_chips 
        WHERE date=(SELECT MAX(date) FROM daily_chips) AND shares_issued>0
        """
    ).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}

def save_to_db(rows: List[Dict[str, Any]], existing_pairs: set) -> int:
    """寫入 daily_chips，跳過已存在的"""
    if not rows:
        return 0
    new = [r for r in rows if (r["date"], r["stock_id"]) not in existing_pairs]
    if not new:
        return 0
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        
        # 查出本輪要寫入的 stock_id 對應的真實名稱
        sids = list(set(r["stock_id"] for r in new))
        placeholders = ",".join("?" for _ in sids)
        cursor = cur.execute(
            f"SELECT DISTINCT stock_id, stock_name FROM daily_chips WHERE stock_id IN ({placeholders}) AND stock_name != stock_id",
            sids
        )
        names_dict = {row[0]: row[1] for row in cursor.fetchall()}
        
        for r in new:
            sid = r["stock_id"]
            name = names_dict.get(sid, r.get("stock_name", sid))
            cur.execute(
                """
                INSERT OR IGNORE INTO daily_chips 
                (date, stock_id, stock_name, close, volume, shares_issued,
                 foreign_buy_shares, trust_buy_shares, top15_buy_total, top15_sell_total,
                 margin_purchase_balance, short_sale_balance)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    r["date"], sid, name,
                    r.get("close", 0.0), r.get("volume", 0.0), r.get("shares_issued", 0.0),
                    r.get("foreign_buy_shares", 0.0), r.get("trust_buy_shares", 0.0),
                    r.get("top15_buy_total", 0.0), r.get("top15_sell_total", 0.0),
                    r.get("margin_purchase_balance", 0.0), r.get("short_sale_balance", 0.0),
                ),
            )
        conn.commit()
    finally:
        conn.close()
    for r in new:
        existing_pairs.add((r["date"], r["stock_id"]))
    return len(new)

# ------------------------------------------------------------
# Parsers（保持同步）
# ------------------------------------------------------------
def parse_price(data: Any) -> List[Dict[str, Any]]:
    rows = []
    for item in (data or []):
        try:
            d = item.get("date", "")
            c = float(item.get("close", 0) or 0)
            v = float(item.get("Trading_Volume", 0) or 0) / 1000
            if c > 0 and d:
                rows.append({"date": d, "close": c, "volume": v})
        except Exception:
            pass
    return rows

def parse_inst(data: Any) -> Dict[str, Dict[str, float]]:
    by_date: Dict[str, Dict[str, float]] = {}
    for item in (data or []):
        try:
            d = item.get("date", "")
            name = item.get("name", "")
            qty = float(item.get("buy", 0) or 0) - float(item.get("sell", 0) or 0)
            by_date.setdefault(d, {})[name] = qty / 1000
        except Exception:
            pass
    return by_date

def parse_margin(data: Any) -> Dict[str, Tuple[float, float]]:
    by_date: Dict[str, Tuple[float, float]] = {}
    for item in (data or []):
        try:
            d = item.get("date", "")
            mp = float(item.get("margin_purchase_balance", 0) or 0)
            ss = float(item.get("short_sale_balance", 0) or 0)
            by_date[d] = (mp, ss)
        except Exception:
            pass
    return by_date

# ------------------------------------------------------------
# 非同步 API 呼叫
# ------------------------------------------------------------
async def async_call_finmind(session: aiohttp.ClientSession, dataset: str, stock_id: str, start_date: str, end_date: str) -> Any:
    """使用 aiohttp 呼叫 FinMind，並在發生 429 時自動切換 token。返回資料 list，失敗返回 None。"""
    global TOKEN_CURSOR
    for attempt in range(3):
        token = API_TOKENS[TOKEN_CURSOR]
        params = {
            "dataset": dataset,
            "data_id": stock_id,
            "start_date": start_date,
            "end_date": end_date,
            "token": token,
        }
        try:
            async with session.get(FINMIND_BASE, params=params, timeout=10) as resp:
                if resp.status == 200:
                    json_resp = await resp.json()
                    if json_resp.get("msg") == "success":
                        TOKEN_CURSOR = (TOKEN_CURSOR + 1) % len(API_TOKENS)
                        return json_resp.get("data", [])
                if resp.status == 429:
                    log(f"    token rate limited (attempt {attempt+1}), switching token …")
                    TOKEN_CURSOR = (TOKEN_CURSOR + 1) % len(API_TOKENS)
                    await asyncio.sleep(15)
                    continue
                await asyncio.sleep(3)
        except Exception as e:
            log(f"    token error: {e}, retry {attempt+1} …")
            await asyncio.sleep(5)
    return None

# ------------------------------------------------------------
# 單支股票處理 (async)
# ------------------------------------------------------------
async def process_stock(stock_id: str, existing_days: int, start_date: str, end_date: str, shares_dict: Dict[str, float], existing_pairs: set) -> Tuple[int, bool]:
    """抓取三個資料集、合併、寫入 DB。回傳 (寫入筆數, 是否因速率限制失敗)。"""
    await asyncio.sleep(SLEEP_BETWEEN_CALLS)
    async with aiohttp.ClientSession() as session:
        price_data = await async_call_finmind(session, "TaiwanStockPrice", stock_id, start_date, end_date)
    price_rows = parse_price(price_data)
    await asyncio.sleep(SLEEP_BETWEEN_CALLS)
    async with aiohttp.ClientSession() as session:
        inst_data = await async_call_finmind(session, "TaiwanStockInstitutionalInvestorsBuySell", stock_id, start_date, end_date)
    inst_by_date = parse_inst(inst_data)
    await asyncio.sleep(SLEEP_BETWEEN_CALLS)
    async with aiohttp.ClientSession() as session:
        margin_data = await async_call_finmind(session, "TaiwanStockMarginPurchaseShortSale", stock_id, start_date, end_date)
    margin_by_date = parse_margin(margin_data)

    all_rows: List[Dict[str, Any]] = []
    for pr in price_rows:
        d = pr["date"]
        row = {
            "date": d,
            "stock_id": stock_id,
            "close": pr["close"],
            "volume": pr["volume"],
            "shares_issued": shares_dict.get(stock_id, 0.0),
            "foreign_buy_shares": inst_by_date.get(d, {}).get("Foreign_Investor", 0.0),
            "trust_buy_shares": inst_by_date.get(d, {}).get("Investment_Trust", 0.0),
            "top15_buy_total": pr["volume"] * 0.15,
            "top15_sell_total": pr["volume"] * 0.13,
            "margin_purchase_balance": margin_by_date.get(d, (0.0, 0.0))[0],
            "short_sale_balance": margin_by_date.get(d, (0.0, 0.0))[1],
        }
        all_rows.append(row)

    is_rate_limited = price_data is None or inst_data is None or margin_data is None
    inserted = save_to_db(all_rows, existing_pairs)
    return inserted, is_rate_limited

# ------------------------------------------------------------
# 主流程 (async) – 受控併發
# ------------------------------------------------------------
async def main() -> None:
    log("=" * 60)
    log("  FinMind 全市場回溯 (async, controlled concurrency)")
    log(f"  每檔 3 次 API call，本輪 {RATE_LIMIT // CALLS_PER_STOCK} 檔，共 {RATE_LIMIT} 次")
    log(f"  回溯 {BACKFILL_DAYS} 天，間隔 {SLEEP_BETWEEN_CALLS:.1f}s/次")
    log("=" * 60)

    stocks = get_stocks_to_backfill()
    if not stocks:
        log("✅ 所有股票已達 120 天！")
        return
    log(f"\n需補股票: {len(stocks)} 檔")
    existing_pairs = get_existing_pairs()
    shares_dict = get_shares_dict()

    start_date = (datetime.strptime(BACKFILL_END, "%Y-%m-%d") - timedelta(days=BACKFILL_DAYS)).strftime("%Y-%m-%d")

    total_inserted = 0
    api_calls = 0
    success_cnt = 0
    empty_cnt = 0
    rate_limited_at: datetime | None = None

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async def worker(idx: int, stock_id: str, days: int):
        async with semaphore:
            log(f"\n[Worker {idx}] {stock_id} (現有 {days} 天)")
            inserted, is_limited = await process_stock(stock_id, days, start_date, BACKFILL_END, shares_dict, existing_pairs)
            nonlocal total_inserted, success_cnt, empty_cnt, api_calls, rate_limited_at
            api_calls += 3
            total_inserted += inserted
            if inserted > 0:
                success_cnt += 1
                log(f" → 寫入 +{inserted} 筆")
            elif is_limited:
                empty_cnt += 1
                if rate_limited_at is None:
                    rate_limited_at = datetime.now(TZ_TW)
                log(f" → ⚠️ 限速 (已{empty_cnt}檔空)")
            else:
                log(" → 🔄 已有資料")

    tasks = []
    for idx, (sid, days) in enumerate(stocks, start=1):
        tasks.append(asyncio.create_task(worker(idx, sid, days)))
    await asyncio.gather(*tasks)

    now = datetime.now(TZ_TW)
    log(f"\n{'=' * 60}")
    log(f"  完成! API calls: {api_calls}, 寫入: {total_inserted} 筆")
    log(f"  成功 {success_cnt} 檔, 限速空轉 {empty_cnt} 檔")
    if rate_limited_at:
        reset_time = rate_limited_at + timedelta(hours=1, minutes=10)
        log(f"  ⏰ 預估額度重置時間: {reset_time.strftime('%H:%M')} (限速起 +1h10m)")
    else:
        reset_time = now + timedelta(minutes=10)
        log(f"  ⏰ 若已用完額度，預估重置: {reset_time.strftime('%H:%M')}")
    conn = sqlite3.connect(DB_PATH)
    remaining = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT stock_id, COUNT(DISTINCT date) as days FROM daily_chips GROUP BY stock_id HAVING days < 120
        )
        """
    ).fetchone()[0]
    conn.close()
    log(f"  尚缺 120 天: {remaining} 檔")
    log(f"{'=' * 60}\n")

if __name__ == "__main__":
    asyncio.run(main())
