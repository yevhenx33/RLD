
import os
import time
import requests
import json
import logging
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
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
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API_KEY = os.getenv("API_KEY") 
PORT = os.getenv("PORT", "8080")  # Default to 8080 for local testing
API_URL = os.getenv("API_URL", f"http://localhost:{PORT}")  # Allow override via env
RATES_API_URL = os.getenv("RATES_API_URL", "http://localhost:8081")  # Rates Indexer
SIM_API_URL = os.getenv("SIM_API_URL", "http://host.docker.internal:8080") # Simulation Indexer
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
logger.info(f"Loaded API_URL: {API_URL}")
logger.info(f"Loaded RATES_API_URL: {RATES_API_URL}")
logger.info(f"Loaded SIM_API_URL: {SIM_API_URL}")
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
        res = requests.get(f"{RATES_API_URL}/", headers=get_headers(), timeout=5)
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
        # Use RATES_API_URL for rates/prices
        base_url = RATES_API_URL
        
        url = f"{base_url}{endpoint}?symbol={symbol}&limit=48&resolution=1H"
        if endpoint == "/eth-prices":
            url = f"{base_url}{endpoint}?limit=48&resolution=1H"
            
        res = requests.get(url, headers=get_headers(), timeout=10)
        if res.status_code != 200:
            return None, None
            
        data = res.json()
        if not data:
            return None, None

        # Sort DESC (Newest First) to ensure data[0] is current
        data.sort(key=lambda x: x['timestamp'], reverse=True)

        # Filter out entries with null values (incomplete hourly buckets)
        value_key = 'price' if endpoint == "/eth-prices" else 'apy'
        data = [d for d in data if d.get(value_key) is not None]
        if not data:
            return None, None

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

def get_sim_status():
    try:
        # Fetch latest market state
        res = requests.get(f"{SIM_API_URL}/api/latest", timeout=5)
        if res.status_code != 200:
            return None
        
        data = res.json()
        
        # API returns lists, grab first item
        ms_list = data.get('market_states', [])
        ps_list = data.get('pool_states', [])
        
        ms = ms_list[0] if ms_list else {}
        ps = ps_list[0] if ps_list else {}
        
        # Calculate key metrics (cast strings to float)
        index_price = float(ms.get('index_price', 0)) / 1e18
        mark_price = float(ps.get('mark_price', 0))
        
        # PEG Spread (bps)
        spread_bps = 0.0
        if index_price > 0:
            spread_bps = ((mark_price - index_price) / index_price) * 10000
            
        return {
            "block": ms.get('block_number'),
            "index_price": index_price,
            "mark_price": mark_price,
            "spread_bps": spread_bps,
            "total_debt": float(ms.get('total_debt', 0)) / 1e6, # USDC has 6 decimals
            "norm_factor": float(ms.get('normalization_factor', 0)) / 1e18,
            "liquidity": int(ps.get('liquidity', 0)),
            "tick": int(ps.get('tick', 0))
        }
    except Exception as e:
        logger.error(f"Sim API Error: {e}")
        return None

def generate_sim_report():
    data = get_sim_status()
    if not data:
        return "⚠️ **Simulation API Unreachable**"
        
    s = data['spread_bps']
    peg_emoji = "✅" if abs(s) < 50 else ("⚠️" if abs(s) < 200 else "🚨")
    
    report = f"🧪 **Simulation Snapshot**\n"
    report += f"📦 Block: `{data['block']}`\n\n"
    
    report += f"**🎯 PEG Status** {peg_emoji}\n"
    report += f"• Index: `${data['index_price']:.4f}`\n"
    report += f"• Mark: `${data['mark_price']:.4f}`\n"
    report += f"• Spread: `{s:+.2f} bps`\n\n"
    
    report += f"**🏦 Protocol State**\n"
    report += f"• Debt: `${data['total_debt']:,.2f}`\n"
    report += f"• Norm Factor: `{data['norm_factor']:.6f}`\n"
    
    report += f"\n**🏊 Pool State**\n"
    report += f"• Liquidity: `{data['liquidity']:,}`\n"
    report += f"• Tick: `{data['tick']}`"
    
    return report

# --- BACKGROUND MONITORING ---
last_peg_alert = 0
PEG_ALERT_COOLDOWN = 3600  # 1 hour
PEG_THRESHOLD_BPS = 100    # 1% deviation

def check_peg_health():
    global last_peg_alert
    try:
        status = get_sim_status()
        if not status:
            return
            
        spread = abs(status['spread_bps'])
        if spread > PEG_THRESHOLD_BPS:
            if time.time() - last_peg_alert > PEG_ALERT_COOLDOWN:
                last_peg_alert = time.time()
                direction = "📈 Premium" if status['spread_bps'] > 0 else "📉 Discount"
                msg = (f"🚨 **PEG ALERT** 🚨\n\n"
                       f"{direction}: `{status['spread_bps']:+.2f} bps`\n"
                       f"Index: `${status['index_price']:.4f}`\n"
                       f"Mark: `${status['mark_price']:.4f}`")
                if CHAT_ID:
                    send_message(CHAT_ID, msg)
    except Exception as e:
        logger.error(f"Peg Check Error: {e}")

last_event_id = 0
WHALE_THRESHOLD_USD = 10000

def get_recent_events(limit=5):
    try:
        res = requests.get(f"{SIM_API_URL}/api/events?limit={limit}", timeout=5)
        if res.status_code == 200:
            return res.json()
    except:
        pass
    return []

def check_whale_events():
    global last_event_id
    try:
        events = get_recent_events(limit=20)
        if not events:
            return
            
        # First run: just set the ID
        if last_event_id == 0:
            last_event_id = events[0]['id']
            return
            
        new_events = [e for e in events if e['id'] > last_event_id]
        if not new_events:
            return
            
        last_event_id = new_events[0]['id']
        
        for e in reversed(new_events): # Process oldest new event first
            name = e['event_name']
            data = e['event_data']
            
            # 1. Swap Alert
            if name == "Swap":
                amount0 = float(data.get('amount0', 0))
                amount1 = float(data.get('amount1', 0))
                # Approx value (USDC is token0 or token1?)
                # We need to know which is USDC. Typically amount1 is quote?
                # Let's just sum absolute values as a heuristic if we don't know price
                # Actually, we have prices. Let's assume > 10k units of either is worth alerting for now.
                # Heuristic: sum of absolute amounts / 1e18? No, USDC is 1e6.
                # Let's just alert on everything for testing, or use a high threshold.
                # Heuristic: Alert if either token > 10,000 units (assuming USDC 1e6 or WRLP 1e18)
                # 10k USDC = 10,000 * 1e6 = 1e10
                # 5k WRLP = 5,000 * 1e18 = 5e21
                
                is_whale = False
                if amount0 > 1e10 or amount1 > 1e10: 
                     is_whale = True
                     
                if is_whale:
                    msg = (f"🐋 **WHALE SWAP**\n"
                           f"Tx: `{e['tx_hash'][:10]}...`\n"
                           f"Amt0: `{amount0:,.0f}`\n"
                           f"Amt1: `{amount1:,.0f}`")
                    if CHAT_ID:
                        send_message(CHAT_ID, msg) 
                
            # 2. Liquidation Alert
            if "Liquidate" in name:
                msg = (f"☠️ **LIQUIDATION DETECTED**\n"
                       f"Tx: `{e['tx_hash'][:10]}...`")
                if CHAT_ID:
                    send_message(CHAT_ID, msg)
                    
    except Exception as e:
        logger.error(f"Whale Check Error: {e}")

def generate_whale_report():
    events = get_recent_events(limit=5)
    if not events:
        return "🌊 No recent events found."
        
    report = "🐋 **Whale Watch (Last 5)**\n\n"
    for e in events:
        name = e['event_name']
        txt = f"• **{name}** (Block {e['block_number']})\n"
        if name == "Swap":
            d = e['event_data']
            a0 = float(d.get('amount0', 0))
            a1 = float(d.get('amount1', 0))
            txt += f"  `{a0:,.2f}` / `{a1:,.2f}`\n"
        report += txt
        
    return report
def generate_report():
    from concurrent.futures import ThreadPoolExecutor

    # Fire ALL requests in parallel (was sequential: 6 x ~1-2s = 6-10s)
    with ThreadPoolExecutor(max_workers=6) as pool:
        f_health = pool.submit(check_api_health)
        f_block = pool.submit(get_latest_block)
        f_usdc = pool.submit(get_asset_stats, "USDC")
        f_dai = pool.submit(get_asset_stats, "DAI")
        f_usdt = pool.submit(get_asset_stats, "USDT")
        f_eth = pool.submit(get_asset_stats, "ETH", "/eth-prices")

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
             report += f"**📦 Block Lag**: N/A\n\n"

        report += "**📉 Market Rates (24h Trend)**\n"
        for symbol, future in [("USDC", f_usdc), ("DAI", f_dai), ("USDT", f_usdt)]:
            curr, past = future.result()
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
        curr_eth, past_eth = f_eth.result()
        if curr_eth:
            price = curr_eth.get('price')
            if price is None:
                price = 0.0
            change_str = " (➖ 0.0%)"
            if past_eth:
                old_price = past_eth.get('price')
                if old_price is None:
                    old_price = 0.0
                if old_price > 0:
                    delta_pct = ((price - old_price) / old_price) * 100
                    sign = "+" if delta_pct >= 0 else ""
                    arrow = "⬆️" if delta_pct > 0.5 else ("⬇️" if delta_pct < -0.5 else "➖")
                    change_str = f" ({arrow} {sign}{delta_pct:.1f}%)"
            report += f"**💎 ETH Price**: `${price:,.2f}`{change_str}\n"
        
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

def start_health_server(port=8080):
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
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
    
    # Initialize Whale Watch ID
    check_whale_events()

    while True:
        # A. Background Health Check & Alerts (Every 60s)
        if time.time() - last_check_time > INTERVAL:
            last_check_time = time.time()
            
            # 1. API Health
            is_healthy, reason, _ = check_api_health()
            
            # 2. Simulation Health (Peg & Whales)
            if is_healthy:
                check_peg_health()
                check_whale_events()
            
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
                        
                        if text == "/start":
                            send_message(CHAT_ID, "🤖 **Monitor Bot**\nCommands:\n/status - System Health\n/sim - Simulation Snapshot\n/whale - Whale Watch")
                        
                        elif text == "/status":
                            report = generate_report()
                            send_message(CHAT_ID, report, get_dashboard_markup())
                            
                        elif text == "/sim":
                            report = generate_sim_report()
                            send_message(CHAT_ID, report)
                            
                        elif text == "/whale":
                            report = generate_whale_report()
                            send_message(CHAT_ID, report)
                    
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

