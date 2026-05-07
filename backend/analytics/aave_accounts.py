"""Event-sourced Aave account reconstruction helpers."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import random
from dataclasses import dataclass
from typing import Optional

import clickhouse_connect
import hypersync
import requests
from eth_abi import decode as abi_decode
from eth_utils import keccak

from analytics.aave_constants import (
    AAVE_V3_DEPLOY_BLOCK,
    AAVE_V3_POOL,
    AAVE_V3_PROTOCOL_DATA_PROVIDER,
)
from analytics.base import BaseSource, insert_rows_batched
from analytics.tokens import TOKENS

log = logging.getLogger("indexer.aave_accounts")

AAVE_ACCOUNTS = "AAVE_ACCOUNTS"
AAVE_DEPLOYMENT_ID = os.getenv("AAVE_DEPLOYMENT_ID", "ethereum:aave-v3-core")
AAVE_CHAIN_ID = int(os.getenv("AAVE_CHAIN_ID", "1"))
RAY = 10**27
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def _topic(signature: str) -> str:
    return "0x" + keccak(text=signature).hex()


TOPIC_RESERVE_INITIALIZED = _topic(
    "ReserveInitialized(address,address,address,address,address)"
)
TOPIC_RESERVE_USED_AS_COLLATERAL_ENABLED = _topic(
    "ReserveUsedAsCollateralEnabled(address,address)"
)
TOPIC_RESERVE_USED_AS_COLLATERAL_DISABLED = _topic(
    "ReserveUsedAsCollateralDisabled(address,address)"
)
TOPIC_USER_EMODE_SET = _topic("UserEModeSet(address,uint8)")
TOPIC_TRANSFER = _topic("Transfer(address,address,uint256)")
TOPIC_BALANCE_TRANSFER = _topic("BalanceTransfer(address,address,uint256,uint256)")
TOPIC_TOKEN_MINT = _topic("Mint(address,address,uint256,uint256,uint256)")
TOPIC_TOKEN_BURN = _topic("Burn(address,address,uint256,uint256,uint256)")

POOL_EVENT_MAP = {
    TOPIC_RESERVE_USED_AS_COLLATERAL_ENABLED: "ReserveUsedAsCollateralEnabled",
    TOPIC_RESERVE_USED_AS_COLLATERAL_DISABLED: "ReserveUsedAsCollateralDisabled",
    TOPIC_USER_EMODE_SET: "UserEModeSet",
    TOPIC_RESERVE_INITIALIZED: "ReserveInitialized",
}
TOKEN_EVENT_MAP = {
    TOPIC_BALANCE_TRANSFER: "BalanceTransfer",
    TOPIC_TRANSFER: "Transfer",
    TOPIC_TOKEN_MINT: "Mint",
    TOPIC_TOKEN_BURN: "Burn",
}

AAVE_ACCOUNT_TOPICS = tuple(
    sorted({*POOL_EVENT_MAP.keys(), TOPIC_BALANCE_TRANSFER, TOPIC_TOKEN_MINT, TOPIC_TOKEN_BURN})
)

RAW_COLUMNS = [
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
]

ACCOUNT_EVENT_COLUMNS = [
    "deployment_id",
    "chain_id",
    "block_number",
    "block_timestamp",
    "tx_hash",
    "log_index",
    "contract",
    "event_name",
    "reserve",
    "token",
    "token_type",
    "user",
    "counterparty",
    "scaled_delta_raw",
    "raw_value",
    "raw_balance_increase",
    "raw_index",
    "collateral_enabled",
    "emode_category",
]
POSITION_PROFILE_COLUMNS = [
    "deployment_id",
    "chain_id",
    "timestamp",
    "user",
    "reserve",
    "symbol",
    "scaled_supply_raw",
    "scaled_variable_debt_raw",
    "supply_usd",
    "debt_usd",
    "collateral_enabled",
    "liquidation_threshold",
    "price_usd",
    "liquidity_index",
    "variable_borrow_index",
    "last_event_block",
]
USER_PROFILE_COLUMNS = [
    "deployment_id",
    "chain_id",
    "timestamp",
    "user",
    "total_collateral_usd",
    "total_debt_usd",
    "net_worth_usd",
    "weighted_liquidation_threshold",
    "health_factor",
    "emode_category",
    "position_count",
    "debt_position_count",
    "collateral_position_count",
    "last_event_block",
]


@dataclass(frozen=True)
class ReserveToken:
    reserve: str
    a_token: str
    variable_debt_token: str
    symbol: str
    decimals: int


def normalize_address(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if raw.startswith("0x"):
        raw = raw[2:]
    return "0x" + raw[-40:].rjust(40, "0")


def topic_address(topic: str | None) -> str:
    return normalize_address(topic)


def data_words(data: str | None) -> list[int]:
    raw = str(data or "")
    if raw.startswith("0x"):
        raw = raw[2:]
    return [int(raw[i : i + 64], 16) for i in range(0, len(raw), 64) if len(raw[i : i + 64]) == 64]


def ray_div_signed(value: int, index: int) -> int:
    if index <= 0:
        return 0
    if value < 0:
        return -ray_div_signed(-value, index)
    return (value * RAY + index // 2) // index


def ensure_aave_account_tables(ch) -> None:
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS aave_reserve_tokens (
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
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS aave_account_raw_events (
            block_number UInt64,
            block_timestamp DateTime,
            tx_hash String,
            log_index UInt32,
            contract String,
            event_name String,
            topic0 String,
            topic1 Nullable(String),
            topic2 Nullable(String),
            topic3 Nullable(String),
            data String,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        ORDER BY (block_number, log_index, tx_hash, contract)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS aave_account_events (
            deployment_id String,
            chain_id UInt32,
            block_number UInt64,
            block_timestamp DateTime,
            tx_hash String,
            log_index UInt32,
            contract String,
            event_name LowCardinality(String),
            reserve String,
            token String,
            token_type LowCardinality(String),
            user String,
            counterparty String,
            scaled_delta_raw Int256,
            raw_value String,
            raw_balance_increase String,
            raw_index String,
            collateral_enabled Int8,
            emode_category UInt16,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(block_timestamp)
        ORDER BY (deployment_id, user, reserve, block_number, log_index, event_name, contract)
        TTL block_timestamp + INTERVAL 72 MONTH DELETE
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS aave_reconstruction_audit_runs (
            run_id String,
            deployment_id String,
            chain_id UInt32,
            block_number UInt64,
            sampled_users UInt32,
            sampled_positions UInt32,
            failed_diffs UInt32,
            status LowCardinality(String),
            started_at DateTime,
            finished_at DateTime,
            details String
        ) ENGINE = ReplacingMergeTree(finished_at)
        ORDER BY (deployment_id, run_id)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS aave_reconstruction_audit_diffs (
            run_id String,
            deployment_id String,
            chain_id UInt32,
            block_number UInt64,
            user String,
            reserve String,
            field LowCardinality(String),
            reconstructed String,
            rpc String,
            abs_diff Float64,
            rel_diff Float64,
            passed UInt8,
            reason String,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = MergeTree()
        ORDER BY (deployment_id, run_id, user, reserve, field)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS aave_account_position_timeseries (
            deployment_id String,
            chain_id UInt32,
            timestamp DateTime,
            user String,
            reserve String,
            symbol LowCardinality(String),
            scaled_supply_raw String,
            scaled_variable_debt_raw String,
            supply_usd Float64,
            debt_usd Float64,
            collateral_enabled UInt8,
            liquidation_threshold Float64,
            price_usd Float64,
            liquidity_index Float64,
            variable_borrow_index Float64,
            last_event_block UInt64,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (deployment_id, user, reserve, timestamp)
        TTL timestamp + INTERVAL 72 MONTH DELETE
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS aave_account_profile_timeseries (
            deployment_id String,
            chain_id UInt32,
            timestamp DateTime,
            user String,
            total_collateral_usd Float64,
            total_debt_usd Float64,
            net_worth_usd Float64,
            weighted_liquidation_threshold Float64,
            health_factor Float64,
            emode_category UInt16,
            position_count UInt16,
            debt_position_count UInt16,
            collateral_position_count UInt16,
            last_event_block UInt64,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (deployment_id, user, timestamp)
        TTL timestamp + INTERVAL 72 MONTH DELETE
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS aave_account_profile_backfill_runs (
            run_id String,
            deployment_id String,
            chain_id UInt32,
            start_timestamp DateTime,
            end_timestamp DateTime,
            profile_rows UInt64,
            position_rows UInt64,
            status LowCardinality(String),
            started_at DateTime,
            finished_at DateTime,
            details String
        ) ENGINE = ReplacingMergeTree(finished_at)
        ORDER BY (deployment_id, run_id)
        """
    )


def load_reserve_tokens(ch, deployment_id: str = AAVE_DEPLOYMENT_ID) -> dict[str, ReserveToken]:
    ensure_aave_account_tables(ch)
    rows = ch.query(
        """
        SELECT reserve, a_token, variable_debt_token, symbol, decimals
        FROM aave_reserve_tokens FINAL
        WHERE deployment_id = %(deployment_id)s AND active = 1
        """,
        parameters={"deployment_id": deployment_id},
    ).result_rows
    return {
        normalize_address(row[0]): ReserveToken(
            reserve=normalize_address(row[0]),
            a_token=normalize_address(row[1]),
            variable_debt_token=normalize_address(row[2]),
            symbol=str(row[3] or ""),
            decimals=int(row[4] or 18),
        )
        for row in rows
    }


def token_contract_registry(ch) -> dict[str, tuple[str, str, ReserveToken]]:
    by_reserve = load_reserve_tokens(ch)
    registry: dict[str, tuple[str, str, ReserveToken]] = {}
    for item in by_reserve.values():
        if item.a_token:
            registry[item.a_token] = ("ATOKEN", item.reserve, item)
        if item.variable_debt_token:
            registry[item.variable_debt_token] = ("VARIABLE_DEBT", item.reserve, item)
    return registry


def bootstrap_reserve_tokens_from_events(ch) -> int:
    """Seed reserve token metadata from ReserveInitialized logs when present."""
    ensure_aave_account_tables(ch)
    rows = ch.query(
        """
        SELECT block_number, block_timestamp, topic1, topic2, data
        FROM (
            SELECT block_number, block_timestamp, topic1, topic2, data, log_index
            FROM aave_events
            WHERE topic0 = %(topic)s
            UNION ALL
            SELECT block_number, block_timestamp, topic1, topic2, data, log_index
            FROM aave_account_raw_events
            WHERE topic0 = %(topic)s
        )
        ORDER BY block_number, log_index
        """,
        parameters={"topic": TOPIC_RESERVE_INITIALIZED},
    ).result_rows
    out = []
    for block_number, _ts, topic1, topic2, data in rows:
        reserve = topic_address(topic1)
        a_token = topic_address(topic2)
        words = data_words(data)
        stable_debt = normalize_address(hex(words[0])) if len(words) > 0 else ""
        variable_debt = normalize_address(hex(words[1])) if len(words) > 1 else ""
        token_meta = TOKENS.get(reserve[2:], ("", 18))
        out.append(
            [
                AAVE_DEPLOYMENT_ID,
                AAVE_CHAIN_ID,
                reserve,
                a_token,
                stable_debt,
                variable_debt,
                token_meta[0],
                int(token_meta[1]),
                1,
                "ReserveInitialized",
                int(block_number or 0),
            ]
        )
    return insert_rows_batched(
        ch,
        "aave_reserve_tokens",
        out,
        [
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
        ],
    )


def decoded_account_rows(log_entry, block_ts_map: dict, token_registry: dict[str, tuple[str, str, ReserveToken]]) -> list[dict]:
    topics = log_entry.topics or []
    if not topics:
        return []
    topic0 = str(topics[0]).lower()
    contract = normalize_address(getattr(log_entry, "address", ""))
    ts = block_ts_map.get(log_entry.block_number, dt.datetime.now(dt.UTC))
    if getattr(ts, "tzinfo", None) is not None:
        ts = ts.replace(tzinfo=None)
    base = {
        "deployment_id": AAVE_DEPLOYMENT_ID,
        "chain_id": AAVE_CHAIN_ID,
        "block_number": int(log_entry.block_number or 0),
        "block_timestamp": ts,
        "tx_hash": getattr(log_entry, "transaction_hash", "") or "",
        "log_index": int(getattr(log_entry, "log_index", 0) or 0),
        "contract": contract,
        "token": "",
        "token_type": "",
        "scaled_delta_raw": 0,
        "raw_value": "0",
        "raw_balance_increase": "0",
        "raw_index": "0",
        "collateral_enabled": -1,
        "emode_category": 0,
        "counterparty": "",
    }

    if contract == normalize_address(AAVE_V3_POOL):
        if topic0 == TOPIC_RESERVE_USED_AS_COLLATERAL_ENABLED and len(topics) >= 3:
            return [
                {
                    **base,
                    "event_name": "ReserveUsedAsCollateralEnabled",
                    "reserve": topic_address(topics[1]),
                    "user": topic_address(topics[2]),
                    "collateral_enabled": 1,
                }
            ]
        if topic0 == TOPIC_RESERVE_USED_AS_COLLATERAL_DISABLED and len(topics) >= 3:
            return [
                {
                    **base,
                    "event_name": "ReserveUsedAsCollateralDisabled",
                    "reserve": topic_address(topics[1]),
                    "user": topic_address(topics[2]),
                    "collateral_enabled": 0,
                }
            ]
        if topic0 == TOPIC_USER_EMODE_SET and len(topics) >= 2:
            words = data_words(log_entry.data)
            return [
                {
                    **base,
                    "event_name": "UserEModeSet",
                    "reserve": "",
                    "user": topic_address(topics[1]),
                    "emode_category": int(words[0] if words else 0),
                }
            ]
        return []

    token_meta = token_registry.get(contract)
    if not token_meta:
        return []
    token_type, reserve, _reserve_token = token_meta
    words = data_words(log_entry.data)
    common = {**base, "reserve": reserve, "token": contract, "token_type": token_type}

    if topic0 == TOPIC_TOKEN_MINT and len(topics) >= 3 and len(words) >= 3:
        value, balance_increase, index = words[0], words[1], words[2]
        return [
            {
                **common,
                "event_name": "Mint",
                "user": topic_address(topics[2]),
                "counterparty": topic_address(topics[1]),
                "scaled_delta_raw": ray_div_signed(value - balance_increase, index),
                "raw_value": str(value),
                "raw_balance_increase": str(balance_increase),
                "raw_index": str(index),
            }
        ]
    if topic0 == TOPIC_TOKEN_BURN and len(topics) >= 3 and len(words) >= 3:
        value, balance_increase, index = words[0], words[1], words[2]
        return [
            {
                **common,
                "event_name": "Burn",
                "user": topic_address(topics[1]),
                "counterparty": topic_address(topics[2]),
                "scaled_delta_raw": -ray_div_signed(value + balance_increase, index),
                "raw_value": str(value),
                "raw_balance_increase": str(balance_increase),
                "raw_index": str(index),
            }
        ]
    if topic0 == TOPIC_BALANCE_TRANSFER and token_type == "ATOKEN" and len(topics) >= 3 and len(words) >= 2:
        from_user = topic_address(topics[1])
        to_user = topic_address(topics[2])
        if from_user == ZERO_ADDRESS or to_user == ZERO_ADDRESS:
            return []
        scaled = int(words[0])
        index = int(words[1])
        return [
            {
                **common,
                "event_name": "BalanceTransfer",
                "user": from_user,
                "counterparty": to_user,
                "scaled_delta_raw": -scaled,
                "raw_value": str(words[0]),
                "raw_index": str(index),
            },
            {
                **common,
                "event_name": "BalanceTransfer",
                "user": to_user,
                "counterparty": from_user,
                "scaled_delta_raw": scaled,
                "raw_value": str(words[0]),
                "raw_index": str(index),
            },
        ]
    # ERC20 Transfer is not sufficient for exact scaled-account reconstruction.
    # aToken BalanceTransfer carries the scaled value and index; Mint/Burn handle
    # zero-address ERC20 transfers.
    if topic0 == TOPIC_TRANSFER:
        return []
    return []


class AaveAccountSource(BaseSource):
    name = AAVE_ACCOUNTS
    raw_table = "aave_account_raw_events"
    genesis_block = AAVE_V3_DEPLOY_BLOCK
    contracts = [AAVE_V3_POOL]
    topics = list(AAVE_ACCOUNT_TOPICS)

    def __init__(self):
        self._token_registry: dict[str, tuple[str, str, ReserveToken]] = {}

    def get_cursor(self, ch) -> int:
        ensure_aave_account_tables(ch)
        if not self._token_registry:
            bootstrap_reserve_tokens_from_events(ch)
            self._token_registry = token_contract_registry(ch)
        self.contracts = [AAVE_V3_POOL, *sorted(self._token_registry.keys())]
        value = ch.command(f"SELECT max(block_number) FROM {self.raw_table}")
        return int(value) if value not in (None, "", "None") else 0

    def _event_name(self, log_entry) -> str:
        topics = log_entry.topics or []
        if not topics:
            return ""
        topic0 = str(topics[0]).lower()
        return POOL_EVENT_MAP.get(topic0) or TOKEN_EVENT_MAP.get(topic0) or ""

    def decode(self, log_entry, block_ts_map) -> Optional[dict]:
        rows = decoded_account_rows(log_entry, block_ts_map, self._token_registry)
        return {"rows": rows} if rows else None

    def merge(self, ch, decoded_rows: list[dict]) -> int:
        rows: list[list] = []
        for item in decoded_rows:
            for row in item.get("rows", []):
                if not row.get("user"):
                    continue
                rows.append([row.get(column) for column in ACCOUNT_EVENT_COLUMNS])
        return insert_rows_batched(ch, "aave_account_events", rows, ACCOUNT_EVENT_COLUMNS)


def _rpc_call(rpc_url: str, to: str, data: str, block_tag: str = "latest") -> str:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": to, "data": data}, block_tag],
    }
    res = requests.post(rpc_url, json=payload, timeout=30)
    res.raise_for_status()
    body = res.json()
    if body.get("error"):
        raise RuntimeError(body["error"])
    return str(body.get("result") or "0x")


def _selector(signature: str) -> str:
    return "0x" + keccak(text=signature).hex()[:8]


def _encode_address_arg(address: str) -> str:
    return normalize_address(address)[2:].rjust(64, "0")


def bootstrap_reserve_tokens_from_rpc(ch, rpc_url: str, block_tag: str = "latest") -> int:
    """One-time metadata fallback using Aave ProtocolDataProvider."""
    ensure_aave_account_tables(ch)
    raw = _rpc_call(
        rpc_url,
        AAVE_V3_PROTOCOL_DATA_PROVIDER,
        _selector("getAllReservesTokens()"),
        block_tag,
    )
    reserves = abi_decode(["(string,address)[]"], bytes.fromhex(raw[2:]))[0]
    rows = []
    for symbol, reserve in reserves:
        reserve = normalize_address(reserve)
        data = _selector("getReserveTokensAddresses(address)") + _encode_address_arg(reserve)
        token_raw = _rpc_call(rpc_url, AAVE_V3_PROTOCOL_DATA_PROVIDER, data, block_tag)
        a_token, stable_debt, variable_debt = abi_decode(
            ["address", "address", "address"],
            bytes.fromhex(token_raw[2:]),
        )
        decimals = TOKENS.get(reserve[2:], (str(symbol), 18))[1]
        rows.append(
            [
                AAVE_DEPLOYMENT_ID,
                AAVE_CHAIN_ID,
                reserve,
                normalize_address(a_token),
                normalize_address(stable_debt),
                normalize_address(variable_debt),
                str(symbol),
                int(decimals),
                1,
                "ProtocolDataProvider",
                0,
            ]
        )
    return insert_rows_batched(
        ch,
        "aave_reserve_tokens",
        rows,
        [
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
        ],
    )


def clickhouse_client_from_env():
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
    )


def deterministic_audit_users(ch, limit: int = 600) -> list[str]:
    rows = ch.query(
        """
        SELECT user
        FROM (
            SELECT user,
                   sumIf(abs(toFloat64(scaled_delta_raw)), token_type = 'VARIABLE_DEBT') AS debt_signal,
                   max(block_number) AS last_block
            FROM aave_account_events
            WHERE user != ''
            GROUP BY user
        )
        ORDER BY debt_signal DESC, last_block DESC, user ASC
        LIMIT %(limit)s
        """,
        parameters={"limit": int(limit)},
    ).result_rows
    users = [str(row[0]) for row in rows]
    if len(users) <= limit:
        return users
    rng = random.Random(17)
    return users[:400] + rng.sample(users[400:], max(0, limit - 400))


def _to_hour(value) -> dt.datetime:
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is not None:
        value = value.replace(tzinfo=None)
    return value.replace(minute=0, second=0, microsecond=0)


def _parse_reserve_index_data(data: str | None) -> tuple[float, float]:
    words = data_words(data)
    if len(words) < 5:
        return float(RAY), float(RAY)
    return float(words[3]), float(words[4])


def _load_profile_context(ch, start_ts: dt.datetime, end_ts: dt.datetime):
    """Load hourly reserve index/price/risk context for profile reconstruction."""
    reserve_meta = load_reserve_tokens(ch)
    base_context: dict[str, dict] = {}
    for reserve, meta in reserve_meta.items():
        base_context[reserve] = {
            "symbol": meta.symbol,
            "decimals": meta.decimals,
            "price_usd": 0.0,
            "liquidation_threshold": 0.0,
            "e_mode_category": 0,
            "e_mode_liquidation_threshold": 0.0,
            "liquidity_index": float(RAY),
            "variable_borrow_index": float(RAY),
        }

    index_seed = ch.query(
        """
        SELECT topic1, argMax(data, tuple(block_number, log_index))
        FROM aave_events
        WHERE event_name = 'ReserveDataUpdated' AND block_timestamp < %(start_ts)s
        GROUP BY topic1
        """,
        parameters={"start_ts": start_ts},
    ).result_rows
    for topic1, data in index_seed:
        reserve = topic_address(topic1)
        if reserve in base_context:
            li, vbi = _parse_reserve_index_data(str(data))
            base_context[reserve]["liquidity_index"] = li
            base_context[reserve]["variable_borrow_index"] = vbi

    price_seed = ch.query(
        """
        SELECT entity_id,
               argMax(price_usd, timestamp),
               argMax(liquidation_threshold, timestamp),
               argMax(e_mode_category, timestamp),
               argMax(e_mode_liquidation_threshold, timestamp)
        FROM market_timeseries
        WHERE protocol = 'AAVE_MARKET' AND timestamp < %(start_ts)s
        GROUP BY entity_id
        """,
        parameters={"start_ts": start_ts},
    ).result_rows
    for reserve, price, threshold, emode, emode_threshold in price_seed:
        reserve = normalize_address(reserve)
        if reserve in base_context:
            base_context[reserve]["price_usd"] = float(price or 0.0)
            base_context[reserve]["liquidation_threshold"] = float(threshold or 0.0)
            base_context[reserve]["e_mode_category"] = int(emode or 0)
            base_context[reserve]["e_mode_liquidation_threshold"] = float(emode_threshold or 0.0)

    context_updates: dict[dt.datetime, dict[str, dict]] = {}
    index_rows = ch.query(
        """
        SELECT toStartOfHour(block_timestamp) AS bucket, topic1,
               argMax(data, tuple(block_number, log_index))
        FROM aave_events
        WHERE event_name = 'ReserveDataUpdated'
          AND block_timestamp >= %(start_ts)s
          AND block_timestamp <= %(end_ts)s
        GROUP BY bucket, topic1
        ORDER BY bucket, topic1
        """,
        parameters={"start_ts": start_ts, "end_ts": end_ts},
    ).result_rows
    for bucket, topic1, data in index_rows:
        reserve = topic_address(topic1)
        li, vbi = _parse_reserve_index_data(str(data))
        context_updates.setdefault(_to_hour(bucket), {}).setdefault(reserve, {})
        context_updates[_to_hour(bucket)][reserve].update(
            {"liquidity_index": li, "variable_borrow_index": vbi}
        )

    price_rows = ch.query(
        """
        SELECT timestamp, entity_id,
               argMax(price_usd, inserted_at),
               argMax(liquidation_threshold, inserted_at),
               argMax(e_mode_category, inserted_at),
               argMax(e_mode_liquidation_threshold, inserted_at)
        FROM market_timeseries
        WHERE protocol = 'AAVE_MARKET'
          AND timestamp >= %(start_ts)s
          AND timestamp <= %(end_ts)s
        GROUP BY timestamp, entity_id
        ORDER BY timestamp, entity_id
        """,
        parameters={"start_ts": start_ts, "end_ts": end_ts},
    ).result_rows
    for bucket, reserve, price, threshold, emode, emode_threshold in price_rows:
        reserve = normalize_address(reserve)
        context_updates.setdefault(_to_hour(bucket), {}).setdefault(reserve, {})
        context_updates[_to_hour(bucket)][reserve].update(
            {
                "price_usd": float(price or 0.0),
                "liquidation_threshold": float(threshold or 0.0),
                "e_mode_category": int(emode or 0),
                "e_mode_liquidation_threshold": float(emode_threshold or 0.0),
            }
        )
    return base_context, context_updates


def rebuild_historical_account_profiles(
    ch,
    *,
    start_ts: dt.datetime | None = None,
    end_ts: dt.datetime | None = None,
    full_snapshot_every_hours: int = 0,
    insert_batch_size: int = 10000,
    run_id: str | None = None,
) -> dict[str, object]:
    """Materialize historical Aave user profiles from decoded account events.

    By default this emits changed-user hourly profiles. Set
    full_snapshot_every_hours to a positive value to also emit all active users
    on that interval, which is heavier but captures index/price-only movement.
    """
    import uuid

    ensure_aave_account_tables(ch)
    bounds = ch.query(
        """
        SELECT min(block_timestamp), max(block_timestamp)
        FROM aave_account_events
        WHERE deployment_id = %(deployment_id)s
        """,
        parameters={"deployment_id": AAVE_DEPLOYMENT_ID},
    ).result_rows
    if not bounds or bounds[0][0] is None:
        return {"status": "NO_ACCOUNT_EVENTS", "profile_rows": 0, "position_rows": 0}
    start_ts = _to_hour(start_ts or bounds[0][0])
    end_ts = _to_hour(end_ts or bounds[0][1])
    run_id = run_id or str(uuid.uuid4())
    started = dt.datetime.now(dt.UTC).replace(tzinfo=None)

    base_context, context_updates = _load_profile_context(ch, start_ts, end_ts)
    context = {reserve: dict(values) for reserve, values in base_context.items()}
    positions: dict[tuple[str, str], dict[str, object]] = {}
    emode_by_user: dict[str, int] = {}
    touched: set[str] = set()
    profile_rows: list[list] = []
    position_rows: list[list] = []
    profile_count = 0
    position_count = 0
    last_snapshot_hour: dt.datetime | None = None

    def apply_context(bucket: dt.datetime) -> None:
        for reserve, update in context_updates.get(bucket, {}).items():
            slot = context.setdefault(reserve, dict(base_context.get(reserve, {})))
            slot.update(update)

    def emit_user(user: str, bucket: dt.datetime) -> None:
        nonlocal profile_count, position_count
        total_collateral = 0.0
        total_debt = 0.0
        liquidation_value = 0.0
        user_position_rows: list[list] = []
        debt_positions = 0
        collateral_positions = 0
        last_event_block = 0
        for (pos_user, reserve), state in positions.items():
            if pos_user != user:
                continue
            scaled_supply = int(state.get("scaled_supply", 0) or 0)
            scaled_debt = int(state.get("scaled_debt", 0) or 0)
            if scaled_supply == 0 and scaled_debt == 0:
                continue
            ctx = context.get(reserve, base_context.get(reserve, {}))
            decimals = int(ctx.get("decimals") or 18)
            price = float(ctx.get("price_usd") or 0.0)
            li = float(ctx.get("liquidity_index") or RAY)
            vbi = float(ctx.get("variable_borrow_index") or RAY)
            supply_usd = max(0.0, scaled_supply * li / 1e27 / (10**decimals) * price)
            debt_usd = max(0.0, scaled_debt * vbi / 1e27 / (10**decimals) * price)
            collateral_enabled = bool(state.get("collateral_enabled", True))
            user_emode = int(emode_by_user.get(user, 0) or 0)
            threshold = float(ctx.get("liquidation_threshold") or 0.0)
            if (
                user_emode > 0
                and user_emode == int(ctx.get("e_mode_category") or 0)
                and float(ctx.get("e_mode_liquidation_threshold") or 0.0) > 0
            ):
                threshold = float(ctx.get("e_mode_liquidation_threshold") or 0.0)
            collateral_usd = supply_usd if collateral_enabled else 0.0
            total_collateral += collateral_usd
            total_debt += debt_usd
            liquidation_value += collateral_usd * threshold
            if debt_usd > 1e-9:
                debt_positions += 1
            if collateral_usd > 1e-9:
                collateral_positions += 1
            last_event_block = max(last_event_block, int(state.get("last_event_block", 0) or 0))
            user_position_rows.append(
                [
                    AAVE_DEPLOYMENT_ID,
                    AAVE_CHAIN_ID,
                    bucket,
                    user,
                    reserve,
                    str(ctx.get("symbol") or ""),
                    str(scaled_supply),
                    str(scaled_debt),
                    supply_usd,
                    debt_usd,
                    1 if collateral_enabled else 0,
                    threshold,
                    price,
                    li,
                    vbi,
                    last_event_block,
                ]
            )
        if not user_position_rows:
            return
        weighted_lt = liquidation_value / total_collateral if total_collateral > 0 else 0.0
        hf = liquidation_value / total_debt if total_debt > 1e-9 else 0.0
        profile_rows.append(
            [
                AAVE_DEPLOYMENT_ID,
                AAVE_CHAIN_ID,
                bucket,
                user,
                total_collateral,
                total_debt,
                total_collateral - total_debt,
                weighted_lt,
                hf,
                int(emode_by_user.get(user, 0) or 0),
                len(user_position_rows),
                debt_positions,
                collateral_positions,
                last_event_block,
            ]
        )
        position_rows.extend(user_position_rows)
        if len(profile_rows) >= insert_batch_size:
            insert_rows_batched(ch, "aave_account_profile_timeseries", profile_rows, USER_PROFILE_COLUMNS)
            insert_rows_batched(ch, "aave_account_position_timeseries", position_rows, POSITION_PROFILE_COLUMNS)
            profile_count += len(profile_rows)
            position_count += len(position_rows)
            profile_rows.clear()
            position_rows.clear()

    event_rows = ch.query(
        """
        SELECT block_number, block_timestamp, log_index, event_name, reserve, token_type,
               user, scaled_delta_raw, collateral_enabled, emode_category
        FROM aave_account_events
        WHERE deployment_id = %(deployment_id)s
          AND block_timestamp >= %(start_ts)s
          AND block_timestamp <= %(end_ts)s
        ORDER BY block_number, log_index, event_name, user, reserve
        """,
        parameters={"deployment_id": AAVE_DEPLOYMENT_ID, "start_ts": start_ts, "end_ts": end_ts},
    ).result_rows

    current_bucket: dt.datetime | None = None
    for block_number, block_ts, _log_index, event_name, reserve, token_type, user, delta, collateral_enabled, emode in event_rows:
        bucket = _to_hour(block_ts)
        if current_bucket is None:
            current_bucket = bucket
            apply_context(bucket)
        if bucket != current_bucket:
            for touched_user in sorted(touched):
                emit_user(touched_user, current_bucket)
            if full_snapshot_every_hours > 0 and (
                last_snapshot_hour is None
                or (current_bucket - last_snapshot_hour).total_seconds() >= full_snapshot_every_hours * 3600
            ):
                for active_user in sorted({key[0] for key, state in positions.items() if int(state.get("scaled_supply", 0) or 0) != 0 or int(state.get("scaled_debt", 0) or 0) != 0}):
                    emit_user(active_user, current_bucket)
                last_snapshot_hour = current_bucket
            touched.clear()
            current_bucket = bucket
            apply_context(bucket)
        user = str(user)
        reserve = normalize_address(reserve) if reserve else ""
        if str(event_name) == "UserEModeSet":
            emode_by_user[user] = int(emode or 0)
            touched.add(user)
            continue
        if str(event_name) in {"ReserveUsedAsCollateralEnabled", "ReserveUsedAsCollateralDisabled"}:
            state = positions.setdefault((user, reserve), {"scaled_supply": 0, "scaled_debt": 0, "collateral_enabled": True, "last_event_block": 0})
            state["collateral_enabled"] = int(collateral_enabled or 0) == 1
            state["last_event_block"] = int(block_number or 0)
            touched.add(user)
            continue
        if reserve:
            state = positions.setdefault((user, reserve), {"scaled_supply": 0, "scaled_debt": 0, "collateral_enabled": True, "last_event_block": 0})
            if str(token_type) == "ATOKEN":
                state["scaled_supply"] = int(state.get("scaled_supply", 0) or 0) + int(delta or 0)
            elif str(token_type) == "VARIABLE_DEBT":
                state["scaled_debt"] = int(state.get("scaled_debt", 0) or 0) + int(delta or 0)
            state["last_event_block"] = int(block_number or 0)
            touched.add(user)

    if current_bucket is not None:
        for touched_user in sorted(touched):
            emit_user(touched_user, current_bucket)
    if profile_rows:
        insert_rows_batched(ch, "aave_account_profile_timeseries", profile_rows, USER_PROFILE_COLUMNS)
        insert_rows_batched(ch, "aave_account_position_timeseries", position_rows, POSITION_PROFILE_COLUMNS)
        profile_count += len(profile_rows)
        position_count += len(position_rows)

    finished = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    ch.insert(
        "aave_account_profile_backfill_runs",
        [[
            run_id,
            AAVE_DEPLOYMENT_ID,
            AAVE_CHAIN_ID,
            start_ts,
            end_ts,
            profile_count,
            position_count,
            "DONE",
            started,
            finished,
            json.dumps({"full_snapshot_every_hours": full_snapshot_every_hours}),
        ]],
        column_names=[
            "run_id",
            "deployment_id",
            "chain_id",
            "start_timestamp",
            "end_timestamp",
            "profile_rows",
            "position_rows",
            "status",
            "started_at",
            "finished_at",
            "details",
        ],
    )
    return {
        "status": "DONE",
        "run_id": run_id,
        "profile_rows": profile_count,
        "position_rows": position_count,
        "start_timestamp": start_ts,
        "end_timestamp": end_ts,
    }
