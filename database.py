import sqlite3
import os

DB_NAME = "taiwan_stock.db"

def init_db(db_path=DB_NAME):
    """
    初始化 SQLite 資料庫，建立籌碼核心資料表（雙軌制結構）與複合索引，並套用效能優化參數。
    """
    db_dir = os.path.dirname(os.path.abspath(db_path))
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)
        
    conn = sqlite3.connect(db_path)
    try:
        # 套用 SQLite 效能優化參數
        conn.execute("PRAGMA journal_mode = WAL;")      # 啟用 Write-Ahead Logging (WAL) 模式，提升並行讀寫效能
        conn.execute("PRAGMA synchronous = NORMAL;")    # 優化寫入同步機制，減少 I/O 阻塞
        conn.execute("PRAGMA foreign_keys = ON;")       # 啟用外鍵約束
        
        cursor = conn.cursor()
        
        # 1. 建立 daily_chips 表格 (每日法人與主力合計數據)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_chips (
                date TEXT NOT NULL,
                stock_id TEXT NOT NULL,
                stock_name TEXT NOT NULL,
                close REAL,
                volume REAL,
                shares_issued REAL,
                foreign_buy_shares REAL,
                trust_buy_shares REAL,
                top15_buy_total REAL,
                top15_sell_total REAL,
                margin_purchase_balance REAL,
                short_sale_balance REAL,
                PRIMARY KEY (date, stock_id)
            );
        """)
        
        # 建立 daily_chips (date, stock_id) 複合索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_daily_chips_date_stock 
            ON daily_chips (date, stock_id);
        """)
        
        # 安全升級機制：若 daily_chips 已存在但缺信用交易欄位，則動態新增
        cursor.execute("PRAGMA table_info(daily_chips);")
        columns = [col[1] for col in cursor.fetchall()]
        if "margin_purchase_balance" not in columns:
            cursor.execute("ALTER TABLE daily_chips ADD COLUMN margin_purchase_balance REAL DEFAULT 0.0;")
            print("[INFO] 每日籌碼表成功升級，新增 融資今日餘額 欄位。")
        if "short_sale_balance" not in columns:
            cursor.execute("ALTER TABLE daily_chips ADD COLUMN short_sale_balance REAL DEFAULT 0.0;")
            print("[INFO] 每日籌碼表成功升級，新增 融券今日餘額 欄位。")
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS weekly_shareholders (
                date TEXT NOT NULL,
                stock_id TEXT NOT NULL,
                holder_over_1000 REAL,
                holder_over_400 REAL,
                PRIMARY KEY (date, stock_id)
            );
        """)
        
        # 建立 weekly_shareholders (date, stock_id) 複合索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_weekly_shareholders_date_stock 
            ON weekly_shareholders (date, stock_id);
        """)
        
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

if __name__ == '__main__':
    try:
        init_db()
        print("[SUCCESS] 終極完美版籌碼資料庫建置成功，兩張表與索引已完全準備就緒！")
    except Exception as err:
        print(f"[ERROR] 資料庫建置失敗: {err}")
