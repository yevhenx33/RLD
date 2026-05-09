"""ClickHouse helpers for Astrid Node."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Iterable

from astrid_node.config import NodeConfig

_QUALIFIED_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")


def validate_qualified_identifier(value: str) -> str:
    if not _QUALIFIED_IDENTIFIER_RE.match(value):
        raise ValueError(f"expected database.table identifier, got {value!r}")
    return value


def client_from_config(config: NodeConfig):
    import clickhouse_connect

    return clickhouse_connect.get_client(
        host=config.clickhouse_host,
        port=config.clickhouse_port,
        username=config.clickhouse_user,
        password=config.clickhouse_password,
    )


def export_query(ch, table: str, *, output_format: str, out: str) -> str:
    table = validate_qualified_identifier(table)
    fmt = {"csv": "CSVWithNames", "parquet": "Parquet"}[output_format]
    query = f"SELECT * FROM {table} FORMAT {fmt}"
    data = ch.raw_query(query)
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def insert_json_payloads(ch, table: str, payloads: Iterable[str], batch_size: int = 1000) -> int:
    table = validate_qualified_identifier(table)
    batch: list[list[str]] = []
    written = 0
    for payload in payloads:
        message_id = _message_id_from_payload(payload)
        batch.append([message_id, payload])
        if len(batch) >= batch_size:
            ch.insert(table, batch, column_names=["message_id", "payload_json"])
            written += len(batch)
            batch.clear()
    if batch:
        ch.insert(table, batch, column_names=["message_id", "payload_json"])
        written += len(batch)
    return written


def _message_id_from_payload(payload: str) -> str:
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError:
        decoded = {}
    value = decoded.get("message_id") or decoded.get("messageId")
    if value:
        return str(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
