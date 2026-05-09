"""Canonical Astrid message envelope helpers."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

from analytics.streams.registry import StreamDefinition


def _json_default(value: Any) -> str:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def normalize_timestamp(value: Any) -> str:
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    text = str(value)
    return text if text.endswith("Z") else text.replace("+00:00", "Z")


def identity_hash(stream: StreamDefinition, row: dict[str, Any]) -> str:
    identity = {column: row.get(column) for column in stream.identity_columns}
    payload = json.dumps(identity, sort_keys=True, default=_json_default, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def message_id(stream: StreamDefinition, row: dict[str, Any]) -> str:
    cursor = row.get(stream.cursor_column) or row.get(stream.timestamp_column) or row.get(stream.block_column)
    return f"{stream.id}:{stream.schema_version}:{cursor}:{identity_hash(stream, row)}"


def envelope(stream: StreamDefinition, rows: list[dict[str, Any]], processor_version: str = "dev") -> dict[str, Any]:
    first = rows[0] if rows else {}
    block_number = first.get(stream.block_column) or first.get(stream.cursor_column) or 0
    try:
        block_number_int = int(block_number or 0)
    except (TypeError, ValueError):
        block_number_int = 0
    block_timestamp = first.get(stream.timestamp_column)
    return {
        "message_id": message_id(stream, first) if first else "",
        "schema": stream.schema,
        "stream_id": stream.id,
        "protocol": stream.protocol,
        "deployment_id": stream.deployment_id,
        "table": stream.source_table,
        "op": "insert",
        "block_number": block_number_int,
        "block_timestamp": normalize_timestamp(block_timestamp) if block_timestamp is not None else None,
        "schema_version": stream.schema_version,
        "schema_hash": stream.schema_hash,
        "processor_version": processor_version,
        "rows": rows,
    }


def encode_envelope(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, default=_json_default, separators=(",", ":")).encode("utf-8")
