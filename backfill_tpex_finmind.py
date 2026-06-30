# -*- coding: utf-8 -*-
"""
FinMind OTC (上櫃) 股歷史資料回溯更新腳本
- 補齊 2026-06-19 以前上櫃股缺失的法人買賣超與信用交易資料
- 使用 3 組 FinMind 憑證輪轉，防 429 限速
- 可中斷後續跑（排除已補齊的股票）
"""

import sqlite3
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import time
import asyncio
import aiohttp
from datetime import datetime
from typing import List, Dict, Any, Tuple

# ------------------------------------------------------------
# 設定
# ------------------------------------------------------------
DB_PATH = "taiwan_stock.db"
START_DATE = "2025-12-01"
END_DATE = "2026-06-22"
SLEEP_BETWEEN_CALLS = 1.0  # 兩次請求間隔 (秒)，搭配 3 組 Token 輪轉

# API token 輪轉 (從 api_sources.md 取得)
API_TOKENS = [
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiYWxiZXJ0MDkxNyIsImVtYWlsIjoiYWxiZXJ0MDkxN0BnbWFpbC5jb20iLCJ0b2tlbl92ZXJzaW9uIjowfQ.NigTcrEmzoH4Ntj3RDzfcRCT2a397hsERMydNZuy05c",
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiY2h1YW5nY2h1YW5nOTE3QGdtYWlsLmNvbSIsImVtYWlsIjoiY2h1YW5nY2h1YW5nOTE3QGdtYWlsLmNvbSIsInRva2VuX3ZlcnNpb24iOjF9.SlWtLQstQJGUCVKl42NxUG8wfqNt6tWD-reyP3xcyBY",
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiY2h1YW5na3VuNjlAZ21haWwuY29tIiwiZW1haWwiOiJjaHVhbmdrdW42OUBnbWFpbC5jb20iLCJ0b2tlbl92ZXJzaW9uIjowfQ.HsULDchhy4vlVfoKipk-JEjDMv34OndMN8M4SVXEp3w"
]
TOKEN_CURSOR = 0
FINMIND_BASE = "https://api.finmindtrade.com/api/v4/data"

def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def get_stocks_to_backfill() -> List[str]:
    """找出所有在 2026-06-19 前完全沒有法人資料的上櫃股"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute(
        """
        SELECT stock_id 
        FROM daily_chips 
        WHERE date < '2026-06-19' 
        GROUP BY stock_id 
        HAVING SUM(ABS(foreign_buy_shares)) = 0.0 AND SUM(ABS(trust_buy_shares)) = 0.0
        ORDER BY stock_id ASC
        """
    )
    stocks = [row[0] for row in cursor.fetchall()]
    conn.close()
    return stocks

async def call_finmind_api(session: aiohttp.ClientSession, dataset: str, stock_id: str) -> Any:
    """呼叫 FinMind，遇 429 會切換 token 並重試"""
    global TOKEN_CURSOR
    for attempt in range(5):
        token = API_TOKENS[TOKEN_CURSOR]
        params = {
            "dataset": dataset,
            "data_id": stock_id,
            "start_date": START_DATE,
            "end_date": END_DATE,
            "token": token,
        }
        try:
            async with session.get(FINMIND_BASE, params=params, timeout=15) as resp:
                if resp.status == 200:
                    json_resp = await resp.json()
                    if json_resp.get("msg") == "success":
                        # 成功調用，輪轉到下一個 token
                        TOKEN_CURSOR = (TOKEN_CURSOR + 1) % len(API_TOKENS)
                        return json_resp.get("data", [])
                elif resp.status == 429:
                    log(f"    ⚠️ Token {TOKEN_CURSOR} 觸發限速 (429)，切換 Token 重試...")
                    TOKEN_CURSOR = (TOKEN_CURSOR + 1) % len(API_TOKENS)
                    await asyncio.sleep(10)
                    continue
                elif resp.status == 402:
                    log(f"    ❌ FinMind API Token 額度已用完 (402)。請等待額度重置。")
                    sys.exit("所有 FinMind Token 的額度已耗盡，終止執行。")
                else:
                    log(f"    ❌ API 錯誤: status={resp.status}")
                await asyncio.sleep(2)
        except Exception as e:
            log(f"    ❌ 網路請求異常: {e}")
            await asyncio.sleep(3)
    return None

def parse_inst_data(data: List[Dict[str, Any]]) -> Dict[str, Tuple[float, float]]:
    """解析法人買賣超資料，回傳 {date: (foreign_shares, trust_shares)}"""
    res = {}
    if not data:
        return res
    for item in data:
        try:
            d = item.get("date", "")
            name = item.get("name", "")
            if not d or name not in ("Foreign_Investor", "Investment_Trust"):
                continue
            buy = float(item.get("buy", 0) or 0)
            sell = float(item.get("sell", 0) or 0)
            net_shares = (buy - sell) / 1000.0  # 轉為張數 (千股)
            
            val = res.setdefault(d, [0.0, 0.0])
            if name == "Foreign_Investor":
                val[0] = net_shares
            elif name == "Investment_Trust":
                val[1] = net_shares
        except Exception:
            pass
    return {k: (v[0], v[1]) for k, v in res.items()}

def parse_margin_data(data: List[Dict[str, Any]]) -> Dict[str, Tuple[float, float]]:
    """解析信用交易資料，回傳 {date: (margin_bal, short_bal)}"""
    res = {}
    if not data:
        return res
    for item in data:
        try:
            d = item.get("date", "")
            if not d:
                continue
            # 使用正確的 FinMind OTC 金鑰名稱
            mp = float(item.get("MarginPurchaseTodayBalance", 0) or 0)
            ss = float(item.get("ShortSaleTodayBalance", 0) or 0)
            res[d] = (mp, ss)
        except Exception:
            pass
    return res

def update_db(stock_id: str, inst_dict: Dict[str, Tuple[float, float]], margin_dict: Dict[str, Tuple[float, float]]) -> int:
    """將抓到的資料更新進 SQLite 資料庫，回傳更新的行數"""
    all_dates = set(inst_dict.keys()) | set(margin_dict.keys())
    if not all_dates:
        return 0
        
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    updated = 0
    try:
        for d in all_dates:
            inst = inst_dict.get(d, (0.0, 0.0))
            margin = margin_dict.get(d, (0.0, 0.0))
            
            cur.execute(
                """
                UPDATE daily_chips 
                SET foreign_buy_shares = ?, trust_buy_shares = ?, margin_purchase_balance = ?, short_sale_balance = ? 
                WHERE date = ? AND stock_id = ?
                """,
                (inst[0], inst[1], margin[0], margin[1], d, stock_id)
            )
            updated += cur.rowcount
        conn.commit()
    except Exception as e:
        conn.rollback()
        log(f"    ❌ 資料庫寫入異常 ({stock_id}): {e}")
    finally:
        conn.close()
    return updated

async def process_single_stock(session: aiohttp.ClientSession, stock_id: str) -> bool:
    """處理單檔股票的下載、解析與更新"""
    # 1. 抓取法人買賣超
    inst_data = await call_finmind_api(session, "TaiwanStockInstitutionalInvestorsBuySell", stock_id)
    await asyncio.sleep(SLEEP_BETWEEN_CALLS)
    
    # 2. 抓取信用交易
    margin_data = await call_finmind_api(session, "TaiwanStockMarginPurchaseShortSale", stock_id)
    await asyncio.sleep(SLEEP_BETWEEN_CALLS)
    
    if inst_data is None or margin_data is None:
        log(f"  ⚠️ {stock_id} 抓取失敗 (FinMind 回傳空值或連線限制)")
        return False
        
    # 3. 解析與儲存
    inst_dict = parse_inst_data(inst_data)
    margin_dict = parse_margin_data(margin_data)
    
    rows = update_db(stock_id, inst_dict, margin_dict)
    log(f"  ✅ {stock_id} 處理完成，成功更新了 {rows} 筆歷史籌碼紀錄")
    return True

async def main():
    stocks = get_stocks_to_backfill()
    if not stocks:
        log("🎉 SQLite 中無缺失歷史資料的上櫃股！")
        return
        
    log(f"🚀 開始回溯！發現共 {len(stocks)} 檔上櫃股缺失歷史資料")
    
    async with aiohttp.ClientSession() as session:
        for idx, stock_id in enumerate(stocks, 1):
            log(f"[{idx}/{len(stocks)}] 正在處理上櫃股: {stock_id} ...")
            success = await process_single_stock(session, stock_id)
            if not success:
                # 遇限速或持續異常，稍微暫停再繼續
                await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
