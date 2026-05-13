#!/usr/bin/env python3
"""Repair Morpho Blue raw-log gaps and rebuild latest event-sourced state.

This tool is intentionally scoped to Morpho tables. It does not touch Aave
workers, Aave tables, or shared processor cursors for other protocols.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import pathlib
import sys
import uuid
import time
from dataclasses import dataclass
from typing import Iterable

import clickhouse_connect
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from analytics.sources.morpho import EVENT_MAP, MORPHO_BLUE, MorphoSource  # noqa: E402
from analytics.state import update_source_status  # noqa: E402


TOPICS = sorted(EVENT_MAP.keys())
MARKET_SELECTOR = "5c60e39a"


@dataclass
class RawLog:
    block_number: int
    block_timestamp: dt.datetime
    transaction_hash: str
    log_index: int
    address: str
    topics: list[str]
    data: str


class SimulatedLog:
    def __init__(self, row):
        self.block_number = int(row[0])
        self.transaction_hash = str(row[2] or "")
        self.log_index = int(row[3] or 0)
        self.address = str(row[4] or "")
        self.topics = [t for t in [row[6], row[7], row[8], row[9]] if t]
        self.data = str(row[10] or "")


def load_env() -> None:
    root = pathlib.Path(__file__).resolve().parents[3]
    for env_path in (root / "backend/analytics/.env", root / "docker/.env"):
        if not env_path.exists():
            continue
        for raw in env_path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def clickhouse_client():
    settings = {}
    if os.getenv("CLICKHOUSE_ASYNC_INSERT", "true").strip().lower() in {"1", "true", "yes"}:
        settings["async_insert"] = 1
        settings["wait_for_async_insert"] = (
            1 if os.getenv("CLICKHOUSE_WAIT_FOR_ASYNC_INSERT", "true").strip().lower() in {"1", "true", "yes"} else 0
        )
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "127.0.0.1"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
        settings=settings,
    )


def ensure_anchor_tables(ch) -> None:
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS morpho_rpc_anchor_runs (
            run_id String,
            started_at DateTime,
            finished_at DateTime,
            block_number UInt64,
            checked_markets UInt64,
            drifted_markets UInt64,
            missing_recent_logs UInt64,
            max_abs_supply_diff String,
            max_abs_borrow_diff String,
            max_rel_supply_diff Float64,
            max_rel_borrow_diff Float64,
            status LowCardinality(String),
            error String,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        ORDER BY run_id
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS morpho_rpc_anchor_diffs (
            run_id String,
            block_number UInt64,
            market_id String,
            field LowCardinality(String),
            db_value String,
            rpc_value String,
            abs_diff String,
            rel_diff Float64,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        ORDER BY (run_id, market_id, field)
        """
    )


def rpc_url(explicit: str | None = None) -> str:
    value = explicit or os.getenv("MAINNET_RPC_URL") or os.getenv("ETH_RPC_URL") or os.getenv("RPC_URL")
    if not value:
        raise RuntimeError("MAINNET_RPC_URL, ETH_RPC_URL, or RPC_URL is required")
    return value


def sanitize_error(exc: Exception | str) -> str:
    message = str(exc)
    for key in ("MAINNET_RPC_URL", "ETH_RPC_URL", "RPC_URL", "RESERVE_RPC_URL"):
        value = os.getenv(key)
        if value:
            message = message.replace(value, "[redacted-rpc-url]")
    return message


def rpc_call(url: str, method: str, params: list, timeout: int = 30, retries: int = 3):
    last_exc: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            response = requests.post(
                url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if "error" in payload:
                raise RuntimeError(f"{method} failed: {payload['error']}")
            return payload["result"]
        except Exception as exc:
            last_exc = exc
            if attempt + 1 < max(1, retries):
                time.sleep(0.5 * (attempt + 1))
    raise last_exc if last_exc else RuntimeError(f"{method} failed")


def block_timestamp(url: str, block_number: int, cache: dict[int, dt.datetime]) -> dt.datetime:
    if block_number not in cache:
        block = rpc_call(url, "eth_getBlockByNumber", [hex(block_number), False])
        cache[block_number] = dt.datetime.fromtimestamp(int(block["timestamp"], 16), tz=dt.timezone.utc).replace(tzinfo=None)
    return cache[block_number]


def fetch_morpho_logs(url: str, start: int, end: int) -> list[dict]:
    return rpc_call(
        url,
        "eth_getLogs",
        [
            {
                "address": MORPHO_BLUE,
                "fromBlock": hex(start),
                "toBlock": hex(end),
                "topics": [TOPICS],
            }
        ],
        timeout=60,
    )


def existing_keys(ch, start: int, end: int) -> set[tuple[int, str, int]]:
    rows = ch.query(
        f"""
        SELECT block_number, lower(tx_hash), log_index
        FROM morpho_events
        WHERE block_number >= {int(start)} AND block_number <= {int(end)}
        """
    ).result_rows
    return {(int(block), str(tx).lower(), int(log_index)) for block, tx, log_index in rows}


def to_raw_rows(url: str, logs: Iterable[dict], ts_cache: dict[int, dt.datetime]) -> list[list]:
    rows = []
    for log in logs:
        block = int(log["blockNumber"], 16)
        topics = [str(topic).lower() for topic in log.get("topics", [])]
        topic0 = topics[0] if topics else ""
        rows.append(
            [
                block,
                block_timestamp(url, block, ts_cache),
                str(log.get("transactionHash", "")).lower(),
                int(log.get("logIndex", "0x0"), 16),
                str(log.get("address", "")).lower(),
                EVENT_MAP.get(topic0, ""),
                topic0,
                topics[1] if len(topics) > 1 else None,
                topics[2] if len(topics) > 2 else None,
                topics[3] if len(topics) > 3 else None,
                str(log.get("data", "0x")),
            ]
        )
    return rows


def repair_logs(args) -> dict[str, int]:
    url = rpc_url(args.rpc_url)
    ch = clickhouse_client()
    inserted = 0
    checked = 0
    missing = 0
    ts_cache: dict[int, dt.datetime] = {}
    try:
        for start in range(args.from_block, args.to_block + 1, args.batch_blocks):
            end = min(start + args.batch_blocks - 1, args.to_block)
            rpc_logs = fetch_morpho_logs(url, start, end)
            checked += len(rpc_logs)
            known = existing_keys(ch, start, end)
            gap_logs = [
                log
                for log in rpc_logs
                if (
                    int(log["blockNumber"], 16),
                    str(log.get("transactionHash", "")).lower(),
                    int(log.get("logIndex", "0x0"), 16),
                )
                not in known
            ]
            missing += len(gap_logs)
            if gap_logs and not args.dry_run:
                rows = to_raw_rows(url, gap_logs, ts_cache)
                ch.insert(
                    "morpho_events",
                    rows,
                    column_names=[
                        "block_number",
                        "block_timestamp",
                        "tx_hash",
                        "log_index",
                        "contract",
                        "event_name",
                        "topic0",
                        "topic1",
                        "topic2",
                        "topic3",
                        "data",
                    ],
                )
                inserted += len(rows)
            print(json.dumps({"range": [start, end], "rpcLogs": len(rpc_logs), "missing": len(gap_logs)}), flush=True)
            time.sleep(args.sleep_sec)
    finally:
        ch.close()
    return {"checked": checked, "missing": missing, "inserted": inserted}


def rebuild_latest_state(args) -> dict[str, int]:
    ch = clickhouse_client()
    source = MorphoSource()
    try:
        source._ensure_tables(ch)
        source._load_available_feeds(ch)
        source._load_shared_price_assets(ch)
        source._load_pendle_address_map(ch)
        max_block = args.to_block or int(ch.command("SELECT max(block_number) FROM morpho_events") or 0)
        min_block = args.from_block or source.genesis_block
        decoded = 0
        for start in range(min_block, max_block + 1, args.replay_batch_blocks):
            end = min(start + args.replay_batch_blocks - 1, max_block)
            rows = ch.query(
                f"""
                SELECT block_number, block_timestamp, tx_hash, log_index, contract,
                       event_name, topic0, topic1, topic2, topic3, data
                FROM morpho_events
                WHERE block_number >= {int(start)} AND block_number <= {int(end)}
                ORDER BY block_number ASC, log_index ASC
                """
            ).result_rows
            block_ts = {int(row[0]): row[1] for row in rows}
            for row in rows:
                event = source.decode(SimulatedLog(row), block_ts)
                if event:
                    decoded += 1
            source._event_facts.clear()
            print(json.dumps({"replayRange": [start, end], "rawRows": len(rows), "decodedRows": decoded}), flush=True)
        if args.dry_run:
            return {"decoded": decoded, "markets": len(source._markets), "positions": len(source._positions)}

        source._persist_params(ch, list(source._params))
        source._persist_history(ch)
        source._touched_markets = set(source._markets)
        source._touched_positions = set(source._positions)
        source._persist_state(ch)
        source._persist_positions(ch)
        latest_ts = ch.query(
            f"SELECT max(block_timestamp) FROM morpho_events WHERE block_number <= {int(max_block)}"
        ).result_rows[0][0]
        source._write_snapshots(
            ch,
            [
                {
                    "kind": "snapshot",
                    "market_id": next(iter(source._markets), ""),
                    "block_number": max_block,
                    "timestamp": latest_ts or dt.datetime.utcnow(),
                }
            ],
        )
        return {"decoded": decoded, "markets": len(source._markets), "positions": len(source._positions)}
    finally:
        ch.close()


def clear_derived_history(ch) -> None:
    for table in (
        "morpho_market_params",
        "morpho_market_state",
        "morpho_market_state_history",
        "morpho_market_positions",
        "morpho_market_position_history",
        "morpho_market_events",
        "morpho_market_oracle_support",
        "morpho_market_metrics",
        "morpho_chainlink_timeseries",
    ):
        ch.command(f"TRUNCATE TABLE {table}")
    for table in (
        "oracle_dependency_edges",
        "oracle_dependency_exposure_latest",
    ):
        ch.command(f"ALTER TABLE {table} DELETE WHERE protocol = 'MORPHO_MARKET' SETTINGS mutations_sync = 2")
    for table in (
        "market_timeseries",
        "api_market_latest",
        "api_market_timeseries_hourly_agg",
    ):
        ch.command(f"ALTER TABLE {table} DELETE WHERE protocol = 'MORPHO_MARKET' SETTINGS mutations_sync = 2")
    ch.command("ALTER TABLE api_protocol_tvl_entity_weekly_agg DELETE WHERE protocol = 'MORPHO' SETTINGS mutations_sync = 2")


def rebuild_historical_timeseries(args) -> dict[str, int]:
    ch = clickhouse_client()
    source = MorphoSource()
    try:
        source._ensure_tables(ch)
        source._load_available_feeds(ch)
        source._load_shared_price_assets(ch)
        source._load_pendle_address_map(ch)
        bounds = ch.query(
            """
            SELECT min(block_number), max(block_number), count()
            FROM morpho_events
            """
        ).result_rows[0]
        min_block = int(args.from_block or bounds[0] or source.genesis_block)
        max_block = int(args.to_block or bounds[1] or 0)
        raw_events = int(bounds[2] or 0)
        if max_block <= 0:
            return {"raw_events": raw_events, "decoded": 0, "timeseries_rows": 0, "markets": 0, "positions": 0}
        if args.max_markets:
            raise SystemExit("--max-markets is not supported for --rebuild-history; history replay must stay complete")

        if not args.dry_run:
            clear_derived_history(ch)

        decoded_total = 0
        timeseries_total = 0
        for start in range(min_block, max_block + 1, int(args.replay_batch_blocks or 50_000)):
            end = min(start + int(args.replay_batch_blocks or 50_000) - 1, max_block)
            rows = ch.query(
                f"""
                SELECT block_number, block_timestamp, tx_hash, log_index, contract,
                       event_name, topic0, topic1, topic2, topic3, data
                FROM morpho_events
                WHERE block_number >= {int(start)} AND block_number <= {int(end)}
                ORDER BY block_number ASC, log_index ASC
                """
            ).result_rows
            block_ts = {int(row[0]): row[1] for row in rows}
            decoded_rows = []
            for row in rows:
                event = source.decode(SimulatedLog(row), block_ts)
                if event:
                    decoded_rows.append(event)
            decoded_total += len(decoded_rows)
            written = 0
            if decoded_rows and not args.dry_run:
                written = source.merge(ch, decoded_rows)
                timeseries_total += int(written or 0)
            elif decoded_rows:
                source._event_facts.clear()
            print(
                json.dumps(
                    {
                        "replayRange": [start, end],
                        "rawRows": len(rows),
                        "decodedRows": len(decoded_rows),
                        "timeseriesRows": written,
                        "markets": len(source._markets),
                        "positions": len(source._positions),
                    }
                ),
                flush=True,
            )
        if not args.dry_run:
            latest_ts = ch.query(
                f"SELECT max(block_timestamp) FROM morpho_events WHERE block_number <= {int(max_block)}"
            ).result_rows[0][0]
            ch.insert(
                "processor_state",
                [[source.name, int(max_block)]],
                column_names=["protocol", "last_processed_block"],
            )
            update_source_status(
                ch,
                source.name,
                "processor",
                last_processed_block=int(max_block),
                last_event_block=int(max_block),
                last_data_timestamp=latest_ts,
            )
        return {
            "raw_events": raw_events,
            "decoded": decoded_total,
            "timeseries_rows": timeseries_total,
            "markets": len(source._markets),
            "positions": len(source._positions),
            "from_block": min_block,
            "to_block": max_block,
        }
    finally:
        ch.close()


def rewind_after_block(args) -> dict[str, object]:
    block = int(args.rewind_after_block or 0)
    if block <= 0:
        raise SystemExit("--rewind-after-block must be positive")
    ch = clickhouse_client()
    try:
        cutoff = ch.query(
            f"SELECT toStartOfHour(max(block_timestamp)) FROM morpho_events WHERE block_number <= {block}"
        ).result_rows[0][0]
        market_topics = ", ".join(f"'{topic}'" for topic in TOPICS)
        touched = ch.query(
            f"""
            SELECT countDistinct(topic1)
            FROM morpho_events
            WHERE block_number > {block}
              AND topic0 IN ({market_topics})
              AND topic1 IS NOT NULL
              AND topic1 != ''
            """
        ).result_rows[0][0]
        touched_markets = f"""
            SELECT DISTINCT topic1
            FROM morpho_events
            WHERE block_number > {block}
              AND topic0 IN ({market_topics})
              AND topic1 IS NOT NULL
              AND topic1 != ''
        """
        commands = [
            f"ALTER TABLE morpho_market_state DELETE WHERE last_event_block > {block} SETTINGS mutations_sync = 2",
            f"ALTER TABLE morpho_market_state_history DELETE WHERE last_event_block > {block} SETTINGS mutations_sync = 2",
            f"ALTER TABLE morpho_market_positions DELETE WHERE last_event_block > {block} SETTINGS mutations_sync = 2",
            f"ALTER TABLE morpho_market_position_history DELETE WHERE last_event_block > {block} SETTINGS mutations_sync = 2",
            f"ALTER TABLE morpho_market_events DELETE WHERE block_number > {block} SETTINGS mutations_sync = 2",
            f"""
            ALTER TABLE morpho_market_metrics
            DELETE WHERE market_id IN ({touched_markets})
              AND timestamp >= toDateTime({int(cutoff.timestamp())})
            SETTINGS mutations_sync = 2
            """,
            f"""
            ALTER TABLE morpho_chainlink_timeseries
            DELETE WHERE entity_id IN ({touched_markets})
              AND timestamp >= toDateTime({int(cutoff.timestamp())})
            SETTINGS mutations_sync = 2
            """,
            f"""
            ALTER TABLE market_timeseries
            DELETE WHERE protocol = 'MORPHO_MARKET'
              AND entity_id IN ({touched_markets})
              AND timestamp >= toDateTime({int(cutoff.timestamp())})
            SETTINGS mutations_sync = 2
            """,
            f"""
            ALTER TABLE api_market_latest
            DELETE WHERE protocol = 'MORPHO_MARKET'
              AND entity_id IN ({touched_markets})
            SETTINGS mutations_sync = 2
            """,
            f"""
            ALTER TABLE api_market_timeseries_hourly_agg
            DELETE WHERE protocol = 'MORPHO_MARKET'
              AND entity_id IN ({touched_markets})
              AND ts >= toDateTime({int(cutoff.timestamp())})
            SETTINGS mutations_sync = 2
            """,
            f"""
            ALTER TABLE api_protocol_tvl_entity_weekly_agg
            DELETE WHERE protocol = 'MORPHO'
              AND entity_id IN ({touched_markets})
              AND day >= toStartOfWeek(toDateTime({int(cutoff.timestamp())}))
            SETTINGS mutations_sync = 2
            """,
        ]
        if not args.dry_run:
            for command in commands:
                ch.command(command)
            ch.insert(
                "processor_state",
                [[MorphoSource.name, block]],
                column_names=["protocol", "last_processed_block"],
            )
            update_source_status(
                ch,
                MorphoSource.name,
                "processor",
                last_processed_block=block,
                last_event_block=block,
                last_data_timestamp=cutoff,
            )
        return {
            "rewound_after_block": block,
            "cutoff_hour": cutoff.isoformat(sep=" "),
            "touched_markets": int(touched or 0),
            "dry_run": bool(args.dry_run),
        }
    finally:
        ch.close()


def sync_rpc_market_state(args) -> dict[str, int]:
    url = rpc_url(args.rpc_url)
    ch = clickhouse_client()
    source = MorphoSource()
    try:
        source._ensure_tables(ch)
        source._load_available_feeds(ch)
        source._load_shared_price_assets(ch)
        block = args.to_block
        block_ts = block_timestamp(url, block, {})
        market_rows = ch.query(
            """
            SELECT market_id
            FROM morpho_market_params
            GROUP BY market_id
            ORDER BY market_id
            """
        ).result_rows
        market_ids = [str(row[0]).lower() for row in market_rows if row and row[0]]
        if args.max_markets:
            market_ids = market_ids[: args.max_markets]
        rate_rows = ch.query(
            """
            SELECT
                market_id,
                argMax(last_borrow_rate_wad, (last_event_block, updated_at)),
                argMax(collateral_assets, (last_event_block, updated_at))
            FROM morpho_market_state
            GROUP BY market_id
            """
        ).result_rows
        borrow_rates = {str(market_id).lower(): str(rate or "0") for market_id, rate, _collateral in rate_rows}
        collateral_assets = {str(market_id).lower(): str(collateral or "0") for market_id, _rate, collateral in rate_rows}
        rows = []
        synced = 0
        errors = 0
        for market_id in market_ids:
            data = "0x" + MARKET_SELECTOR + market_id.removeprefix("0x")
            try:
                result = rpc_call(url, "eth_call", [{"to": MORPHO_BLUE, "data": data}, hex(block)], timeout=60, retries=5)
            except Exception as exc:
                errors += 1
                print(json.dumps({"rpcSyncError": market_id, "error": sanitize_error(exc)}), flush=True)
                continue
            words = result.removeprefix("0x")
            if len(words) < 64 * 6:
                errors += 1
                continue
            values = [int(words[i : i + 64], 16) for i in range(0, 64 * 6, 64)]
            last_update = dt.datetime.fromtimestamp(values[4], tz=dt.timezone.utc).replace(tzinfo=None) if values[4] else dt.datetime(1970, 1, 1)
            rows.append(
                [
                    market_id,
                    str(values[0]),
                    str(values[1]),
                    str(values[2]),
                    str(values[3]),
                    collateral_assets.get(market_id, "0"),
                    str(values[5]),
                    borrow_rates.get(market_id, "0"),
                    last_update,
                    block,
                    block_ts,
                ]
            )
            synced += 1
            if len(rows) >= 500:
                if not args.dry_run:
                    ch.insert(
                        "morpho_market_state",
                        rows,
                        column_names=[
                            "market_id",
                            "total_supply_assets",
                            "total_supply_shares",
                            "total_borrow_assets",
                            "total_borrow_shares",
                            "collateral_assets",
                            "fee_wad",
                            "last_borrow_rate_wad",
                            "last_update_timestamp",
                            "last_event_block",
                            "last_event_timestamp",
                        ],
                    )
                rows.clear()
        if rows and not args.dry_run:
            ch.insert(
                "morpho_market_state",
                rows,
                column_names=[
                    "market_id",
                    "total_supply_assets",
                    "total_supply_shares",
                    "total_borrow_assets",
                    "total_borrow_shares",
                    "collateral_assets",
                    "fee_wad",
                    "last_borrow_rate_wad",
                    "last_update_timestamp",
                    "last_event_block",
                    "last_event_timestamp",
                ],
            )
        if not args.dry_run:
            source._load_params(ch)
            source._load_state(ch)
            source._write_snapshots(
                ch,
                [
                    {
                        "kind": "snapshot",
                        "market_id": market_ids[0] if market_ids else "",
                        "block_number": block,
                        "timestamp": block_ts,
                    }
                ],
            )
        return {"rpcSyncedMarkets": synced, "rpcSyncErrors": errors, "rpcSyncBlock": block}
    finally:
        ch.close()


def latest_processed_block(ch) -> int:
    value = ch.command("SELECT max(last_processed_block) FROM processor_state WHERE protocol='MORPHO_MARKET'")
    return int(value or 0)


def latest_raw_block(ch) -> int:
    value = ch.command("SELECT max(block_number) FROM morpho_events")
    return int(value or 0)


def latest_rpc_block(url: str) -> int:
    return int(rpc_call(url, "eth_blockNumber", []), 16)


def db_market_state(ch) -> dict[str, tuple[int, int]]:
    rows = ch.query(
        """
        SELECT market_id,
               argMax(total_supply_assets, (last_event_block, updated_at)),
               argMax(total_borrow_assets, (last_event_block, updated_at))
        FROM morpho_market_state
        GROUP BY market_id
        """
    ).result_rows
    return {
        str(market_id).lower(): (int(supply or 0), int(borrow or 0))
        for market_id, supply, borrow in rows
    }


def anchor_block(args, ch, url: str) -> int:
    if args.block_number:
        return int(args.block_number)
    mode = str(args.block_mode or "processed").lower()
    if mode == "processed":
        block = latest_processed_block(ch)
        if block <= 0:
            block = latest_raw_block(ch)
        return block
    if mode == "latest":
        return max(0, latest_rpc_block(url) - int(args.confirmations or 0))
    raise ValueError(f"unsupported block mode: {args.block_mode}")


def count_recent_missing_logs(args, url: str, ch, block: int) -> int:
    window = max(0, int(args.gap_audit_blocks or 0))
    if window <= 0 or block <= 0:
        return 0
    start = max(0, block - window + 1)
    missing = 0
    for range_start in range(start, block + 1, int(args.gap_batch_blocks or 500)):
        range_end = min(range_start + int(args.gap_batch_blocks or 500) - 1, block)
        rpc_logs = fetch_morpho_logs(url, range_start, range_end)
        known = existing_keys(ch, range_start, range_end)
        for log in rpc_logs:
            key = (
                int(log["blockNumber"], 16),
                str(log.get("transactionHash", "")).lower(),
                int(log.get("logIndex", "0x0"), 16),
            )
            if key not in known:
                missing += 1
    return missing


def run_rpc_anchor(args) -> dict[str, object]:
    url = rpc_url(args.rpc_url)
    ch = clickhouse_client()
    run_id = str(uuid.uuid4())
    started = dt.datetime.now(dt.UTC).replace(tzinfo=None, microsecond=0)
    checked = 0
    drifted_markets: set[str] = set()
    missing_recent_logs = 0
    max_abs_supply = 0
    max_abs_borrow = 0
    max_rel_supply = 0.0
    max_rel_borrow = 0.0
    status = "OK"
    error = ""
    diff_rows: list[list] = []
    diff_rows_inserted = False
    try:
        ensure_anchor_tables(ch)
        block = anchor_block(args, ch, url)
        market_rows = ch.query(
            """
            SELECT market_id
            FROM morpho_market_params
            GROUP BY market_id
            ORDER BY market_id
            """
        ).result_rows
        market_ids = [str(row[0]).lower() for row in market_rows if row and row[0]]
        if args.max_markets:
            market_ids = market_ids[: int(args.max_markets)]
        db_state = db_market_state(ch)
        missing_recent_logs = count_recent_missing_logs(args, url, ch, block)
        if missing_recent_logs > 0:
            status = "DRIFT"
        tolerance = max(0, int(args.raw_tolerance or 0))
        max_diffs = max(0, int(args.max_diff_rows or 5000))
        for market_id in market_ids:
            result = rpc_call(
                url,
                "eth_call",
                [{"to": MORPHO_BLUE, "data": "0x" + MARKET_SELECTOR + market_id.removeprefix("0x")}, hex(block)],
                timeout=60,
                retries=5,
            )
            words = result.removeprefix("0x")
            if len(words) < 64 * 4:
                continue
            rpc_supply = int(words[0:64], 16)
            rpc_borrow = int(words[64 * 2 : 64 * 3], 16)
            db_supply, db_borrow = db_state.get(market_id, (0, 0))
            checked += 1
            for field, db_value, rpc_value in (
                ("total_supply_assets", db_supply, rpc_supply),
                ("total_borrow_assets", db_borrow, rpc_borrow),
            ):
                abs_diff = abs(int(db_value) - int(rpc_value))
                rel_diff = abs_diff / max(abs(int(rpc_value)), 1)
                if field == "total_supply_assets":
                    max_abs_supply = max(max_abs_supply, abs_diff)
                    max_rel_supply = max(max_rel_supply, rel_diff if math.isfinite(rel_diff) else 0.0)
                else:
                    max_abs_borrow = max(max_abs_borrow, abs_diff)
                    max_rel_borrow = max(max_rel_borrow, rel_diff if math.isfinite(rel_diff) else 0.0)
                if abs_diff > tolerance:
                    drifted_markets.add(market_id)
                    status = "DRIFT"
                    if len(diff_rows) < max_diffs:
                        diff_rows.append(
                            [
                                run_id,
                                block,
                                market_id,
                                field,
                                str(db_value),
                                str(rpc_value),
                                str(abs_diff),
                                float(rel_diff),
                            ]
                        )
        if diff_rows and not args.dry_run:
            ch.insert(
                "morpho_rpc_anchor_diffs",
                diff_rows,
                column_names=[
                    "run_id",
                    "block_number",
                    "market_id",
                    "field",
                    "db_value",
                    "rpc_value",
                    "abs_diff",
                    "rel_diff",
                ],
            )
            diff_rows_inserted = True
    except Exception as exc:
        block = locals().get("block", 0)
        status = "ERROR"
        error = sanitize_error(exc)
    finally:
        finished = dt.datetime.now(dt.UTC).replace(tzinfo=None, microsecond=0)
        if not args.dry_run:
            if diff_rows and not diff_rows_inserted:
                ch.insert(
                    "morpho_rpc_anchor_diffs",
                    diff_rows,
                    column_names=[
                        "run_id",
                        "block_number",
                        "market_id",
                        "field",
                        "db_value",
                        "rpc_value",
                        "abs_diff",
                        "rel_diff",
                    ],
                )
            ch.insert(
                "morpho_rpc_anchor_runs",
                [
                    [
                        run_id,
                        started,
                        finished,
                        int(locals().get("block", 0) or 0),
                        int(checked),
                        int(len(drifted_markets)),
                        int(missing_recent_logs),
                        str(max_abs_supply),
                        str(max_abs_borrow),
                        float(max_rel_supply),
                        float(max_rel_borrow),
                        status,
                        error,
                    ]
                ],
                column_names=[
                    "run_id",
                    "started_at",
                    "finished_at",
                    "block_number",
                    "checked_markets",
                    "drifted_markets",
                    "missing_recent_logs",
                    "max_abs_supply_diff",
                    "max_abs_borrow_diff",
                    "max_rel_supply_diff",
                    "max_rel_borrow_diff",
                    "status",
                    "error",
                ],
            )
        ch.close()
    summary = {
        "runId": run_id,
        "status": status,
        "block": int(locals().get("block", 0) or 0),
        "checkedMarkets": checked,
        "driftedMarkets": len(drifted_markets),
        "missingRecentLogs": missing_recent_logs,
        "maxAbsSupplyDiff": str(max_abs_supply),
        "maxAbsBorrowDiff": str(max_abs_borrow),
        "maxRelSupplyDiff": max_rel_supply,
        "maxRelBorrowDiff": max_rel_borrow,
    }
    if error:
        summary["error"] = error
    print(json.dumps(summary, sort_keys=True), flush=True)
    if args.fail_on_drift and status != "OK":
        return {**summary, "exitCode": 1}
    return {**summary, "exitCode": 0}


def run(args) -> int:
    load_env()
    if getattr(args, "anchor_once", False):
        return int(run_rpc_anchor(args).get("exitCode", 0))
    if getattr(args, "rewind_after_block", 0):
        print(json.dumps(rewind_after_block(args), sort_keys=True), flush=True)
        return 0
    if getattr(args, "rebuild_history", False):
        print(json.dumps(rebuild_historical_timeseries(args), sort_keys=True), flush=True)
        return 0
    if not args.from_block or not args.to_block:
        raise SystemExit("--from-block and --to-block are required unless --anchor-once is set")
    summary = {"checked": 0, "missing": 0, "inserted": 0}
    if not args.skip_repair:
        summary = repair_logs(args)
    if args.rebuild_state:
        summary.update({f"rebuild_{key}": value for key, value in rebuild_latest_state(args).items()})
    if args.sync_rpc_state:
        summary.update(sync_rpc_market_state(args))
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair Morpho Blue raw-log gaps")
    parser.add_argument("--rpc-url", default=None)
    parser.add_argument("--anchor-once", action="store_true")
    parser.add_argument("--block-number", type=int, default=0)
    parser.add_argument("--block-mode", choices=["processed", "latest"], default="processed")
    parser.add_argument("--confirmations", type=int, default=12)
    parser.add_argument("--gap-audit-blocks", type=int, default=7200)
    parser.add_argument("--gap-batch-blocks", type=int, default=500)
    parser.add_argument("--raw-tolerance", type=int, default=0)
    parser.add_argument("--max-diff-rows", type=int, default=5000)
    parser.add_argument("--fail-on-drift", action="store_true")
    parser.add_argument("--from-block", type=int, default=0)
    parser.add_argument("--to-block", type=int, default=0)
    parser.add_argument("--batch-blocks", type=int, default=500)
    parser.add_argument("--skip-repair", action="store_true")
    parser.add_argument("--rebuild-state", action="store_true")
    parser.add_argument("--rebuild-history", action="store_true")
    parser.add_argument("--sync-rpc-state", action="store_true")
    parser.add_argument("--rewind-after-block", type=int, default=0)
    parser.add_argument("--replay-batch-blocks", type=int, default=50_000)
    parser.add_argument("--max-markets", type=int, default=0)
    parser.add_argument("--sleep-sec", type=float, default=0.0)
    parser.add_argument("--dry-run", action="store_true")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
