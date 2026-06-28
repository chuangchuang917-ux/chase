import streamlit as np_st  # 避免 st 縮寫衝突
import streamlit as st
import sqlite3
import pandas as pd
import textwrap
import os
import json
from datetime import datetime, date
from strategy import run_chip_strategy

DEFAULT_DISPLAY_ORDER = [
    "stock_id", "stock_name", "close", "volume", "price_change_60d",
    "ratio_foreign_trust_20d", "ratio_foreign_trust_20d_capital",
    "ratio_foreign_trust_60d", "ratio_foreign_trust_60d_capital",
    "holder_over_1000", "holder_over_400",
    "margin_purchase_change_20d", "short_sale_change_20d"
]

CHINESE_COLUMNS = {
    "stock_id": "股票代號",
    "stock_name": "股票名稱",
    "close": "收盤價",
    "volume": "成交張數",
    "price_change_60d": "60日漲跌幅",
    "ratio_foreign_trust_20d": "20日法人佔量比",
    "ratio_foreign_trust_20d_capital": "20日買超股本比",
    "ratio_foreign_trust_60d": "60日法人佔量比",
    "ratio_foreign_trust_60d_capital": "60日買超股本比",
    "holder_over_1000": "千張大戶持股比",
    "holder_over_400": "400張以上大戶比",
    "margin_purchase_change_20d": "20日融資變化",
    "short_sale_change_20d": "20日融券變化"
}

# ==========================================
# 0. 頁面資訊與基本配置
# ==========================================
st.set_page_config(
    page_title="🔥 自動化高階籌碼鎖碼雷達",
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB_PATH = "taiwan_stock.db"

# ==========================================
# 0.5. Supabase 雲端資料庫配置與雙軌切換
# ==========================================
import requests
import os

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# 也可以從 Streamlit secrets 載入
if not SUPABASE_URL or not SUPABASE_KEY:
    try:
        if hasattr(st, "secrets"):
            SUPABASE_URL = st.secrets.get("SUPABASE_URL", SUPABASE_URL)
            SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", SUPABASE_KEY)
    except Exception:
        pass

# 如果有提供 Supabase URL & KEY，就啟用 Supabase 模式
USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_KEY)

if USE_SUPABASE:
    SUPABASE_HEADERS = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }

# ==========================================
# 1. 取得資料庫最新日期作為預設日期與股票名單
# ==========================================
@st.cache_data(ttl=3600)
def get_db_dates_info():
    """
    從資料庫或 Supabase 讀取最新日期與所有有資料的股票清單
    """
    if USE_SUPABASE:
        try:
            # 1. 取得最新交易日
            url_date = f"{SUPABASE_URL}/rest/v1/chase_strategy_results?select=date&order=date.desc&limit=1"
            r_date = requests.get(url_date, headers=SUPABASE_HEADERS, timeout=10)
            latest_date_str = None
            if r_date.status_code == 200 and r_date.json():
                latest_date_str = r_date.json()[0]["date"]
            
            if not latest_date_str:
                return None, pd.DataFrame(columns=["stock_id", "stock_name"])
                
            # 2. 取得該最新交易日之全量股票清單，避免 select distinct 全表
            url_stocks = f"{SUPABASE_URL}/rest/v1/chase_strategy_results?select=stock_id,stock_name&date=eq.{latest_date_str}&order=stock_id.asc"
            r_stocks = requests.get(url_stocks, headers=SUPABASE_HEADERS, timeout=10)
            if r_stocks.status_code == 200:
                df_stocks = pd.DataFrame(r_stocks.json())
                if not df_stocks.empty:
                    df_stocks = df_stocks[["stock_id", "stock_name"]]
                return latest_date_str, df_stocks
            return latest_date_str, pd.DataFrame(columns=["stock_id", "stock_name"])
        except Exception as e:
            st.error(f"從 Supabase 讀取日期與股票清單失敗: {e}")
            return None, pd.DataFrame(columns=["stock_id", "stock_name"])
    else:
        conn = sqlite3.connect(DB_PATH)
        try:
            # 取得最新交易日
            res = conn.execute("SELECT MAX(date) FROM daily_chips").fetchone()
            latest_date_str = res[0] if (res and res[0]) else None
            
            # 取得所有不重複股票代號與名稱對照
            df_stocks = pd.read_sql_query(
                "SELECT DISTINCT stock_id, stock_name FROM daily_chips ORDER BY stock_id ASC", conn
            )
            return latest_date_str, df_stocks
        except Exception:
            return None, pd.DataFrame(columns=["stock_id", "stock_name"])
        finally:
            conn.close()

def cached_run_chip_strategy(target_date, weekly_trend_weeks=0, min_trade_value=0, db_path=DB_PATH):
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
                if r.status_code != 200:
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
                
            # 轉換資料型別以防 JSON string 造成計算錯誤
            numeric_cols = [
                "close", "volume", "shares_issued", 
                "ratio_foreign_trust_20d", "ratio_foreign_trust_20d_capital",
                "ratio_foreign_trust_60d", "ratio_foreign_trust_60d_capital",
                "concentration_5d", "concentration_20d", "concentration_60d", "concentration_120d",
                "price_change_60d", "holder_over_1000", "holder_over_400",
                "margin_purchase_balance", "short_sale_balance",
                "margin_purchase_change_20d", "short_sale_change_20d", "vol_20d",
                "holder_growth_weeks"
            ]
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
                    
            # 進行與 run_chip_strategy 同等的過濾
            df = df[df["close"] * df["volume"] * 1000 >= min_trade_value]
            if weekly_trend_weeks > 0:
                df = df[df["holder_growth_weeks"] >= weekly_trend_weeks]
                
            return df
        except Exception as e:
            st.error(f"從 Supabase 取得策略選股結果失敗: {e}")
            return pd.DataFrame()
    else:
        return run_chip_strategy(target_date, weekly_trend_weeks, min_trade_value, db_path)

latest_date_str, df_all_stocks = get_db_dates_info()

# 初始化 session state 變數以連動表格點擊事件與主題
if "selected_stock_str" not in st.session_state:
    st.session_state.selected_stock_str = None
if "clicked_row_info" not in st.session_state:
    st.session_state.clicked_row_info = None
if "theme" not in st.session_state:
    st.session_state.theme = "dark"  # 預設深色模式

# 轉換預設選股日期
if latest_date_str:
    default_date = datetime.strptime(latest_date_str, "%Y-%m-%d").date()
else:
    default_date = date.today()

# 搜尋個股 Callback 函式
def handle_search():
    if "diagnose_stock_search_field" in st.session_state:
        search_val = st.session_state.diagnose_stock_search_field.strip()
        if search_val:
            if USE_SUPABASE:
                try:
                    url = f"{SUPABASE_URL}/rest/v1/chase_strategy_results?select=stock_id,stock_name&stock_id=eq.{search_val}&limit=1"
                    r = requests.get(url, headers=SUPABASE_HEADERS, timeout=10)
                    if r.status_code == 200 and r.json():
                        res = r.json()[0]
                        matched_str = f"{res['stock_id']} - {res['stock_name']}"
                        st.session_state.selected_stock_str = matched_str
                        st.session_state.search_success = f"🔎 搜尋成功：已加載 {matched_str}，並已重設日期至最新交易日！"
                        if "search_warning" in st.session_state:
                            del st.session_state.search_warning
                    else:
                        st.session_state.search_warning = f"⚠️ 找不到代號為 '{search_val}' 的股票，將維持原選定股票。"
                except Exception as e:
                    st.session_state.search_warning = f"⚠️ Supabase 查詢出錯: {e}"
            else:
                conn = sqlite3.connect(DB_PATH)
                try:
                    res = conn.execute(
                        "SELECT DISTINCT stock_id, stock_name FROM daily_chips WHERE stock_id = ? LIMIT 1",
                        (search_val,)
                    ).fetchone()
                    if res:
                        matched_str = f"{res[0]} - {res[1]}"
                        st.session_state.selected_stock_str = matched_str
            
                        st.session_state.search_success = f"🔎 搜尋成功：已加載 {matched_str}，並已重設日期至最新交易日！"
                        if "search_warning" in st.session_state:
                            del st.session_state.search_warning
                    else:
                        st.session_state.search_warning = f"⚠️ 找不到代號為 '{search_val}' 的股票，將維持原選定股票。"
                finally:
                    conn.close()

# ==========================================
# 2. 載入自定義 CSS 提升視覺質感
# ==========================================
common_css = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&display=swap');

/* 全域字體與行高巨大化 */
html, body, [class*="css"], .stApp, p, span, label, input, select, button {
    font-family: 'Outfit', 'Segoe UI', 'Microsoft JhengHei', sans-serif !important;
    font-size: 19px !important;
    line-height: 1.6 !important;
}

.gradient-bar {
    height: 8px;
    background: linear-gradient(90deg, #ff4b4b 0%, #ff8533 50%, #ffc04d 100%);
    border-radius: 4px;
    margin-bottom: 25px;
}

/* 大區塊主標題樣式巨大化 */
.main-section-title {
    font-size: 2.2rem !important;
    font-weight: 800 !important;
    padding-bottom: 12px;
    margin-top: 40px;
    margin-bottom: 25px;
}

/* 子區塊標題樣式巨大化 */
.section-title {
    font-size: 1.6rem !important;
    font-weight: 800 !important;
    color: #ff8533;
    border-left: 6px solid #ff8533;
    padding-left: 15px;
    margin-top: 30px;
    margin-bottom: 20px;
}

/* 原生 st.container(border=True) 覆寫 */
div[data-testid="stVerticalBlockBorderInside"] {
    background: var(--card-bg) !important;
    border: 2px solid var(--card-border) !important;
    border-radius: 16px !important;
    box-shadow: var(--card-shadow) !important;
    padding: 25px !important;
}

/* 股票診斷看板自訂卡片 */
.premium-info-card {
    background: var(--card-bg) !important;
    border: 2px solid var(--card-border) !important;
    padding: 25px;
    border-radius: 16px;
    box-shadow: var(--card-shadow) !important;
    margin-bottom: 25px;
}

/* 🎯 右上角主題按鈕 CSS fixed 定位 */
.st-key-theme_toggle_container {
    position: fixed !important;
    top: 12px !important;
    right: 170px !important;
    width: auto !important;
    z-index: 999999 !important;
    margin: 0 !important;
    padding: 0 !important;
    background: transparent !important;
}
.st-key-btn_reset_today button {
    font-size: 0.75rem !important;
    padding: 0 6px !important;
    line-height: 1.2 !important;
}

.st-key-theme_toggle_container div[data-testid="stHorizontalBlock"] {
    gap: 8px !important;
    display: flex !important;
    flex-direction: row !important;
    flex-wrap: nowrap !important;
}

.st-key-theme_toggle_container div[data-testid="column"] {
    width: auto !important;
    flex: none !important;
    padding: 0 !important;
    margin: 0 !important;
}

.st-key-theme_toggle_container button {
    height: 30px !important;
    min-height: 30px !important;
    line-height: 30px !important;
    padding: 0 12px !important;
    font-size: 0.9rem !important;
    font-weight: bold !important;
    border-radius: 8px !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    border: 2px solid var(--card-border) !important;
    background-color: var(--card-bg) !important;
    color: var(--main-title-color) !important;
    margin: 0 !important;
}
</style>
<div class="gradient-bar"></div>
"""

if st.session_state.theme == "light":
    theme_css = """
<style>
:root {
    --card-bg: #ffffff;
    --card-border: #cbd5e1; /* 加深邊框對比 */
    --card-shadow: 0 6px 16px rgba(148, 163, 184, 0.12);
    --main-title-color: #000000; /* 純黑 */
}
.stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {
    background-color: #ffffff !important; /* 純白背景 */
    color: #000000 !important; /* 純黑文字 */
}
section[data-testid="stSidebar"] {
    background-color: #f8fafc !important;
    border-right: 2px solid #cbd5e1 !important;
}
.main-section-title {
    color: #000000 !important;
    border-bottom: 3px solid #cbd5e1 !important;
}
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
section[data-testid="stSidebar"] h1, section[data-testid="stSidebar"] h2, section[data-testid="stSidebar"] h3 {
    color: #000000 !important;
}
/* 核心指標卡片數值與標籤巨大化 */
div[data-testid="stVerticalBlockBorderInside"] [data-testid="stMetricValue"],
.premium-info-card [data-testid="stMetricValue"] {
    color: #ffffff !important;
    font-size: 2.8rem !important;
    font-weight: 900 !important;
}
div[data-testid="stVerticalBlockBorderInside"] [data-testid="stMetricLabel"],
.premium-info-card [data-testid="stMetricLabel"] {
    color: #d1d5db !important; /* light gray */
    font-size: 1.4rem !important; /* slightly larger */
    font-weight: 800 !important;
    margin-top: 4px !important;
}
.badge-lock-premium {
    background-color: #eff6ff !important;
    color: #1d4ed8 !important;
    padding: 6px 16px;
    border-radius: 30px;
    border: 2px solid #bfdbfe !important;
    font-size: 1.0em;
    font-weight: 800;
    display: inline-block;
}
.badge-accelerate-premium {
    background-color: #fef2f2 !important;
    color: #b91c1c !important;
    padding: 6px 16px;
    border-radius: 30px;
    border: 2px solid #fecaca !important;
    font-size: 1.0em;
    font-weight: 800;
    display: inline-block;
}
.badge-normal-premium {
    background-color: #f1f5f9 !important;
    color: #475569 !important;
    padding: 6px 16px;
    border-radius: 30px;
    border: 2px solid #e2e8f0 !important;
    font-size: 1.0em;
    font-weight: 700;
    display: inline-block;
}
.guide-box {
    background-color: #f8fafc !important;
    border-left: 5px solid #ea580c !important;
    color: #1e293b !important;
    padding: 15px 20px;
    border-radius: 8px;
    font-size: 1.05rem;
    font-weight: bold;
    border: 1px solid #e2e8f0;
}
.glow-alert-card {
    background: #fef2f2 !important;
    border: 2px solid #fca5a5 !important;
    padding: 25px;
    border-radius: 16px;
    box-shadow: 0 6px 16px rgba(239, 68, 68, 0.08) !important;
    margin-bottom: 25px;
    color: #991b1b !important;
}
.glow-alert-card h4 { color: #991b1b !important; margin: 0 0 12px 0; font-weight: 800; font-size: 1.4rem; }
.glow-alert-card p { color: #b91c1c !important; margin: 0; font-size: 1.1rem; line-height: 1.6; }
.glow-success-card {
    background: #f0fdf4 !important;
    border: 2px solid #bbf7d0 !important;
    padding: 25px;
    border-radius: 16px;
    box-shadow: 0 6px 16px rgba(34, 197, 94, 0.08) !important;
    margin-bottom: 25px;
    color: #166534 !important;
}
.glow-success-card h4 { color: #166534 !important; margin: 0 0 12px 0; font-weight: 800; font-size: 1.4rem; }
.glow-success-card p { color: #15803d !important; margin: 0; font-size: 1.1rem; line-height: 1.6; }
</style>
"""
else:
    theme_css = """
<style>
:root {
    --card-bg: linear-gradient(135deg, #161b22 0%, #0d1117 100%);
    --card-border: #444c56;
    --card-shadow: 0 8px 30px rgba(0, 0, 0, 0.35);
    --main-title-color: #ffffff;
}
.stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {
    background-color: #0d1117 !important;
    color: #f0f6fc !important; /* 更亮的暗色文字 */
}
section[data-testid="stSidebar"] {
    background-color: #0d1117 !important;
    border-right: 2px solid #30363d !important;
}
.main-section-title {
    color: #ffffff !important;
    border-bottom: 3px solid #30363d !important;
}
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
section[data-testid="stSidebar"] h1, section[data-testid="stSidebar"] h2, section[data-testid="stSidebar"] h3 {
    color: #f0f6fc !important;
}
/* 核心指標卡片數值與標籤巨大化 */
div[data-testid="stVerticalBlockBorderInside"] [data-testid="stMetricValue"],
.premium-info-card [data-testid="stMetricValue"] {
    color: #ffffff !important;
    font-size: 2.8rem !important;
    font-weight: 900 !important;
}
div[data-testid="stVerticalBlockBorderInside"] [data-testid="stMetricLabel"],
.premium-info-card [data-testid="stMetricLabel"] {
    color: #d1d5db !important; /* light gray */
    font-size: 1.4rem !important; /* slightly larger */
    font-weight: 800 !important;
    margin-top: 4px !important;
}
.badge-lock-premium {
    background-color: rgba(26, 115, 232, 0.25) !important;
    color: #8ab4f8 !important;
    padding: 6px 16px;
    border-radius: 30px;
    border: 2px solid rgba(26, 115, 232, 0.5) !important;
    font-size: 1.0em;
    font-weight: 800;
    box-shadow: 0 0 12px rgba(26, 115, 232, 0.3) !important;
    display: inline-block;
}
.badge-accelerate-premium {
    background-color: rgba(219, 68, 85, 0.25) !important;
    color: #f28b82 !important;
    padding: 6px 16px;
    border-radius: 30px;
    border: 2px solid rgba(219, 68, 85, 0.5) !important;
    font-size: 1.0em;
    font-weight: 800;
    box-shadow: 0 0 12px rgba(219, 68, 85, 0.3) !important;
    display: inline-block;
}
.badge-normal-premium {
    background-color: rgba(139, 148, 158, 0.2) !important;
    color: #c9d1d9 !important;
    padding: 6px 16px;
    border-radius: 30px;
    border: 2px solid rgba(139, 148, 158, 0.4) !important;
    font-size: 1.0em;
    font-weight: 700;
    display: inline-block;
}
.guide-box {
    background-color: rgba(255, 133, 51, 0.08) !important;
    border-left: 5px solid #ff8533 !important;
    color: #f0f6fc !important;
    padding: 15px 20px;
    border-radius: 8px;
    font-size: 1.05rem;
    font-weight: bold;
    border: 1px solid #30363d;
}
.glow-alert-card {
    background: linear-gradient(135deg, #2b1517 0%, #1c0e10 100%) !important;
    border: 2px solid #e05c66 !important;
    padding: 25px;
    border-radius: 16px;
    box-shadow: 0 0 25px rgba(224, 92, 102, 0.4) !important;
    margin-bottom: 25px;
    color: #f28b82 !important;
}
.glow-alert-card h4 { color: #ff6b6b !important; margin: 0 0 12px 0; font-weight: 800; font-size: 1.4rem; }
.glow-alert-card p { color: #f28b82 !important; margin: 0; font-size: 1.1rem; line-height: 1.6; }
.glow-success-card {
    background: linear-gradient(135deg, #0e2016 0%, #07120c 100%) !important;
    border: 2px solid #34a853 !important;
    padding: 25px;
    border-radius: 16px;
    box-shadow: 0 0 25px rgba(52, 168, 83, 0.3) !important;
    margin-bottom: 25px;
    color: #a5d6a7 !important;
}
.glow-success-card h4 { color: #81c784 !important; margin: 0 0 12px 0; font-weight: 800; font-size: 1.4rem; }
.glow-success-card p { color: #a5d6a7 !important; margin: 0; font-size: 1.1rem; line-height: 1.6; }
</style>
"""

st.markdown(textwrap.dedent(common_css + theme_css), unsafe_allow_html=True)

# ==========================================
# 3. 頂置主題按鈕
# ==========================================
with st.container(key="theme_toggle_container"):
    theme_btn_col1, theme_btn_col2 = st.columns(2)
    with theme_btn_col1:
        if st.button("☀️ 白天", key="btn_theme_l_top", use_container_width=True):
            st.session_state.theme = "light"
            st.rerun()
    with theme_btn_col2:
        if st.button("🌙 黑夜", key="btn_theme_d_top", use_container_width=True):
            st.session_state.theme = "dark"
            st.rerun()

# ==========================================
# 4. 標題與核心思維呈現
# ==========================================
st.markdown("<h1 style=\"font-size:3rem; font-weight:800; margin-top:20px; margin-bottom:20px;\">🔥 自動化高階籌碼鎖碼雷達</h1>", unsafe_allow_html=True)
st.info(
    "💡 **核心思維**：本系統採用『雙軌制架構』，同時整合**每日主力前15大分點買賣超**與**每週五更新的千張大戶股權分散數據**。協助量化交易老手精準過濾出主力長線鎖碼、近期買盤加速，且股價未有過度拉抬跡象（中低位階）的優質台股。"
)

# ==========================================
# 5. 左側控制列 (Sidebar Controls)
# ==========================================
st.sidebar.header("🎯 策略控制中心")

if "analysis_date_widget" not in st.session_state:
    st.session_state.analysis_date_widget = default_date

date_header_col, date_btn_col = st.sidebar.columns([3, 1])
with date_header_col:
    st.markdown('<p style="font-size: 14px; font-weight: 600; margin-bottom: 0px; margin-top: 5px;">選擇策略分析日期</p>', unsafe_allow_html=True)
with date_btn_col:
    if st.button("今日", key="btn_reset_today", use_container_width=True):
        st.session_state.analysis_date_widget = default_date
        st.rerun()

selected_date = st.sidebar.date_input(
    "選擇策略分析日期", 
    value=st.session_state.analysis_date_widget,
    key="analysis_date_widget",
    label_visibility="collapsed"
)
if selected_date is None:
    selected_date = default_date
selected_date_str = selected_date.strftime("%Y-%m-%d")
# Reduce top margin before the diagnosis subheader for tighter layout
st.markdown("<style>.stSidebar h2 {margin-top: 4px !important;}</style>", unsafe_allow_html=True)

# 🔍 個股診斷查詢
st.sidebar.markdown("---")
st.sidebar.subheader("🔍 個股診斷查詢")

if not df_all_stocks.empty:
    stock_options = df_all_stocks.apply(lambda r: f"{r['stock_id']} - {r['stock_name']}", axis=1).tolist()
    if st.session_state.selected_stock_str in stock_options:
        default_stock = st.session_state.selected_stock_str
    else:
        default_stock = "2330 - 台積電" if "2330 - 台積電" in stock_options else stock_options[0]
else:
    stock_options = ["請先執行爬蟲匯入資料"]
    default_stock = "請先執行爬蟲匯入資料"

st.sidebar.text_input(
    "輸入個股代號 (例如: 2317)", 
    placeholder="輸入 4 碼代號...",
    key="diagnose_stock_search_field",
    on_change=handle_search
)

st.sidebar.button("🔍 執行個股診斷", on_click=handle_search, use_container_width=True)

selected_stock_str = st.sidebar.selectbox(
    "或從清單中挑選：", 
    options=stock_options, 
    index=stock_options.index(default_stock) if default_stock in stock_options else 0
)

if selected_stock_str != "請先執行爬蟲匯入資料":
    st.session_state.selected_stock_str = selected_stock_str

# 最低成交金額與策略條件選擇 (樂齡大字體易點擊設計)
with st.sidebar.form(key="filter_form"):
    st.markdown('<p style="font-weight: 800; margin-bottom: 2px;">💰 股票成交熱度門檻</p>', unsafe_allow_html=True)

    
    trade_val_option = st.radio(
        "最低成交金額",
        options=["不限制", "500 萬元", "1,000 萬元", "2,000 萬元", "5,000 萬元"],
        index=0,
        label_visibility="collapsed"
    )
    trade_val_mapping = {
        "不限制": 0,
        "500 萬元": 500,
        "1,000 萬元": 1000,
        "2,000 萬元": 2000,
        "5,000 萬元": 5000
    }
    min_trade_val_million = trade_val_mapping[trade_val_option]
    min_trade_val_ntd = min_trade_val_million * 10000

    st.markdown('<p style="font-weight: 800; margin-bottom: 2px; margin-top: 15px;">👥 千張大戶買進週數</p>', unsafe_allow_html=True)

    
    weeks_option = st.radio(
        "千張大戶連續吸貨週數過濾",
        options=["不限制", "連續 2 週", "連續 3 週", "連續 4 週", "連續 8 週"],
        index=0,
        horizontal=True,
        label_visibility="collapsed"
    )
    weeks_mapping = {"不限制": 0, "連續 2 週": 2, "連續 3 週": 3, "連續 4 週": 4, "連續 8 週": 8}
    weekly_trend_weeks = weeks_mapping[weeks_option]

    st.markdown("---")
    st.markdown('<p style="font-weight: 800; margin-bottom: 2px; font-size: 1.15rem;">🚀 法人機構買超比例最低門檻</p>', unsafe_allow_html=True)

    
    st.markdown('<p style="font-weight: 700; margin-bottom: 2px; font-size: 1.05rem;">▶️ 20日法人買超比</p>', unsafe_allow_html=True)
    inst_ratio_20_option = st.radio(
        "20日法人佔量比 (%)",
        options=["不限制 (0%)", "高於 5%", "高於 10%", "高於 20%", "高於 30%"],
        index=0,
        horizontal=True,
        label_visibility="collapsed"
    )
    inst_ratio_20_mapping = {
        "不限制 (0%)": 0.0,
        "高於 5%": 5.0,
        "高於 10%": 10.0,
        "高於 20%": 20.0,
        "高於 30%": 30.0
    }
    min_inst_ratio_20d = inst_ratio_20_mapping[inst_ratio_20_option]

    st.markdown('<p style="font-weight: 700; margin-bottom: 2px; font-size: 1.05rem; margin-top: 15px;">▶️ 60日法人買超比</p>', unsafe_allow_html=True)
    inst_ratio_60_option = st.radio(
        "60日法人佔量比 (%)",
        options=["不限制 (0%)", "高於 3%", "高於 5%", "高於 10%"],
        index=0,
        horizontal=True,
        label_visibility="collapsed"
    )
    inst_ratio_60_mapping = {
        "不限制 (0%)": 0.0,
        "高於 3%": 3.0,
        "高於 5%": 5.0,
        "高於 10%": 10.0
    }
    min_inst_ratio_60d = inst_ratio_60_mapping[inst_ratio_60_option]

    st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
    submit_button = st.form_submit_button("🎯 執行籌碼雷達選股", use_container_width=True)

# ==========================================
# 6. 主畫面佈局
# ==========================================

# ------------------------------------------
# 區塊一：🎯 籌碼雷達選股結果
# ------------------------------------------
st.markdown(f'<div class="main-section-title">🎯 籌碼雷達選股結果 ({selected_date_str})</div>', unsafe_allow_html=True)

date_changed = st.session_state.get("_last_strategy_date", "") != selected_date_str
force_recalc = submit_button or date_changed or "_df_strategy_cache" not in st.session_state

if force_recalc:
    df_strategy = cached_run_chip_strategy(
        target_date=selected_date_str,
        weekly_trend_weeks=weekly_trend_weeks,
        min_trade_value=min_trade_val_ntd,
        db_path=DB_PATH
    )
    st.session_state["_df_strategy_cache"] = df_strategy
    st.session_state["_last_strategy_date"] = selected_date_str
else:
    df_strategy = st.session_state["_df_strategy_cache"]

if not df_strategy.empty:
    if min_inst_ratio_20d > 0:
        df_strategy = df_strategy[df_strategy["ratio_foreign_trust_20d"] >= min_inst_ratio_20d]
    if min_inst_ratio_60d > 0:
        df_strategy = df_strategy[df_strategy["ratio_foreign_trust_60d"] >= min_inst_ratio_60d]
    
    if not df_strategy.empty:
        total_matches = len(df_strategy)
        lock_count = df_strategy["is_long_lock"].sum()
        if USE_SUPABASE:
            try:
                url = f"{SUPABASE_URL}/rest/v1/chase_strategy_results?select=stock_id&date=eq.{selected_date_str}"
                r = requests.get(url, headers=SUPABASE_HEADERS, timeout=10)
                total_stocks = len(r.json()) if r.status_code == 200 else 0
            except Exception:
                total_stocks = 0
        else:
            total_stocks_conn = sqlite3.connect(DB_PATH)
            total_stocks = total_stocks_conn.execute(
                "SELECT COUNT(DISTINCT stock_id) FROM daily_chips WHERE date = ?",
                (selected_date_str,)
            ).fetchone()[0]
            total_stocks_conn.close()
        
        with st.container(border=True):
            kpi_col1, kpi_col2 = st.columns(2)
            with kpi_col1:
                st.metric("📋 符合篩選股票", f"{total_matches} 檔")
            with kpi_col2:
                st.metric("📊 股票總數", f"{total_stocks} 檔")

        
        df_display = df_strategy.copy().reset_index(drop=True)
        
        display_order = DEFAULT_DISPLAY_ORDER
        
        df_show = df_display[[c for c in display_order if c in df_display.columns]].rename(columns=CHINESE_COLUMNS)
        
        event = st.dataframe(
            df_show, 
            use_container_width=True, 
            height=400,
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "股票代號": st.column_config.TextColumn("股票代號"),
                "股票名稱": st.column_config.TextColumn("股票名稱"),
                "收盤價": st.column_config.NumberColumn("收盤價", format="%.2f 元"),
                "成交張數": st.column_config.NumberColumn("成交張數", format="%d 張"),
                "60日漲跌幅": st.column_config.NumberColumn("60日漲跌幅", format="%.2f%%"),
                "20日法人佔量比": st.column_config.NumberColumn("20日法人佔量比", format="%.2f%%"),
                "20日買超股本比": st.column_config.NumberColumn("20日買超股本比", format="%.4f%%"),
                "60日法人佔量比": st.column_config.NumberColumn("60日法人佔量比", format="%.2f%%"),
                "60日買超股本比": st.column_config.NumberColumn("60日買超股本比", format="%.4f%%"),
                "千張大戶持股比": st.column_config.NumberColumn("千張大戶持股比", format="%.2f%%"),
                "400張以上大戶比": st.column_config.NumberColumn("400張以上大戶比", format="%.2f%%"),
                "20日融資變化": st.column_config.NumberColumn("20日融資變化", format="%+d 張"),
                "20日融券變化": st.column_config.NumberColumn("20日融券變化", format="%+d 張")
            }
        )
        
        if event.selection["rows"]:
            selected_row_idx = event.selection["rows"][0]
            clicked_stock_id = df_show.iloc[selected_row_idx]["股票代號"]
            clicked_stock_name = df_show.iloc[selected_row_idx]["股票名稱"]
            st.session_state.selected_stock_str = f"{clicked_stock_id} - {clicked_stock_name}"
            st.toast(f"🎯 已選定：{clicked_stock_id} {clicked_stock_name}，已同步載入下方診斷區！", icon="📈")
            
        st.success(f"🎉 篩選完成！共找出 {len(df_show)} 檔符合條件之主力籌碼優質股票。💡 *提示：點選表格任一列，即可自動帶入下方個股診斷區。*")
    else:
        st.info("ℹ️ 在目前滑桿與過濾器條件下無符合股票，請嘗試放寬過濾設定。")
else:
    st.info("ℹ️ 當前日期無籌碼選股資料。請先手動執行 crawler.py 匯入歷史數據，或選擇其它已有數據的交易日期。")


# ------------------------------------------
# 區塊二：📈 個股籌碼深度剖析與趨勢
# ------------------------------------------
st.markdown('<div class="main-section-title">📈 個股籌碼深度剖析與趨勢</div>', unsafe_allow_html=True)

if "search_success" in st.session_state and st.session_state.search_success:
    st.toast(st.session_state.search_success, icon="✅")
    del st.session_state.search_success

if "search_warning" in st.session_state and st.session_state.search_warning:
    st.sidebar.warning(st.session_state.search_warning)
    del st.session_state.search_warning

selected_stock_str = st.session_state.selected_stock_str

if selected_stock_str and selected_stock_str != "請先執行爬蟲匯入資料":
    selected_stock_id = selected_stock_str.split(" - ")[0]
    selected_stock_name = selected_stock_str.split(" - ")[1]
    
    if not USE_SUPABASE:
        conn = sqlite3.connect(DB_PATH)
    try:
        df_strat_all = cached_run_chip_strategy(
            target_date=selected_date_str, 
            weekly_trend_weeks=0, 
            min_trade_value=0,
            db_path=DB_PATH
        )
        df_strat_stock = df_strat_all[df_strat_all["stock_id"] == selected_stock_id]
        
        if not df_strat_stock.empty:
            row_info = df_strat_stock.iloc[0]
            
            lock_status = "💎 長線鎖碼中" if row_info["is_long_lock"] else None
            acc_status = "🔥 買盤加速中" if row_info["is_buy_accelerate"] else None
            
            lock_badge = f'<span class="badge-lock-premium">💎 長線鎖碼</span>' if lock_status else ''
            acc_badge = f'<span class="badge-accelerate-premium">🔥 買盤加速</span>' if acc_status else ''
            
            st.markdown(f"""<div class="premium-info-card">
<div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap;">
<div>
<span style="font-size: 2.2rem !important; font-weight: 800; color: var(--main-title-color);">{row_info['stock_id']} {row_info['stock_name']}</span>
<span style="margin-left: 20px; font-size: 1.2rem !important; color: #8b949e; font-weight: 500;">籌碼狀態診斷板 ({selected_date_str})</span>
</div>
<div style="display: flex; gap: 10px; margin-top: 10px; margin-bottom: 10px;">
{lock_badge}
{acc_badge}
</div>
</div>
</div>""", unsafe_allow_html=True)
            
            st.markdown('<div class="section-title">📊 股價與交易量能</div>', unsafe_allow_html=True)
            with st.container(border=True):
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric(f"收盤價 ({selected_date_str.replace('-', '/')})", f"{row_info['close']:.2f} 元")
                with col2:
                    st.metric("60日漲跌幅", f"{row_info['price_change_60d']:.2f}%", 
                              delta=f"{row_info['price_change_60d']:.2f}%" if row_info['price_change_60d'] != 0 else None,
                              delta_color="inverse")
                with col3:
                    st.metric("當日成交量", f"{row_info['volume']:,.0f} 張")
                with col4:
                    st.metric("發行張數 (股本)", f"{row_info['shares_issued']:,.0f} 張")
            
# 主力分點集中度區塊已移除
            
            st.markdown('<div class="section-title">🚀 法人佈局結構 (三大法人買賣)</div>', unsafe_allow_html=True)
            with st.container(border=True):
                col9, col10, col11, col12 = st.columns(4)
                with col9:
                    st.metric("20日法人佔量比", f"{row_info['ratio_foreign_trust_20d']:.2f}%")
                with col10:
                    st.metric("20日買超股本比", f"{row_info['ratio_foreign_trust_20d_capital']:.4f}%")
                with col11:
                    st.metric("60日法人佔量比", f"{row_info['ratio_foreign_trust_60d']:.2f}%")
                with col12:
                    st.metric("60日買超股本比", f"{row_info['ratio_foreign_trust_60d_capital']:.4f}%")

            st.markdown('<div class="section-title">👥 集保大戶結構 (股權分散明細)</div>', unsafe_allow_html=True)
            
            if USE_SUPABASE:
                try:
                    # 從 Supabase 讀取該股票所有的歷史資料 (截至 selected_date_str)
                    url = f"{SUPABASE_URL}/rest/v1/chase_strategy_results?select=date,holder_over_1000,holder_over_400&stock_id=eq.{selected_stock_id}&date=lte.{selected_date_str}&order=date.desc"
                    r = requests.get(url, headers=SUPABASE_HEADERS, timeout=10)
                    if r.status_code == 200:
                        df_daily_hist = pd.DataFrame(r.json())
                        if not df_daily_hist.empty:
                            df_daily_hist["holder_over_1000"] = pd.to_numeric(df_daily_hist["holder_over_1000"], errors='coerce').fillna(0.0)
                            df_daily_hist["holder_over_400"] = pd.to_numeric(df_daily_hist["holder_over_400"], errors='coerce').fillna(0.0)
                            
                            # 利用 W-FRI resample 將日資料降採樣為週資料
                            df_daily_hist["date_dt"] = pd.to_datetime(df_daily_hist["date"])
                            df_weekly_hist = df_daily_hist.set_index("date_dt").resample("W-FRI").last().dropna().reset_index()
                            df_weekly_hist["date"] = df_weekly_hist["date_dt"].dt.strftime("%Y-%m-%d")
                            df_weekly_hist = df_weekly_hist.sort_values(by="date", ascending=False)
                            df_weekly_hist = df_weekly_hist[["date", "holder_over_1000", "holder_over_400"]]
                        else:
                            df_weekly_hist = pd.DataFrame(columns=["date", "holder_over_1000", "holder_over_400"])
                    else:
                        df_weekly_hist = pd.DataFrame(columns=["date", "holder_over_1000", "holder_over_400"])
                except Exception as e:
                    st.error(f"從 Supabase 讀取集保大戶歷史失敗: {e}")
                    df_weekly_hist = pd.DataFrame(columns=["date", "holder_over_1000", "holder_over_400"])
            else:
                df_weekly_hist = pd.read_sql_query(
                    "SELECT date, holder_over_1000, holder_over_400 FROM weekly_shareholders WHERE stock_id = ? AND date <= ? ORDER BY date DESC",
                    conn, params=(selected_stock_id, selected_date_str)
                )
            
            consec_1000_delta = None
            consec_400_delta = None
            
            if len(df_weekly_hist) >= 2:
                v_1000 = df_weekly_hist["holder_over_1000"].tolist()
                consec_1000 = 0
                diff_1000 = v_1000[0] - v_1000[1]
                if diff_1000 > 0.00001:
                    for i in range(len(v_1000) - 1):
                        if v_1000[i] > v_1000[i+1]:
                            consec_1000 += 1
                        else:
                            break
                    consec_1000_delta = f"+{consec_1000} 週 (連續增加)"
                elif diff_1000 < -0.00001:
                    for i in range(len(v_1000) - 1):
                        if v_1000[i] < v_1000[i+1]:
                            consec_1000 += 1
                        else:
                            break
                    consec_1000_delta = f"-{consec_1000} 週 (連續減少)"
                else:
                    consec_1000_delta = "持平"
                    
                v_400 = df_weekly_hist["holder_over_400"].tolist()
                consec_400 = 0
                diff_400 = v_400[0] - v_400[1]
                if diff_400 > 0.00001:
                    for i in range(len(v_400) - 1):
                        if v_400[i] > v_400[i+1]:
                            consec_400 += 1
                        else:
                            break
                    consec_400_delta = f"+{consec_400} 週 (連續增加)"
                elif diff_400 < -0.00001:
                    for i in range(len(v_400) - 1):
                        if v_400[i] < v_400[i+1]:
                            consec_400 += 1
                        else:
                            break
                    consec_400_delta = f"-{consec_400} 週 (連續減少)"
                else:
                    consec_400_delta = "持平"
            else:
                consec_1000_delta = "歷史資料不足"
                consec_400_delta = "歷史資料不足"

            with st.container(border=True):
                col13, col13_2 = st.columns(2)
                with col13:
                    st.metric(
                        "千張大戶持股比", 
                        f"{row_info['holder_over_1000']:.2f}%" if row_info['holder_over_1000'] > 0 else "—",
                        delta=consec_1000_delta,
                        delta_color="inverse"
                    )
                with col13_2:
                    st.metric(
                        "400張以上大戶比", 
                        f"{row_info['holder_over_400']:.2f}%" if row_info['holder_over_400'] > 0 else "—",
                        delta=consec_400_delta,
                        delta_color="inverse"
                    )

            st.markdown('<div class="section-title">📉 信用交易 (融資融券)</div>', unsafe_allow_html=True)
            with st.container(border=True):
                col14, col15, col16, col17 = st.columns(4)
                with col14:
                    margin_diff = row_info['margin_purchase_change_20d']
                    vol_20d = row_info.get('vol_20d', 0.0)
                    margin_ratio = (margin_diff / vol_20d * 100) if vol_20d > 0 else 0.0
                    st.metric(
                        label="20日融資比率", 
                        value=f"{margin_ratio:+.2f}%" if margin_ratio != 0 else "0.00%",
                        delta=f"{margin_diff:+,.0f} 張" if margin_diff != 0 else None,
                        delta_color="inverse"
                    )
                with col15:
                    short_diff = row_info['short_sale_change_20d']
                    vol_20d = row_info.get('vol_20d', 0.0)
                    short_ratio = (short_diff / vol_20d * 100) if vol_20d > 0 else 0.0
                    st.metric(
                        label="20日融券比率", 
                        value=f"{short_ratio:+.2f}%" if short_ratio != 0 else "0.00%",
                        delta=f"{short_diff:+,.0f} 張" if short_diff != 0 else None,
                        delta_color="inverse"
                    )
                with col16:
                    st.metric("融資今日餘額", f"{row_info.get('margin_purchase_balance', 0.0):,.0f} 張")
                with col17:
                    st.metric("融券今日餘額", f"{row_info.get('short_sale_balance', 0.0):,.0f} 張")
            
        else:
            st.info("ℹ️ 資料庫中無此股票在所選日期之前的籌碼計算資料。")
    except Exception as e:
        st.error(f"詳細指標面板載入失敗: {e}")
    finally:
        if not USE_SUPABASE:
            conn.close()