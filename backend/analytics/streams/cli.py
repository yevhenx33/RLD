"""Astrid publisher CLI — wires existing stream publishing logic into commands."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

log = logging.getLogger("astrid-publish")


def _ch_client():
    import clickhouse_connect

    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
    )


def _nats_url() -> str:
    return os.getenv("ASTRID_NATS_URL", os.getenv("NATS_URL", "nats://127.0.0.1:4222"))


def _find_stream(stream_id: str):
    from analytics.streams.registry import load_registry

    streams = load_registry()
    for stream in streams:
        if stream.id == stream_id:
            return stream
    valid = ", ".join(s.id for s in streams)
    raise SystemExit(f"Unknown stream '{stream_id}'. Available: {valid}")


# ── Commands ─────────────────────────────────────────────────────────


def cmd_apply_streams(args) -> int:
    """Create NATS JetStream streams and publish the registry manifest."""
    from analytics.streams.publisher import apply_streams

    nats_url = args.nats_url or _nats_url()
    result = asyncio.run(apply_streams(nats_url))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_once(args) -> int:
    """Publish one batch of rows for a single stream."""
    from analytics.streams.publisher import publish_once

    stream = _find_stream(args.stream_id)
    ch = _ch_client()
    nats_url = args.nats_url or _nats_url()
    try:
        result = asyncio.run(
            publish_once(
                ch,
                nats_url,
                stream,
                from_value=args.from_value,
                limit=args.batch_size,
            )
        )
    finally:
        ch.close()
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


def cmd_daemon(args) -> int:
    """Continuously poll source table and publish new rows."""
    from analytics.streams.publisher import publish_once

    stream = _find_stream(args.stream_id)
    nats_url = args.nats_url or _nats_url()
    interval = args.interval

    shutdown = False

    def _signal_handler(signum, frame):
        nonlocal shutdown
        log.info("Shutdown signal received, finishing current batch...")
        shutdown = True

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    log.info(f"Starting daemon for {stream.id} (interval={interval}s, batch={args.batch_size})")
    total_published = 0
    ch = _ch_client()
    try:
        while not shutdown:
            try:
                result = asyncio.run(
                    publish_once(ch, nats_url, stream, limit=args.batch_size)
                )
                rows = result.get("rows", 0)
                total_published += rows
                if rows > 0:
                    log.info(f"[{stream.id}] Published {rows} rows (total: {total_published})")
                else:
                    log.debug(f"[{stream.id}] No new rows")
            except Exception:
                log.exception(f"[{stream.id}] Publish error, will retry in {interval}s")
            if not shutdown:
                time.sleep(interval)
    finally:
        ch.close()
    log.info(f"Daemon stopped. Total published: {total_published}")
    return 0


def cmd_export_chunk(args) -> int:
    """Export a data chunk for a stream to a local directory."""
    stream = _find_stream(args.stream_id)
    ch = _ch_client()
    try:
        if args.format == "parquet":
            from analytics.streams.publisher import export_parquet_chunk
            metadata = export_parquet_chunk(
                ch, stream, args.out,
                base_uri=args.base_uri,
                from_value=args.from_value,
                limit=args.batch_size,
            )
        else:
            from analytics.streams.publisher import export_jsonl_chunk
            metadata = export_jsonl_chunk(
                ch, stream, args.out,
                base_uri=args.base_uri,
                from_value=args.from_value,
                limit=args.batch_size,
            )
    finally:
        ch.close()
    print(json.dumps(metadata, indent=2, sort_keys=True))
    return 0


def cmd_manifest(args) -> int:
    """Generate a manifest JSON with optional chunk sidecar metadata."""
    from analytics.streams.publisher import load_chunk_sidecars, manifest_with_chunks
    from analytics.streams.registry import load_registry

    streams = load_registry()
    chunks_by_stream = {}
    if args.chunks_dir:
        chunks_by_stream = load_chunk_sidecars(args.chunks_dir)
    manifest = manifest_with_chunks(streams, chunks_by_stream)
    output = json.dumps(manifest, indent=2, sort_keys=True, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(output + "\n", encoding="utf-8")
        print(f"Wrote manifest to {args.out}")
    else:
        print(output)
    return 0


def cmd_snapshot(args) -> int:
    """Export per-market Parquet snapshots with manifest."""
    from analytics.streams.snapshot import export_snapshot

    t0 = time.time()
    manifest = export_snapshot(
        args.source_table,
        args.out,
        compress=args.compress,
        workers=args.workers,
    )
    elapsed = time.time() - t0
    stats = manifest["stats"]
    print(f"Exported {stats['market_count']} markets, {stats['total_rows']:,} rows")
    print(f"  Per-market: {stats['total_bytes']/1024/1024:.1f} MB in {stats['export_ms']}ms")
    print(f"  Full file:  {manifest['full_snapshot']['bytes']/1024/1024:.1f} MB in {stats['full_export_ms']}ms")
    print(f"  Manifest:   {args.out}/manifest.json")
    print(f"  Total:      {elapsed:.1f}s")
    return 0


def cmd_upload(args) -> int:
    """Upload snapshot to Cloudflare R2."""
    from analytics.streams.upload import upload_snapshot

    kwargs = {"prefix": args.prefix}
    if args.bucket:
        kwargs["bucket"] = args.bucket
    if args.public_url:
        kwargs["public_url"] = args.public_url

    result = upload_snapshot(args.snapshot_dir, **kwargs)
    print(f"Uploaded {result['files_uploaded']} files ({result['total_bytes']/1024/1024:.1f} MB)")
    print(f"  Bucket:   {result['bucket']}")
    print(f"  Prefix:   {result['prefix']}")
    print(f"  Manifest: {result['manifest_url']}")
    print(f"  Time:     {result['elapsed_s']}s ({result['throughput_mbps']} MB/s)")
    return 0


def _selected_r2_streams(args):
    from analytics.streams.r2_delta import _select_streams

    return _select_streams(bool(args.all_streams), list(args.stream_id or []))


def cmd_r2_base(args) -> int:
    """Export and upload an hourly immutable R2 base snapshot for registered streams."""
    from analytics.streams.r2_delta import R2Uploader, publish_base

    streams = _selected_r2_streams(args)
    ch = _ch_client()
    try:
        result = publish_base(
            ch,
            R2Uploader(bucket=args.bucket, public_base_url=args.public_url),
            prefix=args.prefix,
            streams=streams,
            out_dir=args.out_dir,
        )
    finally:
        ch.close()
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


def cmd_r2_delta(args) -> int:
    """Export and upload append-only R2 deltas for registered streams."""
    from analytics.streams.r2_delta import R2Uploader, publish_delta, run_delta_loop

    streams = _selected_r2_streams(args)
    uploader = R2Uploader(bucket=args.bucket, public_base_url=args.public_url)
    if not args.once:
        def _factory():
            return _ch_client()

        run_delta_loop(
            _factory,
            uploader,
            prefix=args.prefix,
            all_streams=bool(args.all_streams),
            stream_ids=list(args.stream_id or []),
            interval_seconds=args.interval,
        )
        return 0

    ch = _ch_client()
    try:
        result = publish_delta(ch, uploader, prefix=args.prefix, streams=streams, out_dir=args.out_dir)
    finally:
        ch.close()
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


def cmd_list(args) -> int:
    """List all registered stream definitions."""
    from analytics.streams.registry import load_registry

    streams = load_registry()
    for stream in streams:
        print(f"{stream.id}\t{stream.mode}\t{stream.source_table}\t→\t{stream.local_table}")
    return 0


# ── Entry Point ──────────────────────────────────────────────────────


def main() -> int:
    logging.basicConfig(
        level=logging.DEBUG if os.getenv("ASTRID_DEBUG") else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="astrid-publish",
        description="Astrid canonical stream publisher",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # apply-streams
    apply = sub.add_parser("apply-streams", help="Create NATS JetStream streams and publish registry manifest")
    apply.add_argument("--nats-url", default=None)
    apply.set_defaults(func=cmd_apply_streams)

    # list
    ls = sub.add_parser("list", help="List all registered stream definitions")
    ls.set_defaults(func=cmd_list)

    # once
    once = sub.add_parser("once", help="Publish one batch of rows for a stream")
    once.add_argument("stream_id")
    once.add_argument("--nats-url", default=None)
    once.add_argument("--from", dest="from_value", default=None, help="Start cursor value")
    once.add_argument("--batch-size", type=int, default=1000)
    once.set_defaults(func=cmd_once)

    # daemon
    daemon = sub.add_parser("daemon", help="Continuously publish new rows for a stream")
    daemon.add_argument("stream_id")
    daemon.add_argument("--nats-url", default=None)
    daemon.add_argument("--interval", type=float, default=10.0, help="Poll interval in seconds")
    daemon.add_argument("--batch-size", type=int, default=1000)
    daemon.set_defaults(func=cmd_daemon)

    # export-chunk
    export = sub.add_parser("export-chunk", help="Export a data chunk to local directory")
    export.add_argument("stream_id")
    export.add_argument("--out", required=True, help="Output directory")
    export.add_argument("--format", choices=["jsonl", "parquet"], default="jsonl", help="Chunk format")
    export.add_argument("--base-uri", default=None, help="Base URI for chunk references in metadata")
    export.add_argument("--from", dest="from_value", default=None)
    export.add_argument("--batch-size", type=int, default=10000)
    export.set_defaults(func=cmd_export_chunk)

    # manifest
    manifest = sub.add_parser("manifest", help="Generate manifest JSON")
    manifest.add_argument("--chunks-dir", default=None, help="Directory with .chunk.json sidecars")
    manifest.add_argument("--out", default=None, help="Output file (stdout if omitted)")
    manifest.set_defaults(func=cmd_manifest)

    # snapshot (v2)
    snap = sub.add_parser("snapshot", help="Export per-market Parquet snapshots")
    snap.add_argument("source_table", help="Source ClickHouse table (e.g. market_timeseries)")
    snap.add_argument("--out", required=True, help="Output directory")
    snap.add_argument("--compress", default="zstd", choices=["zstd", "lz4", "snappy", "none"])
    snap.add_argument("--workers", type=int, default=8, help="Parallel export workers")
    snap.set_defaults(func=cmd_snapshot)

    # upload (v2 — R2)
    upload = sub.add_parser("upload", help="Upload snapshot to Cloudflare R2")
    upload.add_argument("snapshot_dir", help="Snapshot directory (from 'snapshot' command)")
    upload.add_argument("--prefix", default="v2", help="R2 key prefix")
    upload.add_argument("--bucket", default=None, help="R2 bucket name")
    upload.add_argument("--public-url", default=None, help="Public URL base (e.g. https://data.astrid.dev)")
    upload.set_defaults(func=cmd_upload)

    # r2-base (manifest v3)
    r2_base = sub.add_parser("r2-base", help="Publish hourly immutable R2 base snapshots")
    r2_base.add_argument("stream_id", nargs="*", help="Stream id(s) to export")
    r2_base.add_argument("--all-streams", action="store_true", help="Export every registered stream")
    r2_base.add_argument("--prefix", default="v2", help="R2 key prefix")
    r2_base.add_argument("--bucket", default=None, help="R2 bucket name")
    r2_base.add_argument("--public-url", default=None, help="Public URL base (e.g. https://astrid.rld.fi)")
    r2_base.add_argument("--out-dir", default="/tmp/astrid-r2-publish", help="Local staging directory")
    r2_base.set_defaults(func=cmd_r2_base)

    # r2-delta (manifest v3)
    r2_delta = sub.add_parser("r2-delta", help="Publish append-only R2 deltas")
    r2_delta.add_argument("stream_id", nargs="*", help="Stream id(s) to export")
    r2_delta.add_argument("--all-streams", action="store_true", help="Export every registered stream")
    r2_delta.add_argument("--once", action="store_true", help="Run one delta publish and exit")
    r2_delta.add_argument("--interval", type=float, default=15.0, help="Loop interval in seconds")
    r2_delta.add_argument("--prefix", default="v2", help="R2 key prefix")
    r2_delta.add_argument("--bucket", default=None, help="R2 bucket name")
    r2_delta.add_argument("--public-url", default=None, help="Public URL base (e.g. https://astrid.rld.fi)")
    r2_delta.add_argument("--out-dir", default="/tmp/astrid-r2-publish", help="Local staging directory")
    r2_delta.set_defaults(func=cmd_r2_delta)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
