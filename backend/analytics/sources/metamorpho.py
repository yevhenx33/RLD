"""MetaMorpho vault event source.

Historical vault logs are collected through HyperSync and replayed into decoded
vault event facts plus hourly flow aggregates. RPC snapshots remain a separate
checkpoint/reconciliation path.
"""

from __future__ import annotations

import datetime
import logging
import os
from collections import defaultdict
from typing import Optional

from ..base import BaseSource, insert_rows_batched
from ..protocols import METAMORPHO_FACTORY, METAMORPHO_VAULT, METAMORPHO_VAULT_BACKFILL
from ..scripts.backfill_metamorpho import (
    DEFAULT_START_BLOCK,
    DEFAULT_FACTORY_ADDRESSES,
    EVENT_TOPICS,
    TOPICS,
    TOPIC_TO_EVENT,
    abi_decode,
    _decode_vault_event,
    _feed_prices_by_hour,
    _price_at,
    _token_meta,
    _topic_address,
    normalize_address,
)

log = logging.getLogger("indexer.metamorpho")


def _factory_addresses_from_env() -> list[str]:
    raw = os.getenv("METAMORPHO_FACTORY_ADDRESSES") or ",".join(DEFAULT_FACTORY_ADDRESSES)
    return [normalize_address(item.strip()) for item in raw.split(",") if item.strip()]


class MetaMorphoFactorySource(BaseSource):
    name = METAMORPHO_FACTORY
    contracts: list[str] = []
    topics = [TOPICS["CreateMetaMorpho"]]
    raw_table = "metamorpho_factory_raw_events"
    genesis_block = DEFAULT_START_BLOCK

    def __init__(self) -> None:
        self.contracts = _factory_addresses_from_env()

    def _ensure_tables(self, ch) -> None:
        ch.command(
            """
            CREATE TABLE IF NOT EXISTS metamorpho_factory_raw_events (
                block_number UInt64,
                block_timestamp DateTime,
                tx_hash String,
                log_index UInt32,
                contract String,
                event_name LowCardinality(String),
                topic0 String,
                topic1 Nullable(String),
                topic2 Nullable(String),
                topic3 Nullable(String),
                data String
            ) ENGINE = ReplacingMergeTree()
            PARTITION BY toStartOfMonth(block_timestamp)
            ORDER BY (block_number, tx_hash, log_index)
            """
        )
        ch.command(
            """
            CREATE TABLE IF NOT EXISTS metamorpho_vault_registry (
                vault_address String,
                name String,
                asset_symbol LowCardinality(String),
                asset_address String,
                owner String DEFAULT '',
                curator String DEFAULT '',
                guardian String DEFAULT '',
                allocator String DEFAULT '',
                fee_wad String DEFAULT '0',
                fee_recipient String DEFAULT '',
                timelock UInt64 DEFAULT 0,
                source LowCardinality(String) DEFAULT 'seed',
                active UInt8 DEFAULT 1,
                updated_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(updated_at)
            ORDER BY vault_address
            """
        )
        ch.command(
            """
            CREATE TABLE IF NOT EXISTS metamorpho_vault_events (
                block_number UInt64,
                timestamp DateTime,
                tx_hash String,
                log_index UInt32,
                vault_address String,
                event_name LowCardinality(String),
                caller String,
                owner String,
                receiver String,
                market_id String,
                assets String,
                shares String,
                raw_data String,
                inserted_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(inserted_at)
            PARTITION BY toStartOfMonth(timestamp)
            ORDER BY (vault_address, block_number, tx_hash, log_index, event_name)
            TTL timestamp + INTERVAL 36 MONTH DELETE
            """
        )

    def get_cursor(self, ch) -> int:
        self._ensure_tables(ch)
        result = ch.command(f"SELECT max(block_number) FROM {self.raw_table}")
        return int(result) if result else 0

    def _event_name(self, log_entry) -> str:
        topics = log_entry.topics or []
        return "CreateMetaMorpho" if topics and str(topics[0]).lower() == TOPICS["CreateMetaMorpho"].lower() else "Unknown"

    def decode(self, log_entry, block_ts_map: dict) -> Optional[dict]:
        topics = [str(topic) for topic in (log_entry.topics or [])]
        if not topics or topics[0].lower() != TOPICS["CreateMetaMorpho"].lower():
            return None
        ts = block_ts_map.get(log_entry.block_number, datetime.datetime.now(datetime.UTC).replace(tzinfo=None))
        if getattr(ts, "tzinfo", None):
            ts = ts.replace(tzinfo=None)
        data = log_entry.data or "0x"
        vault = _topic_address(topics, 1)
        if not vault:
            return None
        caller = _topic_address(topics, 2)
        asset = _topic_address(topics, 3)
        initial_owner = ""
        initial_timelock = 0
        name = ""
        symbol = ""
        try:
            initial_owner, initial_timelock, name, symbol, _salt = abi_decode(
                ["address", "uint256", "string", "string", "bytes32"],
                bytes.fromhex(str(data).removeprefix("0x")),
            )
            initial_owner = normalize_address(initial_owner)
        except Exception:
            pass
        asset_symbol, _asset_decimals = _token_meta(asset)
        return {
            "registry": [
                vault,
                name or symbol or vault[:10],
                asset_symbol,
                asset,
                initial_owner,
                "",
                "",
                "",
                "0",
                "",
                int(initial_timelock or 0),
                "factory",
                1,
            ],
            "event": [
                int(log_entry.block_number or 0),
                ts,
                log_entry.transaction_hash or "",
                int(log_entry.log_index or 0),
                vault,
                "CreateMetaMorpho",
                caller,
                initial_owner,
                "",
                "",
                "0",
                "0",
                data,
            ],
        }

    def merge(self, ch, decoded_rows: list[dict]) -> int:
        registry_rows = [row["registry"] for row in decoded_rows if row.get("registry")]
        event_rows = [row["event"] for row in decoded_rows if row.get("event")]
        if registry_rows:
            insert_rows_batched(
                ch,
                "metamorpho_vault_registry",
                registry_rows,
                [
                    "vault_address", "name", "asset_symbol", "asset_address", "owner", "curator",
                    "guardian", "allocator", "fee_wad", "fee_recipient", "timelock", "source", "active",
                ],
            )
        if event_rows:
            insert_rows_batched(
                ch,
                "metamorpho_vault_events",
                event_rows,
                [
                    "block_number", "timestamp", "tx_hash", "log_index", "vault_address",
                    "event_name", "caller", "owner", "receiver", "market_id", "assets", "shares", "raw_data",
                ],
            )
        return len(registry_rows) + len(event_rows)


class MetaMorphoSource(BaseSource):
    name = METAMORPHO_VAULT
    contracts: list[str] = []
    topics = EVENT_TOPICS
    raw_table = "metamorpho_vault_raw_events"
    genesis_block = DEFAULT_START_BLOCK

    def __init__(self) -> None:
        self.contracts = []

    def _ensure_tables(self, ch) -> None:
        ch.command(
            f"""
            CREATE TABLE IF NOT EXISTS {self.raw_table} (
                block_number UInt64,
                block_timestamp DateTime,
                tx_hash String,
                log_index UInt32,
                contract String,
                event_name LowCardinality(String),
                topic0 String,
                topic1 Nullable(String),
                topic2 Nullable(String),
                topic3 Nullable(String),
                data String
            ) ENGINE = ReplacingMergeTree()
            PARTITION BY toStartOfMonth(block_timestamp)
            ORDER BY (block_number, tx_hash, log_index)
            """
        )
        # Main schema owns decoded event/state tables. The source creates only
        # lightweight safety tables so isolated workers can bootstrap cleanly.
        ch.command(
            """
            CREATE TABLE IF NOT EXISTS metamorpho_vault_events (
                block_number UInt64,
                timestamp DateTime,
                tx_hash String,
                log_index UInt32,
                vault_address String,
                event_name LowCardinality(String),
                caller String,
                owner String,
                receiver String,
                market_id String,
                assets String,
                shares String,
                raw_data String,
                inserted_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(inserted_at)
            PARTITION BY toStartOfMonth(timestamp)
            ORDER BY (vault_address, block_number, tx_hash, log_index, event_name)
            TTL timestamp + INTERVAL 36 MONTH DELETE
            """
        )
        ch.command(
            """
            CREATE TABLE IF NOT EXISTS metamorpho_vault_flows_hourly (
                timestamp DateTime,
                vault_address String,
                asset_symbol LowCardinality(String),
                deposit_assets String,
                withdraw_assets String,
                deposit_shares String,
                withdraw_shares String,
                transfer_shares String,
                deposit_usd Float64,
                withdraw_usd Float64,
                net_flow_usd Float64,
                event_count UInt64,
                inserted_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(inserted_at)
            PARTITION BY toStartOfMonth(timestamp)
            ORDER BY (vault_address, timestamp)
            TTL timestamp + INTERVAL 36 MONTH DELETE
            """
        )

    def _load_contracts(self, ch) -> None:
        rows = ch.query(
            """
            SELECT vault_address
            FROM metamorpho_vault_registry
            GROUP BY vault_address
            ORDER BY vault_address
            """
        ).result_rows
        contracts = [normalize_address(str(row[0])) for row in rows if row and row[0]]
        self.contracts = sorted({addr for addr in contracts if addr and addr != "0x0000000000000000000000000000000000000000"})

    def get_cursor(self, ch) -> int:
        self._ensure_tables(ch)
        self._load_contracts(ch)
        if not self.contracts:
            log.warning("[%s] No MetaMorpho vaults in registry; collector will be idle", self.name)
            return 0
        result = ch.command(f"SELECT max(block_number) FROM {self.raw_table}")
        return int(result) if result else 0

    def route(self, log_entry) -> bool:
        addr = normalize_address(log_entry.address or "")
        return addr in {contract.lower() for contract in self.contracts}

    def _event_name(self, log_entry) -> str:
        topics = log_entry.topics or []
        return TOPIC_TO_EVENT.get(str(topics[0]).lower(), "Unknown") if topics else "Unknown"

    def decode(self, log_entry, block_ts_map: dict) -> Optional[dict]:
        ts = block_ts_map.get(log_entry.block_number, datetime.datetime.now(datetime.UTC).replace(tzinfo=None))
        if getattr(ts, "tzinfo", None):
            ts = ts.replace(tzinfo=None)
        item = {
            "blockNumber": hex(int(log_entry.block_number or 0)),
            "transactionHash": log_entry.transaction_hash or "",
            "logIndex": hex(int(log_entry.log_index or 0)),
            "address": log_entry.address or "",
            "topics": log_entry.topics or [],
            "data": log_entry.data or "0x",
        }
        decoded = _decode_vault_event(item, ts)
        if not decoded:
            return None
        return {"event": decoded}

    def _registry_assets(self, ch, vaults: set[str]) -> dict[str, tuple[str, str]]:
        if not vaults:
            return {}
        escaped = ", ".join("'" + vault.replace("'", "''") + "'" for vault in sorted(vaults))
        rows = ch.query(
            f"""
            SELECT vault_address,
                   argMax(asset_symbol, updated_at) AS asset_symbol,
                   argMax(asset_address, updated_at) AS asset_address
            FROM metamorpho_vault_registry
            WHERE vault_address IN ({escaped})
            GROUP BY vault_address
            """
        ).result_rows
        return {str(row[0]).lower(): (str(row[1] or ""), str(row[2] or "")) for row in rows}

    def _write_batch_flows(self, ch, events: list[list]) -> int:
        flow_events = [event for event in events if event[5] in {"Deposit", "Withdraw", "Transfer"}]
        if not flow_events:
            return 0
        timestamps = [event[1] for event in flow_events]
        min_ts = min(timestamps).replace(minute=0, second=0, microsecond=0)
        max_ts = max(timestamps).replace(minute=0, second=0, microsecond=0)
        price_hours = _feed_prices_by_hour(ch, min_ts, max_ts)
        registry = self._registry_assets(ch, {str(event[4]).lower() for event in flow_events})
        aggregates: dict[tuple[datetime.datetime, str], dict[str, object]] = {}
        for event in flow_events:
            ts = event[1].replace(minute=0, second=0, microsecond=0)
            vault = str(event[4]).lower()
            event_name = str(event[5])
            asset_symbol, asset_address = registry.get(vault, ("", ""))
            _, decimals = _token_meta(asset_address or "0x0000000000000000000000000000000000000000")
            key = (ts, vault)
            slot = aggregates.setdefault(key, {
                "timestamp": ts,
                "vault_address": vault,
                "asset_symbol": asset_symbol,
                "deposit_assets": 0,
                "withdraw_assets": 0,
                "deposit_shares": 0,
                "withdraw_shares": 0,
                "transfer_shares": 0,
                "deposit_usd": 0.0,
                "withdraw_usd": 0.0,
                "event_count": 0,
            })
            assets = int(event[10] or 0)
            shares = int(event[11] or 0)
            price = _price_at(price_hours, ts, asset_symbol)
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
        rows = []
        for slot in aggregates.values():
            rows.append([
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
        if rows:
            insert_rows_batched(
                ch,
                "metamorpho_vault_flows_hourly",
                rows,
                [
                    "timestamp", "vault_address", "asset_symbol", "deposit_assets", "withdraw_assets",
                    "deposit_shares", "withdraw_shares", "transfer_shares", "deposit_usd", "withdraw_usd",
                    "net_flow_usd", "event_count",
                ],
            )
        return len(rows)

    def merge(self, ch, decoded_rows: list[dict]) -> int:
        events = [row["event"] for row in decoded_rows if row.get("event")]
        if events:
            insert_rows_batched(
                ch,
                "metamorpho_vault_events",
                events,
                [
                    "block_number", "timestamp", "tx_hash", "log_index", "vault_address",
                    "event_name", "caller", "owner", "receiver", "market_id", "assets", "shares", "raw_data",
                ],
            )
        flow_rows = self._write_batch_flows(ch, events)
        return len(events) + int(flow_rows or 0)


class MetaMorphoVaultBackfillSource(MetaMorphoSource):
    name = METAMORPHO_VAULT_BACKFILL
    raw_table = "metamorpho_vault_backfill_raw_events"

    def _load_contracts(self, ch) -> None:
        rows = ch.query(
            """
            SELECT vault_address
            FROM metamorpho_vault_registry FINAL
            WHERE vault_address NOT IN (
                SELECT vault_address
                FROM metamorpho_vault_events FINAL
                WHERE event_name != 'CreateMetaMorpho'
                GROUP BY vault_address
            )
            GROUP BY vault_address
            ORDER BY vault_address
            """
        ).result_rows
        contracts = [normalize_address(str(row[0])) for row in rows if row and row[0]]
        self.contracts = sorted({addr for addr in contracts if addr and addr != "0x0000000000000000000000000000000000000000"})
