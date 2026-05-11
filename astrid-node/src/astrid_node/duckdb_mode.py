"""DuckDB zero-import mode — query Parquet files directly.

No ClickHouse required.  No Docker.  No credentials.  Just a Parquet
file and ``pip install duckdb``.

Agent-optimised: a single Python script can run multiple queries on
local data in one tool-call, eliminating the 3-second-per-call overhead
that dominates LLM agent analysis workflows.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any


def _ensure_duckdb():
    try:
        import duckdb  # noqa: F401
        return duckdb
    except ImportError:
        raise ImportError(
            "DuckDB is required for zero-import mode.  Install with: pip install duckdb"
        )


def query_parquet(
    parquet_path: str | Path,
    sql: str,
    *,
    alias: str = "data",
) -> list[dict[str, Any]]:
    """Run SQL against a Parquet file.  Returns list of dicts.

    The Parquet file is referenced as ``{alias}`` in your SQL.
    Example::

        query_parquet("weth.parquet", "SELECT avg(borrow_apy) FROM data")
    """
    duckdb = _ensure_duckdb()
    con = duckdb.connect()
    path = str(Path(parquet_path).resolve())
    con.execute(f"CREATE VIEW {alias} AS SELECT * FROM read_parquet('{path}')")
    result = con.execute(sql)
    columns = [d[0] for d in result.description]
    rows = result.fetchall()
    con.close()
    return [dict(zip(columns, row)) for row in rows]


def query_parquet_glob(
    parquet_glob: str,
    sql: str,
    *,
    alias: str = "data",
) -> list[dict[str, Any]]:
    """Run SQL against multiple Parquet files matched by glob.

    Supports layered base + delta pattern::

        query_parquet_glob("/data/weth/*.parquet",
                          "SELECT * FROM data ORDER BY timestamp DESC LIMIT 10")
    """
    duckdb = _ensure_duckdb()
    con = duckdb.connect()
    con.execute(f"CREATE VIEW {alias} AS SELECT * FROM read_parquet('{parquet_glob}')")
    result = con.execute(sql)
    columns = [d[0] for d in result.description]
    rows = result.fetchall()
    con.close()
    return [dict(zip(columns, row)) for row in rows]


def export_csv(
    parquet_path: str | Path,
    sql: str,
    out_path: str | Path,
    *,
    alias: str = "data",
) -> dict[str, Any]:
    """Export query results to CSV via DuckDB COPY."""
    duckdb = _ensure_duckdb()
    con = duckdb.connect()
    path = str(Path(parquet_path).resolve())
    out = str(Path(out_path).resolve())
    con.execute(f"CREATE VIEW {alias} AS SELECT * FROM read_parquet('{path}')")
    t0 = time.time()
    con.execute(f"COPY ({sql}) TO '{out}' (FORMAT CSV, HEADER)")
    elapsed = time.time() - t0
    con.close()
    return {
        "path": out,
        "bytes": os.path.getsize(out),
        "elapsed_ms": round(elapsed * 1000),
    }


def describe_parquet(parquet_path: str | Path) -> dict[str, Any]:
    """Return schema, row count, and size of a Parquet file."""
    duckdb = _ensure_duckdb()
    path = str(Path(parquet_path).resolve())
    con = duckdb.connect()

    # Schema
    schema_rows = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{path}')"
    ).fetchall()
    columns = [{"name": r[0], "type": r[1]} for r in schema_rows]

    # Count + range
    stats = con.execute(
        f"SELECT count(*), min(timestamp), max(timestamp) FROM read_parquet('{path}')"
    ).fetchone()

    con.close()
    return {
        "path": path,
        "bytes": os.path.getsize(path),
        "rows": stats[0],
        "min_timestamp": str(stats[1]),
        "max_timestamp": str(stats[2]),
        "columns": columns,
    }


def create_persistent_db(
    db_path: str | Path,
    views: dict[str, str],
) -> dict[str, Any]:
    """Create a persistent DuckDB database with named views over Parquet files.

    Args:
        db_path: Path to the .duckdb file to create
        views: Mapping of view_name → parquet glob pattern

    Example::

        create_persistent_db("astrid.duckdb", {
            "weth": "/data/markets/MORPHO_MARKET__WETH.parquet",
            "usdc": "/data/markets/MORPHO_MARKET__USDC.parquet",
        })

    Then query:
        duckdb astrid.duckdb -c "SELECT avg(borrow_apy) FROM weth"
    """
    duckdb = _ensure_duckdb()
    db = Path(db_path)
    if db.exists():
        db.unlink()
    con = duckdb.connect(str(db))
    for name, pattern in views.items():
        con.execute(
            f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{pattern}')"
        )
    con.close()
    return {
        "db_path": str(db),
        "views": list(views.keys()),
        "db_bytes": os.path.getsize(str(db)),
    }


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def query_parquet_files(
    parquet_paths: list[str | Path],
    sql: str,
    *,
    alias: str = "data",
    stream_manifest: dict[str, Any] | None = None,
    dedupe: bool = True,
) -> list[dict[str, Any]]:
    """Run SQL against one or more Astrid Parquet files.

    When a stream manifest is supplied, the visible ``data`` view is de-duped
    by identity columns using the stream cursor/timestamp ordering. This keeps
    hourly bases plus later deltas from double-counting latest/state rows.
    """
    if not parquet_paths:
        return []
    duckdb = _ensure_duckdb()
    con = duckdb.connect()
    paths = [str(Path(path).resolve()) for path in parquet_paths]
    read_arg = ", ".join(_sql_string(path) for path in paths)
    con.execute(f"CREATE VIEW {alias}_raw AS SELECT * FROM read_parquet([{read_arg}], union_by_name=true)")

    created_alias = False
    if dedupe and stream_manifest:
        identity = [str(item) for item in stream_manifest.get("identity_columns", [])]
        cursor = str(stream_manifest.get("cursor_column") or "")
        timestamp = str(stream_manifest.get("timestamp_column") or "")
        columns = {row[1] for row in con.execute(f"PRAGMA table_info('{alias}_raw')").fetchall()}
        if identity and all(column in columns for column in identity):
            order_columns = [column for column in [cursor, timestamp] if column in columns]
            if order_columns:
                partition = ", ".join(identity)
                order = ", ".join(f"{column} DESC" for column in order_columns)
                con.execute(
                    f"CREATE VIEW {alias} AS "
                    f"SELECT * EXCLUDE (_astrid_rn) FROM ("
                    f"SELECT *, row_number() OVER (PARTITION BY {partition} ORDER BY {order}) AS _astrid_rn "
                    f"FROM {alias}_raw) WHERE _astrid_rn = 1"
                )
                created_alias = True
    if not created_alias:
        con.execute(f"CREATE VIEW {alias} AS SELECT * FROM {alias}_raw")

    result = con.execute(sql)
    columns = [d[0] for d in result.description]
    rows = result.fetchall()
    con.close()
    return [dict(zip(columns, row)) for row in rows]
