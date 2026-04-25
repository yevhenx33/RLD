#!/usr/bin/env python3
"""Unified CLI for operating the ClickHouse analytics indexer."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

import clickhouse_connect

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from indexer.config import apply_env_from_config, source_poll_interval
from indexer.schema import (
    backfill_serving_tables,
    ensure_schema,
    list_serving_views,
    rebuild_aggregates,
)


def ch_client():
    settings = {}
    if os.getenv("CLICKHOUSE_ASYNC_INSERT", "true").strip().lower() in {"1", "true", "yes"}:
        settings["async_insert"] = 1
        settings["wait_for_async_insert"] = (
            1
            if os.getenv("CLICKHOUSE_WAIT_FOR_ASYNC_INSERT", "true").strip().lower()
            in {"1", "true", "yes"}
            else 0
        )
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "127.0.0.1"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
        settings=settings,
    )


def fetch_json(path: str):
    base = os.getenv("RATES_API_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=5) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def cmd_migrate(args) -> int:
    ch = ch_client()
    try:
        ensure_schema(ch)
        if args.backfill:
            backfill_serving_tables(ch)
        if args.rebuild_views:
            rebuild_aggregates(ch)
    finally:
        ch.close()
    return 0


def cmd_status(args) -> int:
    code, payload = fetch_json("/status")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"HTTP {code} status={payload.get('status')} version={payload.get('version')}")
        for source in payload.get("sourceStatus", []):
            print(
                "{source} {kind} scanned={lastScannedBlock} event={lastEventBlock} "
                "processed={lastProcessedBlock} head={sourceHeadBlock} success={lastSuccessAt}".format(**source)
            )
    return 0 if code == 200 else 1


def cmd_smoke(_args) -> int:
    return subprocess.call([sys.executable, os.path.join(os.path.dirname(__file__), "smoke_clickhouse_indexer.py")])


def cmd_backup(_args) -> int:
    return subprocess.call(["bash", os.path.join(os.path.dirname(__file__), "backup_clickhouse.sh")])


def cmd_worker(args) -> int:
    from run_indexer import SOURCE_MAP, run_worker

    if args.source not in SOURCE_MAP:
        raise SystemExit(f"Unknown source {args.source}. Valid sources: {', '.join(sorted(SOURCE_MAP))}")
    poll_interval = args.poll_interval or source_poll_interval(args.source, default=30)
    asyncio.run(
        run_worker(
            SOURCE_MAP[args.source],
            "worker",
            genesis_override=args.genesis_block,
            poll_interval=poll_interval,
        )
    )
    return 0


def cmd_views(args) -> int:
    ch = ch_client()
    try:
        if args.views_command == "list":
            print(json.dumps(list_serving_views(ch), indent=2))
        elif args.views_command == "rebuild":
            rebuild_aggregates(ch)
            print("serving views rebuilt")
    finally:
        ch.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="RLD ClickHouse indexer operator CLI")
    parser.add_argument("--config", default=None, help="Path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    migrate = sub.add_parser("migrate", help="Apply ClickHouse schema")
    migrate.add_argument("--backfill", action="store_true")
    migrate.add_argument("--rebuild-views", action="store_true")
    migrate.set_defaults(func=cmd_migrate)

    status = sub.add_parser("status", help="Show source status")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    smoke = sub.add_parser("smoke", help="Run smoke checks")
    smoke.set_defaults(func=cmd_smoke)

    backup = sub.add_parser("backup", help="Run ClickHouse backup helper")
    backup.set_defaults(func=cmd_backup)

    worker = sub.add_parser("worker", help="Run a supervised source worker")
    worker.add_argument("--source", required=True)
    worker.add_argument("--genesis-block", type=int, default=None)
    worker.add_argument("--poll-interval", type=int, default=None)
    worker.set_defaults(func=cmd_worker)

    views = sub.add_parser("views", help="Manage serving materialized views")
    views_sub = views.add_subparsers(dest="views_command", required=True)
    views_list = views_sub.add_parser("list")
    views_list.set_defaults(func=cmd_views)
    views_rebuild = views_sub.add_parser("rebuild")
    views_rebuild.set_defaults(func=cmd_views)

    args = parser.parse_args()
    apply_env_from_config(args.config)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
