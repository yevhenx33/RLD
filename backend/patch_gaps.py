import sqlite3
import pandas as pd
import requests
import time
from datetime import datetime

# --- CONFIGURATION ---
DB_FILE = "aave_rates.db"
RPC_URL = "https://eth-mainnet.g.alchemy.com/v2/tzRPLD8AM2sz2nMTosk2o"
POOL_ADDRESS = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
BATCH_SIZE = 50
GAP_THRESHOLD_SECONDS = 100  # Treat anything > 5 minutes as a gap to fill

# Pre-computed Data Payload (same as before)
padded_usdc = "000000000000000000000000" + USDC_ADDRESS[2:]
DATA_PAYLOAD = "0x35ea6a75" + padded_usdc

# --- HELPER FUNCTIONS ---

def get_block_by_timestamp(target_ts):
    """
    Uses binary search or an API (e.g. LlamaNodes/Etherscan) to find block number.
    For reliability and speed here, we use a rough estimate formula based on a known anchor,
    then refine or just take a safe buffer.
    
    Anchor: Block 18908800 ~= 1704067200 (Jan 1 2024)
    Avg Block Time ~= 12.05s
    """
    anchor_block = 18908800
    anchor_ts = 1704067200
    
    diff = target_ts - anchor_ts
    estimated_block = anchor_block + int(diff / 12.05)
    return estimated_block

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
        response = requests.post(RPC_URL, json=payload)
        return response.json()
    except Exception as e:
        print(f"  ⚠️ RPC Error: {e}")
        return []

# --- MAIN LOGIC ---

def run_smart_patch():
    if "YOUR_API_KEY" in RPC_URL:
        print("❌ ERROR: Please insert your Alchemy/Infura API Key in RPC_URL.")
        return

    print("🔍 Scanning database for gaps...")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Get all blocks and timestamps
    df = pd.read_sql_query("SELECT block_number, timestamp FROM rates ORDER BY block_number ASC", conn)
    
    if df.empty:
        print("Database empty. Run batched_backfill.py first.")
        return

    # Find Gaps
    df['prev_block'] = df['block_number'].shift(1)
    df['prev_ts'] = df['timestamp'].shift(1)
    df['ts_diff'] = df['timestamp'] - df['prev_ts']
    df['block_diff'] = df['block_number'] - df['prev_block']

    # Filter: Gaps where time > threshold AND block difference > 1
    # (If block diff is 1, it's just a slow block, not missing data)
    gaps = df[(df['ts_diff'] > GAP_THRESHOLD_SECONDS) & (df['block_diff'] > 1)]

    print(f"found {len(gaps)} gaps to patch.")
    
    total_inserted = 0
    
    for index, row in gaps.iterrows():
        # Define Patch Range
        start_gap_block = int(row['prev_block']) + 1
        end_gap_block = int(row['block_number'])
        
        # Determine Timestamps for display
        start_gap_time = datetime.fromtimestamp(row['prev_ts'])
        end_gap_time = datetime.fromtimestamp(row['timestamp'])
        
        print(f"\n🛠️ Patching Gap: {start_gap_time} -> {end_gap_time}")
        print(f"   Blocks: {start_gap_block} -> {end_gap_block} ({end_gap_block - start_gap_block} blocks)")

        # Iterate through this specific gap
        for batch_start in range(start_gap_block, end_gap_block, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, end_gap_block)
            
            results = fetch_batch(batch_start, batch_end)
            
            if not results:
                continue

            results.sort(key=lambda x: x['id'])
            db_data = []

            for i in range(0, len(results), 2):
                res_rate = results[i]
                res_block = results[i+1]

                if 'error' in res_rate or 'error' in res_block: 
                    continue
                
                rate_val = decode_rate(res_rate['result'])
                block_obj = res_block['result']
                
                if block_obj and rate_val is not None:
                    ts_val = int(block_obj['timestamp'], 16)
                    curr_block_num = int(block_obj['number'], 16)
                    db_data.append((curr_block_num, ts_val, rate_val))

            if db_data:
                cursor.executemany("INSERT OR IGNORE INTO rates VALUES (?, ?, ?)", db_data)
                conn.commit()
                total_inserted += len(db_data)
                print(f"   Filled blocks {batch_start}-{batch_end} | +{len(db_data)} rows", end='\r')
            
            time.sleep(0.1) # Be nice to RPC

    print(f"\n\n✅ Patch Complete! Added {total_inserted} missing data points.")
    conn.close()

if __name__ == "__main__":
    run_smart_patch()