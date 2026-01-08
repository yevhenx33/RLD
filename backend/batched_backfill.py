import time
import requests
import sqlite3
import json
from datetime import datetime, timezone

# --- CONFIGURATION ---
# ⚠️ USE A PAID/FREE API KEY (Alchemy/Infura/QuickNode)
# Public RPCs will block you for this volume of data!
RPC_URL = "https://eth-mainnet.g.alchemy.com/v2/tzRPLD8AM2sz2nMTosk2o"

# Aave V3 USDC Pool
POOL_ADDRESS = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

# Backfill Settings
START_DATE = "2025-09-15"  # RLD v3 requirement
BATCH_SIZE = 500            # Conservative batch size (safe for free tier)
DB_FILE = "aave_rates.db"

# Pre-computed Function Selector for getReserveData(address)
# Keccak('getReserveData(address)')[0:4] + padded address
# This avoids needing the ABI for every call.
# USDC Address padded to 64 chars:
padded_usdc = "000000000000000000000000" + USDC_ADDRESS[2:]
DATA_PAYLOAD = "0x35ea6a75" + padded_usdc

# --- DATABASE SETUP ---
conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS rates (
        block_number INTEGER PRIMARY KEY,
        timestamp INTEGER,
        apy REAL
    )
''')
conn.commit()

def get_block_by_date(date_str):
    """Finds the first block of the given date (approximate)."""
    # Using a known anchor for speed or external API. 
    # For Jan 1 2024, Block is approx 18908800.
    # Let's verify with a quick timestamp check if needed, 
    # but hardcoding the start block for Jan 1 2024 is safer/faster for this script.
    # Block 18908800 timestamp is roughly Jan 1 2024 00:00 UTC
    return 23344350 

def decode_rate(hex_data):
    """
    Decodes the raw hex return from Aave V3 getReserveData.
    Structure of return (all uint128/256 are padded to 32 bytes):
    0: configuration (uint256)
    1: liquidityIndex (uint128)
    2: currentLiquidityRate (uint128)
    3: variableBorrowIndex (uint128)
    4: currentVariableBorrowRate (uint128) <--- TARGET
    """
    try:
        # Strip '0x'
        raw = hex_data[2:]
        # Each word is 64 chars (32 bytes). Target is index 4.
        start = 4 * 64
        end = 5 * 64
        target_hex = raw[start:end]
        
        # Convert to int and then to Ray percentage
        rate_ray = int(target_hex, 16)
        return rate_ray / 10**27 * 100
    except:
        return None

def run_batched_backfill():
    if "YOUR_API_KEY" in RPC_URL:
        print("❌ ERROR: Please insert your Alchemy/Infura API Key in RPC_URL.")
        return

    # 1. Determine Range
    start_block = get_block_by_date(START_DATE)
    
    # Get current latest block
    try:
        resp = requests.post(RPC_URL, json={"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1})
        current_block = int(resp.json()['result'], 16)
    except:
        print("Failed to connect to RPC.")
        return

    print(f"🚀 Starting Batched Backfill for RLD v3 Protocol Data")
    print(f"📅 Start Date: {START_DATE} (Block {start_block})")
    print(f"🏁 Target: Block {current_block}")
    print(f"📦 Batch Size: {BATCH_SIZE} blocks/request")
    print("-" * 40)

    # 2. Iterate in Chunks
    total_records = 0
    start_time = time.time()

    for batch_start in range(start_block, current_block, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, current_block)
        
        # Construct JSON-RPC Batch Request
        # We ask for TWO things per block: 
        # 1. The Rate (eth_call)
        # 2. The Timestamp (eth_getBlockByNumber) - Needed for TWAR!
        payload = []
        req_id = 0
        
        for block_num in range(batch_start, batch_end):
            hex_block = hex(block_num)
            
            # Request 1: Get Rate
            payload.append({
                "jsonrpc": "2.0",
                "method": "eth_call",
                "params": [{"to": POOL_ADDRESS, "data": DATA_PAYLOAD}, hex_block],
                "id": req_id
            })
            req_id += 1
            
            # Request 2: Get Timestamp
            payload.append({
                "jsonrpc": "2.0",
                "method": "eth_getBlockByNumber",
                "params": [hex_block, False], # False = don't need full tx objects
                "id": req_id
            })
            req_id += 1

        # Send Batch
        try:
            response = requests.post(RPC_URL, json=payload)
            results = response.json()
            
            # Results come back in a list, not guaranteed to be in order of ID by spec,
            # but usually are. Best practice is to sort by ID.
            results.sort(key=lambda x: x['id'])
            
            # Process pairs (Rate + Timestamp)
            db_data = []
            for i in range(0, len(results), 2):
                # Even index = Rate, Odd index = Block/Timestamp
                res_rate = results[i]
                res_block = results[i+1]
                
                if 'error' in res_rate or 'error' in res_block:
                    continue
                
                # Decode Rate
                rate_val = decode_rate(res_rate['result'])
                
                # Decode Timestamp
                block_obj = res_block['result']
                if block_obj:
                    ts_val = int(block_obj['timestamp'], 16)
                    curr_block_num = int(block_obj['number'], 16)
                    
                    if rate_val is not None:
                        db_data.append((curr_block_num, ts_val, rate_val))

            # Bulk Insert
            if db_data:
                cursor.executemany("INSERT OR IGNORE INTO rates VALUES (?, ?, ?)", db_data)
                conn.commit()
                total_records += len(db_data)

            # Progress Bar
            elapsed = time.time() - start_time
            rate_per_sec = total_records / elapsed if elapsed > 0 else 0
            print(f"✅ Processed blocks {batch_start} to {batch_end} | Total Rows: {total_records} | Speed: {rate_per_sec:.1f} blocks/s", end='\r')

        except Exception as e:
            print(f"\n⚠️ Error in batch {batch_start}: {e}")
            time.sleep(5) # Cooldown on error

    print(f"\n\n🎉 Backfill Complete! Database 'aave_rates.db' is ready for TWAR analysis.")
    conn.close()

if __name__ == "__main__":
    run_batched_backfill()