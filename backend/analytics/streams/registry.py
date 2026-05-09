"""Declarative stream registry for Astrid canonical data streams."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFINITIONS_DIR = Path(__file__).resolve().parent / "definitions"


class StreamRegistryError(ValueError):
    """Raised when stream registry definitions are invalid."""


@dataclass(frozen=True)
class ChunkPolicy:
    partition: str = "month"
    format: str = "parquet"


@dataclass(frozen=True)
class StreamDefinition:
    id: str
    subject: str
    schema: str
    protocol: str
    deployment_id: str
    mode: str
    source_table: str
    local_table: str
    schema_version: str
    identity_columns: tuple[str, ...]
    cursor_column: str
    timestamp_column: str
    block_column: str
    chunk: ChunkPolicy = ChunkPolicy()
    columns: tuple[str, ...] = ()

    @property
    def schema_hash(self) -> str:
        payload = json.dumps(self.to_manifest(include_schema_hash=False), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_manifest(self, include_schema_hash: bool = True) -> dict[str, Any]:
        data = asdict(self)
        data["identity_columns"] = list(self.identity_columns)
        data["columns"] = [{"name": c.split(":", 1)[0], "type": c.split(":", 1)[1]} for c in self.columns if ":" in c]
        if include_schema_hash:
            data["schema_hash"] = self.schema_hash
        return data


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if value[0] in {"'", '"'} and value[-1:] == value[0]:
        return value[1:-1]
    if value in {"[]", "{}"}:
        return [] if value == "[]" else {}
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part.strip()) for part in inner.split(",")]
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    return value


def _load_simple_yaml(path: Path) -> dict[str, Any]:
    """Load the small YAML subset used by stream definitions.

    The project intentionally avoids a PyYAML runtime dependency for this first
    slice. Definitions are limited to top-level scalars, top-level lists, and
    one-level nested mappings.
    """
    result: dict[str, Any] = {}
    current_key: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" "):
            key, sep, value = line.partition(":")
            if not sep:
                raise StreamRegistryError(f"{path}: invalid line {raw!r}")
            key = key.strip()
            value = value.strip()
            if value:
                result[key] = _parse_scalar(value)
                current_key = None
            else:
                result[key] = []
                current_key = key
            continue
        if current_key is None:
            raise StreamRegistryError(f"{path}: nested value without key {raw!r}")
        stripped = line.strip()
        if stripped.startswith("- "):
            if not isinstance(result[current_key], list):
                result[current_key] = []
            result[current_key].append(_parse_scalar(stripped[2:]))
            continue
        nested_key, sep, nested_value = stripped.partition(":")
        if not sep:
            raise StreamRegistryError(f"{path}: invalid nested line {raw!r}")
        if not isinstance(result[current_key], dict):
            result[current_key] = {}
        result[current_key][nested_key.strip()] = _parse_scalar(nested_value.strip())
    return result


def load_definition(path: Path) -> StreamDefinition:
    raw = _load_simple_yaml(path)
    required = {
        "id",
        "subject",
        "schema",
        "protocol",
        "deployment_id",
        "mode",
        "source_table",
        "local_table",
        "schema_version",
        "identity_columns",
        "cursor_column",
        "timestamp_column",
        "block_column",
    }
    missing = sorted(required - set(raw))
    if missing:
        raise StreamRegistryError(f"{path}: missing required keys: {', '.join(missing)}")
    chunk_raw = raw.get("chunk") or {}
    if not isinstance(chunk_raw, dict):
        raise StreamRegistryError(f"{path}: chunk must be a mapping")
    identity = raw["identity_columns"]
    if not isinstance(identity, list) or not identity:
        raise StreamRegistryError(f"{path}: identity_columns must be a non-empty list")
    return StreamDefinition(
        id=str(raw["id"]),
        subject=str(raw["subject"]),
        schema=str(raw["schema"]),
        protocol=str(raw["protocol"]),
        deployment_id=str(raw["deployment_id"]),
        mode=str(raw["mode"]),
        source_table=str(raw["source_table"]),
        local_table=str(raw["local_table"]),
        schema_version=str(raw["schema_version"]),
        identity_columns=tuple(str(item) for item in identity),
        cursor_column=str(raw["cursor_column"]),
        timestamp_column=str(raw["timestamp_column"]),
        block_column=str(raw["block_column"]),
        chunk=ChunkPolicy(
            partition=str(chunk_raw.get("partition", "month")),
            format=str(chunk_raw.get("format", "parquet")),
        ),
        columns=tuple(str(c) for c in raw.get("columns", [])),
    )


def validate_registry(streams: list[StreamDefinition]) -> None:
    ids: set[str] = set()
    subjects: set[str] = set()
    for stream in streams:
        if stream.id in ids:
            raise StreamRegistryError(f"duplicate stream id: {stream.id}")
        if stream.subject in subjects:
            raise StreamRegistryError(f"duplicate stream subject: {stream.subject}")
        if not stream.subject.startswith("astrid.data.v1."):
            raise StreamRegistryError(f"{stream.id}: subject must start with astrid.data.v1.")
        if stream.mode not in {"raw", "processed", "full"}:
            raise StreamRegistryError(f"{stream.id}: invalid mode {stream.mode!r}")
        ids.add(stream.id)
        subjects.add(stream.subject)


def load_registry(definitions_dir: Path = DEFINITIONS_DIR) -> list[StreamDefinition]:
    streams = [load_definition(path) for path in sorted(definitions_dir.glob("*.yaml"))]
    validate_registry(streams)
    return streams


def registry_manifest(streams: list[StreamDefinition] | None = None) -> dict[str, Any]:
    streams = streams if streams is not None else load_registry()
    return {
        "product": "astrid",
        "version": 1,
        "streams": [stream.to_manifest() for stream in streams],
    }
