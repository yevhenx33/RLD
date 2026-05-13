"""Fluid fToken event collection, replay, and RPC anchor validation."""

from __future__ import annotations

import datetime as dt
import json
import os
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import clickhouse_connect
import requests
from eth_utils import keccak

from analytics.base import insert_rows_batched
from analytics.fluid_full_coverage import ETHEREUM_CHAIN_ID, ensure_fluid_full_coverage_tables
from analytics.protocols import FLUID_FTOKEN
from analytics.scripts.backfill_fluid_product_snapshots import (
    RpcClient,
    build_price_context,
    call_address,
    call_string,
    call_uint,
    call_uint_arg,
    discover_ftokens,
    normalize_address,
    resolve_fluid_token_price,
    token_decimals,
    token_symbol,
)

ZERO_ADDRESS = "0x" + "0" * 40
CONFIRMATIONS = 3
FTOKEN_GENESIS_BLOCK = 19_258_464
TOPIC_TRANSFER = "0x" + keccak(text="Transfer(address,address,uint256)").hex()
TOPIC_DEPOSIT = "0xdcbc1c05240f31ff3ad067ef1ee35ce4997762752e3a095284754544f4c709d7"
TOPIC_WITHDRAW = "0xfbde797d201c681b91056529119e0b02407c7bb96a4a2c75c01fc69907bc1682"
EVENT_TOPICS = (TOPIC_TRANSFER, TOPIC_DEPOSIT, TOPIC_WITHDRAW)
EVENT_NAMES = {TOPIC_TRANSFER: "Transfer", TOPIC_DEPOSIT: "Deposit", TOPIC_WITHDRAW: "Withdraw"}
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
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "127.0.0.1"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
        settings=settings,
    )


def _rpc_call(rpc_url: str, method: str, params: list[Any], *, timeout: int, retries: int) -> Any:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = requests.post(rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if payload.get("error"):
                raise RuntimeError(payload["error"])
            return payload.get("result")
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(2.0 * (attempt + 1), 5.0))
    raise RuntimeError(f"RPC {method} failed: {last_error}")


def _int_hex(value: str | int | None) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return int(value, 16) if value.startswith("0x") else int(value)
    return int(value)


def _topic_address(topic: str | None) -> str:
    raw = str(topic or "").lower().removeprefix("0x")
    return "0x" + raw[-40:].rjust(40, "0")


def _word_uint(data: str | None, index: int = 0) -> int:
    raw = str(data or "").removeprefix("0x")
    start = index * 64
    word = raw[start:start + 64]
    return int(word, 16) if len(word) == 64 else 0


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
    rows = ch.query(
        """
        SELECT block_number, log_index, lower(contract), lower(topic0), lower(tx_hash)
        FROM fluid_product_raw_events FINAL
        WHERE product_type = 'FTOKEN' AND block_number >= %(from_block)s AND block_number <= %(to_block)s
        """,
        parameters={"from_block": int(from_block), "to_block": int(to_block)},
    ).result_rows
    return {_row_key(row) for row in rows}


def _discover_ftokens_for_block(rpc: RpcClient, ch, block: int) -> list[str]:
    discovered, _err = discover_ftokens(rpc, block)
    registry = [
        normalize_address(row[0])
        for row in ch.query("""
            SELECT contract FROM fluid_contract_registry FINAL
            WHERE product_type = 'FTOKEN' AND name = 'Fluid fToken' AND active = 1
        """).result_rows
    ]
    return sorted({normalize_address(token) for token in list(discovered or []) + registry if normalize_address(token) != ZERO_ADDRESS})


def _insert_contract_registry(ch, tokens: list[str]) -> None:
    if not tokens:
        return
    ch.insert(
        "fluid_contract_registry",
        [[ETHEREUM_CHAIN_ID, "FTOKEN", token, "", "Fluid fToken", 0, 1, "", "discovered_by=allTokens"] for token in tokens],
        column_names=["chain_id", "product_type", "contract", "factory", "name", "created_block", "active", "resolver", "metadata"],
    )


def _insert_logs(ch, rpc_url: str, logs: list[dict[str, Any]], block_cache: dict[int, dt.datetime], *, timeout: int, retries: int) -> int:
    rows = []
    for log in logs:
        topics = [str(t).lower() for t in (log.get("topics") or [])]
        topic0 = topics[0] if topics else ""
        block_number = _int_hex(log.get("blockNumber"))
        rows.append([
            ETHEREUM_CHAIN_ID,
            "FTOKEN",
            block_number,
            _block_timestamp(rpc_url, block_number, block_cache, timeout=timeout, retries=retries),
            str(log.get("transactionHash") or "").lower(),
            _int_hex(log.get("logIndex")),
            str(log.get("address") or "").lower(),
            EVENT_NAMES.get(topic0, ""),
            topic0,
            topics[1] if len(topics) > 1 else None,
            topics[2] if len(topics) > 2 else None,
            topics[3] if len(topics) > 3 else None,
            str(log.get("data") or "0x"),
        ])
    return insert_rows_batched(ch, "fluid_product_raw_events", rows, [
        "chain_id", "product_type", "block_number", "block_timestamp", "tx_hash", "log_index", "contract", "event_name", "topic0", "topic1", "topic2", "topic3", "data",
    ])


def collect(args, ch=None) -> int:
    owned = ch is None
    ch = ch or _ch_client()
    try:
        ensure_fluid_full_coverage_tables(ch)
        rpc_url = _rpc_url(args)
        rpc = RpcClient(rpc_url, timeout_sec=int(args.http_timeout_sec), retries=int(args.retries))
        to_block = int(args.to_block or 0) or _confirmed_head(rpc_url, timeout=int(args.http_timeout_sec), retries=int(args.retries))
        from_block = int(args.from_block or 0) or FTOKEN_GENESIS_BLOCK
        tokens = _discover_ftokens_for_block(rpc, ch, to_block)
        existing = _existing_keys(ch, from_block, to_block)
        missing: list[dict[str, Any]] = []
        rpc_logs = 0
        for start in range(from_block, to_block + 1, int(args.batch_blocks)):
            end = min(to_block, start + int(args.batch_blocks) - 1)
            logs = _fetch_logs_resilient(rpc_url, tokens, start, end, timeout=int(args.http_timeout_sec), retries=int(args.retries))
            rpc_logs += len(logs)
            missing.extend([log for log in logs if _log_key(log) not in existing])
        inserted = 0
        if not args.dry_run:
            _insert_contract_registry(ch, tokens)
            inserted = _insert_logs(ch, rpc_url, missing, {}, timeout=int(args.http_timeout_sec), retries=int(args.retries))
        print(json.dumps({"status": "OK", "dryRun": bool(args.dry_run), "fromBlock": from_block, "toBlock": to_block, "fTokens": len(tokens), "rpcLogs": rpc_logs, "existingLogs": len(existing), "missingLogs": len(missing), "insertedLogs": inserted}, indent=2, sort_keys=True))
        return 0
    finally:
        if owned:
            ch.close()


@dataclass
class ReplayState:
    supply_raw: int = 0
    deposit_assets_raw: int = 0
    withdraw_assets_raw: int = 0
    mint_shares_raw: int = 0
    burn_shares_raw: int = 0
    transfer_count: int = 0
    deposit_count: int = 0
    withdraw_count: int = 0
    event_count: int = 0


def _snapshot_rpc(rpc: RpcClient, ch, token: str, block: int, prices) -> dict[str, Any]:
    symbol, _ = call_string(rpc, token, "symbol", block)
    underlying, _ = call_address(rpc, token, "asset", block)
    if not underlying:
        underlying, _ = call_address(rpc, token, "underlyingAsset", block)
    decimals, _ = call_uint(rpc, token, "decimals", block)
    total_assets, assets_err = call_uint(rpc, token, "totalAssets", block)
    total_supply, supply_err = call_uint(rpc, token, "totalSupply", block)
    share_assets, share_err = call_uint_arg(rpc, token, "convertToAssets", 10 ** int(decimals or 18), block)
    underlying = normalize_address(underlying or "")
    price = resolve_fluid_token_price(underlying, token_symbol(underlying), prices) if underlying else None
    underlying_decimals = token_decimals(ch, underlying) if underlying else 18
    price_usd = float(price.price_usd) if price and price.pricing_status == "PRICED" else 0.0
    supply_usd = float(total_assets or 0) / float(10 ** int(underlying_decimals or 18)) * price_usd
    return {
        "symbol": symbol or token[:10],
        "underlying": underlying,
        "total_assets_raw": int(total_assets or 0),
        "total_supply_raw": int(total_supply or 0),
        "share_assets_raw": int(share_assets or 0),
        "assets_per_share": float(share_assets or 0) / float(10 ** int(underlying_decimals or 18)) if share_assets is not None else 0.0,
        "price_usd": price_usd,
        "supply_usd": supply_usd,
        "errors": [e for e in [assets_err, supply_err, share_err] if e],
    }


def _load_raw_events(ch, from_block: int, to_block: int) -> list[tuple]:
    return ch.query(
        """
        SELECT block_number, block_timestamp, lower(contract), event_name, lower(topic0),
               ifNull(lower(topic1), ''), ifNull(lower(topic2), ''), ifNull(lower(topic3), ''), data, tx_hash, log_index
        FROM fluid_product_raw_events FINAL
        WHERE product_type = 'FTOKEN' AND block_number >= %(from_block)s AND block_number <= %(to_block)s
        ORDER BY block_number ASC, log_index ASC, contract ASC, topic0 ASC
        """,
        parameters={"from_block": int(from_block), "to_block": int(to_block)},
    ).result_rows


def _replay_events(events: list[tuple], snapshot_mode: str, anchor_block: int) -> tuple[dict[str, ReplayState], dict[tuple[str, int], ReplayState], dict[str, int]]:
    states: dict[str, ReplayState] = defaultdict(ReplayState)
    points: dict[tuple[str, int], ReplayState] = {}
    daily_last: dict[tuple[str, str], int] = {}
    for block, ts, contract, event_name, topic0, topic1, topic2, _topic3, data, _tx, _log_index in events:
        state = states[contract]
        if topic0 == TOPIC_TRANSFER:
            value = _word_uint(data, 0)
            from_addr = _topic_address(topic1)
            to_addr = _topic_address(topic2)
            if from_addr == ZERO_ADDRESS:
                state.supply_raw += value
                state.mint_shares_raw += value
            if to_addr == ZERO_ADDRESS:
                state.supply_raw -= value
                state.burn_shares_raw += value
            state.transfer_count += 1
        elif topic0 == TOPIC_DEPOSIT:
            state.deposit_assets_raw += _word_uint(data, 0)
            state.deposit_count += 1
        elif topic0 == TOPIC_WITHDRAW:
            state.withdraw_assets_raw += _word_uint(data, 0)
            state.withdraw_count += 1
        state.event_count += 1
        if snapshot_mode == "event":
            points[(contract, int(block))] = ReplayState(**state.__dict__)
        elif snapshot_mode == "daily":
            daily_last[(contract, str(ts)[:10])] = int(block)
            points[(contract, int(block))] = ReplayState(**state.__dict__)
    if snapshot_mode == "daily" and daily_last:
        keep = {(contract, block) for (_contract_day, block) in [(k, v) for k, v in daily_last.items()] for contract in [ _contract_day[0] ]}
        points = {key: val for key, val in points.items() if key in keep}
    if anchor_block > 0:
        for contract, state in states.items():
            points[(contract, int(anchor_block))] = ReplayState(**state.__dict__)
    return dict(states), points, {contract: state.supply_raw for contract, state in states.items()}


def replay(args, ch=None) -> int:
    owned = ch is None
    ch = ch or _ch_client()
    try:
        ensure_fluid_full_coverage_tables(ch)
        rpc_url = _rpc_url(args)
        rpc = RpcClient(rpc_url, timeout_sec=int(args.http_timeout_sec), retries=int(args.retries))
        to_block = int(args.to_block or 0) or _confirmed_head(rpc_url, timeout=int(args.http_timeout_sec), retries=int(args.retries))
        from_block = int(args.from_block or 0) or FTOKEN_GENESIS_BLOCK
        events = _load_raw_events(ch, from_block, to_block)
        states, points, _ = _replay_events(events, args.snapshot_mode, to_block if args.include_anchor else 0)
        prices = build_price_context(ch)
        block_ts = {int(row[0]): row[1] for row in events}
        rows = []
        drift = []
        for idx, ((token, block), state) in enumerate(sorted(points.items(), key=lambda item: (item[0][1], item[0][0]))):
            if args.max_snapshot_points and idx >= int(args.max_snapshot_points):
                break
            snap = _snapshot_rpc(rpc, ch, token, block, prices)
            diff = int(snap["total_supply_raw"]) - int(state.supply_raw)
            if diff:
                drift.append({"token": token, "block": block, "replaySupplyRaw": str(state.supply_raw), "rpcSupplyRaw": str(snap["total_supply_raw"]), "diff": str(diff)})
            ts = block_ts.get(block) or _block_timestamp(rpc_url, block, {}, timeout=int(args.http_timeout_sec), retries=int(args.retries))
            rows.append([
                ETHEREUM_CHAIN_ID, ts, block, token, snap["symbol"], snap["underlying"], str(snap["total_assets_raw"]), str(snap["total_supply_raw"]), str(state.supply_raw), float(snap["assets_per_share"]), float(snap["price_usd"]), float(snap["supply_usd"]), str(state.deposit_assets_raw), str(state.withdraw_assets_raw), str(state.mint_shares_raw), str(state.burn_shares_raw), int(state.transfer_count), int(state.deposit_count), int(state.withdraw_count), int(state.event_count), str(diff), "OK" if diff == 0 and not snap["errors"] else "DRIFT", json.dumps({"source": "fluid_ftoken_event_replay", "rpcMethods": ["totalAssets", "totalSupply", "convertToAssets"], "rpcErrors": snap["errors"]}, sort_keys=True),
            ])
        written = 0
        if rows and not args.dry_run:
            written = insert_rows_batched(ch, "fluid_ftoken_timeseries", rows, TIMESERIES_COLUMNS)
        print(json.dumps({"status": "OK" if not drift else "DRIFT", "dryRun": bool(args.dry_run), "fromBlock": from_block, "toBlock": to_block, "rawEvents": len(events), "fTokensWithEvents": len(states), "snapshotRows": len(rows), "writtenRows": written, "supplyDriftCount": len(drift), "sampleDrift": drift[:10]}, indent=2, sort_keys=True))
        return 1 if drift and args.fail_on_drift else 0
    finally:
        if owned:
            ch.close()


def anchor(args, ch=None) -> int:
    owned = ch is None
    ch = ch or _ch_client()
    try:
        ensure_fluid_full_coverage_tables(ch)
        rpc_url = _rpc_url(args)
        rpc = RpcClient(rpc_url, timeout_sec=int(args.http_timeout_sec), retries=int(args.retries))
        to_block = int(args.block_number or 0) or _confirmed_head(rpc_url, timeout=int(args.http_timeout_sec), retries=int(args.retries))
        from_block = int(args.from_block or 0) or max(0, to_block - int(args.recent_blocks))
        tokens = _discover_ftokens_for_block(rpc, ch, to_block)
        rpc_logs = []
        for start in range(from_block, to_block + 1, int(args.batch_blocks)):
            end = min(to_block, start + int(args.batch_blocks) - 1)
            rpc_logs.extend(_fetch_logs_resilient(rpc_url, tokens, start, end, timeout=int(args.http_timeout_sec), retries=int(args.retries)))
        db_keys = _existing_keys(ch, from_block, to_block)
        rpc_keys = {_log_key(log) for log in rpc_logs}
        missing = sorted(rpc_keys - db_keys)
        extra = sorted(db_keys - rpc_keys)
        events = _load_raw_events(ch, 0, to_block)
        states, _points, _ = _replay_events(events, "anchor", to_block)
        prices = build_price_context(ch)
        state_drifts = []
        asset_drifts = []
        stored_drifts = []
        for token in tokens:
            snap = _snapshot_rpc(rpc, ch, token, to_block, prices)
            replay_supply = int(states.get(token.lower(), ReplayState()).supply_raw)
            supply_diff = int(snap["total_supply_raw"]) - replay_supply
            if supply_diff:
                state_drifts.append({"token": token, "symbol": snap["symbol"], "replaySupplyRaw": str(replay_supply), "rpcSupplyRaw": str(snap["total_supply_raw"]), "diff": str(supply_diff)})
            stored = ch.query(
                """
                SELECT total_assets_raw, total_supply_raw, replay_total_supply_raw, state_status, block_number
                FROM fluid_ftoken_timeseries FINAL
                WHERE product_id = %(token)s AND block_number <= %(block)s
                ORDER BY block_number DESC LIMIT 1
                """,
                parameters={"token": token.lower(), "block": int(to_block)},
            ).result_rows
            if stored:
                total_assets_raw, total_supply_raw, replay_total_supply_raw, state_status, stored_block = stored[0]
                if str(total_assets_raw) != str(snap["total_assets_raw"]):
                    asset_drifts.append({"token": token, "symbol": snap["symbol"], "storedBlock": int(stored_block), "storedAssetsRaw": str(total_assets_raw), "rpcAssetsRaw": str(snap["total_assets_raw"])})
                if str(total_supply_raw) != str(snap["total_supply_raw"]) or str(replay_total_supply_raw) != str(replay_supply) or state_status != "OK":
                    stored_drifts.append({"token": token, "symbol": snap["symbol"], "storedBlock": int(stored_block), "storedSupplyRaw": str(total_supply_raw), "rpcSupplyRaw": str(snap["total_supply_raw"]), "storedReplayRaw": str(replay_total_supply_raw), "replayRaw": str(replay_supply), "status": str(state_status)})
            else:
                stored_drifts.append({"token": token, "symbol": snap["symbol"], "missingStoredTimeseries": True})
        mismatch_count = len(missing) + len(extra) + len(state_drifts) + len(asset_drifts) + len(stored_drifts)
        payload = {"status": "OK" if mismatch_count == 0 else "DRIFT", "target": FLUID_FTOKEN, "fromBlock": from_block, "anchorBlock": to_block, "fTokens": len(tokens), "rpcLogs": len(rpc_logs), "dbLogs": len(db_keys), "missingLogs": len(missing), "extraLogs": len(extra), "stateDrifts": len(state_drifts), "assetDrifts": len(asset_drifts), "storedDrifts": len(stored_drifts), "samples": {"missingLogs": missing[:10], "extraLogs": extra[:10], "stateDrifts": state_drifts[:10], "assetDrifts": asset_drifts[:10], "storedDrifts": stored_drifts[:10]}}
        if args.write_validation:
            now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
            ch.insert("fluid_rpc_validation_runs", [[f"fluid-ftoken-{uuid.uuid4().hex}", ETHEREUM_CHAIN_ID, FLUID_FTOKEN, now, now, len(tokens), mismatch_count, 0.0, 0.0, payload["status"], json.dumps(payload, sort_keys=True)]], column_names=["run_id", "chain_id", "target", "started_at", "finished_at", "checked_count", "mismatch_count", "max_relative_supply_diff", "max_relative_borrow_diff", "status", "details"])
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1 if mismatch_count and args.fail_on_drift else 0
    finally:
        if owned:
            ch.close()
