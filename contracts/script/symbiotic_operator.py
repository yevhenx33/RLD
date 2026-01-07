import time
import os
import json
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct
from dotenv import load_dotenv

# Load Environment
load_dotenv("../contracts/.env")  # Adjust path to your .env
RPC_URL = os.getenv("MAINNET_RPC_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY") # Add this to your .env!
ORACLE_ADDRESS = "0x..." # Address of deployed SymbioticRateOracle

# Setup Web3
w3 = Web3(Web3.HTTPProvider(RPC_URL))
operator_account = Account.from_key(PRIVATE_KEY)

print(f"🚀 Symbiotic Operator Started: {operator_account.address}")

# Configuration
TWAR_WINDOW_BLOCKS = 100  # Based on your RMD analysis
POLL_INTERVAL = 12        # ~1 block

# ABI for the update function
ORACLE_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "twarWad", "type": "uint256"},
            {"internalType": "uint256", "name": "timestamp", "type": "uint256"},
            {"internalType": "bytes", "name": "signature", "type": "bytes"}
        ],
        "name": "updateTwar",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

contract = w3.eth.contract(address=ORACLE_ADDRESS, abi=ORACLE_ABI)

# --- Aave Helpers ---
# (Reuse your Aave logic here, simplified for brevity)
# In production, use your batched_backfill.py logic to maintain a rolling window
rolling_window = [] 

def get_aave_spot_price():
    # ... call Aave contract ...
    # Return simulated price for demo
    return 5.00 * 10**18 

def calculate_twar(new_price):
    rolling_window.append(new_price)
    if len(rolling_window) > TWAR_WINDOW_BLOCKS:
        rolling_window.pop(0)
    return sum(rolling_window) // len(rolling_window)

def run_loop():
    while True:
        try:
            # 1. Fetch & Calculate
            current_spot = get_aave_spot_price()
            twar_wad = calculate_twar(current_spot)
            timestamp = int(time.time())
            chain_id = w3.eth.chain_id

            print(f"Stats | Spot: {current_spot/1e18:.4f} | TWAR: {twar_wad/1e18:.4f}")

            # 2. Sign Data (Symbiotic Logic)
            # Must match Solidity: keccak256(twar, timestamp, chainId, contractAddr)
            # We pack arguments tightly
            msg_hash = w3.solidity_keccak(
                ['uint256', 'uint256', 'uint256', 'address'],
                [twar_wad, timestamp, chain_id, ORACLE_ADDRESS]
            )
            message = encode_defunct(hexstr=msg_hash.hex())
            signed_msg = w3.eth.account.sign_message(message, private_key=PRIVATE_KEY)
            signature = signed_msg.signature.hex()

            # 3. Push to Chain
            # Build transaction
            tx = contract.functions.updateTwar(
                twar_wad, 
                timestamp, 
                signature
            ).build_transaction({
                'from': operator_account.address,
                'nonce': w3.eth.get_transaction_count(operator_account.address),
                'gas': 200000,
                'maxFeePerGas': w3.to_wei('20', 'gwei'),
                'maxPriorityFeePerGas': w3.to_wei('1', 'gwei'),
            })

            # Sign & Send (Uncomment to execute real tx)
            # signed_tx = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
            # tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            # print(f"✅ Posted TWAR: {tx_hash.hex()}")

            time.sleep(POLL_INTERVAL)

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    run_loop()