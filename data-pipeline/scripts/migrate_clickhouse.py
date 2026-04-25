#!/usr/bin/env python3
"""Apply ClickHouse schema for the analytics indexer."""

from __future__ import annotations

import argparse
import os
import sys

import clickhouse_connect

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from indexer.config import apply_env_from_config
from indexer.schema import backfill_serving_tables, ensure_schema, rebuild_aggregates


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply ClickHouse analytics schema")
    parser.add_argument("--backfill", action="store_true", help="Backfill serving tables if empty")
    args = parser.parse_args()
    apply_env_from_config()

    settings = {}
    if os.getenv("CLICKHOUSE_ASYNC_INSERT", "true").strip().lower() in {"1", "true", "yes"}:
        settings["async_insert"] = 1
        settings["wait_for_async_insert"] = (
            1
            if os.getenv("CLICKHOUSE_WAIT_FOR_ASYNC_INSERT", "true").strip().lower()
            in {"1", "true", "yes"}
            else 0
        )

    migration_user = os.getenv("CLICKHOUSE_MIGRATION_USER")
    migration_password = os.getenv("CLICKHOUSE_MIGRATION_PASSWORD")
    if not migration_user or not migration_password:
        migration_user = os.getenv("CLICKHOUSE_USER", "default")
        migration_password = os.getenv("CLICKHOUSE_PASSWORD", "")

    ch = clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=migration_user,
        password=migration_password,
        settings=settings,
    )
    try:
        ensure_schema(ch)
        if args.backfill:
            backfill_serving_tables(ch)
            rebuild_aggregates(ch)
    finally:
        ch.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
