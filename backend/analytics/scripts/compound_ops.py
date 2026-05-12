"""Compound bootstrap and RPC anchor operations."""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import uuid
from typing import Any

import hypersync
import requests
from eth_abi import decode as abi_decode
from eth_utils import keccak

from analytics.base import insert_rows_batched
from analytics.protocols import COMPOUND_V2_MARKET, COMPOUND_V3_MARKET
from analytics.sources.compound import (
    BASE_INDEX_SCALE,
    BLOCKS_PER_YEAR,
    COMPOUND_V2_UNITROLLER,
    COMPOUND_V3_USDC,
    COMPOUND_V3_WETH,
    ETH_PSEUDO_ADDRESS,
    SECONDS_PER_YEAR,
    STATIC_V2_MARKETS,
    STATIC_V3_COMETS,
    WAD,
    CompoundV2Source,
    CompoundV3Source,
    ensure_compound_tables,
)
from analytics.state import ensure_source_status_table, update_source_status


def _selector(signature: str) -> str:
    return "0x" + keccak(text=signature).hex()[:8]


def _word_address(address: str) -> str:
    return str(address or "").lower().removeprefix("0x").rjust(64, "0")[-64:]


def _uint_word(value: int) -> str:
    return f"{int(value):064x}"


class RpcClient:
    def __init__(self, url: str, timeout: int = 60, retries: int = 2):
        self.url = url
        self.timeout = timeout
        self.retries = retries

    def call(self, method: str, params: list[Any]) -> Any:
        last_error: Exception | None = None
        for _attempt in range(max(1, self.retries)):
            try:
                response = requests.post(
                    self.url,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                    timeout=self.timeout,
                )
                if response.status_code >= 400:
                    raise RuntimeError(f"HTTP {response.status_code}")
                payload = response.json()
                if payload.get("error"):
                    raise RuntimeError(payload["error"])
                return payload.get("result")
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"RPC {method} failed: {last_error}")

    def eth_call(self, to: str, data: str, block_tag: str | int = "latest") -> str:
        tag = hex(int(block_tag)) if isinstance(block_tag, int) else block_tag
        result = self.call("eth_call", [{"to": to, "data": data}, tag])
        return str(result or "0x")

    def block_number(self) -> int:
        return int(str(self.call("eth_blockNumber", [])), 16)


def _rpc_url(args) -> str:
    value = (
        getattr(args, "rpc_url", None)
        or os.getenv("MAINNET_RPC_URL")
        or os.getenv("ETH_RPC_URL")
        or os.getenv("RPC_URL")
        or ""
    ).strip()
    if not value:
        raise SystemExit("MAINNET_RPC_URL, ETH_RPC_URL, RPC_URL, or --rpc-url is required")
    return value


def _decode_uint(raw: str) -> int:
    return int(str(raw or "0x0"), 16)


def _decode_address(raw: str) -> str:
    value = str(raw or "0x").removeprefix("0x").rjust(64, "0")
    return "0x" + value[-40:].lower()


def _decode_string(raw: str) -> str:
    data = bytes.fromhex(str(raw or "0x").removeprefix("0x"))
    if not data:
        return ""
    try:
        return str(abi_decode(["string"], data)[0])
    except Exception:
        try:
            return data[-32:].rstrip(b"\x00").decode("utf-8")
        except Exception:
            return ""


def _call_uint(rpc: RpcClient, to: str, signature: str, block_tag: str | int) -> int:
    return _decode_uint(rpc.eth_call(to, _selector(signature), block_tag))


def _call_address(rpc: RpcClient, to: str, signature: str, block_tag: str | int) -> str:
    return _decode_address(rpc.eth_call(to, _selector(signature), block_tag))


def _call_string(rpc: RpcClient, to: str, signature: str, block_tag: str | int) -> str:
    return _decode_string(rpc.eth_call(to, _selector(signature), block_tag))


def _call_uint_arg(rpc: RpcClient, to: str, signature: str, value: int, block_tag: str | int) -> int:
    return _decode_uint(rpc.eth_call(to, _selector(signature) + _uint_word(value), block_tag))


def _call_scaled_config(rpc: RpcClient, comet: str, name: str, block_tag: str | int) -> float:
    return _call_uint(rpc, comet, f"{name}()", block_tag) / WAD


def _call_per_second_rate_config(rpc: RpcClient, comet: str, name: str, block_tag: str | int) -> float:
    return _call_uint(rpc, comet, f"{name}()", block_tag) / WAD * SECONDS_PER_YEAR


def _call_v3_asset_info(rpc: RpcClient, comet: str, index: int, block_tag: str | int) -> tuple[int, str, str, int, int, int, int, int]:
    raw = rpc.eth_call(comet, _selector("getAssetInfo(uint8)") + _uint_word(index), block_tag)
    data = bytes.fromhex(raw.removeprefix("0x"))
    return abi_decode(["uint8", "address", "address", "uint64", "uint64", "uint64", "uint64", "uint128"], data)


def _call_v3_totals_basic(rpc: RpcClient, comet: str, block_tag: str | int) -> tuple[int, int, int, int, int, int, int, int]:
    raw = rpc.eth_call(comet, _selector("totalsBasic()"), block_tag)
    data = bytes.fromhex(raw.removeprefix("0x"))
    return abi_decode(["uint64", "uint64", "uint64", "uint64", "uint104", "uint104", "uint40", "uint8"], data)


def _call_markets(rpc: RpcClient, ctoken: str, block_tag: str | int) -> float:
    raw = rpc.eth_call(COMPOUND_V2_UNITROLLER, _selector("markets(address)") + _word_address(ctoken), block_tag)
    data = bytes.fromhex(raw.removeprefix("0x"))
    try:
        _listed, collateral_factor, _comped = abi_decode(["bool", "uint256", "bool"], data)
        return float(collateral_factor) / WAD
    except Exception:
        return 0.0


def _get_all_v2_markets(rpc: RpcClient, block_tag: str | int) -> list[str]:
    raw = rpc.eth_call(COMPOUND_V2_UNITROLLER, _selector("getAllMarkets()"), block_tag)
    data = bytes.fromhex(raw.removeprefix("0x"))
    try:
        return ["0x" + str(addr).lower().removeprefix("0x")[-40:] for addr in abi_decode(["address[]"], data)[0]]
    except Exception:
        return list(STATIC_V2_MARKETS)


def _erc20_meta(rpc: RpcClient, token: str, block_tag: str | int) -> tuple[str, int]:
    if token.lower() == ETH_PSEUDO_ADDRESS:
        return "ETH", 18
    symbol = _call_string(rpc, token, "symbol()", block_tag) or token[:10]
    decimals = _call_uint(rpc, token, "decimals()", block_tag)
    return symbol, int(decimals or 18)


def _insert_cursor(ch, protocol: str, block_number: int) -> None:
    if block_number <= 0:
        return
    ensure_source_status_table(ch)
    ch.insert("collector_state", [[protocol, block_number]], column_names=["protocol", "last_collected_block"])
    ch.insert("processor_state", [[protocol, block_number]], column_names=["protocol", "last_processed_block"])
    update_source_status(ch, protocol, "collector", last_scanned_block=block_number, source_head_block=block_number)
    update_source_status(ch, protocol, "processor", last_processed_block=block_number, last_event_block=block_number)


def bootstrap(args: argparse.Namespace, ch) -> int:
    ensure_compound_tables(ch)
    rpc = RpcClient(_rpc_url(args), timeout=int(args.http_timeout_sec), retries=int(args.retries))
    block = int(args.anchor_block or 0) or rpc.block_number() - int(args.confirmations)
    if args.protocol in {"v2", "both"}:
        _bootstrap_v2(ch, rpc, block, set_cursor=bool(args.set_cursors))
    if args.protocol in {"v3", "both"}:
        _bootstrap_v3(ch, rpc, block, set_cursor=bool(args.set_cursors))
    print(json.dumps({"status": "OK", "block": block, "protocol": args.protocol}, sort_keys=True))
    return 0


def _bootstrap_v2(ch, rpc: RpcClient, block: int, *, set_cursor: bool) -> None:
    registry_rows = []
    state_rows = []
    for ctoken in _get_all_v2_markets(rpc, block):
        ctoken = ctoken.lower()
        static = STATIC_V2_MARKETS.get(ctoken)
        try:
            symbol = _call_string(rpc, ctoken, "symbol()", block) or (static[0] if static else ctoken[:10])
            ctoken_decimals = int(_call_uint(rpc, ctoken, "decimals()", block) or 8)
            if static and ctoken == "0x4ddc2d193948926d02f9b1fe9e1daa0718270ed5":
                underlying = ETH_PSEUDO_ADDRESS
            else:
                underlying = _call_address(rpc, ctoken, "underlying()", block)
            underlying_symbol, underlying_decimals = _erc20_meta(rpc, underlying, block)
            collateral_factor = _call_markets(rpc, ctoken, block)
            total_supply = _call_uint(rpc, ctoken, "totalSupply()", block)
            cash = _call_uint(rpc, ctoken, "getCash()", block)
            borrows = _call_uint(rpc, ctoken, "totalBorrows()", block)
            reserves = _call_uint(rpc, ctoken, "totalReserves()", block)
            borrow_index = _call_uint(rpc, ctoken, "borrowIndex()", block)
            reserve_factor = _call_uint(rpc, ctoken, "reserveFactorMantissa()", block) / WAD
            exchange_rate = _call_uint(rpc, ctoken, "exchangeRateStored()", block)
            supply_apy = _call_uint(rpc, ctoken, "supplyRatePerBlock()", block) / WAD * BLOCKS_PER_YEAR
            borrow_apy = _call_uint(rpc, ctoken, "borrowRatePerBlock()", block) / WAD * BLOCKS_PER_YEAR
        except Exception:
            if not static:
                continue
            symbol, underlying, underlying_symbol, underlying_decimals, ctoken_decimals = static
            collateral_factor = 0.0
            total_supply = cash = borrows = reserves = 0
            borrow_index = WAD
            reserve_factor = exchange_rate = supply_apy = borrow_apy = 0
        registry_rows.append([
            ctoken, symbol, underlying.lower(), underlying_symbol, int(underlying_decimals),
            int(ctoken_decimals), float(collateral_factor), "rpc_bootstrap", 1,
        ])
        state_rows.append([
            ctoken, str(total_supply), str(borrows), str(reserves), str(cash), str(borrow_index),
            float(reserve_factor), str(exchange_rate), float(supply_apy), float(borrow_apy), block,
            datetime.datetime.fromtimestamp(0),
        ])
    if registry_rows:
        insert_rows_batched(
            ch,
            "compound_v2_market_registry",
            registry_rows,
            ["ctoken", "symbol", "underlying", "underlying_symbol", "underlying_decimals", "ctoken_decimals", "collateral_factor", "source", "active"],
        )
    if state_rows:
        insert_rows_batched(
            ch,
            "compound_v2_market_state",
            state_rows,
            ["ctoken", "total_supply_ctokens", "total_borrows", "total_reserves", "cash", "borrow_index", "reserve_factor", "exchange_rate", "supply_apy", "borrow_apy", "last_event_block", "last_event_timestamp"],
        )
    if set_cursor:
        _insert_cursor(ch, COMPOUND_V2_MARKET, block)


def _bootstrap_v3(ch, rpc: RpcClient, block: int, *, set_cursor: bool) -> None:
    registry_rows = []
    collateral_rows = []
    state_rows = []
    for comet, defaults in STATIC_V3_COMETS.items():
        (
            symbol,
            base_token,
            base_symbol,
            base_decimals,
            supply_kink,
            supply_low,
            supply_high,
            supply_base,
            borrow_kink,
            borrow_low,
            borrow_high,
            borrow_base,
        ) = defaults
        state_values = None
        try:
            symbol = _call_string(rpc, comet, "symbol()", block) or defaults[0]
            base_token = _call_address(rpc, comet, "baseToken()", block)
            base_symbol, base_decimals = _erc20_meta(rpc, base_token, block)
            total_supply = _call_uint(rpc, comet, "totalSupply()", block)
            total_borrow = _call_uint(rpc, comet, "totalBorrow()", block)
            base_supply_index, base_borrow_index, _tracking_supply, _tracking_borrow, supply_principal, borrow_principal, last_accrual_time, _pause_flags = _call_v3_totals_basic(rpc, comet, block)
            state_values = (
                total_supply,
                total_borrow,
                supply_principal,
                borrow_principal,
                base_supply_index,
                base_borrow_index,
                last_accrual_time,
            )
        except Exception:
            pass
        try:
            num_assets = int(_call_uint(rpc, comet, "numAssets()", block) or 0)
        except Exception:
            num_assets = 0
        for index in range(num_assets):
            try:
                _offset, asset, price_feed, scale, borrow_cf, liquidate_cf, liquidation_factor, supply_cap = _call_v3_asset_info(rpc, comet, index, block)
            except Exception:
                continue
            collateral_rows.append([
                comet, str(asset).lower(), str(price_feed).lower(), str(scale),
                float(borrow_cf) / WAD, float(liquidate_cf) / WAD, float(liquidation_factor) / WAD,
                str(supply_cap), "rpc_bootstrap", 1,
            ])
        try:
            supply_kink = _call_scaled_config(rpc, comet, "supplyKink", block)
        except Exception:
            pass
        try:
            supply_low = _call_per_second_rate_config(rpc, comet, "supplyPerSecondInterestRateSlopeLow", block)
        except Exception:
            pass
        try:
            supply_high = _call_per_second_rate_config(rpc, comet, "supplyPerSecondInterestRateSlopeHigh", block)
        except Exception:
            pass
        try:
            supply_base = _call_per_second_rate_config(rpc, comet, "supplyPerSecondInterestRateBase", block)
        except Exception:
            pass
        try:
            borrow_kink = _call_scaled_config(rpc, comet, "borrowKink", block)
        except Exception:
            pass
        try:
            borrow_low = _call_per_second_rate_config(rpc, comet, "borrowPerSecondInterestRateSlopeLow", block)
        except Exception:
            pass
        try:
            borrow_high = _call_per_second_rate_config(rpc, comet, "borrowPerSecondInterestRateSlopeHigh", block)
        except Exception:
            pass
        try:
            borrow_base = _call_per_second_rate_config(rpc, comet, "borrowPerSecondInterestRateBase", block)
        except Exception:
            pass
        registry_rows.append([
            comet, symbol, base_token.lower(), base_symbol, int(base_decimals),
            float(supply_kink), float(supply_low), float(supply_high), float(supply_base),
            float(borrow_kink), float(borrow_low), float(borrow_high), float(borrow_base),
            "rpc_bootstrap", 1,
        ])
        if state_values is not None:
            total_supply, total_borrow, supply_principal, borrow_principal, base_supply_index, base_borrow_index, last_accrual_time = state_values
            state_rows.append([
                comet,
                str(total_supply),
                str(total_borrow),
                str(supply_principal),
                str(borrow_principal),
                str(base_supply_index),
                str(base_borrow_index),
                "0",
                block,
                datetime.datetime.fromtimestamp(int(last_accrual_time or 0)),
                datetime.datetime.fromtimestamp(int(last_accrual_time or 0)),
            ])
    insert_rows_batched(
        ch,
        "compound_v3_comet_registry",
        registry_rows,
        ["comet", "symbol", "base_token", "base_symbol", "base_decimals", "supply_kink", "supply_slope_low", "supply_slope_high", "supply_base", "borrow_kink", "borrow_slope_low", "borrow_slope_high", "borrow_base", "source", "active"],
    )
    if collateral_rows:
        insert_rows_batched(
            ch,
            "compound_v3_collateral_registry",
            collateral_rows,
            ["comet", "asset", "price_feed", "scale", "borrow_collateral_factor", "liquidate_collateral_factor", "liquidation_factor", "supply_cap", "source", "active"],
        )
    if state_rows:
        insert_rows_batched(
            ch,
            "compound_v3_comet_state",
            state_rows,
            [
                "comet",
                "total_supply_base",
                "total_borrow_base",
                "total_supply_principal",
                "total_borrow_principal",
                "base_supply_index",
                "base_borrow_index",
                "reserves_base",
                "last_event_block",
                "last_event_timestamp",
                "last_accrual_timestamp",
            ],
        )
    if set_cursor:
        _insert_cursor(ch, COMPOUND_V3_MARKET, block)


def anchor(args: argparse.Namespace, ch) -> int:
    ensure_compound_tables(ch)
    rpc = RpcClient(_rpc_url(args), timeout=int(args.http_timeout_sec), retries=int(args.retries))
    if args.block_number:
        block = int(args.block_number)
    elif args.block_mode == "latest":
        block = rpc.block_number() - int(args.confirmations)
    else:
        block = _processed_block(ch, args.protocol)
    run_id = str(uuid.uuid4())
    diffs: list[list[Any]] = []
    if args.protocol in {"v2", "both"}:
        diffs.extend(_anchor_v2(ch, rpc, block, run_id, args))
    if args.protocol in {"v3", "both"}:
        diffs.extend(_anchor_v3(ch, rpc, block, run_id, args))
    if diffs:
        insert_rows_batched(ch, "compound_rpc_anchor_diffs", diffs, ["run_id", "protocol", "block_number", "market_id", "field", "indexed_value", "rpc_value", "drift"])
    drifted = {row[3] for row in diffs}
    max_notional = max((float(row[7]) for row in diffs if not str(row[4]).endswith("_apy")), default=0.0)
    max_apy = max((float(row[7]) for row in diffs if str(row[4]).endswith("_apy")), default=0.0)
    status = "OK" if not diffs else "DRIFT"
    checked = _count_checked(ch, args.protocol)
    ch.insert(
        "compound_rpc_anchor_runs",
        [[run_id, "COMPOUND", block, status, checked, len(drifted), max_notional, max_apy, ""]],
        column_names=["run_id", "protocol", "block_number", "status", "checked_markets", "drifted_markets", "max_notional_drift", "max_apy_drift", "error"],
    )
    payload = {"runId": run_id, "block": block, "status": status, "drifts": len(diffs), "driftedMarkets": len(drifted)}
    print(json.dumps(payload, sort_keys=True))
    return 1 if getattr(args, "fail_on_drift", False) and diffs else 0


def _processed_block(ch, protocol: str = "both") -> int:
    protocols = []
    if protocol in {"v2", "both"}:
        protocols.append(COMPOUND_V2_MARKET)
    if protocol in {"v3", "both"}:
        protocols.append(COMPOUND_V3_MARKET)
    state_in = ", ".join(f"'{p}'" for p in protocols)
    rows = ch.query(
        f"""
        SELECT protocol, max(last_processed_block)
        FROM processor_state FINAL
        WHERE protocol IN ({state_in})
        GROUP BY protocol
        """
    ).result_rows
    blocks = [int(row[1] or 0) for row in rows]
    if not blocks:
        return 0
    return min(blocks) if protocol == "both" else max(blocks)


def _drift(indexed: float, rpc_value: float) -> float:
    basis = max(abs(float(rpc_value)), 1.0)
    return abs(float(indexed) - float(rpc_value)) / basis


def _maybe_diff(rows: list[list[Any]], run_id: str, protocol: str, block: int, market: str, field: str, indexed: float, rpc_value: float, threshold: float) -> None:
    drift = _drift(indexed, rpc_value)
    if drift > threshold:
        rows.append([run_id, protocol, block, market, field, str(indexed), str(rpc_value), drift])


def _anchor_v2(ch, rpc: RpcClient, block: int, run_id: str, args) -> list[list[Any]]:
    rows = ch.query(
        """
        SELECT r.ctoken, r.ctoken_decimals, r.underlying_decimals, r.collateral_factor,
               s.total_supply_ctokens, s.total_borrows, s.total_reserves, s.cash, s.supply_apy, s.borrow_apy
        FROM compound_v2_market_registry AS r FINAL
        INNER JOIN compound_v2_market_state AS s FINAL ON r.ctoken = s.ctoken
        WHERE r.active = 1
        """
    ).result_rows
    diffs: list[list[Any]] = []
    for ctoken, _cdec, _udec, collateral_factor, supply_ctokens, borrows, reserves, cash, supply_apy, borrow_apy in rows:
        ctoken = str(ctoken)
        try:
            rpc_supply_ctokens = _call_uint(rpc, ctoken, "totalSupply()", block)
            rpc_borrows = _call_uint(rpc, ctoken, "totalBorrows()", block)
            rpc_reserves = _call_uint(rpc, ctoken, "totalReserves()", block)
            rpc_cash = _call_uint(rpc, ctoken, "getCash()", block)
            rpc_supply_apy = _call_uint(rpc, ctoken, "supplyRatePerBlock()", block) / WAD * BLOCKS_PER_YEAR
            rpc_borrow_apy = _call_uint(rpc, ctoken, "borrowRatePerBlock()", block) / WAD * BLOCKS_PER_YEAR
            rpc_collateral_factor = _call_markets(rpc, ctoken, block)
        except Exception:
            continue
        indexed_supply_underlying = float(cash or 0) + float(borrows or 0) - float(reserves or 0)
        rpc_supply_underlying = float(rpc_cash) + float(rpc_borrows) - float(rpc_reserves)
        _maybe_diff(diffs, run_id, COMPOUND_V2_MARKET, block, ctoken, "total_supply_ctokens", float(supply_ctokens or 0), float(rpc_supply_ctokens), args.notional_threshold)
        _maybe_diff(diffs, run_id, COMPOUND_V2_MARKET, block, ctoken, "supply_underlying", indexed_supply_underlying, rpc_supply_underlying, args.notional_threshold)
        _maybe_diff(diffs, run_id, COMPOUND_V2_MARKET, block, ctoken, "total_borrows", float(borrows or 0), float(rpc_borrows), args.notional_threshold)
        _maybe_diff(diffs, run_id, COMPOUND_V2_MARKET, block, ctoken, "cash", float(cash or 0), float(rpc_cash), args.notional_threshold)
        _maybe_diff(diffs, run_id, COMPOUND_V2_MARKET, block, ctoken, "total_reserves", float(reserves or 0), float(rpc_reserves), args.notional_threshold)
        _maybe_diff(diffs, run_id, COMPOUND_V2_MARKET, block, ctoken, "supply_apy", float(supply_apy or 0), float(rpc_supply_apy), args.apy_threshold)
        _maybe_diff(diffs, run_id, COMPOUND_V2_MARKET, block, ctoken, "borrow_apy", float(borrow_apy or 0), float(rpc_borrow_apy), args.apy_threshold)
        _maybe_diff(diffs, run_id, COMPOUND_V2_MARKET, block, ctoken, "collateral_factor", float(collateral_factor or 0), float(rpc_collateral_factor), args.apy_threshold)
    return diffs


def _anchor_v3(ch, rpc: RpcClient, block: int, run_id: str, args) -> list[list[Any]]:
    rows = ch.query(
        """
        SELECT r.comet, r.base_token, s.total_supply_base, s.total_borrow_base,
               r.supply_kink, r.supply_slope_low, r.supply_slope_high, r.supply_base,
               r.borrow_kink, r.borrow_slope_low, r.borrow_slope_high, r.borrow_base
        FROM compound_v3_comet_registry AS r FINAL
        INNER JOIN compound_v3_comet_state AS s FINAL ON r.comet = s.comet
        WHERE r.active = 1
        """
    ).result_rows
    diffs: list[list[Any]] = []
    for (
        comet,
        base_token,
        supply,
        borrow,
        supply_kink,
        supply_low,
        supply_high,
        supply_base,
        borrow_kink,
        borrow_low,
        borrow_high,
        borrow_base,
    ) in rows:
        comet = str(comet)
        try:
            rpc_base_token = _call_address(rpc, comet, "baseToken()", block)
            rpc_supply = _call_uint(rpc, comet, "totalSupply()", block)
            rpc_borrow = _call_uint(rpc, comet, "totalBorrow()", block)
            util = _call_uint(rpc, comet, "getUtilization()", block)
            rpc_supply_apy = _call_uint_arg(rpc, comet, "getSupplyRate(uint256)", util, block) / WAD * SECONDS_PER_YEAR
            rpc_borrow_apy = _call_uint_arg(rpc, comet, "getBorrowRate(uint256)", util, block) / WAD * SECONDS_PER_YEAR
        except Exception:
            continue
        if str(base_token).lower() != rpc_base_token.lower():
            diffs.append([run_id, COMPOUND_V3_MARKET, block, comet, "base_token", str(base_token), rpc_base_token, 1.0])
        indexed_supply = float(supply or 0)
        indexed_borrow = float(borrow or 0)
        indexed_util = indexed_borrow / indexed_supply if indexed_supply > 0 else 0.0
        _maybe_diff(diffs, run_id, COMPOUND_V3_MARKET, block, comet, "total_supply_base", indexed_supply, float(rpc_supply), args.notional_threshold)
        _maybe_diff(diffs, run_id, COMPOUND_V3_MARKET, block, comet, "total_borrow_base", indexed_borrow, float(rpc_borrow), args.notional_threshold)
        _maybe_diff(diffs, run_id, COMPOUND_V3_MARKET, block, comet, "utilization", indexed_util, float(util) / WAD, args.notional_threshold)
        rate_utilization = float(util) / WAD
        indexed_supply_apy = _v3_anchor_rate(rate_utilization, float(supply_kink or 0), float(supply_low or 0), float(supply_high or 0), float(supply_base or 0))
        indexed_borrow_apy = _v3_anchor_rate(rate_utilization, float(borrow_kink or 0), float(borrow_low or 0), float(borrow_high or 0), float(borrow_base or 0))
        _maybe_diff(diffs, run_id, COMPOUND_V3_MARKET, block, comet, "supply_apy", indexed_supply_apy, float(rpc_supply_apy), args.apy_threshold)
        _maybe_diff(diffs, run_id, COMPOUND_V3_MARKET, block, comet, "borrow_apy", indexed_borrow_apy, float(rpc_borrow_apy), args.apy_threshold)
        _anchor_v3_collateral_metadata(ch, rpc, block, run_id, comet, diffs)
    return diffs


def _v3_anchor_rate(utilization: float, kink: float, slope_low: float, slope_high: float, base: float) -> float:
    util = max(0.0, min(float(utilization), 1.0))
    if kink <= 0 or util <= kink:
        return max(0.0, base + slope_low * util)
    return max(0.0, base + slope_low * kink + slope_high * (util - kink))


def _anchor_v3_collateral_metadata(ch, rpc: RpcClient, block: int, run_id: str, comet: str, diffs: list[list[Any]]) -> None:
    expected_rows = ch.query(
        """
        SELECT asset, borrow_collateral_factor, liquidate_collateral_factor, liquidation_factor, supply_cap
        FROM compound_v3_collateral_registry FINAL
        WHERE comet = %(comet)s AND active = 1
        """,
        parameters={"comet": comet},
    ).result_rows
    if not expected_rows:
        return
    expected = {str(row[0]).lower(): row for row in expected_rows}
    try:
        num_assets = int(_call_uint(rpc, comet, "numAssets()", block) or 0)
        rpc_assets = {}
        for index in range(num_assets):
            _offset, asset, _price_feed, _scale, borrow_cf, liquidate_cf, liquidation_factor, supply_cap = _call_v3_asset_info(rpc, comet, index, block)
            rpc_assets[str(asset).lower()] = (borrow_cf, liquidate_cf, liquidation_factor, supply_cap)
    except Exception:
        return
    if set(expected) != set(rpc_assets):
        diffs.append([run_id, COMPOUND_V3_MARKET, block, comet, "collateral_assets", ",".join(sorted(expected)), ",".join(sorted(rpc_assets)), 1.0])
        return
    for asset, row in expected.items():
        _asset, borrow_cf, liquidate_cf, liquidation_factor, supply_cap = row
        rpc_borrow_cf, rpc_liquidate_cf, rpc_liquidation_factor, rpc_supply_cap = rpc_assets[asset]
        _maybe_diff(diffs, run_id, COMPOUND_V3_MARKET, block, f"{comet}:{asset}", "borrow_collateral_factor", float(borrow_cf or 0), float(rpc_borrow_cf) / WAD, 1e-12)
        _maybe_diff(diffs, run_id, COMPOUND_V3_MARKET, block, f"{comet}:{asset}", "liquidate_collateral_factor", float(liquidate_cf or 0), float(rpc_liquidate_cf) / WAD, 1e-12)
        _maybe_diff(diffs, run_id, COMPOUND_V3_MARKET, block, f"{comet}:{asset}", "liquidation_factor", float(liquidation_factor or 0), float(rpc_liquidation_factor) / WAD, 1e-12)
        _maybe_diff(diffs, run_id, COMPOUND_V3_MARKET, block, f"{comet}:{asset}", "supply_cap", float(supply_cap or 0), float(rpc_supply_cap), 0.0)


def _count_checked(ch, protocol: str) -> int:
    total = 0
    if protocol in {"v2", "both"}:
        total += int(ch.command("SELECT count() FROM compound_v2_market_registry FINAL WHERE active = 1") or 0)
    if protocol in {"v3", "both"}:
        total += int(ch.command("SELECT count() FROM compound_v3_comet_registry FINAL WHERE active = 1") or 0)
    return total


def _compound_sources(protocol: str):
    if protocol in {"v2", "both"}:
        yield CompoundV2Source()
    if protocol in {"v3", "both"}:
        yield CompoundV3Source()


async def _collect_window(source, ch, from_block: int, to_block: int, batch_blocks: int) -> int:
    from analytics.collector import BLOCK_FIELDS, LOG_FIELDS, advance_hypersync_cursor, build_block_ts_map, require_hypersync_token

    source.get_cursor(ch)
    client = hypersync.HypersyncClient(hypersync.ClientConfig(url="https://eth.hypersync.xyz", bearer_token=require_hypersync_token()))
    inserted = 0
    current = int(from_block)
    selection = source.log_selection()
    while current <= int(to_block):
        batch_end = min(current + int(batch_blocks) - 1, int(to_block))
        batch_to = batch_end + 1
        cursor = current
        logs = []
        blocks = []
        while cursor < batch_to:
            query = hypersync.Query(
                from_block=cursor,
                to_block=batch_to,
                logs=[selection],
                field_selection=hypersync.FieldSelection(log=LOG_FIELDS, block=BLOCK_FIELDS),
            )
            response = await client.get(query)
            logs.extend(response.data.logs)
            blocks.extend(response.data.blocks)
            cursor = advance_hypersync_cursor(cursor, response.next_block)
        block_ts_map = build_block_ts_map(blocks)
        source_logs = [entry for entry in logs if source.route(entry)]
        inserted += source.insert_raw(ch, source_logs, block_ts_map)
        update_source_status(ch, source.name, "collector", last_scanned_block=batch_end, last_event_block=batch_end, source_head_block=batch_end)
        current = batch_end + 1
    return inserted


def _process_window(source, ch, from_block: int, to_block: int) -> int:
    from analytics.processor import SimulatedLog

    rows = ch.query(
        f"""
        SELECT block_number, block_timestamp, tx_hash, log_index, contract,
               event_name, topic0, topic1, topic2, topic3, data
        FROM {source.raw_table}
        WHERE block_number >= %(from_block)s AND block_number <= %(to_block)s
        ORDER BY block_number ASC, log_index ASC
        """,
        parameters={"from_block": int(from_block), "to_block": int(to_block)},
    ).result_rows
    block_ts_map = {row[0]: row[1] for row in rows}
    decoded = []
    for row in rows:
        item = source.decode(SimulatedLog(row), block_ts_map)
        if item:
            decoded.append(item)
    written = source.merge(ch, decoded) if decoded else 0
    ch.insert("processor_state", [[source.name, int(to_block)]], column_names=["protocol", "last_processed_block"])
    update_source_status(ch, source.name, "processor", last_processed_block=int(to_block), last_event_block=int(to_block))
    return written


def _serving_smoke(ch, protocols: list[str]) -> dict[str, dict[str, int]]:
    result = {}
    for protocol in protocols:
        result[protocol] = {
            "latest": int(ch.command(f"SELECT count() FROM api_market_latest WHERE protocol = '{protocol}'") or 0),
            "series": int(ch.command(f"SELECT count() FROM market_timeseries WHERE protocol = '{protocol}'") or 0),
        }
    return result


def e2e(args: argparse.Namespace, ch) -> int:
    ensure_compound_tables(ch)
    if args.bootstrap:
        bootstrap(args, ch)
    if int(getattr(args, "from_block", 0) or 0) and int(getattr(args, "to_block", 0) or 0):
        collected = {}
        processed = {}
        for source in _compound_sources(args.protocol):
            collected[source.name] = asyncio.run(_collect_window(source, ch, int(args.from_block), int(args.to_block), int(args.batch_blocks)))
            processed[source.name] = _process_window(source, ch, int(args.from_block), int(args.to_block))
        print(json.dumps({"compoundE2eReplay": {"collected": collected, "processed": processed, "serving": _serving_smoke(ch, list(processed))}}, sort_keys=True))
    return anchor(args, ch)
