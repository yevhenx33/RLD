"""Astrid v2 snapshot exporter — per-market Parquet files.

Exports data from ClickHouse using native FORMAT Parquet (C++ path,
zero Python in the hot loop).  Produces one Parquet file per market
(protocol + entity_id) and a manifest.json with SHA-256 checksums.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any


def _ch_url() -> str:
    host = os.getenv("CLICKHOUSE_HOST", "localhost")
    port = os.getenv("CLICKHOUSE_PORT", "8123")
    return f"http://{host}:{port}"


def _ch_auth() -> str:
    user = os.getenv("CLICKHOUSE_USER", "default")
    pw = os.getenv("CLICKHOUSE_PASSWORD", "")
    return base64.b64encode(f"{user}:{pw}".encode()).decode()


def _ch_get(query: str, *, settings: dict[str, str] | None = None) -> bytes:
    """Execute a ClickHouse query via HTTP GET, return raw bytes."""
    url = f"{_ch_url()}/?query={urllib.request.quote(query)}"
    if settings:
        for k, v in settings.items():
            url += f"&{k}={v}"
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {_ch_auth()}"})
    return urllib.request.urlopen(req).read()


def discover_markets(source_table: str) -> list[dict[str, str]]:
    """Discover unique (protocol, entity_id, symbol) triples in source table."""
    query = (
        f"SELECT DISTINCT protocol, entity_id, symbol "
        f"FROM {source_table} "
        f"ORDER BY protocol, symbol "
        f"FORMAT JSONCompactEachRow"
    )
    data = _ch_get(query).decode().strip()
    if not data:
        return []
    return [
        {"protocol": row[0], "entity_id": row[1], "symbol": row[2]}
        for row in (json.loads(line) for line in data.split("\n") if line.strip())
    ]


def export_market_parquet(
    source_table: str,
    market: dict[str, str],
    out_dir: Path,
    *,
    compress: str = "zstd",
) -> dict[str, Any]:
    """Export one market to a Parquet file. Returns partition metadata."""
    proto = market["protocol"]
    entity_id = market["entity_id"]
    symbol = market["symbol"]
    eid_short = hashlib.sha256(entity_id.encode()).hexdigest()[:8]
    safe_name = f"{proto}__{symbol}__{eid_short}".replace("/", "_").replace(" ", "_").replace("-", "_")

    query = (
        f"SELECT * FROM {source_table} "
        f"WHERE protocol='{proto}' AND entity_id='{entity_id}' "
        f"ORDER BY timestamp "
        f"FORMAT Parquet"
    )
    data = _ch_get(query, settings={"output_format_parquet_compression_method": compress})
    if len(data) < 100:
        return {"symbol": symbol, "rows": 0, "bytes": 0, "skipped": True}

    filename = f"{safe_name}.parquet"
    path = out_dir / filename
    path.write_bytes(data)

    sha = hashlib.sha256(data).hexdigest()
    # Row count from Parquet metadata (no Python row parsing)
    try:
        import pyarrow.parquet as pq
        row_count = pq.read_metadata(str(path)).num_rows
    except Exception:
        row_count = -1

    return {
        "protocol": proto,
        "entity_id": entity_id,
        "symbol": symbol,
        "filename": filename,
        "sha256": sha,
        "bytes": len(data),
        "rows": row_count,
    }


def export_full_snapshot(
    source_table: str,
    out_dir: Path,
    *,
    compress: str = "zstd",
) -> dict[str, Any]:
    """Export entire table as a single Parquet file."""
    query = f"SELECT * FROM {source_table} ORDER BY protocol, entity_id, timestamp FORMAT Parquet"
    data = _ch_get(query, settings={"output_format_parquet_compression_method": compress})

    out_dir.mkdir(parents=True, exist_ok=True)
    filename = "all_markets.parquet"
    path = out_dir / filename
    path.write_bytes(data)

    sha = hashlib.sha256(data).hexdigest()
    try:
        import pyarrow.parquet as pq
        row_count = pq.read_metadata(str(path)).num_rows
    except Exception:
        row_count = -1

    return {
        "filename": filename,
        "sha256": sha,
        "bytes": len(data),
        "rows": row_count,
    }


def export_snapshot(
    source_table: str,
    out_dir: str | Path,
    *,
    compress: str = "zstd",
    workers: int = 8,
) -> dict[str, Any]:
    """Export per-market Parquet snapshots + manifest.json.

    Returns manifest dict.  All heavy lifting is done by ClickHouse C++
    (FORMAT Parquet + zstd).  Python only orchestrates HTTP requests.
    """
    out = Path(out_dir)
    markets_dir = out / "markets"
    markets_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    markets = discover_markets(source_table)
    t_discover = time.time() - t0

    # Export per-market in parallel
    t0 = time.time()

    def _export(market: dict[str, str]) -> dict[str, Any]:
        return export_market_parquet(source_table, market, markets_dir, compress=compress)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        partitions = list(pool.map(_export, markets))

    partitions = [p for p in partitions if not p.get("skipped")]
    t_export = time.time() - t0

    # Also export the full file for bulk-download users
    t0 = time.time()
    full_info = export_full_snapshot(source_table, out, compress=compress)
    t_full = time.time() - t0

    total_bytes = sum(p["bytes"] for p in partitions)
    total_rows = sum(p["rows"] for p in partitions if p["rows"] > 0)

    manifest = {
        "version": 2,
        "source_table": source_table,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "compression": compress,
        "full_snapshot": full_info,
        "markets": partitions,
        "stats": {
            "market_count": len(partitions),
            "total_rows": total_rows,
            "total_bytes": total_bytes,
            "discover_ms": round(t_discover * 1000),
            "export_ms": round(t_export * 1000),
            "full_export_ms": round(t_full * 1000),
        },
    }

    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
