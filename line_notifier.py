import os
import requests
import datetime
import json
import sys
from supabase import create_client, Client

# 讀取 GitHub Secrets 雲端密鑰
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.environ.get("LINE_USER_ID")

# 防呆機制一：如果發現保險箱密鑰根本沒讀到，直接印出原因，不讓系統崩潰
if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ 錯誤：找不到 SUPABASE_URL 或 SUPABASE_KEY，請檢查 GitHub Secrets 設定！")
    sys.exit(1)

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID:
    print("❌ 錯誤：找不到 LINE 機器人密鑰，請檢查 GitHub Secrets 設定！")
    sys.exit(1)

def check_signals_and_notify():
    # 清洗網頁網址字串，防止末尾有多餘空格或斜線導致 API 連線失敗
    url_cleaned = SUPABASE_URL.strip().rstrip('/')
    supabase: Client = create_client(url_cleaned, SUPABASE_KEY.strip())
    
    # 獲取台灣時間日期
    tz_taiwan = datetime.timezone(datetime.timedelta(hours=8))
    today_str = datetime.datetime.now(tz_taiwan).strftime('%Y-%m-%d')
    
    print(f"📡 正在檢查台灣時間 {today_str} 的 Supabase 訊號...")
    
    try:
        response = supabase.table("etf_signals").select("*").eq("update_date", today_str).execute()
    except Exception as e:
        print(f"❌ 讀取 Supabase 資料表失敗，錯誤訊息: {e}")
        return
        
    if not response.data:
        print(f"⚪ 提示：今日 ({today_str}) Supabase 尚未存入數據，或暫無最新個股訊號。")
        return
        
    contents = []
    for row in response.data:
        status = row.get('signal_status', '⚪ 觀望中')
        
        # 🎯 完美同步自適應新提示字眼，其餘觀望雜訊一律過濾！
        if "買入" in status or "賣出" in status or "抱緊" in status:
            # 根據新版狀態文字動態給予正確字卡外框顏色
            color = "#2ecc71" if "買入" in status else ("#e74c3c" if "賣出" in status else ("#f1c40f" if "抱緊" in status else "#95a5a6"))
            
            item_block = {
                "type": "box",
                "layout": "vertical",
                "margin": "lg",
                "spacing": "sm",
                "borderColor": color,
                "borderWidth": "2px",
                "cornerRadius": "md",
                "paddingAll": "10px",
                "contents": [
                    {"type": "text", "text": f"🏢 {row.get('ticker', '未知')}", "weight": "bold", "size": "md"},
                    {"type": "text", "text": f"💰 今日收盤: ${float(row.get('price', 0)):.2f} 元", "size": "sm", "color": "#555555"},
                    {"type": "text", "text": f"📍 KD 狀態: K={float(row.get('k_value', 0)):.1f}, D={float(row.get('d_value', 0)):.1f}", "size": "sm", "color": "#555555"},
                    {"type": "text", "text": f"📢 操盤建議: {status}", "size": "sm", "weight": "bold", "color": color}
                ]
            }
            contents.append(item_block)
            
    if contents:
        line_url = "https://api.line.me/v2/bot/message/push"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN.strip()}"
        }
        
        flex_payload = {
            "to": LINE_USER_ID.strip(),
            "messages": [
                {
                    "type": "flex",
                    "altText": "⚡ 智慧策略雷達 - 實戰訊號觸發！",
                    "contents": {
                        "type": "bubble",
                        "header": {
                            "type": "box",
                            "layout": "vertical",
                            "backgroundColor": "#1a1a1a",
                            "contents": [
                                {"type": "text", "text": "📡 智慧策略雷達", "weight": "bold", "color": "#ffffff", "size": "lg"},
                                {"type": "text", "text": f"自適應實戰訊號通知 ({today_str})", "color": "#aaaaaa", "size": "xs", "margin": "xs"}
                            ]
                        },
                        "body": {
                            "type": "box",
                            "layout": "vertical",
                            "contents": contents
                        }
                    }
                }
            ]
        }
        
        req = requests.post(line_url, headers=headers, data=json.dumps(flex_payload))
        if req.status_code == 200:
            print("🟢 LINE 機器人高級訊息（Flex Message）發送成功！")
        else:
            print(f"❌ 發送失敗，LINE 伺服器錯誤回應: {req.text}")
    else:
        print("⚪ 今日精選標的皆處於觀望型態，不發送 LINE 訊息打擾。")

if __name__ == "__main__":
    check_signals_and_notify()
