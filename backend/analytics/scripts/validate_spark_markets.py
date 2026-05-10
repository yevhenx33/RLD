#!/usr/bin/env python3
"""Validate SparkLend indexed market rows against direct Pool RPC reads."""

from __future__ import annotations

import argparse
import os
import sys

import clickhouse_connect
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from analytics.aave_constants import SPARK_V3_POOL  # noqa: E402
from analytics.tokens import TOKENS  # noqa: E402

RAY = 10**27
GET_RESERVE_DATA_SELECTOR = "0x35ea6a75"
SCALED_TOTAL_SUPPLY_SELECTOR = "0xb1bf962d"


def normalize_address(value: str) -> str:
    raw = str(value or "").lower()
    if raw.startswith("0x"):
        raw = raw[2:]
    return "0x" + raw[-40:].rjust(40, "0")


def rpc_call(rpc_url: str, to: str, data: str, block_number: int) -> str:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": to, "data": data}, hex(int(block_number))],
    }
    response = requests.post(rpc_url, json=payload, timeout=30)
    response.raise_for_status()
    body = response.json()
    if body.get("error"):
        raise RuntimeError(body["error"])
    return str(body.get("result") or "0x")


def rpc_uint256(rpc_url: str, to: str, data: str, block_number: int) -> int:
    raw = rpc_call(rpc_url, to, data, block_number)
    return int(str(raw or "0x0"), 16)


def ch_client():
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Spark market reconstruction against Pool.getReserveData")
    parser.add_argument("--rpc-url", default=os.getenv("MAINNET_RPC_URL") or os.getenv("ETH_RPC_URL"))
    parser.add_argument("--apy-threshold", type=float, default=0.001)
    parser.add_argument("--utilization-threshold", type=float, default=0.001)
    parser.add_argument("--notional-threshold", type=float, default=0.005)
    parser.add_argument("--min-notional-usd", type=float, default=1000.0)
    args = parser.parse_args()
    if not args.rpc_url:
        raise RuntimeError("MAINNET_RPC_URL or ETH_RPC_URL is required")

    ch = ch_client()
    block_raw = ch.command("SELECT max(last_processed_block) FROM processor_state WHERE protocol = 'SPARK_MARKET'")
    block_number = int(block_raw) if block_raw not in (None, "", "None") else 0
    if block_number <= 0:
        raise RuntimeError("SPARK_MARKET has no processed block")

    rows = ch.query(
        """
        SELECT entity_id, symbol, supply_usd, borrow_usd, supply_apy, borrow_apy, utilization, price_usd
        FROM api_market_latest FINAL
        WHERE protocol = 'SPARK_MARKET'
        ORDER BY supply_usd DESC
        """
    ).result_rows
    if not rows:
        raise RuntimeError("SPARK_MARKET has no latest market rows")

    failures = []
    checked = 0
    for entity_id, symbol, supply_usd, borrow_usd, supply_apy, borrow_apy, utilization, price_usd in rows:
        reserve = normalize_address(entity_id)
        token = TOKENS.get(reserve[2:])
        if not token:
            continue
        _, decimals = token
        raw = rpc_call(args.rpc_url, SPARK_V3_POOL, GET_RESERVE_DATA_SELECTOR + reserve[2:].rjust(64, "0"), block_number)
        data = raw[2:]
        if len(data) < 12 * 64:
            failures.append(f"{symbol}: short getReserveData response")
            continue

        liquidity_index = int(data[1 * 64:2 * 64], 16)
        variable_borrow_index = int(data[3 * 64:4 * 64], 16)
        rpc_supply_apy = int(data[2 * 64:3 * 64], 16) / RAY
        rpc_borrow_apy = int(data[4 * 64:5 * 64], 16) / RAY
        a_token = normalize_address("0x" + data[8 * 64 + 24:9 * 64])
        variable_debt_token = normalize_address("0x" + data[10 * 64 + 24:11 * 64])
        scaled_supply = rpc_uint256(args.rpc_url, a_token, SCALED_TOTAL_SUPPLY_SELECTOR, block_number)
        scaled_borrow = rpc_uint256(args.rpc_url, variable_debt_token, SCALED_TOTAL_SUPPLY_SELECTOR, block_number)
        total_supply_raw = scaled_supply * liquidity_index / RAY
        total_borrow_raw = scaled_borrow * variable_borrow_index / RAY
        price = float(price_usd or 0.0)
        rpc_supply_usd = total_supply_raw / (10 ** int(decimals)) * price
        rpc_borrow_usd = total_borrow_raw / (10 ** int(decimals)) * price
        local_supply = float(supply_usd or 0.0)
        local_borrow = float(borrow_usd or 0.0)
        local_utilization = float(utilization or 0.0)
        rpc_utilization = float(total_borrow_raw / total_supply_raw) if total_supply_raw else 0.0
        checked += 1

        if abs(float(supply_apy or 0.0) - rpc_supply_apy) > args.apy_threshold:
            failures.append(f"{symbol}: supply APY drift local={float(supply_apy or 0.0):.8f} rpc={rpc_supply_apy:.8f}")
        if abs(float(borrow_apy or 0.0) - rpc_borrow_apy) > args.apy_threshold:
            failures.append(f"{symbol}: borrow APY drift local={float(borrow_apy or 0.0):.8f} rpc={rpc_borrow_apy:.8f}")
        if abs(local_utilization - rpc_utilization) > args.utilization_threshold:
            failures.append(f"{symbol}: utilization drift local={local_utilization:.8f} rpc={rpc_utilization:.8f}")

        for label, local_value, rpc_value in (
            ("supply", local_supply, rpc_supply_usd),
            ("borrow", local_borrow, rpc_borrow_usd),
        ):
            basis = max(abs(rpc_value), abs(local_value))
            if basis < args.min_notional_usd:
                continue
            drift = abs(local_value - rpc_value) / basis if basis else 0.0
            if drift > args.notional_threshold:
                failures.append(f"{symbol}: {label} drift {drift:.4%} local={local_value:.2f} rpc={rpc_value:.2f}")

    print(f"spark_validation_checked={checked} block={block_number} failures={len(failures)}")
    for failure in failures[:50]:
        print(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
