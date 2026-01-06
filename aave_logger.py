import time
import sqlite3
from web3 import Web3

# --- CONFIGURATION ---
RPC_URL = "https://eth.llamarpc.com"
POOL_ADDRESS = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

# --- SETUP ---
w3 = Web3(Web3.HTTPProvider(RPC_URL))

# Database Setup
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
# The decoder requires every single field to be defined to work.
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
def get_aave_usdc_rate(block_identifier='latest'):
    try:
        # Fetch data from smart contract
        reserve_data = pool_contract.functions.getReserveData(USDC_ADDRESS).call(block_identifier=block_identifier)
        
        # Target Index: 4 (currentVariableBorrowRate)
        raw_rate = reserve_data[4]
        
        # Convert from Ray (10^27) to percentage
        apy = raw_rate / 10**27 * 100
        return apy
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    if not w3.is_connected():
        print("CRITICAL: Could not connect to Ethereum node.")
        exit()
        
    print(f"Monitoring Aave V3 USDC Borrow Rate...")
    print(f"Connected to: {RPC_URL}")
    print("-" * 40)
    
    try:
        while True:
            # Fetch latest block for timestamp and consistency
            block = w3.eth.get_block('latest')
            block_number = block['number']
            block_timestamp = block['timestamp']
            
            rate = get_aave_usdc_rate(block_number)
            if rate is not None:
                cursor.execute("INSERT INTO rates VALUES (?, ?, ?)", (block_number, block_timestamp, rate))
                conn.commit()
                
                timestamp_str = time.strftime("%H:%M:%S", time.localtime(block_timestamp))
                print(f"[{timestamp_str}] Block {block_number} | USDC Borrow APY: {rate:.2f}%")
            
            time.sleep(12)
            
    except KeyboardInterrupt:
        print("\nStopping script.")