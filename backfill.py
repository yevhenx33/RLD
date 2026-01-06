import time
import sqlite3
from web3 import Web3

# --- CONFIGURATION ---
RPC_URL = "https://eth.llamarpc.com"
POOL_ADDRESS = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

# Backfill Settings
DAYS_TO_BACKFILL = 7
HOURS_PER_STEP = 1  # Grab 1 data point per hour to save time
# Ethereum averages 12 seconds per block. 
# 3600 seconds / 12 seconds = 300 blocks per hour
BLOCK_STEP = 300 * HOURS_PER_STEP 

# --- SETUP ---
w3 = Web3(Web3.HTTPProvider(RPC_URL))

# Database Setup (Connects to the same DB as your logger)
conn = sqlite3.connect('aave_rates.db')
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS rates (
        block_number INTEGER,
        timestamp INTEGER,
        apy REAL
    )
''')
conn.commit()

# FULL ABI (Exact Aave V3 ReserveData Structure)
POOL_ABI = [
    {
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
    }
]

pool_contract = w3.eth.contract(address=POOL_ADDRESS, abi=POOL_ABI)

def get_historical_rate(block_number):
    try:
        reserve_data = pool_contract.functions.getReserveData(USDC_ADDRESS).call(block_identifier=block_number)
        raw_rate = reserve_data[4] # Index 4 is Variable Borrow Rate
        return raw_rate / 10**27 * 100
    except Exception as e:
        print(f"Error at block {block_number}: {e}")
        return None

def run_backfill():
    if not w3.is_connected():
        print("CRITICAL: Could not connect to Ethereum node.")
        exit()

    # 1. Calculate Block Range
    latest_block = w3.eth.get_block('latest')
    end_block = latest_block['number']
    
    # Approx blocks in 7 days: (7 * 24 * 3600) / 12 = 50,400
    blocks_back = int((DAYS_TO_BACKFILL * 24 * 3600) / 12)
    start_block = end_block - blocks_back

    print(f"--- Starting Backfill ---")
    print(f"Range: Block {start_block} to {end_block}")
    print(f"Step: Every {BLOCK_STEP} blocks (~{HOURS_PER_STEP} hour)")
    print(f"Total points to fetch: {int(blocks_back / BLOCK_STEP)}")
    print("-" * 30)

    count = 0
    # 2. Iterate backwards or forwards. For charts, order doesn't strictly matter as we sort by time later.
    # We loop forward so we can see progress chronologically.
    for current_block in range(start_block, end_block, BLOCK_STEP):
        try:
            # Get timestamp for this specific block
            # (Note: This adds an extra RPC call, but ensures accurate time)
            block_data = w3.eth.get_block(current_block)
            timestamp = block_data['timestamp']
            
            rate = get_historical_rate(current_block)
            
            if rate is not None:
                cursor.execute("INSERT INTO rates VALUES (?, ?, ?)", (current_block, timestamp, rate))
                conn.commit()
                
                date_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))
                print(f"[{date_str}] Block {current_block} | APY: {rate:.2f}%")
                count += 1
            
            # Tiny sleep to be nice to the free RPC
            time.sleep(0.1)
            
        except Exception as e:
            print(f"Skipping block {current_block}: {e}")

    print("-" * 30)
    print(f"Backfill Complete! Added {count} data points.")
    conn.close()

if __name__ == "__main__":
    run_backfill()