"""
自動循環跑 backfill_finmind.py
每輪 200 檔 → 等重置 → 下一輪 → 08:00 停
v2 - 修復 timeout 問題，改用動態等待
"""
import subprocess, time, requests, sys, os, signal
from datetime import datetime, timezone, timedelta

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

TZ_TW = timezone(timedelta(hours=8))
LOG = "backfill_finmind.log"
END_HOUR = 99  # 不限制時間，跑到所有資料補完為止
SCRIPT = os.path.join(os.path.dirname(__file__), "backfill_finmind.py")

def now_str():
    return datetime.now(TZ_TW).strftime("%H:%M")

def finmind_ok():
    try:
        r = requests.get("https://api.finmindtrade.com/api/v4/data", params={
            "dataset": "TaiwanStockPrice", "data_id": "2330",
            "start_date": "2026-06-20", "end_date": "2026-06-23",
            "token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiYWxiZXJ0MDkxNyIsImVtYWlsIjoiYWxiZXJ0MDkxN0BnbWFpbC5jb20iLCJ0b2tlbl92ZXJzaW9uIjowfQ.NigTcrEmzoH4Ntj3RDzfcRCT2a397hsERMydNZuy05c"
        }, timeout=10)
        return r.json().get("msg") == "success"
    except:
        return False

def log(msg):
    t = now_str()
    print(f"[{t}] {msg}", flush=True)

def wait_for_reset(max_minutes=120):
    """等 FinMind 解鎖，最多等 max_minutes 分鐘"""
    waited = 0
    while not finmind_ok():
        time.sleep(60)
        waited += 1
        if waited % 5 == 0:
            log(f"  仍不可用，已等 {waited} 分鐘")
        if waited >= max_minutes:
            log(f"⚠️ 等了 {max_minutes} 分鐘仍未解鎖")
            return False
    log(f"  ✅ FinMind 已解鎖（等了 {waited} 分鐘）")
    return True

def run_one_round(round_num):
    """跑一輪 backfill_finmind.py，回傳成功/失敗"""
    log(f"🚀 第 {round_num} 輪開始...")

    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"  === 第 {round_num} 輪開始 @ {now_str()} ===\n")
        f.write(f"{'='*60}\n")

    # 先開 logs
    stdout_log = open(f"round_{round_num}.log", "w", encoding="utf-8")

    proc = subprocess.Popen(
        [sys.executable, "-u", SCRIPT],
        stdout=stdout_log, stderr=subprocess.STDOUT,
        cwd=os.path.dirname(__file__)
    )

    # 監控過程：每 5 分鐘檢查一下 FinMind，如果一直被限速就提早收
    while True:
        try:
            proc.wait(timeout=300)  # 5 分鐘檢查點
            break  # 正常結束
        except subprocess.TimeoutExpired:
            # 檢查是不是限速空轉中
            try:
                round_log_file = f"round_{round_num}.log"
                if os.path.exists(round_log_file):
                    with open(round_log_file, "r", encoding="utf-8") as f:
                        log_content = f.read()
                    empty_count = log_content.count("限速")
                    total = log_content.count("寫入") + empty_count
                    if total > 0 and empty_count >= total * 0.8:
                        log(f"  已 {empty_count}/{total} 檔空轉，提前收工等重置")
                        proc.terminate()
                        try:
                            proc.wait(timeout=30)
                        except:
                            proc.kill()
                        break
            except Exception as e:
                log(f"  檢查限速時發生錯誤: {e}")

    stdout_log.close()

    # 把 round log 追加到主 log
    round_log_file = f"round_{round_num}.log"
    if os.path.exists(round_log_file):
        with open(round_log_file, "r", encoding="utf-8") as f:
            round_content = f.read()
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(round_content)

    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"  === 第 {round_num} 輪結束 @ {now_str()} ===\n")

    log(f"✅ 第 {round_num} 輪完成 (exit: {proc.returncode})")
    return True


# ========== 主循環 ==========
round_num = 0
while True:
    now = datetime.now(TZ_TW)
    if now.hour >= END_HOUR or (now.hour == END_HOUR - 1 and now.minute >= 40):
        log(f"🛑 已 {now_str()}，來不及跑下一輪，停止")
        break

    round_num += 1

    # 1. 等 FinMind 解鎖
    if not finmind_ok():
        log("🛑 FinMind 已被限制（或不可用），停止並退出。")
        break

    # 2. 檢查時間還夠不夠
    now = datetime.now(TZ_TW)
    if now.hour >= END_HOUR - 1 and now.minute >= 40:
        log(f"🛑 已 {now_str()}，來不及跑一輪，結束")
        break

    # 3. 跑一輪
    run_one_round(round_num)

    # 4. 檢查時間
    now = datetime.now(TZ_TW)
    if now.hour >= END_HOUR or (now.hour == END_HOUR - 1 and now.minute >= 30):
        log(f"🛑 已 {now_str()}，停止")
        break

    # 5. 等重置
    log("🛑 一輪結束或偵測到限速，停止並退出以防止空轉。")
    break

log(f"\n{'='*60}")
log("🏁 全部完成！")

# 顯示總結
import sqlite3
conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "taiwan_stock.db"))
full = conn.execute("SELECT COUNT(*) FROM (SELECT stock_id, COUNT(DISTINCT date) as days FROM daily_chips GROUP BY stock_id HAVING days >= 120)").fetchone()[0]
need = conn.execute("SELECT COUNT(*) FROM (SELECT stock_id, COUNT(DISTINCT date) as days FROM daily_chips GROUP BY stock_id HAVING days < 120)").fetchone()[0]
conn.close()
log(f"累計 >=120天: {full} 檔, 尚缺: {need} 檔")