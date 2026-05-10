#!/usr/bin/env python3
"""Seed SparkLend reserve -> aToken/vToken metadata."""

from __future__ import annotations

import argparse
import os
import sys

import clickhouse_connect
import requests
from eth_abi import decode as abi_decode
from eth_utils import keccak

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from analytics.aave_constants import (  # noqa: E402
    AAVE_TOPIC_RESERVE_INITIALIZED,
    SPARK_V3_POOL,
)
from analytics.tokens import TOKENS  # noqa: E402

SPARK_DEPLOYMENT_ID = "spark-v3-ethereum"
CHAIN_ID = 1
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
RESERVE_COLUMNS = [
    "deployment_id",
    "chain_id",
    "reserve",
    "a_token",
    "stable_debt_token",
    "variable_debt_token",
    "symbol",
    "decimals",
    "active",
    "source",
    "block_number",
]


def insert_rows_batched(ch, table: str, rows: list[list], columns: list[str], batch_size: int = 5000) -> int:
    if not rows:
        return 0
    total = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        ch.insert(table, batch, column_names=columns)
        total += len(batch)
    return total


def normalize_address(value: str) -> str:
    raw = str(value or "").lower()
    if raw.startswith("0x"):
        raw = raw[2:]
    return "0x" + raw[-40:].rjust(40, "0")


def topic_address(value: str) -> str:
    return normalize_address(value)


def data_words(data: str) -> list[int]:
    raw = str(data or "")
    if raw.startswith("0x"):
        raw = raw[2:]
    return [int(raw[i:i + 64], 16) for i in range(0, len(raw), 64) if len(raw[i:i + 64]) == 64]


def selector(signature: str) -> str:
    return "0x" + keccak(text=signature).hex()[:8]


def encode_address_arg(address: str) -> str:
    return normalize_address(address)[2:].rjust(64, "0")


def rpc_call(rpc_url: str, to: str, data: str, block_tag: str = "latest") -> str:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": to, "data": data}, block_tag],
    }
    response = requests.post(rpc_url, json=payload, timeout=30)
    response.raise_for_status()
    body = response.json()
    if body.get("error"):
        raise RuntimeError(body["error"])
    return str(body.get("result") or "0x")


def clickhouse_client_from_env():
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
    )


def ensure_spark_reserve_tokens_table(ch) -> None:
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS spark_reserve_tokens (
            deployment_id String,
            chain_id UInt32,
            reserve String,
            a_token String,
            stable_debt_token String DEFAULT '',
            variable_debt_token String,
            symbol LowCardinality(String),
            decimals UInt8,
            active UInt8 DEFAULT 1,
            source LowCardinality(String) DEFAULT 'UNKNOWN',
            block_number UInt64 DEFAULT 0,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY (deployment_id, reserve)
        """
    )


def reserve_row(reserve: str, a_token: str, stable_debt: str, variable_debt: str, source: str, block_number: int) -> list:
    symbol, decimals = TOKENS.get(reserve[2:], ("", 18))
    return [
        SPARK_DEPLOYMENT_ID,
        CHAIN_ID,
        reserve,
        a_token,
        stable_debt,
        variable_debt,
        symbol,
        int(decimals),
        1,
        source,
        int(block_number or 0),
    ]


def bootstrap_from_events(ch) -> int:
    ensure_spark_reserve_tokens_table(ch)
    rows = ch.query(
        """
        SELECT block_number, topic1, topic2, data
        FROM spark_events
        WHERE topic0 = %(topic)s
        ORDER BY block_number, log_index
        """,
        parameters={"topic": AAVE_TOPIC_RESERVE_INITIALIZED},
    ).result_rows
    out = []
    for block_number, topic1, topic2, data in rows:
        reserve = topic_address(topic1)
        a_token = topic_address(topic2)
        words = data_words(data)
        stable_debt = normalize_address(hex(words[0])) if len(words) > 0 else ZERO_ADDRESS
        variable_debt = normalize_address(hex(words[1])) if len(words) > 1 else ZERO_ADDRESS
        out.append(reserve_row(reserve, a_token, stable_debt, variable_debt, "ReserveInitialized", block_number))
    return insert_rows_batched(ch, "spark_reserve_tokens", out, RESERVE_COLUMNS)


def bootstrap_from_rpc(ch, rpc_url: str, block_tag: str = "latest") -> int:
    ensure_spark_reserve_tokens_table(ch)
    raw = rpc_call(rpc_url, SPARK_V3_POOL, selector("getReservesList()"), block_tag)
    reserves = abi_decode(["address[]"], bytes.fromhex(raw[2:]))[0]
    out = []
    get_reserve_data = selector("getReserveData(address)")
    for reserve_raw in reserves:
        reserve = normalize_address(reserve_raw)
        reserve_data = rpc_call(rpc_url, SPARK_V3_POOL, get_reserve_data + encode_address_arg(reserve), block_tag)
        words = data_words(reserve_data)
        if len(words) < 11:
            continue
        a_token = normalize_address(hex(words[8]))
        stable_debt = normalize_address(hex(words[9]))
        variable_debt = normalize_address(hex(words[10]))
        out.append(reserve_row(reserve, a_token, stable_debt, variable_debt, "Pool.getReserveData", 0))
    return insert_rows_batched(ch, "spark_reserve_tokens", out, RESERVE_COLUMNS)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap Spark reserve token registry")
    parser.add_argument("--rpc-url", default=os.getenv("MAINNET_RPC_URL") or os.getenv("ETH_RPC_URL"))
    parser.add_argument("--block-tag", default="latest")
    parser.add_argument("--events-only", action="store_true")
    args = parser.parse_args()

    ch = clickhouse_client_from_env()
    event_rows = bootstrap_from_events(ch)
    rpc_rows = 0
    if event_rows == 0 and not args.events_only:
        if not args.rpc_url:
            raise RuntimeError("spark_events has no ReserveInitialized logs; pass --rpc-url or MAINNET_RPC_URL")
        rpc_rows = bootstrap_from_rpc(ch, args.rpc_url, args.block_tag)
    print(f"spark_reserve_token_rows_from_events={event_rows} spark_reserve_token_rows_from_rpc={rpc_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
