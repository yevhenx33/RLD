"""Shared ClickHouse source status helpers.

This table is the canonical freshness contract for workers and the API.
Legacy cursor tables can remain for backward compatibility, but readiness
should prefer this source-specific status record.
"""

from __future__ import annotations

import datetime as dt
from typing import Any


EPOCH = dt.datetime(1970, 1, 1)


def _as_datetime(value: Any) -> dt.datetime:
    if value is None:
        return EPOCH
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time.min)
    if isinstance(value, str):
        try:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return EPOCH
    return EPOCH


def ensure_source_status_table(ch) -> None:
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS source_status (
            source LowCardinality(String),
            kind LowCardinality(String),
            last_scanned_block UInt64 DEFAULT 0,
            last_event_block UInt64 DEFAULT 0,
            last_processed_block UInt64 DEFAULT 0,
            source_head_block UInt64 DEFAULT 0,
            last_data_timestamp DateTime DEFAULT toDateTime(0),
            last_success_at DateTime DEFAULT now(),
            last_error String DEFAULT '',
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY (source, kind)
        """
    )


def get_source_status(ch, source: str, kind: str) -> dict[str, Any]:
    rows = ch.query(
        """
        SELECT
            last_scanned_block,
            last_event_block,
            last_processed_block,
            source_head_block,
            last_data_timestamp,
            last_success_at,
            last_error
        FROM source_status FINAL
        WHERE source = %(source)s AND kind = %(kind)s
        LIMIT 1
        """,
        parameters={"source": source, "kind": kind},
    ).result_rows
    if not rows:
        return {
            "last_scanned_block": 0,
            "last_event_block": 0,
            "last_processed_block": 0,
            "source_head_block": 0,
            "last_data_timestamp": EPOCH,
            "last_success_at": EPOCH,
            "last_error": "",
        }
    row = rows[0]
    return {
        "last_scanned_block": int(row[0] or 0),
        "last_event_block": int(row[1] or 0),
        "last_processed_block": int(row[2] or 0),
        "source_head_block": int(row[3] or 0),
        "last_data_timestamp": _as_datetime(row[4]),
        "last_success_at": _as_datetime(row[5]),
        "last_error": str(row[6] or ""),
    }


def update_source_status(
    ch,
    source: str,
    kind: str,
    *,
    last_scanned_block: int | None = None,
    last_event_block: int | None = None,
    last_processed_block: int | None = None,
    source_head_block: int | None = None,
    last_data_timestamp: Any | None = None,
    last_error: str | None = None,
) -> None:
    ensure_source_status_table(ch)
    current = get_source_status(ch, source, kind)
    row = [
        source,
        kind,
        int(last_scanned_block if last_scanned_block is not None else current["last_scanned_block"]),
        int(last_event_block if last_event_block is not None else current["last_event_block"]),
        int(last_processed_block if last_processed_block is not None else current["last_processed_block"]),
        int(source_head_block if source_head_block is not None else current["source_head_block"]),
        _as_datetime(last_data_timestamp if last_data_timestamp is not None else current["last_data_timestamp"]),
        dt.datetime.utcnow(),
        "" if last_error is None else str(last_error),
        dt.datetime.utcnow(),
    ]
    ch.insert(
        "source_status",
        [row],
        column_names=[
            "source",
            "kind",
            "last_scanned_block",
            "last_event_block",
            "last_processed_block",
            "source_head_block",
            "last_data_timestamp",
            "last_success_at",
            "last_error",
            "updated_at",
        ],
    )
