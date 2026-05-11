#!/usr/bin/env python3
"""Disposable Spark -> Astrid end-to-end serving harness."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
ANALYTICS_ROOT = Path(__file__).resolve().parents[1]
ASTRID_NODE_SRC_CANDIDATES = (ROOT / "astrid-node" / "src", Path("/app/astrid-node/src"))
for path in (ROOT, *ASTRID_NODE_SRC_CANDIDATES):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

SPARK_STREAM_IDS = [
    "spark.raw.events.v1",
    "spark.processed.timeseries.v1",
    "spark.serving.market_latest.v1",
    "spark.serving.market_timeseries.v1",
    "spark.state.scaled.v1",
    "spark.state.reserve_risk.v1",
    "spark.state.emode_categories.v1",
    "spark.state.reserve_tokens.v1",
    "spark.status.source.v1",
]

FULL_COPY_TABLES = [
    "spark_events",
    "spark_timeseries",
    "spark_scaled_state",
    "spark_reserve_risk_state",
    "spark_emode_categories",
    "spark_reserve_tokens",
]

FILTERED_COPY_TABLES = {
    "api_market_latest": "protocol = 'SPARK_MARKET'",
    "market_timeseries": "protocol = 'SPARK_MARKET'",
    "source_status": "source = 'SPARK_MARKET'",
}


@dataclass(frozen=True)
class ContainerNames:
    production_clickhouse: str
    source_clickhouse: str
    sink_clickhouse: str
    nats: str


def _run(cmd: list[str], *, check: bool = True, capture: bool = False, text: bool = True) -> subprocess.CompletedProcess:
    kwargs: dict[str, Any] = {"check": False, "text": text}
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    proc = subprocess.run(cmd, **kwargs)
    if check and proc.returncode != 0:
        stdout = getattr(proc, "stdout", "") or ""
        stderr = getattr(proc, "stderr", "") or ""
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd)}\n{stdout}\n{stderr}".strip())
    return proc


def _compose(args: argparse.Namespace, *compose_args: str) -> None:
    _run(["docker", "compose", "-f", str(args.compose_file), "-p", args.project_name, *compose_args])


def _docker_exec(container: str, *cmd: str, capture: bool = True) -> str:
    proc = _run(["docker", "exec", container, *cmd], capture=capture)
    return (proc.stdout or "") if capture else ""


def _clickhouse(container: str, query: str, *, user: str | None = None, password: str | None = None) -> str:
    cmd = ["clickhouse-client"]
    if user:
        cmd.extend(["--user", user])
    if password:
        cmd.extend(["--password", password])
    cmd.extend(["--query", query])
    return _docker_exec(container, *cmd)


def _wait_for_clickhouse(container: str, *, user: str | None = None, password: str | None = None, timeout: int = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _clickhouse(container, "SELECT 1", user=user, password=password)
            return
        except Exception:
            time.sleep(2)
    raise TimeoutError(f"ClickHouse container did not become ready: {container}")


def _wait_for_nats(container: str, timeout: int = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _docker_exec(container, "wget", "-q", "-O", "-", "http://127.0.0.1:8222/healthz")
            return
        except Exception:
            time.sleep(2)
    raise TimeoutError(f"NATS container did not become ready: {container}")


def _safe_table_name(table: str) -> str:
    if not table.replace("_", "").isalnum() or table[0].isdigit():
        raise ValueError(f"unsafe table name: {table!r}")
    return table


def _show_create(container: str, table: str) -> str:
    table = _safe_table_name(table)
    return _clickhouse(container, f"SHOW CREATE TABLE {table}").strip().replace("\\n", "\n").replace("\\'", "'")


def _prepare_clone_table(source: str, target: str, table: str) -> None:
    table = _safe_table_name(table)
    ddl = _show_create(source, table)
    _clickhouse(target, f"DROP TABLE IF EXISTS {table} SYNC")
    _clickhouse(target, ddl)


def _native_copy(source: str, target: str, table: str, where: str = "") -> int:
    table = _safe_table_name(table)
    predicate = f" WHERE {where}" if where else ""
    select_query = f"SELECT * FROM {table}{predicate} FORMAT Native"
    insert_query = f"INSERT INTO {table} FORMAT Native"
    reader = subprocess.Popen(
        ["docker", "exec", source, "clickhouse-client", "--query", select_query],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert reader.stdout is not None
    writer = subprocess.Popen(
        ["docker", "exec", "-i", target, "clickhouse-client", "--query", insert_query],
        stdin=reader.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    reader.stdout.close()
    writer_out, writer_err = writer.communicate()
    reader_err = reader.stderr.read() if reader.stderr else b""
    reader_rc = reader.wait()
    if reader_rc != 0:
        raise RuntimeError(f"failed reading {table} from {source}: {reader_err.decode(errors='replace')}")
    if writer.returncode != 0:
        raise RuntimeError(
            f"failed writing {table} to {target}: {writer_out.decode(errors='replace')} {writer_err.decode(errors='replace')}"
        )
    count_where = f" WHERE {where}" if where else ""
    return int(_clickhouse(source, f"SELECT count() FROM {table}{count_where}").strip() or "0")


def clone_spark_slice(containers: ContainerNames) -> dict[str, int]:
    counts: dict[str, int] = {}
    print("[clone] preparing Spark-owned tables")
    for table in FULL_COPY_TABLES:
        _prepare_clone_table(containers.production_clickhouse, containers.source_clickhouse, table)
        counts[table] = _native_copy(containers.production_clickhouse, containers.source_clickhouse, table)
        print(f"[clone] {table}: {counts[table]} rows")
    print("[clone] preparing Spark-filtered shared tables")
    for table, where in FILTERED_COPY_TABLES.items():
        _prepare_clone_table(containers.production_clickhouse, containers.source_clickhouse, table)
        counts[table] = _native_copy(containers.production_clickhouse, containers.source_clickhouse, table, where)
        print(f"[clone] {table} ({where}): {counts[table]} rows")
    return counts


def mirror_new_spark_status(containers: ContainerNames, since: dt.datetime) -> int:
    where = "source = 'SPARK_MARKET' AND updated_at > toDateTime('%s')" % since.strftime("%Y-%m-%d %H:%M:%S")
    return _native_copy(containers.production_clickhouse, containers.source_clickhouse, "source_status", where)


def _source_ch(args: argparse.Namespace):
    import clickhouse_connect

    return clickhouse_connect.get_client(host=args.source_host, port=args.source_port)


def _sink_ch(args: argparse.Namespace):
    import clickhouse_connect

    return clickhouse_connect.get_client(
        host=args.sink_host,
        port=args.sink_port,
        username=args.sink_user,
        password=args.sink_password,
    )


def _load_spark_streams():
    from analytics.streams.registry import load_registry

    streams_by_id = {stream.id: stream for stream in load_registry()}
    missing = [stream_id for stream_id in SPARK_STREAM_IDS if stream_id not in streams_by_id]
    if missing:
        raise RuntimeError(f"missing Spark stream definitions: {', '.join(missing)}")
    return [streams_by_id[stream_id] for stream_id in SPARK_STREAM_IDS]


async def _publish_stream_until_empty(ch, nats_url: str, stream, batch_size: int) -> dict[str, int]:
    from analytics.streams.publisher import publish_once

    started = time.perf_counter()
    rows = 0
    messages = 0
    batches = 0
    while True:
        result = await publish_once(ch, nats_url, stream, limit=batch_size, processor_version="spark-astrid-e2e")
        if result.get("status") == "NO_ROWS":
            break
        batch_rows = int(result.get("rows", 0) or 0)
        batch_messages = int(result.get("messages", 0) or 0)
        rows += batch_rows
        messages += batch_messages
        batches += 1
        if batch_rows <= 0:
            break
    seconds = time.perf_counter() - started
    return {
        "rows": rows,
        "messages": messages,
        "batches": batches,
        "seconds": round(seconds, 3),
        "messages_per_sec": round(messages / seconds, 2) if seconds else 0.0,
        "nats_connections": batches,
        "connection_model": "one NATS connection per publish batch",
    }


def publish_spark_streams_for_ids(args: argparse.Namespace, stream_ids: list[str]) -> dict[str, dict[str, int]]:
    from analytics.streams.publisher import apply_streams

    started = time.perf_counter()
    all_streams = {stream.id: stream for stream in _load_spark_streams()}
    streams = [all_streams[stream_id] for stream_id in stream_ids]
    ch = _source_ch(args)
    try:
        asyncio.run(apply_streams(args.nats_url, streams))
        result = {
            stream.id: asyncio.run(_publish_stream_until_empty(ch, args.nats_url, stream, args.batch_size))
            for stream in streams
        }
        total_seconds = time.perf_counter() - started
        total_messages = sum(int(stats["messages"]) for stats in result.values())
        result["_total"] = {
            "rows": sum(int(stats["rows"]) for stats in result.values()),
            "messages": total_messages,
            "batches": sum(int(stats["batches"]) for stats in result.values()),
            "seconds": round(total_seconds, 3),
            "messages_per_sec": round(total_messages / total_seconds, 2) if total_seconds else 0.0,
            "nats_connections": 1 + sum(int(stats["nats_connections"]) for stats in result.values()),
            "connection_model": "one registry connection plus one publisher connection per batch",
        }
        return result
    finally:
        ch.close()


def publish_spark_streams(args: argparse.Namespace) -> dict[str, dict[str, int]]:
    result = publish_spark_streams_for_ids(args, SPARK_STREAM_IDS)
    for stream_id, stats in result.items():
        if stream_id == "_total":
            continue
        print(f"[publish] {stream_id}: {stats['rows']} rows in {stats['batches']} batches")
    total = result.get("_total")
    if total:
        print(
            "[publish] total: "
            f"{total['messages']} messages in {total['seconds']}s "
            f"({total['messages_per_sec']} msg/s, {total['nats_connections']} NATS connections)"
        )
    return result


async def _consume_stream_until_empty(stream, config, ch, *, durable: str, batch_size: int) -> dict[str, Any]:
    import nats
    from astrid_node.clickhouse import insert_json_payloads
    from nats.errors import TimeoutError as NatsTimeoutError

    started = time.perf_counter()
    nc = await nats.connect(config.nats_url, name="spark-astrid-e2e")
    js = nc.jetstream()
    consumed = 0
    batches = 0
    try:
        sub = await js.pull_subscribe(stream.subject, durable=durable)
        while True:
            try:
                messages = await sub.fetch(batch_size, timeout=1)
            except (TimeoutError, NatsTimeoutError):
                break
            if not messages:
                break
            payloads = [msg.data.decode("utf-8") for msg in messages]
            written = insert_json_payloads(ch, stream.local_table, payloads)
            for msg in messages:
                await msg.ack()
            consumed += written
            batches += 1
            ch.insert(
                "astrid_meta.stream_cursors",
                [[stream.id, str(consumed), 0]],
                column_names=["stream_id", "last_cursor", "last_block"],
            )
    finally:
        await nc.close()
    seconds = time.perf_counter() - started
    return {
        "messages": consumed,
        "batches": batches,
        "seconds": round(seconds, 3),
        "messages_per_sec": round(consumed / seconds, 2) if seconds else 0.0,
        "nats_connections": 1,
        "connection_model": "one persistent NATS connection per consumed stream",
    }


def consume_spark_streams_for_ids(args: argparse.Namespace, stream_ids: list[str]) -> dict[str, int]:
    from astrid_node.config import NodeConfig
    from astrid_node.metadata import ensure_metadata
    from astrid_node.streams import Stream, install_stream

    all_streams = {stream.id: stream for stream in _load_spark_streams()}
    config = NodeConfig(
        clickhouse_host=args.sink_host,
        clickhouse_port=args.sink_port,
        clickhouse_user=args.sink_user,
        clickhouse_password=args.sink_password,
        nats_url=args.nats_url,
    )
    sink = _sink_ch(args)
    try:
        started = time.perf_counter()
        ensure_metadata(sink)
        totals: dict[str, int] = {}
        run_id = int(time.time())
        for stream_id in stream_ids:
            stream = Stream.from_manifest(all_streams[stream_id].to_manifest())
            install_stream(sink, stream, dry_run=False)
            stats = asyncio.run(
                _consume_stream_until_empty(
                    stream,
                    config,
                    sink,
                    durable=f"spark_e2e_{run_id}_{stream.id.replace('.', '_')}",
                    batch_size=args.batch_size,
                )
            )
            totals[stream_id] = stats
        total_seconds = time.perf_counter() - started
        total_messages = sum(int(stats["messages"]) for stats in totals.values())
        totals["_total"] = {
            "messages": total_messages,
            "batches": sum(int(stats["batches"]) for stats in totals.values()),
            "seconds": round(total_seconds, 3),
            "messages_per_sec": round(total_messages / total_seconds, 2) if total_seconds else 0.0,
            "nats_connections": sum(int(stats["nats_connections"]) for stats in totals.values()),
            "connection_model": "one persistent NATS connection per stream",
        }
        return totals
    finally:
        sink.close()


def consume_spark_streams(args: argparse.Namespace) -> dict[str, int]:
    consumed = consume_spark_streams_for_ids(args, SPARK_STREAM_IDS)
    for stream_id, stats in consumed.items():
        if stream_id == "_total":
            continue
        print(f"[consume] {stream_id}: {stats['messages']} messages")
    total = consumed.get("_total")
    if total:
        print(
            "[consume] total: "
            f"{total['messages']} messages in {total['seconds']}s "
            f"({total['messages_per_sec']} msg/s, {total['nats_connections']} NATS connections)"
        )
    return consumed


def _where(stream) -> str:
    return f" WHERE {stream.source_filter}" if stream.source_filter else ""


def _source_count_and_max(ch, stream) -> tuple[int, str]:
    query = f"SELECT count(), max({stream.timestamp_column}) FROM {stream.source_table}{_where(stream)}"
    row = ch.query(query).result_rows[0]
    return int(row[0] or 0), str(row[1] or "")


def _view_count_and_max(ch, stream) -> tuple[int, str]:
    view = f"{stream.local_table}_v"
    row = ch.query(f"SELECT count(), max({stream.timestamp_column}) FROM {view}").result_rows[0]
    return int(row[0] or 0), str(row[1] or "")


def validate_serving(args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    source = _source_ch(args)
    sink = _sink_ch(args)
    report: dict[str, dict[str, Any]] = {}
    try:
        for stream in _load_spark_streams():
            source_count, source_max = _source_count_and_max(source, stream)
            view_count, view_max = _view_count_and_max(sink, stream)
            if source_count != view_count:
                raise AssertionError(f"{stream.id}: source count {source_count} != sink view count {view_count}")
            if source_max != view_max:
                raise AssertionError(f"{stream.id}: source max {source_max} != sink view max {view_max}")
            if source_count <= 0:
                raise AssertionError(f"{stream.id}: expected at least one row")
            report[stream.id] = {
                "rows": source_count,
                "max_timestamp": source_max,
                "view": f"{stream.local_table}_v",
            }
            print(f"[validate] {stream.id}: {source_count} rows max={source_max}")
        return report
    finally:
        source.close()
        sink.close()


def run_live_delta(args: argparse.Namespace, containers: ContainerNames) -> dict[str, Any]:
    source = _source_ch(args)
    sink = _sink_ch(args)
    try:
        before = source.query("SELECT max(updated_at) FROM source_status WHERE source = 'SPARK_MARKET'").result_rows[0][0]
        if before is None:
            raise AssertionError("Spark source_status clone has no updated_at cursor")
        before_dt = before if isinstance(before, dt.datetime) else dt.datetime.fromisoformat(str(before))
        before_sink_count = int(
            sink.command(
                "SELECT count() FROM astrid_spark.source_status_v WHERE updated_at > toDateTime('%s')"
                % before_dt.strftime("%Y-%m-%d %H:%M:%S")
            )
            or 0
        )
    finally:
        source.close()
        sink.close()

    deadline = time.time() + args.live_seconds
    mirrored = 0
    while time.time() < deadline:
        mirrored += mirror_new_spark_status(containers, before_dt)
        publish_spark_streams_for_ids(args, ["spark.status.source.v1"])
        consume_spark_streams_for_ids(args, ["spark.status.source.v1"])
        sink = _sink_ch(args)
        try:
            after_count = int(
                sink.command(
                    "SELECT count() FROM astrid_spark.source_status_v WHERE updated_at > toDateTime('%s')"
                    % before_dt.strftime("%Y-%m-%d %H:%M:%S")
                )
                or 0
            )
        finally:
            sink.close()
        if after_count > before_sink_count:
            print(f"[live] Spark status delta observed ({after_count - before_sink_count} new rows)")
            return {"status": "OK", "mirrored_rows": mirrored, "new_rows": after_count - before_sink_count}
        time.sleep(args.live_poll_sec)
    raise AssertionError("live Spark source_status delta was not observed before timeout")


def run_harness(args: argparse.Namespace) -> dict[str, Any]:
    containers = ContainerNames(
        production_clickhouse=args.production_clickhouse_container,
        source_clickhouse=args.source_clickhouse_container,
        sink_clickhouse=args.sink_clickhouse_container,
        nats=args.nats_container,
    )
    if not args.no_compose:
        _compose(args, "up", "-d", "source_clickhouse", "sink_clickhouse", "nats")
        _wait_for_clickhouse(containers.production_clickhouse)
        _wait_for_clickhouse(containers.source_clickhouse)
        _wait_for_clickhouse(containers.sink_clickhouse, user=args.sink_user, password=args.sink_password)
        _wait_for_nats(containers.nats)
    elif not args.skip_clone:
        _wait_for_clickhouse(containers.production_clickhouse)
        _wait_for_clickhouse(containers.source_clickhouse)

    clone_counts = {} if args.skip_clone else clone_spark_slice(containers)
    if args.clone_only:
        return {"clone": clone_counts, "status": "CLONED"}
    published = publish_spark_streams(args)
    consumed = consume_spark_streams(args)
    validation = validate_serving(args)
    live = run_live_delta(args, containers) if args.live_seconds > 0 else {"status": "SKIPPED"}
    return {"clone": clone_counts, "published": published, "consumed": consumed, "validation": validation, "live": live}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    default_compose = ANALYTICS_ROOT / "docker-compose.spark-astrid-e2e.yml"
    parser = argparse.ArgumentParser(description="Run disposable Spark Astrid E2E serving validation")
    parser.add_argument("--compose-file", type=Path, default=default_compose)
    parser.add_argument("--project-name", default="spark-astrid-e2e")
    parser.add_argument("--production-clickhouse-container", default="rld_clickhouse")
    parser.add_argument("--source-clickhouse-container", default="spark_astrid_e2e_source_clickhouse")
    parser.add_argument("--sink-clickhouse-container", default="spark_astrid_e2e_sink_clickhouse")
    parser.add_argument("--nats-container", default="spark_astrid_e2e_nats")
    parser.add_argument("--source-host", default="127.0.0.1")
    parser.add_argument("--source-port", type=int, default=18123)
    parser.add_argument("--sink-host", default="127.0.0.1")
    parser.add_argument("--sink-port", type=int, default=18124)
    parser.add_argument("--sink-user", default="astrid")
    parser.add_argument("--sink-password", default="astrid")
    parser.add_argument("--nats-url", default="nats://127.0.0.1:14222")
    parser.add_argument("--batch-size", type=int, default=20000)
    parser.add_argument("--live-seconds", type=int, default=90)
    parser.add_argument("--live-poll-sec", type=int, default=10)
    parser.add_argument("--no-compose", action="store_true", help="Assume disposable services are already running")
    parser.add_argument("--skip-clone", action="store_true", help="Reuse existing disposable source ClickHouse data")
    parser.add_argument("--clone-only", action="store_true", help="Start disposable services and clone Spark data, then stop before publishing")
    parser.add_argument("--keep", action="store_true", help="Keep disposable containers and volumes after the run")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = run_harness(args)
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return 0
    finally:
        if not args.keep and not args.no_compose and not args.clone_only:
            _compose(args, "down", "-v", "--remove-orphans")


if __name__ == "__main__":
    raise SystemExit(main())
