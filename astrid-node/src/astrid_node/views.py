"""Typed ClickHouse view generation for Astrid Node stream tables.

Generates CREATE VIEW DDL that extracts typed columns from the
schema-stable payload_json storage, giving users clean SQL access
without manual JSON_EXTRACT calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from astrid_node.streams import Stream


@dataclass(frozen=True)
class ColumnDef:
    name: str
    type: str  # ClickHouse type: String, Float64, DateTime, UInt64, etc.


# Map from ClickHouse type to the JSONExtract function variant
_CH_TYPE_TO_EXTRACT = {
    "String": "JSONExtractString",
    "Float64": "JSONExtractFloat",
    "Float32": "JSONExtractFloat",
    "UInt8": "JSONExtractUInt",
    "UInt16": "JSONExtractUInt",
    "UInt32": "JSONExtractUInt",
    "UInt64": "JSONExtractUInt",
    "Int8": "JSONExtractInt",
    "Int16": "JSONExtractInt",
    "Int32": "JSONExtractInt",
    "Int64": "JSONExtractInt",
    "DateTime": "JSONExtractString",  # Parse with parseDateTimeBestEffort
}


def _extract_expr(column: ColumnDef) -> str:
    """Generate the JSONExtract expression for a single column."""
    extractor = _CH_TYPE_TO_EXTRACT.get(column.type, "JSONExtractString")
    path = f"payload_json, 'rows', 1, '{column.name}'"

    if column.type == "DateTime":
        return f"parseDateTimeBestEffort({extractor}({path}))"
    if column.type.startswith("LowCardinality("):
        inner = column.type[len("LowCardinality("):-1]
        inner_extractor = _CH_TYPE_TO_EXTRACT.get(inner, "JSONExtractString")
        return f"{inner_extractor}({path})"

    return f"{extractor}({path})"


def parse_columns(manifest: dict[str, Any]) -> tuple[ColumnDef, ...]:
    """Parse column definitions from a stream manifest's 'columns' field."""
    raw = manifest.get("columns")
    if not raw or not isinstance(raw, list):
        return ()
    result = []
    for item in raw:
        if isinstance(item, dict):
            name = item.get("name", "")
            col_type = item.get("type", "String")
        elif isinstance(item, str) and ":" in item:
            name, col_type = item.split(":", 1)
            name = name.strip()
            col_type = col_type.strip()
        else:
            continue
        if name:
            result.append(ColumnDef(name=name, type=col_type))
    return tuple(result)


def view_name(local_table: str) -> str:
    """Generate the typed view name from a stream's local_table.

    astrid_aave.account_profiles -> astrid_aave.account_profiles_v
    """
    return f"{local_table}_v"


def create_view_ddl(stream: Stream, columns: tuple[ColumnDef, ...]) -> str:
    """Generate CREATE OR REPLACE VIEW DDL for typed access."""
    if not columns:
        return ""

    vname = view_name(stream.local_table)
    select_parts = []
    for col in columns:
        expr = _extract_expr(col)
        select_parts.append(f"    {expr} AS {col.name}")

    select_clause = ",\n".join(select_parts)
    return (
        f"CREATE OR REPLACE VIEW {vname} AS\n"
        f"SELECT\n"
        f"{select_clause}\n"
        f"FROM {stream.local_table} FINAL"
    )


def install_view(ch, stream: Stream) -> str | None:
    """Create a typed view for a stream if column definitions are present.

    Returns the view name if created, None otherwise.
    """
    columns = parse_columns(stream.manifest)
    if not columns:
        return None
    ddl = create_view_ddl(stream, columns)
    if not ddl:
        return None
    ch.command(ddl)
    return view_name(stream.local_table)
