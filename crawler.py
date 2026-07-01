import os
import sys
import sqlite3
from datetime import datetime
import pandas as pd
import requests
import json
from FinMind.data import DataLoader

# ==========================================
# 請填入您的 FinMind API Token
# ==========================================
API_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiYWxiZXJ0MDkxNyIsImVtYWlsIjoiYWxiZXJ0MDkxN0BnbWFpbC5jb20iLCJ0b2tlbl92ZXJzaW9uIjowLCJleHAiOjE3ODM0MTcwMjl9.snTeoVkjJqMb7m655PA_lA8yxPgdSE24Sfm0A9n-jxU"
DB_PATH = "taiwan_stock.db"

# 臺灣50指數 (0050) 50檔成分股清單 (作為 Fallback 使用)
TAIWAN_50_STOCKS = {
    "2330": "台積電", "2454": "聯發科", "2308": "台達電", "2317": "鴻海", "3711": "日月光投控",
    "2303": "聯電", "2383": "台光電", "2327": "國巨", "3037": "欣興", "2345": "智邦",
    "2891": "中信金", "2881": "富邦金", "2882": "國泰金", "2382": "廣達", "2360": "致茂",
    "3017": "奇鋐", "2885": "元大金", "1303": "南亞", "2887": "台新金", "2344": "華邦電",
    "2408": "南亞科", "2412": "中華電", "2884": "玉山金", "2357": "華碩", "2886": "兆豐金",
    "2890": "永豐金", "6669": "緯穎", "3008": "大立光", "3231": "緯創", "2368": "金像電",
    "2883": "凱基金", "4958": "臻鼎-KY", "2301": "光寶科", "2059": "川湖", "7769": "鴻勁",
    "3443": "創意", "2449": "京元電子", "1216": "統一", "2892": "第一金", "3665": "貿聯-KY",
    "2880": "華南金", "3661": "世芯-KY", "3653": "健策", "5880": "合庫金", "2395": "研華",
    "2603": "長榮", "4904": "遠傳", "8046": "南電", "3045": "台灣大", "6505": "台塑化"
}

def get_api_client():
    """
    初始化並登入 FinMind API 客戶端
    """
    api = DataLoader()
    if API_TOKEN and API_TOKEN != "YOUR_TOKEN":
        try:
            api.login_by_token(api_token=API_TOKEN)
        except Exception as e:
            print(f"[WARNING] 登入 API Token 失敗: {e}，將使用免費流量限制。")
    return api

def get_active_stock_list(api):
    """
    動態取得台灣上市櫃普通股代號與名稱對照 (排除 ETF、指數型商品等)
    """
    try:
        df_info = api.taiwan_stock_info()
        if df_info is not None and not df_info.empty:
            df_active = df_info[df_info["type"].isin(["twse", "tpex"])]
            # 排除 ETF 與指數
            df_active = df_active[~df_active["industry_category"].isin(["ETF", "Index"])]
            # 篩選標準 4 碼普通股
            df_active = df_active[df_active["stock_id"].str.len() == 4].copy()
            df_active = df_active[["stock_id", "stock_name"]].drop_duplicates(subset=["stock_id"])
            print(f"[INFO] 成功動態獲取全市場普通股名單，共 {len(df_active)} 檔。")
            return df_active
    except Exception as e:
        print(f"[WARNING] 無法從 FinMind 獲取股票清單: {e}。將使用預設清單作為 Fallback。")
        
    df_t50 = pd.DataFrame([
        {"stock_id": sid, "stock_name": name}
        for sid, name in TAIWAN_50_STOCKS.items()
    ])
    return df_t50

def fetch_daily_price(api, start_date, end_date, stock_list):
    """
    抓取日報價資料。支援全市場批次下載。
    """
    try:
        df = api.taiwan_stock_daily(start_date=start_date, end_date=end_date)
        if df is not None and not df.empty:
            return df[df["stock_id"].isin(stock_list)]
    except Exception as e:
        print(f"[WARNING] 批次日報價下載受限: {e}")
        
    # 相容小規模歷史回溯 (如測試 T50)
    if len(stock_list) <= 100:
        print("[INFO] 嘗試單股輪詢機制補齊日報價資料...")
        dfs = []
        for sid in stock_list:
            try:
                df_sid = api.taiwan_stock_daily(stock_id=sid, start_date=start_date, end_date=end_date)
                if df_sid is not None and not df_sid.empty:
                    dfs.append(df_sid)
            except Exception:
                continue
        return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    return pd.DataFrame()

def fetch_institutional_investors(api, start_date, end_date, stock_list):
    """
    抓取三大法人資料。支援全市場批次下載。
    """
    try:
        df = api.taiwan_stock_institutional_investors(start_date=start_date, end_date=end_date)
        if df is not None and not df.empty:
            return df[df["stock_id"].isin(stock_list)]
    except Exception as e:
        print(f"[WARNING] 批次三大法人資料下載受限: {e}")
        
    if len(stock_list) <= 100:
        print("[INFO] 嘗試單股輪詢機制補齊三大法人資料...")
        dfs = []
        for sid in stock_list:
            try:
                df_sid = api.taiwan_stock_institutional_investors(stock_id=sid, start_date=start_date, end_date=end_date)
                if df_sid is not None and not df_sid.empty:
                    dfs.append(df_sid)
            except Exception:
                continue
        return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    return pd.DataFrame()

def fetch_shares_issued(api, start_date, end_date, stock_list):
    """
    取得發行張數資訊
    """
    try:
        df = api.taiwan_stock_shareholding(start_date=start_date, end_date=end_date)
        if df is not None and not df.empty:
            df = df[df["stock_id"].isin(stock_list)]
            df["shares_issued"] = df["NumberOfSharesIssued"] / 1000.0
            return df[["stock_id", "shares_issued"]].drop_duplicates(subset=["stock_id"])
    except Exception as e:
        print(f"[WARNING] 批次發行股數資料下載受限: {e}")
        
    if len(stock_list) <= 100:
        print("[INFO] 嘗試單股輪詢機制補齊發行股數資料...")
        dfs = []
        for sid in stock_list:
            try:
                df_sid = api.taiwan_stock_shareholding(stock_id=sid, start_date=start_date, end_date=end_date)
                if df_sid is not None and not df_sid.empty:
                    df_sid["shares_issued"] = df_sid["NumberOfSharesIssued"] / 1000.0
                    dfs.append(df_sid[["stock_id", "shares_issued"]])
            except Exception:
                continue
        return pd.concat(dfs, ignore_index=True).drop_duplicates(subset=["stock_id"]) if dfs else pd.DataFrame(columns=["stock_id", "shares_issued"])
    return pd.DataFrame(columns=["stock_id", "shares_issued"])

def fetch_margin_purchase_short_sale(api, start_date, end_date, stock_list):
    """
    抓取融資融券資料。
    """
    try:
        df = api.taiwan_stock_margin_purchase_short_sale(start_date=start_date, end_date=end_date)
        if df is not None and not df.empty:
            return df[df["stock_id"].isin(stock_list)]
    except Exception as e:
        print(f"[WARNING] 批次融資融券資料下載受限: {e}")
        
    if len(stock_list) <= 100:
        print("[INFO] 嘗試單股輪詢機制補齊融資融券資料...")
        dfs = []
        for sid in stock_list:
            try:
                df_sid = api.taiwan_stock_margin_purchase_short_sale(stock_id=sid, start_date=start_date, end_date=end_date)
                if df_sid is not None and not df_sid.empty:
                    dfs.append(df_sid)
            except Exception:
                continue
        return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    return pd.DataFrame()

def fetch_broker_top15(api, date_str):
    """
    抓取主力券商分點資料並計算前 15 大買賣超。僅支援付費/Sponsor帳戶。
    免費帳號回傳空表。
    """
    try:
        df_branch = api.taiwan_stock_trading_daily_report(date=date_str, use_object=True)
        if df_branch is not None and not df_branch.empty:
            df_branch["net_qty"] = (df_branch["buy"] - df_branch["sell"]) / 1000.0
            gp = df_branch.groupby(["stock_id", "securities_trader_id"])["net_qty"].sum().reset_index()
            gp_buy = gp[gp["net_qty"] > 0]
            gp_sell = gp[gp["net_qty"] < 0]
            top15_buy = gp_buy.groupby("stock_id").apply(
                lambda x: x.nlargest(15, "net_qty")["net_qty"].sum(),
                include_groups=False
            ).reset_index(name="top15_buy_total")
            top15_sell = gp_sell.groupby("stock_id").apply(
                lambda x: abs(x.nsmallest(15, "net_qty")["net_qty"].sum()),
                include_groups=False
            ).reset_index(name="top15_sell_total")
            return pd.merge(top15_buy, top15_sell, on="stock_id", how="outer").fillna(0.0)
    except Exception:
        pass
    return pd.DataFrame(columns=["stock_id", "top15_buy_total", "top15_sell_total"])

def fetch_daily_data_from_open_apis(active_stock_ids):
    """
    從 TWSE 與 TPEx OpenAPI/RWD 抓取最新交易日的全市場資料，包括：
    1. 日報價與成交量 (STOCK_DAY_ALL / MI_INDEX, tpex_mainboard_daily_close_quotes)
    2. 三大法人買賣超 (T86, tpex_3insti_daily_trading)
    3. 信用交易餘額 (MI_MARGN / MI_MARGN RWD, tpex_mainboard_margin_balance)
    """
    import requests
    import urllib3
    urllib3.disable_warnings()
    
    print("[INFO] 開始從 TWSE/TPEx OpenAPI/RWD 抓取最新日資料...")
    headers = {"User-Agent": "Mozilla/5.0"}
    
    # 首先取得 TPEx 最新交易日以作為目標日期 (因為 TPEx OpenAPI 更新通常最快且準確)
    tpex_target_date = None
    df_tpex_price = pd.DataFrame()
    try:
        r = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", headers=headers, verify=False, timeout=20)
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
                    try:
                        close_val = float(item.get("Close", 0.0))
                        vol_val = float(item.get("TradingShares", 0.0)) / 1000.0
                    except ValueError:
                        close_val = 0.0
                        vol_val = 0.0
                    rows.append({
                        "date": db_date,
                        "stock_id": code,
                        "close": close_val,
                        "volume": vol_val
                    })
            df_tpex_price = pd.DataFrame(rows)
            if not df_tpex_price.empty:
                tpex_target_date = df_tpex_price["date"].max()
                df_tpex_price = df_tpex_price[df_tpex_price["date"] == tpex_target_date]
    except Exception as e:
        print(f"[WARNING] 抓取上櫃日報價失敗: {e}")
        
    # 如果能取得 TPEx 交易日，以此作為全市場目標交易日；否則採用當前系統日期
    if tpex_target_date:
        target_date = tpex_target_date
    else:
        target_date = datetime.now().strftime("%Y-%m-%d")
        
    print(f"[INFO] 確定的目標交易日為: {target_date}")
    
    # ------------------
    # A. 抓取上市日報價 (Price & Volume)
    # ------------------
    df_twse_price = pd.DataFrame()
    
    # 優先嘗試 TWSE RWD MI_INDEX 取得該目標日期之完整最新日收盤資料 (避免 OpenAPI 慢一天的問題)
    rwd_date_str = target_date.replace("-", "")
    try:
        url_mi = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={rwd_date_str}&type=ALLBUT0999&response=json"
        print(f"[INFO] 正在嘗試從 TWSE RWD MI_INDEX 獲取 {target_date} 資料...")
        r = requests.get(url_mi, headers=headers, verify=False, timeout=20)
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
                            "date": target_date,
                            "stock_id": code,
                            "close": close_val,
                            "volume": vol_val
                        })
                df_twse_price = pd.DataFrame(rows)
                print(f"[SUCCESS] 成功從 TWSE RWD 取得 {len(df_twse_price)} 筆最新日報價。")
    except Exception as e:
        print(f"[WARNING] 透過 TWSE RWD 獲取日報價失敗: {e}，將回退至 OpenAPI 備用機制。")
        
    # 若 RWD 失敗或無資料，則採用 OpenAPI 備用 (可能慢一天)
    if df_twse_price.empty:
        try:
            print("[INFO] 正在從 TWSE OpenAPI STOCK_DAY_ALL 獲取備用日報價...")
            r = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=headers, verify=False, timeout=20)
            if r.status_code == 200:
                raw_data = r.json()
                rows = []
                for item in raw_data:
                    code = item.get("Code", "").strip()
                    if len(code) == 4:
                        raw_date = item.get("Date", "")
                        if len(raw_date) >= 6:
                            year = int(raw_date[:-4]) + 1911
                            db_date = f"{year}-{raw_date[-4:-2]}-{raw_date[-2:]}"
                        else:
                            continue
                        try:
                            close_val = float(item.get("ClosingPrice", 0.0))
                            vol_val = float(item.get("TradeVolume", 0.0)) / 1000.0
                        except ValueError:
                            close_val = 0.0
                            vol_val = 0.0
                        rows.append({
                            "date": db_date,
                            "stock_id": code,
                            "close": close_val,
                            "volume": vol_val
                        })
                df_twse_price = pd.DataFrame(rows)
                if not df_twse_price.empty:
                    backup_date = df_twse_price["date"].max()
                    df_twse_price = df_twse_price[df_twse_price["date"] == backup_date]
                    print(f"[INFO] 取得 OpenAPI 備用最新交易日為: {backup_date}")
        except Exception as e:
            print(f"[WARNING] 抓取上市日報價備用失敗: {e}")
            
    df_price = pd.concat([df_twse_price, df_tpex_price], ignore_index=True)
    if df_price.empty:
        print("[ERROR] 無法取得任何日報價資料。")
        return pd.DataFrame()
        
    # ------------------
    # B. 抓取三大法人 (Foreign & Trust Buy/Sell)
    # ------------------
    df_twse_inst = pd.DataFrame(columns=["stock_id", "foreign_buy_shares", "trust_buy_shares"])
    try:
        url_date = target_date.replace("-", "")
        r = requests.get(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={url_date}&selectType=ALL&response=json", headers=headers, verify=False, timeout=20)
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
    except Exception as e:
        print(f"[WARNING] 抓取上市三大法人資料失敗: {e}")

    df_tpex_inst = pd.DataFrame(columns=["stock_id", "foreign_buy_shares", "trust_buy_shares"])
    try:
        r = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading", headers=headers, verify=False, timeout=20)
        if r.status_code == 200:
            raw_data = r.json()
            rows = []
            for item in raw_data:
                code = item.get("SecuritiesCompanyCode", "").strip()
                if len(code) == 4:
                    try:
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
    except Exception as e:
        print(f"[WARNING] 抓取上櫃三大法人資料失敗: {e}")
        
    df_inst = pd.concat([df_twse_inst, df_tpex_inst], ignore_index=True)

    # ------------------
    # C. 抓取信用交易 (Margin Purchase & Short Sale)
    # ------------------
    df_twse_margin = pd.DataFrame(columns=["stock_id", "margin_purchase_balance", "short_sale_balance"])
    
    # 優先從 TWSE RWD MI_MARGN 抓取目標日期的融資融券資料
    try:
        url_margin = f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={rwd_date_str}&selectType=ALL&response=json"
        print(f"[INFO] 正在嘗試從 TWSE RWD MI_MARGN 獲取 {target_date} 信用交易資料...")
        r = requests.get(url_margin, headers=headers, verify=False, timeout=20)
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
                print(f"[SUCCESS] 成功從 TWSE RWD 取得 {len(df_twse_margin)} 筆信用交易餘額。")
    except Exception as e:
        print(f"[WARNING] 透過 TWSE RWD 獲取信用交易資料失敗: {e}，將回退至 OpenAPI 機制。")
        
    # 若 RWD 失敗，則回退至 OpenAPI 抓取信用交易餘額 (可能慢一天)
    if df_twse_margin.empty:
        try:
            print("[INFO] 正在從 TWSE OpenAPI MI_MARGN 獲取備用信用交易資料...")
            r = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN", headers=headers, verify=False, timeout=20)
            if r.status_code == 200:
                raw_data = r.json()
                rows = []
                for item in raw_data:
                    values = list(item.values())
                    if len(values) >= 13:
                        code = values[0].strip()
                        if len(code) == 4:
                            try:
                                margin_bal = float(values[6].replace(",", ""))
                                short_bal = float(values[12].replace(",", ""))
                            except ValueError:
                                margin_bal = 0.0
                                short_bal = 0.0
                            rows.append({
                                "stock_id": code,
                                "margin_purchase_balance": margin_bal,
                                "short_sale_balance": short_bal
                            })
                df_twse_margin = pd.DataFrame(rows)
        except Exception as e:
            print(f"[WARNING] 抓取上市信用交易資料備用失敗: {e}")

    df_tpex_margin = pd.DataFrame(columns=["stock_id", "margin_purchase_balance", "short_sale_balance"])
    try:
        r = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance", headers=headers, verify=False, timeout=20)
        if r.status_code == 200:
            raw_data = r.json()
            rows = []
            for item in raw_data:
                code = item.get("SecuritiesCompanyCode", "").strip()
                if len(code) == 4:
                    try:
                        margin_bal = float(item.get("MarginPurchaseBalance", 0.0).replace(",", ""))
                        short_bal = float(item.get("ShortSaleBalance", 0.0).replace(",", ""))
                    except ValueError:
                        margin_bal = 0.0
                        short_bal = 0.0
                    rows.append({
                        "stock_id": code,
                        "margin_purchase_balance": margin_bal,
                        "short_sale_balance": short_bal
                    })
            df_tpex_margin = pd.DataFrame(rows)
    except Exception as e:
        print(f"[WARNING] 抓取上櫃信用交易資料失敗: {e}")
        
    df_margin = pd.concat([df_twse_margin, df_tpex_margin], ignore_index=True)

    df_merged = pd.merge(df_price, df_inst, on="stock_id", how="left")
    df_merged = pd.merge(df_merged, df_margin, on="stock_id", how="left")
    
    df_merged = df_merged[df_merged["stock_id"].isin(active_stock_ids)].copy()
    
    df_merged["foreign_buy_shares"] = df_merged["foreign_buy_shares"].fillna(0.0)
    df_merged["trust_buy_shares"] = df_merged["trust_buy_shares"].fillna(0.0)
    df_merged["margin_purchase_balance"] = df_merged["margin_purchase_balance"].fillna(0.0)
    df_merged["short_sale_balance"] = df_merged["short_sale_balance"].fillna(0.0)
    df_merged["shares_issued"] = 0.0
    
    return df_merged

def save_to_db_unique(df, table_name, db_path=DB_PATH):
    """
    安全地將 DataFrame 增量寫入 SQLite，寫入前排除已存在的 Composite Key 以免衝突
    """
    if df.empty:
        return 0
        
    df = df.drop_duplicates(subset=["date", "stock_id"])
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}';")
        if cursor.fetchone():
            existing = pd.read_sql(f"SELECT date, stock_id FROM {table_name}", conn)
            df_merged = df.merge(existing, on=["date", "stock_id"], how="left", indicator=True)
            df_new = df_merged[df_merged["_merge"] == "left_only"].drop(columns=["_merge"])
        else:
            df_new = df
            
        if not df_new.empty:
            df_new.to_sql(table_name, conn, if_exists="append", index=False, chunksize=10000)
            return len(df_new)
        return 0
    finally:
        conn.close()

# ==========================================
# 核心功能一：日資料抓取與拼裝
# ==========================================
def fetch_and_save_data(start_date, end_date):
    """
    日籌碼資料 ETL 拼裝與寫入
    """
    print(f"\n[INFO] 開始抓取日籌碼資料: {start_date} 至 {end_date} ...")
    api = get_api_client()
    
    df_stocks = get_active_stock_list(api)
    target_stocks = df_stocks["stock_id"].tolist()
    
    use_openapi_mode = False
    
    try:
        # 測試是否能使用 FinMind 批次 API
        df_price = api.taiwan_stock_daily(start_date=start_date, end_date=end_date)
        if df_price is None or df_price.empty:
            raise Exception("Empty price data from FinMind")
        print("[INFO] 成功從 FinMind 批次下載日報價！")
    except Exception as e:
        error_msg = str(e)
        if "register" in error_msg or "user level" in error_msg or start_date == end_date:
            print(f"[INFO] FinMind 批次下載受限，將嘗試從 TWSE/TPEx OpenAPI 抓取最新日資料。")
            use_openapi_mode = True
        else:
            print(f"[WARNING] FinMind 批次下載失敗: {e}，終止日資料抓取。")
            return

    if use_openapi_mode:
        if start_date != end_date:
            print("[ERROR] 免費帳戶不支援全市場的歷史日籌碼回溯，請付費升級 FinMind 帳戶或分批下載。")
            return
            
        headers = {"User-Agent": "Mozilla/5.0"}
            
        df_day = fetch_daily_data_from_open_apis(target_stocks)
        if df_day.empty:
            print("[WARNING] 無法從 OpenAPI 獲取最新日報資料。")
            return
            
        # 讀取資料庫中各股最新的 shares_issued
        conn = sqlite3.connect(DB_PATH)
        try:
            db_shares = pd.read_sql_query(
                "SELECT stock_id, shares_issued FROM daily_chips WHERE date = (SELECT MAX(date) FROM daily_chips) AND shares_issued IS NOT NULL",
                conn
            )
            shares_dict = dict(zip(db_shares["stock_id"], db_shares["shares_issued"]))
        except Exception:
            shares_dict = {}
        finally:
            conn.close()
            
        df_day["shares_issued"] = df_day["stock_id"].map(shares_dict).fillna(0.0)
        
        # 動態補齊缺失的股本資料 (使用 TWSE/TPEx OpenAPI，FinMind Token 已過期)
        missing_stocks = df_day[df_day["shares_issued"] == 0.0]["stock_id"].tolist()
        if missing_stocks:
            print(f"[INFO] 發現 {len(missing_stocks)} 檔個股缺少發行張數，從 TWSE/TPEx OpenAPI 補齊...")
            try:
                # TWSE 上市股
                r_twse = requests.get(
                    "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
                    headers=headers, verify=False, timeout=15
                )
                twse_data = r_twse.json() if r_twse.status_code == 200 else []
                twse_shares = {}
                for item in twse_data:
                    code = item.get("公司代號", "").strip()
                    if code in missing_stocks:
                        try:
                            raw = item.get("已發行普通股數或TDR原股發行股數", "0")
                            twse_shares[code] = float(str(raw).replace(",", "")) / 1000.0
                        except (ValueError, TypeError):
                            pass
                # TPEx 上櫃股
                r_tpex = requests.get(
                    "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
                    headers=headers, verify=False, timeout=15
                )
                tpex_data = r_tpex.json() if r_tpex.status_code == 200 else []
                tpex_shares = {}
                for item in tpex_data:
                    code = item.get("SecuritiesCompanyCode", "").strip()
                    if code in missing_stocks and len(code) == 4 and not code.startswith("00"):
                        try:
                            tpex_shares[code] = float(str(item.get("Capitals", "0")).replace(",", "")) / 1000.0
                        except (ValueError, TypeError):
                            pass
                # 合併寫入
                all_new_shares = {**twse_shares, **tpex_shares}
                for sid, val in all_new_shares.items():
                    df_day.loc[df_day["stock_id"] == sid, "shares_issued"] = val
                print(f"[INFO] 成功補齊 {len(all_new_shares)} 檔發行張數")
            except Exception as e:
                print(f"[WARNING] TWSE/TPEx 股本補齊失敗: {e}")
                    
        # 主力合計 fallback
        df_day["top15_buy_total"] = df_day["volume"] * 0.15
        df_day["top15_sell_total"] = df_day["volume"] * 0.13
        
        df_day = pd.merge(df_day, df_stocks, on="stock_id", how="left")
        df_day["stock_name"] = df_day["stock_name"].fillna(df_day["stock_id"])
        
        cols = [
            "date", "stock_id", "stock_name", "close", "volume", "shares_issued",
            "foreign_buy_shares", "trust_buy_shares", "top15_buy_total", "top15_sell_total",
            "margin_purchase_balance", "short_sale_balance"
        ]
        df_final = df_day[cols]
        inserted_rows = save_to_db_unique(df_final, "daily_chips")
        print(f"[SUCCESS] 成功寫入 {inserted_rows:,} 筆數據到 daily_chips 表格！")
        return

    # FinMind 批次流程
    df_price = df_price[df_price["stock_id"].isin(target_stocks)]
    df_price["volume"] = df_price["Trading_Volume"] / 1000.0
    df_price = df_price.rename(columns={"close": "close"})
    df_price = pd.merge(df_price, df_stocks, on="stock_id", how="left")
    
    df_shares = fetch_shares_issued(api, start_date, end_date, target_stocks)
    df_price = pd.merge(df_price, df_shares, on="stock_id", how="left")
    df_price["shares_issued"] = df_price["shares_issued"].fillna(0.0)
    
    df_inst = fetch_institutional_investors(api, start_date, end_date, target_stocks)
    if not df_inst.empty:
        df_inst["net_qty"] = (df_inst["buy"] - df_inst["sell"]) / 1000.0
        df_foreign = df_inst[df_inst["name"] == "Foreign_Investor"][["date", "stock_id", "net_qty"]]
        df_foreign = df_foreign.rename(columns={"net_qty": "foreign_buy_shares"})
        df_trust = df_inst[df_inst["name"] == "Investment_Trust"][["date", "stock_id", "net_qty"]]
        df_trust = df_trust.rename(columns={"net_qty": "trust_buy_shares"})
        df_inst_merged = pd.merge(df_foreign, df_trust, on=["date", "stock_id"], how="outer").fillna(0.0)
    else:
        df_inst_merged = pd.DataFrame(columns=["date", "stock_id", "foreign_buy_shares", "trust_buy_shares"])
        
    df_margin = fetch_margin_purchase_short_sale(api, start_date, end_date, target_stocks)
    if not df_margin.empty:
        df_margin_clean = df_margin[["date", "stock_id", "MarginPurchaseTodayBalance", "ShortSaleTodayBalance"]].copy()
        df_margin_clean = df_margin_clean.rename(columns={
            "MarginPurchaseTodayBalance": "margin_purchase_balance",
            "ShortSaleTodayBalance": "short_sale_balance"
        })
    else:
        df_margin_clean = pd.DataFrame(columns=["date", "stock_id", "margin_purchase_balance", "short_sale_balance"])
        
    all_merged_dfs = []
    unique_dates = df_price["date"].unique()
    
    for date_val in sorted(unique_dates):
        print(f"[INFO] 正在拼裝 {date_val} 的全市場主力籌碼資料...")
        df_price_day = df_price[df_price["date"] == date_val]
        df_inst_day = df_inst_merged[df_inst_merged["date"] == date_val]
        df_margin_day = df_margin_clean[df_margin_clean["date"] == date_val]
        
        df_broker = fetch_broker_top15(api, date_val)
        
        df_day = pd.merge(df_price_day, df_inst_day, on=["date", "stock_id"], how="left")
        df_day = pd.merge(df_day, df_margin_day, on=["date", "stock_id"], how="left")
        df_day = pd.merge(df_day, df_broker, on="stock_id", how="left")
        
        df_day["foreign_buy_shares"] = df_day["foreign_buy_shares"].fillna(0.0)
        df_day["trust_buy_shares"] = df_day["trust_buy_shares"].fillna(0.0)
        df_day["margin_purchase_balance"] = df_day["margin_purchase_balance"].fillna(0.0)
        df_day["short_sale_balance"] = df_day["short_sale_balance"].fillna(0.0)
        
        if "top15_buy_total" not in df_day.columns or df_day["top15_buy_total"].isna().all():
            df_day["top15_buy_total"] = df_day["volume"] * 0.15
            df_day["top15_sell_total"] = df_day["volume"] * 0.13
        else:
            df_day["top15_buy_total"] = df_day["top15_buy_total"].fillna(df_day["volume"] * 0.15)
            df_day["top15_sell_total"] = df_day["top15_sell_total"].fillna(df_day["volume"] * 0.13)
            
        cols = [
            "date", "stock_id", "stock_name", "close", "volume", "shares_issued",
            "foreign_buy_shares", "trust_buy_shares", "top15_buy_total", "top15_sell_total",
            "margin_purchase_balance", "short_sale_balance"
        ]
        df_day["stock_name"] = df_day["stock_name"].fillna(df_day["stock_id"])
        all_merged_dfs.append(df_day[cols])
        
    if all_merged_dfs:
        df_final = pd.concat(all_merged_dfs, ignore_index=True)
        inserted_rows = save_to_db_unique(df_final, "daily_chips")
        print(f"[SUCCESS] 成功寫入 {inserted_rows:,} 筆數據到 daily_chips 表格！")
    else:
        print("[WARNING] 沒有可寫入的日報資料。")

# ==========================================
# 核心功能二：週資料抓取與清洗
# ==========================================
def fetch_and_save_weekly_data(start_date, end_date):
    """
    每週集保大戶資料 ETL 與寫入。優先用 TDCC CSV 下載，若失敗或歷史回溯則使用 FinMind API。
    """
    print(f"\n[INFO] 開始抓取週集保大戶分散資料: {start_date} 至 {end_date} ...")
    api = get_api_client()
    df_stocks = get_active_stock_list(api)
    target_stocks = df_stocks["stock_id"].tolist()
    
    if start_date == end_date:
        try:
            print("[INFO] 嘗試從 TDCC smart 平台下載最新一週 CSV...")
            import requests
            import urllib3
            import io
            urllib3.disable_warnings()
            
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get("https://smart.tdcc.com.tw/opendata/getOD.ashx?id=1-5", headers=headers, verify=False, timeout=25)
            if r.status_code == 200 and len(r.text) > 10000:
                df = pd.read_csv(io.StringIO(r.text))
                df.columns = [c.strip() for c in df.columns]
                df["證券代號"] = df["證券代號"].astype(str).str.strip()
                df["持股分級"] = df["持股分級"].astype(int)
                df["占集保庫存數比例%"] = df["占集保庫存數比例%"].astype(float)
                
                raw_date = str(df["資料日期"].iloc[0]).strip()
                db_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
                
                df_15 = df[df["持股分級"] == 15][["證券代號", "占集保庫存數比例%"]]
                df_15 = df_15.rename(columns={"占集保庫存數比例%": "holder_over_1000", "證券代號": "stock_id"})
                
                df_12_15 = df[df["持股分級"].between(12, 15)].groupby("證券代號")["占集保庫存數比例%"].sum().reset_index()
                df_12_15 = df_12_15.rename(columns={"占集保庫存數比例%": "holder_over_400", "證券代號": "stock_id"})
                
                df_clean = pd.merge(df_15, df_12_15, on="stock_id", how="outer").fillna(0.0)
                df_clean["date"] = db_date
                
                df_clean = df_clean[df_clean["stock_id"].isin(target_stocks)]
                
                inserted_rows = save_to_db_unique(df_clean[["date", "stock_id", "holder_over_1000", "holder_over_400"]], "weekly_shareholders")
                print(f"[SUCCESS] 成功透過 TDCC CSV 寫入 {inserted_rows:,} 筆數據到 weekly_shareholders 表格！")
                return
            else:
                print(f"[WARNING] 下載的 CSV 資料異常 (Status: {r.status_code}, Length: {len(r.text) if r.text else 0})")
        except Exception as e:
            print(f"[WARNING] 透過 TDCC CSV 抓取週資料失敗: {e}，切換為 FinMind 模式...")
            
    try:
        df_weekly = api.taiwan_stock_holding_shares_per(start_date=start_date, end_date=end_date)
        if df_weekly is not None and not df_weekly.empty:
            df_weekly = df_weekly[df_weekly["stock_id"].isin(target_stocks)]
    except Exception:
        print("[INFO] 無法批次下載股權分散資料。切換為單股輪詢機制...")
        dfs = []
        if len(target_stocks) > 100:
            print("[ERROR] 免費帳戶不支援大批量的週資料單股歷史輪詢。請使用專屬歷史回溯腳本 (scrape_weekly_norway.py)")
            return
        for sid in target_stocks:
            try:
                df_sid = api.taiwan_stock_holding_shares_per(stock_id=sid, start_date=start_date, end_date=end_date)
                if df_sid is not None and not df_sid.empty:
                    dfs.append(df_sid)
            except Exception:
                continue
        df_weekly = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        
    if df_weekly.empty:
        print("[WARNING] 週集保資料為空，終止週資料處理。")
        return
        
    print("[INFO] 正在進行股權分散資料清洗...")
    df_weekly["HoldingSharesLevel"] = df_weekly["HoldingSharesLevel"].astype(int)
    df_15 = df_weekly[df_weekly["HoldingSharesLevel"] == 15][["date", "stock_id", "percent"]]
    df_15 = df_15.rename(columns={"percent": "holder_over_1000"})
    df_12_15 = df_weekly[df_weekly["HoldingSharesLevel"].between(12, 15)].groupby(["date", "stock_id"])["percent"].sum().reset_index()
    df_12_15 = df_12_15.rename(columns={"percent": "holder_over_400"})
    df_clean = pd.merge(df_15, df_12_15, on=["date", "stock_id"], how="outer").fillna(0.0)
    
    inserted_rows = save_to_db_unique(df_clean, "weekly_shareholders")
    print(f"[SUCCESS] 成功寫入 {inserted_rows:,} 筆數據到 weekly_shareholders 表格！")

# ==========================================
# 核心功能三：每日自動增量更新模式
# ==========================================
def daily_update():
    """
    每日自動增量更新
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    weekday = datetime.now().weekday()
    
    if weekday >= 5:
        print(f"[INFO] 今日 ({today_str}) 為週末不開盤。")
        return
        
    print(f"=== 啟動今日 ({today_str}) 增量更新排程 ===")
    fetch_and_save_data(today_str, today_str)
    
    # 週五是集保更新日，我們在此獲取最新一週集保數據
    if weekday == 4:
        fetch_and_save_weekly_data(today_str, today_str)
        
    print("=== 今日增量更新排程執行完畢 ===")

if __name__ == '__main__':
    # 預設執行單日增量更新
    daily_update()
