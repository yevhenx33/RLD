import sqlite3
import pandas as pd
import requests
import time
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

# Ensure we can import config
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from config import DB_NAME, ASSETS, AAVE_POOL_ADDRESS

# --- CONFIGURATION ---
load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))
DB_FILE = os.path.join(os.path.dirname(__file__), DB_NAME)
RPC_URL = os.getenv("MAINNET_RPC_URL")

# Fallback RPC if env var is missing
if not RPC_URL:
    print("⚠️ Warning: MAINNET_RPC_URL not found, using public RPC.")
    RPC_URL = "https://eth.llamarpc.com"

BATCH_SIZE = 50
GAP_THRESHOLD_BLOCKS = 50 # Only fill if gap > 50 blocks (~10 mins)

# Selector for Aave V3 getReserveData(address)
# 0x35ea6a75
FUNC_SELECTOR = "0x35ea6a75"

def get_data_payload(asset_address):
    # Padding address to 32 bytes (64 chars)
    # Asset address is 40 chars (20 bytes) + '0x'
    clean_addr = asset_address[2:]
    padded = clean_addr.zfill(64)
    return FUNC_SELECTOR + padded

def decode_rate(hex_data):
    try:
        raw = hex_data[2:]
        # Index 4 is currentVariableBorrowRate
        start = 4 * 64 
        end = 5 * 64
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

    try:
        response = requests.post(RPC_URL, json=payload, timeout=20)
        results = response.json()
        if isinstance(results, dict) and 'error' in results:
            print(f"RPC Error: {results['error']}")
            return [], {}
        return results, id_map
    except Exception as e:
        print(f"  ⚠️ RPC Connection Error: {e}")
        return [], {}

def fill_gap_range(start_block, end_block, cursor, conn, target_assets):
    """
    target_assets: dict of symbol -> config
    """
    count = end_block - start_block
    print(f"   🚀 Patching {count} blocks ({start_block} -> {end_block}) for {list(target_assets.keys())}...")
    
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
            
            if 'error' in res: continue
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

        # Write to DB
        for blk in range(batch_start, batch_end):
            if blk in block_timestamps and blk in block_rates:
                ts = block_timestamps[blk]
                rates = block_rates[blk]
                
                for sym, rate in rates.items():
                    table = assets_map[sym]['table']
                    cursor.execute(f"INSERT OR IGNORE INTO {table} VALUES (?, ?, ?)", (blk, ts, rate))
                    total_records += 1
        
        conn.commit()
        print(f"   Filled to block {batch_end}...", end='\r')
        time.sleep(0.1)

    print(f"\n   ✅ Complete. {total_records} records added.")

def get_current_chain_block():
    try:
        payload = {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}
        response = requests.post(RPC_URL, json=payload, timeout=5)
        return int(response.json()['result'], 16)
    except Exception as e:
        print(f"❌ Error getting chain height: {e}")
        return None

def run_startup_check():
    print("🔍 [Startup] Checking Data Continuity...")
    
    if not os.path.exists(DB_FILE):
        print(f"ℹ️  DB {DB_FILE} missing. Skipping.")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    current_chain = get_current_chain_block()
    if not current_chain:
        print("⚠️ Could not reach RPC. Skipping gap fill.")
        conn.close()
        return

    print(f"⛓️  Chain Tip: {current_chain}")

    # Check each asset
    assets_to_fill = [] # List of (symbol, start_fill_block, config)

    # 1. Identify where each asset is
    for symbol, config in ASSETS.items():
        if config['type'] != 'onchain': continue
        
        table = config['table']
        try:
            cursor.execute(f"SELECT MAX(block_number) FROM {table}")
            res = cursor.fetchone()
            last_db_block = res[0] if res and res[0] else 0
            
            if last_db_block == 0:
                # Table empty? Maybe backfill from a recent block or skip
                # We assume init_tables ran. If empty, maybe backfill last 24h?
                # For safety, let's pick chain_tip - 7200 (1 day) if empty
                last_db_block = current_chain - 50000 # ~1 week default if empty
                print(f"   {symbol}: Empty. Defaulting to -1 week.")
            
            gap = current_chain - last_db_block
            
            if gap > GAP_THRESHOLD_BLOCKS:
                print(f"   ⚠️ {symbol}: Lagging by {gap} blocks (DB: {last_db_block})")
                assets_to_fill.append({
                    'symbol': symbol,
                    'start': last_db_block + 1,
                    'config': config
                })
            else:
                print(f"   ✅ {symbol}: Synced (Gap: {gap})")
                
        except Exception as e:
            print(f"   Error checking {symbol}: {e}")

    # 2. Group by start block to optimize? 
    # Actually, simpler to just run fill for each, or merge ranges.
    # Merging ranges is complex if they differ wildly. 
    # Let's process them individually or grouped if starts are close.
    # For now, let's process individually for safety, OR group globally if they are all close.
    # In "startup" scenario, likely all stopped at same time.
    
    if not assets_to_fill:
        print("🎉 All assets up to date.")
        conn.close()
        return

    # Determine global start (min of all starts) to batch efficiently?
    # No, effectively we want to batch fetch.
    # If USDC needs 1000 blocks and DAI needs 1000 blocks, fetch together.
    # Let's simple approach: Group everything into one massive "Fill Target" from MIN(start) to CHAIN_TIP.
    # And only insert for assets that need it?
    # Better: just use the fill_gap_range function with a dict of assets.
    
    # We find the MIN start block
    min_start = min(a['start'] for a in assets_to_fill)
    
    # Filter map for the filler
    target_assets_map = {a['symbol']: a['config'] for a in assets_to_fill}
    
    # Note: This will fetch data for ALL target assets from min_start. 
    # If DAI was synced but USDC wasn't, we shouldn't fetch DAI.
    # But current implementation of fetch_batch_multi fetches ALL in map.
    # So we should validly pass the map.
    # Optimize: If DAI start is >> min_start, we waste calls.
    # But usually they are synced. 
    # Let's trust they are close.
    
    try:
        fill_gap_range(min_start, current_chain, cursor, conn, target_assets_map)
    except KeyboardInterrupt:
        print("\n🛑 Interrupted.")
    except Exception as e:
        print(f"\n❌ Error during fill: {e}")

    conn.close()

if __name__ == "__main__":
    run_startup_check()
