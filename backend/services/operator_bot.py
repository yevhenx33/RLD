import time
import os
from collections import deque
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct
from dotenv import load_dotenv

# --- CONFIGURATION ---
# Load environment variables from the contracts/.env file
load_dotenv("../.env")

# Network & Wallet Config
import logging

# --- Logging Config ---
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Network & Wallet Config
# Use explicit Env Var. Fail if missing to prevent silent localhost connection attempts.
RPC_URL = os.getenv("MAINNET_RPC_URL")
if not RPC_URL:
    logging.critical("❌ MAINNET_RPC_URL not set in .env")
    exit(1)
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
if not PRIVATE_KEY:
    raise ValueError("❌ PRIVATE_KEY not set in environment or .env file!")

# Contract Addresses (Update ORACLE_ADDRESS after deployment!)
# Load Contract Addresses from Shared JSON
try:
    with open("../shared/addresses.json", "r") as f:
        import json
        addresses = json.load(f)
        ORACLE_ADDRESS = addresses.get("SymbioticRateOracle")

    if not ORACLE_ADDRESS:
        logging.warning("⚠️ SymbioticRateOracle address not found in shared/addresses.json. Bot will sleep.")
        # Proceed with Dummy address to allow import, but script logic should handle it
        ORACLE_ADDRESS = "0x0000000000000000000000000000000000000000"

except Exception as e:
    logging.warning(f"⚠️ Warning: Could not load shared/addresses.json: {e}")
    ORACLE_ADDRESS = os.getenv("ORACLE_ADDRESS", "0x0000000000000000000000000000000000000000")

# Aave V3 Mainnet Addresses
AAVE_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
USDC_ADDR = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

# RLD Protocol Parameters
K_SCALAR = 100
TWAR_WINDOW_BLOCKS = 2  # Reduced to 2 for faster testing (Use 100 in production)
POLL_INTERVAL = 12      # Seconds (Approx 1 block)

# --- SETUP ---
w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    raise Exception(f"❌ Failed to connect to RPC: {RPC_URL}")

account = Account.from_key(PRIVATE_KEY)
logging.info(f"🚀 Operator Active: {account.address}")
logging.info(f"📡 Oracle Contract: {ORACLE_ADDRESS}")

# Minimal ABIs
POOL_ABI = '[{"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getReserveData","outputs":[{"internalType":"uint256","name":"configuration","type":"uint256"},{"internalType":"uint128","name":"liquidityIndex","type":"uint128"},{"internalType":"uint128","name":"currentLiquidityRate","type":"uint128"},{"internalType":"uint128","name":"variableBorrowIndex","type":"uint128"},{"internalType":"uint128","name":"currentVariableBorrowRate","type":"uint128"},{"internalType":"uint128","name":"currentStableBorrowRate","type":"uint128"},{"internalType":"uint40","name":"lastUpdateTimestamp","type":"uint40"},{"internalType":"uint16","name":"id","type":"uint16"},{"internalType":"address","name":"aTokenAddress","type":"address"},{"internalType":"address","name":"stableDebtTokenAddress","type":"address"},{"internalType":"address","name":"variableDebtTokenAddress","type":"address"},{"internalType":"address","name":"interestRateStrategyAddress","type":"address"},{"internalType":"uint128","name":"accruedToTreasury","type":"uint128"},{"internalType":"uint128","name":"unbacked","type":"uint128"},{"internalType":"uint128","name":"isolationModeTotalDebt","type":"uint128"}],"stateMutability":"view","type":"function"}]'
ORACLE_ABI = '[{"inputs":[{"internalType":"uint256","name":"twarWad","type":"uint256"},{"internalType":"uint256","name":"timestamp","type":"uint256"},{"internalType":"bytes","name":"signature","type":"bytes"}],"name":"updateTwar","outputs":[],"stateMutability":"nonpayable","type":"function"}]'

pool_contract = w3.eth.contract(address=AAVE_POOL, abi=POOL_ABI)
oracle_contract = w3.eth.contract(address=ORACLE_ADDRESS, abi=ORACLE_ABI)

# In-memory history for TWAR calculation
rate_history = deque(maxlen=TWAR_WINDOW_BLOCKS)

def get_aave_price_wad():
    """Fetches Aave rate and converts to RLD Price (WAD)"""
    # Call Aave V3 getReserveData
    data = pool_contract.functions.getReserveData(USDC_ADDR).call()
    rate_ray = data[4] # currentVariableBorrowRate index
    
    # RLD Math: Price = (RateRAY * K) / 1e9 -> Converts 27 decimals (RAY) to 18 (WAD)
    # Example: 5% (0.05e27) * 100 / 1e9 = 5e18 ($5.00)
    price_wad = (rate_ray * K_SCALAR) // 10**9
    return price_wad

def run_oracle():
    logging.info(f"⏳ collecting {TWAR_WINDOW_BLOCKS} data points before first push...")
    
    while True:
        try:
            # 1. Fetch Spot Price
            spot_wad = get_aave_price_wad()
            rate_history.append(spot_wad)
            
            # 2. Calculate TWAR (Simple Moving Average of Window)
            twar_wad = sum(rate_history) // len(rate_history)
            timestamp = int(time.time())
            
            # Formatting for display
            spot_fmt = spot_wad / 10**18
            twar_fmt = twar_wad / 10**18
            
            logging.info(f"[{len(rate_history)}/{TWAR_WINDOW_BLOCKS}] Spot: ${spot_fmt:.4f} | TWAR: ${twar_fmt:.4f}")

            # 3. If window is full, Push to Chain
            if len(rate_history) >= TWAR_WINDOW_BLOCKS:
                logging.info(">> Pushing Update...")
                
                # Create Hash: keccak256(twar, timestamp, chainId, oracleAddr)
                # This MUST match the solidity structure exactly
                msg_hash = w3.solidity_keccak(
                    ['uint256', 'uint256', 'uint256', 'address'],
                    [twar_wad, timestamp, w3.eth.chain_id, ORACLE_ADDRESS]
                )
                
                # Sign the hash (Symbiotic Validation)
                message = encode_defunct(hexstr=msg_hash.hex())
                signed_msg = w3.eth.account.sign_message(message, private_key=PRIVATE_KEY)
                
                # Transact
                # FIX: Pass signed_msg.signature (bytes) directly, DO NOT use .hex()
                tx = oracle_contract.functions.updateTwar(
                    twar_wad, 
                    timestamp, 
                    signed_msg.signature 
                ).build_transaction({
                    'from': account.address,
                    'nonce': w3.eth.get_transaction_count(account.address),
                    'gas': 200000,
                    'maxFeePerGas': w3.to_wei('25', 'gwei'),
                    'maxPriorityFeePerGas': w3.to_wei('2', 'gwei'),
                })
                
                # Sign & Send Transaction
                signed_tx = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
                # Handle different web3.py/eth_account versions (camelCase vs snake_case)
                if hasattr(signed_tx, "rawTransaction"):
                    raw_tx = signed_tx.rawTransaction
                elif hasattr(signed_tx, "raw_transaction"):
                    raw_tx = signed_tx.raw_transaction
                else:
                    # Fallback for older versions or dict-like objects
                    raw_tx = signed_tx[0] 
                
                tx_hash = w3.eth.send_raw_transaction(raw_tx)
                logging.info(f"✅ Tx Sent: {tx_hash.hex()}")

            time.sleep(POLL_INTERVAL)

        except Exception as e:
            logging.error(f"❌ Error: {e}")
            # Don't crash the loop, just retry
            time.sleep(5)

if __name__ == "__main__":
    run_oracle()