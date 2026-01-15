
import sqlite3
import pandas as pd
import requests
import time
import os
import sys
import subprocess
from datetime import datetime
from dotenv import load_dotenv
import logging

# Ensure we can import config
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from config import DB_NAME, ASSETS, AAVE_POOL_ADDRESS, DB_PATH

# Logging Config
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("DataFiller")

# --- CONFIGURATION ---
load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))
DB_FILE = DB_PATH
# RPC Configuration
RPC_URLS = [
    os.getenv("MAINNET_RPC_URL"),
    os.getenv("RESERVE_RPC_URL"), # Reserve from .env
    "https://eth.llamarpc.com" # Public Fallback
]
# Filter out None/Empty
RPC_URLS = [url for url in RPC_URLS if url]

BATCH_SIZE = 10 # Reduced for more frequent commits
GAP_THRESHOLD_BLOCKS = 1

# Selector for Aave V3 getReserveData(address)
FUNC_SELECTOR = "0x35ea6a75"

def call_rpc(payload):
    """
    Attempts to call RPCs in order. Returns response json or None if all fail.
    """
    for url in RPC_URLS:
        try:
            response = requests.post(url, json=payload, timeout=20)
            if response.status_code == 200:
                parsed = response.json()
                return parsed
        except Exception as e:
            logger.warning(f"RPC {url} failed: {e}")
            continue
    return None

def get_data_payload(asset_address):
    clean_addr = asset_address[2:]
    padded = clean_addr.zfill(64)
    return FUNC_SELECTOR + padded

def decode_rate(hex_data):
    try:
        if hex_data == "0x": return None
        raw = hex_data[2:]
        # Index 4 is currentVariableBorrowRate
        start = 4 * 64 
        end = 5 * 64
        if len(raw) < end: return None
        return int(raw[start:end], 16) / 10**27 * 100
    except:
        return None

def fetch_batch_multi(start_block, end_block, assets_map):
    """
    Fetches data for MULTIPLE assets in a single batch loop.
    assets_map: { symbol: { address, payload } }
    """
    payload = []
    req_id = 0
    
    # We will map req_id back to (block_num, type, symbol)
    # type: 'rate' or 'timestamp'
    id_map = {} 

    for block_num in range(start_block, end_block):
        hex_block = hex(block_num)
        
        # 1. Fetch Rates for ALL Assets
        for symbol, data in assets_map.items():
            payload.append({
                "jsonrpc": "2.0", "method": "eth_call", "id": req_id,
                "params": [{"to": AAVE_POOL_ADDRESS, "data": data['payload']}, hex_block]
            })
            id_map[req_id] = {'block': block_num, 'type': 'rate', 'symbol': symbol}
            req_id += 1

        # 2. Get Timestamp (Once per block)
        payload.append({
            "jsonrpc": "2.0", "method": "eth_getBlockByNumber", "id": req_id,
            "params": [hex_block, False]
        })
        id_map[req_id] = {'block': block_num, 'type': 'timestamp'}
        req_id += 1

    results = call_rpc(payload)
    if not results:
        logger.error("All RPCs failed for batch.")
        return [], {}
    
    if isinstance(results, dict) and 'error' in results:
        # Single error response? Batch usually returns list of results
        logger.error(f"RPC Error: {results['error']}")
        return [], {}
        
    return results, id_map

def fill_gap_range(start_block, end_block, cursor, conn, target_assets):
    """
    target_assets: dict of symbol -> config
    """
    count = end_block - start_block
    logger.info(f"🚀 Patching {count} blocks ({start_block} -> {end_block}) for {list(target_assets.keys())}...")
    
    # Pre-compute payloads
    assets_map = {}
    for sym, cfg in target_assets.items():
        assets_map[sym] = {
            'address': cfg['address'],
            'payload': get_data_payload(cfg['address']),
            'table': cfg['table']
        }

    total_records = 0
    
    for batch_start in range(start_block, end_block, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, end_block)
        
        results, id_map = fetch_batch_multi(batch_start, batch_end, assets_map)
        if not results:
            continue

        # Sort by ID to process strictly
        if isinstance(results, list):
            results.sort(key=lambda x: x.get('id', 0))
        else:
            continue

        # Temporary storage for this batch: block -> timestamp
        block_timestamps = {}
        # Temporary storage: block -> symbol -> rate
        block_rates = {}

        for res in results:
            rid = res.get('id')
            meta = id_map.get(rid)
            if not meta: continue
            
            if 'error' in res: 
                logger.warning(f"Block {meta['block']} Error: {res['error']}")
                continue
            if 'result' not in res or not res['result']: continue

            blk = meta['block']
            
            if meta['type'] == 'timestamp':
                # Parse timestamp
                try:
                    ts = int(res['result']['timestamp'], 16)
                    block_timestamps[blk] = ts
                except: pass
            
            elif meta['type'] == 'rate':
                # Parse rate
                sym = meta['symbol']
                val = decode_rate(res['result'])
                if val is not None:
                    if blk not in block_rates: block_rates[blk] = {}
                    block_rates[blk][sym] = val
                else:
                    # Debug decoding failure occasionally?
                    if total_records == 0 and (blk % 100 == 0):
                       logger.warning(f"Decode failed for {sym} at {blk}. Raw: {res['result'][:10]}...")

        for blk in range(batch_start, batch_end):
            if blk in block_timestamps and blk in block_rates:
                ts = block_timestamps[blk]
                rates = block_rates[blk]
                
                for sym, rate in rates.items():
                    # Check if already exists? (Ignore handles it)
                    table = assets_map[sym]['table']
                    cursor.execute(f"INSERT OR IGNORE INTO {table} VALUES (?, ?, ?)", (blk, ts, rate))
                    total_records += 1
        
        conn.commit()
        
        # Periodic Logging instead of Progress Bar
        completed = batch_end - start_block
        if completed % 50 == 0 or completed == count: # Log every 50 blocks
             percent = (completed / count) * 100
             logger.info(f"Patching progress: {percent:.1f}% ({completed}/{count}) - Block {batch_end}")
        
        time.sleep(0.05) # Gentler rate limit

    logger.info(f"✅ Filled {total_records} records.")


def get_current_chain_block():
    payload = {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}
    result = call_rpc(payload)
    
    if result and 'result' in result:
        try:
            return int(result['result'], 16)
        except:
             pass
    
    print(f"❌ Error getting chain height from all RPCs.")
    return None

def run_repair_job():
    print("🛠️  [Repair] Starting Deep Repair (Last 7 Days)...")
    
    if not os.path.exists(DB_FILE):
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    current_chain = get_current_chain_block()
    if not current_chain:
        conn.close()
        return

    # 7 Days in blocks (~12s) -> 50400 blocks approximately
    BLOCKS_7D = int(7 * 24 * 3600 / 12)
    start_search_block = current_chain - BLOCKS_7D
    
    print(f"   Scanning from block {start_search_block} to {current_chain}...")

    # For each asset, find MISSING blocks
    for symbol, config in ASSETS.items():
        if config['type'] != 'onchain': continue
        
        table = config['table']
        print(f"   🔎 Scanning {symbol} ({table})...")
        
        # Get all blocks in range
        cursor.execute(f"SELECT block_number FROM {table} WHERE block_number >= ? ORDER BY block_number ASC", (start_search_block,))
        rows = cursor.fetchall()
        existing_blocks = set(r[0] for r in rows)
        
        if not existing_blocks:
            print(f"   ⚠️ No data found. Full fill needed.")
            missing_blocks = list(range(start_search_block, current_chain))
        else:
            # Find gaps
            # Optimization: create ranges
            missing_ranges = []
            
            # Check head gap
            max_existing = max(existing_blocks)
            if max_existing < current_chain:
                # Add tail range
                pass # Handled by range logic below?
                
            # Iterate expected
            # This is slow if we iterate 50k items in python? 50k is fast.
            # But making requests for 50k separate blocks is slow.
            # We want to identify RANGES.
            
            # Simple approach: Identify gaps > 1 block
            sorted_blocks = sorted(list(existing_blocks))
            
            # Check internal gaps
            ranges_to_fill = []
            
            # 1. From Start to First Existing
            if sorted_blocks[0] > start_search_block:
                ranges_to_fill.append((start_search_block, sorted_blocks[0]))
            
            # 2. Internal
            for i in range(1, len(sorted_blocks)):
                prev = sorted_blocks[i-1]
                curr = sorted_blocks[i]
                if curr - prev > 1:
                    ranges_to_fill.append((prev + 1, curr))
            
            # 3. Tail
            if sorted_blocks[-1] < current_chain:
                ranges_to_fill.append((sorted_blocks[-1] + 1, current_chain))
                
            # Execute Fill for ranges
            total_missing = sum([e - s for s, e in ranges_to_fill])
            if total_missing > 0:
                print(f"   ⚠️ Found {total_missing} missing blocks in {len(ranges_to_fill)} ranges.")
                
                target_assets = {symbol: config}
                for start, end in ranges_to_fill:
                    # Limit batch size effectively
                    # fill_gap_range handles batching
                    try:
                        fill_gap_range(start, end, cursor, conn, target_assets)
                    except Exception as e:
                        print(f"Fill error: {e}")
            else:
                print(f"   ✅ {symbol} is complete.")

    conn.close()
    
    # Trigger Sync
    print("🔄 Triggering Data Sync (Raw -> Clean)...")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sync_script = os.path.join(script_dir, "scripts", "sync_clean_db.py")
    subprocess.run(["python3", sync_script])
    
    print("✨ Repair Complete.")

if __name__ == "__main__":
    run_repair_job()
