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


def cmd_status(_args) -> int:
    ch = _ch()
    try:
        result = ch.query("SELECT stream_id, local_table, installed_at FROM astrid_meta.installed_streams FINAL ORDER BY stream_id")
        rows = getattr(result, "result_rows", [])
    finally:
        ch.close()
    print(json.dumps({"installedStreams": len(rows), "streams": [list(row) for row in rows]}, default=str, indent=2))
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
    sub.add_parser("status").set_defaults(func=cmd_status)

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

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
