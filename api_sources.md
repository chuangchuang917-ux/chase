# 台灣股市數據 API 資源配置與操作指南

本文件記錄並彙整了用戶提供的外部數據源與 API 金鑰，方便未來開發籌碼分析與數據抓取功能時隨時查閱。

---

## 💎 1. TEJ (台灣經濟新報) API 資源
TEJ API 提供高度標準化與整理過的金融資料，能夠避免動態網頁爬蟲被封鎖的問題。

*   **API 金鑰 (API KEY)**: `r9yBJidJ0dUmQJvXr9EfPzqFJ2dEp9`
*   **有效期間**: `2026-06-23` 至 `2026-09-23`
*   **試用資料庫說明**: [TEJ 試用資料庫說明頁](https://api.tej.com.tw/datatables.html?db=TRAIL&t=%E8%A9%A6%E7%94%A8%E8%B3%87%E6%96%99%E5%BA%AB)
*   **官方 API 使用說明**: [https://api.tej.com.tw](https://api.tej.com.tw)
*   **可用試用資料表**:
    *   **`TRAIL/TAPRCD`** (未調整日股價與交易量數據)：包含 2025 年以來的 0050 等個股日交易資料。

### Python 快速對接範例 (REST API)
```python
import requests
import json

api_key = "r9yBJidJ0dUmQJvXr9EfPzqFJ2dEp9"
# 撈取個股日股價數據 (以 0050 為例)
url = f"https://api.tej.com.tw/api/datatables/TRAIL/TAPRCD.json?api_key={api_key}"
headers = {"User-Agent": "Mozilla/5.0"} # 需加 User-Agent 防止被 Application Gateway 阻擋 (403/502)

response = requests.get(url, headers=headers, timeout=10)
if response.status_code == 200:
    data = response.json()
    print("成功讀取 TEJ 數據！")
else:
    print(f"失敗，狀態碼: {response.status_code}")
```

---

## 🏛️ 2. 證交所 (TWSE) OpenAPI 資源
證交所官方提供的免費開放資料 API，包含最新大盤、個股與公司治理等數據。

*   **官方 OpenAPI 入口**: [https://openapi.twse.com.tw/](https://openapi.twse.com.tw/)
*   **API 規格說明 (Swagger API Spec)**: [https://openapi.twse.com.tw/v1/swagger.json](https://openapi.twse.com.tw/v1/swagger.json)
*   **基本網址 (Base URL)**: `https://openapi.twse.com.tw/v1`
*   **數據涵蓋範圍**:
    *   大盤每日收盤及歷史交易資訊。
    *   三大法人每日交易明細、信用交易（融資融券）餘額。
    *   公司治理與 ESG 披露（董事會會議、環境廢棄物與能耗、人力資源等）。

### Python 快速對接範例
```python
import requests

# 取得三大法人買賣超日報表
url = "https://openapi.twse.com.tw/v1/3I/3I80U"
response = requests.get(url, timeout=10)
if response.status_code == 200:
    data = response.json()
    # 資料解析與入庫
```

---

## 🚀 3. FinMind API 資源
FinMind 提供多元的台灣股票、權證、期權等金融數據。

*   **API 授權金鑰 (Primary Token)**: `eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiYWxiZXJ0MDkxNyIsImVtYWlsIjoiYWxiZXJ0MDkxN0BnbWFpbC5jb20iLCJ0b2tlbl92ZXJzaW9uIjowLCJleHAiOjE3ODM0MTcwMjl9.snTeoVkjJqMb7m655PA_lA8yxPgdSE24Sfm0A9n-jxU`
*   **API 授權金鑰 (Fallback Token)**: `eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiY2h1YW5nY2h1YW5nOTE3QGdtYWlsLmNvbSIsImVtYWlsIjoiY2h1YW5nY2h1YW5nOTE3QGdtYWlsLmNvbSIsInRva2VuX3ZlcnNpb24iOjIsImV4cCI6MTc4MzQxNzA0M30.IKH0tshNaAX_OAfXnFlzrygANbbGyo_KAs_M2JlO_tg`
*   **API 授權金鑰 (Third Token)**: `eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiY2h1YW5na3VuNjlAZ21haWwuY29tIiwiZW1haWwiOiJjaHVhbmdrdW42OUBnbWFpbC5jb20iLCJ0b2tlbl92ZXJzaW9uIjowLCJleHAiOjE3ODM0MTcyMDF9.dmGveEOR8lEXdA2Wibx8DcOYoHrVWBc3X2w0s1RPQSU`
*   **官方網站**: [https://finmindtrade.com/](https://finmindtrade.com/)
*   **Python SDK 對接範例**:
```python
from FinMind.data import DataLoader

api = DataLoader()
# 登入 Token 即可獲得比免費帳戶更高的速率限制與部分批次數據下載權限
api.login_by_token(api_token="eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiY2h1YW5nY2h1YW5nOTE3QGdtYWlsLmNvbSIsImVtYWlsIjoiY2h1YW5nY2h1YW5nOTE3QGdtYWlsLmNvbSIsInRva2VuX3ZlcnNpb24iOjF9.SlWtLQstQJGUCVKl42NxUG8wfqNt6tWD-reyP3xcyBY")

# 獲取台積電日交易數據
df = api.taiwan_stock_daily(stock_id="2330", start_date="2026-06-01", end_date="2026-06-22")
```

---

## 🏔️ 4. Norway Twsthr 集保大戶網頁爬蟲來源
該公開網站收集了台股歷史集保分散明細，是不使用官方 API 限制下，免費抓取歷史「千張大戶比」與「400張以上大戶比」的極速來源。

*   **網站入口**: [https://norway.twsthr.info/StockHolders.aspx](https://norway.twsthr.info/StockHolders.aspx)
*   **個股參數網址**: `https://norway.twsthr.info/StockHolders.aspx?stock={stock_id}`
*   **爬蟲對接特性**:
    *   網站編碼為 `big5` (Big-5 繁體中文)。
    *   **無防爬蟲驗證**，下載單股歷年所有數據僅需一個 HTTP GET 請求（50檔成分股可在 30 秒內全數爬完）。
    *   網頁解析時需定位 **`Table 9`**，並使用 `recursive=False` 來讀取該列，其中 **Col 7** 欄位為 `400張以上大戶持股比`，**Col 13** 欄位為 `千張大戶持股比`。

### Python 爬蟲實作範例
```python
import requests
from bs4 import BeautifulSoup

stock_id = "2330"
url = f"https://norway.twsthr.info/StockHolders.aspx?stock={stock_id}"
headers = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://norway.twsthr.info/"
}
r = requests.get(url, headers=headers, timeout=15)
r.encoding = "big5" # 必須手動指定 big5 編碼

soup = BeautifulSoup(r.text, 'html.parser')
tables = soup.find_all("table")
target_table = None

# 精確尋找大戶數據表 (Table 9)
for t in tables:
    rows = t.find_all("tr")
    if len(rows) > 100:
        first_row_cols = rows[0].find_all(["td", "th"])
        if 12 <= len(first_row_cols) < 50:
            target_table = t
            break

if target_table:
    rows = target_table.find_all("tr")[1:] # 跳過表頭
    for row in rows:
        cols = [td.text.strip() for td in row.find_all(["td", "th"], recursive=False)]
        if len(cols) >= 14:
            date = cols[2] # 格式為 YYYYMMDD (如 20260618)
            holder_over_400 = float(cols[7].replace(",", ""))
            holder_over_1000 = float(cols[13].replace(",", ""))
            print(f"日期: {date}, 400張以上: {holder_over_400}%, 1000張以上: {holder_over_1000}%")
```


