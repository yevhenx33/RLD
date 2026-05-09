"""Astrid stream manifest and install helpers."""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astrid_node.clickhouse import validate_qualified_identifier


@dataclass(frozen=True)
class Stream:
    id: str
    subject: str
    local_table: str
    schema_version: str
    schema_hash: str
    manifest: dict[str, Any]
    chunks: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_manifest(cls, row: dict[str, Any]) -> "Stream":
        return cls(
            id=row["id"],
            subject=row["subject"],
            local_table=row["local_table"],
            schema_version=row["schema_version"],
            schema_hash=row.get("schema_hash", ""),
            manifest=row,
            chunks=tuple(row.get("chunks") or ()),
        )


def parse_manifest(payload: str | bytes | dict[str, Any]) -> list[Stream]:
    if isinstance(payload, (str, bytes)):
        payload = json.loads(payload)
    return [Stream.from_manifest(row) for row in payload.get("streams", [])]


def load_manifest(source: str) -> list[Stream]:
    if source.startswith(("http://", "https://")):
        request = urllib.request.Request(source, headers={"User-Agent": "Astrid-Node/0.1"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return parse_manifest(response.read())
    return parse_manifest(Path(source).read_text(encoding="utf-8"))


def install_plan(stream: Stream) -> dict[str, Any]:
    validate_qualified_identifier(stream.local_table)
    database, _, table = stream.local_table.partition(".")
    if not database or not table:
        raise ValueError(f"local_table must include database.table: {stream.local_table}")
    return {
        "stream_id": stream.id,
        "create": [
            f"CREATE DATABASE IF NOT EXISTS {database}",
            f"CREATE TABLE IF NOT EXISTS {stream.local_table} (message_id String, payload_json String, inserted_at DateTime DEFAULT now()) ENGINE = ReplacingMergeTree(inserted_at) ORDER BY message_id",
            "INSERT INTO astrid_meta.installed_streams (stream_id, local_table) VALUES",
        ],
        "will_not_modify": ["existing canonical stream tables", "unrelated astrid_user tables"],
    }


def install_stream(ch, stream: Stream, *, dry_run: bool = False) -> dict[str, Any]:
    plan = install_plan(stream)
    if dry_run:
        return {"status": "DRY_RUN", "plan": plan}
    database = stream.local_table.split(".", 1)[0]
    ch.command(f"CREATE DATABASE IF NOT EXISTS {database}")
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {stream.local_table} (
            message_id String,
            payload_json String,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        ORDER BY message_id
        """
    )
    ch.insert(
        "astrid_meta.installed_streams",
        [[stream.id, stream.local_table]],
        column_names=["stream_id", "local_table"],
    )
    ch.insert(
        "astrid_meta.stream_registry",
        [[stream.id, stream.subject, stream.schema_version, stream.schema_hash, json.dumps(stream.manifest, sort_keys=True)]],
        column_names=["stream_id", "subject", "schema_version", "schema_hash", "manifest_json"],
    )
    # Create typed view if column definitions are present in the manifest
    view = None
    try:
        from astrid_node.views import install_view
        view = install_view(ch, stream)
    except Exception:
        pass  # View creation is optional — don't fail install if it errors
    result: dict[str, Any] = {"status": "INSTALLED", "stream_id": stream.id, "local_table": stream.local_table}
    if view:
        result["typed_view"] = view
    return result


def find_stream(streams: list[Stream], stream_id: str) -> Stream:
    for stream in streams:
        if stream.id == stream_id:
            return stream
    valid = ", ".join(stream.id for stream in streams)
    raise ValueError(f"Unknown stream {stream_id}. Valid streams: {valid}")
