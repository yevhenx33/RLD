#!/usr/bin/env python3
"""
Continuous Rate Indexer Daemon.

Runs continuously to:
1. On startup: repair gaps from the last 7 days
2. Continuously: index new blocks every ~12 seconds
3. After each batch: sync to clean_rates.db

Usage:
    python3 rate_indexer_daemon.py

Environment:
    MAINNET_RPC_URL - Primary Ethereum RPC
    RESERVE_RPC_URL - Backup RPC (optional)
"""

import sqlite3
import requests
import time
import os
import sys
import subprocess
from datetime import datetime
from dotenv import load_dotenv
import logging
import signal

# Ensure we can import config
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from config import DB_NAME, ASSETS, AAVE_POOL_ADDRESS, DB_PATH, CLEAN_DB_PATH

# Logging Config
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("RateIndexerDaemon")

# --- CONFIGURATION ---
# Load from multiple .env locations
load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))  # backend/.env
load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))  # root .env
load_dotenv(os.path.join(os.path.dirname(__file__), "../../contracts/.env"))  # contracts/.env
load_dotenv(os.path.join(os.path.dirname(__file__), "../../frontend/.env"))  # frontend/.env (has ETH_PRICE_GRAPH_URL)
DB_FILE = DB_PATH
CLEAN_DB_FILE = CLEAN_DB_PATH

# RPC Configuration
RPC_URLS = [
    os.getenv("MAINNET_RPC_URL"),
    os.getenv("RESERVE_RPC_URL"),
    "https://eth.llamarpc.com"  # Public Fallback
]
RPC_URLS = [url for url in RPC_URLS if url]

# Daemon settings
POLL_INTERVAL = 12  # seconds (1 Ethereum block)
BATCH_SIZE = 50     # Blocks per RPC batch
SYNC_INTERVAL = 60  # Full hourly aggregation every 60s (incremental, lightweight)
BLOCKS_7D = int(7 * 24 * 3600 / 12)  # ~50400 blocks
SOFR_SYNC_INTERVAL = 86400  # Sync SOFR once per day
SOFR_GENESIS = "2023-03-01"  # Backfill start date
SOFR_API_URL = "https://markets.newyorkfed.org/api/rates/secured/sofr/search.json"

# Read ETH_PRICE_GRAPH_URL after all envs loaded (config.py loads too early)
ETH_PRICE_GRAPH_URL_LOCAL = os.getenv("ETH_PRICE_GRAPH_URL")

# Aave V3 getReserveData selector
FUNC_SELECTOR = "0x35ea6a75"

# Graceful shutdown
running = True

def signal_handler(signum, frame):
    global running
    logger.info("🛑 Received shutdown signal. Exiting gracefully...")
    running = False

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def call_rpc(payload):
    """Attempts to call RPCs in order. Returns response json or None if all fail."""
    for url in RPC_URLS:
        try:
            response = requests.post(url, json=payload, timeout=20)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.warning(f"RPC {url} failed: {e}")
            continue
    return None


def get_current_chain_block():
    """Get the current block number from the chain."""
    payload = {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}
    result = call_rpc(payload)
    if result and 'result' in result:
        try:
            return int(result['result'], 16)
        except:
            pass
    return None


def get_last_indexed_block(cursor, table="rates"):
    """Get the last indexed block from the database."""
    try:
        cursor.execute(f"SELECT MAX(block_number) FROM {table}")
        result = cursor.fetchone()
        return result[0] if result and result[0] else 0
    except:
        return 0


def get_data_payload(asset_address):
    """Build eth_call payload for getReserveData."""
    clean_addr = asset_address[2:]
    padded = clean_addr.zfill(64)
    return FUNC_SELECTOR + padded


def decode_rate(hex_data):
    """Decode currentVariableBorrowRate from getReserveData response."""
    try:
        if hex_data == "0x":
            return None
        raw = hex_data[2:]
        # Index 4 is currentVariableBorrowRate (5th field, 0-indexed)
        start = 4 * 64
        end = 5 * 64
        if len(raw) < end:
            return None
        return int(raw[start:end], 16) / 10**27 * 100
    except:
        return None


def index_block_range(start_block, end_block, cursor, conn):
    """
    Index a range of blocks for all onchain assets.
    """
    if start_block >= end_block:
        return 0

    count = end_block - start_block
    logger.info(f"📡 Indexing blocks {start_block} -> {end_block} ({count} blocks)")

    # Build asset payloads
    assets_map = {}
    for sym, cfg in ASSETS.items():
        if cfg['type'] != 'onchain':
            continue
        assets_map[sym] = {
            'address': cfg['address'],
            'payload': get_data_payload(cfg['address']),
            'table': cfg['table']
        }

    if not assets_map:
        logger.warning("No onchain assets configured")
        return 0

    # Ensure eth_prices table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS eth_prices (
            timestamp INTEGER PRIMARY KEY,
            price REAL,
            block_number INTEGER
        )
    """)

    total_records = 0

    for batch_start in range(start_block, end_block, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, end_block)

        # Build batch RPC request
        payload = []
        id_map = {}
        req_id = 0

        for block_num in range(batch_start, batch_end):
            hex_block = hex(block_num)

            # Rate calls for all assets
            for symbol, data in assets_map.items():
                payload.append({
                    "jsonrpc": "2.0", "method": "eth_call", "id": req_id,
                    "params": [{"to": AAVE_POOL_ADDRESS, "data": data['payload']}, hex_block]
                })
                id_map[req_id] = {'block': block_num, 'type': 'rate', 'symbol': symbol}
                req_id += 1

            # ETH price from Uniswap V3 slot0
            payload.append({
                "jsonrpc": "2.0", "method": "eth_call", "id": req_id,
                "params": [{"to": ETH_POOL_ADDRESS, "data": SLOT0_SELECTOR}, hex_block]
            })
            id_map[req_id] = {'block': block_num, 'type': 'eth_price'}
            req_id += 1

            # Timestamp for block
            payload.append({
                "jsonrpc": "2.0", "method": "eth_getBlockByNumber", "id": req_id,
                "params": [hex_block, False]
            })
            id_map[req_id] = {'block': block_num, 'type': 'timestamp'}
            req_id += 1

        results = call_rpc(payload)
        if not results or not isinstance(results, list):
            continue

        # Parse results
        block_timestamps = {}
        block_rates = {}
        block_eth_prices = {}

        for res in results:
            rid = res.get('id')
            meta = id_map.get(rid)
            if not meta:
                continue
            if 'error' in res or 'result' not in res or not res['result']:
                continue

            blk = meta['block']

            if meta['type'] == 'timestamp':
                try:
                    ts = int(res['result']['timestamp'], 16)
                    block_timestamps[blk] = ts
                except:
                    pass
            elif meta['type'] == 'rate':
                sym = meta['symbol']
                val = decode_rate(res['result'])
                if val is not None:
                    if blk not in block_rates:
                        block_rates[blk] = {}
                    block_rates[blk][sym] = val
            elif meta['type'] == 'eth_price':
                try:
                    raw = res['result'][2:]
                    sqrtPriceX96 = int(raw[:64], 16)
                    if sqrtPriceX96 > 0:
                        price_raw = (sqrtPriceX96 ** 2) / Q192
                        eth_price = DECIMAL_ADJUST / price_raw
                        if 100 < eth_price < 100000:  # Sanity check
                            block_eth_prices[blk] = eth_price
                except:
                    pass

        # Insert into DB
        for blk in range(batch_start, batch_end):
            if blk in block_timestamps:
                ts = block_timestamps[blk]

                # Insert rates
                if blk in block_rates:
                    rates = block_rates[blk]
                    for sym, rate in rates.items():
                        table = assets_map[sym]['table']
                        cursor.execute(
                            f"INSERT OR IGNORE INTO {table} VALUES (?, ?, ?)",
                            (blk, ts, rate)
                        )
                        total_records += 1

                # Insert ETH price
                if blk in block_eth_prices:
                    cursor.execute(
                        "INSERT OR REPLACE INTO eth_prices (timestamp, price, block_number) VALUES (?, ?, ?)",
                        (ts, block_eth_prices[blk], blk)
                    )
                    total_records += 1

        conn.commit()
        time.sleep(0.05)  # Rate limit

    logger.info(f"✅ Indexed {total_records} records")
    return total_records


def update_sync_state(latest_block):
    """Directly update the last_block_number in clean_rates.db sync_state (instant)."""
    try:
        conn_clean = sqlite3.connect(CLEAN_DB_FILE)
        cur = conn_clean.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        cur.execute(
            "INSERT OR REPLACE INTO sync_state (key, value) VALUES ('last_block_number', ?)",
            (str(latest_block),)
        )
        conn_clean.commit()
        conn_clean.close()
    except Exception as e:
        logger.error(f"sync_state update error: {e}")


def sync_clean_db():
    """Trigger full hourly aggregation sync to clean_rates.db."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sync_script = os.path.join(script_dir, "sync_clean_db.py")
    
    if os.path.exists(sync_script):
        try:
            subprocess.run([sys.executable, sync_script], capture_output=True, timeout=120)
            logger.info("🔄 Full hourly sync to clean_rates.db")
        except Exception as e:
            logger.error(f"Sync error: {e}")


# ETH Price Configuration
ETH_POOL_ADDRESS = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"  # Uniswap V3 USDC/ETH 0.05%
SLOT0_SELECTOR = "0x3850c7bd"  # slot0() function selector
Q192 = 2 ** 192  # Precomputed constant for price conversion
DECIMAL_ADJUST = 10 ** 12  # 10^(WETH_dec - USDC_dec) = 10^(18-6)


def sync_eth_prices(conn):
    """Fetch ETH prices from The Graph and sync to database."""
    if not ETH_PRICE_GRAPH_URL_LOCAL:
        logger.warning("ETH_PRICE_GRAPH_URL not configured, skipping ETH price sync")
        return 0
    
    cursor = conn.cursor()
    
    # Ensure table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS eth_prices (
            timestamp INTEGER PRIMARY KEY,
            price REAL,
            block_number INTEGER
        )
    """)
    
    # Get last timestamp
    cursor.execute("SELECT MAX(timestamp) FROM eth_prices")
    result = cursor.fetchone()
    last_ts = result[0] if result and result[0] else 0
    
    # GraphQL query for hourly pool data
    query = """
    {
      poolHourDatas(
        orderBy: periodStartUnix
        orderDirection: asc
        where: {
            pool: "%s", 
            periodStartUnix_gt: %d
        }
        first: 100
      ) {
        periodStartUnix
        token0Price
      }
    }
    """ % (ETH_POOL_ADDRESS, last_ts)
    
    try:
        response = requests.post(
            ETH_PRICE_GRAPH_URL_LOCAL,
            json={'query': query},
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        
        if 'errors' in data:
            logger.error(f"GraphQL error: {data['errors']}")
            return 0
        
        items = data.get('data', {}).get('poolHourDatas', [])
        
        if not items:
            logger.debug("ETH prices up to date")
            return 0
        
        count = 0
        for item in items:
            ts = int(item['periodStartUnix'])
            price = float(item['token0Price'])
            cursor.execute(
                "INSERT OR REPLACE INTO eth_prices (timestamp, price) VALUES (?, ?)",
                (ts, price)
            )
            count += 1
        
        conn.commit()
        logger.info(f"💰 Synced {count} ETH prices")
        return count
        
    except Exception as e:
        logger.error(f"ETH price sync error: {e}")
        return 0


def sync_sofr_rates(conn):
    """Fetch daily SOFR rates from the NY Fed API and insert into sofr_rates."""
    cursor = conn.cursor()

    # Ensure table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sofr_rates (
            timestamp INTEGER PRIMARY KEY,
            apy REAL
        )
    """)

    # Get last SOFR timestamp
    cursor.execute("SELECT MAX(timestamp) FROM sofr_rates")
    result = cursor.fetchone()
    last_ts = result[0] if result and result[0] else 0

    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        start = SOFR_GENESIS if last_ts == 0 else datetime.utcfromtimestamp(last_ts).strftime("%Y-%m-%d")
        url = f"{SOFR_API_URL}?startDate={start}&endDate={today}"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()

        rates = data.get("refRates", [])
        if not rates:
            logger.debug("No SOFR data returned from NY Fed API")
            return 0

        count = 0
        for item in rates:
            # Parse date -> midnight UTC timestamp
            date_str = item.get("effectiveDate")
            rate = item.get("percentRate")
            if not date_str or rate is None:
                continue

            dt = datetime.strptime(date_str, "%Y-%m-%d")
            ts = int(dt.timestamp())

            if ts > last_ts:
                cursor.execute(
                    "INSERT OR IGNORE INTO sofr_rates (timestamp, apy) VALUES (?, ?)",
                    (ts, float(rate))
                )
                count += 1

        conn.commit()
        if count:
            logger.info(f"📊 Synced {count} SOFR rates from NY Fed")
        return count

    except Exception as e:
        logger.error(f"SOFR sync error: {e}")
        return 0


def run_initial_repair(cursor, conn):
    """Repair gaps from the last 7 days on startup."""
    logger.info("🛠️  Running initial 7-day gap repair...")
    
    current_block = get_current_chain_block()
    if not current_block:
        logger.error("Could not get current block")
        return

    start_block = current_block - BLOCKS_7D

    for symbol, config in ASSETS.items():
        if config['type'] != 'onchain':
            continue

        table = config['table']
        logger.info(f"   Checking {symbol} ({table})...")

        # Get existing blocks
        cursor.execute(
            f"SELECT block_number FROM {table} WHERE block_number >= ? ORDER BY block_number ASC",
            (start_block,)
        )
        rows = cursor.fetchall()
        existing_blocks = set(r[0] for r in rows)

        if not existing_blocks:
            # No data - full fill needed
            logger.info(f"   ⚠️ No data for {symbol}. Filling from block {start_block}...")
            assets_to_fill = {symbol: config}
            index_block_range(start_block, current_block, cursor, conn)
        else:
            # Find gaps
            sorted_blocks = sorted(list(existing_blocks))
            ranges_to_fill = []

            # Gap before first existing
            if sorted_blocks[0] > start_block:
                ranges_to_fill.append((start_block, sorted_blocks[0]))

            # Internal gaps
            for i in range(1, len(sorted_blocks)):
                prev = sorted_blocks[i-1]
                curr = sorted_blocks[i]
                if curr - prev > 1:
                    ranges_to_fill.append((prev + 1, curr))

            # Gap after last existing
            if sorted_blocks[-1] < current_block:
                ranges_to_fill.append((sorted_blocks[-1] + 1, current_block))

            total_missing = sum([e - s for s, e in ranges_to_fill])
            if total_missing > 0:
                logger.info(f"   ⚠️ {symbol}: {total_missing} missing blocks in {len(ranges_to_fill)} ranges")
                for start, end in ranges_to_fill:
                    index_block_range(start, end, cursor, conn)
            else:
                logger.info(f"   ✅ {symbol} is complete")

    logger.info("🛠️  Initial repair complete")


def run_daemon():
    """Main daemon loop."""
    logger.info("=" * 60)
    logger.info("🚀 Rate Indexer Daemon Started")
    logger.info(f"   DB Path: {DB_FILE}")
    logger.info(f"   Poll Interval: {POLL_INTERVAL}s")
    logger.info(f"   RPCs: {len(RPC_URLS)} configured")
    logger.info("=" * 60)

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Ensure tables exist
    for symbol, config in ASSETS.items():
        if config['type'] != 'onchain':
            continue
        table = config['table']
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                block_number INTEGER PRIMARY KEY,
                timestamp INTEGER,
                apy REAL
            )
        """)
    conn.commit()

    # Initial repair
    run_initial_repair(cursor, conn)
    sync_eth_prices(conn)  # Sync ETH prices on startup
    sync_sofr_rates(conn)  # Backfill SOFR from NY Fed on startup
    sync_clean_db()

    last_sync_time = time.time()
    last_sofr_sync = time.time()

    # Continuous loop
    while running:
        try:
            current_block = get_current_chain_block()
            if not current_block:
                logger.warning("Could not get current block. Retrying...")
                time.sleep(POLL_INTERVAL)
                continue

            last_indexed = get_last_indexed_block(cursor)

            if last_indexed < current_block:
                # Index new blocks
                records = index_block_range(last_indexed + 1, current_block + 1, cursor, conn)

                # Update sync_state immediately (instant - keeps block lag near zero)
                update_sync_state(current_block)

                # Full hourly aggregation periodically 
                if time.time() - last_sync_time > SYNC_INTERVAL:
                    sync_clean_db()
                    last_sync_time = time.time()

                # Daily SOFR sync
                if time.time() - last_sofr_sync > SOFR_SYNC_INTERVAL:
                    sync_sofr_rates(conn)
                    last_sofr_sync = time.time()
            else:
                logger.debug(f"Up to date at block {current_block}")

            time.sleep(POLL_INTERVAL)

        except Exception as e:
            logger.error(f"Daemon error: {e}")
            time.sleep(POLL_INTERVAL)

    conn.close()
    logger.info("👋 Daemon stopped")


if __name__ == "__main__":
    run_daemon()
