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

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from analytics.config import apply_env_from_config, source_poll_interval
from analytics.schema import (
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
    from analytics.scripts.run_indexer import SOURCE_MAP, run_worker

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


def cmd_aave_account_events_backfill(args) -> int:
    from analytics.scripts.backfill_aave_account_events import main as run_account_events_backfill

    argv = ["backfill_aave_account_events"]
    if args.rpc_url:
        argv.extend(["--rpc-url", args.rpc_url])
    argv.extend(["--from-block", str(args.from_block), "--to-block", str(args.to_block)])
    argv.extend(["--batch-blocks", str(args.batch_blocks)])
    if args.skip_collect:
        argv.append("--skip-collect")
    if args.skip_process:
        argv.append("--skip-process")
    old_argv = sys.argv
    try:
        sys.argv = argv
        return run_account_events_backfill()
    finally:
        sys.argv = old_argv


def cmd_aave_account_profiles_backfill(args) -> int:
    from analytics.scripts.backfill_aave_account_profiles import main as run_account_profiles_backfill

    argv = ["backfill_aave_account_profiles"]
    if args.start:
        argv.extend(["--start", args.start])
    if args.end:
        argv.extend(["--end", args.end])
    argv.extend(["--full-snapshot-every-hours", str(args.full_snapshot_every_hours)])
    argv.extend(["--insert-batch-size", str(args.insert_batch_size)])
    old_argv = sys.argv
    try:
        sys.argv = argv
        return run_account_profiles_backfill()
    finally:
        sys.argv = old_argv


def cmd_morpho_oracle_backfill(args) -> int:
    from analytics.scripts.backfill_morpho_oracle_snapshots import run as run_morpho_oracle_backfill

    return run_morpho_oracle_backfill(args)


def cmd_morpho_repair(args) -> int:
    from analytics.scripts.repair_morpho_events import run as run_morpho_repair

    return run_morpho_repair(args)


def cmd_morpho_anchor(args) -> int:
    from analytics.scripts.repair_morpho_events import run as run_morpho_anchor

    args.anchor_once = True
    return run_morpho_anchor(args)


def cmd_metamorpho_backfill(args) -> int:
    from analytics.scripts.backfill_metamorpho import run as run_metamorpho_backfill

    return run_metamorpho_backfill(args)


def cmd_fluid_product_backfill(args) -> int:
    from analytics.scripts.backfill_fluid_product_snapshots import run as run_fluid_product_backfill

    return run_fluid_product_backfill(args)


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

    aave_account_events = sub.add_parser("aave-account-events-backfill", help="Backfill Aave account token/config events")
    aave_account_events.add_argument("--rpc-url", default=None)
    aave_account_events.add_argument("--from-block", type=int, default=16291127)
    aave_account_events.add_argument("--to-block", type=int, default=0)
    aave_account_events.add_argument("--batch-blocks", type=int, default=50000)
    aave_account_events.add_argument("--skip-collect", action="store_true")
    aave_account_events.add_argument("--skip-process", action="store_true")
    aave_account_events.set_defaults(func=cmd_aave_account_events_backfill)

    aave_account_profiles = sub.add_parser("aave-account-profiles-backfill", help="Build historical Aave user profile timeseries")
    aave_account_profiles.add_argument("--start", default=None)
    aave_account_profiles.add_argument("--end", default=None)
    aave_account_profiles.add_argument("--full-snapshot-every-hours", type=int, default=0)
    aave_account_profiles.add_argument("--insert-batch-size", type=int, default=10000)
    aave_account_profiles.set_defaults(func=cmd_aave_account_profiles_backfill)

    morpho_oracle = sub.add_parser("morpho-oracle-backfill", help="Backfill Morpho oracle.price() snapshots")
    morpho_oracle.add_argument("--rpc-url", default=None)
    morpho_oracle.add_argument("--start", default=None)
    morpho_oracle.add_argument("--end", default=None)
    morpho_oracle.add_argument("--min-supply-usd", type=float, default=100_000.0)
    morpho_oracle.add_argument("--min-borrow-usd", type=float, default=1.0)
    morpho_oracle.add_argument("--max-oracles", type=int, default=None)
    morpho_oracle.add_argument("--limit-hours", type=int, default=None)
    morpho_oracle.add_argument("--rpc-batch-size", type=int, default=100)
    morpho_oracle.add_argument("--http-timeout-sec", type=int, default=120)
    morpho_oracle.add_argument("--retries", type=int, default=2)
    morpho_oracle.add_argument("--progress-every", type=int, default=25)
    morpho_oracle.add_argument("--skip-existing", action=argparse.BooleanOptionalAction, default=True)
    morpho_oracle.add_argument("--dry-run", action="store_true")
    morpho_oracle.set_defaults(func=cmd_morpho_oracle_backfill)

    morpho_repair = sub.add_parser("morpho-repair", help="Backfill missing Morpho Blue raw logs and optionally rebuild latest state")
    morpho_repair.add_argument("--rpc-url", default=None)
    morpho_repair.add_argument("--from-block", type=int, required=True)
    morpho_repair.add_argument("--to-block", type=int, required=True)
    morpho_repair.add_argument("--batch-blocks", type=int, default=500)
    morpho_repair.add_argument("--skip-repair", action="store_true")
    morpho_repair.add_argument("--rebuild-state", action="store_true")
    morpho_repair.add_argument("--sync-rpc-state", action="store_true")
    morpho_repair.add_argument("--replay-batch-blocks", type=int, default=50000)
    morpho_repair.add_argument("--max-markets", type=int, default=0)
    morpho_repair.add_argument("--sleep-sec", type=float, default=0.0)
    morpho_repair.add_argument("--dry-run", action="store_true")
    morpho_repair.set_defaults(func=cmd_morpho_repair)

    morpho_anchor = sub.add_parser("morpho-anchor", help="Run one RPC anchor audit against Morpho Blue market storage")
    morpho_anchor.add_argument("--rpc-url", default=None)
    morpho_anchor.add_argument("--block-number", type=int, default=0)
    morpho_anchor.add_argument("--block-mode", choices=["processed", "latest"], default="processed")
    morpho_anchor.add_argument("--confirmations", type=int, default=12)
    morpho_anchor.add_argument("--gap-audit-blocks", type=int, default=7200)
    morpho_anchor.add_argument("--gap-batch-blocks", type=int, default=500)
    morpho_anchor.add_argument("--raw-tolerance", type=int, default=0)
    morpho_anchor.add_argument("--max-markets", type=int, default=0)
    morpho_anchor.add_argument("--max-diff-rows", type=int, default=5000)
    morpho_anchor.add_argument("--fail-on-drift", action="store_true")
    morpho_anchor.add_argument("--dry-run", action="store_true")
    morpho_anchor.set_defaults(func=cmd_morpho_anchor)

    metamorpho = sub.add_parser("metamorpho-backfill", help="Backfill and snapshot MetaMorpho vault state, events, and allocations")
    metamorpho.add_argument("--rpc-url", default=None)
    metamorpho.add_argument("--block-number", type=int, default=None)
    metamorpho.add_argument("--factory-addresses", default=None)
    metamorpho.add_argument("--factory-start-block", type=int, default=None)
    metamorpho.add_argument("--events-start-block", type=int, default=None)
    metamorpho.add_argument("--ignore-existing-cursor", action="store_true")
    metamorpho.add_argument("--max-vaults", type=int, default=None)
    metamorpho.add_argument("--max-queue-items", type=int, default=256)
    metamorpho.add_argument("--log-chunk-size", type=int, default=25000)
    metamorpho.add_argument("--progress-every-ranges", type=int, default=10)
    metamorpho.add_argument("--http-timeout-sec", type=int, default=60)
    metamorpho.add_argument("--retries", type=int, default=2)
    metamorpho.add_argument("--skip-factory-discovery", action="store_true")
    metamorpho.add_argument("--skip-events", action="store_true")
    metamorpho.add_argument("--skip-replay", action="store_true")
    metamorpho.add_argument("--skip-rpc-snapshot", action="store_true")
    metamorpho.add_argument("--dry-run", action="store_true")
    metamorpho.set_defaults(func=cmd_metamorpho_backfill)

    fluid_products = sub.add_parser("fluid-products-backfill", help="Backfill Fluid product discovery and latest snapshots")
    fluid_products.add_argument("--rpc-url", default=None)
    fluid_products.add_argument("--block-number", type=int, default=None)
    fluid_products.add_argument("--max-contracts", type=int, default=None)
    fluid_products.add_argument("--http-timeout-sec", type=int, default=60)
    fluid_products.add_argument("--retries", type=int, default=2)
    fluid_products.add_argument("--dry-run", action="store_true")
    fluid_products.add_argument("--skip-oracles", action="store_true")
    fluid_products.add_argument("--skip-validation", action="store_true")
    fluid_products.set_defaults(func=cmd_fluid_product_backfill)

    fluid_full = sub.add_parser("fluid-full-coverage-cycle", help="Run Fluid oracle snapshots, product snapshots, and validation")
    fluid_full.add_argument("--rpc-url", default=None)
    fluid_full.add_argument("--block-number", type=int, default=None)
    fluid_full.add_argument("--max-contracts", type=int, default=None)
    fluid_full.add_argument("--http-timeout-sec", type=int, default=60)
    fluid_full.add_argument("--retries", type=int, default=2)
    fluid_full.add_argument("--dry-run", action="store_true")
    fluid_full.add_argument("--skip-oracles", action="store_true")
    fluid_full.add_argument("--skip-validation", action="store_true")
    fluid_full.set_defaults(func=cmd_fluid_product_backfill)

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
