"""Fluid fToken event collection, replay, and RPC anchor validation."""

from __future__ import annotations

import datetime as dt
import json
import os
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from bisect import bisect_right
from typing import Any

import clickhouse_connect
import requests

from analytics.fluid_full_coverage import ETHEREUM_CHAIN_ID, FLUID_LENDING_FACTORY, ensure_fluid_full_coverage_tables
from analytics.protocols import FLUID_FTOKEN

ZERO_ADDRESS = "0x" + "0" * 40
NATIVE_ETH_ADDRESS = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
WETH_ADDRESS = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
CONFIRMATIONS = 3
SECONDS_PER_YEAR = 365 * 24 * 60 * 60
FTOKEN_GENESIS_BLOCK = 19_258_464
TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TOPIC_DEPOSIT = "0xdcbc1c05240f31ff3ad067ef1ee35ce4997762752e3a095284754544f4c709d7"
TOPIC_WITHDRAW = "0xfbde797d201c681b91056529119e0b02407c7bb96a4a2c75c01fc9667232c8db"
TOPIC_LOG_UPDATE_RATES = "0x9dd85e9767d796973b86c6ccf3a294429cfd5e3e93fa23ac388b9277bb8283fd"
TOPIC_FLUID_OPERATE = "0x4d93b232a24e82b284ced7461bf4deacffe66759d5c24513e6f29e571ad78d15"
TOPIC_FLUID_UPDATE_EXCHANGE_PRICES = "0x96c40bed7fc8d0ac41633a3bd47f254f0b0076e5df70975c51d23514bc49d3b8"
EVENT_TOPICS = (TOPIC_TRANSFER, TOPIC_DEPOSIT, TOPIC_WITHDRAW, TOPIC_LOG_UPDATE_RATES)
EVENT_NAMES = {
    TOPIC_TRANSFER: "Transfer",
    TOPIC_DEPOSIT: "Deposit",
    TOPIC_WITHDRAW: "Withdraw",
    TOPIC_LOG_UPDATE_RATES: "LogUpdateRates",
}
STATIC_FTOKENS = [
    "0x9fb7b4477576fe5b32be4c1843afb1e55f251b33",
    "0x90551c1795392094fe6d29b758eccd233cfaa260",
    "0x5c20b550819128074fd538edf79791733ccedd18",
    "0x2411802d8bea09be0af8fd8d08314a63e706b29c",
    "0x6a29a46e21c730dca1d8b23d637c101cec605c5b",
    "0x2bbe31d63e6813e3ac858c04dae43fb2a72b0d11",
    "0x15e8c742614b5d8db4083a41df1a14f5d2bfb400",
]
SELECTORS = {
    "allTokens": "0x6ff97f1d",
    "asset": "0x38d52e0f",
    "underlyingAsset": "0xb16a19de",
    "symbol": "0x95d89b41",
    "decimals": "0x313ce567",
    "totalAssets": "0x01e1d114",
    "totalSupply": "0x18160ddd",
    "convertToAssets": "0x07a2d13a",
}
TIMESERIES_COLUMNS = [
    "chain_id", "timestamp", "block_number", "product_id", "symbol", "underlying",
    "total_assets_raw", "total_supply_raw", "replay_total_supply_raw", "assets_per_share",
    "price_usd", "supply_usd", "deposit_assets_raw", "withdraw_assets_raw", "mint_shares_raw",
    "burn_shares_raw", "transfer_count", "deposit_count", "withdraw_count", "event_count",
    "supply_raw_diff", "state_status", "provenance",
]


def _rpc_url(args) -> str:
    value = (getattr(args, "rpc_url", None) or os.getenv("MAINNET_RPC_URL") or os.getenv("ETH_RPC_URL") or "").strip()
    if not value:
        raise SystemExit("MAINNET_RPC_URL, ETH_RPC_URL, or --rpc-url is required")
    return value


def _ch_client():
    settings = {}
    if os.getenv("CLICKHOUSE_ASYNC_INSERT", "true").strip().lower() in {"1", "true", "yes"}:
        settings["async_insert"] = 1
        settings["wait_for_async_insert"] = 1
    return clickhouse_connect.get_client(host=os.getenv("CLICKHOUSE_HOST", "127.0.0.1"), port=int(os.getenv("CLICKHOUSE_PORT", "8123")), username=os.getenv("CLICKHOUSE_USER", "default"), password=os.getenv("CLICKHOUSE_PASSWORD", ""), settings=settings)


def _insert_rows_batched(ch, table: str, rows: list[list], column_names: list[str], batch_size: int = 20000) -> int:
    written = 0
    for start in range(0, len(rows), batch_size):
        chunk = rows[start:start + batch_size]
        if chunk:
            ch.insert(table, chunk, column_names=column_names)
            written += len(chunk)
    return written


def _table_exists(ch, table: str) -> bool:
    return bool(ch.query("EXISTS TABLE " + table).result_rows[0][0])


def normalize_address(value: str | None) -> str:
    raw = str(value or "").lower().removeprefix("0x")
    return "0x" + raw[-40:].rjust(40, "0")


def _liquidity_token(value: str | None) -> str:
    token = normalize_address(value)
    return NATIVE_ETH_ADDRESS if token == WETH_ADDRESS else token


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
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            last_error = f"HTTP {status}"
            if attempt < retries:
                time.sleep(min(2.0 * (attempt + 1), 5.0))
        except requests.RequestException as exc:
            last_error = type(exc).__name__
            if attempt < retries:
                time.sleep(min(2.0 * (attempt + 1), 5.0))
        except Exception as exc:
            last_error = type(exc).__name__
            if attempt < retries:
                time.sleep(min(2.0 * (attempt + 1), 5.0))
    raise RuntimeError(f"RPC {method} failed after {retries + 1} attempts: {last_error}")


def _eth_call(rpc_url: str, to: str, data: str, block: int | str, *, timeout: int, retries: int) -> str:
    tag = hex(block) if isinstance(block, int) else block
    return str(_rpc_call(rpc_url, "eth_call", [{"to": to, "data": data}, tag], timeout=timeout, retries=retries) or "0x")


def _int_hex(value: str | int | None) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return int(value, 16) if value.startswith("0x") else int(value)
    return int(value)


def _decode_uint(raw: str) -> int:
    return int(str(raw or "0x0"), 16)


def _decode_address(raw: str) -> str:
    return normalize_address(str(raw or "0x").removeprefix("0x")[-40:])


def _decode_string(raw: str) -> str:
    hex_data = str(raw or "0x").removeprefix("0x")
    if not hex_data:
        return ""
    try:
        if len(hex_data) >= 128:
            offset = int(hex_data[:64], 16) * 2
            length = int(hex_data[offset:offset + 64], 16) * 2
            return bytes.fromhex(hex_data[offset + 64:offset + 64 + length]).decode("utf-8", "ignore")
        return bytes.fromhex(hex_data[:64]).rstrip(b"\x00").decode("utf-8", "ignore")
    except Exception:
        return ""


def _decode_address_array(raw: str) -> list[str]:
    hex_data = str(raw or "0x").removeprefix("0x")
    if len(hex_data) < 128:
        return []
    offset = int(hex_data[:64], 16) * 2
    length = int(hex_data[offset:offset + 64], 16)
    values = []
    pos = offset + 64
    for idx in range(length):
        word = hex_data[pos + idx * 64:pos + (idx + 1) * 64]
        values.append(normalize_address(word[-40:]))
    return values


def _call_uint(rpc_url: str, to: str, selector: str, block: int, *, timeout: int, retries: int) -> int:
    return _decode_uint(_eth_call(rpc_url, to, selector, block, timeout=timeout, retries=retries))


def _call_address(rpc_url: str, to: str, selector: str, block: int, *, timeout: int, retries: int) -> str:
    return _decode_address(_eth_call(rpc_url, to, selector, block, timeout=timeout, retries=retries))


def _call_string(rpc_url: str, to: str, selector: str, block: int, *, timeout: int, retries: int) -> str:
    return _decode_string(_eth_call(rpc_url, to, selector, block, timeout=timeout, retries=retries))


def _call_uint_arg(rpc_url: str, to: str, selector: str, value: int, block: int, *, timeout: int, retries: int) -> int:
    return _decode_uint(_eth_call(rpc_url, to, selector + f"{int(value):064x}", block, timeout=timeout, retries=retries))


def _topic_address(topic: str | None) -> str:
    return normalize_address(str(topic or "").removeprefix("0x")[-40:])


def _word_uint(data: str | None, index: int = 0) -> int:
    raw = str(data or "").removeprefix("0x")
    word = raw[index * 64:index * 64 + 64]
    return int(word, 16) if len(word) == 64 else 0


def _clip(value: float, low: float = 0.0, high: float = 10.0) -> float:
    if value != value:
        return low
    return max(low, min(float(value), high))


def _ratio_or_bps(raw: int, high: float = 10.0) -> float:
    if raw <= 0:
        return 0.0
    if raw <= 10_000:
        return _clip(raw / 10_000.0, 0.0, high)
    return _clip(raw / 1e18, 0.0, high)


def _fluid_supply_exchange_price(topic0: str, topic1: str | None, topic2: str | None, data: str | None, previous_supply_apy: float = 0.0) -> tuple[str, int, float]:
    if str(topic0).lower() == TOPIC_FLUID_UPDATE_EXCHANGE_PRICES:
        borrow_apy = _ratio_or_bps(_word_uint(data, 0), high=10.0)
        utilization = _ratio_or_bps(_word_uint(data, 1), high=1.0)
        supply_apy = borrow_apy * utilization
        return _topic_address(topic1), int(str(topic2 or "0x0").removeprefix("0x") or "0", 16), supply_apy or previous_supply_apy
    w5 = _word_uint(data, 5)
    util_raw = (w5 >> 30) & 0x3FFF
    rate_raw = w5 & 0xFFFF
    fee_raw = (w5 >> 16) & 0x3FFF
    borrow_apy = _clip(rate_raw / 10_000.0, 0.0, 10.0)
    utilization = _clip(util_raw / 10_000.0, 0.0, 1.0)
    fee = _clip(fee_raw / 10_000.0, 0.0, 1.0)
    supply_apy = max(0.0, borrow_apy * utilization * (1.0 - fee))
    return _topic_address(topic2), ((w5 >> 91) & ((1 << 64) - 1)) or 10**12, supply_apy


def _hex_block(block: int) -> str:
    return hex(int(block))


def _confirmed_head(rpc_url: str, *, timeout: int, retries: int) -> int:
    head = _int_hex(_rpc_call(rpc_url, "eth_blockNumber", [], timeout=timeout, retries=retries))
    return max(0, head - CONFIRMATIONS)


def _block_timestamp(rpc_url: str, block_number: int, cache: dict[int, dt.datetime], *, timeout: int, retries: int) -> dt.datetime:
    block_number = int(block_number)
    if block_number not in cache:
        block = _rpc_call(rpc_url, "eth_getBlockByNumber", [_hex_block(block_number), False], timeout=timeout, retries=retries)
        cache[block_number] = dt.datetime.fromtimestamp(_int_hex(block.get("timestamp")), tz=dt.UTC).replace(tzinfo=None)
    return cache[block_number]


def _discover_ftokens(rpc_url: str, block: int, *, timeout: int, retries: int) -> list[str]:
    return _decode_address_array(_eth_call(rpc_url, FLUID_LENDING_FACTORY, SELECTORS["allTokens"], block, timeout=timeout, retries=retries))


def _fetch_logs(rpc_url: str, addresses: list[str], from_block: int, to_block: int, *, timeout: int, retries: int) -> list[dict[str, Any]]:
    if not addresses or to_block < from_block:
        return []
    params = {"fromBlock": _hex_block(from_block), "toBlock": _hex_block(to_block), "address": addresses, "topics": [list(EVENT_TOPICS)]}
    return list(_rpc_call(rpc_url, "eth_getLogs", [params], timeout=timeout, retries=retries) or [])


def _fetch_logs_resilient(rpc_url: str, addresses: list[str], from_block: int, to_block: int, *, timeout: int, retries: int) -> list[dict[str, Any]]:
    try:
        return _fetch_logs(rpc_url, addresses, from_block, to_block, timeout=timeout, retries=retries)
    except Exception:
        if from_block >= to_block:
            raise
        mid = (from_block + to_block) // 2
        return _fetch_logs_resilient(rpc_url, addresses, from_block, mid, timeout=timeout, retries=retries) + _fetch_logs_resilient(rpc_url, addresses, mid + 1, to_block, timeout=timeout, retries=retries)


def _log_key(log: dict[str, Any]) -> tuple[int, int, str, str, str]:
    topics = [str(t).lower() for t in (log.get("topics") or [])]
    return (_int_hex(log.get("blockNumber")), _int_hex(log.get("logIndex")), str(log.get("address") or "").lower(), topics[0] if topics else "", str(log.get("transactionHash") or "").lower())


def _row_key(row) -> tuple[int, int, str, str, str]:
    return (int(row[0]), int(row[1]), str(row[2]).lower(), str(row[3]).lower(), str(row[4]).lower())


def _existing_keys(ch, from_block: int, to_block: int) -> set[tuple[int, int, str, str, str]]:
    if not _table_exists(ch, "fluid_product_raw_events"):
        return set()
    rows = ch.query("""
        SELECT block_number, log_index, lower(contract), lower(topic0), lower(tx_hash)
        FROM fluid_product_raw_events FINAL
        WHERE product_type = 'FTOKEN' AND block_number >= %(from_block)s AND block_number <= %(to_block)s
        """, parameters={"from_block": int(from_block), "to_block": int(to_block)}).result_rows
    return {_row_key(row) for row in rows}


def _discover_ftokens_for_block(rpc_url: str, ch, block: int, *, timeout: int, retries: int) -> list[str]:
    try:
        discovered = _discover_ftokens(rpc_url, block, timeout=timeout, retries=retries)
    except Exception:
        discovered = []
    registry = []
    if _table_exists(ch, "fluid_contract_registry"):
        registry = [normalize_address(row[0]) for row in ch.query("""
            SELECT contract FROM fluid_contract_registry FINAL
            WHERE product_type = 'FTOKEN' AND name = 'Fluid fToken' AND active = 1
        """).result_rows]
    tokens = discovered + registry
    if not tokens:
        tokens = STATIC_FTOKENS
    return sorted({normalize_address(token) for token in tokens if normalize_address(token) != ZERO_ADDRESS})


def _insert_contract_registry(ch, tokens: list[str]) -> None:
    if tokens:
        ch.insert("fluid_contract_registry", [[ETHEREUM_CHAIN_ID, "FTOKEN", token, "", "Fluid fToken", 0, 1, "", "discovered_by=allTokens"] for token in tokens], column_names=["chain_id", "product_type", "contract", "factory", "name", "created_block", "active", "resolver", "metadata"])


def _insert_logs(ch, rpc_url: str, logs: list[dict[str, Any]], block_cache: dict[int, dt.datetime], *, timeout: int, retries: int) -> int:
    rows = []
    for log in logs:
        topics = [str(t).lower() for t in (log.get("topics") or [])]
        topic0 = topics[0] if topics else ""
        block_number = _int_hex(log.get("blockNumber"))
        rows.append([ETHEREUM_CHAIN_ID, "FTOKEN", block_number, _block_timestamp(rpc_url, block_number, block_cache, timeout=timeout, retries=retries), str(log.get("transactionHash") or "").lower(), _int_hex(log.get("logIndex")), str(log.get("address") or "").lower(), EVENT_NAMES.get(topic0, ""), topic0, topics[1] if len(topics) > 1 else None, topics[2] if len(topics) > 2 else None, topics[3] if len(topics) > 3 else None, str(log.get("data") or "0x")])
    return _insert_rows_batched(ch, "fluid_product_raw_events", rows, ["chain_id", "product_type", "block_number", "block_timestamp", "tx_hash", "log_index", "contract", "event_name", "topic0", "topic1", "topic2", "topic3", "data"])


def _token_meta(ch, rpc_url: str, token: str, block: int, *, timeout: int, retries: int) -> tuple[str, int]:
    rows = []
    if _table_exists(ch, "fluid_reserve_state"):
        rows = ch.query("SELECT any(symbol), any(decimals) FROM fluid_reserve_state FINAL WHERE token = %(token)s", parameters={"token": token}).result_rows
    if rows and rows[0][0]:
        return str(rows[0][0]), int(rows[0][1] or 18)
    symbol = _call_string(rpc_url, token, SELECTORS["symbol"], block, timeout=timeout, retries=retries) or token[:10]
    decimals = _call_uint(rpc_url, token, SELECTORS["decimals"], block, timeout=timeout, retries=retries) or 18
    return symbol, int(decimals)


def _price_usd(ch, token: str) -> float:
    queries = [
        "SELECT argMax(price_usd, tuple(timestamp, block_number)) FROM fluid_product_components FINAL WHERE token = %(token)s AND price_usd > 0",
        "SELECT argMax(price_usd, timestamp) FROM fluid_reserve_metrics FINAL WHERE token = %(token)s AND price_usd > 0",
    ]
    for query in queries:
        if _table_exists(ch, query.split(" FROM ")[1].split()[0]):
            rows = ch.query(query, parameters={"token": token}).result_rows
            if rows and rows[0][0]:
                return float(rows[0][0])
    return 0.0


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
    from analytics.sources.fluid_ftoken import FluidFTokenSource

    source = FluidFTokenSource()
    if not args.dry_run:
        ensure_fluid_full_coverage_tables(ch)
    source._load_contracts(ch)

    client = hypersync.HypersyncClient(hypersync.ClientConfig(
        url="https://eth.hypersync.xyz",
        bearer_token=require_hypersync_token(),
    ))
    head = int(await client.get_height()) - CONFIRMATIONS
    to_block = int(args.to_block or 0) or head
    from_block = int(args.from_block or 0) or FTOKEN_GENESIS_BLOCK
    if to_block < from_block:
        payload = {
            "status": "OK",
            "dryRun": bool(args.dry_run),
            "source": "envio_hypersync",
            "fromBlock": from_block,
            "toBlock": to_block,
            "fTokens": len(source.contracts),
            "rawLogs": 0,
            "matchedLogs": 0,
            "insertedLogs": 0,
            "pages": 0,
        }
        return payload

    log_selection = source.log_selection()
    current_start = from_block
    raw_logs = 0
    matched_logs = 0
    inserted_logs = 0
    pages = 0
    batches = 0
    batch_blocks = int(args.batch_blocks)
    progress_every = int(args.progress_every or 0)

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

        source_logs = [entry for entry in mempool_logs if source.route(entry)]
        raw_logs += len(mempool_logs)
        matched_logs += len(source_logs)
        if source_logs and not args.dry_run:
            inserted_logs += source.insert_raw(ch, source_logs, build_block_ts_map(mempool_blocks))

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
        "fTokens": len(source.contracts),
        "rawLogs": raw_logs,
        "matchedLogs": matched_logs,
        "insertedLogs": inserted_logs,
        "pages": pages,
    }


def collect_envio(args, ch=None) -> int:
    owned = ch is None
    ch = ch or _ch_client()
    try:
        import asyncio

        payload = asyncio.run(_collect_envio_async(args, ch))
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    finally:
        if owned:
            ch.close()


def collect(args, ch=None) -> int:
    owned = ch is None
    ch = ch or _ch_client()
    try:
        if not args.dry_run:
            ensure_fluid_full_coverage_tables(ch)
        rpc_url = _rpc_url(args)
        timeout, retries = int(args.http_timeout_sec), int(args.retries)
        to_block = int(args.to_block or 0) or _confirmed_head(rpc_url, timeout=timeout, retries=retries)
        from_block = int(args.from_block or 0) or FTOKEN_GENESIS_BLOCK
        tokens = _discover_ftokens_for_block(rpc_url, ch, to_block, timeout=timeout, retries=retries)
        existing = _existing_keys(ch, from_block, to_block)
        missing: list[dict[str, Any]] = []
        rpc_logs = 0
        for start in range(from_block, to_block + 1, int(args.batch_blocks)):
            end = min(to_block, start + int(args.batch_blocks) - 1)
            logs = _fetch_logs_resilient(rpc_url, tokens, start, end, timeout=timeout, retries=retries)
            rpc_logs += len(logs)
            missing.extend([log for log in logs if _log_key(log) not in existing])
        inserted = 0
        if not args.dry_run:
            _insert_contract_registry(ch, tokens)
            inserted = _insert_logs(ch, rpc_url, missing, {}, timeout=timeout, retries=retries)
        print(json.dumps({"status": "OK", "dryRun": bool(args.dry_run), "fromBlock": from_block, "toBlock": to_block, "fTokens": len(tokens), "rpcLogs": rpc_logs, "existingLogs": len(existing), "missingLogs": len(missing), "insertedLogs": inserted}, indent=2, sort_keys=True))
        return 0
    finally:
        if owned:
            ch.close()


@dataclass
class ReplayState:
    supply_raw: int = 0
    token_exchange_price: int = 10**12
    liquidity_exchange_price: int = 10**12
    deposit_assets_raw: int = 0
    withdraw_assets_raw: int = 0
    mint_shares_raw: int = 0
    burn_shares_raw: int = 0
    transfer_count: int = 0
    deposit_count: int = 0
    withdraw_count: int = 0
    rate_update_count: int = 0
    event_count: int = 0


def _snapshot_rpc(rpc_url: str, ch, token: str, block: int, *, timeout: int, retries: int) -> dict[str, Any]:
    symbol = _call_string(rpc_url, token, SELECTORS["symbol"], block, timeout=timeout, retries=retries) or token[:10]
    underlying = _call_address(rpc_url, token, SELECTORS["asset"], block, timeout=timeout, retries=retries)
    if underlying == ZERO_ADDRESS:
        underlying = _call_address(rpc_url, token, SELECTORS["underlyingAsset"], block, timeout=timeout, retries=retries)
    share_decimals = _call_uint(rpc_url, token, SELECTORS["decimals"], block, timeout=timeout, retries=retries) or 18
    total_assets = _call_uint(rpc_url, token, SELECTORS["totalAssets"], block, timeout=timeout, retries=retries)
    total_supply = _call_uint(rpc_url, token, SELECTORS["totalSupply"], block, timeout=timeout, retries=retries)
    share_assets = _call_uint_arg(rpc_url, token, SELECTORS["convertToAssets"], 10 ** int(share_decimals), block, timeout=timeout, retries=retries)
    _underlying_symbol, underlying_decimals = _token_meta(ch, rpc_url, underlying, block, timeout=timeout, retries=retries)
    price = _price_usd(ch, underlying)
    supply_usd = float(total_assets or 0) / float(10 ** int(underlying_decimals or 18)) * price
    return {"symbol": symbol, "underlying": underlying, "total_assets_raw": int(total_assets or 0), "total_supply_raw": int(total_supply or 0), "assets_per_share": float(share_assets or 0) / float(10 ** int(underlying_decimals or 18)), "price_usd": price, "supply_usd": supply_usd, "errors": []}


def _snapshot_event_only(ch, token: str, block: int, timestamp: dt.datetime, state: ReplayState, meta: dict[str, dict[str, Any]], liquidity_prices: dict[str, list[tuple[int, int, int]]]) -> dict[str, Any]:
    symbol = token[:10]
    underlying = ""
    decimals = 18
    item = meta.get(token.lower()) or {}
    if item:
        symbol = str(item.get("symbol") or symbol)
        underlying = str(item.get("underlying") or "")
        decimals = int(item.get("decimals") or decimals)
        price = float(item.get("price_usd") or 0.0)
    elif _table_exists(ch, "fluid_product_components"):
        rows = ch.query("""
            SELECT symbol, token, price_usd, decimals
            FROM fluid_product_components FINAL
            WHERE product_type = 'FTOKEN'
              AND product_id = %(token)s
              AND block_number <= %(block)s
            ORDER BY block_number DESC
            LIMIT 1
            """, parameters={"token": token.lower(), "block": int(block)}).result_rows
        if rows:
            symbol = str(rows[0][0] or symbol)
            underlying = str(rows[0][1] or "")
            price = float(rows[0][2] or 0.0)
            decimals = int(rows[0][3] or decimals)
        else:
            price = 0.0
    else:
        price = 0.0
    if not underlying and _table_exists(ch, "fluid_product_snapshots"):
        rows = ch.query("""
            SELECT symbol, underlying
            FROM fluid_product_snapshots FINAL
            WHERE product_type = 'FTOKEN'
              AND product_id = %(token)s
              AND block_number <= %(block)s
            ORDER BY block_number DESC
            LIMIT 1
            """, parameters={"token": token.lower(), "block": int(block)}).result_rows
        if rows:
            symbol = str(rows[0][0] or symbol)
            underlying = str(rows[0][1] or underlying)
    live_liquidity_exchange_price = _latest_liquidity_exchange(liquidity_prices, _liquidity_token(underlying), int(block), timestamp=timestamp)
    live_token_exchange_price = _live_token_exchange_price(state, live_liquidity_exchange_price)
    total_assets_raw = int(state.supply_raw) * int(live_token_exchange_price) // 10**12
    supply_usd = float(total_assets_raw) / float(10 ** int(decimals)) * price if decimals >= 0 else 0.0
    return {
        "symbol": symbol,
        "underlying": underlying,
        "total_assets_raw": int(total_assets_raw),
        "total_supply_raw": int(state.supply_raw),
        "assets_per_share": float(live_token_exchange_price) / 1e12,
        "price_usd": price,
        "supply_usd": supply_usd,
        "errors": [],
        "token_exchange_price": int(live_token_exchange_price),
        "liquidity_exchange_price": int(live_liquidity_exchange_price),
    }


def _load_ftoken_meta(ch, to_block: int) -> dict[str, dict[str, Any]]:
    meta: dict[str, dict[str, Any]] = {}
    if _table_exists(ch, "fluid_product_components"):
        rows = ch.query("""
            SELECT product_id, argMax(symbol, tuple(timestamp, block_number)), argMax(token, tuple(timestamp, block_number)),
                   argMax(decimals, tuple(timestamp, block_number)), argMax(price_usd, tuple(timestamp, block_number))
            FROM fluid_product_components FINAL
            WHERE product_type = 'FTOKEN' AND block_number <= %(block)s
            GROUP BY product_id
            """, parameters={"block": int(to_block)}).result_rows
        for product_id, symbol, underlying, decimals, price_usd in rows:
            meta[str(product_id).lower()] = {
                "symbol": str(symbol or str(product_id)[:10]),
                "underlying": normalize_address(str(underlying or "")) if underlying else "",
                "decimals": int(decimals or 18),
                "price_usd": float(price_usd or 0.0),
            }
    if _table_exists(ch, "fluid_product_snapshots"):
        rows = ch.query("""
            SELECT product_id, argMax(symbol, tuple(timestamp, block_number)), argMax(underlying, tuple(timestamp, block_number))
            FROM fluid_product_snapshots FINAL
            WHERE product_type = 'FTOKEN' AND block_number <= %(block)s
            GROUP BY product_id
            """, parameters={"block": int(to_block)}).result_rows
        for product_id, symbol, underlying in rows:
            item = meta.setdefault(str(product_id).lower(), {})
            item.setdefault("symbol", str(symbol or str(product_id)[:10]))
            if underlying:
                item.setdefault("underlying", normalize_address(str(underlying)))
    return meta


def _load_liquidity_exchange_prices(ch, underlyings: set[str], to_block: int) -> dict[str, dict[str, list]]:
    if not underlyings or not _table_exists(ch, "fluid_events"):
        return {}
    tokens = ", ".join("'" + token.lower().replace("'", "''") + "'" for token in sorted(underlyings) if token)
    if not tokens:
        return {}
    rows = ch.query(f"""
        SELECT block_number, block_timestamp, log_index, lower(topic0), topic1, topic2, data
        FROM fluid_events FINAL
        WHERE block_number <= %(block)s
          AND lower(topic0) IN (%(operate)s, %(exchange)s)
          AND multiIf(
                lower(topic0) = %(exchange)s,
                lower(concat('0x', substring(ifNull(topic1, ''), 27))),
                lower(concat('0x', substring(ifNull(topic2, ''), 27)))
              ) IN ({tokens})
        ORDER BY block_number ASC, log_index ASC
        """, parameters={"block": int(to_block), "operate": TOPIC_FLUID_OPERATE, "exchange": TOPIC_FLUID_UPDATE_EXCHANGE_PRICES}).result_rows
    by_token: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
    last_supply_apy: dict[str, float] = {}
    for block, block_ts, log_index, topic0, topic1, topic2, data in rows:
        token_hint = _topic_address(topic1 if str(topic0).lower() == TOPIC_FLUID_UPDATE_EXCHANGE_PRICES else topic2)
        token, exchange_price, supply_apy = _fluid_supply_exchange_price(str(topic0).lower(), topic1, topic2, data, last_supply_apy.get(token_hint.lower(), 0.0))
        if exchange_price:
            last_supply_apy[token.lower()] = float(supply_apy or 0.0)
            by_token[token.lower()].append((int(block), int(log_index or 0), int(exchange_price), block_ts, float(supply_apy or 0.0)))
    return {
        token: {
            "keys": [(block, log_index) for block, log_index, _exchange_price, _timestamp, _supply_apy in series],
            "values": [(exchange_price, timestamp, supply_apy) for _block, _log_index, exchange_price, timestamp, supply_apy in series],
        }
        for token, series in by_token.items()
    }


def _latest_liquidity_exchange(liquidity_prices: dict[str, dict[str, list]], token: str, block: int, log_index: int = 10**9, timestamp: dt.datetime | None = None) -> int:
    series = liquidity_prices.get(str(token or "").lower()) or {}
    keys = series.get("keys") or []
    values = series.get("values") or []
    if not keys:
        return 10**12
    idx = bisect_right(keys, (int(block), int(log_index))) - 1
    if idx < 0:
        return 10**12
    exchange_price, exchange_ts, supply_apy = values[idx]
    exchange_price = int(exchange_price)
    if timestamp is None or exchange_ts is None or not supply_apy:
        return exchange_price
    elapsed = max(0.0, (timestamp - exchange_ts).total_seconds())
    return exchange_price + int(float(exchange_price) * float(supply_apy) * elapsed / SECONDS_PER_YEAR)


def _live_token_exchange_price(state: ReplayState, live_liquidity_exchange_price: int) -> int:
    old_liquidity = int(state.liquidity_exchange_price or 10**12)
    old_token = int(state.token_exchange_price or 10**12)
    live_liquidity = int(live_liquidity_exchange_price or old_liquidity)
    if old_liquidity <= 0 or live_liquidity < old_liquidity:
        return old_token
    total_return_in_percent = ((live_liquidity - old_liquidity) * 10**14) // old_liquidity
    return old_token + ((old_token * total_return_in_percent) // 10**14)


def _load_raw_events(ch, from_block: int, to_block: int) -> list[tuple]:
    if not _table_exists(ch, "fluid_product_raw_events"):
        return []
    return ch.query("""
        SELECT block_number, block_timestamp, lower(contract), lower(topic0), ifNull(lower(topic1), ''), ifNull(lower(topic2), ''), data, tx_hash, log_index
        FROM fluid_product_raw_events FINAL
        WHERE product_type = 'FTOKEN' AND block_number >= %(from_block)s AND block_number <= %(to_block)s
        ORDER BY block_number ASC, log_index ASC, contract ASC, topic0 ASC
        """, parameters={"from_block": int(from_block), "to_block": int(to_block)}).result_rows


def _replay_events(events: list[tuple], snapshot_mode: str, anchor_block: int, meta: dict[str, dict[str, Any]], liquidity_prices: dict[str, list[tuple[int, int, int]]]) -> tuple[dict[str, ReplayState], dict[tuple[str, int], ReplayState]]:
    states: dict[str, ReplayState] = defaultdict(ReplayState)
    points: dict[tuple[str, int], ReplayState] = {}
    daily_last: dict[tuple[str, str], int] = {}
    for block, ts, contract, topic0, topic1, topic2, data, _tx, _log_index in events:
        state = states[contract]
        if topic0 == TOPIC_TRANSFER:
            value = _word_uint(data, 0)
            if _topic_address(topic1) == ZERO_ADDRESS:
                state.supply_raw += value
                state.mint_shares_raw += value
            if _topic_address(topic2) == ZERO_ADDRESS:
                state.supply_raw -= value
                state.burn_shares_raw += value
            state.transfer_count += 1
        elif topic0 == TOPIC_DEPOSIT:
            assets = _word_uint(data, 0)
            shares = _word_uint(data, 1)
            state.deposit_assets_raw += assets
            if assets and shares:
                state.token_exchange_price = int(assets) * 10**12 // int(shares)
                underlying = meta.get(contract, {}).get("underlying", "")
                state.liquidity_exchange_price = _latest_liquidity_exchange(liquidity_prices, _liquidity_token(underlying), int(block), int(_log_index or 0), ts)
            state.deposit_count += 1
        elif topic0 == TOPIC_WITHDRAW:
            assets = _word_uint(data, 0)
            shares = _word_uint(data, 1)
            state.withdraw_assets_raw += assets
            if assets and shares:
                state.token_exchange_price = int(assets) * 10**12 // int(shares)
                underlying = meta.get(contract, {}).get("underlying", "")
                state.liquidity_exchange_price = _latest_liquidity_exchange(liquidity_prices, _liquidity_token(underlying), int(block), int(_log_index or 0), ts)
            state.withdraw_count += 1
        elif topic0 == TOPIC_LOG_UPDATE_RATES:
            token_exchange_price = _word_uint(data, 0)
            liquidity_exchange_price = _word_uint(data, 1)
            if token_exchange_price:
                state.token_exchange_price = token_exchange_price
            if liquidity_exchange_price:
                state.liquidity_exchange_price = liquidity_exchange_price
            state.rate_update_count += 1
        state.event_count += 1
        if snapshot_mode == "event":
            points[(contract, int(block))] = ReplayState(**state.__dict__)
        elif snapshot_mode == "daily":
            daily_last[(contract, str(ts)[:10])] = int(block)
            points[(contract, int(block))] = ReplayState(**state.__dict__)
    if snapshot_mode == "daily" and daily_last:
        keep = {(contract_day[0], block) for contract_day, block in daily_last.items()}
        points = {key: val for key, val in points.items() if key in keep}
    if anchor_block > 0:
        for contract, state in states.items():
            points[(contract, int(anchor_block))] = ReplayState(**state.__dict__)
    return dict(states), points


def replay(args, ch=None) -> int:
    owned = ch is None
    ch = ch or _ch_client()
    try:
        if not args.dry_run:
            ensure_fluid_full_coverage_tables(ch)
        rpc_url = _rpc_url(args) if args.rpc_state else ""
        timeout, retries = int(args.http_timeout_sec), int(args.retries)
        if int(args.to_block or 0):
            to_block = int(args.to_block)
        elif args.rpc_state:
            to_block = _confirmed_head(rpc_url, timeout=timeout, retries=retries)
        elif _table_exists(ch, "fluid_product_raw_events"):
            rows = ch.query(
                "SELECT max(block_number) FROM fluid_product_raw_events WHERE product_type = %(product_type)s",
                parameters={"product_type": "FTOKEN"},
            ).result_rows
            raw_head = rows[0][0] if rows else 0
            to_block = int(raw_head) if raw_head else FTOKEN_GENESIS_BLOCK
        else:
            to_block = FTOKEN_GENESIS_BLOCK
        from_block = int(args.from_block or 0) or FTOKEN_GENESIS_BLOCK
        events = _load_raw_events(ch, from_block, to_block)
        meta = _load_ftoken_meta(ch, to_block)
        liquidity_prices = _load_liquidity_exchange_prices(
            ch,
            {_liquidity_token(str(item.get("underlying") or "")).lower() for item in meta.values() if item.get("underlying")},
            to_block,
        )
        states, points = _replay_events(events, args.snapshot_mode, to_block if args.include_anchor else 0, meta, liquidity_prices)
        block_ts = {int(row[0]): row[1] for row in events}
        latest_ts = {}
        for block, ts, contract, *_rest in events:
            latest_ts[str(contract).lower()] = ts
        rows = []
        drift = []
        for idx, ((token, block), state) in enumerate(sorted(points.items(), key=lambda item: (item[0][1], item[0][0]))):
            if args.max_snapshot_points and idx >= int(args.max_snapshot_points):
                break
            ts = block_ts.get(block) or latest_ts.get(token.lower()) or dt.datetime.now(dt.UTC).replace(tzinfo=None)
            if args.rpc_state:
                snap = _snapshot_rpc(rpc_url, ch, token, block, timeout=timeout, retries=retries)
                diff = int(snap["total_supply_raw"]) - int(state.supply_raw)
            else:
                snap = _snapshot_event_only(ch, token, block, ts, state, meta, liquidity_prices)
                diff = 0
            if diff:
                drift.append({"token": token, "block": block, "replaySupplyRaw": str(state.supply_raw), "rpcSupplyRaw": str(snap["total_supply_raw"]), "diff": str(diff)})
            state_status = "OK" if args.rpc_state and diff == 0 and not snap["errors"] else "DRIFT" if diff else "EVENT_REPLAY_INDEXED"
            provenance = {
                "source": "fluid_ftoken_event_replay",
                "collection": "envio_hypersync",
                "assetFormula": "totalSupplyRaw * tokenExchangePrice / 1e12",
                "tokenExchangePrice": str(snap.get("token_exchange_price", state.token_exchange_price)),
                "storedTokenExchangePrice": str(state.token_exchange_price),
                "liquidityExchangePrice": str(snap.get("liquidity_exchange_price", state.liquidity_exchange_price)),
                "storedLiquidityExchangePrice": str(state.liquidity_exchange_price),
                "rateUpdateCount": int(state.rate_update_count),
                "rpcMethods": [],
            }
            if args.rpc_state:
                provenance["rpcMethods"] = ["totalAssets", "totalSupply", "convertToAssets"]
                provenance["rpcErrors"] = snap["errors"]
            rows.append([ETHEREUM_CHAIN_ID, ts, block, token, snap["symbol"], snap["underlying"], str(snap["total_assets_raw"]), str(snap["total_supply_raw"]), str(state.supply_raw), float(snap["assets_per_share"]), float(snap["price_usd"]), float(snap["supply_usd"]), str(state.deposit_assets_raw), str(state.withdraw_assets_raw), str(state.mint_shares_raw), str(state.burn_shares_raw), int(state.transfer_count), int(state.deposit_count), int(state.withdraw_count), int(state.event_count), str(diff), state_status, json.dumps(provenance, sort_keys=True)])
        written = 0
        if rows and not args.dry_run:
            written = _insert_rows_batched(ch, "fluid_ftoken_timeseries", rows, TIMESERIES_COLUMNS)
        print(json.dumps({"status": "OK" if not drift else "DRIFT", "dryRun": bool(args.dry_run), "replayMode": "rpc_state" if args.rpc_state else "event_only", "fromBlock": from_block, "toBlock": to_block, "rawEvents": len(events), "liquidityIndexTokens": len(liquidity_prices), "fTokensWithEvents": len(states), "snapshotRows": len(rows), "writtenRows": written, "supplyDriftCount": len(drift), "sampleDrift": drift[:10]}, indent=2, sort_keys=True))
        return 1 if drift and args.fail_on_drift else 0
    finally:
        if owned:
            ch.close()


def anchor(args, ch=None) -> int:
    owned = ch is None
    ch = ch or _ch_client()
    try:
        if args.write_validation:
            ensure_fluid_full_coverage_tables(ch)
        rpc_url = _rpc_url(args)
        timeout, retries = int(args.http_timeout_sec), int(args.retries)
        to_block = int(args.block_number or 0) or _confirmed_head(rpc_url, timeout=timeout, retries=retries)
        from_block = int(args.from_block or 0) or max(0, to_block - int(args.recent_blocks))
        tokens = _discover_ftokens_for_block(rpc_url, ch, to_block, timeout=timeout, retries=retries)
        rpc_logs = []
        for start in range(from_block, to_block + 1, int(args.batch_blocks)):
            end = min(to_block, start + int(args.batch_blocks) - 1)
            rpc_logs.extend(_fetch_logs_resilient(rpc_url, tokens, start, end, timeout=timeout, retries=retries))
        db_keys = _existing_keys(ch, from_block, to_block)
        rpc_keys = {_log_key(log) for log in rpc_logs}
        missing = sorted(rpc_keys - db_keys)
        extra = sorted(db_keys - rpc_keys)
        events = _load_raw_events(ch, 0, to_block)
        meta = _load_ftoken_meta(ch, to_block)
        liquidity_prices = _load_liquidity_exchange_prices(
            ch,
            {_liquidity_token(str(item.get("underlying") or "")).lower() for item in meta.values() if item.get("underlying")},
            to_block,
        )
        states, _points = _replay_events(events, "anchor", to_block, meta, liquidity_prices)
        state_drifts = []
        asset_drifts = []
        asset_diff_samples = []
        max_relative_asset_diff = 0.0
        stored_drifts = []
        has_timeseries = _table_exists(ch, "fluid_ftoken_timeseries")
        for token in tokens:
            snap = _snapshot_rpc(rpc_url, ch, token, to_block, timeout=timeout, retries=retries)
            replay_supply = int(states.get(token.lower(), ReplayState()).supply_raw)
            supply_diff = int(snap["total_supply_raw"]) - replay_supply
            if supply_diff:
                state_drifts.append({"token": token, "symbol": snap["symbol"], "replaySupplyRaw": str(replay_supply), "rpcSupplyRaw": str(snap["total_supply_raw"]), "diff": str(supply_diff)})
            stored = []
            if has_timeseries:
                stored = ch.query("""
                    SELECT total_assets_raw, total_supply_raw, replay_total_supply_raw, state_status, block_number
                    FROM fluid_ftoken_timeseries FINAL
                    WHERE product_id = %(token)s AND block_number <= %(block)s
                    ORDER BY block_number DESC LIMIT 1
                    """, parameters={"token": token.lower(), "block": int(to_block)}).result_rows
            if stored:
                total_assets_raw, total_supply_raw, replay_total_supply_raw, state_status, stored_block = stored[0]
                event_only = str(state_status) == "EVENT_REPLAY_ONLY"
                if str(total_assets_raw) not in {"", "0"} and str(total_assets_raw) != str(snap["total_assets_raw"]):
                    asset_diff = abs(int(snap["total_assets_raw"]) - int(total_assets_raw))
                    relative_asset_diff = asset_diff / max(1.0, float(snap["total_assets_raw"] or 0))
                    max_relative_asset_diff = max(max_relative_asset_diff, relative_asset_diff)
                    sample = {"token": token, "symbol": snap["symbol"], "storedBlock": int(stored_block), "storedAssetsRaw": str(total_assets_raw), "rpcAssetsRaw": str(snap["total_assets_raw"]), "relativeDiff": relative_asset_diff}
                    asset_diff_samples.append(sample)
                    if relative_asset_diff > float(args.asset_relative_tolerance):
                        asset_drifts.append(sample)
                if event_only:
                    stored_has_drift = str(total_supply_raw) != str(replay_supply) or str(replay_total_supply_raw) != str(replay_supply)
                else:
                    stored_has_drift = (
                        str(total_supply_raw) != str(snap["total_supply_raw"])
                        or str(replay_total_supply_raw) != str(replay_supply)
                        or str(state_status) not in {"OK", "EVENT_REPLAY_INDEXED"}
                    )
                if stored_has_drift:
                    stored_drifts.append({"token": token, "symbol": snap["symbol"], "storedBlock": int(stored_block), "storedSupplyRaw": str(total_supply_raw), "rpcSupplyRaw": str(snap["total_supply_raw"]), "storedReplayRaw": str(replay_total_supply_raw), "replayRaw": str(replay_supply), "status": str(state_status)})
            else:
                stored_drifts.append({"token": token, "symbol": snap["symbol"], "missingStoredTimeseries": True})
        mismatch_count = len(missing) + len(extra) + len(state_drifts) + len(asset_drifts) + len(stored_drifts)
        payload = {"status": "OK" if mismatch_count == 0 else "DRIFT", "target": FLUID_FTOKEN, "fromBlock": from_block, "anchorBlock": to_block, "fTokens": len(tokens), "rpcLogs": len(rpc_logs), "dbLogs": len(db_keys), "missingLogs": len(missing), "extraLogs": len(extra), "stateDrifts": len(state_drifts), "assetDrifts": len(asset_drifts), "storedDrifts": len(stored_drifts), "maxRelativeAssetDiff": max_relative_asset_diff, "assetRelativeTolerance": float(args.asset_relative_tolerance), "samples": {"missingLogs": missing[:10], "extraLogs": extra[:10], "stateDrifts": state_drifts[:10], "assetDrifts": asset_drifts[:10], "assetDiffs": asset_diff_samples[:10], "storedDrifts": stored_drifts[:10]}}
        if args.write_validation:
            now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
            ch.insert("fluid_rpc_validation_runs", [[f"fluid-ftoken-{uuid.uuid4().hex}", ETHEREUM_CHAIN_ID, FLUID_FTOKEN, now, now, len(tokens), mismatch_count, 0.0, 0.0, payload["status"], json.dumps(payload, sort_keys=True)]], column_names=["run_id", "chain_id", "target", "started_at", "finished_at", "checked_count", "mismatch_count", "max_relative_supply_diff", "max_relative_borrow_diff", "status", "details"])
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1 if mismatch_count and args.fail_on_drift else 0
    finally:
        if owned:
            ch.close()
