"""
Morpho Blue — Fast Historical Backfill.

Optimized for large-scale collection:
- Pre-estimates block numbers (no binary search)
- Skips vault positions for historical (market + oracle + vault share prices only)
- Uses large RPC batch sizes
- Concurrent workers

Usage: PYTHONPATH=. python3 -m morpho.backfill_fast --start 2025-01-01 --workers 4
"""
import argparse, time, logging, sys, os, math
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from morpho.db import init_db, get_conn, get_tracked_markets, get_tracked_vaults
from morpho.rpc import rpc_batch, eth_block_number, eth_get_block, decode_uint

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(threadName)s] %(message)s")
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────
BATCH_SIZE = 500          # Alchemy supports up to 1000
SECONDS_PER_BLOCK = 12.05 # Ethereum avg block time

# Selectors (verified)
SEL_MARKET = "0x5c60e39a"
SEL_TOTAL_ASSETS = "0x01e1d114"
SEL_TOTAL_SUPPLY = "0x18160ddd"
SEL_PRICE = "0xa035b1fe"

# Morpho Blue
MORPHO = "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"

db_lock = Lock()

def estimate_block(target_ts, ref_block, ref_ts):
    """Estimate block number for a target timestamp using linear interpolation."""
    diff = target_ts - ref_ts
    block_diff = int(diff / SECONDS_PER_BLOCK)
    return max(1, ref_block + block_diff)

def encode_bytes32(hex_str):
    return hex_str.replace("0x", "").lower().zfill(64)

def collect_light_snapshot(block_number, snap_ts, market_ids, oracle_map, vault_addrs):
    """Collect market states + oracle prices + vault share prices. No vault positions."""
    block_hex = hex(block_number)

    # Build all calls in one list
    calls = []
    call_index = []  # track what each call is for

    # 1. Market states
    for mid in market_ids:
        data = SEL_MARKET + encode_bytes32(mid)
        calls.append(("eth_call", [{"to": MORPHO, "data": data}, block_hex]))
        call_index.append(("market", mid))

    # 2. Oracle prices
    oracles = list(set(v for v in oracle_map.values() if v))
    for oracle in oracles:
        calls.append(("eth_call", [{"to": oracle, "data": SEL_PRICE}, block_hex]))
        call_index.append(("oracle", oracle))

    # 3. Vault states (totalAssets + totalSupply)
    for va in vault_addrs:
        calls.append(("eth_call", [{"to": va, "data": SEL_TOTAL_ASSETS}, block_hex]))
        call_index.append(("vault_assets", va))
        calls.append(("eth_call", [{"to": va, "data": SEL_TOTAL_SUPPLY}, block_hex]))
        call_index.append(("vault_supply", va))

    # Execute in batches
    all_results = []
    for i in range(0, len(calls), BATCH_SIZE):
        batch = calls[i:i+BATCH_SIZE]
        try:
            results = rpc_batch(batch, retries=3)
            all_results.extend(results)
        except Exception as e:
            log.warning(f"Batch failed at offset {i}: {e}")
            all_results.extend([None] * len(batch))

    # Parse results
    market_states = {}
    oracle_prices = {}
    vault_states = {}

    for idx, (call_type, key) in enumerate(call_index):
        res = all_results[idx] if idx < len(all_results) else None
        if not res or res == "0x" or len(res.replace("0x", "")) < 64:
            continue
        try:
            if call_type == "market":
                market_states[key] = {
                    "totalSupplyAssets": decode_uint(res, 0),
                    "totalSupplyShares": decode_uint(res, 1),
                    "totalBorrowAssets": decode_uint(res, 2),
                    "totalBorrowShares": decode_uint(res, 3),
                    "lastUpdate": decode_uint(res, 4),
                    "fee": decode_uint(res, 5),
                }
            elif call_type == "oracle":
                oracle_prices[key] = decode_uint(res, 0)
            elif call_type == "vault_assets":
                vault_states.setdefault(key, {})["totalAssets"] = decode_uint(res, 0)
            elif call_type == "vault_supply":
                vault_states.setdefault(key, {})["totalSupply"] = decode_uint(res, 0)
        except Exception:
            pass

    # Store
    with db_lock:
        with get_conn() as conn:
            for mid in market_ids:
                ms = market_states.get(mid)
                if not ms:
                    continue
                supply = ms["totalSupplyAssets"]
                borrow = ms["totalBorrowAssets"]
                util = borrow / supply if supply > 0 else 0
                oracle_addr = oracle_map.get(mid)
                oracle_p = oracle_prices.get(oracle_addr) if oracle_addr else None

                conn.execute("""
                    INSERT OR IGNORE INTO market_snapshots
                    (timestamp, block_number, market_id,
                     total_supply_assets, total_borrow_assets,
                     total_supply_shares, total_borrow_shares,
                     last_update, fee, utilization, borrow_apy, supply_apy,
                     oracle_price, rate_at_target)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (snap_ts, block_number, mid,
                      str(supply), str(borrow),
                      str(ms["totalSupplyShares"]), str(ms["totalBorrowShares"]),
                      ms["lastUpdate"], ms["fee"],
                      util, None, None,
                      str(oracle_p) if oracle_p else None, None))

            for va in vault_addrs:
                vs = vault_states.get(va)
                if not vs or "totalAssets" not in vs:
                    continue
                ta = vs["totalAssets"]
                ts_shares = vs.get("totalSupply", 0)
                sp = ta / ts_shares if ts_shares > 0 else 0
                conn.execute("""
                    INSERT OR IGNORE INTO vault_snapshots
                    (timestamp, block_number, vault_address,
                     total_assets, total_supply, share_price, total_assets_usd)
                    VALUES (?,?,?,?,?,?,?)
                """, (snap_ts, block_number, va, str(ta), str(ts_shares), sp, None))

    return len(market_states), len(vault_states)

def process_timestamp(args):
    """Worker function for a single timestamp."""
    target_ts, ref_block, ref_ts, market_ids, oracle_map, vault_addrs = args
    snap_ts = (target_ts // 3600) * 3600
    block = estimate_block(target_ts, ref_block, ref_ts)

    try:
        n_markets, n_vaults = collect_light_snapshot(
            block, snap_ts, market_ids, oracle_map, vault_addrs
        )
        return snap_ts, block, n_markets, n_vaults, None
    except Exception as e:
        return snap_ts, block, 0, 0, str(e)

def run_backfill(start_date, end_date=None, workers=4):
    init_db()

    from morpho.discovery import discover_markets_and_vaults
    discover_markets_and_vaults()

    markets = get_tracked_markets()
    vaults = get_tracked_vaults()
    market_ids = [m["market_id"] for m in markets]
    vault_addrs = [v["vault_address"] for v in vaults]
    oracle_map = {m["market_id"]: m["oracle"] for m in markets}

    # Reference point for block estimation
    ref_block = eth_block_number()
    ref_block_data = eth_get_block(ref_block)
    ref_ts = int(ref_block_data["timestamp"], 16)

    start_ts = int(start_date.replace(tzinfo=timezone.utc).timestamp())
    end_ts = int(end_date.replace(tzinfo=timezone.utc).timestamp()) if end_date else ref_ts
    start_ts = (start_ts // 3600) * 3600
    end_ts = (end_ts // 3600) * 3600

    # Get existing timestamps
    with get_conn() as conn:
        existing = set(r[0] for r in conn.execute(
            "SELECT DISTINCT timestamp FROM market_snapshots"
        ).fetchall())

    all_ts = list(range(start_ts, end_ts, 3600))
    needed = [ts for ts in all_ts if ts not in existing]

    total_calls = len(needed) * (len(market_ids) + len(set(oracle_map.values())) + len(vault_addrs) * 2)
    log.info(f"Backfill: {len(all_ts)} total hours, {len(existing)} existing, {len(needed)} needed")
    log.info(f"  Markets: {len(market_ids)}, Vaults: {len(vault_addrs)}")
    log.info(f"  ~{total_calls:,} RPC calls, {total_calls // BATCH_SIZE + 1:,} batches")
    log.info(f"  Workers: {workers}")

    if not needed:
        log.info("Nothing to do!")
        return

    # Build args
    args_list = [
        (ts, ref_block, ref_ts, market_ids, oracle_map, vault_addrs)
        for ts in needed
    ]

    done = 0
    errors = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="w") as executor:
        futures = {executor.submit(process_timestamp, a): a[0] for a in args_list}
        for future in as_completed(futures):
            snap_ts, block, n_m, n_v, err = future.result()
            done += 1
            if err:
                errors += 1
                log.warning(f"  ERR ts={snap_ts}: {err}")
            if done % 50 == 0 or done == len(needed):
                elapsed = time.time() - t0
                rate = done / elapsed
                eta = (len(needed) - done) / rate if rate > 0 else 0
                log.info(f"  Progress: {done}/{len(needed)} ({done/len(needed)*100:.1f}%) "
                         f"rate={rate:.1f}/s  ETA={eta/60:.0f}m  errors={errors}")

    elapsed = time.time() - t0
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(DISTINCT timestamp) FROM market_snapshots").fetchone()[0]
    log.info(f"Done in {elapsed/60:.1f}m. {total} timestamps in DB. {errors} errors.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD (default: now)")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end, "%Y-%m-%d") if args.end else None
    run_backfill(start, end, args.workers)
