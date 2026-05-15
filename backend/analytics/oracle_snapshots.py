"""Shared on-chain oracle snapshot support for analytics sources.

The table is intentionally generic: Morpho and Fluid can both store historical
non-event oracle/share-rate values with the exact contract/method provenance
needed to prove how a USD row was priced.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class OracleSnapshot:
    chain_id: int
    source: str
    oracle_type: str
    subject: str
    oracle: str
    method: str
    block_number: int
    timestamp: dt.datetime
    value_raw: str
    value_scale: str
    price_usd: float
    status: str
    error: str = ""


def normalize_address(value: str | None) -> str:
    if not value:
        return ""
    value = str(value).lower()
    return value if value.startswith("0x") else "0x" + value


def ensure_oracle_snapshot_tables(ch) -> None:
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS oracle_snapshots (
            chain_id UInt32,
            source LowCardinality(String),
            oracle_type LowCardinality(String),
            subject String,
            oracle String,
            method String,
            block_number UInt64,
            timestamp DateTime,
            value_raw String,
            value_scale String,
            price_usd Float64,
            status LowCardinality(String),
            error String,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (chain_id, source, subject, oracle_type, timestamp, block_number)
        TTL timestamp + INTERVAL 72 MONTH DELETE
        """
    )


def insert_oracle_snapshots(ch, snapshots: list[OracleSnapshot]) -> int:
    if not snapshots:
        return 0
    rows = [
        [
            s.chain_id,
            s.source,
            s.oracle_type,
            normalize_address(s.subject),
            normalize_address(s.oracle),
            s.method,
            s.block_number,
            s.timestamp,
            s.value_raw,
            s.value_scale,
            s.price_usd,
            s.status,
            s.error,
        ]
        for s in snapshots
    ]
    ch.insert(
        "oracle_snapshots",
        rows,
        column_names=[
            "chain_id",
            "source",
            "oracle_type",
            "subject",
            "oracle",
            "method",
            "block_number",
            "timestamp",
            "value_raw",
            "value_scale",
            "price_usd",
            "status",
            "error",
        ],
    )
    return len(rows)
