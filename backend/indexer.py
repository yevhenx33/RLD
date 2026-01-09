import time
import sqlite3
import os
from web3 import Web3
from dotenv import load_dotenv

# --- CONFIGURATION ---
load_dotenv("../contracts/.env")
RPC_URL = os.getenv("MAINNET_RPC_URL")
if not RPC_URL:
    print("Warning: MAINNET_RPC_URL not found in .env, using public RPC")
    RPC_URL = "https://eth.llamarpc.com"

POOL_ADDRESS = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
# --- ASSETS CONFIGURATION ---
# Symbol -> Underlying Address
ASSETS = {
    "USDC": {
        "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "table": "rates" # Legacy table name for USDC
    },
    "DAI": {
        "address": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
        "table": "rates_dai"
    },
    "USDT": {
        "address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "table": "rates_usdt"
    }
}

# --- SETUP ---
w3 = Web3(Web3.HTTPProvider(RPC_URL))

# Database Setup
conn = sqlite3.connect('aave_rates.db')
cursor = conn.cursor()

# Initialize Tables
for symbol, data in ASSETS.items():
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS {data['table']} (
            block_number INTEGER,
            timestamp INTEGER,
            apy REAL
        )
    ''')
conn.commit()

# FULL ABI (Exact Aave V3 ReserveData Structure)
# ... (ABI remains same) ...
POOL_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "asset", "type": "address"}],
        "name": "getReserveData",
        "outputs": [
            # 0. Configuration (Bitmap)
            {"internalType": "uint256", "name": "configuration", "type": "uint256"},
            # 1. Liquidity Index
            {"internalType": "uint128", "name": "liquidityIndex", "type": "uint128"},
            # 2. Supply Rate
            {"internalType": "uint128", "name": "currentLiquidityRate", "type": "uint128"},
            # 3. Variable Borrow Index
            {"internalType": "uint128", "name": "variableBorrowIndex", "type": "uint128"},
            # 4. Variable Borrow Rate (TARGET)
            {"internalType": "uint128", "name": "currentVariableBorrowRate", "type": "uint128"},
            # 5. Stable Borrow Rate
            {"internalType": "uint128", "name": "currentStableBorrowRate", "type": "uint128"},
            # 6. Timestamp
            {"internalType": "uint40", "name": "lastUpdateTimestamp", "type": "uint40"},
            # 7. ID
            {"internalType": "uint16", "name": "id", "type": "uint16"},
            # 8. aToken Address
            {"internalType": "address", "name": "aTokenAddress", "type": "address"},
            # 9. Stable Debt Token
            {"internalType": "address", "name": "stableDebtTokenAddress", "type": "address"},
            # 10. Variable Debt Token
            {"internalType": "address", "name": "variableDebtTokenAddress", "type": "address"},
            # 11. Interest Strategy
            {"internalType": "address", "name": "interestRateStrategyAddress", "type": "address"},
            # 12. Accrued To Treasury
            {"internalType": "uint128", "name": "accruedToTreasury", "type": "uint128"},
            # 13. Unbacked
            {"internalType": "uint128", "name": "unbacked", "type": "uint128"},
            # 14. Isolation Mode Total Debt
            {"internalType": "uint128", "name": "isolationModeTotalDebt", "type": "uint128"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]

pool_contract = w3.eth.contract(address=POOL_ADDRESS, abi=POOL_ABI)

# --- FUNCTIONS ---
def get_aave_rate(asset_address, block_identifier='latest'):
    try:
        # Fetch data from smart contract
        reserve_data = pool_contract.functions.getReserveData(asset_address).call(block_identifier=block_identifier)
        
        # Target Index: 4 (currentVariableBorrowRate)
        raw_rate = reserve_data[4]
        
        # Convert from Ray (10^27) to percentage
        apy = raw_rate / 10**27 * 100
        return apy
    except Exception as e:
        print(f"Error fetching data for {asset_address}: {e}")
        return None

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    if not w3.is_connected():
        print("CRITICAL: Could not connect to Ethereum node.")
        exit()
        
    print(f"Monitoring Aave V3 Borrow Rates (USDC, DAI, USDT)...")
    print(f"Connected to: {RPC_URL}")
    print("-" * 40)
    
    try:
        while True:
            # Fetch latest block for timestamp and consistency
            try:
                block = w3.eth.get_block('latest')
                block_number = block['number']
                block_timestamp = block['timestamp']
                
                timestamp_str = time.strftime("%H:%M:%S", time.localtime(block_timestamp))
                
                for symbol, data in ASSETS.items():
                    rate = get_aave_rate(data['address'], block_number)
                    if rate is not None:
                        cursor.execute(f"INSERT INTO {data['table']} VALUES (?, ?, ?)", (block_number, block_timestamp, rate))
                        conn.commit()
                        print(f"[{timestamp_str}] Block {block_number} | {symbol}: {rate:.2f}%")
            
            except Exception as loop_err:
                print(f"Error in loop: {loop_err}")

            time.sleep(12)
            
    except KeyboardInterrupt:
        print("\nStopping script.")