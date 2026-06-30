"""
sync_single_date.py
針對指定日期計算籌碼指標並 Upsert 至 Supabase。
用法：python sync_single_date.py 2026-06-24
"""

import sys
import os
import sqlite3
import time

import pandas as pd
import numpy as np
import requests

# ─── 設定 ────────────────────────────────────────────────
TARGET_DATE  = sys.argv[1] if len(sys.argv) > 1 else "2026-06-24"
SUPABASE_URL = "https://xjalllcvwbgnxwcruhzz.supabase.co"
SUPABASE_KEY = "sb_publishable_4jXrUcO-DXpwGu4QklflXg_v7w4IYNt"
DB_PATH      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "taiwan_stock.db")
TABLE        = "chase_strategy_results"
BATCH_SIZE   = 500

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "resolution=merge-duplicates",   # Upsert
}

# ─── 工具函式 ─────────────────────────────────────────────
def calculate_consecutive_growth(series):
    counts, cur = [], 0
    for val in series:
        cur = (cur + 1) if val else 0
        counts.append(cur)
    return counts

def get_consec_days(series):
    consec = []
    current_count = 0
    current_sign = 0
    for val in series:
        if val > 0.00001:
            sign = 1
        elif val < -0.00001:
            sign = -1
        else:
            sign = 0
            
        if sign == 0:
            current_count = 0
            current_sign = 0
        elif sign == current_sign:
            current_count += sign
        else:
            current_sign = sign
            current_count = sign
        consec.append(current_count)
    return consec

# ─── 主流程 ───────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"[START] 單日籌碼同步 → Supabase  |  日期：{TARGET_DATE}")
    print("=" * 60)
    t0 = time.time()

    # 1. 載入 SQLite 全量資料（滾動計算需要歷史）
    print("[STEP 1] 載入 SQLite 資料...")
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            "SELECT * FROM daily_chips ORDER BY stock_id ASC, date ASC", conn
        )
        if df.empty:
            print("[ERROR] daily_chips 資料表為空！")
            return
        print(f"         載入 {len(df):,} 筆日資料")

        # 2. 計算滾動指標
        print("[STEP 2] 計算滾動策略指標...")
        g = df.groupby("stock_id")

        def roll(col, w): return g[col].rolling(w, min_periods=1).sum().reset_index(level=0, drop=True)
        def safe(x): return x.fillna(0.0).replace([np.inf, -np.inf], 0.0)

        # 法人比
        f20 = roll("foreign_buy_shares", 20); t20 = roll("trust_buy_shares", 20)
        f60 = roll("foreign_buy_shares", 60); t60 = roll("trust_buy_shares", 60)
        v20 = roll("volume", 20);             v60 = roll("volume", 60)
        si  = df["shares_issued"]

        df["ratio_foreign_trust_20d"]         = safe((f20+t20) / v20) * 100
        df["ratio_foreign_trust_20d_capital"] = safe((f20+t20) / si)  * 100
        df["ratio_foreign_trust_60d"]         = safe((f60+t60) / v60) * 100
        df["ratio_foreign_trust_60d_capital"] = safe((f60+t60) / si)  * 100
        df["vol_20d"]                         = safe(v20)

        # 集中度
        for w in [1, 5, 10, 20, 60, 120]:
            b = roll("top15_buy_total",  w)
            s = roll("top15_sell_total", w)
            v = roll("volume", w)
            df[f"concentration_{w}d"] = safe((b - s) / v) * 100

        df["is_long_lock"]      = (df["concentration_60d"] > 5.0) & (df["concentration_120d"] > 3.0)
        df["is_buy_accelerate"] = (df["concentration_5d"] > df["concentration_20d"]) & \
                                   (df["concentration_20d"] > df["concentration_60d"])

        # 60日漲跌
        df["close_60d_ago"]  = g["close"].shift(60)
        df["price_change_60d"] = safe((df["close"] - df["close_60d_ago"]) / df["close_60d_ago"]) * 100

        # 融資券變化
        df["margin_purchase_change_20d"] = (
            df["margin_purchase_balance"] -
            g["margin_purchase_balance"].shift(20).reset_index(level=0, drop=True)
        ).fillna(0.0)
        df["short_sale_change_20d"] = (
            df["short_sale_balance"] -
            g["short_sale_balance"].shift(20).reset_index(level=0, drop=True)
        ).fillna(0.0)

        df["inst_daily"] = df["foreign_buy_shares"] + df["trust_buy_shares"]
        df["inst_consec_days"] = g["inst_daily"].transform(get_consec_days).astype(int)

        # 3. 合併週資料
        print("[STEP 3] 合併集保大戶週資料...")
        df_w = pd.read_sql_query(
            "SELECT date, stock_id, holder_over_1000, holder_over_400 FROM weekly_shareholders", conn
        )
        if df_w.empty:
            df["holder_over_1000"]   = 0.0
            df["holder_over_400"]    = 0.0
            df["holder_growth_weeks"] = 0
        else:
            df_w = df_w.sort_values(["stock_id", "date"])
            df_w["increased"]    = df_w.groupby("stock_id")["holder_over_1000"].diff() > 0
            df_w["growth_weeks"] = df_w.groupby("stock_id")["increased"].transform(calculate_consecutive_growth)

            df["dt"]   = pd.to_datetime(df["date"])
            df_w["dt"] = pd.to_datetime(df_w["date"])
            df  = df.sort_values("dt")
            df_w = df_w.sort_values("dt")

            df = pd.merge_asof(
                df,
                df_w[["dt","stock_id","holder_over_1000","holder_over_400","growth_weeks"]],
                on="dt", by="stock_id", direction="backward"
            )
            df["holder_over_1000"]    = df["holder_over_1000"].fillna(0.0)
            df["holder_over_400"]     = df["holder_over_400"].fillna(0.0)
            df["holder_growth_weeks"] = df["growth_weeks"].fillna(0).astype(int)

    finally:
        conn.close()

    # 4. 篩選目標日期
    print(f"[STEP 4] 篩選 {TARGET_DATE} 的資料...")
    out_cols = [
        "date", "stock_id", "stock_name", "close", "volume", "shares_issued",
        "ratio_foreign_trust_20d", "ratio_foreign_trust_20d_capital",
        "ratio_foreign_trust_60d", "ratio_foreign_trust_60d_capital",
        "concentration_1d", "concentration_5d", "concentration_10d",
        "concentration_20d", "concentration_60d", "concentration_120d",
        "is_long_lock", "is_buy_accelerate", "price_change_60d",
        "holder_over_1000", "holder_over_400",
        "margin_purchase_balance", "short_sale_balance",
        "margin_purchase_change_20d", "short_sale_change_20d",
        "vol_20d",          # 先保留以防欄位存在
        "holder_growth_weeks",
        "inst_consec_days",
    ]
    # 補足缺失欄位
    for col in out_cols:
        if col not in df.columns:
            df[col] = False if col in ("is_long_lock","is_buy_accelerate") else 0.0

    df_day = df[df["date"] == TARGET_DATE][out_cols].copy()
    total  = len(df_day)
    print(f"         共 {total} 檔股票需要寫入")

    if total == 0:
        print(f"[WARN]  {TARGET_DATE} 在 SQLite 中沒有資料，請先執行每日爬蟲。")
        return

    # 5. 批次 Upsert → Supabase
    print(f"[STEP 5] 批次 Upsert → Supabase（{BATCH_SIZE} 筆/次）...")
    records    = df_day.to_dict(orient="records")
    url        = f"{SUPABASE_URL}/rest/v1/{TABLE}"
    ok_count   = 0
    err_count  = 0
    t_up       = time.time()

    for i in range(0, total, BATCH_SIZE):
        batch = records[i : i + BATCH_SIZE]
        # 清理 NaN / inf
        for r in batch:
            for k, v in list(r.items()):
                if isinstance(v, float) and (pd.isna(v) or v in (float("inf"), float("-inf"))):
                    r[k] = None
                elif hasattr(v, "item"):       # numpy bool/int/float → Python native
                    r[k] = v.item()

        resp = requests.post(url, headers=HEADERS, json=batch)
        if resp.status_code in (200, 201):
            ok_count += len(batch)
        else:
            # 如果因為 inst_consec_days 欄位不存在而失敗，嘗試排除該欄位後重試
            if "inst_consec_days" in resp.text:
                print("  [WARN] Supabase 資料表缺少 'inst_consec_days' 欄位，排除該欄位並重試...")
                for r in batch:
                    r.pop("inst_consec_days", None)
                resp = requests.post(url, headers=HEADERS, json=batch)
                
            if resp.status_code in (200, 201):
                ok_count += len(batch)
            else:
                err_count += len(batch)
                print(f"  [ERROR] 批次 {i}~{i+len(batch)} 失敗：{resp.status_code} {resp.text[:120]}")

        if (i // BATCH_SIZE) % 5 == 0 or i + BATCH_SIZE >= total:
            pct     = (i + len(batch)) / total * 100
            elapsed = time.time() - t_up
            spd     = (i + len(batch)) / elapsed if elapsed > 0 else 0
            print(f"  [進度] {i+len(batch):>4}/{total}  ({pct:5.1f}%)  速度 {spd:.0f} 筆/秒")

    # 6. 結果報告
    elapsed_total = time.time() - t0
    print("=" * 60)
    print(f"[DONE]  日期：{TARGET_DATE}")
    print(f"        成功寫入：{ok_count} 檔")
    print(f"        失敗筆數：{err_count} 檔")
    print(f"        總耗時：{elapsed_total:.1f} 秒")
    print("=" * 60)


if __name__ == "__main__":
    main()
