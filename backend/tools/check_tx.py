#!/usr/bin/env python3
"""
Check a specific transaction for events.
"""
import sys
sys.path.insert(0, '/home/ubuntu/RLD/backend')

from web3 import Web3
import json

w3 = Web3(Web3.HTTPProvider("http://127.0.0.1:8545"))

# Transaction hash from browser test
tx_hash = "0x1360ab769717e9e5d5bc75d69303573e190923faa784c2a039bf517811530db6"

print(f"Checking transaction: {tx_hash}\n")

try:
    # Get transaction receipt
    receipt = w3.eth.get_transaction_receipt(tx_hash)
    
    print(f"Status: {'Success' if receipt['status'] == 1 else 'Failed'}")
    print(f"Block: {receipt['blockNumber']}")
    print(f"Gas Used: {receipt['gasUsed']}")
    print(f"To: {receipt['to']}")
    print(f"\nLogs ({len(receipt['logs'])} total):")
    
    for i, log in enumerate(receipt['logs']):
        print(f"\n  Log {i}:")
        print(f"    Address: {log['address']}")
        print(f"    Topics: {len(log['topics'])}")
        for j, topic in enumerate(log['topics']):
            print(f"      Topic {j}: {topic.hex()}")
    
    # Load factory ABI and try to decode
    with open("/home/ubuntu/RLD/contracts/out/RLDMarketFactory.sol/RLDMarketFactory.json") as f:
        factory_abi = json.load(f)["abi"]
    
    with open("/home/ubuntu/RLD/shared/addresses.json") as f:
        addresses = json.load(f)
        factory_address = addresses.get("RLDMarketFactory")
    
    factory = w3.eth.contract(address=factory_address, abi=factory_abi)
    
    print(f"\n\nDecoding logs for Factory ({factory_address}):")
    for log in receipt['logs']:
        if log['address'].lower() == factory_address.lower():
            try:
                decoded = factory.events.MarketDeployed().process_log(log)
                print(f"\n✅ Found MarketDeployed event!")
                print(f"   Market ID: {decoded['args']['marketId']}")
                print(f"   Market Address: {decoded['args']['market']}")
                print(f"   Position Token: {decoded['args']['positionToken']}")
            except Exception as e:
                print(f"   Could not decode as MarketDeployed: {e}")
    
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
