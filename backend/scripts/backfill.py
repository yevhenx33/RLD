import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

import time
import sqlite3
import os
import sys
from web3 import Web3
from dotenv import load_dotenv

# --- CONFIGURATION ---
load_dotenv("../contracts/.env")
RPC_URL = os.getenv("MAINNET_RPC_URL")
if not RPC_URL:
    print("Warning: MAINNET_RPC_URL not found in .env, using public RPC")
    RPC_URL = "https://eth.llamarpc.com"

# Aave V3 Pool & USDC
POOL_ADDRESS = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

# 365 Days * 24 Hours * 300 blocks/hr = ~2,628,000 blocks
# We will fetch 1 data point every ~1 hour (300 blocks)
import bisect

# ... (Configuration)
BLOCKS_PER_HOUR = 300
# Aave V3 Ethereum Pool Deployment Block (approx Jan 2023)
MIN_BLOCK = 16475699

# Backfill covers back to deployment (approx 3 years)
HOURS_TO_BACKFILL = int(24 * 365 * 3.5) 

# --- SETUP ---
w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    print("CRITICAL: Could not connect to Ethereum node.")
    sys.exit(1)

conn = sqlite3.connect('aave_rates.db')
cursor = conn.cursor()

# Ensure table exists
cursor.execute('''
    CREATE TABLE IF NOT EXISTS rates (
        block_number INTEGER,
        timestamp INTEGER,
        apy REAL
    )
''')

# Minimal ABI for getReserveData
POOL_ABI = [{
    "inputs": [{"internalType": "address", "name": "asset", "type": "address"}],
    "name": "getReserveData",
    "outputs": [
        {"internalType": "uint256", "name": "configuration", "type": "uint256"},
        {"internalType": "uint128", "name": "liquidityIndex", "type": "uint128"},
        {"internalType": "uint128", "name": "currentLiquidityRate", "type": "uint128"},
        {"internalType": "uint128", "name": "variableBorrowIndex", "type": "uint128"},
        {"internalType": "uint128", "name": "currentVariableBorrowRate", "type": "uint128"},
        {"internalType": "uint128", "name": "currentStableBorrowRate", "type": "uint128"},
        {"internalType": "uint40", "name": "lastUpdateTimestamp", "type": "uint40"},
        {"internalType": "uint16", "name": "id", "type": "uint16"},
        {"internalType": "address", "name": "aTokenAddress", "type": "address"},
        {"internalType": "address", "name": "stableDebtTokenAddress", "type": "address"},
        {"internalType": "address", "name": "variableDebtTokenAddress", "type": "address"},
        {"internalType": "address", "name": "interestRateStrategyAddress", "type": "address"},
        {"internalType": "uint128", "name": "accruedToTreasury", "type": "uint128"},
        {"internalType": "uint128", "name": "unbacked", "type": "uint128"},
        {"internalType": "uint128", "name": "isolationModeTotalDebt", "type": "uint128"}
    ],
    "stateMutability": "view",
    "type": "function"
}]

pool_contract = w3.eth.contract(address=POOL_ADDRESS, abi=POOL_ABI)

def fetch_data_for_block(block_num):
    try:
        # Get timestamp
        block = w3.eth.get_block(block_num)
        ts = block['timestamp']
        
        # Get Rate
        reserve_data = pool_contract.functions.getReserveData(USDC_ADDRESS).call(block_identifier=block_num)
        raw_rate = reserve_data[4] # currentVariableBorrowRate
        apy = raw_rate / 10**27 * 100
        
        return (block_num, ts, apy)
    except Exception as e:
        # print(f"Error fetching block {block_num}: {e}")
        return None

def main():
    print(f"🚀 Starting Deep Backfill ({HOURS_TO_BACKFILL} hours)...")
    
    current_block = w3.eth.get_block('latest')['number']
    
    # Calculate start block but respect deployment block
    target_start = current_block - (BLOCKS_PER_HOUR * HOURS_TO_BACKFILL)
    start_block = max(target_start, MIN_BLOCK)
    
    print(f"Start Block: {start_block} (Deployment limit: {MIN_BLOCK})")
    
    candidate_blocks = list(range(start_block, current_block, BLOCKS_PER_HOUR))
    print(f"Target Grid: {len(candidate_blocks)} points.")
    
    # --- SMART GAP DETECTION ---
    print("Checking existing DB data to skip duplicates...")
    cursor.execute("SELECT block_number FROM rates ORDER BY block_number")
    existing_blocks = [r[0] for r in cursor.fetchall()]
    
    blocks_to_fetch = []
    
    if not existing_blocks:
        blocks_to_fetch = candidate_blocks
    else:
        # For each candidate, check if we have a block within +/- 150 blocks (approx 30 mins)
        TOLERANCE = 150
        for b in candidate_blocks:
            # Find insertion point
            idx = bisect.bisect_left(existing_blocks, b)
            
            # Check neighbors (idx-1 and idx)
            match_found = False
            
            # Check left neighbor
            if idx > 0:
                if abs(existing_blocks[idx-1] - b) <= TOLERANCE:
                    match_found = True
            
            # Check right neighbor (if match not found yet)
            if not match_found and idx < len(existing_blocks):
                if abs(existing_blocks[idx] - b) <= TOLERANCE:
                    match_found = True
            
            if not match_found:
                blocks_to_fetch.append(b)
                
    total = len(blocks_to_fetch)
    print(f"📉 Optimization: Found {len(candidate_blocks) - total} existing points. fetching {total} missing points.")
    
    if total == 0:
        print("✅ No gaps found. Data is complete!")
        conn.close()
        return

    print(f"Fetching {total} data points in parallel (Max Workers: 20)...")
    results = []
    
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    with ThreadPoolExecutor(max_workers=20) as executor:
        future_to_block = {executor.submit(fetch_data_for_block, b): b for b in blocks_to_fetch}
        
        completed = 0
        for future in as_completed(future_to_block):
            data = future.result()
            if data:
                results.append(data)
            
            completed += 1
            if completed % 100 == 0 or completed == total:
                print(f"Progress: {completed}/{total} ({(completed/total)*100:.1f}%)")

    print("Sorting and Inserting into DB...")
    results.sort(key=lambda x: x[1]) # Sort by timestamp
    
    cursor.executemany("INSERT INTO rates VALUES (?, ?, ?)", results)
    conn.commit()
    conn.close()
    
    print(f"✅ Backfill Complete. Inserted {len(results)} data points.")

if __name__ == "__main__":
    main()