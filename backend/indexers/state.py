"""Postgres-backed source status helpers for the simulation indexer.

This mirrors the external ClickHouse indexer's source_status contract, but keeps
the simulation runtime self-contained in Postgres.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import asyncpg


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coalesce_int(value: int | None, current: int | None) -> int:
    if value is not None:
        return int(value)
    return int(current or 0)


async def ensure_source_status_table(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_status (
          source               TEXT NOT NULL,
          kind                 TEXT NOT NULL,
          market_id            TEXT,
          market_type          TEXT,
          last_scanned_block   BIGINT NOT NULL DEFAULT 0,
          last_event_block     BIGINT NOT NULL DEFAULT 0,
          last_processed_block BIGINT NOT NULL DEFAULT 0,
          source_head_block    BIGINT NOT NULL DEFAULT 0,
          last_success_at      TIMESTAMPTZ,
          last_error           TEXT NOT NULL DEFAULT '',
          updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY (source, kind)
        )
        """
    )


async def get_source_status(
    conn: asyncpg.Connection,
    source: str,
    kind: str,
) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT source, kind, market_id, market_type,
               last_scanned_block, last_event_block, last_processed_block,
               source_head_block, last_success_at, last_error, updated_at
        FROM source_status
        WHERE source = $1 AND kind = $2
        """,
        source,
        kind,
    )
    if not row:
        return {
            "source": source,
            "kind": kind,
            "market_id": None,
            "market_type": None,
            "last_scanned_block": 0,
            "last_event_block": 0,
            "last_processed_block": 0,
            "source_head_block": 0,
            "last_success_at": None,
            "last_error": "",
            "updated_at": None,
        }
    return dict(row)


async def update_source_status(
    conn: asyncpg.Connection,
    source: str,
    kind: str,
    *,
    market_id: str | None = None,
    market_type: str | None = None,
    last_scanned_block: int | None = None,
    last_event_block: int | None = None,
    last_processed_block: int | None = None,
    source_head_block: int | None = None,
    last_error: str | None = None,
    mark_success: bool = True,
) -> None:
    await ensure_source_status_table(conn)
    current = await get_source_status(conn, source, kind)
    now = utc_now()
    await conn.execute(
        """
        INSERT INTO source_status (
          source, kind, market_id, market_type,
          last_scanned_block, last_event_block, last_processed_block,
          source_head_block, last_success_at, last_error, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        ON CONFLICT (source, kind) DO UPDATE SET
          market_id = COALESCE(EXCLUDED.market_id, source_status.market_id),
          market_type = COALESCE(EXCLUDED.market_type, source_status.market_type),
          last_scanned_block = EXCLUDED.last_scanned_block,
          last_event_block = EXCLUDED.last_event_block,
          last_processed_block = EXCLUDED.last_processed_block,
          source_head_block = EXCLUDED.source_head_block,
          last_success_at = COALESCE(EXCLUDED.last_success_at, source_status.last_success_at),
          last_error = EXCLUDED.last_error,
          updated_at = EXCLUDED.updated_at
        """,
        source,
        kind,
        market_id or current.get("market_id"),
        market_type or current.get("market_type"),
        _coalesce_int(last_scanned_block, current.get("last_scanned_block")),
        _coalesce_int(last_event_block, current.get("last_event_block")),
        _coalesce_int(last_processed_block, current.get("last_processed_block")),
        _coalesce_int(source_head_block, current.get("source_head_block")),
        now if mark_success else current.get("last_success_at"),
        "" if last_error is None else str(last_error),
        now,
    )


async def source_status_snapshot(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    await ensure_source_status_table(conn)
    rows = await conn.fetch(
        """
        SELECT source, kind, market_id, market_type,
               last_scanned_block, last_event_block, last_processed_block,
               source_head_block, last_success_at, last_error, updated_at
        FROM source_status
        ORDER BY market_type NULLS LAST, source, kind
        """
    )
    return [dict(row) for row in rows]
