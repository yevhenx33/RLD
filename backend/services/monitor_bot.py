
import os
import time
import requests
import json
import logging
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import threading
import sys

# Add backend directory to path for config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from dotenv import load_dotenv

# Load Env from multiple locations
load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))  # root .env
load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))  # backend/.env
load_dotenv(os.path.join(os.path.dirname(__file__), "../../contracts/.env"))  # contracts/.env
load_dotenv(os.path.join(os.path.dirname(__file__), "../../frontend/.env"))  # frontend/.env
load_dotenv(os.path.join(os.path.dirname(__file__), "../../docker/.env"))  # docker/.env (contains MAINNET_RPC_URL)

# --- CONFIG ---
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_KEY = os.getenv("API_KEY") 

DATA_FILE = "/data/chat_id.txt"

def load_chat_id():
    # 1. Try file
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                content = f.read().strip()
                if content:
                    return content
        except Exception:
            pass
            
    # 2. Try env if file missing
    return os.getenv("TELEGRAM_CHAT_ID")

def save_chat_id(new_id):
    try:
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        with open(DATA_FILE, "w") as f:
            f.write(str(new_id))
    except Exception:
        pass

CHAT_ID = load_chat_id()
PORT = os.getenv("PORT", "8080")  # Health endpoint port for this bot process
RATES_API_PORT = (
    os.getenv("RATES_API_PORT")
    or os.getenv("ENVIO_API_PORT")
    or os.getenv("ENVIO_PORT")
    or "5000"
)
RATES_API_BASE_URL = (
    os.getenv("RATES_API_BASE_URL")
    or os.getenv("RATES_API_URL")
    or os.getenv("ENVIO_API_URL")
    or os.getenv("API_URL")
    or f"http://localhost:{RATES_API_PORT}"
)
RPC_URL = os.getenv("MAINNET_RPC_URL")

# Refresh Interval for Background Checks
INTERVAL = 60

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("MonitorBot")

if not TOKEN:
    logger.critical("TELEGRAM_BOT_TOKEN not found!")
    exit(1)

# Debug: Log loaded config
logger.info(f"Loaded RATES_API_BASE_URL: {RATES_API_BASE_URL}")
logger.info(f"Loaded RPC_URL: {RPC_URL[:50] if RPC_URL else 'None'}...")

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
        res = requests.get(f"{RATES_API_BASE_URL}/healthz", headers=get_headers(), timeout=5)
        latency = (time.time() - start) * 1000
        if res.status_code == 200:
            res.json()
            # Envio health endpoint exposes lag, not canonical block number.
            last_indexed = None
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

def fetch_all_rates_graphql():
    """Fetch all rate data from Envio GraphQL and reshape for reporting."""
    query = """
    {
      historicalRates(symbols: ["USDC", "DAI", "USDT", "WETH"], resolution: "1H", limit: 96) {
        timestamp
        symbol
        apy
        price
      }
      latestRates {
        timestamp
        usdc
        dai
        usdt
        ethPrice
      }
    }
    """
    try:
        res = requests.post(
            f"{RATES_API_BASE_URL}/graphql",
            json={"query": query},
            headers=get_headers(),
            timeout=10,
        )
        if res.status_code != 200:
            logger.error(f"GraphQL HTTP {res.status_code}")
            return None
        body = res.json()
        payload = body.get("data")
        if not payload:
            return None

        # Normalize Envio shape to monitor's legacy in-memory shape.
        grouped = {"usdc": [], "dai": [], "usdt": [], "ethPrices": []}
        for row in payload.get("historicalRates", []):
            symbol = row.get("symbol")
            ts = row.get("timestamp")
            if ts is None:
                continue

            if symbol in ("USDC", "DAI", "USDT"):
                apy = row.get("apy")
                if apy is None:
                    continue
                apy_val = float(apy)
                if apy_val <= 1:
                    apy_val *= 100
                grouped[symbol.lower()].append({"timestamp": ts, "apy": apy_val})
            elif symbol == "WETH":
                price = row.get("price")
                if price is not None:
                    grouped["ethPrices"].append({"timestamp": ts, "price": float(price)})

        payload.update(grouped)

        latest = payload.get("latestRates") or {}
        for key in ("usdc", "dai", "usdt"):
            if latest.get(key) is not None:
                val = float(latest[key])
                latest[key] = val * 100 if val <= 1 else val
        payload["latestRates"] = latest

        return payload
    except Exception as e:
        logger.error(f"GraphQL fetch error: {e}")
        return None


def _extract_current_and_past(data_list, value_key="apy"):
    """From a list of {timestamp, value} dicts, return (current, past_24h)."""
    if not data_list:
        return None, None

    # Sort DESC
    data_list.sort(key=lambda x: x["timestamp"], reverse=True)
    # Filter nulls
    data_list = [d for d in data_list if d.get(value_key) is not None]
    if not data_list:
        return None, None

    current = data_list[0]
    target_ts = current["timestamp"] - 86400
    past = None
    min_diff = 3600 * 6  # 6h tolerance

    for item in data_list[1:]:
        diff = abs(item["timestamp"] - target_ts)
        if diff < min_diff:
            min_diff = diff
            past = item

    return current, past


def generate_report():
    from concurrent.futures import ThreadPoolExecutor

    # Only 2 REST calls remain: health check + RPC block number
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_health = pool.submit(check_api_health)
        f_block = pool.submit(get_latest_block)
        f_gql = pool.submit(fetch_all_rates_graphql)

    is_healthy, latency, last_indexed = f_health.result()
    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    status_emoji = "🟢" if is_healthy else "🔴"
    status_text = "Online" if is_healthy else "Offline"
    
    report = f"📊 **System Dashboard**\n🕒 `{now_str}`\n\n"
    report += f"**{status_emoji} API Status**: {status_text}\n"
    report += f"**⏱️ Response Time**: {latency}\n"
    
    # Block Lag
    if is_healthy:
        latest_block = f_block.result()
        if latest_block and last_indexed:
            lag = latest_block - last_indexed
            lag_emoji = "✅" if lag < 50 else ("⚠️" if lag < 300 else "🚨")
            report += f"**📦 Block Lag**: {lag_emoji} {lag:,} blocks\n\n"
        else:
             report += "**📦 Block Lag**: N/A\n\n"

        report += "**📉 Market Rates (24h Trend)**\n"

        gql_data = f_gql.result()

        if gql_data:
            # Extract rates from GraphQL response
            for symbol, gql_key in [("USDC", "usdc"), ("DAI", "dai"), ("USDT", "usdt")]:
                curr, past = _extract_current_and_past(gql_data.get(gql_key, []), "apy")
                if curr:
                    rate = curr.get('apy')
                    if rate is None:
                        rate = 0.0
                    change_str = " (➖ 0.00%)"
                    if past:
                        old_rate = past.get('apy')
                        if old_rate is None:
                            old_rate = 0.0
                        if old_rate > 0:
                            delta_pct = ((rate - old_rate) / old_rate) * 100
                            sign = "+" if delta_pct >= 0 else ""
                            arrow = "⬆️" if delta_pct > 0.5 else ("⬇️" if delta_pct < -0.5 else "➖")
                            change_str = f" ({arrow} {sign}{delta_pct:.2f}%)"
                        else:
                             change_str = " (➖ 0.00%)"
                    report += f"• **{symbol}**: `{rate:.2f}%`{change_str}\n"
                else:
                    report += f"• **{symbol}**: `N/A`\n"

            report += "\n"

            # ETH price: prefer latestRates (block-level), fall back to hourly
            latest = gql_data.get("latestRates")
            live_price = latest.get("ethPrice") if latest else None

            curr_eth, past_eth = _extract_current_and_past(gql_data.get("ethPrices", []), "price")

            price = live_price
            if price is None and curr_eth:
                price = curr_eth.get('price', 0.0)

            if price and price > 0:
                change_str = " (➖ 0.0%)"
                if past_eth:
                    old_price = past_eth.get('price')
                    if old_price and old_price > 0:
                        delta_pct = ((price - old_price) / old_price) * 100
                        sign = "+" if delta_pct >= 0 else ""
                        arrow = "⬆️" if delta_pct > 0.5 else ("⬇️" if delta_pct < -0.5 else "➖")
                        change_str = f" ({arrow} {sign}{delta_pct:.1f}%)"
                report += f"**💎 ETH Price**: `${price:,.2f}`{change_str}\n"
        else:
            # GraphQL failed — show N/A for all
            for symbol in ["USDC", "DAI", "USDT"]:
                report += f"• **{symbol}**: `N/A`\n"
        
        report += "\n**✅ Check**: Stable"
    else:
        error_msg = str(latency)
        if len(error_msg) > 100:
            error_msg = error_msg[:100] + "..."
        report += f"\n⚠️ **System is DOWN**\nReason: `{error_msg}`"
    
    return report

def get_dashboard_markup():
    return {"inline_keyboard": [[{"text": "🔄 Refresh", "callback_data": "refresh"}]]}

# --- HEALTH ENDPOINT ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "status": "ok",
            "service": "telegram-monitor-bot",
            "uptime": int(time.time() - _start_time),
        }).encode())

    def log_message(self, format, *args):
        pass  # Suppress request logs

_start_time = time.time()

class ThreadingHealthServer(ThreadingMixIn, HTTPServer):
    """Threaded HTTP server so health checks never block on GIL."""
    daemon_threads = True

def start_health_server(port=8080):
    server = ThreadingHealthServer(("0.0.0.0", port), HealthHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info(f"🩺 Health endpoint running on :{port}")

# --- MAIN LOOP ---
def monitor_loop():
    global CHAT_ID
    start_health_server()
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
            
            # 1. API Health
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
                            new_chat = str(chat)
                            if CHAT_ID != new_chat:
                                CHAT_ID = new_chat
                                save_chat_id(CHAT_ID) # Auto-save chat ID to file
                        
                        if text == "/start":
                            send_message(CHAT_ID, "🤖 **Monitor Bot**\nCommands:\n/status - System Health")
                        
                        elif text == "/status":
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

