import threading
import requests
from datetime import datetime
import time
import sqlite3
import os
from web3 import Web3
from dotenv import load_dotenv

# --- CONFIGURATION ---
load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))
RPC_URL = os.getenv("MAINNET_RPC_URL")
RESERVE_RPC = os.getenv("RESERVE_RPC_URL")

RPC_URLS = [url for url in [RPC_URL, RESERVE_RPC, "https://eth.llamarpc.com"] if url]
current_rpc_index = 0

if not RPC_URLS:
    print("CRITICAL: No RPC URLs found.")
    exit(1)

# Import Centralized Config
from config import AAVE_POOL_ADDRESS, UNI_POOL_ADDRESS, ASSETS, DB_NAME, DB_PATH, CLEAN_DB_PATH

POOL_ADDRESS = AAVE_POOL_ADDRESS
# UNI_POOL_ADDRESS is imported directly

# --- SETUP ---
w3 = Web3(Web3.HTTPProvider(RPC_URLS[0]))

def switch_rpc():
    global current_rpc_index, w3
    current_rpc_index = (current_rpc_index + 1) % len(RPC_URLS)
    new_url = RPC_URLS[current_rpc_index]
    print(f"⚠️ Switching RPC to: {new_url}")
    w3.provider = Web3.HTTPProvider(new_url)

# Database Setup
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Initialize Tables (On-Chain Assets only)
for symbol, data in ASSETS.items():
    if data['type'] == 'onchain':
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS {data['table']} (
                block_number INTEGER,
                timestamp INTEGER,
                apy REAL
            )
        ''')

# Price Table
cursor.execute('''
    CREATE TABLE IF NOT EXISTS eth_prices (
        timestamp INTEGER PRIMARY KEY, 
        price REAL,
        block_number INTEGER
    )
''')

# SOFR Table
cursor.execute('''
    CREATE TABLE IF NOT EXISTS sofr_rates (
        timestamp INTEGER PRIMARY KEY,
        apy REAL
    )
''')
conn.commit()

SOFR_API_URL = "https://markets.newyorkfed.org/api/rates/secured/sofr/last/1.json"

def poll_sofr_data():
    """
    Background thread to fetch SOFR rates from NY Fed API.
    Runs every hour, but only inserts if data is new.
    """
    print("📈 Starting SOFR Indexer Thread...")
    
    while True:
        try:
            conn_sofr = sqlite3.connect(DB_NAME)
            cursor_sofr = conn_sofr.cursor()
            
            response = requests.get(SOFR_API_URL, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if 'refRates' in data and len(data['refRates']) > 0:
                    rate_data = data['refRates'][0]
                    
                    # Parse Date
                    date_str = rate_data['effectiveDate'] # YYYY-MM-DD
                    percent = rate_data['percentRate']
                    
                    # Convert to Timestamp (Midnight UTC or specific?)
                    # Existing Excel import likely used pd.to_datetime which defaults to midnight
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    ts = int(dt.timestamp())
                    
                    # Insert (Ignore if exists to avoid duplicates)
                    cursor_sofr.execute("INSERT OR IGNORE INTO sofr_rates (timestamp, apy) VALUES (?, ?)", (ts, percent))
                    conn_sofr.commit()
                    
                    # Log if it was a new insertion (rowcount > 0 doesn't work well with IGNORE in all sqlite versions, check manually? 
                    # simple print is fine, it's a background thread)
                    # print(f"   [SOFR] Checked {date_str}: {percent}%")
            
            conn_sofr.close()
            
        except Exception as e:
            print(f"   ⚠️ [SOFR] Error: {e}")
        
        # Sleep 1 hour
        time.sleep(3600)


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

UNI_ABI = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
            {"internalType": "int24", "name": "tick", "type": "int24"},
            {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
            {"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},
            {"internalType": "bool", "name": "unlocked", "type": "bool"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]

pool_contract = w3.eth.contract(address=POOL_ADDRESS, abi=POOL_ABI)
uni_contract = w3.eth.contract(address=UNI_POOL_ADDRESS, abi=UNI_ABI)

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

def get_eth_price(block_identifier='latest'):
    try:
        # slot0 returns (sqrtPriceX96, tick, observationIndex, ...)
        slot0 = uni_contract.functions.slot0().call(block_identifier=block_identifier)
        sqrtPriceX96 = slot0[0]
        
        # Calculate Price
        # Address 0xA0b8... (USDC) is Token0
        # Address 0xC02a... (WETH) is Token1
        # Price = Token1/Token0 (WETH per USDC)
        # We want USDC per ETH = 1 / Price
        
        # P_raw = (sqrtPriceX96 / 2^96) ^ 2
        # Price_USD = 10^12 / P_raw = 10^12 / ((sqrtPriceX96 / 2^96) ^ 2)
        
        price_raw = (sqrtPriceX96 / (2**96)) ** 2
        eth_price = (10**12) / price_raw
        return eth_price
    except Exception as e:
        print(f"Error fetching ETH Price: {e}")
        return None

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    if not w3.is_connected():
        print("CRITICAL: Could not connect to Ethereum node.")
        exit()
        
    print(f"Monitoring Aave V3 Borrow Rates (USDC, DAI, USDT)...")
    print(f"Connected to: {RPC_URL}")
    print("-" * 40)
    
    # Start SOFR Thread
    t = threading.Thread(target=poll_sofr_data, daemon=True)
    t.start()
    
    # Clean DB Path
    CLEAN_DB_NAME = "clean_rates.db"

    try:
        while True:
            # Fetch latest block for timestamp and consistency
            try:
                block = w3.eth.get_block('latest')
                block_number = block['number']
                block_timestamp = block['timestamp']
                
                timestamp_str = time.strftime("%H:%M:%S", time.localtime(block_timestamp))
                
                # --- UPDATE CLEAN DB ---
                period_ts = (block_timestamp // 3600) * 3600
                conn_clean = sqlite3.connect(CLEAN_DB_PATH)
                cursor_clean = conn_clean.cursor()
                cursor_clean.execute("INSERT OR IGNORE INTO hourly_stats (timestamp) VALUES (?)", (period_ts,))
                conn_clean.commit()
                # -----------------------

                for symbol, data in ASSETS.items():
                    if data.get('type') != 'onchain':
                        continue
                        
                    rate = get_aave_rate(data['address'], block_number)
                    if rate is not None:
                        # Existing DB
                        cursor.execute(f"INSERT INTO {data['table']} VALUES (?, ?, ?)", (block_number, block_timestamp, rate))
                        conn.commit()
                        print(f"[{timestamp_str}] Block {block_number} | {symbol}: {rate:.2f}%")
                        
                        # Clean DB Update
                        col_map = {
                            "USDC": "usdc_rate",
                            "DAI": "dai_rate",
                            "USDT": "usdt_rate"
                        }
                        if symbol in col_map:
                            col = col_map[symbol]
                            cursor_clean.execute(f"UPDATE hourly_stats SET {col} = ? WHERE timestamp = ?", (rate, period_ts))
                            conn_clean.commit()
                
                # Fetch & Store ETH Price
                eth_price = get_eth_price(block_number)
                if eth_price:
                    # Existing DB
                    cursor.execute("INSERT OR REPLACE INTO eth_prices (timestamp, price, block_number) VALUES (?, ?, ?)", 
                                  (block_timestamp, eth_price, block_number))
                    conn.commit()
                    print(f"[{timestamp_str}] Block {block_number} | ETH Price: ${eth_price:,.2f}")
                    
                    # Clean DB Update
                    cursor_clean.execute("UPDATE hourly_stats SET eth_price = ? WHERE timestamp = ?", (eth_price, period_ts))
                    conn_clean.commit()

                conn_clean.close()
            
            except Exception as loop_err:
                print(f"Error in loop: {loop_err}")
                switch_rpc()

            time.sleep(12)
            
    except KeyboardInterrupt:
        print("\nStopping script.")