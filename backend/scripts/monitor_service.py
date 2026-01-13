
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
API_KEY = os.getenv("API_KEY") # For authenticated requests
API_URL = "http://localhost:8000" # Local health check

# Refresh Interval
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

def send_telegram_message(message):
    global CHAT_ID
    if not CHAT_ID:
        # Try to fetch from getUpdates if user messaged the bot
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
            res = requests.get(url, timeout=10).json()
            if res['ok'] and len(res['result']) > 0:
                # Get the last chat ID
                CHAT_ID = str(res['result'][-1]['message']['chat']['id'])
                logger.info(f"Discovered Chat ID: {CHAT_ID}")
            else:
                logger.warning("Chat ID not set and no updates found. Please search for the bot and send /start")
                return False
        except Exception as e:
            logger.error(f"Failed to fetch updates: {e}")
            return False

    if not CHAT_ID:
        return False

    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
        requests.post(url, json=data, timeout=10)
        return True
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
        return False

def check_api_health():
    try:
        start = time.time()
        res = requests.get(f"{API_URL}/", timeout=5)
        latency = (time.time() - start) * 1000
        if res.status_code == 200:
            return True, f"{int(latency)}ms"
        else:
            return False, f"Status Code: {res.status_code}"
    except Exception as e:
        return False, str(e)

def get_asset_stats(symbol, endpoint="/rates"):
    """Fetch Current and 24h ago stats for an asset"""
    try:
        # Limit 48 to ensure we capture 24h ago even with minor gaps
        url = f"{API_URL}{endpoint}?symbol={symbol}&limit=48&resolution=1H"
        # Using resolution=1H is safer for history comparison
        if endpoint == "/eth-prices":
            url = f"{API_URL}{endpoint}?limit=48&resolution=1H"
            
        res = requests.get(url, headers=get_headers(), timeout=10)
        if res.status_code != 200:
            return None, None
            
        data = res.json()
        if not data:
            return None, None

        current = data[0]
        
        # Find ~24h ago
        # data is sorted DESC (newest first).
        # We want timestamp closest to (current['timestamp'] - 86400)
        target_ts = current['timestamp'] - 86400
        
        # Search for closest match
        past = None
        min_diff = 3600 * 2 # Tolerance window
        
        for item in data:
            diff = abs(item['timestamp'] - target_ts)
            if diff < min_diff:
                min_diff = diff
                past = item
                
        return current, past
    except Exception as e:
        logger.error(f"Failed to fetch stats for {symbol}: {e}")
        return None, None

def generate_hourly_report(is_healthy, latency):
    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    status_emoji = "🟢" if is_healthy else "🔴"
    status_text = "Online" if is_healthy else "Offline"
    
    report = f"📊 **Hourly System Report**\n🕒 `{now_str}`\n\n"
    report += f"**{status_emoji} API Status**: {status_text}\n"
    report += f"**⏱️ Response Time**: {latency}\n\n"
    
    # 1. Market Rates
    report += "**📉 Market Rates (24h Trend)**\n"
    for symbol in ["USDC", "DAI", "USDT"]:
        curr, past = get_asset_stats(symbol)
        if curr:
            rate = curr.get('apy', 0)
            # Change
            change_str = " (➖ 0.00%)"
            if past:
                old_rate = past.get('apy', 0)
                delta = rate - old_rate
                sign = "+" if delta >= 0 else ""
                arrow = "⬆️" if delta > 0.05 else ("⬇️" if delta < -0.05 else "➖")
                change_str = f" ({arrow} {sign}{delta:.2f}%)"
            
            report += f"• **{symbol}**: `{rate:.2f}%`{change_str}\n"
        else:
            report += f"• **{symbol}**: `N/A`\n"

    # 2. ETH Price
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
    
    # 3. Overall Check
    report += "\n**✅ Check**: Stable"
    return report

def monitor_loop():
    logger.info("🤖 Monitor Bot Started")
    send_telegram_message("🤖 **Monitor Bot Started**\nWatching System...")

    status_ok = True
    last_report_hour = -1

    while True:
        # 1. Check API Health
        is_healthy, latency_or_error = check_api_health()
        
        # State Change Alerts
        if status_ok and not is_healthy:
            status_ok = False
            msg = f"🚨 **ALERT: System DOWN** 🚨\nReason: `{latency_or_error}`\nTime: {datetime.utcnow().strftime('%H:%M:%S UTC')}"
            logger.error("System went DOWN")
            send_telegram_message(msg)
        
        elif not status_ok and is_healthy:
            status_ok = True
            msg = f"✅ **RECOVERY: System UP**\nTime: {datetime.utcnow().strftime('%H:%M:%S UTC')}"
            logger.info("System Recovered")
            send_telegram_message(msg)

        # 2. Hourly Report (Every hour at :00)
        now = datetime.now()
        if now.minute == 0 and now.hour != last_report_hour:
             # Wait a few seconds to let backend stabilize if just restarted/hourly tasks running
            if is_healthy:
                logger.info("Generating Hourly Report...")
                report = generate_hourly_report(is_healthy, latency_or_error)
                send_telegram_message(report)
                last_report_hour = now.hour
            else:
                send_telegram_message("⚠️ **Hourly Report Skipped**: System is DOWN.")
                last_report_hour = now.hour

        time.sleep(INTERVAL)

if __name__ == "__main__":
    monitor_loop()
