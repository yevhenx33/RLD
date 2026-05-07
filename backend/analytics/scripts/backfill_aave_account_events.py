#!/usr/bin/env python3
"""Bounded historical backfill for Aave account reconstruction events."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import sys

import hypersync

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from analytics.aave_accounts import (  # noqa: E402
    AAVE_V3_DEPLOY_BLOCK,
    AaveAccountSource,
    bootstrap_reserve_tokens_from_rpc,
    clickhouse_client_from_env,
    ensure_aave_account_tables,
)
from analytics.collector import BLOCK_FIELDS, LOG_FIELDS, build_block_ts_map, require_hypersync_token  # noqa: E402
from analytics.config import apply_env_from_config  # noqa: E402
from analytics.processor import SimulatedLog  # noqa: E402

apply_env_from_config()


async def collect_range(source: AaveAccountSource, ch, from_block: int, to_block: int, batch_blocks: int) -> int:
    client = hypersync.HypersyncClient(
        hypersync.ClientConfig(
            url="https://eth.hypersync.xyz",
            bearer_token=require_hypersync_token(),
        )
    )
    if to_block <= 0:
        to_block = int(await client.get_height()) - 3
    total = 0
    current = int(from_block)
    log_selection = source.log_selection()
    while current <= to_block:
        end = min(current + int(batch_blocks) - 1, to_block)
        cursor = current
        logs = []
        blocks = []
        while cursor <= end:
            query = hypersync.Query(
                from_block=cursor,
                to_block=end,
                logs=[log_selection],
                field_selection=hypersync.FieldSelection(log=LOG_FIELDS, block=BLOCK_FIELDS),
            )
            res = await client.get(query)
            logs.extend(res.data.logs)
            blocks.extend(res.data.blocks)
            if int(res.next_block) <= cursor:
                break
            cursor = int(res.next_block)
        matched = [entry for entry in logs if source.route(entry)]
        if matched:
            total += source.insert_raw(ch, matched, build_block_ts_map(blocks))
        print(f"collected blocks={current}->{end} raw_rows={len(matched)}")
        current = end + 1
    return total


def process_range(source: AaveAccountSource, ch, from_block: int, to_block: int, batch_blocks: int) -> int:
    total = 0
    current = int(from_block)
    while current <= to_block:
        end = min(current + int(batch_blocks) - 1, to_block)
        rows = ch.query(
            """
            SELECT block_number, block_timestamp, tx_hash, log_index, contract,
                   event_name, topic0, topic1, topic2, topic3, data
            FROM aave_account_raw_events
            WHERE block_number >= %(from_block)s AND block_number <= %(to_block)s
            ORDER BY block_number, log_index, contract
            """,
            parameters={"from_block": current, "to_block": end},
        ).result_rows
        block_ts_map = {row[0]: row[1] for row in rows}
        decoded = []
        for row in rows:
            item = source.decode(SimulatedLog(row), block_ts_map)
            if item:
                decoded.append(item)
        inserted = source.merge(ch, decoded) if decoded else 0
        total += inserted
        print(f"processed blocks={current}->{end} raw_rows={len(rows)} account_event_rows={inserted}")
        current = end + 1
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill Aave account raw and decoded events")
    parser.add_argument("--rpc-url", default=os.getenv("MAINNET_RPC_URL") or os.getenv("ETH_RPC_URL"))
    parser.add_argument("--from-block", type=int, default=AAVE_V3_DEPLOY_BLOCK)
    parser.add_argument("--to-block", type=int, default=0)
    parser.add_argument("--batch-blocks", type=int, default=50000)
    parser.add_argument("--skip-collect", action="store_true")
    parser.add_argument("--skip-process", action="store_true")
    parser.add_argument("--bootstrap-rpc", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    ch = clickhouse_client_from_env()
    ensure_aave_account_tables(ch)
    if args.bootstrap_rpc:
        if not args.rpc_url:
            raise RuntimeError("--rpc-url or MAINNET_RPC_URL is required to seed aToken/vToken contracts")
        bootstrap_reserve_tokens_from_rpc(ch, args.rpc_url)
    source = AaveAccountSource()
    source.get_cursor(ch)
    if len(source.contracts) <= 1:
        raise RuntimeError("aave_reserve_tokens is empty; seed reserve tokens before account-event backfill")

    to_block = int(args.to_block)
    if not args.skip_collect:
        asyncio.run(collect_range(source, ch, args.from_block, to_block, args.batch_blocks))
        if to_block <= 0:
            to_block = int(ch.command("SELECT max(block_number) FROM aave_account_raw_events") or 0)
    if not args.skip_process:
        if to_block <= 0:
            to_block = int(ch.command("SELECT max(block_number) FROM aave_account_raw_events") or 0)
        process_range(source, ch, args.from_block, to_block, args.batch_blocks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

