import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

import time
import sqlite3
import os
from web3 import Web3
from dotenv import load_dotenv

# --- CONFIGURATION ---
load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))
RPC_URL = os.getenv("MAINNET_RPC_URL")
if not RPC_URL:
    print("Warning: MAINNET_RPC_URL not found in .env, using public RPC")
    RPC_URL = "https://eth.llamarpc.com"

# --- ASSETS CONFIGURATION (Targeting DAI and USDT only for backfill as USDC has history) ---
# USDC has history from previous indexer runs (or csv), but we can include it if we want full coverage.
# User asked specifically "similarly to how we collected USDC rates" implying we want DAI/USDT to catch up.
ASSETS = {
    "DAI": {
        "address": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
        "table": "rates_dai"
    },
    "USDT": {
        "address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "table": "rates_usdt"
    }
}

POOL_ADDRESS = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
BLOCKS_PER_HOUR = 300 # Approx 12s block time -> 300 blocks/hr
HOURS_TO_BACKFILL = 24 * 365 # 365 Days

# --- SETUP ---
w3 = Web3(Web3.HTTPProvider(RPC_URL))
conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), 'aave_rates.db'))
cursor = conn.cursor()

# Ensure tables exist
for symbol, data in ASSETS.items():
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS {data['table']} (
            block_number INTEGER,
            timestamp INTEGER,
            apy REAL
        )
    ''')
conn.commit()

# ABI (Same as indexer.py)
POOL_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "asset", "type": "address"}],
        "name": "getReserveData",
        "outputs": [
            {"internalType": "uint256", "name": "configuration", "type": "uint256"},
            {"internalType": "uint128", "name": "liquidityIndex", "type": "uint128"},
            {"internalType": "uint128", "name": "currentLiquidityRate", "type": "uint128"},
            {"internalType": "uint128", "name": "variableBorrowIndex", "type": "uint128"},
            {"internalType": "uint128", "name": "currentVariableBorrowRate", "type": "uint128"}, # Index 4
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
    }
]

pool_contract = w3.eth.contract(address=POOL_ADDRESS, abi=POOL_ABI)

def get_aave_rate_at_block(asset_address, block_num):
    try:
        reserve_data = pool_contract.functions.getReserveData(asset_address).call(block_identifier=block_num)
        raw_rate = reserve_data[4]
        apy = raw_rate / 10**27 * 100
        return apy
    except Exception as e:
        print(f"Error fetching data for {asset_address} at block {block_num}: {e}")
        return None

def get_min_block():
    try:
        # Check both tables for the absolute minimum block to ensure continuity
        cursor.execute("SELECT MIN(block_number) FROM rates_dai")
        row_dai = cursor.fetchone()
        min_dai = row_dai[0] if row_dai else None
        
        cursor.execute("SELECT MIN(block_number) FROM rates_usdt")
        row_usdt = cursor.fetchone()
        min_usdt = row_usdt[0] if row_usdt else None
        
        # Filter out None values (if table is empty)
        blocks = [b for b in [min_dai, min_usdt] if b is not None]
        
        if not blocks:
            return None
        return min(blocks)
    except Exception as e:
        print(f"Error fetching min block: {e}")
        return None

def run_backfill():
    if not w3.is_connected():
        print("CRITICAL: Could not connect to Ethereum node.")
        return

    # Determine Start Block
    min_db_block = get_min_block()
    
    if min_db_block:
        latest_block = min_db_block
        print(f"📉 Found existing data. Continuing backfill from Oldest Block: {latest_block}")
    else:
        latest_block = w3.eth.get_block('latest')['number']
        print(f"🆕 No data found. Starting backfill from Chain Head: {latest_block}")

    print(f"Target: Backfilling {HOURS_TO_BACKFILL} hours relative to start block...")

    # Start loop from 1 since we assume start_block is already filled (or we want to start immediately before it)
    for i in range(1, HOURS_TO_BACKFILL + 1):
        target_block = latest_block - (i * BLOCKS_PER_HOUR)
        
        # Safety check for negative blocks (pre-genesis)
        if target_block < 0:
            print("Reached genesis block. Stopping.")
            break
        
        try:
            # Check for existing data first to avoid duplicates/waste
            exists = False
            for symbol, data in ASSETS.items():
                cursor.execute(f"SELECT 1 FROM {data['table']} WHERE block_number = ?", (target_block,))
                if cursor.fetchone():
                    exists = True
                    break
            
            if exists:
                print(f"  Skipping Block {target_block} (Exists)")
                continue

            # Get timestamp for accuracy
            block_data = w3.eth.get_block(target_block)
            timestamp = block_data['timestamp']
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
            
            print(f"[{i}/{HOURS_TO_BACKFILL}] Processing Block {target_block} ({time_str})")

            for symbol, data in ASSETS.items():
                rate = get_aave_rate_at_block(data['address'], target_block)
                if rate is not None:
                    cursor.execute(f"INSERT OR IGNORE INTO {data['table']} VALUES (?, ?, ?)", (target_block, timestamp, rate))
                    conn.commit()
                    print(f"  Saved {symbol}: {rate:.2f}%")
                    
        except Exception as e:
            print(f"Error processing block {target_block}: {e}")
            continue
            
    print("Backfill Complete.")
    conn.close()

if __name__ == "__main__":
    run_backfill()