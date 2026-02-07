#!/usr/bin/env python3
"""
Test script to reproduce market deployment and capture revert reason.
"""
import sys
sys.path.insert(0, '/home/ubuntu/RLD/backend')

from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv
import json
import os

# Load environment
load_dotenv("/home/ubuntu/RLD/contracts/.env")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

w3 = Web3(Web3.HTTPProvider("http://127.0.0.1:8545"))
account = Account.from_key(PRIVATE_KEY)

print("=" * 80)
print("MARKET DEPLOYMENT TEST")
print("=" * 80)
print(f"\nDeployer: {account.address}")
print(f"Balance: {w3.from_wei(w3.eth.get_balance(account.address), 'ether')} ETH")
print(f"Current Block: {w3.eth.block_number}\n")

# Load contracts
with open("/home/ubuntu/RLD/contracts/out/RLDMarketFactory.sol/RLDMarketFactory.json") as f:
    factory_abi = json.load(f)["abi"]

with open("/home/ubuntu/RLD/contracts/out/RLDAaveOracle.sol/RLDAaveOracle.json") as f:
    oracle_artifact = json.load(f)
    oracle_abi = oracle_artifact["abi"]
    oracle_bytecode = oracle_artifact["bytecode"]["object"]

with open("/home/ubuntu/RLD/shared/addresses.json") as f:
    addresses = json.load(f)
    factory_address = addresses.get("RLDMarketFactory")

print(f"Factory Address: {factory_address}\n")

# Step 1: Deploy Oracle
print("Step 1: Deploying RLDAaveOracle...")
try:
    OracleFactory = w3.eth.contract(abi=oracle_abi, bytecode=oracle_bytecode)
    
    construct_tx = OracleFactory.constructor().build_transaction({
        'from': account.address,
        'nonce': w3.eth.get_transaction_count(account.address),
        'gas': 2000000,
        'maxFeePerGas': w3.to_wei('2', 'gwei'),
        'maxPriorityFeePerGas': w3.to_wei('1', 'gwei'),
    })
    
    signed_tx = w3.eth.account.sign_transaction(construct_tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    
    if receipt['status'] == 1:
        rate_oracle_address = receipt.contractAddress
        print(f"✅ Oracle deployed: {rate_oracle_address}")
    else:
        print(f"❌ Oracle deployment FAILED")
        print(f"   Gas used: {receipt['gasUsed']}")
        sys.exit(1)
        
except Exception as e:
    print(f"❌ Oracle deployment error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Step 2: Prepare market parameters
print("\nStep 2: Preparing market parameters...")

# Mock addresses (same as api.py)
TOKEN_MAP = {
    "aUSDC": {
        "collateral": w3.to_checksum_address("0xFF00000000000000000000000000000000000001"), 
        "underlying": w3.to_checksum_address("0xFF00000000000000000000000000000000000002"), 
        "pool": w3.to_checksum_address("0xFF00000000000000000000000000000000000003")
    }
}

market_config = TOKEN_MAP["aUSDC"]

# Parameters
min_col_wad = int(150 * 10**16)  # 150%
maint_margin_wad = int(110 * 10**16)  # 110%
liq_close_wad = int(50 * 10**16)  # 50%

spot_oracle_address = w3.to_checksum_address("0x0000000000000000000000000000000000000000")
curator_address = account.address
liq_module = w3.to_checksum_address(addresses.get("DutchLiquidationModule", "0x0000000000000000000000000000000000000000"))

deploy_params = (
    market_config["pool"],          # underlyingPool
    market_config["underlying"],    # underlyingToken
    market_config["collateral"],    # collateralToken
    curator_address,                # curator
    "Wrapped RLP: aUSDC",           # name
    "wRLPaUSDC",                    # symbol
    min_col_wad,                    # minColRatio
    maint_margin_wad,               # maintenanceMargin
    liq_close_wad,                  # liquidationCloseFactor
    liq_module,                     # liquidationModule
    b'\x00' * 32,                   # liquidationParams
    spot_oracle_address,            # spotOracle
    rate_oracle_address,            # rateOracle
    3600,                           # oraclePeriod
    3000,                           # poolFee
    60                              # tickSpacing
)

print("Parameters:")
for i, param in enumerate(deploy_params):
    print(f"  [{i}] {param}")

# Step 3: Call createMarket
print("\nStep 3: Calling factory.createMarket()...")
try:
    factory_contract = w3.eth.contract(address=factory_address, abi=factory_abi)
    
    # First, try to estimate gas to see if it would revert
    print("  Estimating gas...")
    try:
        gas_estimate = factory_contract.functions.createMarket(deploy_params).estimate_gas({
            'from': account.address
        })
        print(f"  ✅ Gas estimate: {gas_estimate}")
    except Exception as e:
        print(f"  ❌ Gas estimation FAILED (transaction would revert)")
        print(f"     Error: {e}")
        
        # Try to get more details by calling the function
        try:
            result = factory_contract.functions.createMarket(deploy_params).call({
                'from': account.address
            })
            print(f"     Call result: {result}")
        except Exception as call_error:
            print(f"     Call error: {call_error}")
        
        sys.exit(1)
    
    # If gas estimation succeeded, send the transaction
    create_tx = factory_contract.functions.createMarket(deploy_params).build_transaction({
        'from': account.address,
        'nonce': w3.eth.get_transaction_count(account.address),
        'gas': 5000000,
        'maxFeePerGas': w3.to_wei('2', 'gwei'),
        'maxPriorityFeePerGas': w3.to_wei('1', 'gwei'),
    })
    
    signed_tx = w3.eth.account.sign_transaction(create_tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    
    print(f"  Transaction sent: {tx_hash.hex()}")
    print(f"  Waiting for receipt...")
    
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    
    if receipt['status'] == 1:
        print(f"\n✅ SUCCESS! Market deployed in block {receipt['blockNumber']}")
        print(f"   Gas used: {receipt['gasUsed']}")
        print(f"   Logs: {len(receipt['logs'])}")
    else:
        print(f"\n❌ FAILED! Transaction reverted")
        print(f"   Gas used: {receipt['gasUsed']}")
        
except Exception as e:
    print(f"\n❌ Deployment error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 80)
