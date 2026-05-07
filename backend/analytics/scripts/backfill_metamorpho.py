#!/usr/bin/env python3
"""Backfill and snapshot Ethereum MetaMorpho vault coverage.

This job keeps MetaMorpho separate from Morpho Blue market TVL. It discovers
factory-created vaults, records vault event facts, snapshots ERC4626/admin
state, and derives per-market allocations from the reconstructed Morpho Blue
position table for the vault address.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import clickhouse_connect
import requests
from eth_abi import decode as abi_decode
from eth_utils import keccak

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from analytics.config import apply_env_from_config
from analytics.morpho_oracle_snapshots import ZERO_ADDRESS, normalize_address
from analytics.schema import ensure_schema
from analytics.state import update_source_status
from analytics.sources.morpho import resolve_symbol_price
from analytics.tokens import TOKENS

METAMORPHO_V1_1_FACTORY = "0x1897A8997241C1cD4bD0698647e4EB7213535c24"
METAMORPHO_OLD_FACTORY = "0xA9c3D3a366466Fa809d1Ae982Fb2c46E5fC41101"
DEFAULT_FACTORY_ADDRESSES = (METAMORPHO_V1_1_FACTORY, METAMORPHO_OLD_FACTORY)
DEFAULT_START_BLOCK = 18_883_124
BASIS = 10**18


def selector(signature: str) -> str:
    return "0x" + keccak(text=signature)[:4].hex()


def topic(signature: str) -> str:
    return "0x" + keccak(text=signature).hex()


def word_uint(value: int) -> str:
    return f"{int(value):064x}"


SELECTORS = {
    "asset": selector("asset()"),
    "name": selector("name()"),
    "symbol": selector("symbol()"),
    "decimals": selector("decimals()"),
    "owner": selector("owner()"),
    "curator": selector("curator()"),
    "guardian": selector("guardian()"),
    "fee": selector("fee()"),
    "feeRecipient": selector("feeRecipient()"),
    "timelock": selector("timelock()"),
    "totalAssets": selector("totalAssets()"),
    "totalSupply": selector("totalSupply()"),
    "supplyQueueLength": selector("supplyQueueLength()"),
    "withdrawQueueLength": selector("withdrawQueueLength()"),
    "supplyQueue": selector("supplyQueue(uint256)"),
    "withdrawQueue": selector("withdrawQueue(uint256)"),
    "config": selector("config(bytes32)"),
    "pendingCap": selector("pendingCap(bytes32)"),
}

TOPICS = {
    "CreateMetaMorpho": topic("CreateMetaMorpho(address,address,address,uint256,address,string,string,bytes32)"),
    "Deposit": topic("Deposit(address,address,uint256,uint256)"),
    "Withdraw": topic("Withdraw(address,address,address,uint256,uint256)"),
    "Transfer": topic("Transfer(address,address,uint256)"),
    "SetName": topic("SetName(string)"),
    "SetSymbol": topic("SetSymbol(string)"),
    "SubmitTimelock": topic("SubmitTimelock(uint256)"),
    "SetTimelock": topic("SetTimelock(address,uint256)"),
    "SetSkimRecipient": topic("SetSkimRecipient(address)"),
    "SetFee": topic("SetFee(address,uint256)"),
    "SetFeeRecipient": topic("SetFeeRecipient(address)"),
    "SubmitGuardian": topic("SubmitGuardian(address)"),
    "SetGuardian": topic("SetGuardian(address,address)"),
    "SubmitCap": topic("SubmitCap(address,bytes32,uint256)"),
    "SetCap": topic("SetCap(address,bytes32,uint256)"),
    "UpdateLastTotalAssets": topic("UpdateLastTotalAssets(uint256)"),
    "UpdateLostAssets": topic("UpdateLostAssets(uint256)"),
    "SubmitMarketRemoval": topic("SubmitMarketRemoval(address,bytes32)"),
    "SetCurator": topic("SetCurator(address)"),
    "SetIsAllocator": topic("SetIsAllocator(address,bool)"),
    "RevokePendingTimelock": topic("RevokePendingTimelock(address)"),
    "RevokePendingCap": topic("RevokePendingCap(address,bytes32)"),
    "RevokePendingGuardian": topic("RevokePendingGuardian(address)"),
    "RevokePendingMarketRemoval": topic("RevokePendingMarketRemoval(address,bytes32)"),
    "SetSupplyQueue": topic("SetSupplyQueue(address,bytes32[])"),
    "SetWithdrawQueue": topic("SetWithdrawQueue(address,bytes32[])"),
    "ReallocateSupply": topic("ReallocateSupply(address,bytes32,uint256,uint256)"),
    "ReallocateWithdraw": topic("ReallocateWithdraw(address,bytes32,uint256,uint256)"),
    "AccrueInterest": topic("AccrueInterest(uint256,uint256)"),
    "Skim": topic("Skim(address,address,uint256)"),
}
TOPIC_TO_EVENT = {value.lower(): key for key, value in TOPICS.items()}
EVENT_TOPICS = [value for key, value in TOPICS.items() if key != "CreateMetaMorpho"]


@dataclass
class RpcResult:
    ok: bool
    result: str = "0x"
    error: str = ""


class RpcClient:
    def __init__(self, rpc_url: str, timeout_sec: int = 60, retries: int = 2):
        self.rpc_url = rpc_url
        self.timeout_sec = timeout_sec
        self.retries = retries
        self._id = 0

    def _post(self, payload: Any) -> Any:
        last_error = ""
        for attempt in range(self.retries + 1):
            try:
                response = requests.post(self.rpc_url, json=payload, timeout=self.timeout_sec)
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                last_error = _sanitize_error(exc)
                if attempt < self.retries:
                    time.sleep(0.35 * (attempt + 1))
        return {"error": {"message": last_error}}

    def call(self, to: str, data: str, block: str | int = "latest") -> RpcResult:
        block_tag = hex(int(block)) if isinstance(block, int) else block
        self._id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": "eth_call",
            "params": [{"to": normalize_address(to), "data": data}, block_tag],
        }
        item = self._post(payload)
        if item.get("error"):
            err = item.get("error", {}) or {}
            return RpcResult(False, "0x", _sanitize_error(err.get("message") if isinstance(err, dict) else err))
        return RpcResult(True, str(item.get("result") or "0x"), "")

    def block_number(self) -> int:
        self._id += 1
        item = self._post({"jsonrpc": "2.0", "id": self._id, "method": "eth_blockNumber", "params": []})
        if item.get("error"):
            raise RuntimeError(_sanitize_error(item.get("error")))
        return int(item["result"], 16)

    def block_timestamp(self, block_number: int) -> dt.datetime:
        self._id += 1
        item = self._post({"jsonrpc": "2.0", "id": self._id, "method": "eth_getBlockByNumber", "params": [hex(block_number), False]})
        if item.get("error") or not item.get("result"):
            raise RuntimeError(_sanitize_error(item.get("error", "missing block")))
        return dt.datetime.utcfromtimestamp(int(item["result"]["timestamp"], 16)).replace(microsecond=0)

    def block_timestamps(self, block_numbers: list[int]) -> dict[int, dt.datetime]:
        result: dict[int, dt.datetime] = {}
        missing = sorted({int(block) for block in block_numbers if int(block) > 0})
        if not missing:
            return result
        payload = []
        for block in missing:
            self._id += 1
            payload.append({
                "jsonrpc": "2.0",
                "id": self._id,
                "method": "eth_getBlockByNumber",
                "params": [hex(block), False],
            })
        for chunk_start in range(0, len(payload), 100):
            chunk = payload[chunk_start:chunk_start + 100]
            item = self._post(chunk)
            responses = item if isinstance(item, list) else []
            by_id = {response.get("id"): response for response in responses if isinstance(response, dict)}
            for request in chunk:
                response = by_id.get(request["id"], {})
                block = int(request["params"][0], 16)
                data = response.get("result") or {}
                if data.get("timestamp"):
                    result[block] = dt.datetime.utcfromtimestamp(int(data["timestamp"], 16)).replace(microsecond=0)
        missing_after_batch = [block for block in missing if block not in result]
        for block in missing_after_batch:
            try:
                result[block] = self.block_timestamp(block)
            except Exception:
                pass
        return result

    def logs(self, address: str | list[str], topics: list[Any], start: int, end: int) -> list[dict[str, Any]]:
        self._id += 1
        params = {"address": address, "fromBlock": hex(int(start)), "toBlock": hex(int(end)), "topics": topics}
        item = self._post({"jsonrpc": "2.0", "id": self._id, "method": "eth_getLogs", "params": [params]})
        if item.get("error"):
            raise RuntimeError(_sanitize_error(item.get("error")))
        return list(item.get("result") or [])


def _sanitize_error(message: object) -> str:
    text = str(message or "")
    if " for url:" in text:
        text = text.split(" for url:", 1)[0]
    return text[:500]


def _decode(raw: str, abi_type: str) -> Any:
    if not raw or raw == "0x":
        raise ValueError("empty result")
    return abi_decode([abi_type], bytes.fromhex(raw[2:]))[0]


def _word(raw: str, idx: int) -> str:
    data = (raw or "").removeprefix("0x")
    start = idx * 64
    return data[start:start + 64].rjust(64, "0")


def _word_int(raw: str, idx: int) -> int:
    word = _word(raw, idx)
    return int(word, 16) if word else 0


def _topic_address(topics: list[str], idx: int) -> str:
    if idx >= len(topics) or not topics[idx]:
        return ""
    return "0x" + str(topics[idx]).removeprefix("0x")[-40:].lower()


def _topic_bytes32(topics: list[str], idx: int) -> str:
    if idx >= len(topics) or not topics[idx]:
        return ""
    return "0x" + str(topics[idx]).removeprefix("0x").rjust(64, "0")[-64:].lower()


def _bytes32_result(raw: str) -> str:
    if not raw or raw == "0x":
        return ""
    return "0x" + raw.removeprefix("0x")[:64].rjust(64, "0").lower()


def _call_uint(rpc: RpcClient, vault: str, key: str, block: int | str) -> tuple[int | None, str]:
    res = rpc.call(vault, SELECTORS[key], block)
    if not res.ok:
        return None, res.error
    try:
        return int(_decode(res.result, "uint256")), ""
    except Exception as exc:
        return None, _sanitize_error(exc)


def _call_address(rpc: RpcClient, vault: str, key: str, block: int | str) -> tuple[str, str]:
    res = rpc.call(vault, SELECTORS[key], block)
    if not res.ok:
        return "", res.error
    try:
        return normalize_address(_decode(res.result, "address")), ""
    except Exception as exc:
        return "", _sanitize_error(exc)


def _call_string(rpc: RpcClient, vault: str, key: str, block: int | str) -> tuple[str, str]:
    res = rpc.call(vault, SELECTORS[key], block)
    if not res.ok:
        return "", res.error
    try:
        return str(_decode(res.result, "string")), ""
    except Exception as exc:
        return "", _sanitize_error(exc)


def _call_queue(rpc: RpcClient, vault: str, key: str, idx: int, block: int | str) -> tuple[str, str]:
    res = rpc.call(vault, SELECTORS[key] + word_uint(idx), block)
    if not res.ok:
        return "", res.error
    return _bytes32_result(res.result), ""


def _call_config_cap(rpc: RpcClient, vault: str, market_id: str, block: int | str) -> tuple[int, str]:
    res = rpc.call(vault, SELECTORS["config"] + market_id.removeprefix("0x").rjust(64, "0"), block)
    if not res.ok:
        return 0, res.error
    try:
        return _word_int(res.result, 0), ""
    except Exception as exc:
        return 0, _sanitize_error(exc)


def _token_meta(address: str) -> tuple[str, int]:
    symbol, decimals = TOKENS.get(normalize_address(address).removeprefix("0x").lower(), (normalize_address(address)[:10], 18))
    return str(symbol), int(decimals)


def _ch_client():
    settings = {}
    if os.getenv("CLICKHOUSE_ASYNC_INSERT", "true").strip().lower() in {"1", "true", "yes"}:
        settings["async_insert"] = 1
        settings["wait_for_async_insert"] = 1 if os.getenv("CLICKHOUSE_WAIT_FOR_ASYNC_INSERT", "true").strip().lower() in {"1", "true", "yes"} else 0
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "127.0.0.1"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
        database=os.getenv("CLICKHOUSE_DATABASE", "default"),
        settings=settings,
    )


def default_rpc_url() -> str:
    return os.getenv("MAINNET_RPC_URL") or os.getenv("ETH_RPC_URL") or os.getenv("RPC_URL") or ""


def _factory_addresses(args) -> list[str]:
    raw = args.factory_addresses or os.getenv("METAMORPHO_FACTORY_ADDRESSES") or ",".join(DEFAULT_FACTORY_ADDRESSES)
    return [normalize_address(item.strip()) for item in raw.split(",") if item.strip()]


def _latest_feed_prices(ch) -> dict[str, float]:
    rows = ch.query("SELECT feed, argMax(price, timestamp) FROM chainlink_prices GROUP BY feed").result_rows
    return {str(feed): float(price) for feed, price in rows if feed and price is not None}


def _vault_rows(ch, max_vaults: int | None = None) -> list[tuple[str, str, str, str]]:
    limit = f"LIMIT {int(max_vaults)}" if max_vaults else ""
    return [
        (str(row[0]).lower(), str(row[1] or ""), str(row[2] or ""), str(row[3] or "").lower())
        for row in ch.query(
            f"""
            SELECT vault_address, argMax(name, updated_at), argMax(asset_symbol, updated_at), argMax(asset_address, updated_at)
            FROM metamorpho_vault_registry
            GROUP BY vault_address
            ORDER BY vault_address
            {limit}
            """
        ).result_rows
    ]


def _insert_registry_rows(ch, rows: list[list]) -> int:
    if not rows:
        return 0
    ch.insert(
        "metamorpho_vault_registry",
        rows,
        column_names=[
            "vault_address", "name", "asset_symbol", "asset_address", "owner", "curator", "guardian",
            "allocator", "fee_wad", "fee_recipient", "timelock", "source", "active",
        ],
    )
    return len(rows)


def _discover_factory_vaults(ch, rpc: RpcClient, args, head: int) -> int:
    factories = _factory_addresses(args)
    if not factories:
        return 0
    start = int(args.factory_start_block or os.getenv("METAMORPHO_FACTORY_START_BLOCK", DEFAULT_START_BLOCK))
    rows = []
    event_rows = []
    step = max(1_000, int(args.log_chunk_size or 25_000))
    for factory in factories:
        cursor = start
        while cursor <= head:
            end = min(head, cursor + step - 1)
            try:
                logs = rpc.logs(factory, [[TOPICS["CreateMetaMorpho"]]], cursor, end)
            except Exception as exc:
                if step > 2_000:
                    step = max(2_000, step // 2)
                    continue
                print(json.dumps({"warning": "factory_log_range_failed", "factory": factory, "start": cursor, "end": end, "error": _sanitize_error(exc)}))
                cursor = end + 1
                continue
            ts_by_block = rpc.block_timestamps([int(str(item.get("blockNumber") or "0x0"), 16) for item in logs])
            for item in logs:
                topics = [str(t) for t in item.get("topics") or []]
                data = str(item.get("data") or "0x")
                vault = _topic_address(topics, 1)
                caller = _topic_address(topics, 2)
                asset = _topic_address(topics, 3)
                initial_owner = ""
                initial_timelock = 0
                name = ""
                symbol = ""
                try:
                    initial_owner, initial_timelock, name, symbol, _salt = abi_decode(
                        ["address", "uint256", "string", "string", "bytes32"], bytes.fromhex(data.removeprefix("0x"))
                    )
                    initial_owner = normalize_address(initial_owner)
                except Exception:
                    pass
                asset_symbol, _asset_decimals = _token_meta(asset)
                rows.append([vault, name or symbol or vault[:10], asset_symbol, asset, initial_owner, "", "", "", "0", "", int(initial_timelock or 0), "factory", 1])
                block_number = int(str(item.get("blockNumber") or "0x0"), 16)
                event_rows.append([
                    block_number,
                    ts_by_block.get(block_number, dt.datetime.utcnow().replace(microsecond=0)),
                    str(item.get("transactionHash") or ""),
                    int(str(item.get("logIndex") or "0x0"), 16),
                    vault,
                    "CreateMetaMorpho",
                    caller,
                    initial_owner,
                    "",
                    "",
                    "0",
                    "0",
                    data,
                ])
            cursor = end + 1
    written = _insert_registry_rows(ch, rows)
    if event_rows:
        ch.insert(
            "metamorpho_vault_events",
            event_rows,
            column_names=["block_number", "timestamp", "tx_hash", "log_index", "vault_address", "event_name", "caller", "owner", "receiver", "market_id", "assets", "shares", "raw_data"],
        )
    return written


def _decode_vault_event(item: dict[str, Any], timestamp: dt.datetime | None = None) -> list[Any] | None:
    topics = [str(t) for t in item.get("topics") or []]
    if not topics:
        return None
    event = TOPIC_TO_EVENT.get(topics[0].lower(), "Unknown")
    data = str(item.get("data") or "0x")
    block_number = int(str(item.get("blockNumber") or "0x0"), 16)
    log_index = int(str(item.get("logIndex") or "0x0"), 16)
    vault = normalize_address(str(item.get("address") or ""))
    caller = owner = receiver = market_id = ""
    assets = shares = "0"
    if event == "Deposit":
        caller = _topic_address(topics, 1)
        owner = _topic_address(topics, 2)
        assets = str(_word_int(data, 0))
        shares = str(_word_int(data, 1))
    elif event == "Withdraw":
        caller = _topic_address(topics, 1)
        receiver = _topic_address(topics, 2)
        owner = _topic_address(topics, 3)
        assets = str(_word_int(data, 0))
        shares = str(_word_int(data, 1))
    elif event == "Transfer":
        owner = _topic_address(topics, 1)
        receiver = _topic_address(topics, 2)
        shares = str(_word_int(data, 0))
    elif event in {"SetCap", "SubmitCap", "RevokePendingCap", "SubmitMarketRemoval", "RevokePendingMarketRemoval", "ReallocateSupply", "ReallocateWithdraw"}:
        caller = _topic_address(topics, 1)
        market_id = _topic_bytes32(topics, 2)
        assets = str(_word_int(data, 0))
        shares = str(_word_int(data, 1)) if event.startswith("Reallocate") else "0"
    elif event in {"SetTimelock", "SetFee", "RevokePendingTimelock", "SetSupplyQueue", "SetWithdrawQueue", "Skim"}:
        caller = _topic_address(topics, 1)
        if event == "Skim":
            receiver = _topic_address(topics, 2)
            assets = str(_word_int(data, 0))
        else:
            assets = str(_word_int(data, 0))
    elif event in {"SetGuardian"}:
        caller = _topic_address(topics, 1)
        owner = _topic_address(topics, 2)
    elif event in {"SetCurator", "SetFeeRecipient", "SetSkimRecipient", "SubmitGuardian", "SetIsAllocator"}:
        owner = _topic_address(topics, 1)
        assets = str(_word_int(data, 0))
    elif event == "AccrueInterest":
        assets = str(_word_int(data, 0))
        shares = str(_word_int(data, 1))
    elif event in {"UpdateLastTotalAssets", "UpdateLostAssets", "SubmitTimelock"}:
        assets = str(_word_int(data, 0))
    return [
        block_number,
        (timestamp or dt.datetime.utcnow().replace(microsecond=0)).replace(tzinfo=None),
        str(item.get("transactionHash") or ""),
        log_index,
        vault,
        event,
        caller,
        owner,
        receiver,
        market_id,
        assets,
        shares,
        data,
    ]


def _backfill_vault_events(ch, rpc: RpcClient, args, head: int) -> int:
    vaults = [row[0] for row in _vault_rows(ch, args.max_vaults)]
    if not vaults:
        return 0
    existing = ch.query("SELECT max(block_number) FROM metamorpho_vault_events").result_rows[0][0]
    default_start = int(args.events_start_block or os.getenv("METAMORPHO_EVENTS_START_BLOCK", DEFAULT_START_BLOCK))
    start = default_start if args.ignore_existing_cursor else max(default_start, int(existing or 0) + 1)
    if start > head:
        return 0
    step = max(1_000, int(args.log_chunk_size or 25_000))
    rows = []
    written_total = 0
    last_event_block = 0
    last_event_ts = dt.datetime(1970, 1, 1)
    cursor = start
    range_index = 0
    while cursor <= head:
        end = min(head, cursor + step - 1)
        range_index += 1
        try:
            logs = rpc.logs(vaults, [EVENT_TOPICS], cursor, end)
        except Exception as exc:
            if step > 2_000:
                step = max(2_000, step // 2)
                continue
            print(json.dumps({"warning": "vault_log_range_failed", "start": cursor, "end": end, "error": _sanitize_error(exc)}))
            cursor = end + 1
            continue
        if range_index == 1 or range_index % int(args.progress_every_ranges or 10) == 0 or logs:
            print(json.dumps({"progress": "metamorpho_event_range", "range": range_index, "start": cursor, "end": end, "logs": len(logs)}), flush=True)
        ts_by_block = rpc.block_timestamps([int(str(item.get("blockNumber") or "0x0"), 16) for item in logs])
        for item in logs:
            block_number = int(str(item.get("blockNumber") or "0x0"), 16)
            event_ts = ts_by_block.get(block_number, dt.datetime.utcnow().replace(microsecond=0))
            decoded = _decode_vault_event(item, event_ts)
            if decoded:
                rows.append(decoded)
                last_event_block = max(last_event_block, block_number)
                last_event_ts = max(last_event_ts, event_ts)
        if len(rows) >= 20_000:
            ch.insert(
                "metamorpho_vault_events",
                rows,
                column_names=["block_number", "timestamp", "tx_hash", "log_index", "vault_address", "event_name", "caller", "owner", "receiver", "market_id", "assets", "shares", "raw_data"],
            )
            written_total += len(rows)
            rows.clear()
        update_source_status(
            ch,
            "METAMORPHO_VAULT",
            "collector",
            last_scanned_block=end,
            last_event_block=last_event_block or None,
            source_head_block=head,
            last_data_timestamp=last_event_ts if last_event_block else None,
        )
        cursor = end + 1
    if rows:
        ch.insert(
            "metamorpho_vault_events",
            rows,
            column_names=["block_number", "timestamp", "tx_hash", "log_index", "vault_address", "event_name", "caller", "owner", "receiver", "market_id", "assets", "shares", "raw_data"],
        )
        written_total += len(rows)
    update_source_status(
        ch,
        "METAMORPHO_VAULT",
        "collector",
        last_scanned_block=head,
        last_event_block=last_event_block or None,
        source_head_block=head,
        last_data_timestamp=last_event_ts if last_event_block else None,
    )
    return written_total


def _position_allocations(ch, vault: str, asset_decimals: int, asset_price: float) -> dict[str, dict[str, Any]]:
    rows = ch.query(
        """
        SELECT pos.market_id,
               toUInt256OrZero(pos.supply_shares) AS supply_shares,
               toUInt256OrZero(state.total_supply_assets) AS total_supply_assets,
               toUInt256OrZero(state.total_supply_shares) AS total_supply_shares,
               argMax(metrics.loan_price_usd, tuple(metrics.timestamp, metrics.inserted_at)) AS loan_price_usd,
               any(params.loan_decimals) AS loan_decimals
        FROM (SELECT * FROM morpho_market_positions FINAL) AS pos
        LEFT JOIN (SELECT * FROM morpho_market_state FINAL) AS state USING market_id
        LEFT JOIN morpho_market_metrics AS metrics USING market_id
        LEFT JOIN morpho_market_params AS params USING market_id
        WHERE pos.user = %(vault)s
          AND toUInt256OrZero(pos.supply_shares) > 0
        GROUP BY pos.market_id, supply_shares, total_supply_assets, total_supply_shares
        """,
        parameters={"vault": vault.lower()},
    ).result_rows
    allocations: dict[str, dict[str, Any]] = {}
    for market_id, supply_shares, total_supply_assets, total_supply_shares, loan_price, loan_decimals in rows:
        supplied_raw = 0
        if int(total_supply_shares or 0) > 0:
            supplied_raw = int(supply_shares or 0) * int(total_supply_assets or 0) // int(total_supply_shares)
        price = float(loan_price or asset_price or 0.0)
        decimals = int(loan_decimals or asset_decimals or 18)
        supplied_usd = (supplied_raw / (10 ** decimals)) * price if price > 0 else 0.0
        allocations[str(market_id).lower()] = {"supplied_assets": str(supplied_raw), "supplied_usd": float(supplied_usd)}
    return allocations



def _feed_prices_by_hour(ch, start: dt.datetime, end: dt.datetime) -> dict[dt.datetime, dict[str, float]]:
    rows = ch.query(
        """
        SELECT toStartOfHour(timestamp) AS ts, feed, argMax(price, timestamp) AS price
        FROM chainlink_prices
        WHERE timestamp >= %(start)s AND timestamp <= %(end)s
        GROUP BY ts, feed
        ORDER BY ts, feed
        """,
        parameters={"start": start - dt.timedelta(days=7), "end": end},
    ).result_rows
    by_hour: dict[dt.datetime, dict[str, float]] = defaultdict(dict)
    for ts, feed, price in rows:
        by_hour[ts.replace(tzinfo=None) if getattr(ts, "tzinfo", None) else ts][str(feed)] = float(price)
    last: dict[str, float] = {}
    filled: dict[dt.datetime, dict[str, float]] = {}
    for ts in sorted(by_hour):
        last.update(by_hour[ts])
        filled[ts] = dict(last)
    return filled


def _price_at(price_hours: dict[dt.datetime, dict[str, float]], ts: dt.datetime, symbol: str) -> float:
    candidates = [key for key in price_hours if key <= ts]
    if not candidates:
        return 0.0
    return float(resolve_symbol_price(symbol, price_hours[max(candidates)]) or 0.0)


def _replay_vault_flows(ch) -> int:
    bounds = ch.query("SELECT min(timestamp), max(timestamp), count() FROM metamorpho_vault_events").result_rows[0]
    if not bounds or int(bounds[2] or 0) == 0:
        return 0
    start = bounds[0].replace(tzinfo=None) if getattr(bounds[0], "tzinfo", None) else bounds[0]
    end = bounds[1].replace(tzinfo=None) if getattr(bounds[1], "tzinfo", None) else bounds[1]
    price_hours = _feed_prices_by_hour(ch, start, end)
    rows = ch.query(
        """
        SELECT toStartOfHour(e.timestamp) AS ts,
               e.vault_address,
               argMax(r.asset_symbol, r.updated_at) AS asset_symbol,
               argMax(r.asset_address, r.updated_at) AS asset_address,
               e.event_name,
               e.assets,
               e.shares,
               e.timestamp
        FROM (SELECT * FROM metamorpho_vault_events FINAL) AS e
        LEFT JOIN metamorpho_vault_registry AS r ON r.vault_address = e.vault_address
        WHERE e.event_name IN ('Deposit', 'Withdraw', 'Transfer')
        GROUP BY ts, e.vault_address, e.event_name, e.assets, e.shares, e.timestamp
        ORDER BY ts, e.vault_address
        """
    ).result_rows
    aggregates: dict[tuple[dt.datetime, str], dict[str, object]] = {}
    for ts, vault, asset_symbol, asset_address, event_name, assets_raw, shares_raw, event_ts in rows:
        ts = ts.replace(tzinfo=None) if getattr(ts, "tzinfo", None) else ts
        vault = str(vault).lower()
        symbol = str(asset_symbol or "")
        _, decimals = _token_meta(str(asset_address or ZERO_ADDRESS))
        key = (ts, vault)
        slot = aggregates.setdefault(key, {
            "timestamp": ts,
            "vault_address": vault,
            "asset_symbol": symbol,
            "deposit_assets": 0,
            "withdraw_assets": 0,
            "deposit_shares": 0,
            "withdraw_shares": 0,
            "transfer_shares": 0,
            "deposit_usd": 0.0,
            "withdraw_usd": 0.0,
            "event_count": 0,
        })
        assets = int(assets_raw or 0)
        shares = int(shares_raw or 0)
        price = _price_at(price_hours, ts, symbol)
        amount_usd = (assets / (10 ** decimals)) * price if price > 0 else 0.0
        if event_name == "Deposit":
            slot["deposit_assets"] = int(slot["deposit_assets"]) + assets
            slot["deposit_shares"] = int(slot["deposit_shares"]) + shares
            slot["deposit_usd"] = float(slot["deposit_usd"]) + amount_usd
        elif event_name == "Withdraw":
            slot["withdraw_assets"] = int(slot["withdraw_assets"]) + assets
            slot["withdraw_shares"] = int(slot["withdraw_shares"]) + shares
            slot["withdraw_usd"] = float(slot["withdraw_usd"]) + amount_usd
        else:
            slot["transfer_shares"] = int(slot["transfer_shares"]) + shares
        slot["event_count"] = int(slot["event_count"]) + 1
    out = []
    for slot in aggregates.values():
        out.append([
            slot["timestamp"],
            slot["vault_address"],
            slot["asset_symbol"],
            str(slot["deposit_assets"]),
            str(slot["withdraw_assets"]),
            str(slot["deposit_shares"]),
            str(slot["withdraw_shares"]),
            str(slot["transfer_shares"]),
            float(slot["deposit_usd"]),
            float(slot["withdraw_usd"]),
            float(slot["deposit_usd"]) - float(slot["withdraw_usd"]),
            int(slot["event_count"]),
        ])
    if out:
        ch.insert(
            "metamorpho_vault_flows_hourly",
            out,
            column_names=[
                "timestamp", "vault_address", "asset_symbol", "deposit_assets", "withdraw_assets",
                "deposit_shares", "withdraw_shares", "transfer_shares", "deposit_usd", "withdraw_usd",
                "net_flow_usd", "event_count",
            ],
        )
        update_source_status(
            ch,
            "METAMORPHO_VAULT",
            "processor",
            last_processed_block=int(ch.query("SELECT max(block_number) FROM metamorpho_vault_events").result_rows[0][0] or 0),
            source_head_block=int(ch.query("SELECT max(block_number) FROM metamorpho_vault_events").result_rows[0][0] or 0),
            last_data_timestamp=end,
        )
    return len(out)


def _snapshot_vaults(ch, rpc: RpcClient, args, block_number: int, timestamp: dt.datetime) -> tuple[int, int, int]:
    prices = _latest_feed_prices(ch)
    registry_rows = []
    state_rows = []
    allocation_rows = []
    written_registry = 0
    written_states = 0
    written_allocations = 0
    flush_every = max(1, int(os.getenv("METAMORPHO_SNAPSHOT_FLUSH_EVERY", "25")))

    def flush() -> None:
        nonlocal registry_rows, state_rows, allocation_rows, written_registry, written_states, written_allocations
        if registry_rows:
            written_registry += _insert_registry_rows(ch, registry_rows)
            registry_rows = []
        if state_rows:
            ch.insert(
                "metamorpho_vault_state",
                state_rows,
                column_names=["timestamp", "block_number", "vault_address", "total_assets", "total_supply", "share_price_usd", "tvl_usd", "asset_price_usd", "is_canonical_tvl", "snapshot_status", "error"],
            )
            written_states += len(state_rows)
            state_rows = []
        if allocation_rows:
            ch.insert(
                "metamorpho_vault_allocations",
                allocation_rows,
                column_names=["timestamp", "block_number", "vault_address", "market_id", "cap", "supplied_assets", "supplied_usd", "allocation_share", "snapshot_status", "error"],
            )
            written_allocations += len(allocation_rows)
            allocation_rows = []

    for idx, (vault, seed_name, seed_asset_symbol, seed_asset) in enumerate(_vault_rows(ch, args.max_vaults), start=1):
        asset, asset_error = _call_address(rpc, vault, "asset", block_number)
        if not asset:
            asset = seed_asset
        asset_symbol, asset_decimals = _token_meta(asset)
        if seed_asset_symbol and seed_asset_symbol != asset[:10]:
            asset_symbol = seed_asset_symbol
        name, _name_error = _call_string(rpc, vault, "name", block_number)
        share_symbol, _share_symbol_error = _call_string(rpc, vault, "symbol", block_number)
        share_decimals, _share_dec_error = _call_uint(rpc, vault, "decimals", block_number)
        owner, _ = _call_address(rpc, vault, "owner", block_number)
        curator, _ = _call_address(rpc, vault, "curator", block_number)
        guardian, _ = _call_address(rpc, vault, "guardian", block_number)
        fee_wad, _ = _call_uint(rpc, vault, "fee", block_number)
        fee_recipient, _ = _call_address(rpc, vault, "feeRecipient", block_number)
        timelock, _ = _call_uint(rpc, vault, "timelock", block_number)
        total_assets, total_assets_error = _call_uint(rpc, vault, "totalAssets", block_number)
        total_supply, total_supply_error = _call_uint(rpc, vault, "totalSupply", block_number)
        asset_price = resolve_symbol_price(asset_symbol, prices) or 0.0
        status = "OK"
        error = ""
        if total_assets is None or total_supply is None:
            status = "RPC_ERROR"
            error = total_assets_error or total_supply_error or asset_error
            total_assets = int(0)
            total_supply = int(0)
        elif asset_price <= 0:
            status = "MISSING_PRICE"
            error = f"missing asset USD price for {asset_symbol}"
        total_assets_i = int(total_assets or 0)
        total_supply_i = int(total_supply or 0)
        share_decimals_i = int(share_decimals or asset_decimals or 18)
        tvl_usd = (total_assets_i / (10 ** asset_decimals)) * asset_price if asset_price > 0 else 0.0
        share_price = 0.0
        if total_supply_i > 0 and asset_price > 0:
            share_price = (total_assets_i / (10 ** asset_decimals)) / (total_supply_i / (10 ** share_decimals_i)) * asset_price
        registry_rows.append([
            vault,
            name or seed_name or share_symbol or vault[:10],
            asset_symbol,
            asset,
            owner,
            curator,
            guardian,
            "",
            str(int(fee_wad or 0)),
            fee_recipient,
            int(timelock or 0),
            "rpc_snapshot",
            1,
        ])
        state_rows.append([
            timestamp,
            block_number,
            vault,
            str(total_assets_i),
            str(total_supply_i),
            float(share_price),
            float(tvl_usd),
            float(asset_price),
            0,
            status,
            error,
        ])
        allocations = _position_allocations(ch, vault, asset_decimals, asset_price)
        queue_ids: set[str] = set(allocations)
        for length_key, getter_key in (("supplyQueueLength", "supplyQueue"), ("withdrawQueueLength", "withdrawQueue")):
            length, _ = _call_uint(rpc, vault, length_key, block_number)
            for queue_idx in range(min(int(length or 0), int(args.max_queue_items or 256))):
                market_id, _ = _call_queue(rpc, vault, getter_key, queue_idx, block_number)
                if market_id and market_id != "0x" + "0" * 64:
                    queue_ids.add(market_id.lower())
        total_alloc_usd = sum(float(item.get("supplied_usd") or 0.0) for item in allocations.values())
        for market_id in sorted(queue_ids):
            cap, cap_error = _call_config_cap(rpc, vault, market_id, block_number)
            item = allocations.get(market_id, {"supplied_assets": "0", "supplied_usd": 0.0})
            supplied_usd = float(item.get("supplied_usd") or 0.0)
            allocation_rows.append([
                timestamp,
                block_number,
                vault,
                market_id,
                str(int(cap or 0)),
                str(item.get("supplied_assets") or "0"),
                supplied_usd,
                float(supplied_usd / total_alloc_usd) if total_alloc_usd > 0 else 0.0,
                "OK" if not cap_error else "PARTIAL",
                cap_error,
            ])
        if idx % flush_every == 0:
            flush()
            print(json.dumps({"progress": "metamorpho_snapshot", "vaults": idx, "states": written_states, "allocations": written_allocations}), flush=True)
    flush()
    return written_registry, written_states, written_allocations


def run(args) -> int:
    apply_env_from_config(args.config)
    rpc_url = args.rpc_url or default_rpc_url()
    if not rpc_url and not args.dry_run:
        raise SystemExit("MAINNET_RPC_URL is required unless --dry-run is used")
    ch = _ch_client()
    run_id = uuid4().hex
    try:
        ensure_schema(ch)
        head = int(args.block_number or (0 if args.dry_run else RpcClient(rpc_url, args.http_timeout_sec, args.retries).block_number()))
        rpc = RpcClient(rpc_url, args.http_timeout_sec, args.retries) if rpc_url else None
        timestamp = dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        if rpc and head:
            try:
                timestamp = rpc.block_timestamp(head).replace(minute=0, second=0, microsecond=0)
            except Exception:
                pass
        if args.dry_run:
            vault_count = ch.query("SELECT count() FROM metamorpho_vault_registry").result_rows[0][0]
            print(json.dumps({"run_id": run_id, "dry_run": True, "head": head, "registry_vaults": int(vault_count or 0)}, indent=2))
            return 0
        assert rpc is not None
        discovered = 0 if args.skip_factory_discovery else _discover_factory_vaults(ch, rpc, args, head)
        events = 0 if args.skip_events else _backfill_vault_events(ch, rpc, args, head)
        flow_rows = 0 if args.skip_replay else _replay_vault_flows(ch)
        if args.skip_rpc_snapshot:
            registry, states, allocations = 0, 0, 0
        else:
            registry, states, allocations = _snapshot_vaults(ch, rpc, args, head, timestamp)
        print(json.dumps({
            "run_id": run_id,
            "block_number": head,
            "timestamp": timestamp.isoformat(),
            "discovered_vaults": discovered,
            "event_rows": events,
            "flow_rows": flow_rows,
            "registry_rows": registry,
            "state_rows": states,
            "allocation_rows": allocations,
        }, indent=2))
    finally:
        ch.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill and snapshot Ethereum MetaMorpho vault coverage")
    parser.add_argument("--config", default=None)
    parser.add_argument("--rpc-url", default=None)
    parser.add_argument("--block-number", type=int, default=None)
    parser.add_argument("--factory-addresses", default=None)
    parser.add_argument("--factory-start-block", type=int, default=None)
    parser.add_argument("--events-start-block", type=int, default=None)
    parser.add_argument("--ignore-existing-cursor", action="store_true")
    parser.add_argument("--max-vaults", type=int, default=None)
    parser.add_argument("--max-queue-items", type=int, default=256)
    parser.add_argument("--log-chunk-size", type=int, default=25_000)
    parser.add_argument("--progress-every-ranges", type=int, default=10)
    parser.add_argument("--http-timeout-sec", type=int, default=60)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--skip-factory-discovery", action="store_true")
    parser.add_argument("--skip-events", action="store_true")
    parser.add_argument("--skip-replay", action="store_true")
    parser.add_argument("--skip-rpc-snapshot", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
