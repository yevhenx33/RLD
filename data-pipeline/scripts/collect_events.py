#!/usr/bin/env python3
"""
HyperSync Event Collector — Download raw events to Parquet.

Downloads all historical events for Aave V3 and Morpho Blue via HyperSync
and saves them as Parquet files for offline analysis.

Usage:
    python3 scripts/collect_events.py

Output:
    /mnt/data/hypersync_events/aave_v3_events.parquet
    /mnt/data/hypersync_events/morpho_blue_events.parquet
"""

import os, sys, time, asyncio
import pandas as pd
import hypersync
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from indexer.aave_constants import (
    AAVE_V3_POOL,
    AAVE_V3_DEPLOY_BLOCK,
    AAVE_TOPIC_RESERVE_DATA_UPDATED,
    AAVE_TOPIC_SUPPLY,
    AAVE_TOPIC_BORROW,
    AAVE_TOPIC_REPAY,
    AAVE_TOPIC_LIQUIDATION_CALL,
    AAVE_TOPIC_FLASH_LOAN,
)

ENVIO_TOKEN = os.getenv("ENVIO_API_TOKEN", "").strip()
OUTPUT_DIR = Path("/mnt/data/hypersync_events")
BATCH_SIZE = 1_000_000  # 1M blocks per query

# ── Protocol Definitions ────────────────────────────────────

PROTOCOLS = {
    "aave_v3": {
        "contract": AAVE_V3_POOL,
        "start_block": AAVE_V3_DEPLOY_BLOCK,
        "events": {
            "ReserveDataUpdated": AAVE_TOPIC_RESERVE_DATA_UPDATED,
            "Supply": AAVE_TOPIC_SUPPLY,
            "Borrow": AAVE_TOPIC_BORROW,
            "Repay": AAVE_TOPIC_REPAY,
            "LiquidationCall": AAVE_TOPIC_LIQUIDATION_CALL,
            "FlashLoan": AAVE_TOPIC_FLASH_LOAN,
        },
    },
    "morpho_blue": {
        "contract": "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb",
        "start_block": 18_883_124,
        "events": {
            "Supply":             "0xedf8870433c83823eb071d3df1caa8d008f12f6440918c20d75a3602cda30fe0",
            "Borrow":             "0x570954540bed6b1304a87dfe815a5eda4a648f7097a16240dcd85c9b5fd42a43",
            "Repay":              "0x52acb05cebbd3cd39715469f22afbf5a17496295ef3bc9bb5944056c63ccaa09",
            "Liquidate":          "0xa4946ede45d0c6f06a0f5ce92c9ad3b4751e2b26571a85b4b10571077571493f",
            "SupplyCollateral":   "0xa3b9472a1399e17e123f3c2e6586c23e36dadeb77fcfbeaaa7c91af3543f15d3",
            "WithdrawCollateral": "0xe80ebd7cc9223d7382aab2e0d1d6155c65651f83d53c8b9b06571e7480a87c62",
            "CreateMarket":       "0xac4b2400f169220b0c0afdde7a0b32e775ba727ea1cb30b35f935cdaab8683ac",
        },
    },
}

# Known event topic0 → name lookup (inverted from PROTOCOLS)
TOPIC0_NAME = {}
for proto in PROTOCOLS.values():
    for name, topic in proto["events"].items():
        TOPIC0_NAME[topic.lower()] = name


def parse_block_ts(b):
    """Parse block timestamp (handles hex strings)."""
    ts = b.timestamp
    if isinstance(ts, str):
        ts = int(ts, 16) if ts.startswith("0x") else int(ts)
    return ts


async def collect_protocol(client, name, config, head):
    """Download all events for a single protocol to a Parquet file."""
    contract = config["contract"]
    start = config["start_block"]
    topic0s = list(config["events"].values())

    print("\n" + "=" * 60, flush=True)
    print("  Collecting: %s" % name, flush=True)
    print("  Contract:   %s" % contract, flush=True)
    print("  Events:     %d types" % len(topic0s), flush=True)
    print("  Blocks:     %d -> %d (%d blocks)" % (start, head, head - start), flush=True)
    print("=" * 60, flush=True)

    all_rows = []
    block_ts_map = {}
    from_block = start
    total_t0 = time.time()

    while from_block < head:
        to_block = min(from_block + BATCH_SIZE, head)

        query = hypersync.Query(
            from_block=from_block,
            to_block=to_block,
            logs=[hypersync.LogSelection(
                address=[contract],
                topics=[topic0s],
            )],
            field_selection=hypersync.FieldSelection(
                log=[
                    hypersync.LogField.BLOCK_NUMBER,
                    hypersync.LogField.LOG_INDEX,
                    hypersync.LogField.TRANSACTION_HASH,
                    hypersync.LogField.ADDRESS,
                    hypersync.LogField.TOPIC0,
                    hypersync.LogField.TOPIC1,
                    hypersync.LogField.TOPIC2,
                    hypersync.LogField.TOPIC3,
                    hypersync.LogField.DATA,
                ],
                block=[hypersync.BlockField.NUMBER, hypersync.BlockField.TIMESTAMP],
            ),
        )

        t0 = time.time()
        try:
            res = await client.get(query)
        except Exception as e:
            print("  ERROR at block %d: %s" % (from_block, e), flush=True)
            from_block = to_block + 1
            continue

        elapsed = time.time() - t0

        # Block timestamps
        for b in res.data.blocks:
            if b.number is not None and b.timestamp is not None:
                block_ts_map[b.number] = parse_block_ts(b)

        # Flatten log entries to dicts
        for entry in res.data.logs:
            topics = entry.topics or []
            t0_hash = topics[0].lower() if topics else ""
            event_name = TOPIC0_NAME.get(t0_hash, "UNKNOWN")
            ts_unix = block_ts_map.get(entry.block_number, 0)

            all_rows.append({
                "block_number": entry.block_number,
                "block_timestamp": ts_unix,
                "log_index": entry.log_index or 0,
                "tx_hash": entry.transaction_hash or "",
                "contract": (entry.address or "").lower(),
                "event_name": event_name,
                "topic0": topics[0] if len(topics) > 0 else "",
                "topic1": topics[1] if len(topics) > 1 else "",
                "topic2": topics[2] if len(topics) > 2 else "",
                "topic3": topics[3] if len(topics) > 3 else "",
                "data": entry.data or "",
            })

        print("  %10d -> %10d: +%6d events (total=%8d, %.1fs)" % (
            from_block, to_block, len(res.data.logs), len(all_rows), elapsed), flush=True)
        from_block = to_block + 1

    total_elapsed = time.time() - total_t0

    # Save to Parquet
    if all_rows:
        df = pd.DataFrame(all_rows)
        df["block_timestamp"] = pd.to_datetime(df["block_timestamp"], unit="s", utc=True)

        outpath = OUTPUT_DIR / ("%s_events.parquet" % name)
        df.to_parquet(outpath, index=False, engine="pyarrow")

        print("\n  Saved: %s" % outpath, flush=True)
        print("  Rows:  %d" % len(df), flush=True)
        print("  Size:  %.1f MB" % (outpath.stat().st_size / 1e6), flush=True)
        print("  Time:  %.1fs" % total_elapsed, flush=True)

        # Summary
        print("\n  Event breakdown:", flush=True)
        for evt, count in df["event_name"].value_counts().items():
            print("    %-25s %8d" % (evt, count), flush=True)

        print("\n  Time range: %s -> %s" % (
            df["block_timestamp"].min(), df["block_timestamp"].max()), flush=True)
    else:
        print("  No events found!", flush=True)

    return len(all_rows)


async def main():
    print("HyperSync Event Collector", flush=True)
    print("Output: %s" % OUTPUT_DIR, flush=True)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not ENVIO_TOKEN:
        print("ERROR: ENVIO_API_TOKEN is required", flush=True)
        sys.exit(1)

    client = hypersync.HypersyncClient(hypersync.ClientConfig(
        url="https://eth.hypersync.xyz",
        bearer_token=ENVIO_TOKEN,
    ))
    head = await client.get_height()
    print("Chain head: %d" % head, flush=True)

    total = 0
    for name, config in PROTOCOLS.items():
        n = await collect_protocol(client, name, config, head)
        total += n

    print("\n" + "=" * 60, flush=True)
    print("  DONE. Total events collected: %d" % total, flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
