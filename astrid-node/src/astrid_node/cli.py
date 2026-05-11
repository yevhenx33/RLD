"""Astrid Node CLI."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import subprocess
from pathlib import Path

from astrid_node.chunks import sync_chunks
from astrid_node.clickhouse import client_from_config, export_query, validate_qualified_identifier
from astrid_node.config import load_config
from astrid_node.metadata import ensure_metadata
from astrid_node.nats_client import consume_stream
from astrid_node.processor import Processor
from astrid_node.streams import find_stream, install_stream, load_manifest


ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "docker-compose.yml"


def _ch():
    return client_from_config(load_config())


def cmd_init(_args) -> int:
    print("Astrid Node initialized. Run `astrid-node up` then `astrid-node migrate`.")
    return 0


def cmd_up(_args) -> int:
    return subprocess.call(["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"])


def cmd_down(_args) -> int:
    return subprocess.call(["docker", "compose", "-f", str(COMPOSE_FILE), "down"])


def cmd_migrate(_args) -> int:
    ch = _ch()
    try:
        ensure_metadata(ch)
    finally:
        ch.close()
    print("Astrid local metadata is ready")
    return 0


def _manifest_source(args) -> str:
    source = getattr(args, "manifest", None) or load_config().manifest_url
    if not source:
        raise SystemExit("manifest source required: pass --manifest or set ASTRID_NODE_MANIFEST_URL")
    return source


def cmd_streams(args) -> int:
    streams = load_manifest(_manifest_source(args))
    if args.streams_command == "list":
        for stream in streams:
            print(f"{stream.id}\t{stream.subject}\t{stream.local_table}")
        return 0
    if args.streams_command == "install":
        selected = find_stream(streams, args.stream_id)
        if args.dry_run:
            print(json.dumps(install_stream(None, selected, dry_run=True), indent=2, sort_keys=True))
            return 0
        ch = _ch()
        try:
            ensure_metadata(ch)
            result = install_stream(ch, selected, dry_run=args.dry_run)
        finally:
            ch.close()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    raise SystemExit(f"Unknown streams command {args.streams_command}")


def cmd_export(args) -> int:
    ch = _ch()
    try:
        path = export_query(ch, args.table, output_format=args.format, out=args.out)
    finally:
        ch.close()
    print(path)
    return 0


def cmd_status(args) -> int:
    from astrid_node.pull import cache_status
    from pathlib import Path

    data_dir = Path(args.data_dir) if getattr(args, "data_dir", None) else None
    payload = {"cache": cache_status(data_dir) if data_dir else cache_status()}
    try:
        ch = _ch()
        try:
            result = ch.query("SELECT stream_id, local_table, installed_at FROM astrid_meta.installed_streams FINAL ORDER BY stream_id")
            rows = getattr(result, "result_rows", [])
            payload["installedStreams"] = len(rows)
            payload["streams"] = [list(row) for row in rows]
        finally:
            ch.close()
    except Exception as exc:  # ClickHouse is optional for v3 local data mode.
        payload["clickhouse"] = {"available": False, "error": str(exc)}
    print(json.dumps(payload, default=str, indent=2, sort_keys=True))
    return 0


def cmd_sync(args) -> int:
    config = load_config()
    stream = find_stream(load_manifest(_manifest_source(args)), args.stream_id)
    ch = client_from_config(config)
    try:
        ensure_metadata(ch)
        install_stream(ch, stream, dry_run=False)
        result = sync_chunks(ch, stream, config)
    finally:
        ch.close()
    print(json.dumps({"status": "OK", "stream": stream.id, **result}, indent=2, sort_keys=True))
    return 0


def cmd_sync_fast(args) -> int:
    """v2 fast sync: download Parquet snapshot → native HTTP POST import."""
    import base64
    import time as _time

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        raise SystemExit(f"Manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    data_dir = manifest_path.parent / "markets"
    if not data_dir.exists():
        data_dir = manifest_path.parent  # fallback: flat layout

    config = load_config()
    ch_url = f"http://{config.clickhouse_host}:{config.clickhouse_port}"
    auth = base64.b64encode(f"{config.clickhouse_user}:{config.clickhouse_password}".encode()).decode()
    target_db = args.target_db or "astrid_data"

    symbols = args.symbols.split(",") if args.symbols else None

    from astrid_node.native_import import sync_from_manifest

    t0 = _time.time()
    result = sync_from_manifest(
        ch_url, manifest, data_dir,
        target_db=target_db,
        auth=auth,
        symbols=symbols,
        workers=args.workers,
    )
    elapsed = _time.time() - t0

    print(f"\u2713 {result['rows_imported']:,} rows synced in {elapsed:.1f}s")
    print(f"  Table:   {result['table']}")
    print(f"  Markets: {result['markets_synced']}")
    print(f"  Import:  {result['elapsed_s']}s ({result['throughput_mbps']} MB/s)")
    print(f"  Verify:  {result['verify_ms']}ms")
    return 0


def cmd_query(args) -> int:
    """Query local Parquet files with DuckDB (zero ClickHouse dependency)."""
    from astrid_node.duckdb_mode import describe_parquet, query_parquet, query_parquet_files, query_parquet_glob
    from astrid_node.pull import local_parquet_files, pull_streams
    from pathlib import Path
    import time as _time

    data_dir = Path(args.data_dir) if args.data_dir else None
    stream_ids = [args.stream] if args.stream else None
    symbols = args.symbols if args.symbols else None

    if args.pull:
        pull_kwargs = {}
        if args.base_url:
            pull_kwargs["base_url"] = args.base_url
        if data_dir:
            pull_kwargs["data_dir"] = data_dir
        pulled = pull_streams(stream_ids, symbols=symbols, since=args.since, **pull_kwargs)
        if pulled.get("errors"):
            raise SystemExit("pull failed: " + "; ".join(pulled["errors"]))

    if args.stream:
        files, _manifest, stream_manifest = local_parquet_files(data_dir=data_dir or Path(__import__('os').path.expanduser('~/.astrid/data/v2')), stream_ids=stream_ids, symbols=symbols, since=args.since)
        if not files:
            raise SystemExit("no cached files match query; run astrid-node pull first or pass --pull")
        if args.describe:
            print(json.dumps({"files": files, "stream": args.stream}, indent=2, default=str))
            return 0
        sql = args.sql
        if not sql:
            raise SystemExit("--sql is required")
        t0 = _time.time()
        rows = query_parquet_files(files, sql, stream_manifest=stream_manifest, dedupe=not args.no_dedupe)
        elapsed = _time.time() - t0
    else:
        if not args.parquet:
            raise SystemExit("query requires a parquet path or --stream")
        if args.describe:
            info = describe_parquet(args.parquet)
            print(json.dumps(info, indent=2, default=str))
            return 0
        sql = args.sql
        if not sql:
            raise SystemExit("--sql is required (or use --describe)")
        t0 = _time.time()
        if "*" in args.parquet:
            rows = query_parquet_glob(args.parquet, sql)
        else:
            rows = query_parquet(args.parquet, sql)
        elapsed = _time.time() - t0

    if args.format == "json":
        print(json.dumps(rows, indent=2, default=str))
    elif rows:
        cols = list(rows[0].keys())
        print("\t".join(cols))
        for row in rows:
            print("\t".join(str(row.get(c, "")) for c in cols))
    print(f"\n({len(rows)} rows, {elapsed*1000:.0f}ms)", file=__import__('sys').stderr)
    return 0


def cmd_pull(args) -> int:
    """Download market data from Astrid R2."""
    from astrid_node.pull import pull_markets, pull_full, pull_streams, list_cached
    from pathlib import Path
    import time as _time

    data_dir = Path(args.data_dir) if args.data_dir else None
    kwargs = {}
    if args.base_url:
        kwargs["base_url"] = args.base_url
    if data_dir:
        kwargs["data_dir"] = data_dir

    if args.list:
        cached = list_cached(data_dir) if data_dir else list_cached()
        if not cached:
            print("No cached data. Run: astrid-node pull WETH")
            return 0
        total = 0
        for f in cached:
            print(f"  {f['filename']:50s}  {f['bytes']/1024:>8.0f} KB")
            total += f['bytes']
        print(f"  {'TOTAL':50s}  {total/1024/1024:>8.1f} MB")
        return 0

    if args.full:
        result = pull_full(**kwargs)
        print(f"\u2713 Downloaded {result['bytes']/1024/1024:.1f} MB in {result['elapsed_s']}s")
        print(f"  Path: {result['path']}")
        return 0

    symbols = args.symbols if args.symbols else None

    t0 = _time.time()
    if args.stream:
        result = pull_streams(args.stream, symbols=symbols, since=args.since, force=args.force, **kwargs)
    else:
        result = pull_streams(None, symbols=symbols, since=args.since, force=args.force, **kwargs)
    elapsed = _time.time() - t0

    print(f"\u2713 {result['downloaded']} downloaded, {result['skipped']} cached ({elapsed:.1f}s)")
    print(f"  Data: {result['data_dir']}")
    if result['errors']:
        for e in result['errors']:
            print(f"  ERROR: {e}")
    return 1 if result['errors'] else 0


def cmd_consume(args) -> int:
    config = load_config()
    stream = find_stream(load_manifest(_manifest_source(args)), args.stream_id)
    ch = client_from_config(config)
    try:
        ensure_metadata(ch)
        install_stream(ch, stream, dry_run=False)
        result = asyncio.run(
            consume_stream(
                stream,
                config,
                ch,
                durable=args.durable,
                batch_size=args.batch_size,
                once=not args.tail,
            )
        )
    finally:
        ch.close()
    print(json.dumps({"status": "OK", **result}, indent=2, sort_keys=True))
    return 0


def cmd_processor(args) -> int:
    if not args.fixture:
        if not args.table:
            raise SystemExit("processor run requires --fixture or --table")

    processor_cls = _load_processor_class(Path(args.path))
    if args.fixture:
        messages = [
            json.loads(line)
            for line in Path(args.fixture).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        ctx = processor_cls.run_fixture(messages)
        message_count = len(messages)
    else:
        table = validate_qualified_identifier(args.table)
        ch = _ch()
        try:
            result = ch.query(f"SELECT payload_json FROM {table} FINAL LIMIT {int(args.limit)}")
            payloads = [row[0] for row in getattr(result, "result_rows", [])]
            ctx = processor_cls.run_payloads(payloads, ch=ch if args.write else None)
        finally:
            ch.close()
        message_count = len(payloads)
    print(
        json.dumps(
            {
                "status": "OK",
                "processor": processor_cls.__name__,
                "messages": message_count,
                "writes": len(ctx.writes),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _load_processor_class(path: Path) -> type[Processor]:
    if path.is_dir():
        path = path / "__init__.py"
    spec = importlib.util.spec_from_file_location("astrid_user_processor", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load processor from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    candidates = [
        value
        for value in vars(module).values()
        if isinstance(value, type) and issubclass(value, Processor) and value is not Processor
    ]
    if len(candidates) != 1:
        raise SystemExit(f"expected exactly one Processor subclass in {path}, found {len(candidates)}")
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Astrid local data node")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init").set_defaults(func=cmd_init)
    sub.add_parser("up").set_defaults(func=cmd_up)
    sub.add_parser("down").set_defaults(func=cmd_down)
    sub.add_parser("migrate").set_defaults(func=cmd_migrate)
    status = sub.add_parser("status")
    status.add_argument("--data-dir", default=None)
    status.set_defaults(func=cmd_status)

    streams = sub.add_parser("streams")
    streams_sub = streams.add_subparsers(dest="streams_command", required=True)
    streams_list = streams_sub.add_parser("list")
    streams_list.add_argument("--manifest", default=None)
    streams_list.set_defaults(func=cmd_streams)
    streams_install = streams_sub.add_parser("install")
    streams_install.add_argument("stream_id")
    streams_install.add_argument("--manifest", default=None)
    streams_install.add_argument("--dry-run", action="store_true")
    streams_install.set_defaults(func=cmd_streams)

    sync = sub.add_parser("sync")
    sync.add_argument("stream_id")
    sync.add_argument("--manifest", default=None)
    sync.set_defaults(func=cmd_sync)

    consume = sub.add_parser("consume")
    consume.add_argument("stream_id")
    consume.add_argument("--manifest", default=None)
    consume.add_argument("--tail", action="store_true")
    consume.add_argument("--durable", default=None)
    consume.add_argument("--batch-size", type=int, default=100)
    consume.set_defaults(func=cmd_consume)

    export = sub.add_parser("export")
    export.add_argument("table")
    export.add_argument("--format", choices=["csv", "parquet"], required=True)
    export.add_argument("--out", required=True)
    export.set_defaults(func=cmd_export)

    processor = sub.add_parser("processor")
    processor_sub = processor.add_subparsers(dest="processor_command", required=True)
    processor_run = processor_sub.add_parser("run")
    processor_run.add_argument("path")
    processor_run.add_argument("--from", dest="from_value", default=None)
    processor_run.add_argument("--fixture", default=None)
    processor_run.add_argument("--table", default=None, help="Process payload_json rows from a local Astrid table")
    processor_run.add_argument("--limit", type=int, default=1000)
    processor_run.add_argument("--write", action="store_true", help="Write processor output rows to ClickHouse")
    processor_run.set_defaults(func=cmd_processor)

    # sync-fast (v2)
    sync_fast = sub.add_parser("sync-fast", help="Fast sync: Parquet snapshot → native ClickHouse import")
    sync_fast.add_argument("--manifest", required=True, help="Path to manifest.json")
    sync_fast.add_argument("--target-db", default=None, help="Target database (default: astrid_data)")
    sync_fast.add_argument("--symbols", default=None, help="Comma-separated symbols to sync (default: all)")
    sync_fast.add_argument("--workers", type=int, default=16, help="Parallel import workers")
    sync_fast.set_defaults(func=cmd_sync_fast)

    # query (v2 — DuckDB)
    qry = sub.add_parser("query", help="Query local Parquet with DuckDB (no ClickHouse needed)")
    qry.add_argument("parquet", nargs="?", help="Path to .parquet file (or glob pattern)")
    qry.add_argument("--sql", default=None, help="SQL query (table is aliased as 'data')")
    qry.add_argument("--describe", action="store_true", help="Show schema and stats")
    qry.add_argument("--format", choices=["table", "json"], default="table")
    qry.add_argument("--stream", default=None, help="Manifest stream id to query from the local Astrid cache")
    qry.add_argument("--symbol", dest="symbols", action="append", default=None, help="Optional symbol filter for selecting cached objects")
    qry.add_argument("--since", default=None, help="Only select cached objects whose max timestamp is at or after this ISO timestamp")
    qry.add_argument("--pull", action="store_true", help="Pull matching R2 objects before querying")
    qry.add_argument("--base-url", default=None, help="Astrid R2 base URL")
    qry.add_argument("--data-dir", default=None, help="Local data directory")
    qry.add_argument("--no-dedupe", action="store_true", help="Disable identity/cursor de-duplication for base+delta queries")
    qry.set_defaults(func=cmd_query)

    # pull (v2 — R2 download)
    pull = sub.add_parser("pull", help="Download market data from Astrid R2")
    pull.add_argument("symbols", nargs="*", help="Symbols to download (e.g. WETH USDC). Omit for all.")
    pull.add_argument("--full", action="store_true", help="Download single all_markets.parquet")
    pull.add_argument("--list", action="store_true", help="List cached data")
    pull.add_argument("--force", action="store_true", help="Re-download even if cached")
    pull.add_argument("--base-url", default=None, help="Data source URL")
    pull.add_argument("--data-dir", default=None, help="Local data directory (default: ~/.astrid/data/v2)")
    pull.add_argument("--stream", action="append", default=None, help="Stream id to pull; can be repeated")
    pull.add_argument("--since", default=None, help="Only pull objects whose max timestamp is at or after this ISO timestamp")
    pull.set_defaults(func=cmd_pull)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
