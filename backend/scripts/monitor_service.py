
import os
import time
import requests
import json
import logging
from datetime import datetime
import sys

# Add backend directory to path for config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from dotenv import load_dotenv

# Load Env
load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))
load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))

# --- CONFIG ---
# --- CONFIG ---
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API_KEY = os.getenv("API_KEY") 
PORT = os.getenv("PORT", "10000")
API_URL = f"http://localhost:{PORT}"
RPC_URL = os.getenv("MAINNET_RPC_URL")

# Refresh Interval for Background Checks
INTERVAL = 60

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("MonitorBot")

if not TOKEN:
    logger.critical("TELEGRAM_BOT_TOKEN not found!")
    exit(1)

def get_headers():
    if API_KEY:
        return {"X-API-Key": API_KEY}
    return {}

# --- TELEGRAM API ---
def tg_request(method, data=None):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/{method}"
        res = requests.post(url, json=data, timeout=20)
        return res.json()
    except Exception as e:
        logger.error(f"Telegram API Error ({method}): {e}")
        return None

def send_message(chat_id, text, reply_markup=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return tg_request("sendMessage", data)

def edit_message(chat_id, message_id, text, reply_markup=None):
    data = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return tg_request("editMessageText", data)

def answer_callback(callback_query_id, text=None):
    data = {"callback_query_id": callback_query_id}
    if text:
        data["text"] = text
    return tg_request("answerCallbackQuery", data)

# --- DATA FETCHING ---
def check_api_health():
    try:
        start = time.time()
        res = requests.get(f"{API_URL}/", headers=get_headers(), timeout=5)
        latency = (time.time() - start) * 1000
        if res.status_code == 200:
            data = res.json()
            last_indexed = data.get("last_indexed_block")
            return True, f"{int(latency)}ms", last_indexed
        else:
            return False, f"Status Code: {res.status_code}", None
    except Exception as e:
        return False, str(e), None

def get_latest_block():
    try:
        if not RPC_URL:
            return None
        payload = {"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}
        res = requests.post(RPC_URL, json=payload, timeout=5)
        data = res.json()
        return int(data['result'], 16)
    except Exception as e:
        logger.error(f"RPC Error: {e}")
        return None

def get_asset_stats(symbol, endpoint="/rates"):
    try:
        url = f"{API_URL}{endpoint}?symbol={symbol}&limit=48&resolution=1H"
        if endpoint == "/eth-prices":
            url = f"{API_URL}{endpoint}?limit=48&resolution=1H"
            
        res = requests.get(url, headers=get_headers(), timeout=10)
        if res.status_code != 200:
            return None, None
            
        data = res.json()
        if not data:
            return None, None

        # Sort DESC (Newest First) to ensure data[0] is current
        data.sort(key=lambda x: x['timestamp'], reverse=True)

        current = data[0]
        target_ts = current['timestamp'] - 86400
        past = None
        min_diff = 3600 * 2
        
        for item in data:
            diff = abs(item['timestamp'] - target_ts)
            if diff < min_diff:
                min_diff = diff
                past = item
                
        return current, past
    except Exception as e:
        logger.error(f"Failed to fetch stats for {symbol}: {e}")
        return None, None

def generate_report():
    is_healthy, latency, last_indexed = check_api_health()
    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    status_emoji = "🟢" if is_healthy else "🔴"
    status_text = "Online" if is_healthy else "Offline"
    
    report = f"📊 **System Dashboard**\n🕒 `{now_str}`\n\n"
    report += f"**{status_emoji} API Status**: {status_text}\n"
    report += f"**⏱️ Response Time**: {latency}\n"
    
    # Block Lag
    if is_healthy:
        latest_block = get_latest_block()
        if latest_block and last_indexed:
            lag = latest_block - last_indexed
            # If lag is huge, show warning
            lag_emoji = "✅" if lag < 50 else ("⚠️" if lag < 300 else "🚨")
            report += f"**📦 Block Lag**: {lag_emoji} {lag:,} blocks\n\n"
        else:
             report += f"**📦 Block Lag**: N/A\n\n"

        report += "**📉 Market Rates (24h Trend)**\n"
        for symbol in ["USDC", "DAI", "USDT"]:
            curr, past = get_asset_stats(symbol)
            if curr:
                rate = curr.get('apy', 0)
                change_str = " (➖ 0.00%)"
                if past:
                    old_rate = past.get('apy', 0)
                    if old_rate > 0:
                        delta_pct = ((rate - old_rate) / old_rate) * 100
                        sign = "+" if delta_pct >= 0 else ""
                        # Threshold 0.5% relative change for arrow (consistent with ETH)
                        arrow = "⬆️" if delta_pct > 0.5 else ("⬇️" if delta_pct < -0.5 else "➖")
                        change_str = f" ({arrow} {sign}{delta_pct:.2f}%)"
                    else:
                         change_str = " (➖ 0.00%)"
                report += f"• **{symbol}**: `{rate:.2f}%`{change_str}\n"
            else:
                report += f"• **{symbol}**: `N/A`\n"

        report += "\n"
        curr_eth, past_eth = get_asset_stats("ETH", endpoint="/eth-prices")
        if curr_eth:
            price = curr_eth.get('price', 0)
            change_str = " (➖ 0.0%)"
            if past_eth:
                old_price = past_eth.get('price', 0)
                if old_price > 0:
                    delta_pct = ((price - old_price) / old_price) * 100
                    sign = "+" if delta_pct >= 0 else ""
                    arrow = "⬆️" if delta_pct > 0.5 else ("⬇️" if delta_pct < -0.5 else "➖")
                    change_str = f" ({arrow} {sign}{delta_pct:.1f}%)"
            report += f"**💎 ETH Price**: `${price:,.2f}`{change_str}\n"
        
        report += "\n**✅ Check**: Stable"
    else:
        # Sanitize error message (if it's long)
        error_msg = str(latency)
        if len(error_msg) > 100:
            error_msg = error_msg[:100] + "..."
        report += f"\n⚠️ **System is DOWN**\nReason: `{error_msg}`"
    
    return report

def get_dashboard_markup():
    return {"inline_keyboard": [[{"text": "🔄 Refresh", "callback_data": "refresh"}]]}

# --- MAIN LOOP ---
def monitor_loop():
    global CHAT_ID
    logger.info("🤖 Interactive Monitor Bot Started")
    
    if CHAT_ID:
        send_message(CHAT_ID, "🤖 **Interactive Bot Started**\nSend /start to open dashboard.")

    offset = 0
    last_check_time = 0
    status_ok = True
    last_report_hour = -1

    while True:
        # A. Background Health Check & Alerts (Every 60s)
        if time.time() - last_check_time > INTERVAL:
            last_check_time = time.time()
            is_healthy, reason, _ = check_api_health()
            
            if status_ok and not is_healthy:
                status_ok = False
                if CHAT_ID:
                    # Sanitize error
                    reason_safe = str(reason)
                    if len(reason_safe) > 100:
                         reason_safe = reason_safe[:100] + "..."
                    send_message(CHAT_ID, f"🚨 **ALERT: System DOWN** 🚨\nReason: `{reason_safe}`")
            elif not status_ok and is_healthy:
                status_ok = True
                if CHAT_ID:
                    send_message(CHAT_ID, "✅ **RECOVERY: System UP**")
            
            # Hourly Report
            now = datetime.now()
            if now.minute == 0 and now.hour != last_report_hour and CHAT_ID:
                 if is_healthy:
                    report = generate_report()
                    # Determine title (this is auto-report)
                    report = report.replace("System Dashboard", "Hourly Autoscan")
                    send_message(CHAT_ID, report)
                 last_report_hour = now.hour

        # B. Long Polling for Updates (Timeout 5s to allow loop to cycle)
        try:
            updates_res = tg_request("getUpdates", {"offset": offset, "timeout": 5})
            
            if updates_res and updates_res.get("ok"):
                for update in updates_res["result"]:
                    offset = update["update_id"] + 1
                    
                    # 1. Handle Message (Commands)
                    if "message" in update:
                        msg = update["message"]
                        chat = msg.get("chat", {}).get("id")
                        text = msg.get("text", "")
                        
                        if chat:
                            CHAT_ID = str(chat) # Auto-save chat ID
                        
                        if text == "/start" or text == "/status":
                            report = generate_report()
                            send_message(CHAT_ID, report, get_dashboard_markup())
                    
                    # 2. Handle Callback Query (Buttons)
                    if "callback_query" in update:
                        cb = update["callback_query"]
                        cb_id = cb["id"]
                        chat_id = cb["message"]["chat"]["id"]
                        msg_id = cb["message"]["message_id"]
                        data = cb["data"]
                        
                        if data == "refresh":
                            # Acknowledge click immediately
                            answer_callback(cb_id, "Refreshing data...")
                            # Generate new report
                            new_report = generate_report()
                            # Edit message
                            edit_message(chat_id, msg_id, new_report, get_dashboard_markup())

        except Exception as e:
            logger.error(f"Polling Loop Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    monitor_loop()

