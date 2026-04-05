"""
Morpho Blue — Historical Backfill Script.

Collects hourly snapshots going back N days.
Run directly: PYTHONPATH=. python3 -m morpho.backfill --days 30
"""
import argparse, time, logging, sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from morpho.db import init_db, get_conn
from morpho.discovery import discover_markets_and_vaults
from morpho.collector import collect_snapshot
from morpho.rpc import eth_block_number, eth_get_block, rpc_request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

def find_block_for_timestamp(target_ts):
    """Binary search for the block closest to target_ts."""
    lo = 18_883_124  # Morpho Blue genesis
    hi = eth_block_number()

    # Quick bounds check
    hi_block = eth_get_block(hi)
    hi_ts = int(hi_block["timestamp"], 16)
    if target_ts >= hi_ts:
        return hi

    lo_block = eth_get_block(lo)
    lo_ts = int(lo_block["timestamp"], 16)
    if target_ts <= lo_ts:
        return lo

    # Binary search with ~12s block time heuristic
    for _ in range(30):
        if hi - lo <= 5:
            break
        # Estimate based on 12s blocks
        mid = lo + int((hi - lo) * (target_ts - lo_ts) / max(hi_ts - lo_ts, 1))
        mid = max(lo + 1, min(mid, hi - 1))

        mid_block = eth_get_block(mid)
        mid_ts = int(mid_block["timestamp"], 16)

        if mid_ts < target_ts:
            lo, lo_ts = mid, mid_ts
        else:
            hi, hi_ts = mid, mid_ts

    return lo

def backfill(days, step_hours=24):
    """Backfill historical snapshots."""
    init_db()
    discover_markets_and_vaults()

    now = int(time.time())
    start_ts = now - (days * 86400)
    # Align to hour boundary
    start_ts = (start_ts // 3600) * 3600
    current_ts = (now // 3600) * 3600

    # Check what we already have
    with get_conn() as conn:
        existing = set(r[0] for r in conn.execute(
            "SELECT DISTINCT timestamp FROM market_snapshots"
        ).fetchall())

    step_secs = step_hours * 3600
    timestamps = list(range(start_ts, current_ts, step_secs))
    to_collect = [ts for ts in timestamps if ts not in existing]

    log.info(f"Backfill: {days} days, {step_hours}h steps, {len(timestamps)} total, {len(to_collect)} needed")

    # Pre-resolve block numbers for all target timestamps
    log.info("Resolving block numbers...")
    latest_block = eth_block_number()

    for i, target_ts in enumerate(to_collect):
        try:
            block = find_block_for_timestamp(target_ts)
            log.info(f"  [{i+1}/{len(to_collect)}] ts={target_ts} ({time.strftime('%Y-%m-%d %H:%M', time.gmtime(target_ts))}) block={block}")
            collect_snapshot(block_number=block)
        except Exception as e:
            log.error(f"  Failed ts={target_ts}: {e}")
            time.sleep(2)
            continue

    # Final count
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(DISTINCT timestamp) FROM market_snapshots").fetchone()[0]
    log.info(f"Done. {count} unique timestamps in DB.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Morpho Blue Historical Backfill")
    parser.add_argument("--days", type=int, default=30, help="Days to backfill")
    parser.add_argument("--step", type=int, default=24, help="Hours between snapshots")
    args = parser.parse_args()
    backfill(args.days, args.step)
