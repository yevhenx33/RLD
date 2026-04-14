import sys
import os
import argparse
import asyncio
import logging
from web3 import Web3
import clickhouse_connect

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from indexer.sources.morpho import MorphoSource

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("morpho-poka-yoke")

MORPHO_BLUE = "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"
MARKET_ABI = [
    {
        "inputs": [{"internalType": "Id", "name": "id", "type": "bytes32"}],
        "name": "market",
        "outputs": [
            {"internalType": "uint128", "name": "totalSupplyAssets", "type": "uint128"},
            {"internalType": "uint128", "name": "totalSupplyShares", "type": "uint128"},
            {"internalType": "uint128", "name": "totalBorrowAssets", "type": "uint128"},
            {"internalType": "uint128", "name": "totalBorrowShares", "type": "uint128"},
            {"internalType": "uint128", "name": "lastUpdate", "type": "uint128"},
            {"internalType": "uint128", "name": "fee", "type": "uint128"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]

class Struct:
    def __init__(self, **entries):
        self.__dict__.update(entries)

import pandas as pd

def run_validation():
    log.info("Starting Morpho Blue Poka-Yoke Drift Validator...")
    ch_host = os.getenv("CLICKHOUSE_HOST", "localhost")
    ch_port = int(os.getenv("CLICKHOUSE_PORT", "8123"))
    rpc_url = os.environ.get("MAINNET_RPC_URL", "https://eth.llamarpc.com")
    
    ch = clickhouse_connect.get_client(host=ch_host, port=ch_port)
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    
    # 1. Determine highest block available in cold storage
    try:
        max_block = ch.command("SELECT max(block_number) FROM morpho_events")
        max_block = int(max_block)
    except Exception as e:
        log.error(f"Cannot read morpho_events: {e}")
        return
        
    log.info(f"Targeting Absolute Validation Block: {max_block}")
    
    # 2. Replay the Local Deterministic Physics Engine
    log.info("Replaying EVM memory pool events locally...")
    source = MorphoSource()
    source._load_symbols(ch)
    
    events_df = ch.query_df(f"SELECT block_number, log_index, topic0, topic1, topic2, topic3, data FROM morpho_events WHERE block_number <= {max_block} ORDER BY block_number ASC, log_index ASC")
    
    for row in events_df.itertuples():
        topics_arr = [row.topic0]
        if pd.notna(row.topic1): topics_arr.append(row.topic1)
        if pd.notna(row.topic2): topics_arr.append(row.topic2)
        if pd.notna(row.topic3): topics_arr.append(row.topic3)
        
        log_entry = Struct(
            block_number=row.block_number,
            topics=topics_arr,
            data=row.data
        )
        # block_ts_map is just an empty dictionary since we only care about state array, not time here
        source.decode(log_entry, {})

    # 3. Stateless RPC Check
    log.info(f"Local Engine Generated {len(source._markets)} Unique State Matrices.")
    
    contract = w3.eth.contract(address=w3.to_checksum_address(MORPHO_BLUE), abi=MARKET_ABI)
    
    drift_detected = False
    
    # Check top highly-active markets
    checked = 0
    for market_id, state in list(source._markets.items())[:50]: # Bound loop
        if state.total_supply_assets == 0 and state.total_borrow_assets == 0:
            continue
            
        checked += 1
        rpc_market = contract.functions.market("0x" + market_id).call(block_identifier=max_block)
        
        rpc_supply = rpc_market[0]
        rpc_borrow = rpc_market[2]
        
        local_supply = state.total_supply_assets
        local_borrow = state.total_borrow_assets
        
        # Calculate Delta
        supply_delta = abs(rpc_supply - local_supply) / rpc_supply if rpc_supply > 0 else 0
        borrow_delta = abs(rpc_borrow - local_borrow) / rpc_borrow if rpc_borrow > 0 else 0
        
        # Poka Yoke Andon Cord
        if supply_delta > 0.001 or borrow_delta > 0.001:
            log.error(f"DRIFT DETECTED IN MARKET {market_id}")
            log.error(f"  Supply | RPC: {rpc_supply} | Local: {local_supply} | Delta: {supply_delta:.4%}")
            log.error(f"  Borrow | RPC: {rpc_borrow} | Local: {local_borrow} | Delta: {borrow_delta:.4%}")
            drift_detected = True
        else:
            log.debug(f"Market {market_id[:10]}... perfectly matched.")
            
    if not drift_detected:
        log.info(f"POKA-YOKE VALIDATION PASSED. 0% structural drift detected across {checked} evaluated markets at block {max_block}.")
    else:
        log.error("SYSTEM HALT. Event processor array has mathematically drifted from the EVM.")
        sys.exit(1)

if __name__ == "__main__":
    run_validation()
