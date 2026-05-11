"""ClickHouse-to-JetStream publisher for Astrid streams."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from analytics.streams.canonical import encode_envelope, envelope, message_id
from analytics.streams.client import AstridJetStreamClient
from analytics.streams.registry import StreamDefinition, load_registry, registry_manifest
from analytics.streams.state import ensure_publisher_state_tables, read_cursor, upsert_cursor


DEFAULT_BATCH_SIZE = int(os.getenv("ASTRID_PUBLISHER_BATCH_SIZE", "1000"))


def _escape_sql(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''")


def _order_columns(stream: StreamDefinition) -> list[str]:
    return list(dict.fromkeys([stream.cursor_column, stream.timestamp_column, *stream.identity_columns]))


def _cursor_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dt.datetime):
        return {"type": "datetime", "value": value.strftime("%Y-%m-%d %H:%M:%S")}
    if isinstance(value, dt.date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, bool):
        return {"type": "bool", "value": value}
    if isinstance(value, int):
        return {"type": "int", "value": value}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    return {"type": "str", "value": "" if value is None else str(value)}


def _cursor_payload(stream: StreamDefinition, row: dict[str, Any]) -> str:
    columns = _order_columns(stream)
    return json.dumps(
        {
            "version": 1,
            "columns": columns,
            "values": [_cursor_value(row.get(column)) for column in columns],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _cursor_sql_literal(value: dict[str, Any]) -> str:
    value_type = value.get("type")
    raw = value.get("value")
    if value_type == "datetime":
        return f"toDateTime('{_escape_sql(str(raw))}')"
    if value_type == "date":
        return f"toDate('{_escape_sql(str(raw))}')"
    if value_type in {"int", "float"}:
        return str(raw)
    if value_type == "bool":
        return "1" if raw else "0"
    return f"'{_escape_sql(str(raw))}'"


def _cursor_predicate(stream: StreamDefinition, cursor: str) -> str:
    try:
        payload = json.loads(cursor)
    except json.JSONDecodeError:
        return f"{stream.cursor_column} > '{_escape_sql(str(cursor))}'"
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return f"{stream.cursor_column} > '{_escape_sql(str(cursor))}'"
    columns = payload.get("columns")
    values = payload.get("values")
    if not isinstance(columns, list) or not isinstance(values, list) or len(columns) != len(values):
        return f"{stream.cursor_column} > '{_escape_sql(str(cursor))}'"
    expected_columns = _order_columns(stream)
    if [str(column) for column in columns] != expected_columns:
        return f"{stream.cursor_column} > '{_escape_sql(str(cursor))}'"
    left = ", ".join(expected_columns)
    literals = [_cursor_sql_literal(value) for value in values if isinstance(value, dict)]
    if len(literals) != len(expected_columns):
        return f"{stream.cursor_column} > '{_escape_sql(str(cursor))}'"
    right = ", ".join(literals)
    return f"tuple({left}) > tuple({right})"


def _where_clause(stream: StreamDefinition, *, from_value: str | None = None, last_cursor: str | None = None) -> str:
    predicates: list[str] = []
    if stream.source_filter:
        predicates.append(f"({stream.source_filter})")
    if last_cursor:
        predicates.append(_cursor_predicate(stream, last_cursor))
    elif from_value:
        predicates.append(f"{stream.cursor_column} > '{_escape_sql(str(from_value))}'")
    if not predicates:
        return ""
    return "WHERE " + " AND ".join(predicates)


def select_rows_query(stream: StreamDefinition, *, from_value: str | None = None, last_cursor: str | None = None, limit: int = DEFAULT_BATCH_SIZE) -> str:
    where = _where_clause(stream, from_value=from_value, last_cursor=last_cursor)
    order = ", ".join(_order_columns(stream))
    return f"SELECT * FROM {stream.source_table} {where} ORDER BY {order} LIMIT {int(limit)}"


def _rows_as_dicts(result: Any) -> list[dict[str, Any]]:
    rows = getattr(result, "result_rows", [])
    columns = getattr(result, "column_names", None) or getattr(result, "columns", None)
    if not rows:
        return []
    if not columns:
        raise RuntimeError("ClickHouse query result must expose column_names")
    return [dict(zip(columns, row)) for row in rows]


def export_jsonl_chunk(
    ch,
    stream: StreamDefinition,
    out_dir: str | Path,
    *,
    base_uri: str | None = None,
    from_value: str | None = None,
    limit: int = DEFAULT_BATCH_SIZE,
    processor_version: str = "dev",
) -> dict[str, Any]:
    result = ch.query(select_rows_query(stream, from_value=from_value, limit=limit))
    rows = _rows_as_dicts(result)
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    chunk_id = f"{stream.id}.{stamp}"
    data_path = out_path / f"{chunk_id}.jsonl"
    metadata_path = out_path / f"{chunk_id}.chunk.json"
    digest = hashlib.sha256()
    first_cursor = None
    last_cursor = None
    with data_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            first_cursor = first_cursor or row.get(stream.cursor_column)
            last_cursor = row.get(stream.cursor_column)
            payload = encode_envelope(envelope(stream, [row], processor_version=processor_version))
            digest.update(payload)
            digest.update(b"\n")
            fh.write(payload.decode("utf-8"))
            fh.write("\n")
    uri = f"{base_uri.rstrip('/')}/{data_path.name}" if base_uri else data_path.resolve().as_uri()
    metadata = {
        "chunk_id": chunk_id,
        "stream_id": stream.id,
        "uri": uri,
        "format": "jsonl",
        "sha256": digest.hexdigest(),
        "row_count": len(rows),
        "first_cursor": _json_default(first_cursor),
        "last_cursor": _json_default(last_cursor),
        "created_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return metadata


def export_parquet_chunk(
    ch,
    stream: StreamDefinition,
    out_dir: str | Path,
    *,
    base_uri: str | None = None,
    from_value: str | None = None,
    limit: int = DEFAULT_BATCH_SIZE,
) -> dict[str, Any]:
    """Export a Parquet chunk directly from ClickHouse native FORMAT Parquet."""
    query = select_rows_query(stream, from_value=from_value, limit=limit)
    data = ch.raw_query(f"{query} FORMAT Parquet")
    if not data:
        return {"chunk_id": "", "stream_id": stream.id, "format": "parquet", "row_count": 0}

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    chunk_id = f"{stream.id}.{stamp}"
    data_path = out_path / f"{chunk_id}.parquet"
    metadata_path = out_path / f"{chunk_id}.chunk.json"

    data_path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()

    # Count rows via pyarrow for metadata accuracy
    try:
        import pyarrow.parquet as pq
        table = pq.read_table(data_path)
        row_count = table.num_rows
    except Exception:
        row_count = 0

    uri = f"{base_uri.rstrip('/')}/{data_path.name}" if base_uri else data_path.resolve().as_uri()
    metadata = {
        "chunk_id": chunk_id,
        "stream_id": stream.id,
        "uri": uri,
        "format": "parquet",
        "sha256": digest,
        "row_count": row_count,
        "created_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return metadata


def load_chunk_sidecars(chunks_dir: str | Path) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(Path(chunks_dir).glob("*.chunk.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        stream_id = payload["stream_id"]
        result.setdefault(stream_id, []).append(payload)
    return result


def manifest_with_chunks(streams: list[StreamDefinition], chunks_by_stream: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    manifest = registry_manifest(streams)
    chunks_by_stream = chunks_by_stream or {}
    for row in manifest["streams"]:
        chunks = chunks_by_stream.get(row["id"])
        if chunks:
            row["chunks"] = chunks
    return manifest


def _json_default(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


async def apply_streams(nats_url: str, streams: list[StreamDefinition] | None = None) -> dict[str, Any]:
    streams = streams or load_registry()
    client = AstridJetStreamClient(nats_url)
    await client.connect()
    try:
        await client.ensure_stream("ASTRID_REGISTRY", ["astrid.data.v1.registry.>"])
        await client.ensure_stream("ASTRID_DATA", sorted({stream.subject for stream in streams}))
        manifest_payload = json.dumps(registry_manifest(streams), sort_keys=True).encode("utf-8")
        ack = await client.publish(
            "astrid.data.v1.registry.streams",
            manifest_payload,
            message_id="astrid-registry-manifest-v1",
            stream_name="ASTRID_REGISTRY",
            headers={"Astrid-Message-Type": "registry-manifest"},
        )
        return {"status": "OK", "streams": len(streams), "registry_sequence": ack.seq}
    finally:
        await client.close()


async def publish_once(
    ch,
    nats_url: str,
    stream: StreamDefinition,
    *,
    from_value: str | None = None,
    limit: int = DEFAULT_BATCH_SIZE,
    processor_version: str = "dev",
) -> dict[str, Any]:
    ensure_publisher_state_tables(ch)
    cursor = read_cursor(ch, stream.id)
    result = ch.query(select_rows_query(stream, from_value=from_value, last_cursor=cursor and cursor["last_cursor"], limit=limit))
    rows = _rows_as_dicts(result)
    if not rows:
        return {"status": "NO_ROWS", "stream_id": stream.id, "rows": 0, "messages": 0}

    client = AstridJetStreamClient(nats_url)
    await client.connect()
    messages = 0
    last_ack_seq = 0
    try:
        for row in rows:
            payload = envelope(stream, [row], processor_version=processor_version)
            ack = await client.publish(
                stream.subject,
                encode_envelope(payload),
                message_id=message_id(stream, row),
                stream_name="ASTRID_DATA",
                headers={
                    "Astrid-Stream-Id": stream.id,
                    "Astrid-Schema-Version": stream.schema_version,
                    "Astrid-Schema-Hash": stream.schema_hash,
                },
            )
            messages += 1
            last_ack_seq = ack.seq
        last = rows[-1]
        ts = last.get(stream.timestamp_column)
        if not isinstance(ts, dt.datetime):
            ts = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).replace(tzinfo=None)
        try:
            last_block = int(last.get(stream.block_column) or 0)
        except (TypeError, ValueError):
            last_block = 0
        upsert_cursor(
            ch,
            stream_id=stream.id,
            last_cursor=_cursor_payload(stream, last),
            last_block=last_block,
            last_timestamp=ts,
            last_nats_sequence=last_ack_seq,
        )
        return {"status": "OK", "stream_id": stream.id, "rows": len(rows), "messages": messages}
    finally:
        await client.close()
