"""ClickHouse state tables for Astrid publishers."""

from __future__ import annotations

import datetime as dt
from typing import Any


def ensure_publisher_state_tables(ch) -> None:
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS stream_publisher_state (
            stream_id String,
            last_cursor String,
            last_block UInt64,
            last_timestamp DateTime,
            last_nats_sequence UInt64 DEFAULT 0,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY stream_id
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS stream_publisher_runs (
            run_id String,
            stream_id String,
            mode LowCardinality(String),
            started_at DateTime,
            finished_at DateTime,
            status LowCardinality(String),
            rows_published UInt64,
            messages_published UInt64,
            error String DEFAULT ''
        ) ENGINE = MergeTree()
        ORDER BY (stream_id, started_at, run_id)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS stream_chunk_manifest (
            stream_id String,
            chunk_id String,
            schema_version String,
            from_block UInt64,
            to_block UInt64,
            from_timestamp DateTime,
            to_timestamp DateTime,
            row_count UInt64,
            format LowCardinality(String),
            uri String,
            sha256 String,
            created_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(created_at)
        ORDER BY (stream_id, chunk_id)
        """
    )


def upsert_cursor(
    ch,
    *,
    stream_id: str,
    last_cursor: str,
    last_block: int,
    last_timestamp: dt.datetime,
    last_nats_sequence: int = 0,
) -> None:
    ch.insert(
        "stream_publisher_state",
        [[stream_id, last_cursor, int(last_block), last_timestamp, int(last_nats_sequence), dt.datetime.now(dt.UTC).replace(tzinfo=None)]],
        column_names=[
            "stream_id",
            "last_cursor",
            "last_block",
            "last_timestamp",
            "last_nats_sequence",
            "updated_at",
        ],
    )


def read_cursor(ch, stream_id: str) -> dict[str, Any] | None:
    result = ch.query(
        f"""
        SELECT last_cursor, last_block, last_timestamp, last_nats_sequence
        FROM stream_publisher_state FINAL
        WHERE stream_id = '{stream_id.replace("'", "''")}'
        LIMIT 1
        """
    )
    rows = getattr(result, "result_rows", [])
    if not rows:
        return None
    last_cursor, last_block, last_timestamp, last_nats_sequence = rows[0]
    return {
        "stream_id": stream_id,
        "last_cursor": last_cursor,
        "last_block": last_block,
        "last_timestamp": last_timestamp,
        "last_nats_sequence": last_nats_sequence,
    }
