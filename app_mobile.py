import streamlit as st
import sqlite3
import pandas as pd
import numpy as np
import requests
import textwrap
import os
from datetime import datetime, date

# ==========================================
# 0. 頁面資訊與基本配置 (手機版強制為窄版)
# ==========================================
try:
    st.set_page_config(
        page_title="🔥 高階籌碼雷達 (手機老齡版)",
        page_icon="🔥",
        layout="centered",
        initial_sidebar_state="collapsed"
    )
except Exception:
    pass

DB_PATH = "taiwan_stock.db"

# Supabase 配置與雙軌切換
_DEFAULT_SUPABASE_URL = "https://xjalllcvwbgnxwcruhzz.supabase.co"
_DEFAULT_SUPABASE_KEY = "sb_publishable_4jXrUcO-DXpwGu4QklflXg_v7w4IYNt"

SUPABASE_URL = os.environ.get("SUPABASE_URL") if "os" in globals() else None
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") if "os" in globals() else None

if not SUPABASE_URL or not SUPABASE_KEY:
    try:
        if hasattr(st, "secrets"):
            SUPABASE_URL = st.secrets.get("SUPABASE_URL", SUPABASE_URL)
            SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", SUPABASE_KEY)
    except Exception:
        pass

if not SUPABASE_URL or not SUPABASE_KEY:
    SUPABASE_URL = _DEFAULT_SUPABASE_URL
    SUPABASE_KEY = _DEFAULT_SUPABASE_KEY

USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_KEY)

if USE_SUPABASE:
    SUPABASE_HEADERS = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }

def _supabase_fetch_all(url, headers, timeout=15):
    records = []
    limit = 1000
    offset = 0
    while True:
        req_headers = headers.copy()
        req_headers["Range"] = f"{offset}-{offset + limit - 1}"
        r = requests.get(url, headers=req_headers, timeout=timeout)
        if r.status_code not in (200, 206):
            break
        data = r.json()
        if not data:
            break
        records.extend(data)
        if len(data) < limit:
            break
        offset += limit
    return records

def clean_html(html_str):
    """
    清除 HTML 字串中每一行的前導空格與縮排，防止 Markdown 誤將其解析為程式碼區塊。
    """
    return "\n".join([line.strip() for line in html_str.split("\n")])

# ==========================================
# 1. 取得所有開盤交易日清單 (沒有交易的日期，不要顯示)
# ==========================================
@st.cache_data(ttl=3600)
def get_available_trading_dates():
    # 優先使用本地 SQLite，速度極快 (0.02秒) 且省流量
    try:
        if os.path.exists(DB_PATH):
            conn = sqlite3.connect(DB_PATH)
            res = conn.execute("SELECT DISTINCT date FROM daily_chips ORDER BY date DESC").fetchall()
            conn.close()
            if res:
                return [r[0] for r in res]
    except Exception:
        pass

    # 若無本地資料庫，才從 Supabase 讀取 (限制筆數避免全表掃描當機)
    if USE_SUPABASE:
        try:
            # 限制讀取最新 10000 筆紀錄，約包含最新的 5-10 個交易日，這對行動版已足夠
            url = f"{SUPABASE_URL}/rest/v1/chase_strategy_results?select=date&order=date.desc&limit=10000"
            r = requests.get(url, headers=SUPABASE_HEADERS, timeout=10)
            if r.status_code == 200:
                records = r.json()
                if records:
                    dates = sorted(list(set([r["date"] for r in records])), reverse=True)
                    return dates
        except Exception as e:
            st.error(f"從 Supabase 讀取交易日失敗: {e}")
    
    return [str(date.today())]

# ==========================================
# 2. 策略篩選核心引擎
# ==========================================
def cached_run_chip_strategy(target_date, weekly_trend_weeks=0, min_trade_value=0):
    if USE_SUPABASE:
        try:
            records = []
            limit = 1000
            offset = 0
            while True:
                req_headers = SUPABASE_HEADERS.copy()
                req_headers["Range"] = f"{offset}-{offset + limit - 1}"
                url = f"{SUPABASE_URL}/rest/v1/chase_strategy_results?date=eq.{target_date}"
                r = requests.get(url, headers=req_headers, timeout=15)
                if r.status_code not in (200, 206):
                    break
                data = r.json()
                if not data:
                    break
                records.extend(data)
                if len(data) < limit:
                    break
                offset += limit
                
            df = pd.DataFrame(records)
            if df.empty:
                return df
                
            numeric_cols = [
                "close", "volume", "shares_issued", 
                "ratio_foreign_trust_20d", "ratio_foreign_trust_20d_capital",
                "ratio_foreign_trust_60d", "ratio_foreign_trust_60d_capital",
                "price_change_60d", "holder_over_1000", "holder_over_400",
                "margin_purchase_balance", "short_sale_balance",
                "margin_purchase_change_20d", "short_sale_change_20d", "vol_20d",
                "holder_growth_weeks"
            ]
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
                    
            df = df[df["close"] * df["volume"] * 1000 >= min_trade_value]
            if weekly_trend_weeks > 0:
                df = df[df["holder_growth_weeks"] >= weekly_trend_weeks]
                
            return df
        except Exception as e:
            st.error(f"從 Supabase 取得選股結果失敗: {e}")
            return pd.DataFrame()
    else:
        # SQLite Local Strategy
        from strategy import run_chip_strategy
        return run_chip_strategy(target_date, weekly_trend_weeks, min_trade_value, DB_PATH)

# ==========================================
# 3. 取得所有篩選股票之千張/400張大戶歷史連續週數
# ==========================================
def _calc_consec(values):
    """給定一組由新到舊的週持股比列表，回傳連續趨勢文字"""
    if len(values) < 2:
        return "無連續趨勢"
    diff = values[0] - values[1]
    if diff > 0.00001:
        consec = 0
        for i in range(len(values) - 1):
            if values[i] > values[i+1]:
                consec += 1
            else:
                break
        pct = values[0] - values[consec]
        return f"買進 {consec} 週 (+{pct:.2f}%)"
    elif diff < -0.00001:
        consec = 0
        for i in range(len(values) - 1):
            if values[i] < values[i+1]:
                consec += 1
            else:
                break
        pct = values[0] - values[consec]
        return f"減持 {consec} 週 ({pct:.2f}%)"
    else:
        return "持平"

def calculate_consecutive_weeks(stock_ids, target_date):
    """回傳 (dict_1000, dict_400)，每個 dict 的 key 為 stock_id"""
    if not stock_ids:
        return {}, {}
        
    dict_1000 = {}
    dict_400 = {}
    
    if USE_SUPABASE:
        try:
            stock_in_query = ",".join([f'"{sid}"' for sid in stock_ids])
            url = f"{SUPABASE_URL}/rest/v1/chase_strategy_results?select=date,stock_id,holder_over_1000,holder_over_400&stock_id=in.({stock_in_query})&date=lte.{target_date}&order=date.desc"
            records = _supabase_fetch_all(url, SUPABASE_HEADERS)
            if records:
                df_hist = pd.DataFrame(records)
                df_hist["holder_over_1000"] = pd.to_numeric(df_hist["holder_over_1000"], errors='coerce').fillna(0.0)
                df_hist["holder_over_400"] = pd.to_numeric(df_hist["holder_over_400"], errors='coerce').fillna(0.0)
                df_hist["date_dt"] = pd.to_datetime(df_hist["date"])
                df_hist["iso_year"] = df_hist["date_dt"].dt.isocalendar().year
                df_hist["iso_week"] = df_hist["date_dt"].dt.isocalendar().week
                
                for sid in stock_ids:
                    df_sid = df_hist[df_hist["stock_id"] == sid].groupby(["iso_year", "iso_week"]).first().reset_index()
                    df_sid = df_sid.sort_values(by="date", ascending=False)
                    
                    val_1000 = df_sid["holder_over_1000"].tolist()
                    val_400 = df_sid["holder_over_400"].tolist()
                    
                    # 處理當前交易日為週一至週四且本週尚未發布新集保資料時的重複填補問題
                    if len(df_sid) >= 2:
                        latest_date = pd.to_datetime(df_sid.iloc[0]["date"])
                        if latest_date.weekday() < 4 and val_1000[0] == val_1000[1] and val_400[0] == val_400[1]:
                            val_1000 = val_1000[1:]
                            val_400 = val_400[1:]
                            
                    dict_1000[sid] = _calc_consec(val_1000)
                    dict_400[sid] = _calc_consec(val_400)
        except Exception:
            pass
    else:
        conn = sqlite3.connect(DB_PATH)
        try:
            for sid in stock_ids:
                df_weekly_hist = pd.read_sql_query(
                    "SELECT date, holder_over_1000, holder_over_400 FROM weekly_shareholders WHERE stock_id = ? AND date <= ? ORDER BY date DESC",
                    conn, params=(sid, target_date)
                )
                dict_1000[sid] = _calc_consec(df_weekly_hist["holder_over_1000"].tolist())
                dict_400[sid] = _calc_consec(df_weekly_hist["holder_over_400"].tolist())
        except Exception:
            pass
        finally:
            conn.close()
            
    return dict_1000, dict_400


# ==========================================
# 4. 銀髮族專用高強度高對比 CSS
# ==========================================
elder_css = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;700;900&display=swap');

/* 全域字體與行高巨大化，確保老眼花長輩極易閱讀，排除內建 Icon 與側邊欄收折按鈕 */
html, body, [class*="css"], .stApp, p, 
span:not([data-testid="stIconMaterial"]):not([class*="Icon"]), 
label, input, select, 
button:not([data-testid="collapsedControl"]) {
    font-family: 'Outfit', 'Segoe UI', 'Microsoft JhengHei', sans-serif !important;
    font-size: 20px !important;
    line-height: 1.7 !important;
}

/* 還原 Streamlit 內建圖標字型，避免圖標顯示為 ligature 英文 (如 keyboard_double_arrow) */
[data-testid="stIconMaterial"],
[data-testid="stIcon"],
[data-testid="collapsedControl"] *,
[class*="Icon"] {
    font-family: "Material Symbols Rounded", "Material Symbols Outlined", "Material Icons", "Segoe UI Symbol", sans-serif !important;
}

.stApp {
    background-color: #0d1117 !important;
    color: #f0f6fc !important;
}

/* 下拉式選單 Label 樣式 */
label[data-testid="stWidgetLabel"] p {
    font-size: 1.25rem !important;
    font-weight: 800 !important;
    color: #ff8533 !important;
    margin-bottom: 8px !important;
}

/* 下拉式選單本體高度與字體加大 */
div[data-baseweb="select"] {
    font-size: 1.2rem !important;
    border-radius: 12px !important;
}

/* 巨大按鈕樣式 */
div.stButton button {
    font-size: 1.4rem !important;
    font-weight: bold !important;
    height: 60px !important;
    border-radius: 16px !important;
    background: linear-gradient(90deg, #2ea44f 0%, #34d058 100%) !important;
    color: #ffffff !important;
    border: none !important;
    box-shadow: 0 4px 15px rgba(46, 164, 79, 0.4) !important;
}

/* 卡片主體樣式：大型圓角、深色漸層高對比、雙色亮眼外框 */
.elder-card {
    background: linear-gradient(135deg, #161b22 0%, #0d1117 100%);
    border: 3px solid #30363d;
    border-radius: 20px;
    padding: 24px;
    margin-bottom: 25px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
}

.elder-card:active {
    border-color: #ff8533;
}

.elder-card-title {
    font-size: 1.85rem !important;
    font-weight: 900 !important;
    color: #ffffff;
    border-bottom: 2px solid #30363d;
    padding-bottom: 12px;
    margin-bottom: 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.elder-card-badge-lock {
    background-color: rgba(26, 115, 232, 0.25);
    color: #8ab4f8;
    padding: 4px 12px;
    border-radius: 30px;
    border: 2px solid rgba(26, 115, 232, 0.5);
    font-size: 0.9rem !important;
    font-weight: bold;
}

.elder-card-badge-acc {
    background-color: rgba(219, 68, 85, 0.25);
    color: #f28b82;
    padding: 4px 12px;
    border-radius: 30px;
    border: 2px solid rgba(219, 68, 85, 0.5);
    font-size: 0.9rem !important;
    font-weight: bold;
}

.elder-card-section {
    margin-bottom: 16px;
    padding-bottom: 12px;
    border-bottom: 1px dashed #21262d;
}

.elder-card-section:last-child {
    border-bottom: none;
    margin-bottom: 0;
    padding-bottom: 0;
}

.section-label {
    font-size: 1.05rem !important;
    font-weight: bold;
    color: #8b949e;
    margin-bottom: 6px;
}

.section-values {
    display: flex;
    justify-content: space-between;
    font-size: 1.3rem !important;
    font-weight: bold;
}

.value-highlight-up {
    color: #ff6b6b !important; /* 紅色偏多 */
}
.value-highlight-down {
    color: #81c784 !important; /* 綠色偏空 */
}
.value-normal {
    color: #c9d1d9 !important;
}
.value-key {
    color: #ff9800 !important;
}

.gradient-bar {
    height: 8px;
    background: linear-gradient(90deg, #ff4b4b 0%, #ff8533 50%, #ffc04d 100%);
    border-radius: 4px;
    margin-bottom: 25px;
}
</style>
<div class="gradient-bar"></div>
"""

st.markdown(elder_css, unsafe_allow_html=True)

# ==========================================
# 5. 標題與核心
# ==========================================
st.markdown("<h1 style='font-size:3.2rem; font-weight:900; color:#ffffff; margin-top:10px; margin-bottom:20px; text-align:center;'>🔥 高階籌碼雷達</h1>", unsafe_allow_html=True)

# 讓使用者可以點擊手動切換至電腦版
if st.button("💻 切換至電腦版", key="switch_to_desktop", use_container_width=True):
    st.query_params["layout"] = "desktop"
    st.components.v1.html("""
    <script>
        try {
            // 如果是在 8502 獨立埠運行，手動切換時幫忙跳轉回 8501 埠的電腦版
            if (window.location.port === "8502") {
                window.location.href = "http://localhost:8501/?layout=desktop";
            }
        } catch(e){}
    </script>
    """, height=0)
    st.rerun()

# 載入所有有交易的日期清單
trading_dates = get_available_trading_dates()

# ==========================================
# 6. 頂部篩選控制區 (全下拉選單設計)
# ==========================================
with st.container():
    # 1. 策略分析日期
    selected_date_str = st.selectbox(
        "📅 選擇分析日期",
        options=trading_dates,
        index=0
    )
    
    # 2. 股票成交熱度門檻
    trade_val_label = st.selectbox(
        "💰 股票成交熱度門檻",
        options=["不限制", "1,000萬 以上", "2,000萬 以上", "5,000萬 以上"],
        index=0
    )
    trade_val_mapping = {
        "不限制": 0,
        "1,000萬 以上": 1000 * 10000,
        "2,000萬 以上": 2000 * 10000,
        "5,000萬 以上": 5000 * 10000
    }
    min_trade_val = trade_val_mapping[trade_val_label]
    
    # 3. 千張大戶買進週數
    weeks_label = st.selectbox(
        "👥 千張大戶連續買進週數",
        options=["不限制", "連續 2 週", "連續 3 週", "連續 4 週", "連續 8 週"],
        index=0
    )
    weeks_mapping = {"不限制": 0, "連續 2 週": 2, "連續 3 週": 3, "連續 4 週": 4, "連續 8 週": 8}
    weekly_trend_weeks = weeks_mapping[weeks_label]
    
    # 4. 法人機構買超比例最低門檻
    col_inst1, col_inst2 = st.columns(2)
    with col_inst1:
        inst_ratio_20d_label = st.selectbox(
            "▶️ 20日法人買超比",
            options=["不限制", "高於 5%", "高於 10%", "高於 15%", "高於 20%"],
            index=0
        )
    with col_inst2:
        inst_ratio_60d_label = st.selectbox(
            "▶️ 60日法人買超比",
            options=["不限制", "高於 5%", "高於 10%", "高於 15%", "高於 20%"],
            index=0
        )
        
    inst_ratio_mapping = {"不限制": 0.0, "高於 5%": 5.0, "高於 10%": 10.0, "高於 15%": 15.0, "高於 20%": 20.0}
    min_inst_ratio_20d = inst_ratio_mapping[inst_ratio_20d_label]
    min_inst_ratio_60d = inst_ratio_mapping[inst_ratio_60d_label]

    # 🔍 搜尋特定個股
    st.markdown("<div style='margin-top:15px;'></div>", unsafe_allow_html=True)
    search_stock_q = st.text_input(
        "🔍 搜尋特定個股 (輸入代號或名稱)",
        value="",
        placeholder="搜尋個股診斷，例如 2330 或 台積電 (留空則顯示策略結果)"
    )

    # 執行搜尋按鈕
    st.markdown("<div style='margin-top:15px;'></div>", unsafe_allow_html=True)
    search_clicked = st.button("🎯 執行籌碼雷達選股", use_container_width=True)

st.markdown("---")

# ==========================================
# 7. 策略計算與卡片渲染區
# ==========================================
# 快取處理
date_changed = st.session_state.get("_last_mobile_date", "") != selected_date_str
force_recalc = search_clicked or date_changed or "_df_mobile_cache" not in st.session_state

if force_recalc:
    with st.spinner("🔍 正在為您篩選優質股票..."):
        df_strategy = cached_run_chip_strategy(
            target_date=selected_date_str,
            weekly_trend_weeks=weekly_trend_weeks,
            min_trade_value=min_trade_val
        )
        st.session_state["_df_mobile_cache"] = df_strategy
        st.session_state["_last_mobile_date"] = selected_date_str
else:
    df_strategy = st.session_state["_df_mobile_cache"]

if not df_strategy.empty:
    # 套用法人買超最低比例過濾
    if min_inst_ratio_20d > 0:
        df_strategy = df_strategy[df_strategy["ratio_foreign_trust_20d"] >= min_inst_ratio_20d]
    if min_inst_ratio_60d > 0:
        df_strategy = df_strategy[df_strategy["ratio_foreign_trust_60d"] >= min_inst_ratio_60d]
        
    # 搜尋與個股診斷過濾
    if search_stock_q.strip():
        q = search_stock_q.strip()
        df_filtered = df_strategy[
            (df_strategy["stock_id"] == q) | 
            (df_strategy["stock_name"].str.contains(q, case=False, na=False))
        ]
        if not df_filtered.empty:
            df_strategy = df_filtered
        else:
            # 如果在目前篩選結果中找不到，則單獨向資料庫查詢該個股的資料（個股診斷）
            with st.spinner(f"🔍 正在單獨查詢 {q} 的籌碼診斷資料..."):
                df_all = cached_run_chip_strategy(
                    target_date=selected_date_str,
                    weekly_trend_weeks=0,
                    min_trade_value=0
                )
                df_strategy = df_all[
                    (df_all["stock_id"] == q) | 
                    (df_all["stock_name"].str.contains(q, case=False, na=False))
                ]

    if not df_strategy.empty:
        stock_ids = df_strategy["stock_id"].tolist()
        
        # 批量計算大戶連續買超週數（千張 + 400張）
        consec_dict_1000, consec_dict_400 = calculate_consecutive_weeks(stock_ids, selected_date_str)
        
        if search_stock_q.strip():
            st.success(f"🔍 搜尋完成！共找到 {len(df_strategy)} 檔相關個股")
        else:
            st.success(f"🎉 篩選完成！共找到 {len(df_strategy)} 檔主力鎖碼股票")
        
        # 開始渲染卡片
        for idx, row in df_strategy.iterrows():
            stock_id = row["stock_id"]
            stock_name = row["stock_name"]
            close_val = row["close"]
            price_change = row["price_change_60d"]
            ratio_20d = row["ratio_foreign_trust_20d"]
            ratio_60d = row["ratio_foreign_trust_60d"]
            holder_1000 = row["holder_over_1000"]
            holder_400 = row["holder_over_400"]
            margin_diff = row["margin_purchase_change_20d"]
            short_diff = row["short_sale_change_20d"]
            vol_20d = row.get("vol_20d", 0.0)
            volume_val = row.get("volume", 0.0)
            
            # 計算融資券比率
            margin_ratio = (margin_diff / vol_20d * 100) if vol_20d > 0 else 0.0
            short_ratio = (short_diff / vol_20d * 100) if vol_20d > 0 else 0.0
            
            # 大戶連續買超週數
            consec_text_1000 = consec_dict_1000.get(stock_id, "持平")
            consec_text_400 = consec_dict_400.get(stock_id, "持平")
            
            # 狀態徽章
            badge_html = ""
            if row["is_long_lock"]:
                badge_html += '<span class="elder-card-badge-lock">💎長線鎖碼</span> '
            if row["is_buy_accelerate"]:
                badge_html += '<span class="elder-card-badge-acc">🔥買盤加速</span>'
                
            # 漲跌色調與正負號
            p_change_color = "value-highlight-up" if price_change > 0 else ("value-highlight-down" if price_change < 0 else "value-normal")
            p_change_symbol = "▲" if price_change > 0 else ("▼" if price_change < 0 else "")

            # 計算信用交易 class 名稱，避免在 f-string 中評估 set 語法
            margin_class = "value-highlight-up" if margin_ratio > 0 else "value-normal"
            short_class = "value-highlight-down" if short_ratio > 0 else "value-normal"

            
            # 渲染卡片 HTML
            st.html(clean_html(f"""
            <div class="elder-card">
                <!-- 1. 卡片頭：代號名稱與收盤價、漲跌幅 -->
                <div class="elder-card-title">
                    <div>
                        <span>{stock_id} {stock_name}</span>
                        <div style="margin-top: 6px; font-weight: normal; font-size: 1.15rem; color: #8b949e;">
                            成交張數：<span style="color: #ffffff; font-weight: bold;">{volume_val:,.0f}</span> 張
                        </div>
                        {f'<div style="margin-top: 6px;">{badge_html}</div>' if badge_html else ''}
                    </div>
                    <div style="text-align: right;">
                        <div style="font-size: 1.9rem; color: #ffeb3b;">{close_val:.2f} 元</div>
                        <div class="{p_change_color}" style="font-size: 1.15rem;">60日：{p_change_symbol} {abs(price_change):.2f}%</div>
                    </div>
                </div>
                
                <!-- 2. 法人佈局結構 -->
                <div class="elder-card-section">
                    <div class="section-label">🚀 法人佈局結構 (佔交易量比)</div>
                    <div class="section-values">
                        <span>20日法人比：<span class="value-normal">{ratio_20d:.2f}%</span></span>
                        <span>60日法人比：<span class="value-normal">{ratio_60d:.2f}%</span></span>
                    </div>
                </div>
                
                <!-- 3. 集保大戶結構 -->
                <div class="elder-card-section">
                    <div class="section-label">👥 集保大戶結構 (股權分散)</div>
                    <div class="section-values" style="flex-direction: column; gap: 4px;">
                        <div style="display:flex; justify-content:space-between;">
                            <span>千張大戶持股：<span class="value-normal">{holder_1000:.2f}%</span></span>
                            <span>400張大戶：<span class="value-normal">{holder_400:.2f}%</span></span>
                        </div>
                        <div style="font-size: 1.15rem; color: #ff9800; margin-top: 4px;">
                            👉 千張大戶週變動：<span class="value-key">{consec_text_1000}</span>
                        </div>
                        <div style="font-size: 1.15rem; color: #ff9800; margin-top: 4px;">
                            👉 400張大戶週變動：<span class="value-key">{consec_text_400}</span>
                        </div>
                    </div>
                </div>
                
                <!-- 4. 信用交易 (融資融券) -->
                <div class="elder-card-section">
                    <div class="section-label">📉 信用交易 (20日變化比率與張數)</div>
                    <div class="section-values" style="flex-direction: column; gap: 4px;">
                        <div style="display:flex; justify-content:space-between;">
                            <span>20日融資比：<span class="{margin_class}">{margin_ratio:+.2f}%</span></span>
                            <span>變動張數：<span class="value-normal">{margin_diff:+,.0f} 張</span></span>
                        </div>
                        <div style="display:flex; justify-content:space-between; margin-top: 4px;">
                            <span>20日融券比：<span class="{short_class}">{short_ratio:+.2f}%</span></span>
                            <span>變動張數：<span class="value-normal">{short_diff:+,.0f} 張</span></span>
                        </div>
                    </div>
                </div>
            </div>
            """))
            
    else:
        st.info("ℹ️ 在目前過濾條件下無符合股票，請嘗試放寬篩選設定。")
else:
    st.info("ℹ️ 當前日期無籌碼選股資料。請先手動執行 crawler.py 匯入歷史數據，或選擇其它已有數據的交易日期。")
