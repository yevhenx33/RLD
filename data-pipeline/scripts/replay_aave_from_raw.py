#!/usr/bin/env python3
"""
One-shot Aave replay from raw ClickHouse events.

This script reprocesses `aave_events` into `aave_timeseries` by running the
existing deterministic Aave processor once from a chosen block boundary.
"""

import argparse
import logging
import os

import clickhouse_connect

from indexer.processor import ProtocolProcessor
from indexer.sources.aave_v3 import AaveV3Source


def build_client():
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "127.0.0.1"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
    )


def main():
    parser = argparse.ArgumentParser(description="Replay Aave from raw events once")
    parser.add_argument(
        "--from-block",
        type=int,
        default=None,
        help="Override replay start block (inclusive). Default uses Aave genesis anchor.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger("replay-aave")

    source = AaveV3Source()
    processor = ProtocolProcessor(
        source,
        clickhouse_host=os.getenv("CLICKHOUSE_HOST", "127.0.0.1"),
        clickhouse_port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
    )
    ch = build_client()

    raw_head_raw = ch.command("SELECT max(block_number) FROM aave_events")
    raw_head = int(raw_head_raw) if raw_head_raw not in (None, "", "None") else 0
    if raw_head == 0:
        raise RuntimeError("aave_events is empty; cannot replay.")

    start_block = args.from_block if args.from_block is not None else source.genesis_block
    if start_block < source.genesis_block:
        start_block = source.genesis_block

    processor.set_last_processed_block(ch, max(0, start_block - 1))
    log.info(
        "[AAVE_REPLAY] Starting replay from block %s up to raw head %s",
        start_block,
        raw_head,
    )

    processor.run_processor_cycle()

    ts_rows = ch.command("SELECT count() FROM aave_timeseries")
    ts_max = ch.command("SELECT max(timestamp) FROM aave_timeseries")
    proc_head = ch.command(
        "SELECT max(last_processed_block) FROM processor_state WHERE protocol = 'AAVE_MARKET'"
    )
    log.info(
        "[AAVE_REPLAY] Completed. rows=%s max_timestamp=%s processor_head=%s",
        ts_rows,
        ts_max,
        proc_head,
    )


if __name__ == "__main__":
    main()
