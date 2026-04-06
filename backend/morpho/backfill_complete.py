"""
Morpho Blue Complete Historical Backfill
Discovers market genesis blocks, determines active vault allocators,
and backfills hourly market state & vault positions using Multicall3 batching.
"""

import os
import sys
import time
import math
import logging
from concurrent.futures import ThreadPoolExecutor

from eth_abi import encode

# Adjust path if running locally
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.morpho.config import (
    MORPHO_BLUE, ADAPTIVE_CURVE_IRM, EVENT_CREATE_MARKET,
    SEL_MARKET, SEL_PRICE, SEL_RATE_AT_TARGET, SEL_POSITION
)
from backend.morpho.db import get_conn
import threading
db_lock = threading.Lock()

from backend.morpho.rpc import (
    eth_block_number, eth_get_logs, multicall3_try_aggregate,
    encode_bytes32, encode_address, decode_uint, decode_int, decode_address
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("backfill_complete")

# Constants
SECONDS_PER_BLOCK = 12
BLOCKS_PER_HOUR = 3600 // SECONDS_PER_BLOCK
RAY = 10**27
WAD = 10**18
SECONDS_PER_YEAR = 31536000

# Event: Supply(bytes32 indexed id, address caller, address indexed onBehalf, uint256 assets, uint256 shares)
EVENT_SUPPLY = "0xedf8870433c83823eb071d3df1caa8d008f12f6440918c20d75a3602cda30fe0"
MORPHO_GENESIS_BLOCK = 18_883_124

def parse_supply_on_behalf(log_entry):
    """Extract 'onBehalf' address from topic3 of Supply log."""
    topics = log_entry.get("topics", [])
    if len(topics) >= 3:
        return "0x" + topics[2].replace("0x", "")[-40:]
    return None

def compute_apy(rate_at_target_wad, utilization_wad, fee_wad):
    """Compute annualized borrow and supply APY."""
    if rate_at_target_wad is None or utilization_wad is None:
        return None, None
        
    try:
        # Borrow APY formula: exp(rateAtTarget * SECONDS_PER_YEAR) - 1
        borrow_apy = math.exp(rate_at_target_wad / WAD * SECONDS_PER_YEAR) - 1.0
        # Supply APY formula: borrow_apy * utilization * (1 - fee)
        supply_apy = borrow_apy * (utilization_wad / WAD) * (1.0 - (fee_wad / WAD))
        return borrow_apy, supply_apy
    except Exception:
        return None, None

def get_db_markets():
    with get_conn() as conn:
        rows = conn.execute("SELECT market_id, oracle, created_block FROM market_params").fetchall()
        return [{"market_id": r[0], "oracle": r[1], "created_block": r[2]} for r in rows]

def get_db_vaults():
    with get_conn() as conn:
        rows = conn.execute("SELECT vault_address FROM vault_meta").fetchall()
        return set(r[0].lower() for r in rows)

def get_already_indexed_timestamps(market_id):
    with get_conn() as conn:
        rows = conn.execute("SELECT timestamp FROM market_snapshots WHERE market_id = ?", (market_id,)).fetchall()
        return set(r[0] for r in rows)

def update_market_created_block(market_id, block):
    with db_lock:
        with get_conn() as conn:
            conn.execute("UPDATE market_params SET created_block = ? WHERE market_id = ?", (block, market_id))

def discover_market_params(market_id, all_vaults, head_block):
    """
    Find the market creation block and all vaults that have ever supplied to this market.
    """
    market_id_topic = "0x" + encode_bytes32(market_id)
    
    # 1. Discover creation block from CreateMarket event
    create_logs = eth_get_logs(MORPHO_GENESIS_BLOCK, "latest", MORPHO_BLUE, [EVENT_CREATE_MARKET, market_id_topic])
    if not create_logs:
        return None, []
        
    created_block = int(create_logs[0]["blockNumber"], 16)
    
    # 2. Discover active vaults from Supply events. Paginate by 500k blocks to avoid Alchemy 10K log cap 400 error.
    active_vaults = set()
    current_block = created_block
    chunk_size = 500_000
    
    while current_block <= head_block:
        to_block = min(current_block + chunk_size - 1, head_block)
        try:
            supply_logs = eth_get_logs(current_block, to_block, MORPHO_BLUE, [EVENT_SUPPLY, market_id_topic])
            for entry in supply_logs:
                on_behalf = parse_supply_on_behalf(entry)
                if on_behalf and on_behalf.lower() in all_vaults:
                    active_vaults.add(on_behalf.lower())
            current_block = to_block + 1
        except Exception as e:
            if chunk_size > 10000:
                chunk_size //= 2
                time.sleep(0.5)
            else:
                log.error(f"Cannot fetch logs even with small chunks around {current_block}: {e}")
                break
            
    return created_block, list(active_vaults)

def backfill_market(market, active_vaults, head_block, head_timestamp):
    market_id = market["market_id"]
    created_block = market["created_block"]
    oracle = market.get("oracle")
    
    indexed_ts = get_already_indexed_timestamps(market_id)
    inserts_market = []
    inserts_vault = []
    
    # Pre-parse base calldatas
    encoded_mid = encode_bytes32(market_id)
    call_market = (MORPHO_BLUE, SEL_MARKET + encoded_mid)
    call_oracle = (oracle, SEL_PRICE) if oracle and oracle != "0x0000000000000000000000000000000000000000" else None
    call_irm = (ADAPTIVE_CURVE_IRM, SEL_RATE_AT_TARGET + encoded_mid)
    
    call_positions = []
    for vault in active_vaults:
        call_positions.append((MORPHO_BLUE, SEL_POSITION + encoded_mid + encode_address(vault)))
        
    blocks_to_process = list(range(created_block, head_block, BLOCKS_PER_HOUR))
    if not blocks_to_process:
        return 0
        
    # Estimate timestamp for genesis block 
    blocks_diff = head_block - created_block
    genesis_ts = head_timestamp - (blocks_diff * SECONDS_PER_BLOCK)
    
    skipped = 0
    processed = 0
    
    for block in blocks_to_process:
        # Approximate timestamp
        block_diff = block - created_block
        snap_ts = genesis_ts + (block_diff * SECONDS_PER_BLOCK)
        
        if snap_ts in indexed_ts:
            skipped += 1
            continue
            
        calls = [call_market]
        if call_oracle: calls.append(call_oracle)
        calls.append(call_irm)
        calls.extend(call_positions)
        
        # Batch execute
        results = multicall3_try_aggregate(calls, hex(block))
        if not results or not results[0][0]:
            continue # Market call failed, skip
            
        res_idx = 0
        
        # 1. Market State
        succ_mkt, raw_mkt = results[res_idx]
        res_idx += 1
        if not succ_mkt or raw_mkt == "0x": continue
        
        supply_assets = decode_uint(raw_mkt, 0)
        supply_shares = decode_uint(raw_mkt, 1)
        borrow_assets = decode_uint(raw_mkt, 2)
        borrow_shares = decode_uint(raw_mkt, 3)
        last_update = decode_uint(raw_mkt, 4)
        fee = decode_uint(raw_mkt, 5)
        
        # Zero-supply filter
        if supply_assets == 0:
            continue
            
        utilization = borrow_assets / supply_assets if supply_assets > 0 else 0.0
        util_wad = int(utilization * WAD)
        
        # 2. Oracle Price
        oracle_price = None
        if call_oracle:
            succ_op, raw_op = results[res_idx]
            res_idx += 1
            if succ_op and raw_op != "0x" and len(raw_op) >= 66:
                oracle_price = decode_uint(raw_op, 0)
                
        # 3. IRM Rate
        succ_irm, raw_irm = results[res_idx]
        res_idx += 1
        rate_at_target = None
        if succ_irm and raw_irm != "0x" and len(raw_irm) >= 66:
            rate_at_target = decode_int(raw_irm, 0)
            
        # APY calculation
        borrow_apy, supply_apy = compute_apy(rate_at_target, util_wad, fee)
        
        inserts_market.append((
            snap_ts, block, market_id,
            str(supply_assets), str(borrow_assets),
            str(supply_shares), str(borrow_shares),
            last_update, fee, utilization, borrow_apy, supply_apy,
            str(oracle_price) if oracle_price is not None else None,
            rate_at_target
        ))
        
        # 4. Vault Positions
        for vault in active_vaults:
            succ_pos, raw_pos = results[res_idx]
            res_idx += 1
            if succ_pos and raw_pos != "0x":
                shares = decode_uint(raw_pos, 0)
                if shares > 0:
                    assets = (shares * supply_assets) // supply_shares if supply_shares > 0 else 0
                    inserts_vault.append((snap_ts, vault, market_id, str(assets), str(shares)))
        
        processed += 1
        if len(inserts_market) >= 50:
            flush_inserts(inserts_market, inserts_vault)
            inserts_market = []
            inserts_vault = []
            time.sleep(0.5) # Rate limit
            
    if inserts_market:
        flush_inserts(inserts_market, inserts_vault)
        
    return processed

def flush_inserts(markets, vaults):
    with db_lock:
        with get_conn() as conn:
            conn.executemany("""
                INSERT OR IGNORE INTO market_snapshots
                (timestamp, block_number, market_id,
                 total_supply_assets, total_borrow_assets,
                 total_supply_shares, total_borrow_shares,
                 last_update, fee, utilization, borrow_apy, supply_apy,
                 oracle_price, rate_at_target)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, markets)
            
            if vaults:
                conn.executemany("""
                    INSERT OR IGNORE INTO vault_allocations
                    (timestamp, vault_address, market_id, supply_assets, supply_shares)
                    VALUES (?,?,?,?,?)
                """, vaults)
            conn.commit()

def process_single_market(market, all_vaults, head_block, head_timestamp):
    market_id = market["market_id"]
    try:
        # Phase 0: Discovery
        if not market.get("created_block"):
            cb, active_vaults = discover_market_params(market_id, all_vaults, head_block)
            if not cb:
                log.warning(f"Could not find created_block for {market_id[:10]}")
                return
            market["created_block"] = cb
            update_market_created_block(market_id, cb)
        else:
            cb, active_vaults = discover_market_params(market_id, all_vaults, head_block)
            
        # Phase 1: Backfill
        log.info(f"[{market_id[:10]}] Backfilling from {market['created_block']}. Active Vaults: {len(active_vaults)}")
        processed = backfill_market(market, active_vaults, head_block, head_timestamp)
        log.info(f"[{market_id[:10]}] Done. Inserted {processed} snapshot(s).")
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        log.error(f"Error processing market {market_id[:10]}: {e}")

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-limit", type=int, help="Limit number of markets to process")
    parser.add_argument("--workers", type=int, default=4, help="Number of concurrent workers")
    parser.add_argument("--days", type=int, help="Limit backfill to the last N days")
    args = parser.parse_args()
    
    log.info("Starting historical complete backfill")
    head_block = eth_block_number()
    from backend.morpho.rpc import eth_get_block
    head_timestamp = int(eth_get_block(head_block)["timestamp"], 16)
    
    all_vaults = get_db_vaults()
    markets = get_db_markets()
    
    if args.days:
        cutoff_block = head_block - (args.days * 24 * 3600 // SECONDS_PER_BLOCK)
        for m in markets:
            if m["created_block"] is None or m["created_block"] < cutoff_block:
                m["created_block"] = cutoff_block
                
    if args.market_limit:
        markets = markets[:args.market_limit]
        
    log.info(f"Processing {len(markets)} markets with {args.workers} workers...")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for m in markets:
            pool.submit(process_single_market, m, all_vaults, head_block, head_timestamp)

if __name__ == "__main__":
    main()
