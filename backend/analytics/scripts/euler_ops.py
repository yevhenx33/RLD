"""Operator commands for the Ethereum Euler V2 verified vault indexer."""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from dataclasses import dataclass
from typing import Any

import clickhouse_connect
from eth_abi import decode, encode
from eth_utils import keccak

from analytics.tokens import TOKENS

ZERO_ADDRESS = "0x" + "0" * 40
DEFAULT_BLOCK_CONFIRMATIONS = 12
RPC_TIMEOUT_SEC = 60
EULER_GOVERNED_PERSPECTIVE = "0xc0121817ff224a018840e4d15a864747d36e6eb2"
SECONDS_PER_YEAR = 365.2425 * 24 * 60 * 60
RAY = 10**27


def _selector(signature: str) -> str:
    return "0x" + keccak(text=signature).hex()[:8]


def _topic(signature: str) -> str:
    return "0x" + keccak(text=signature).hex()


TOPIC_EVAULT_CREATED = _topic("EVaultCreated(address,address,address)")
TOPIC_DEPOSIT = _topic("Deposit(address,address,uint256,uint256)")
TOPIC_WITHDRAW = _topic("Withdraw(address,address,address,uint256,uint256)")
TOPIC_BORROW = _topic("Borrow(address,uint256)")
TOPIC_REPAY = _topic("Repay(address,uint256)")
TOPIC_INTEREST_ACCRUED = _topic("InterestAccrued(address,uint256)")
TOPIC_LIQUIDATE = _topic("Liquidate(address,address,address,uint256,uint256)")
TOPIC_VAULT_STATUS = _topic("VaultStatus(uint256,uint256,uint256,uint256,uint256,uint256,uint256)")
TOPIC_GOV_SET_LTV = _topic("GovSetLTV(address,uint16,uint16,uint16,uint48,uint32)")
TOPIC_GOV_SET_INTEREST_FEE = _topic("GovSetInterestFee(uint16)")
TOPIC_GOV_SET_INTEREST_RATE_MODEL = _topic("GovSetInterestRateModel(address)")
TOPIC_GOV_SET_MAX_LIQUIDATION_DISCOUNT = _topic("GovSetMaxLiquidationDiscount(uint16)")
TOPIC_GOV_SET_LIQUIDATION_COOL_OFF_TIME = _topic("GovSetLiquidationCoolOffTime(uint16)")
TOPIC_GOV_SET_HOOK_CONFIG = _topic("GovSetHookConfig(address,uint32)")
TOPIC_GOV_SET_CAPS = _topic("GovSetCaps(uint16,uint16)")
TOPIC_GOV_SET_CONFIG_FLAGS = _topic("GovSetConfigFlags(uint32)")

EVENT_MAP = {
    TOPIC_EVAULT_CREATED: "EVaultCreated",
    TOPIC_DEPOSIT: "Deposit",
    TOPIC_WITHDRAW: "Withdraw",
    TOPIC_BORROW: "Borrow",
    TOPIC_REPAY: "Repay",
    TOPIC_INTEREST_ACCRUED: "InterestAccrued",
    TOPIC_LIQUIDATE: "Liquidate",
    TOPIC_VAULT_STATUS: "VaultStatus",
    TOPIC_GOV_SET_LTV: "GovSetLTV",
    TOPIC_GOV_SET_INTEREST_FEE: "GovSetInterestFee",
    TOPIC_GOV_SET_INTEREST_RATE_MODEL: "GovSetInterestRateModel",
    TOPIC_GOV_SET_MAX_LIQUIDATION_DISCOUNT: "GovSetMaxLiquidationDiscount",
    TOPIC_GOV_SET_LIQUIDATION_COOL_OFF_TIME: "GovSetLiquidationCoolOffTime",
    TOPIC_GOV_SET_HOOK_CONFIG: "GovSetHookConfig",
    TOPIC_GOV_SET_CAPS: "GovSetCaps",
    TOPIC_GOV_SET_CONFIG_FLAGS: "GovSetConfigFlags",
}
EULER_VAULT_TOPICS = tuple(EVENT_MAP.keys())
EULER_STATE_TOPICS = (
    TOPIC_VAULT_STATUS,
    TOPIC_GOV_SET_LTV,
    TOPIC_GOV_SET_INTEREST_FEE,
    TOPIC_GOV_SET_INTEREST_RATE_MODEL,
    TOPIC_GOV_SET_MAX_LIQUIDATION_DISCOUNT,
    TOPIC_GOV_SET_LIQUIDATION_COOL_OFF_TIME,
    TOPIC_GOV_SET_HOOK_CONFIG,
    TOPIC_GOV_SET_CAPS,
    TOPIC_GOV_SET_CONFIG_FLAGS,
)


SELECTORS = {
    "verifiedArray": _selector("verifiedArray()"),
    "isVerified": _selector("isVerified(address)"),
    "asset": _selector("asset()"),
    "name": _selector("name()"),
    "symbol": _selector("symbol()"),
    "decimals": _selector("decimals()"),
    "totalAssets": _selector("totalAssets()"),
    "totalSupply": _selector("totalSupply()"),
    "interestRate": _selector("interestRate()"),
    "cash": _selector("cash()"),
    "totalBorrows": _selector("totalBorrows()"),
    "accumulatedFees": _selector("accumulatedFees()"),
    "interestAccumulator": _selector("interestAccumulator()"),
    "interestFee": _selector("interestFee()"),
}


def normalize_address(value: str | None) -> str:
    raw = str(value or "").lower().removeprefix("0x")
    if len(raw) < 40:
        raw = raw.rjust(40, "0")
    return "0x" + raw[-40:]


def spy_to_apy(spy: int) -> float:
    if spy <= 0:
        return 0.0
    import math

    per_second = float(spy) / RAY
    annual = per_second * SECONDS_PER_YEAR
    if annual < 0.50:
        try:
            return min(math.expm1(math.log1p(per_second) * SECONDS_PER_YEAR), 10.0)
        except (OverflowError, ValueError):
            pass
    return min(max(annual, 0.0), 10.0)


def interest_fee_ratio(raw_fee: int) -> float:
    if raw_fee <= 0:
        return 0.0
    if raw_fee <= 10_000:
        return max(0.0, min(float(raw_fee) / 10_000.0, 1.0))
    return max(0.0, min(float(raw_fee) / 1e18, 1.0))


@dataclass
class RpcClient:
    url: str
    timeout: int = RPC_TIMEOUT_SEC
    retries: int = 2

    def call(self, method: str, params: list[Any]) -> Any:
        import requests

        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = requests.post(self.url, json=payload, timeout=self.timeout)
                response.raise_for_status()
                data = response.json()
                if "error" in data:
                    raise RuntimeError(data["error"])
                return data.get("result")
            except Exception as exc:
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"RPC {method} failed: {last_exc}")

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def block_timestamp(self, block_number: int) -> dt.datetime:
        block = self.call("eth_getBlockByNumber", [hex(int(block_number)), False])
        timestamp = int(block["timestamp"], 16)
        return dt.datetime.fromtimestamp(timestamp, tz=dt.UTC).replace(tzinfo=None)

    def eth_call(self, to: str, data: str, block_tag: str | int = "latest") -> str:
        tag = hex(block_tag) if isinstance(block_tag, int) else block_tag
        return self.call("eth_call", [{"to": to, "data": data}, tag])

    def get_logs(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self.call("eth_getLogs", [params]) or []


def rpc_url(value: str | None = None) -> str:
    url = (value or os.getenv("MAINNET_RPC_URL") or os.getenv("ETH_RPC_URL") or os.getenv("RPC_URL") or "").strip()
    if not url:
        raise SystemExit("MAINNET_RPC_URL, ETH_RPC_URL, RPC_URL, or --rpc-url is required")
    return url


def ch_client():
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


def _decode_address(value: str) -> str:
    if not value or value == "0x":
        return ""
    return normalize_address(decode(["address"], bytes.fromhex(value.removeprefix("0x")))[0])


def _decode_uint(value: str) -> int:
    if not value or value == "0x":
        return 0
    return int(decode(["uint256"], bytes.fromhex(value.removeprefix("0x")))[0])


def _decode_bool(value: str) -> bool:
    if not value or value == "0x":
        return False
    return bool(decode(["bool"], bytes.fromhex(value.removeprefix("0x")))[0])


def _decode_string(value: str) -> str:
    if not value or value == "0x":
        return ""
    raw = bytes.fromhex(value.removeprefix("0x"))
    try:
        return str(decode(["string"], raw)[0])
    except Exception:
        if len(raw) >= 32:
            return raw[:32].rstrip(b"\x00").decode("utf-8", "ignore")
    return ""


def _call_no_args(rpc: RpcClient, address: str, selector_name: str, block_tag: str | int = "latest") -> str:
    return rpc.eth_call(address, SELECTORS[selector_name], block_tag)


def _call_uint(rpc: RpcClient, address: str, selector_name: str, block_tag: str | int = "latest") -> int:
    return _decode_uint(_call_no_args(rpc, address, selector_name, block_tag))


def _call_string(rpc: RpcClient, address: str, selector_name: str, block_tag: str | int = "latest") -> str:
    return _decode_string(_call_no_args(rpc, address, selector_name, block_tag))


def _is_verified(rpc: RpcClient, vault: str, block_tag: str | int = "latest") -> bool:
    calldata = SELECTORS["isVerified"] + encode(["address"], [vault]).hex()
    return _decode_bool(rpc.eth_call(EULER_GOVERNED_PERSPECTIVE, calldata, block_tag))


def _verified_array(rpc: RpcClient, block_tag: str | int = "latest") -> list[str]:
    result = rpc.eth_call(EULER_GOVERNED_PERSPECTIVE, SELECTORS["verifiedArray"], block_tag)
    values = decode(["address[]"], bytes.fromhex(result.removeprefix("0x")))[0]
    return [normalize_address(value) for value in values]


def _asset(rpc: RpcClient, vault: str, block_tag: str | int = "latest") -> str:
    return _decode_address(_call_no_args(rpc, vault, "asset", block_tag))


def _token_meta(rpc: RpcClient, asset: str, block_tag: str | int = "latest") -> tuple[str, int]:
    meta = TOKENS.get(asset.removeprefix("0x").lower())
    if meta:
        return str(meta[0]), int(meta[1])
    symbol = _call_string(rpc, asset, "symbol", block_tag) or asset[:10]
    decimals = _call_uint(rpc, asset, "decimals", block_tag) or 18
    return symbol, int(decimals)


def _vault_name(rpc: RpcClient, vault: str, block_tag: str | int = "latest") -> str:
    try:
        return _call_string(rpc, vault, "name", block_tag)
    except Exception:
        return ""


def _registry_rows(ch, verified_only: bool = False) -> list[dict[str, Any]]:
    where = "WHERE verified = 1" if verified_only else ""
    rows = ch.query(
        f"""
        SELECT vault_address, asset_address, asset_symbol, asset_decimals, verified
        FROM euler_vault_registry FINAL
        {where}
        ORDER BY vault_address
        """
    ).result_rows
    return [
        {
            "vault_address": normalize_address(row[0]),
            "asset_address": normalize_address(row[1]) if row[1] else "",
            "asset_symbol": str(row[2] or ""),
            "asset_decimals": int(row[3] or 18),
            "verified": bool(row[4]),
        }
        for row in rows
    ]


def refresh_verified(args) -> int:
    rpc = RpcClient(rpc_url(args.rpc_url), timeout=args.http_timeout_sec, retries=args.retries)
    block_tag = hex(args.block_number) if args.block_number else "latest"
    verified = _verified_array(rpc, block_tag)
    if args.max_vaults:
        verified = verified[: args.max_vaults]
    rows = []
    for idx, vault in enumerate(verified, start=1):
        is_verified = _is_verified(rpc, vault, block_tag)
        asset = _asset(rpc, vault, block_tag)
        symbol, decimals = _token_meta(rpc, asset, block_tag)
        rows.append(
            [
                vault,
                asset,
                symbol,
                decimals,
                1 if is_verified else 0,
                "governedPerspective",
                0,
                dt.datetime(1970, 1, 1),
                _vault_name(rpc, vault, block_tag),
            ]
        )
        if args.progress_every and idx % args.progress_every == 0:
            print(json.dumps({"stage": "refresh-verified", "processed": idx, "vault": vault}))

    if args.dry_run:
        print(json.dumps({"verified_count": len(verified), "dry_run": True}, indent=2, sort_keys=True))
        return 0

    from analytics.euler_schema import ensure_euler_tables

    ch = ch_client()
    try:
        ensure_euler_tables(ch)
        if rows:
            ch.insert(
                "euler_vault_registry",
                rows,
                column_names=[
                    "vault_address",
                    "asset_address",
                    "asset_symbol",
                    "asset_decimals",
                    "verified",
                    "source",
                    "created_block",
                    "created_timestamp",
                    "name",
                ],
            )
        stale_rows = []
        if not args.max_vaults:
            known = _registry_rows(ch, verified_only=False)
            verified_set = set(verified)
            for row in known:
                if row["verified"] and row["vault_address"] not in verified_set:
                    stale_rows.append(
                        [
                            row["vault_address"],
                            row["asset_address"],
                            row["asset_symbol"],
                            row["asset_decimals"],
                            0,
                            "governedPerspective",
                            0,
                            dt.datetime(1970, 1, 1),
                            "",
                        ]
                    )
        if stale_rows:
            ch.insert(
                "euler_vault_registry",
                stale_rows,
                column_names=[
                    "vault_address",
                    "asset_address",
                    "asset_symbol",
                    "asset_decimals",
                    "verified",
                    "source",
                    "created_block",
                    "created_timestamp",
                    "name",
                ],
            )
    finally:
        ch.close()
    print(json.dumps({"verified_count": len(verified), "stale_unverified": len(stale_rows), "dry_run": False}, indent=2, sort_keys=True))
    return 0


def _block_timestamp_cache(rpc: RpcClient, logs: list[dict[str, Any]]) -> dict[int, dt.datetime]:
    cache: dict[int, dt.datetime] = {}
    for entry in logs:
        block = int(entry["blockNumber"], 16)
        if block not in cache:
            cache[block] = rpc.block_timestamp(block)
    return cache


def _insert_logs(ch, logs: list[dict[str, Any]], timestamps: dict[int, dt.datetime]) -> int:
    rows = []
    for entry in logs:
        topics = [str(topic).lower() for topic in entry.get("topics") or []]
        block_number = int(entry["blockNumber"], 16)
        rows.append(
            [
                block_number,
                timestamps.get(block_number, dt.datetime.utcnow()),
                str(entry.get("transactionHash") or ""),
                int(entry.get("logIndex", "0x0"), 16),
                normalize_address(entry.get("address")),
                EVENT_MAP.get(topics[0], "") if topics else "",
                topics[0] if len(topics) > 0 else "",
                topics[1] if len(topics) > 1 else None,
                topics[2] if len(topics) > 2 else None,
                topics[3] if len(topics) > 3 else None,
                str(entry.get("data") or "0x"),
            ]
        )
    if rows:
        ch.insert(
            "euler_events",
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
    return len(rows)


def _process_range(ch, from_block: int, to_block: int) -> int:
    from analytics.processor import SimulatedLog
    from analytics.sources.euler import EulerSource

    source = EulerSource()
    source.get_cursor(ch)
    rows = ch.query(
        f"""
        SELECT block_number, block_timestamp, tx_hash, log_index, contract,
               event_name, topic0, topic1, topic2, topic3, data
        FROM euler_events
        WHERE block_number >= {int(from_block)} AND block_number <= {int(to_block)}
        ORDER BY block_number, log_index
        """
    ).result_rows
    block_ts_map = {row[0]: row[1] for row in rows}
    decoded = []
    for row in rows:
        item = source.decode(SimulatedLog(row), block_ts_map)
        if item:
            decoded.append(item)
    written = source.merge(ch, decoded) if decoded else 0
    ch.insert("processor_state", [["EULER_MARKET", int(to_block)]], column_names=["protocol", "last_processed_block"])
    return written


def replay(args) -> int:
    rpc = RpcClient(rpc_url(args.rpc_url), timeout=args.http_timeout_sec, retries=args.retries)
    from_block = int(args.from_block)
    to_block = int(args.to_block or 0)
    if to_block <= 0:
        to_block = max(0, rpc.block_number() - int(args.confirmations))
    if to_block < from_block:
        raise SystemExit("--to-block must be >= --from-block")
    ch = ch_client()
    inserted = 0
    written = 0
    try:
        from analytics.euler_schema import ensure_euler_tables

        ensure_euler_tables(ch)
        registry = _registry_rows(ch, verified_only=args.verified_only)
        vaults = [row["vault_address"] for row in registry]
        if not vaults and not args.discover_factory:
            raise SystemExit("No Euler vault registry rows found. Run euler-refresh-verified first or pass --discover-factory.")
        address_chunks = [vaults[i : i + args.address_batch_size] for i in range(0, len(vaults), args.address_batch_size)]
        topic_sets = [list(EULER_VAULT_TOPICS)]
        if args.state_only:
            topic_sets = [list(EULER_STATE_TOPICS)]
        if args.discover_factory:
            address_chunks.append([])
            topic_sets.append([TOPIC_EVAULT_CREATED])

        for start in range(from_block, to_block + 1, args.batch_blocks):
            end = min(start + args.batch_blocks - 1, to_block)
            for topics in topic_sets:
                chunks = [[]] if topics == [TOPIC_EVAULT_CREATED] else address_chunks
                for addresses in chunks:
                    if topics != [TOPIC_EVAULT_CREATED] and not addresses:
                        continue
                    params: dict[str, Any] = {"fromBlock": hex(start), "toBlock": hex(end), "topics": [topics]}
                    if addresses:
                        params["address"] = addresses
                    logs = rpc.get_logs(params)
                    if not logs:
                        continue
                    if args.dry_run:
                        inserted += len(logs)
                        continue
                    inserted += _insert_logs(ch, logs, _block_timestamp_cache(rpc, logs))
            if args.progress_every:
                print(json.dumps({"stage": "euler-replay", "from": start, "to": end, "inserted": inserted}))
        if args.process and not args.dry_run:
            written = _process_range(ch, from_block, to_block)
        elif args.run_processor and not args.dry_run:
            from analytics.processor import ProtocolProcessor
            from analytics.sources.euler import EulerSource

            ProtocolProcessor(EulerSource()).run_processor_cycle()
    finally:
        ch.close()
    print(json.dumps({"from_block": from_block, "to_block": to_block, "inserted_raw_logs": inserted, "written_timeseries_rows": written, "dry_run": bool(args.dry_run)}, indent=2, sort_keys=True))
    return 0


def anchor(args) -> int:
    rpc = RpcClient(rpc_url(args.rpc_url), timeout=args.http_timeout_sec, retries=args.retries)
    block_number = int(args.block_number or 0)
    if block_number <= 0:
        block_number = max(0, rpc.block_number() - int(args.confirmations))
    if args.dry_run:
        print(json.dumps({"block_number": block_number, "dry_run": True}, indent=2, sort_keys=True))
        return 0
    ch = ch_client()
    drifts: list[dict[str, Any]] = []
    checked = 0
    try:
        from analytics.euler_schema import ensure_euler_tables

        ensure_euler_tables(ch)
        rows = ch.query(
            f"""
            SELECT
                r.vault_address, r.asset_address, r.asset_symbol, r.asset_decimals,
                s.total_shares, s.total_borrows, s.accumulated_fees, s.cash,
                s.interest_accumulator, s.interest_rate, s.interest_fee, s.last_event_block
            FROM (
                SELECT vault_address, asset_address, asset_symbol, asset_decimals, verified
                FROM euler_vault_registry FINAL
                WHERE verified = 1
                ORDER BY vault_address
                LIMIT {int(args.max_vaults)}
            ) AS r
            LEFT JOIN (
                SELECT vault_address, total_shares, total_borrows, accumulated_fees, cash,
                       interest_accumulator, interest_rate, interest_fee, last_event_block
                FROM euler_vault_state FINAL
            ) AS s ON r.vault_address = s.vault_address
            ORDER BY r.vault_address
            """
        ).result_rows
        latest_rows = ch.query(
            """
            SELECT entity_id, timestamp, supply_usd, borrow_usd, supply_apy, borrow_apy,
                   utilization, price_usd
            FROM api_market_latest FINAL
            WHERE protocol = 'EULER_MARKET'
            """
        ).result_rows
        latest = {
            normalize_address(row[0]): {
                "timestamp": row[1],
                "supply_usd": float(row[2] or 0.0),
                "borrow_usd": float(row[3] or 0.0),
                "supply_apy": float(row[4] or 0.0),
                "borrow_apy": float(row[5] or 0.0),
                "utilization": float(row[6] or 0.0),
                "price_usd": float(row[7] or 0.0),
            }
            for row in latest_rows
        }
        for row in rows:
            vault = normalize_address(row[0])
            checked += 1
            indexed_asset = normalize_address(row[1]) if row[1] else ""
            indexed = {
                "total_shares": int(row[4] or 0),
                "total_borrows": int(row[5] or 0),
                "accumulated_fees": int(row[6] or 0),
                "cash": int(row[7] or 0),
                "interest_accumulator": int(row[8] or 0),
                "interest_rate": int(row[9] or 0),
                "interest_fee": int(row[10] or 0),
                "last_event_block": int(row[11] or block_number),
            }
            check_block = indexed["last_event_block"] or block_number
            onchain = {
                "asset": _asset(rpc, vault, check_block),
                "totalAssets": _call_uint(rpc, vault, "totalAssets", check_block),
                "totalSupply": _call_uint(rpc, vault, "totalSupply", check_block),
                "interestRate": _call_uint(rpc, vault, "interestRate", check_block),
                "cash": _call_uint(rpc, vault, "cash", check_block),
                "totalBorrows": _call_uint(rpc, vault, "totalBorrows", check_block),
                "accumulatedFees": _call_uint(rpc, vault, "accumulatedFees", check_block),
                "interestAccumulator": _call_uint(rpc, vault, "interestAccumulator", check_block),
                "interestFee": _call_uint(rpc, vault, "interestFee", check_block),
                "isVerified": _is_verified(rpc, vault, block_number),
            }
            comparisons = [
                ("asset", indexed_asset, onchain["asset"]),
                ("verified", True, onchain["isVerified"]),
                ("total_shares", indexed["total_shares"], onchain["totalSupply"]),
                ("total_borrows", indexed["total_borrows"], onchain["totalBorrows"]),
                ("accumulated_fees", indexed["accumulated_fees"], onchain["accumulatedFees"]),
                ("cash", indexed["cash"], onchain["cash"]),
                ("interest_accumulator", indexed["interest_accumulator"], onchain["interestAccumulator"]),
                ("interest_rate", indexed["interest_rate"], onchain["interestRate"]),
                ("interest_fee", indexed["interest_fee"], onchain["interestFee"]),
            ]
            for field, expected, actual in comparisons:
                if expected != actual:
                    drifts.append({"vault": vault, "field": field, "indexed": expected, "onchain": actual})
            indexed_total_assets = indexed["cash"] + indexed["total_borrows"]
            if indexed_total_assets != onchain["totalAssets"]:
                drifts.append({"vault": vault, "field": "total_assets", "indexed": indexed_total_assets, "onchain": onchain["totalAssets"]})
            latest_row = latest.get(vault)
            if latest_row and latest_row["price_usd"] > 0:
                scale = float(10 ** int(row[3] or 18))
                supply_usd = (onchain["totalAssets"] / scale) * latest_row["price_usd"]
                borrow_usd = (onchain["totalBorrows"] / scale) * latest_row["price_usd"]
                util = onchain["totalBorrows"] / onchain["totalAssets"] if onchain["totalAssets"] else 0.0
                borrow_apy = spy_to_apy(onchain["interestRate"])
                supply_apy = borrow_apy * util * (1.0 - interest_fee_ratio(onchain["interestFee"]))
                for field, expected, actual, tol in (
                    ("supply_usd", latest_row["supply_usd"], supply_usd, args.usd_tolerance),
                    ("borrow_usd", latest_row["borrow_usd"], borrow_usd, args.usd_tolerance),
                    ("borrow_apy", latest_row["borrow_apy"], borrow_apy, args.apy_tolerance),
                    ("supply_apy", latest_row["supply_apy"], supply_apy, args.apy_tolerance),
                    ("utilization", latest_row["utilization"], util, args.apy_tolerance),
                ):
                    if abs(float(expected) - float(actual)) > float(tol):
                        drifts.append({"vault": vault, "field": field, "indexed": expected, "onchain": actual, "tolerance": tol})
    finally:
        ch.close()
    payload = {"block_number": block_number, "checked_vaults": checked, "drift_count": len(drifts), "drifts": drifts[: args.max_diff_rows]}
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 1 if args.fail_on_drift and drifts else 0
