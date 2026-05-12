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
    from analytics.schema import backfill_serving_tables, ensure_schema, rebuild_aggregates

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


def cmd_fluid_repair(args) -> int:
    from analytics.scripts.repair_fluid_events import run_repair

    ch = ch_client()
    try:
        return run_repair(args, ch)
    finally:
        ch.close()


def cmd_fluid_validate_rpc(args) -> int:
    from analytics.scripts.repair_fluid_events import run_validate

    ch = ch_client()
    try:
        return run_validate(args, ch)
    finally:
        ch.close()


def cmd_euler_refresh_verified(args) -> int:
    from analytics.scripts.euler_ops import refresh_verified

    return refresh_verified(args)


def cmd_euler_replay(args) -> int:
    from analytics.scripts.euler_ops import replay

    return replay(args)


def cmd_euler_anchor(args) -> int:
    from analytics.scripts.euler_ops import anchor

    return anchor(args)


def cmd_compound_bootstrap(args) -> int:
    from analytics.scripts.compound_ops import bootstrap

    ch = ch_client()
    try:
        return bootstrap(args, ch)
    finally:
        ch.close()


def cmd_compound_anchor(args) -> int:
    from analytics.scripts.compound_ops import anchor

    ch = ch_client()
    try:
        return anchor(args, ch)
    finally:
        ch.close()


def cmd_compound_e2e(args) -> int:
    from analytics.scripts.compound_ops import e2e

    ch = ch_client()
    try:
        return e2e(args, ch)
    finally:
        ch.close()




def cmd_views(args) -> int:
    from analytics.schema import list_serving_views, rebuild_aggregates

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


def _stream_by_id(stream_id: str):
    from analytics.streams.registry import load_registry

    streams = load_registry()
    for stream in streams:
        if stream.id == stream_id:
            return stream
    raise SystemExit(f"Unknown Astrid stream {stream_id}. Valid streams: {', '.join(s.id for s in streams)}")


def cmd_streams(args) -> int:
    from analytics.streams.publisher import apply_streams, load_chunk_sidecars, manifest_with_chunks
    from analytics.streams.registry import load_registry, registry_manifest

    streams = load_registry()
    if args.streams_command == "check":
        payload = {"status": "OK", "streams": [stream.to_manifest() for stream in streams]}
        print(json.dumps(payload, indent=2 if args.json else None, sort_keys=True))
        return 0
    if args.streams_command == "manifest":
        payload = (
            manifest_with_chunks(streams, load_chunk_sidecars(args.chunks_dir))
            if args.chunks_dir
            else registry_manifest(streams)
        )
        rendered = json.dumps(payload, indent=2, sort_keys=True)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(rendered + "\n")
        else:
            print(rendered)
        return 0
    if args.streams_command == "apply":
        result = asyncio.run(apply_streams(os.getenv("ASTRID_NATS_URL", "nats://127.0.0.1:4222"), streams))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    raise SystemExit(f"Unknown streams command {args.streams_command}")


def cmd_publisher(args) -> int:
    from analytics.streams.publisher import export_jsonl_chunk, publish_once

    if args.publisher_command == "status":
        ch = ch_client()
        try:
            rows = ch.query(
                """
                SELECT stream_id, last_cursor, last_block, last_timestamp, last_nats_sequence, updated_at
                FROM stream_publisher_state FINAL
                ORDER BY stream_id
                """
            ).result_rows
            payload = [
                {
                    "streamId": row[0],
                    "lastCursor": row[1],
                    "lastBlock": row[2],
                    "lastTimestamp": str(row[3]),
                    "lastNatsSequence": row[4],
                    "updatedAt": str(row[5]),
                }
                for row in rows
            ]
        finally:
            ch.close()
        print(json.dumps(payload, indent=2 if args.json else None, sort_keys=True))
        return 0

    stream = _stream_by_id(args.stream)
    if args.publisher_command == "export-chunk":
        ch = ch_client()
        try:
            result = export_jsonl_chunk(
                ch,
                stream,
                args.out_dir,
                base_uri=args.base_uri,
                from_value=args.from_value,
                limit=args.limit,
                processor_version=os.getenv("INDEXER_VERSION", "dev"),
            )
        finally:
            ch.close()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    ch = ch_client()
    try:
        result = asyncio.run(
            publish_once(
                ch,
                os.getenv("ASTRID_NATS_URL", "nats://127.0.0.1:4222"),
                stream,
                from_value=getattr(args, "from_value", None),
                limit=args.limit,
                processor_version=os.getenv("INDEXER_VERSION", "dev"),
            )
        )
    finally:
        ch.close()
    print(json.dumps(result, indent=2, sort_keys=True))
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
    morpho_repair.add_argument("--rebuild-history", action="store_true")
    morpho_repair.add_argument("--sync-rpc-state", action="store_true")
    morpho_repair.add_argument("--rewind-after-block", type=int, default=0)
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

    fluid_repair = sub.add_parser("fluid-repair", help="Repair missing Fluid raw logs from Ethereum RPC")
    fluid_repair.add_argument("--rpc-url", default=None)
    fluid_repair.add_argument("--from-block", type=int, required=True)
    fluid_repair.add_argument("--to-block", type=int, default=0, help="Inclusive block; defaults to confirmed RPC head")
    fluid_repair.add_argument("--batch-blocks", type=int, default=200)
    fluid_repair.add_argument("--http-timeout-sec", type=int, default=60)
    fluid_repair.add_argument("--retries", type=int, default=2)
    fluid_repair.add_argument("--dry-run", action="store_true")
    fluid_repair.add_argument("--replay", action="store_true", help="Replay Fluid state and serving rows after inserting missing raw logs")
    fluid_repair.add_argument("--force-replay", action="store_true", help="Run replay even if no missing logs were found")
    fluid_repair.add_argument("--replay-from-block", type=int, default=0)
    fluid_repair.add_argument("--replay-batch-blocks", type=int, default=50000)
    fluid_repair.set_defaults(func=cmd_fluid_repair)

    fluid_validate = sub.add_parser("fluid-validate-rpc", help="Validate Fluid raw logs and reserve state against Ethereum RPC")
    fluid_validate.add_argument("--rpc-url", default=None)
    fluid_validate.add_argument("--from-block", type=int, default=0)
    fluid_validate.add_argument("--to-block", type=int, default=0, help="Inclusive block; defaults to confirmed RPC head")
    fluid_validate.add_argument("--recent-blocks", type=int, default=500)
    fluid_validate.add_argument("--http-timeout-sec", type=int, default=60)
    fluid_validate.add_argument("--batch-blocks", type=int, default=200)
    fluid_validate.add_argument("--retries", type=int, default=2)
    fluid_validate.add_argument("--fail-on-drift", action="store_true")
    fluid_validate.set_defaults(func=cmd_fluid_validate_rpc)

    euler_refresh = sub.add_parser("euler-refresh-verified", help="Refresh Euler governedPerspective verified vault registry")
    euler_refresh.add_argument("--rpc-url", default=None)
    euler_refresh.add_argument("--block-number", type=int, default=0, help="Optional fixed Ethereum block tag")
    euler_refresh.add_argument("--max-vaults", type=int, default=0)
    euler_refresh.add_argument("--http-timeout-sec", type=int, default=60)
    euler_refresh.add_argument("--retries", type=int, default=2)
    euler_refresh.add_argument("--progress-every", type=int, default=25)
    euler_refresh.add_argument("--dry-run", action="store_true")
    euler_refresh.set_defaults(func=cmd_euler_refresh_verified)

    euler_replay = sub.add_parser("euler-replay", help="Replay Euler EVault logs for a bounded Ethereum mainnet block range")
    euler_replay.add_argument("--rpc-url", default=None)
    euler_replay.add_argument("--from-block", type=int, required=True)
    euler_replay.add_argument("--to-block", type=int, default=0, help="Inclusive block; defaults to confirmed RPC head")
    euler_replay.add_argument("--confirmations", type=int, default=12)
    euler_replay.add_argument("--batch-blocks", type=int, default=500)
    euler_replay.add_argument("--address-batch-size", type=int, default=100)
    euler_replay.add_argument("--http-timeout-sec", type=int, default=60)
    euler_replay.add_argument("--retries", type=int, default=2)
    euler_replay.add_argument("--verified-only", action=argparse.BooleanOptionalAction, default=True)
    euler_replay.add_argument("--state-only", action="store_true", help="Replay only state/config events needed for anchoring")
    euler_replay.add_argument("--discover-factory", action="store_true", help="Also scan EVaultCreated by topic over this range")
    euler_replay.add_argument("--process", action="store_true", help="Decode and merge this replay range immediately")
    euler_replay.add_argument("--run-processor", action="store_true", help="Run the normal processor loop after inserting logs")
    euler_replay.add_argument("--progress-every", action="store_true")
    euler_replay.add_argument("--dry-run", action="store_true")
    euler_replay.set_defaults(func=cmd_euler_replay)

    euler_anchor = sub.add_parser("euler-anchor", help="Compare latest indexed Euler state against live Ethereum RPC calls")
    euler_anchor.add_argument("--rpc-url", default=None)
    euler_anchor.add_argument("--block-number", type=int, default=0)
    euler_anchor.add_argument("--confirmations", type=int, default=12)
    euler_anchor.add_argument("--max-vaults", type=int, default=50)
    euler_anchor.add_argument("--max-diff-rows", type=int, default=500)
    euler_anchor.add_argument("--usd-tolerance", type=float, default=1.0)
    euler_anchor.add_argument("--apy-tolerance", type=float, default=1e-6)
    euler_anchor.add_argument("--http-timeout-sec", type=int, default=60)
    euler_anchor.add_argument("--retries", type=int, default=2)
    euler_anchor.add_argument("--fail-on-drift", action="store_true")
    euler_anchor.add_argument("--dry-run", action="store_true")
    euler_anchor.set_defaults(func=cmd_euler_anchor)

    compound_bootstrap = sub.add_parser("compound-bootstrap", help="Seed Compound v2/v3 registries and initial state from Ethereum RPC")
    compound_bootstrap.add_argument("--rpc-url", default=None)
    compound_bootstrap.add_argument("--protocol", choices=["v2", "v3", "both"], default="both")
    compound_bootstrap.add_argument("--anchor-block", type=int, default=0, help="Block for initial state; defaults to confirmed RPC head")
    compound_bootstrap.add_argument("--confirmations", type=int, default=12)
    compound_bootstrap.add_argument("--http-timeout-sec", type=int, default=60)
    compound_bootstrap.add_argument("--retries", type=int, default=2)
    compound_bootstrap.add_argument("--set-cursors", action="store_true", help="Start collectors/processors after the seeded block")
    compound_bootstrap.set_defaults(func=cmd_compound_bootstrap)

    compound_anchor = sub.add_parser("compound-anchor", help="Compare indexed Compound state against direct Ethereum RPC calls")
    compound_anchor.add_argument("--rpc-url", default=None)
    compound_anchor.add_argument("--protocol", choices=["v2", "v3", "both"], default="both")
    compound_anchor.add_argument("--block-number", type=int, default=0)
    compound_anchor.add_argument("--block-mode", choices=["processed", "latest"], default="processed")
    compound_anchor.add_argument("--confirmations", type=int, default=12)
    compound_anchor.add_argument("--notional-threshold", type=float, default=0.001)
    compound_anchor.add_argument("--apy-threshold", type=float, default=1e-6)
    compound_anchor.add_argument("--http-timeout-sec", type=int, default=60)
    compound_anchor.add_argument("--retries", type=int, default=2)
    compound_anchor.add_argument("--fail-on-drift", action="store_true")
    compound_anchor.set_defaults(func=cmd_compound_anchor)

    compound_e2e = sub.add_parser("compound-e2e", help="Run bounded Compound HyperSync replay, serving smoke, and final RPC anchor")
    compound_e2e.add_argument("--rpc-url", default=None)
    compound_e2e.add_argument("--protocol", choices=["v2", "v3", "both"], default="both")
    compound_e2e.add_argument("--anchor-block", type=int, default=0)
    compound_e2e.add_argument("--from-block", type=int, default=0, help="Inclusive HyperSync replay start block")
    compound_e2e.add_argument("--to-block", type=int, default=0, help="Inclusive HyperSync replay end block")
    compound_e2e.add_argument("--batch-blocks", type=int, default=5_000)
    compound_e2e.add_argument("--block-number", type=int, default=0)
    compound_e2e.add_argument("--block-mode", choices=["processed", "latest"], default="processed")
    compound_e2e.add_argument("--confirmations", type=int, default=12)
    compound_e2e.add_argument("--notional-threshold", type=float, default=0.001)
    compound_e2e.add_argument("--apy-threshold", type=float, default=1e-6)
    compound_e2e.add_argument("--http-timeout-sec", type=int, default=60)
    compound_e2e.add_argument("--retries", type=int, default=2)
    compound_e2e.add_argument("--set-cursors", action="store_true")
    compound_e2e.add_argument("--bootstrap", action=argparse.BooleanOptionalAction, default=True)
    compound_e2e.add_argument("--fail-on-drift", action="store_true")
    compound_e2e.set_defaults(func=cmd_compound_e2e)

    views = sub.add_parser("views", help="Manage serving materialized views")
    views_sub = views.add_subparsers(dest="views_command", required=True)
    views_list = views_sub.add_parser("list")
    views_list.set_defaults(func=cmd_views)
    views_rebuild = views_sub.add_parser("rebuild")
    views_rebuild.set_defaults(func=cmd_views)

    streams = sub.add_parser("streams", help="Manage Astrid canonical stream registry")
    streams_sub = streams.add_subparsers(dest="streams_command", required=True)
    streams_check = streams_sub.add_parser("check", help="Validate stream registry")
    streams_check.add_argument("--json", action="store_true")
    streams_check.set_defaults(func=cmd_streams)
    streams_apply = streams_sub.add_parser("apply", help="Apply JetStream stream definitions and publish manifest")
    streams_apply.set_defaults(func=cmd_streams)
    streams_manifest = streams_sub.add_parser("manifest", help="Render Astrid stream manifest")
    streams_manifest.add_argument("--out", default=None)
    streams_manifest.add_argument("--chunks-dir", default=None, help="Directory containing *.chunk.json sidecars to embed")
    streams_manifest.set_defaults(func=cmd_streams)

    publisher = sub.add_parser("publisher", help="Publish canonical ClickHouse rows to Astrid streams")
    publisher_sub = publisher.add_subparsers(dest="publisher_command", required=True)
    publisher_run = publisher_sub.add_parser("run", help="Publish the next batch for a stream")
    publisher_run.add_argument("--stream", required=True)
    publisher_run.add_argument("--limit", type=int, default=int(os.getenv("ASTRID_PUBLISHER_BATCH_SIZE", "1000")))
    publisher_run.set_defaults(func=cmd_publisher)
    publisher_backfill = publisher_sub.add_parser("backfill", help="Publish from an explicit stream cursor")
    publisher_backfill.add_argument("--stream", required=True)
    publisher_backfill.add_argument("--from", dest="from_value", default=None)
    publisher_backfill.add_argument("--limit", type=int, default=int(os.getenv("ASTRID_PUBLISHER_BATCH_SIZE", "1000")))
    publisher_backfill.set_defaults(func=cmd_publisher)
    publisher_export_chunk = publisher_sub.add_parser("export-chunk", help="Export a stream batch as an Astrid JSONL chunk")
    publisher_export_chunk.add_argument("--stream", required=True)
    publisher_export_chunk.add_argument("--out-dir", required=True)
    publisher_export_chunk.add_argument("--base-uri", default=None)
    publisher_export_chunk.add_argument("--from", dest="from_value", default=None)
    publisher_export_chunk.add_argument("--limit", type=int, default=int(os.getenv("ASTRID_PUBLISHER_BATCH_SIZE", "1000")))
    publisher_export_chunk.set_defaults(func=cmd_publisher)
    publisher_status = publisher_sub.add_parser("status", help="Show Astrid publisher cursors")
    publisher_status.add_argument("--json", action="store_true")
    publisher_status.set_defaults(func=cmd_publisher)

    args = parser.parse_args()
    apply_env_from_config(args.config)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
