#!/usr/bin/env python3
"""
Compare simulation indexer state between Postgres and ClickHouse mirror.

Checks:
  - indexer cursor block parity
  - latest block snapshot parity
  - latest candle bucket parity per resolution
  - optional unique event count parity (enable via SIM_PARITY_STRICT_EVENTS=true)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from decimal import Decimal
from typing import Any

import asyncpg
import clickhouse_connect


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes"}


def _as_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default


def _row_to_dict(row: asyncpg.Record | None) -> dict[str, Any]:
    if not row:
        return {}
    return {k: _jsonify(v) for k, v in dict(row).items()}


def _jsonify(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    return value


async def _load_postgres_state(conn: asyncpg.Connection, market_id: str) -> dict[str, Any]:
    cursor_row = await conn.fetchrow(
        """
        SELECT market_id, last_indexed_block, total_events
        FROM indexer_state
        WHERE market_id = $1
        """,
        market_id,
    )
    block_state_row = await conn.fetchrow(
        """
        SELECT market_id, block_number, block_timestamp, index_price, mark_price
        FROM block_states
        WHERE market_id = $1
        ORDER BY block_number DESC
        LIMIT 1
        """,
        market_id,
    )
    candle_rows = await conn.fetch(
        """
        SELECT resolution, MAX(bucket) AS bucket
        FROM candles
        WHERE market_id = $1
        GROUP BY resolution
        """,
        market_id,
    )
    event_count = await conn.fetchval(
        "SELECT COUNT(*) FROM events WHERE market_id = $1",
        market_id,
    )
    return {
        "cursor": _row_to_dict(cursor_row),
        "block_state": _row_to_dict(block_state_row),
        "candles": {str(r["resolution"]): _as_int(r["bucket"]) for r in candle_rows},
        "event_count": _as_int(event_count),
        "event_count_since": {},
    }


def _load_clickhouse_state(client, market_id: str) -> dict[str, Any]:
    cursor_rows = client.query(
        """
        SELECT market_id, last_indexed_block, total_events
        FROM sim_indexer_cursor
        WHERE market_id = %(market_id)s
        ORDER BY synced_at DESC
        LIMIT 1
        """,
        parameters={"market_id": market_id},
    ).result_rows
    block_state_rows = client.query(
        """
        SELECT market_id, block_number, block_timestamp, index_price, mark_price
        FROM sim_block_states
        WHERE market_id = %(market_id)s
        ORDER BY block_number DESC, synced_at DESC
        LIMIT 1
        """,
        parameters={"market_id": market_id},
    ).result_rows
    candle_rows = client.query(
        """
        SELECT resolution, max(bucket) AS bucket
        FROM sim_candles
        WHERE market_id = %(market_id)s
        GROUP BY resolution
        """,
        parameters={"market_id": market_id},
    ).result_rows
    event_count_rows = client.query(
        """
        SELECT uniqExact(tx_hash, log_index)
        FROM sim_events
        WHERE market_id = %(market_id)s
        """,
        parameters={"market_id": market_id},
    ).result_rows
    min_event_block_rows = client.query(
        """
        SELECT min(block_number)
        FROM sim_events
        WHERE market_id = %(market_id)s
        """,
        parameters={"market_id": market_id},
    ).result_rows

    cursor = {}
    if cursor_rows:
        r = cursor_rows[0]
        cursor = {
            "market_id": str(r[0]),
            "last_indexed_block": _as_int(r[1]),
            "total_events": _as_int(r[2]),
        }

    block_state = {}
    if block_state_rows:
        r = block_state_rows[0]
        block_state = {
            "market_id": str(r[0]),
            "block_number": _as_int(r[1]),
            "block_timestamp": _as_int(r[2]),
            "index_price": str(r[3]) if r[3] is not None else None,
            "mark_price": str(r[4]) if r[4] is not None else None,
        }

    return {
        "cursor": cursor,
        "block_state": block_state,
        "candles": {str(r[0]): _as_int(r[1]) for r in candle_rows},
        "event_count": _as_int(event_count_rows[0][0]) if event_count_rows else 0,
        "min_event_block": (
            _as_int(min_event_block_rows[0][0]) if min_event_block_rows and min_event_block_rows[0][0] is not None else None
        ),
    }


async def _add_postgres_window_event_count(
    dsn: str,
    market_id: str,
    from_block: int | None,
) -> int | None:
    if from_block is None:
        return None
    conn = await asyncpg.connect(dsn)
    try:
        value = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM events
            WHERE market_id = $1 AND block_number >= $2
            """,
            market_id,
            from_block,
        )
        return _as_int(value)
    finally:
        await conn.close()


def _compare_states(
    pg: dict[str, Any],
    ch: dict[str, Any],
    event_tolerance: int,
    strict_events: bool,
) -> tuple[bool, dict[str, Any]]:
    checks: dict[str, Any] = {}

    pg_cursor_block = _as_int(pg.get("cursor", {}).get("last_indexed_block"))
    ch_cursor_block = _as_int(ch.get("cursor", {}).get("last_indexed_block"))
    checks["cursor_block_match"] = (pg_cursor_block == ch_cursor_block)

    pg_block = _as_int(pg.get("block_state", {}).get("block_number"))
    ch_block = _as_int(ch.get("block_state", {}).get("block_number"))
    checks["latest_block_state_match"] = (pg_block == ch_block)

    pg_candles = pg.get("candles", {})
    ch_candles = ch.get("candles", {})
    candle_diffs = {}
    for resolution in sorted(set(pg_candles.keys()) | set(ch_candles.keys())):
        candle_diffs[resolution] = {
            "postgres_bucket": _as_int(pg_candles.get(resolution)),
            "clickhouse_bucket": _as_int(ch_candles.get(resolution)),
        }
    checks["candle_bucket_diffs"] = candle_diffs
    checks["candle_buckets_match"] = all(
        v["postgres_bucket"] == v["clickhouse_bucket"] for v in candle_diffs.values()
    )

    pg_events = _as_int(pg.get("event_count"))
    ch_events = _as_int(ch.get("event_count"))
    window_pg_events = pg.get("event_count_since", {}).get("count")
    effective_pg_events = (
        _as_int(window_pg_events, default=pg_events)
        if window_pg_events is not None
        else pg_events
    )
    checks["event_count"] = {
        "postgres_total": pg_events,
        "postgres_effective": effective_pg_events,
        "clickhouse": ch_events,
        "delta": abs(effective_pg_events - ch_events),
        "tolerance": event_tolerance,
        "window_from_block": pg.get("event_count_since", {}).get("from_block"),
    }
    checks["event_count_within_tolerance"] = abs(effective_pg_events - ch_events) <= event_tolerance
    checks["event_check_enforced"] = strict_events

    ok = (
        checks["cursor_block_match"]
        and checks["latest_block_state_match"]
        and checks["candle_buckets_match"]
        and (checks["event_count_within_tolerance"] if strict_events else True)
    )
    return ok, checks


async def main() -> int:
    dsn = os.getenv("DATABASE_URL", "postgresql://rld:rld@localhost:5432/rld_indexer")
    ch_host = os.getenv("SIM_CLICKHOUSE_HOST", "localhost")
    ch_port = int(os.getenv("SIM_CLICKHOUSE_PORT", "8123"))
    ch_db = os.getenv("SIM_CLICKHOUSE_DATABASE", "default")
    market_id = os.getenv("SIM_PARITY_MARKET_ID", "").strip()
    event_tolerance = int(os.getenv("SIM_PARITY_EVENT_DELTA_TOLERANCE", "0"))
    strict_events = _env_bool("SIM_PARITY_STRICT_EVENTS", False)

    conn = await asyncpg.connect(dsn)
    try:
        if not market_id:
            market_id = await conn.fetchval("SELECT market_id FROM markets LIMIT 1")
        if not market_id:
            print(json.dumps({"ok": False, "error": "No market_id found in Postgres"}))
            return 2

        pg_state = await _load_postgres_state(conn, market_id)
    finally:
        await conn.close()

    ch_client = clickhouse_connect.get_client(host=ch_host, port=ch_port, database=ch_db)
    try:
        ch_state = _load_clickhouse_state(ch_client, market_id)
    finally:
        ch_client.close()

    min_event_block = ch_state.get("min_event_block")
    pg_window_events = await _add_postgres_window_event_count(dsn, market_id, min_event_block)
    if min_event_block is not None and pg_window_events is not None:
        pg_state["event_count_since"] = {
            "from_block": min_event_block,
            "count": pg_window_events,
        }

    ok, checks = _compare_states(pg_state, ch_state, event_tolerance, strict_events)
    payload = {
        "ok": ok,
        "market_id": market_id,
        "postgres": pg_state,
        "clickhouse": ch_state,
        "checks": checks,
    }
    print(json.dumps(payload, indent=2))

    if ok:
        return 0
    return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    if _env_bool("SIM_PARITY_STRICT", True):
        sys.exit(exit_code)
    sys.exit(0)
