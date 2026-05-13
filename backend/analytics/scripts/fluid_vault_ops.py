"""Fluid vault event collection, replay, and RPC anchor validation.

The canonical vault input is the Envio/HyperSync-collected vault contract log
stream in ``fluid_product_raw_events``. The older Fluid Liquidity event replay
is kept as a fallback/comparison path. Direct RPC is used only by ``anchor`` to
validate reconstructed state at a fixed block.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time
from uuid import uuid4
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import clickhouse_connect
import requests
from eth_abi import decode as abi_decode
from eth_utils import keccak

from analytics.fluid_full_coverage import ETHEREUM_CHAIN_ID, ensure_fluid_anchor_tables, ensure_fluid_full_coverage_tables
from analytics.tokens import TOKENS

ZERO_ADDRESS = "0x" + "0" * 40
NATIVE_ETH_ADDRESS = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
WETH_ADDRESS = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
PRECISION = 10**12
MASK_64 = (1 << 64) - 1
CONFIRMATIONS = 3
FLUID_VAULT_GENESIS_BLOCK = 19_258_464
TOPIC_OPERATE = "0x4d93b232a24e82b284ced7461bf4deacffe66759d5c24513e6f29e571ad78d15"
TOPIC_UPDATE_EXCHANGE_PRICES = "0x96c40bed7fc8d0ac41633a3bd47f254f0b0076e5df70975c51d23514bc49d3b8"
TOPIC_VAULT_LOG_OPERATE = "0xfef64760e30a41b9d5ba7dd65ff7236a61d89ed8b44c67a29e84db1a67513a1c"
VAULT_CONSTANTS_TYPE = "(address,address,address,address,address,address,address,address,(address,address),(address,address),uint256,uint256,bytes32,bytes32,bytes32,bytes32)"
VAULT_T1_CONSTANTS_TYPE = "(address,address,address,address,address,address,uint8,uint8,uint256,bytes32,bytes32,bytes32,bytes32)"

TIMESERIES_COLUMNS = [
    "timestamp", "vault_id", "symbol", "collateral_token", "debt_token",
    "supply_raw_shares", "borrow_raw_shares", "supply_tokens", "borrow_tokens",
    "supply_usd", "borrow_usd", "utilization", "supply_ex_price", "borrow_ex_price",
    "supply_apy", "borrow_apy", "supply_inflow_usd", "supply_outflow_usd",
    "borrow_inflow_usd", "borrow_outflow_usd", "net_supply_flow_usd",
    "net_borrow_flow_usd", "event_count",
]


def _rpc_url(args) -> str:
    value = (getattr(args, "rpc_url", None) or os.getenv("MAINNET_RPC_URL") or os.getenv("ETH_RPC_URL") or "").strip()
    if not value:
        raise SystemExit("MAINNET_RPC_URL, ETH_RPC_URL, or --rpc-url is required")
    return value


def normalize_address(value: str | None) -> str:
    raw = str(value or "").lower().removeprefix("0x")
    return "0x" + raw[-40:].rjust(40, "0")


def _token_key(value: str | None) -> str:
    token = normalize_address(value)
    return NATIVE_ETH_ADDRESS if token == WETH_ADDRESS else token


def _selector(signature: str) -> str:
    return "0x" + keccak(text=signature)[:4].hex()


def _word_uint(value: int) -> str:
    return f"{int(value):064x}"


def _decode_int256_word(word: str) -> int:
    value = int(str(word or "0").removeprefix("0x") or "0", 16)
    return value - (1 << 256) if value >= (1 << 255) else value


def _word(data: str | None, index: int) -> str:
    raw = str(data or "").removeprefix("0x")
    word = raw[index * 64:index * 64 + 64]
    return word if len(word) == 64 else "0" * 64


def _div_towards_zero(numerator: int, denominator: int) -> int:
    if denominator == 0:
        return 0
    sign = -1 if numerator < 0 else 1
    return sign * (abs(int(numerator)) // int(denominator))


def _event_exchange_prices(data: str | None, previous: tuple[int, int] = (PRECISION, PRECISION)) -> tuple[int, int]:
    packed = int(_word(data, 5), 16)
    supply_ep = (packed >> 91) & MASK_64
    borrow_ep = (packed >> 155) & MASK_64
    return supply_ep or previous[0] or PRECISION, borrow_ep or previous[1] or PRECISION


def _token_meta(address: str) -> tuple[str, int]:
    token = _token_key(address)
    meta = TOKENS.get(token.removeprefix("0x").lower())
    if meta:
        return str(meta[0]), int(meta[1])
    fallback = {
        NATIVE_ETH_ADDRESS: ("ETH", 18),
        WETH_ADDRESS: ("WETH", 18),
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6),
        "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6),
        "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": ("WBTC", 8),
        "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0": ("wstETH", 18),
        "0x40d16fc0246ad3160ccc09b8d0d3a2cd28ae6c2f": ("GHO", 18),
        "0x4c9edd5852cd905f086c759e8383e09bff1e68b3": ("USDe", 18),
        "0x9d39a5de30e57443bff2a8307a4256c8797a3497": ("sUSDe", 18),
        "0xcd5fe23c85820f7b72d0926fc9b05b43e359b7ee": ("weETH", 18),
        "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf": ("cbBTC", 8),
    }
    return fallback.get(token, (token[:10], 18))


def _ch_client():
    settings = {}
    if os.getenv("CLICKHOUSE_ASYNC_INSERT", "true").strip().lower() in {"1", "true", "yes"}:
        settings["async_insert"] = 1
        settings["wait_for_async_insert"] = 1
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "127.0.0.1"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
        settings=settings,
    )


def _table_exists(ch, table: str) -> bool:
    return bool(ch.query("EXISTS TABLE " + table).result_rows[0][0])


def _insert_rows_batched(ch, table: str, rows: list[list], column_names: list[str], batch_size: int = 20000) -> int:
    written = 0
    for start in range(0, len(rows), batch_size):
        chunk = rows[start:start + batch_size]
        if chunk:
            ch.insert(table, chunk, column_names=column_names)
            written += len(chunk)
    return written


def ensure_tables(ch) -> None:
    ensure_fluid_full_coverage_tables(ch)
    ch.command("""
        CREATE TABLE IF NOT EXISTS fluid_vault_timeseries (
            timestamp DateTime,
            vault_id String,
            symbol LowCardinality(String),
            collateral_token String,
            debt_token String,
            supply_raw_shares Float64,
            borrow_raw_shares Float64,
            supply_tokens Float64,
            borrow_tokens Float64,
            supply_usd Float64,
            borrow_usd Float64,
            utilization Float64,
            supply_ex_price Float64,
            borrow_ex_price Float64,
            supply_apy Float64,
            borrow_apy Float64,
            supply_inflow_usd Float64,
            supply_outflow_usd Float64,
            borrow_inflow_usd Float64,
            borrow_outflow_usd Float64,
            net_supply_flow_usd Float64,
            net_borrow_flow_usd Float64,
            event_count UInt32,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (vault_id, timestamp)
        TTL timestamp + INTERVAL 36 MONTH DELETE
    """)


def load_vaults(ch) -> list[str]:
    rows = ch.query("""
        SELECT contract
        FROM fluid_contract_registry FINAL
        WHERE product_type = 'VAULT'
          AND name = 'Fluid Vault'
          AND active = 1
        ORDER BY contract
    """).result_rows
    return [normalize_address(row[0]) for row in rows]


def current_event_head(ch) -> int:
    row = ch.query("SELECT max(block_number) FROM fluid_events").result_rows[0]
    return int(row[0] or 0)


def current_vault_raw_event_head(ch) -> int:
    if not _table_exists(ch, "fluid_product_raw_events"):
        return 0
    row = ch.query("""
        SELECT max(block_number)
        FROM fluid_product_raw_events
        WHERE product_type = 'VAULT'
          AND event_name = 'LogOperate'
    """).result_rows[0]
    return int(row[0] or 0)


def _date_range(start: dt.date, end: dt.date):
    day = start
    one = dt.timedelta(days=1)
    while day <= end:
        yield day
        day += one


def load_daily_prices(ch) -> dict[str, list[tuple[dt.date, float, float, float]]]:
    rows = ch.query("""
        SELECT
            entity_id,
            toDate(timestamp) AS day,
            argMax(price_usd, timestamp) AS price_usd,
            argMax(supply_apy, timestamp) AS supply_apy,
            argMax(borrow_apy, timestamp) AS borrow_apy
        FROM fluid_reserve_metrics
        WHERE fluid_reserve_metrics.price_usd > 0
        GROUP BY entity_id, day
        ORDER BY entity_id, day
    """).result_rows
    out: dict[str, list[tuple[dt.date, float, float, float]]] = defaultdict(list)
    for token, day, price, supply_apy, borrow_apy in rows:
        out[_token_key(str(token))].append((day, float(price or 0.0), float(supply_apy or 0.0), float(borrow_apy or 0.0)))
    return dict(out)


def _asof_price(price_map: dict[str, list[tuple[dt.date, float, float, float]]], token: str, day: dt.date) -> tuple[float, float, float]:
    items = price_map.get(_token_key(token), [])
    if not items:
        symbol, _decimals = _token_meta(token)
        if symbol.upper() in {"USDC", "USDT", "DAI", "GHO", "USDE", "USDTB"}:
            return 1.0, 0.0, 0.0
        return 0.0, 0.0, 0.0
    days = [item[0] for item in items]
    idx = bisect_right(days, day) - 1
    if idx < 0:
        return 0.0, 0.0, 0.0
    _day, price, supply_apy, borrow_apy = items[idx]
    return price, supply_apy, borrow_apy


def load_daily_exchange_prices(ch, to_block: int) -> dict[str, list[tuple[dt.date, int, int]]]:
    rows = ch.query("""
        WITH raw AS (
            SELECT
                lower(concat('0x', substring(topic2, 27))) AS token,
                toDate(block_timestamp) AS day,
                block_number,
                log_index,
                toUInt64(bitAnd(bitShiftRight(reinterpretAsUInt256(reverse(unhex(substring(data, 323, 64)))), 91), toUInt256(18446744073709551615))) AS supply_ep,
                toUInt64(bitAnd(bitShiftRight(reinterpretAsUInt256(reverse(unhex(substring(data, 323, 64)))), 155), toUInt256(18446744073709551615))) AS borrow_ep
            FROM fluid_events
            WHERE event_name IN ('Operate', 'LogOperate')
              AND block_number <= %(to_block)s

            UNION ALL

            SELECT
                lower(concat('0x', substring(topic1, 27))) AS token,
                toDate(block_timestamp) AS day,
                block_number,
                log_index,
                toUInt64(reinterpretAsUInt256(reverse(unhex(substring(topic2, 3, 64))))) AS supply_ep,
                toUInt64(reinterpretAsUInt256(reverse(unhex(substring(topic3, 3, 64))))) AS borrow_ep
            FROM fluid_events
            WHERE event_name = 'LogUpdateExchangePrices'
              AND block_number <= %(to_block)s
        )
        SELECT
            token,
            day,
            argMaxIf(supply_ep, tuple(block_number, log_index), supply_ep > 0) AS supply_ep,
            argMaxIf(borrow_ep, tuple(block_number, log_index), borrow_ep > 0) AS borrow_ep
        FROM raw
        GROUP BY token, day
        ORDER BY token, day
    """, parameters={"to_block": int(to_block)}).result_rows
    out: dict[str, list[tuple[dt.date, int, int]]] = defaultdict(list)
    for token, day, supply_ep, borrow_ep in rows:
        out[_token_key(str(token))].append((day, int(supply_ep or PRECISION), int(borrow_ep or PRECISION)))
    return dict(out)


def _asof_ep(ep_map: dict[str, list[tuple[dt.date, int, int]]], token: str, day: dt.date) -> tuple[int, int]:
    items = ep_map.get(_token_key(token), [])
    if not items:
        return PRECISION, PRECISION
    days = [item[0] for item in items]
    idx = bisect_right(days, day) - 1
    if idx < 0:
        return PRECISION, PRECISION
    _day, supply_ep, borrow_ep = items[idx]
    return int(supply_ep or PRECISION), int(borrow_ep or PRECISION)


def load_vault_metadata(ch, to_block: int) -> dict[str, dict[str, Any]]:
    if not _table_exists(ch, "fluid_vault_timeseries"):
        return {}
    rows = ch.query("""
        SELECT
            lower(vault_id) AS vault_id,
            argMax(symbol, tuple(timestamp, inserted_at)) AS symbol,
            argMax(collateral_token, tuple(timestamp, inserted_at)) AS collateral_token,
            argMax(debt_token, tuple(timestamp, inserted_at)) AS debt_token
        FROM fluid_vault_timeseries FINAL
        GROUP BY vault_id
    """).result_rows
    out: dict[str, dict[str, Any]] = {}
    for vault, symbol, collateral_token, debt_token in rows:
        out[normalize_address(vault)] = {
            "symbol": str(symbol or "VAULT"),
            "collateral_token": normalize_address(collateral_token) if collateral_token else "",
            "debt_token": normalize_address(debt_token) if debt_token else "",
        }
    return out


def load_vault_contract_events(ch, from_block: int, to_block: int, vaults: set[str]) -> list[tuple]:
    if not _table_exists(ch, "fluid_product_raw_events"):
        return []
    return ch.query("""
        SELECT
            block_number,
            block_timestamp,
            lower(contract) AS vault,
            data,
            tx_hash,
            log_index
        FROM fluid_product_raw_events FINAL
        WHERE product_type = 'VAULT'
          AND event_name = 'LogOperate'
          AND topic0 = %(topic0)s
          AND block_number >= %(from_block)s
          AND block_number <= %(to_block)s
          AND vault IN %(vaults)s
        ORDER BY block_number, log_index, tx_hash, contract
    """, parameters={
        "topic0": TOPIC_VAULT_LOG_OPERATE,
        "from_block": int(from_block),
        "to_block": int(to_block),
        "vaults": tuple(sorted(vaults)),
    }).result_rows


async def _collect_envio_async(args, ch) -> dict[str, Any]:
    import hypersync

    from analytics.collector import (
        BLOCK_FIELDS,
        LOG_FIELDS,
        advance_hypersync_cursor,
        build_block_ts_map,
        require_hypersync_token,
        scanned_block_from_exclusive,
    )

    if not args.dry_run:
        ensure_tables(ch)
    vaults = load_vaults(ch)
    if not vaults:
        raise SystemExit("No Fluid vaults found in fluid_contract_registry")

    client = hypersync.HypersyncClient(hypersync.ClientConfig(
        url="https://eth.hypersync.xyz",
        bearer_token=require_hypersync_token(),
    ))
    head = int(await client.get_height()) - CONFIRMATIONS
    to_block = int(args.to_block or 0) or head
    existing_head = current_vault_raw_event_head(ch)
    from_block = int(args.from_block or 0) or (existing_head + 1 if existing_head else FLUID_VAULT_GENESIS_BLOCK)
    if to_block < from_block:
        return {
            "status": "OK",
            "dryRun": bool(args.dry_run),
            "source": "envio_hypersync",
            "fromBlock": from_block,
            "toBlock": to_block,
            "vaults": len(vaults),
            "rawLogs": 0,
            "matchedLogs": 0,
            "insertedLogs": 0,
            "pages": 0,
        }

    log_selection = hypersync.LogSelection(
        address=vaults,
        topics=[[TOPIC_VAULT_LOG_OPERATE]],
    )
    current_start = from_block
    raw_logs = 0
    matched_logs = 0
    inserted_logs = 0
    pages = 0
    batches = 0
    batch_blocks = int(args.batch_blocks)
    progress_every = int(args.progress_every or 0)
    vault_set = {vault.lower() for vault in vaults}

    while current_start <= to_block:
        batch_to_exclusive = min(current_start + batch_blocks, to_block + 1)
        current_end = scanned_block_from_exclusive(batch_to_exclusive)
        cursor = current_start
        mempool_logs = []
        mempool_blocks = []
        while cursor < batch_to_exclusive:
            query = hypersync.Query(
                from_block=cursor,
                to_block=batch_to_exclusive,
                logs=[log_selection],
                field_selection=hypersync.FieldSelection(log=LOG_FIELDS, block=BLOCK_FIELDS),
            )
            res = await client.get(query)
            mempool_logs.extend(res.data.logs)
            mempool_blocks.extend(res.data.blocks)
            pages += 1
            cursor = advance_hypersync_cursor(cursor, res.next_block)

        block_ts_map = build_block_ts_map(mempool_blocks)
        matched = [
            entry for entry in mempool_logs
            if normalize_address(getattr(entry, "address", None)) in vault_set
        ]
        raw_logs += len(mempool_logs)
        matched_logs += len(matched)
        if matched and not args.dry_run:
            rows = []
            for entry in matched:
                topics = [str(topic).lower() for topic in (entry.topics or [])]
                ts = block_ts_map.get(entry.block_number, dt.datetime.now(dt.UTC))
                ts_naive = ts.replace(tzinfo=None) if getattr(ts, "tzinfo", None) else ts
                rows.append([
                    ETHEREUM_CHAIN_ID,
                    "VAULT",
                    int(entry.block_number),
                    ts_naive,
                    str(entry.transaction_hash or "").lower(),
                    int(entry.log_index or 0),
                    normalize_address(entry.address),
                    "LogOperate",
                    topics[0] if topics else "",
                    topics[1] if len(topics) > 1 else None,
                    topics[2] if len(topics) > 2 else None,
                    topics[3] if len(topics) > 3 else None,
                    str(entry.data or "0x"),
                ])
            inserted_logs += _insert_rows_batched(
                ch,
                "fluid_product_raw_events",
                rows,
                [
                    "chain_id", "product_type", "block_number", "block_timestamp",
                    "tx_hash", "log_index", "contract", "event_name", "topic0",
                    "topic1", "topic2", "topic3", "data",
                ],
            )

        batches += 1
        if progress_every and batches % progress_every == 0:
            print(json.dumps({
                "status": "PROGRESS",
                "source": "envio_hypersync",
                "scannedToBlock": current_end,
                "matchedLogs": matched_logs,
                "insertedLogs": inserted_logs,
                "pages": pages,
            }, sort_keys=True))

        mempool_logs.clear()
        mempool_blocks.clear()
        current_start = batch_to_exclusive

    return {
        "status": "OK",
        "dryRun": bool(args.dry_run),
        "source": "envio_hypersync",
        "fromBlock": from_block,
        "toBlock": to_block,
        "vaults": len(vaults),
        "rawLogs": raw_logs,
        "matchedLogs": matched_logs,
        "insertedLogs": inserted_logs,
        "pages": pages,
    }


def collect_envio(args, ch=None) -> int:
    owns_client = ch is None
    ch = ch or _ch_client()
    try:
        import asyncio

        payload = asyncio.run(_collect_envio_async(args, ch))
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    finally:
        if owns_client:
            ch.close()


@dataclass
class ReplayResult:
    rows: list[list]
    anchor_state: dict[str, dict[str, Any]]
    event_count: int
    active_vaults: int
    from_block: int
    to_block: int


def build_replay(ch, *, from_block: int = FLUID_VAULT_GENESIS_BLOCK, to_block: int = 0, min_usd: float = 0.0) -> ReplayResult:
    vaults = set(load_vaults(ch))
    if not vaults:
        raise SystemExit("No Fluid vaults found in fluid_contract_registry")
    if not to_block:
        to_block = current_vault_raw_event_head(ch)
    vault_rows = load_vault_contract_events(ch, int(from_block), int(to_block), vaults)
    vault_meta = load_vault_metadata(ch, int(to_block))
    state: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: {"__vault__": {"supply_normal": 0, "borrow_normal": 0}})
    daily_state: dict[str, dict[dt.date, dict[str, dict[str, int]]]] = defaultdict(dict)
    daily_flows: dict[tuple[str, dt.date], dict[str, dict[str, int] | int]] = defaultdict(lambda: {"tokens": defaultdict(lambda: {"supply_in": 0, "supply_out": 0, "borrow_in": 0, "borrow_out": 0}), "count": 0})

    for _block_number, block_ts, vault, data, _tx_hash, _log_index in vault_rows:
        vault = normalize_address(vault)
        supply_delta = _decode_int256_word(_word(data, 2))
        borrow_delta = _decode_int256_word(_word(data, 3))
        current = state[vault]["__vault__"]
        current["supply_normal"] += int(supply_delta)
        current["borrow_normal"] += int(borrow_delta)
        day = block_ts.date() if hasattr(block_ts, "date") else dt.datetime.fromisoformat(str(block_ts)).date()
        meta = vault_meta.get(vault, {})
        collateral_token = meta.get("collateral_token") or ""
        debt_token = meta.get("debt_token") or ""
        if collateral_token:
            supply_flows = daily_flows[(vault, day)]["tokens"][collateral_token]  # type: ignore[index]
            if supply_delta > 0:
                supply_flows["supply_in"] += int(supply_delta)
            elif supply_delta < 0:
                supply_flows["supply_out"] += int(-supply_delta)
        if debt_token:
            borrow_flows = daily_flows[(vault, day)]["tokens"][debt_token]  # type: ignore[index]
            if borrow_delta > 0:
                borrow_flows["borrow_in"] += int(borrow_delta)
            elif borrow_delta < 0:
                borrow_flows["borrow_out"] += int(-borrow_delta)
        daily_flows[(vault, day)]["count"] = int(daily_flows[(vault, day)]["count"]) + 1
        daily_state[vault][day] = {token: dict(values) for token, values in state[vault].items()}

    price_map = load_daily_prices(ch)
    ep_map = load_daily_exchange_prices(ch, int(to_block))
    if vault_rows:
        end_ts = max(row[1] for row in vault_rows)
    else:
        end_ts = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    end_day = end_ts.date() if hasattr(end_ts, "date") else dt.datetime.now(dt.UTC).date()

    output_rows: list[list] = []
    anchor_state: dict[str, dict[str, Any]] = {}
    for vault, states_by_day in sorted(daily_state.items()):
        if not states_by_day:
            continue
        first_day = min(states_by_day)
        current: dict[str, dict[str, int]] = {}
        meta = vault_meta.get(vault, {})
        collateral_token = meta.get("collateral_token") or ""
        debt_token = meta.get("debt_token") or ""
        symbol = meta.get("symbol") or "VAULT"
        for day in _date_range(first_day, end_day):
            if day in states_by_day:
                current = states_by_day[day]
            if not current:
                continue
            values = current.get("__vault__", {})
            supply_normal = max(0, int(values.get("supply_normal", 0)))
            borrow_normal = max(0, int(values.get("borrow_normal", 0)))
            supply_usd = borrow_usd = 0.0
            supply_units = borrow_units = 0.0
            supply_apy = borrow_apy = 0.0
            supply_ep_display = borrow_ep_display = PRECISION
            if collateral_token:
                _s_symbol, supply_decimals = _token_meta(collateral_token)
                price, supply_apy, _unused = _asof_price(price_map, collateral_token, day)
                supply_ep_display, _ = _asof_ep(ep_map, collateral_token, day)
                supply_units = supply_normal / float(10 ** supply_decimals)
                supply_usd = supply_units * price
            if debt_token:
                _b_symbol, borrow_decimals = _token_meta(debt_token)
                price, _unused, borrow_apy = _asof_price(price_map, debt_token, day)
                _, borrow_ep_display = _asof_ep(ep_map, debt_token, day)
                borrow_units = borrow_normal / float(10 ** borrow_decimals)
                borrow_usd = borrow_units * price
            if min_usd and abs(supply_usd) < min_usd and abs(borrow_usd) < min_usd:
                continue
            flow_bucket = daily_flows.get((vault, day), {"tokens": {}, "count": 0})
            supply_inflow_usd = supply_outflow_usd = borrow_inflow_usd = borrow_outflow_usd = 0.0
            for token, flows in dict(flow_bucket.get("tokens", {})).items():
                _symbol, decimals = _token_meta(token)
                price, _s_apy, _b_apy = _asof_price(price_map, token, day)
                scale = float(10 ** decimals)
                supply_inflow_usd += int(flows.get("supply_in", 0)) / scale * price
                supply_outflow_usd += int(flows.get("supply_out", 0)) / scale * price
                borrow_inflow_usd += int(flows.get("borrow_in", 0)) / scale * price
                borrow_outflow_usd += int(flows.get("borrow_out", 0)) / scale * price
            utilization = max(0.0, min(10.0, borrow_usd / supply_usd)) if supply_usd > 0 else 0.0
            output_rows.append([
                dt.datetime.combine(day, dt.time()), vault, symbol, collateral_token, debt_token,
                float(supply_normal), float(borrow_normal), float(supply_units), float(borrow_units),
                float(max(0.0, supply_usd)), float(max(0.0, borrow_usd)), float(utilization),
                float(supply_ep_display), float(borrow_ep_display), float(supply_apy), float(borrow_apy),
                float(supply_inflow_usd), float(supply_outflow_usd), float(borrow_inflow_usd), float(borrow_outflow_usd),
                float(supply_inflow_usd - supply_outflow_usd), float(borrow_inflow_usd - borrow_outflow_usd),
                int(flow_bucket.get("count", 0)),
            ])
        if current:
            supply_ep = borrow_ep = PRECISION
            if collateral_token:
                supply_ep, _ = _asof_ep(ep_map, collateral_token, end_day)
            if debt_token:
                _, borrow_ep = _asof_ep(ep_map, debt_token, end_day)
            anchor_state[vault] = {
                "tokens": {"__vault__": dict(current.get("__vault__", {}))},
                "day": str(end_day),
                "collateral_token": collateral_token,
                "debt_token": debt_token,
                "supply_exchange_price": int(supply_ep),
                "borrow_exchange_price": int(borrow_ep),
                "source": "vault_contract_logs",
            }

    return ReplayResult(output_rows, anchor_state, len(vault_rows), len(daily_state), int(from_block), int(to_block))

def replay(args, ch=None) -> int:
    owns_client = ch is None
    ch = ch or _ch_client()
    try:
        if not args.dry_run:
            ensure_tables(ch)
        result = build_replay(ch, from_block=int(args.from_block or FLUID_VAULT_GENESIS_BLOCK), to_block=int(args.to_block or 0), min_usd=float(args.min_usd or 0.0))
        if args.dry_run:
            print(json.dumps({
                "mode": "dry_run",
                "fromBlock": result.from_block,
                "toBlock": result.to_block,
                "inputEvents": result.event_count,
                "activeVaults": result.active_vaults,
                "outputRows": len(result.rows),
                "firstTimestamp": str(result.rows[0][0]) if result.rows else None,
                "lastTimestamp": str(result.rows[-1][0]) if result.rows else None,
            }, indent=2, sort_keys=True))
            return 0
        written = _insert_rows_batched(ch, "fluid_vault_timeseries", result.rows, TIMESERIES_COLUMNS)
        print(json.dumps({
            "fromBlock": result.from_block,
            "toBlock": result.to_block,
            "inputEvents": result.event_count,
            "activeVaults": result.active_vaults,
            "writtenRows": written,
        }, indent=2, sort_keys=True))
        return 0
    finally:
        if owns_client:
            ch.close()


def _rpc_call(rpc_url: str, method: str, params: list[Any], *, timeout: int, retries: int) -> Any:
    last_error = ""
    for attempt in range(retries + 1):
        try:
            response = requests.post(rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if payload.get("error"):
                raise RuntimeError(payload["error"])
            return payload.get("result")
        except Exception as exc:
            last_error = type(exc).__name__
            if attempt < retries:
                time.sleep(min(2.0 * (attempt + 1), 5.0))
    raise RuntimeError(f"RPC {method} failed after {retries + 1} attempts: {last_error}")


def _eth_call(rpc_url: str, to: str, data: str, block: int, *, timeout: int, retries: int) -> str:
    return str(_rpc_call(rpc_url, "eth_call", [{"to": normalize_address(to), "data": data}, hex(int(block))], timeout=timeout, retries=retries) or "0x")


def _decode_uint(raw: str) -> int:
    return int(str(raw or "0x0").removeprefix("0x") or "0", 16)


def _storage_uint(rpc_url: str, vault: str, slot: int, block: int, *, timeout: int, retries: int) -> int:
    data = _selector("readFromStorage(bytes32)") + _word_uint(slot)
    return _decode_uint(_eth_call(rpc_url, vault, data, block, timeout=timeout, retries=retries))


def _bigmath(packed: int) -> int:
    return (int(packed) >> 8) << (int(packed) & 0xFF)


def _decode_vault_packed_totals(vault_variables: int | None, rate_raw: int | None) -> tuple[int, int, int, int]:
    if vault_variables is None:
        return 0, 0, 0, 0
    raw_supply = _bigmath((int(vault_variables) >> 82) & MASK_64)
    raw_borrow = _bigmath((int(vault_variables) >> 146) & MASK_64)
    supply_ep = ((int(rate_raw or 0) >> 128) & MASK_64) or PRECISION
    borrow_ep = ((int(rate_raw or 0) >> 192) & MASK_64) or PRECISION
    supply = (raw_supply * supply_ep) // PRECISION
    borrow = (raw_borrow * borrow_ep) // PRECISION
    return supply, borrow, supply_ep, borrow_ep


def _replay_normal_totals(anchor_state: dict[str, Any], ep_map: dict[str, list[tuple[dt.date, int, int]]], day: dt.date) -> tuple[int, int]:
    supply = 0
    borrow = 0
    for token, vals in anchor_state.get("tokens", {}).items():
        if "supply_normal" in vals or "borrow_normal" in vals:
            supply += max(0, int(vals.get("supply_normal", 0)))
            borrow += max(0, int(vals.get("borrow_normal", 0)))
            continue
        supply_ep, borrow_ep = _asof_ep(ep_map, token, day)
        supply += (int(vals.get("supply_raw", 0)) * supply_ep) // PRECISION
        borrow += (int(vals.get("borrow_raw", 0)) * borrow_ep) // PRECISION
    return supply, borrow


def _relative_diff(a: int, b: int) -> float:
    if int(a) == int(b):
        return 0.0
    return abs(int(a) - int(b)) / max(abs(int(a)), abs(int(b)), 1)


def _decode_single(raw: str, abi_type: str) -> Any:
    if not raw or raw == "0x":
        raise ValueError("empty RPC result")
    return abi_decode([abi_type], bytes.fromhex(str(raw).removeprefix("0x")))[0]


def _call_uint_selector(rpc_url: str, to: str, signature: str, block: int, *, timeout: int, retries: int) -> int:
    return int(_decode_single(_eth_call(rpc_url, to, _selector(signature), block, timeout=timeout, retries=retries), "uint256"))


def _call_tuple_selector(rpc_url: str, to: str, signature: str, abi_type: str, block: int, *, timeout: int, retries: int) -> Any:
    return _decode_single(_eth_call(rpc_url, to, _selector(signature), block, timeout=timeout, retries=retries), abi_type)


def _rpc_vault_metadata(rpc_url: str, vault: str, block: int, *, timeout: int, retries: int) -> dict[str, Any]:
    try:
        vault_type = _call_uint_selector(rpc_url, vault, "TYPE()", block, timeout=timeout, retries=retries)
    except Exception:
        vault_type = 0
    try:
        if int(vault_type or 0) > 1:
            constants = _call_tuple_selector(rpc_url, vault, "constantsView()", VAULT_CONSTANTS_TYPE, block, timeout=timeout, retries=retries)
        else:
            t1 = _call_tuple_selector(rpc_url, vault, "constantsView()", VAULT_T1_CONSTANTS_TYPE, block, timeout=timeout, retries=retries)
            constants = (
                t1[0], t1[1], vault, t1[2], t1[3], ZERO_ADDRESS,
                t1[0], t1[0], (t1[4], ZERO_ADDRESS), (t1[5], ZERO_ADDRESS),
                t1[8], 1, t1[9], t1[10], t1[11], t1[12]
            )
        supply_tokens = [normalize_address(constants[8][0]), normalize_address(constants[8][1])]
        borrow_tokens = [normalize_address(constants[9][0]), normalize_address(constants[9][1])]
        return {
            "vault_type": int(constants[11] or vault_type or 0),
            "vault_id": int(constants[10] or 0),
            "collateral_token": supply_tokens[0],
            "debt_token": borrow_tokens[0],
            "supply_tokens": supply_tokens,
            "borrow_tokens": borrow_tokens,
        }
    except Exception:
        return {"vault_type": int(vault_type or 0), "collateral_token": "", "debt_token": ""}


def _numeric_diff(indexed: int, rpc: int) -> tuple[str, float, bool]:
    abs_diff = abs(int(indexed) - int(rpc))
    rel_diff = abs_diff / max(abs(int(rpc)), 1)
    return str(abs_diff), float(rel_diff), abs_diff != 0


def _string_diff(indexed: str, rpc: str) -> tuple[str, float, bool]:
    mismatch = normalize_address(indexed) != normalize_address(rpc)
    return ("1" if mismatch else "0"), (1.0 if mismatch else 0.0), mismatch


def _append_diff(diffs: list[dict[str, Any]], target: str, entity_id: str, field: str, indexed: Any, rpc: Any, abs_diff: str, rel_diff: float) -> None:
    diffs.append({
        "target": target,
        "entity_id": entity_id,
        "field": field,
        "indexed_value": str(indexed),
        "rpc_value": str(rpc),
        "abs_diff": str(abs_diff),
        "rel_diff": float(rel_diff),
    })


def _write_anchor_audit(ch, *, run_id: str, target: str, started: dt.datetime, finished: dt.datetime, anchor_block: int, checked_entities: int, drifted_entities: int, checked_fields: int, drifted_fields: int, max_abs_diff: str, max_rel_diff: float, status: str, error: str, diffs: list[dict[str, Any]], dry_run: bool) -> None:
    if dry_run:
        return
    ensure_fluid_anchor_tables(ch)
    ch.insert(
        "fluid_anchor_runs",
        [[run_id, target, started, finished, int(anchor_block), int(checked_entities), int(drifted_entities), int(checked_fields), int(drifted_fields), str(max_abs_diff), float(max_rel_diff), status, error[:1000]]],
        column_names=["run_id", "target", "started_at", "finished_at", "anchor_block", "checked_entities", "drifted_entities", "checked_fields", "drifted_fields", "max_abs_diff", "max_rel_diff", "status", "error"],
    )
    if diffs:
        ch.insert(
            "fluid_anchor_diffs",
            [[run_id, item["target"], int(anchor_block), item["entity_id"], item["field"], item["indexed_value"], item["rpc_value"], item["abs_diff"], float(item["rel_diff"])] for item in diffs],
            column_names=["run_id", "target", "anchor_block", "entity_id", "field", "indexed_value", "rpc_value", "abs_diff", "rel_diff"],
        )


def anchor(args, ch=None) -> int:
    owns_client = ch is None
    ch = ch or _ch_client()
    run_id = str(uuid4())
    started = dt.datetime.now(dt.UTC).replace(tzinfo=None, microsecond=0)
    target = "FLUID_VAULT"
    diffs: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    checked = 0
    checked_fields = 0
    drifted_fields = 0
    drifted_entities: set[str] = set()
    max_abs = 0
    max_rel = 0.0
    block = 0
    head_ts: Any = None
    try:
        rpc_url = _rpc_url(args)
        block = int(args.block_number or current_vault_raw_event_head(ch))
        result = build_replay(ch, from_block=int(args.from_block or FLUID_VAULT_GENESIS_BLOCK), to_block=block, min_usd=0.0)
        head_row = ch.query("""
            SELECT max(block_timestamp)
            FROM fluid_product_raw_events
            WHERE product_type = 'VAULT'
              AND event_name = 'LogOperate'
              AND block_number <= %(block)s
        """, parameters={"block": block}).result_rows
        head_ts = head_row[0][0] if head_row and head_row[0] else dt.datetime.now(dt.UTC).replace(tzinfo=None)
        day = head_ts.date() if hasattr(head_ts, "date") else dt.datetime.now(dt.UTC).date()
        vaults = load_vaults(ch)
        timeout = int(args.http_timeout_sec or 60)
        retries = int(args.retries or 2)
        for vault in vaults:
            vault = normalize_address(vault)
            try:
                replay_state = result.anchor_state.get(vault, {})
                replay_supply, replay_borrow = _replay_normal_totals(replay_state or {"tokens": {}}, {}, day)
                replay_supply_ep = int(replay_state.get("supply_exchange_price", 0) or 0)
                replay_borrow_ep = int(replay_state.get("borrow_exchange_price", 0) or 0)
                indexed_collateral = str(replay_state.get("collateral_token", "") or "")
                indexed_debt = str(replay_state.get("debt_token", "") or "")
                vault_variables = _storage_uint(rpc_url, vault, 0, block, timeout=timeout, retries=retries)
                rate_raw = _storage_uint(rpc_url, vault, 8, block, timeout=timeout, retries=retries)
                rpc_supply, rpc_borrow, rpc_supply_ep, rpc_borrow_ep = _decode_vault_packed_totals(vault_variables, rate_raw)
                rpc_meta = _rpc_vault_metadata(rpc_url, vault, block, timeout=timeout, retries=retries)
                comparisons = [
                    ("supply_raw_or_normal", replay_supply, rpc_supply, "numeric"),
                    ("borrow_raw_or_normal", replay_borrow, rpc_borrow, "numeric"),
                    ("supply_exchange_price", replay_supply_ep, rpc_supply_ep, "numeric"),
                    ("borrow_exchange_price", replay_borrow_ep, rpc_borrow_ep, "numeric"),
                    ("collateral_token", indexed_collateral, rpc_meta.get("collateral_token", ""), "string"),
                    ("debt_token", indexed_debt, rpc_meta.get("debt_token", ""), "string"),
                ]
                checked += 1
                entity_drifted = False
                for field, indexed_value, rpc_value, kind in comparisons:
                    checked_fields += 1
                    if kind == "numeric":
                        abs_diff, rel_diff, mismatch = _numeric_diff(int(indexed_value or 0), int(rpc_value or 0))
                        max_abs = max(max_abs, int(abs_diff))
                        max_rel = max(max_rel, rel_diff)
                    else:
                        abs_diff, rel_diff, mismatch = _string_diff(str(indexed_value or ""), str(rpc_value or ""))
                        max_abs = max(max_abs, int(abs_diff))
                        max_rel = max(max_rel, rel_diff)
                    if mismatch:
                        drifted_fields += 1
                        entity_drifted = True
                        _append_diff(diffs, target, vault, field, indexed_value, rpc_value, abs_diff, rel_diff)
                if entity_drifted:
                    drifted_entities.add(vault)
            except Exception as exc:
                errors.append({"vault": vault, "error": str(exc)[:240]})
        finished = dt.datetime.now(dt.UTC).replace(tzinfo=None, microsecond=0)
        status = "ERROR" if errors else "DRIFT" if diffs else "OK"
        _write_anchor_audit(
            ch,
            run_id=run_id,
            target=target,
            started=started,
            finished=finished,
            anchor_block=block,
            checked_entities=checked,
            drifted_entities=len(drifted_entities) + len(errors),
            checked_fields=checked_fields,
            drifted_fields=drifted_fields + len(errors),
            max_abs_diff=str(max_abs),
            max_rel_diff=max_rel,
            status=status,
            error="; ".join(item["error"] for item in errors)[:1000],
            diffs=diffs,
            dry_run=bool(getattr(args, "dry_run", False)),
        )
        payload = {
            "runId": run_id,
            "target": target,
            "anchorBlock": block,
            "anchorTimestamp": str(head_ts),
            "inputEvents": result.event_count,
            "activeReplayVaults": result.active_vaults,
            "checkedVaults": checked,
            "checkedFields": checked_fields,
            "driftedVaults": len(drifted_entities),
            "driftedFields": drifted_fields,
            "rpcErrors": len(errors),
            "maxAbsDiff": str(max_abs),
            "maxRelDiff": max_rel,
            "status": status,
            "dryRun": bool(getattr(args, "dry_run", False)),
            "diffs": sorted(diffs, key=lambda x: x["rel_diff"], reverse=True)[:25],
            "errors": errors[:25],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1 if getattr(args, "fail_on_drift", False) and status != "OK" else 0
    finally:
        if owns_client:
            ch.close()

def add_args(sub: argparse._SubParsersAction) -> None:
    collect_parser = sub.add_parser("fluid-vault-collect", help="Backfill Fluid vault raw logs through Envio HyperSync")
    collect_parser.add_argument("--from-block", type=int, default=0)
    collect_parser.add_argument("--to-block", type=int, default=0, help="Inclusive block; defaults to confirmed HyperSync head")
    collect_parser.add_argument("--batch-blocks", type=int, default=100000)
    collect_parser.add_argument("--progress-every", type=int, default=5)
    collect_parser.add_argument("--dry-run", action="store_true")
    collect_parser.set_defaults(func=lambda args: collect_envio(args))

    replay_parser = sub.add_parser("fluid-vault-replay", help="Replay Fluid vault state from Envio-collected vault contract events")
    replay_parser.add_argument("--from-block", type=int, default=FLUID_VAULT_GENESIS_BLOCK)
    replay_parser.add_argument("--to-block", type=int, default=0, help="Inclusive block; defaults to latest collected Fluid event block")
    replay_parser.add_argument("--min-usd", type=float, default=0.0, help="Optional storage filter; default keeps all replayed states")
    replay_parser.add_argument("--dry-run", action="store_true")
    replay_parser.set_defaults(func=lambda args: replay(args))

    anchor_parser = sub.add_parser("fluid-vault-anchor", help="Validate Fluid vault event replay against direct RPC storage at an anchor block")
    anchor_parser.add_argument("--rpc-url", default=None)
    anchor_parser.add_argument("--from-block", type=int, default=FLUID_VAULT_GENESIS_BLOCK)
    anchor_parser.add_argument("--block-number", type=int, default=0, help="Anchor block; defaults to latest collected Fluid event block")
    anchor_parser.add_argument("--http-timeout-sec", type=int, default=60)
    anchor_parser.add_argument("--retries", type=int, default=2)
    anchor_parser.add_argument("--relative-tolerance", type=float, default=1e-8)
    anchor_parser.add_argument("--dry-run", action="store_true")
    anchor_parser.add_argument("--write-validation", action="store_true", help="Deprecated no-op; anchors write fluid_anchor_runs unless --dry-run")
    anchor_parser.add_argument("--fail-on-drift", action="store_true")
    anchor_parser.set_defaults(func=lambda args: anchor(args))
