import sqlite3
import pandas as pd
import numpy as np

DB_NAME = "taiwan_stock.db"

def run_chip_strategy(target_date, weekly_trend_weeks=0, min_trade_value=50000000, db_path=DB_NAME):
    """
    台股高階籌碼選股策略篩選引擎
    
    參數:
      - target_date: 目標選股日期 (YYYY-MM-DD)
      - weekly_trend_weeks: 集保大戶趨勢連續上升/散戶下降週數 (0 代表不啟用週趨勢過濾)
      - min_trade_value: 最低成交金額 (NTD)
      - db_path: 資料庫路徑
      
    回傳:
      - 符合條件的 Pandas DataFrame
    """
    conn = sqlite3.connect(db_path)
    try:
        # ==========================================
        # 1. 撈取截止至 target_date 的前 120 天歷史日資料
        # ==========================================
        # 取得排序後的交易日清單
        dates_df = pd.read_sql_query(
            "SELECT DISTINCT date FROM daily_chips WHERE date <= ? ORDER BY date DESC LIMIT 120",
            conn, params=(target_date,)
        )
        if dates_df.empty:
            print(f"[WARNING] 日期 {target_date} 前無歷史籌碼資料。")
            return pd.DataFrame()
            
        target_dates = sorted(dates_df["date"].tolist())
        
        # 撈取對應交易日的全股票資料
        placeholders = ",".join(["?"] * len(target_dates))
        query = f"""
            SELECT date, stock_id, stock_name, close, volume, shares_issued,
                   foreign_buy_shares, trust_buy_shares, top15_buy_total, top15_sell_total,
                   margin_purchase_balance, short_sale_balance
            FROM daily_chips 
            WHERE date IN ({placeholders}) 
            ORDER BY stock_id ASC, date ASC
        """
        df = pd.read_sql_query(query, conn, params=target_dates)
        if df.empty:
            return pd.DataFrame()

        # 確保信用交易欄位存在且填補空值
        if "margin_purchase_balance" not in df.columns:
            df["margin_purchase_balance"] = 0.0
        else:
            df["margin_purchase_balance"] = df["margin_purchase_balance"].fillna(0.0)
        if "short_sale_balance" not in df.columns:
            df["short_sale_balance"] = 0.0
        else:
            df["short_sale_balance"] = df["short_sale_balance"].fillna(0.0)

        # ==========================================
        # 2. 核心籌碼指標計算 (df.groupby('stock_id') 進行獨立個股滾動計算)
        # ==========================================
        grouped = df.groupby("stock_id")
        
        # 20日與60日法人累計買超
        df["foreign_20d"] = grouped["foreign_buy_shares"].rolling(window=20, min_periods=1).sum().reset_index(level=0, drop=True)
        df["trust_20d"] = grouped["trust_buy_shares"].rolling(window=20, min_periods=1).sum().reset_index(level=0, drop=True)
        df["foreign_60d"] = grouped["foreign_buy_shares"].rolling(window=60, min_periods=1).sum().reset_index(level=0, drop=True)
        df["trust_60d"] = grouped["trust_buy_shares"].rolling(window=60, min_periods=1).sum().reset_index(level=0, drop=True)
        
        # 各天期總成交量滾動加總
        df["vol_20d"] = grouped["volume"].rolling(window=20, min_periods=1).sum().reset_index(level=0, drop=True)
        df["vol_60d"] = grouped["volume"].rolling(window=60, min_periods=1).sum().reset_index(level=0, drop=True)
        
        # 法人佔量比與買超股本比 (安全除法，防除以 0)
        df["ratio_foreign_trust_20d"] = ((df["foreign_20d"] + df["trust_20d"]) / df["vol_20d"]).fillna(0.0).replace([np.inf, -np.inf], 0.0) * 100
        df["ratio_foreign_trust_20d_capital"] = ((df["foreign_20d"] + df["trust_20d"]) / df["shares_issued"]).fillna(0.0).replace([np.inf, -np.inf], 0.0) * 100
        df["ratio_foreign_trust_60d"] = ((df["foreign_60d"] + df["trust_60d"]) / df["vol_60d"]).fillna(0.0).replace([np.inf, -np.inf], 0.0) * 100
        df["ratio_foreign_trust_60d_capital"] = ((df["foreign_60d"] + df["trust_60d"]) / df["shares_issued"]).fillna(0.0).replace([np.inf, -np.inf], 0.0) * 100
        
        # 跨天期主力籌碼集中度矩陣 (1, 5, 10, 20, 60, 120 日)
        for w in [1, 5, 10, 20, 60, 120]:
            df[f"buy_{w}d"] = grouped["top15_buy_total"].rolling(window=w, min_periods=1).sum().reset_index(level=0, drop=True)
            df[f"sell_{w}d"] = grouped["top15_sell_total"].rolling(window=w, min_periods=1).sum().reset_index(level=0, drop=True)
            df[f"vol_{w}d"] = grouped["volume"].rolling(window=w, min_periods=1).sum().reset_index(level=0, drop=True)
            
            # 集中度 = (買超前15大加總 - 賣超前15大加總) / 總量 * 100
            df[f"concentration_{w}d"] = ((df[f"buy_{w}d"] - df[f"sell_{w}d"]) / df[f"vol_{w}d"]).fillna(0.0).replace([np.inf, -np.inf], 0.0) * 100

        # ==========================================
        # 3. 標籤與狀態判定 (💎 長線鎖碼, 🔥 買盤加速, 60日股價位階)
        # ==========================================
        # 💎 長線鎖碼：60日集中度 > 5% 且 120日集中度 > 3%
        df["is_long_lock"] = (df["concentration_60d"] > 5.0) & (df["concentration_120d"] > 3.0)
        
        # 🔥 買盤加速：5日集中度 > 20日集中度 > 60日集中度
        df["is_buy_accelerate"] = (df["concentration_5d"] > df["concentration_20d"]) & (df["concentration_20d"] > df["concentration_60d"])
        
        # 60日股價位階判斷 (利用 shift(60) 取得60天前價格)
        df["close_60d_ago"] = grouped["close"].shift(60)
        df["price_change_60d"] = ((df["close"] - df["close_60d_ago"]) / df["close_60d_ago"]).fillna(0.0).replace([np.inf, -np.inf], 0.0) * 100

        # 20日融資與融券餘額變化 (當前餘額 - 20天前餘額)
        df["margin_purchase_change_20d"] = df["margin_purchase_balance"] - grouped["margin_purchase_balance"].shift(20).reset_index(level=0, drop=True)
        df["short_sale_change_20d"] = df["short_sale_balance"] - grouped["short_sale_balance"].shift(20).reset_index(level=0, drop=True)
        df["margin_purchase_change_20d"] = df["margin_purchase_change_20d"].fillna(0.0)
        df["short_sale_change_20d"] = df["short_sale_change_20d"].fillna(0.0)

        # 篩選出 target_date 的當天資料
        df_target = df[df["date"] == target_date].copy()
        
        # ==========================================
        # 4. 整合每週大戶資料與吸貨週數過濾
        # ==========================================
        # 取得最新的一筆集保日期
        latest_w_date_df = pd.read_sql_query(
            "SELECT MAX(date) FROM weekly_shareholders WHERE date <= ?",
            conn, params=(target_date,)
        )
        latest_w_date = latest_w_date_df.iloc[0, 0]
        
        if latest_w_date:
            # 讀取該最新週資料
            df_latest_weekly = pd.read_sql_query(
                "SELECT stock_id, holder_over_1000, holder_over_400 FROM weekly_shareholders WHERE date = ?",
                conn, params=(latest_w_date,)
            )
            df_target = pd.merge(df_target, df_latest_weekly, on="stock_id", how="left")
        else:
            # 建立空欄位備用
            df_target["holder_over_1000"] = 0.0
            df_target["holder_over_400"] = 0.0
            
        # 週趨勢連續吸貨過濾
        if weekly_trend_weeks > 0:
            # 取得連續 N + 1 個週日期
            w_dates_df = pd.read_sql_query(
                "SELECT DISTINCT date FROM weekly_shareholders WHERE date <= ? ORDER BY date DESC LIMIT ?",
                conn, params=(target_date, weekly_trend_weeks + 1)
            )
            w_dates = sorted(w_dates_df["date"].tolist())
            
            if len(w_dates) == weekly_trend_weeks + 1:
                placeholders_w = ",".join(["?"] * len(w_dates))
                df_weekly_hist = pd.read_sql_query(
                    f"SELECT date, stock_id, holder_over_1000, holder_over_400 FROM weekly_shareholders WHERE date IN ({placeholders_w})",
                    conn, params=w_dates
                )
                
                # Pivot 展開成寬表格以方便進行列向趨勢比較
                df_wp = df_weekly_hist.pivot(index="stock_id", columns="date", values=["holder_over_1000", "holder_over_400"])
                df_wp.columns = [f"{var}_{d}" for var, d in df_wp.columns]
                df_wp = df_wp.reset_index()
                
                cond_large = pd.Series(True, index=df_wp.index)
                
                # 連續 N 週上升比較 (僅過濾千張大戶持股比)
                for i in range(len(w_dates) - 1):
                    d_prev = w_dates[i]
                    d_next = w_dates[i+1]
                    cond_large &= (df_wp[f"holder_over_1000_{d_next}"] > df_wp[f"holder_over_1000_{d_prev}"])
                    
                df_wp["passed"] = cond_large
                passed_stocks = set(df_wp[df_wp["passed"]]["stock_id"])
                
                # 套用吸貨週數過濾
                df_target = df_target[df_target["stock_id"].isin(passed_stocks)]
                print(f"[INFO] 已啟用 {weekly_trend_weeks} 週吸貨過濾，篩選後剩餘 {len(df_target)} 檔股票。")
            else:
                print(f"[WARNING] 週歷史資料筆數不足 ({len(w_dates)}/{weekly_trend_weeks + 1})，跳過週趨勢過濾。")

        # ==========================================
        # 5. 冷門股過濾與輸出
        # ==========================================
        # 當日總成交金額 = 收盤價 * 當日成交張數 * 1000 (1 張 = 1000 股)
        # 篩選掉小於指定門檻的股票
        df_target["trade_value"] = df_target["close"] * df_target["volume"] * 1000
        df_target = df_target[df_target["trade_value"] >= min_trade_value]
        
        # 整理最終輸出的 DataFrame 結構
        output_cols = [
            "date", "stock_id", "stock_name", "close", "volume", "shares_issued",
            "ratio_foreign_trust_20d", "ratio_foreign_trust_20d_capital",
            "ratio_foreign_trust_60d", "ratio_foreign_trust_60d_capital",
            "concentration_1d", "concentration_5d", "concentration_10d",
            "concentration_20d", "concentration_60d", "concentration_120d",
            "is_long_lock", "is_buy_accelerate", "price_change_60d",
            "holder_over_1000", "holder_over_400",
            "margin_purchase_balance", "short_sale_balance",
            "margin_purchase_change_20d", "short_sale_change_20d",
            "vol_20d"
        ]
        
        # 確保所有需要的欄位都存在，缺失則補 0
        for col in output_cols:
            if col not in df_target.columns:
                df_target[col] = 0.0
                
        df_result = df_target[output_cols].copy()
        
        # 依照成交量降序排列
        df_result = df_result.sort_values(by="volume", ascending=False).reset_index(drop=True)
        return df_result
        
    finally:
        conn.close()

if __name__ == '__main__':
    # 測試執行區塊
    test_date = "2026-06-23"
    print(f"=== 開始執行籌碼選股策略測試 (目標日期: {test_date}) ===")
    
    # 執行策略
    result_df = run_chip_strategy(test_date, weekly_trend_weeks=0)
    
    if not result_df.empty:
        print(f"\n[SUCCESS] 策略執行成功！共選出 {len(result_df)} 檔符合成交量門檻的股票。")
        print("\n前 5 筆篩選結果：")
        print(result_df[["stock_id", "stock_name", "close", "volume", "is_long_lock", "is_buy_accelerate"]].head(5))
    else:
        print("\n[INFO] 執行完成。目前資料庫無對應日期或符合成交金額大於 5000 萬的個股數據。")
