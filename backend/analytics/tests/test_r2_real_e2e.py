import datetime as dt
import hashlib
import os
import re
import sys
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

pytestmark = pytest.mark.r2_e2e

ROOT = Path(__file__).resolve().parents[2]
ASTRID_SRC = ROOT.parent / "astrid-node" / "src"
for path in (ROOT, ASTRID_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from analytics.streams.r2_delta import R2Uploader, publish_base, publish_delta
from analytics.streams.registry import StreamDefinition, load_registry


def _require_r2_env():
    if os.getenv("ASTRID_R2_E2E") != "1":
        pytest.skip("set ASTRID_R2_E2E=1 to run real R2 e2e")
    required = ["R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_PUBLIC_URL"]
    missing = [name for name in required if not os.getenv(name)]
    if not (os.getenv("R2_ENDPOINT") or os.getenv("R2_ACCOUNT_ID")):
        missing.append("R2_ENDPOINT or R2_ACCOUNT_ID")
    if missing:
        raise AssertionError("missing required R2 e2e environment: " + ", ".join(missing))


def _filter_literals(stream: StreamDefinition) -> dict[str, str]:
    return dict(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*'([^']*)'", stream.source_filter or ""))


def _stream_column_types(stream: StreamDefinition) -> dict[str, str]:
    types: dict[str, str] = {}
    for item in stream.columns:
        if ":" in item:
            name, kind = item.split(":", 1)
            types[name] = kind
    for column in [*stream.identity_columns, stream.cursor_column, stream.timestamp_column, stream.block_column]:
        types.setdefault(column, _infer_type(column, stream))
    for column in _filter_literals(stream):
        types.setdefault(column, "String")
    return types


def _infer_type(column: str, stream: StreamDefinition) -> str:
    if column in {stream.timestamp_column, "timestamp", "block_timestamp", "updated_at", "inserted_at", "last_data_timestamp", "last_success_at"}:
        return "DateTime"
    if column in {stream.cursor_column, stream.block_column} and column not in {stream.timestamp_column, "updated_at"}:
        return "UInt64"
    if any(token in column for token in ("block", "log_index", "round_id", "count", "category")):
        return "UInt64"
    if any(token in column for token in ("usd", "apy", "ltv", "threshold", "penalty", "factor", "price", "utilization")):
        return "Float64"
    if column.startswith("is_") or column.endswith("_enabled"):
        return "Int8"
    return "String"


def _is_datetime(kind: str) -> bool:
    return kind.startswith("DateTime") or kind == "Date"


def _is_integer(kind: str) -> bool:
    return kind.startswith("UInt") or kind.startswith("Int")


def _is_float(kind: str) -> bool:
    return kind.startswith("Float") or kind.startswith("Decimal")


def _value_for(column: str, kind: str, stream: StreamDefinition, when: dt.datetime, sequence: int) -> object:
    literals = _filter_literals(stream)
    if column in literals:
        return literals[column]
    if _is_datetime(kind):
        return when.replace(tzinfo=None)
    if column == "protocol":
        return "SPARK_MARKET" if stream.protocol == "spark" else "AAVE_MARKET"
    if column == "source":
        return "SPARK_MARKET" if stream.protocol == "spark" else "AAVE_MARKET"
    if column == "kind":
        return "market"
    if column == "entity_id":
        return "weth"
    if column == "symbol":
        return "WETH"
    if column == "target_id":
        return "WETH"
    if column == "deployment_id":
        return stream.deployment_id
    if column == "reserve":
        return "0x0000000000000000000000000000000000000001"
    if column == "user":
        return "0x0000000000000000000000000000000000000002"
    if column == "tx_hash":
        return f"0x{sequence:064x}"
    if column == "feed":
        return "ETH/USD"
    if column == "event_name":
        return "Fixture"
    if _is_integer(kind):
        return sequence
    if _is_float(kind):
        return float(sequence) / 10.0
    return f"{column}-{sequence}"


def _table_columns(streams: list[StreamDefinition]) -> dict[str, dict[str, str]]:
    by_table: dict[str, dict[str, str]] = {}
    for stream in streams:
        columns = by_table.setdefault(stream.source_table, {})
        for name, kind in _stream_column_types(stream).items():
            if name in columns and columns[name] != kind:
                if _is_datetime(columns[name]) or _is_datetime(kind):
                    columns[name] = "DateTime"
                elif _is_float(columns[name]) or _is_float(kind):
                    columns[name] = "Float64"
                else:
                    columns[name] = "String"
            else:
                columns[name] = kind
    return by_table


def _qualified_streams(database: str, streams: list[StreamDefinition]) -> list[StreamDefinition]:
    return [replace(stream, source_table=f"{database}.{stream.source_table}") for stream in streams]


def _create_fixture_tables(ch, streams: list[StreamDefinition]) -> None:
    for table, columns in _table_columns(streams).items():
        ddl = ", ".join(f"{name} {kind}" for name, kind in columns.items())
        ch.command(f"CREATE TABLE {table} ({ddl}) ENGINE = MergeTree ORDER BY tuple()")


def _insert_fixture_rows(ch, streams: list[StreamDefinition], when: dt.datetime, phase: int) -> None:
    columns_by_table = _table_columns(streams)
    streams_by_table: dict[str, list[StreamDefinition]] = {}
    for stream in streams:
        streams_by_table.setdefault(stream.source_table, []).append(stream)
    for table, table_streams in streams_by_table.items():
        columns = columns_by_table[table]
        names = list(columns)
        rows = []
        for index, stream in enumerate(table_streams, start=1):
            sequence = phase * 1000 + index
            rows.append([_value_for(name, columns[name], stream, when, sequence) for name in names])
        ch.insert(table, rows, column_names=names)


def _stream_entry(manifest: dict, stream_id: str) -> dict:
    return next(stream for stream in manifest["streams"] if stream["id"] == stream_id)


def test_real_r2_publisher_client_duckdb_e2e(tmp_path):
    _require_r2_env()
    pytest.importorskip("boto3")
    pytest.importorskip("duckdb")
    clickhouse_connect = pytest.importorskip("clickhouse_connect")
    from astrid_node.duckdb_mode import query_parquet_files
    from astrid_node.pull import local_parquet_files, pull_streams

    run_id = uuid.uuid4().hex
    prefix = f"v2-e2e/{run_id}"
    database = f"astrid_r2_e2e_{run_id[:12]}"
    public_base = os.environ["R2_PUBLIC_URL"].rstrip("/")
    uploader = R2Uploader(public_base_url=public_base)
    admin = clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
    )
    try:
        admin.command(f"CREATE DATABASE {database}")
        ch = clickhouse_connect.get_client(
            host=os.getenv("CLICKHOUSE_HOST", "localhost"),
            port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
            username=os.getenv("CLICKHOUSE_USER", "default"),
            password=os.getenv("CLICKHOUSE_PASSWORD", ""),
            database=database,
        )
        base_time = dt.datetime(2026, 5, 10, 18, 0, tzinfo=dt.UTC)
        streams = _qualified_streams(database, load_registry())
        stream_ids = [stream.id for stream in streams]
        _create_fixture_tables(ch, streams)
        _insert_fixture_rows(ch, streams, base_time, phase=1)

        base_result = publish_base(ch, uploader, prefix=prefix, streams=streams, out_dir=tmp_path / "publish", base_time=base_time)
        assert base_result["files"] == len(streams)
        cache = tmp_path / "cache"
        first_pull = pull_streams(stream_ids, base_url=f"{public_base}/{prefix}", data_dir=cache)
        assert first_pull["errors"] == []
        assert first_pull["downloaded"] == len(streams)

        _insert_fixture_rows(ch, streams, base_time + dt.timedelta(seconds=15), phase=2)
        delta_result = publish_delta(ch, uploader, prefix=prefix, streams=streams, out_dir=tmp_path / "publish", delta_time=base_time + dt.timedelta(seconds=15))
        assert delta_result["files"] == len(streams)
        second_pull = pull_streams(stream_ids, base_url=f"{public_base}/{prefix}", data_dir=cache)
        assert second_pull["errors"] == []
        assert second_pull["downloaded"] == len(streams)
        assert second_pull["skipped"] >= len(streams)

        files, manifest, spark_latest = local_parquet_files(data_dir=cache, stream_ids=["spark.serving.market_latest.v1"])
        rows = query_parquet_files(files, "SELECT count(*) AS n, max(timestamp) AS latest_ts FROM data", stream_manifest=spark_latest)
        assert rows[0]["n"] == 1
        assert "2026-05-10 18:00:15" in str(rows[0]["latest_ts"])
        assert _stream_entry(manifest, "spark.serving.market_latest.v1")["stats"]["delta_count"] == 1

        next_base_time = base_time + dt.timedelta(hours=1)
        rollover = publish_base(ch, uploader, prefix=prefix, streams=streams, out_dir=tmp_path / "publish", base_time=next_base_time)
        assert rollover["files"] == len(streams)
        third_pull = pull_streams(stream_ids, base_url=f"{public_base}/{prefix}", data_dir=cache)
        assert third_pull["errors"] == []
        assert third_pull["downloaded"] == len(streams)
        files, manifest, spark_latest = local_parquet_files(data_dir=cache, stream_ids=["spark.serving.market_latest.v1"])
        entry = _stream_entry(manifest, "spark.serving.market_latest.v1")
        assert entry["base"]["base_id"] == "20260510T190000Z"
        assert entry["deltas"] == []
        rows = query_parquet_files(files, "SELECT count(*) AS n, max(timestamp) AS latest_ts FROM data", stream_manifest=spark_latest)
        assert rows[0]["n"] == 1
        assert "2026-05-10 18:00:15" in str(rows[0]["latest_ts"])

        for path in files:
            data = Path(path).read_bytes()
            assert hashlib.sha256(data).hexdigest()
    finally:
        try:
            admin.command(f"DROP DATABASE IF EXISTS {database}")
        finally:
            uploader.delete_prefix(prefix)
