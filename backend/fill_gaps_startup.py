import sqlite3
import pandas as pd
import requests
import time
import os
from datetime import datetime
from dotenv import load_dotenv

# --- CONFIGURATION ---
load_dotenv("../contracts/.env")
DB_FILE = "aave_rates.db"
RPC_URL = os.getenv("MAINNET_RPC_URL")

# Fallback RPC if env var is missing
if not RPC_URL:
    print("⚠️ Warning: MAINNET_RPC_URL not found, using public RPC.")
    RPC_URL = "https://eth.llamarpc.com"

# Constants
POOL_ADDRESS = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
BATCH_SIZE = 50
GAP_THRESHOLD_SECONDS = 300 # 5 minutes

# Pre-computed Data Payload for currentVariableBorrowRate
padded_usdc = "000000000000000000000000" + USDC_ADDRESS[2:]
DATA_PAYLOAD = "0x35ea6a75" + padded_usdc

def decode_rate(hex_data):
    try:
        raw = hex_data[2:]
        start = 4 * 64 # Index 4
        end = 5 * 64
        return int(raw[start:end], 16) / 10**27 * 100
    except:
        return None

def fetch_batch(start_block, end_block):
    """
    Fetches a range of blocks using JSON-RPC Batch
    """
    payload = []
    req_id = 0
    for block_num in range(start_block, end_block):
        hex_block = hex(block_num)
        # 1. Get Rate
        payload.append({
            "jsonrpc": "2.0", "method": "eth_call", "id": req_id,
            "params": [{"to": POOL_ADDRESS, "data": DATA_PAYLOAD}, hex_block]
        })
        req_id += 1
        # 2. Get Timestamp
        payload.append({
            "jsonrpc": "2.0", "method": "eth_getBlockByNumber", "id": req_id,
            "params": [hex_block, False]
        })
        req_id += 1

    try:
        response = requests.post(RPC_URL, json=payload, timeout=10)
        return response.json()
    except Exception as e:
        print(f"  ⚠️ RPC Error: {e}")
        return []

def fill_gap_range(start_block, end_block, cursor, conn):
    print(f"   Filling: Blocks {start_block} -> {end_block} ({end_block - start_block} blocks)")
    
    total_inserted = 0
    for batch_start in range(start_block, end_block, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, end_block)
        
        results = fetch_batch(batch_start, batch_end)
        if not results:
            continue

        if not results:
            continue

        # Map results by ID for safe pairing
        res_map = {r.get('id'): r for r in results}
        db_data = []

        # We know IDs range from 0 to (len(block_range)*2 - 1)
        # But here IDs are relative to the batch.
        # In fetch_batch, ids start at 0.
        # So for this batch, we expect IDs 0, 1, 2, 3 ... up to batch_size*2
        
        # Calculate expected number of pairs based on batch range loop in fetch_batch
        num_blocks = batch_end - batch_start
        
        for k in range(num_blocks):
            rate_id = k * 2
            block_id = k * 2 + 1
            
            res_rate = res_map.get(rate_id)
            res_block = res_map.get(block_id)
            
            if not res_rate or not res_block:
                continue

            if 'error' in res_rate or 'error' in res_block: 
                continue
            
            # Defensive check for result keys
            if 'result' not in res_rate or 'result' not in res_block:
                continue

            rate_val = decode_rate(res_rate['result'])
            block_obj = res_block['result']
            
            # Ensure block_obj is a dictionary (it should be for eth_getBlockByNumber)
            if not isinstance(block_obj, dict):
                continue
            
            if block_obj and rate_val is not None:
                ts_val = int(block_obj['timestamp'], 16)
                curr_block_num = int(block_obj['number'], 16)
                db_data.append((curr_block_num, ts_val, rate_val))

        if db_data:
            cursor.executemany("INSERT OR IGNORE INTO rates VALUES (?, ?, ?)", db_data)
            conn.commit()
            total_inserted += len(db_data)
            print(f"   Saved {len(db_data)} records...", end='\r')
            
        time.sleep(0.1) # Rate limit courtesy
        
    print(f"\n   ✅ Filled range with {total_inserted} records.")
    return total_inserted

def get_current_chain_block():
    try:
        payload = {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}
        response = requests.post(RPC_URL, json=payload, timeout=5)
        return int(response.json()['result'], 16)
    except Exception as e:
        print(f"❌ Error getting chain height: {e}")
        return None

def run_gap_fill():
    print("🔍 Checking Data Continuity...")
    
    if not os.path.exists(DB_FILE):
        print(f"ℹ️  DB file {DB_FILE} not found. Skipping gap check (fresh start).")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 1. Get DB State
    df = pd.read_sql_query("SELECT block_number, timestamp FROM rates ORDER BY block_number ASC", conn)
    
    if df.empty:
        print("ℹ️  Database is empty. Skipping gap check.")
        conn.close()
        return

    # 2. Check for Internal Gaps
    print("   Checking internal gaps...")
    df['prev_block'] = df['block_number'].shift(1)
    df['prev_ts'] = df['timestamp'].shift(1)
    df['ts_diff'] = df['timestamp'] - df['prev_ts']
    df['block_diff'] = df['block_number'] - df['prev_block']

    # Filter 1: Gaps > 5 mins AND > 1 block
    candidates = df[(df['ts_diff'] > GAP_THRESHOLD_SECONDS) & (df['block_diff'] > 1)]
    
    # Filter 2: Limit to last 24 hours (86400 seconds)
    # We use the 'timestamp' of the gap end to determine recency
    current_ts = time.time()
    cutoff_ts = current_ts - 86400
    
    internal_gaps = candidates[candidates['timestamp'] > cutoff_ts]
    
    total_internal_filled = 0
    if not internal_gaps.empty:
        print(f"⚠️ Found {len(internal_gaps)} INTERNAL gaps in the last 24h.")
        for index, row in internal_gaps.iterrows():
            start = int(row['prev_block']) + 1
            end = int(row['block_number'])
            total_internal_filled += fill_gap_range(start, end, cursor, conn)
    else:
        print(f"✅ No internal gaps found in the last 24h. (Ignored {len(candidates)} older gaps)")

    # 3. Check Tail Gap (Last DB Data -> Now)
    last_db_block = int(df.iloc[-1]['block_number'])
    current_chain_block = get_current_chain_block()
    
    if current_chain_block:
        # If we are more than ~5 mins behind (approx 25 blocks)
        if (current_chain_block - last_db_block) > 25:
            print(f"⚠️ DOWNTIME DETECTED: DB is at {last_db_block}, Chain is at {current_chain_block}.")
            print(f"   Lag: {current_chain_block - last_db_block} blocks.")
            fill_gap_range(last_db_block + 1, current_chain_block, cursor, conn)
        else:
            print("✅ Database is up to date (within acceptable range).")
    
    conn.close()
    print("🎉 Continuity Check Complete.\n")

if __name__ == "__main__":
    run_gap_fill()
