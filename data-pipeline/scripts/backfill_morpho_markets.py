#!/usr/bin/env python3
import os, sys, logging, datetime
import pandas as pd
import clickhouse_connect

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from indexer.sources.morpho import MorphoSource, MarketState
from indexer.tokens import SYM_DECIMALS, get_usd_price, get_chainlink_prices

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("backfill-morpho-markets")

CH_HOST = os.getenv("CLICKHOUSE_HOST", "localhost")
CH_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))

class DummyLogEntry:
    def __init__(self, topics, data, block_number):
        self.topics = topics
        self.data = data
        self.block_number = block_number

def main():
    ch = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT)
    ms = MorphoSource()
    # 1. Load symbol mapping
    ms._load_symbols(ch)

    # 2. Get the latest state BEFORE the gap (e.g. up to Apr 6 08:00)
    cutoff = "2026-04-06 08:00:00"
    latest = ch.query_df(f"""
        SELECT entity_id,
               argMax(symbol, timestamp) AS sym,
               argMax(supply_usd, timestamp) AS supply_usd,
               argMax(borrow_usd, timestamp) AS borrow_usd
        FROM unified_timeseries
        WHERE protocol = 'MORPHO_MARKET'
          AND timestamp <= '{cutoff}'
        GROUP BY entity_id
        HAVING supply_usd > 0
    """)
    eth_price, btc_price = get_chainlink_prices(ch)

    for _, row in latest.iterrows():
        eid = row["entity_id"]
        sym = row["sym"]
        decimals = SYM_DECIMALS.get(sym, 18)
        tp = get_usd_price(sym, eth_price, btc_price)
        if tp <= 0: tp = 1.0
        state = ms._ensure_market(eid)
        state.total_supply_assets = int(row["supply_usd"] / tp * (10 ** decimals))
        state.total_borrow_assets = int(row["borrow_usd"] / tp * (10 ** decimals))
        state.loan_symbol = sym
        state.loan_decimals = decimals

    log.info(f"Pre-seeded {len(latest)} markets from before {cutoff}")

    # 3. Load all events from gap start to present
    events_df = ch.query_df(f"""
        SELECT block_number, block_timestamp, topic0, topic1, topic2, topic3, data
        FROM morpho_events
        WHERE block_timestamp > '{cutoff}'
        ORDER BY block_number, log_index
    """)
    log.info(f"Loaded {len(events_df)} events for replay")

    # 4. Replay events
    snapshots = []
    block_ts_map = {}
    
    # Pre-populate block_ts_map since we have it
    for _, ev in events_df.drop_duplicates('block_number').iterrows():
        block_ts_map[ev["block_number"]] = ev["block_timestamp"]

    for i, ev in events_df.iterrows():
        topics = [t for t in [ev["topic0"], ev["topic1"], ev["topic2"], ev["topic3"]] if t]
        log_entry = DummyLogEntry(topics, ev["data"], ev["block_number"])
        
        snap = ms.decode(log_entry, block_ts_map)
        if snap:
            snapshots.append(snap)
            
    log.info(f"Produced {len(snapshots)} snapshots during replay")

    # Group snapshots by day or process them all at once
    # If we process all at once, merge() will take argMax per hour per entity, and then forward fill properly.
    if snapshots:
        # We need to temporarily disable the vault processing in merge() so we don't duplicate allocation backfill
        ms._vault_addrs = set() 
        ms._vault_positions = {}
        
        # Merge handles everything!
        merged_count = ms.merge(ch, snapshots)
        log.info(f"Merged {merged_count} MORPHO_MARKET rows to unified_timeseries")

if __name__ == "__main__":
    main()
