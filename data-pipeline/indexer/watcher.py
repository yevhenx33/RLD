import sys
import os
import time
import logging
import asyncio
import requests
import datetime
import clickhouse_connect

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from indexer.sources.aave_v3 import AaveV3Source

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("watcher")

RPC_URL = os.getenv("MAINNET_RPC_URL", "https://eth.llamarpc.com")
DEVIATION_THRESHOLD_APY = 0.0005  # 0.05% APY acceptable drift

# Aave V3 USDC Address for the test
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
AAVE_POOL = "0x87870Bca3F3fD622A016d4xc6A13b632D06" # from config

def get_aave_rpc_borrow_apy(block_number: int, asset_address: str) -> float:
    # getReserveData(address)
    clean_addr = asset_address[2:] if asset_address.startswith("0x") else asset_address
    calldata = "0x35ea6a75" + clean_addr.zfill(64)
    # Aave pool contract
    AAVE_POOL_CONTRACT = "0x87870Bca3F3fD622A016d4A561d6A13b632d44f6"
    
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [
            {"to": AAVE_POOL_CONTRACT, "data": calldata},
            hex(block_number)
        ],
        "id": 1
    }
    try:
        resp = requests.post(RPC_URL, json=payload, timeout=10)
        res = resp.json()
        raw = res.get("result")
        if not raw or raw == "0x":
            return 0.0
            
        hex_data = raw[2:]
        start = 4 * 64
        end = 5 * 64
        if len(hex_data) < end:
            return 0.0
            
        return int(hex_data[start:end], 16) / 10**27
    except Exception as e:
        log.error(f"RPC call failed: {e}")
        return 0.0

class ShadowWatcher:
    def __init__(self, clickhouse_host="localhost", clickhouse_port=8123):
        self.ch_host = clickhouse_host
        self.ch_port = clickhouse_port

    def get_ch_client(self):
        return clickhouse_connect.get_client(host=self.ch_host, port=self.ch_port)
        
    def check_aave_integrity(self):
        ch = self.get_ch_client()
        
        # 1. Get the last processed block from the processor state
        res = ch.command("SELECT max(last_processed_block) FROM processor_state WHERE protocol='AAVE_MARKET'")
        last_block = int(res) if res else 0
        if last_block == 0:
            log.info("No processed state for AAVE_MARKET. Watcher sleeping.")
            return

        # 2. Get the APY we calculated in Timeseries at the maximum timestamp
        # (Assuming the max timestamp corresponds roughly to the last_processed_block execution)
        ts_res = ch.query_df("""
            SELECT supply_apy, borrow_apy, timestamp
            FROM unified_timeseries 
            WHERE protocol='AAVE_MARKET' AND symbol='USDC'
            ORDER BY timestamp DESC LIMIT 1
        """)
        
        if ts_res.empty:
            log.info("No unified timeseries data for Aave. Watcher sleeping.")
            return
            
        db_borrow_apy = ts_res.iloc[0]['borrow_apy']
        db_ts = ts_res.iloc[0]['timestamp']
        
        log.info(f"DB State at Block {last_block} (approx {db_ts}): {db_borrow_apy*100:.4f}%")
        
        # 3. Call exact On-Chain state
        chain_borrow_apy = get_aave_rpc_borrow_apy(last_block, USDC_ADDRESS)
        log.info(f"Chain State exact at Block {last_block}: {chain_borrow_apy*100:.4f}%")
        
        if chain_borrow_apy == 0.0:
            log.warning("Could not fetch valid chain state. Skipping.")
            return
            
        # 4. Check Diff
        diff = abs(chain_borrow_apy - db_borrow_apy)
        log.info(f"Drift Deviation: {diff*100:.4f}%")
        
        if diff > DEVIATION_THRESHOLD_APY:
            self._trigger_self_healing(ch, "AAVE_MARKET", last_block)
        else:
            log.info("✅ State Integrity Verified. Math perfectly aligns with EVM.")
            
    def _trigger_self_healing(self, ch, protocol: str, current_block: int):
        log.warning(f"🚨 CRITICAL DRIFT DETECTED IN {protocol} 🚨")
        
        # Check if we already tried to heal recently (One-Strike Rule)
        recent_heals = ch.command(f"""
            SELECT count() FROM processor_state 
            WHERE protocol='{protocol}' AND inserted_at >= now() - INTERVAL 1 HOUR
        """)
        if recent_heals > 5:
            # The Andon Cord!
            log.error(f"❌ [ANDON CORD PULLED] {protocol} has failed self-healing multiple times. Shutting down worker.")
            sys.exit(1)
            
        rollback_blocks = 7200  # approx 24 hours back
        new_cursor = max(0, current_block - rollback_blocks)
        
        log.warning(f"Initiating Self-Healing Wipe. Rolling cursor back to {new_cursor}...")
        
        # Wipe the last 24 hours of timeseries output
        ch.command(f"""
            ALTER TABLE unified_timeseries DELETE 
            WHERE protocol='{protocol}' AND timestamp >= now() - INTERVAL 25 HOUR
        """)
        
        # Purge mempool raw events
        # Mapping to deduce raw_table name
        RAW_TABLE_MAP = {
            "AAVE_MARKET": "aave_events",
            "FLUID_MARKET": "fluid_events",
            "MORPHO_MARKET": "morpho_events",
            # Add other mappings as necessary
        }
        raw_table = RAW_TABLE_MAP.get(protocol)
        if raw_table:
            log.warning(f"Purging mempool {raw_table} from block {new_cursor} onwards...")
            ch.command(f"ALTER TABLE {raw_table} DELETE WHERE block_number >= {new_cursor}")

        # Reset the processor cursor state
        ch.insert('processor_state', [[protocol, new_cursor]], column_names=['protocol', 'last_processed_block'])
        
        log.info("Self-healing triggered. The protocol processor will reconstruct state from the raw_events mempool on the next tick.")

if __name__ == "__main__":
    w = ShadowWatcher()
    w.check_aave_integrity()
