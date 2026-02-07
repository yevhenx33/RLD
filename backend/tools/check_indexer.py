#!/usr/bin/env python3
"""
Diagnostic script to check indexer and database status.
"""
import sys
sys.path.insert(0, '/home/ubuntu/RLD/backend')

from db import get_db, get_last_indexed_block, get_all_markets
from web3 import Web3
import json

print("=" * 60)
print("RLD INDEXER DIAGNOSTIC")
print("=" * 60)

# Check database
print("\n📊 DATABASE STATUS:")
try:
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Check markets table
        cursor.execute("SELECT COUNT(*) FROM markets")
        market_count = cursor.fetchone()[0]
        print(f"  Markets in DB: {market_count}")
        
        # Check indexer state
        cursor.execute("SELECT last_block FROM indexer_state WHERE id = 1")
        row = cursor.fetchone()
        last_block = row[0] if row else 0
        print(f"  Last indexed block: {last_block}")
        
        # Show recent markets
        if market_count > 0:
            cursor.execute("SELECT tx_hash, position_token_symbol, deployment_block FROM markets ORDER BY deployment_block DESC LIMIT 5")
            print("\n  Recent markets:")
            for row in cursor.fetchall():
                print(f"    - {row[1]} (block {row[2]}, tx: {row[0][:10]}...)")
except Exception as e:
    print(f"  ❌ Database error: {e}")

# Check blockchain
print("\n⛓️  BLOCKCHAIN STATUS:")
try:
    w3 = Web3(Web3.HTTPProvider("http://127.0.0.1:8545"))
    if w3.is_connected():
        current_block = w3.eth.block_number
        print(f"  Current block: {current_block}")
        print(f"  Connection: ✅ Connected")
        
        # Check for factory address
        with open("/home/ubuntu/RLD/shared/addresses.json") as f:
            addresses = json.load(f)
            factory_address = addresses.get("RLDMarketFactory")
            print(f"  Factory address: {factory_address}")
            
        # Check for MarketDeployed events
        with open("/home/ubuntu/RLD/contracts/out/RLDMarketFactory.sol/RLDMarketFactory.json") as f:
            factory_abi = json.load(f)["abi"]
            
        factory = w3.eth.contract(address=factory_address, abi=factory_abi)
        
        # Get all MarketDeployed events from block 0
        events = factory.events.MarketDeployed.get_logs(from_block=0, to_block='latest')
        print(f"  MarketDeployed events on chain: {len(events)}")
        
        if events:
            print("\n  Recent events:")
            for event in events[-5:]:
                print(f"    - Block {event['blockNumber']}, tx: {event['transactionHash'].hex()[:10]}...")
    else:
        print("  ❌ Not connected to Anvil")
except Exception as e:
    print(f"  ❌ Blockchain error: {e}")

print("\n" + "=" * 60)
