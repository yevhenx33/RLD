"""E2E test: Astrid v2 snapshot pipeline using real Aave data.

Validates the full export → verify → import → query pipeline against
the live market_timeseries table on the local ClickHouse instance.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import time
import urllib.request
from pathlib import Path

import pytest

# ── Fixtures ──

CH_SRC_HOST = os.getenv("CLICKHOUSE_HOST", "127.0.0.1")
CH_SRC_PORT = os.getenv("CLICKHOUSE_PORT", "8123")
CH_SRC_USER = os.getenv("CLICKHOUSE_USER", "default")
CH_SRC_PW = os.getenv("CLICKHOUSE_PASSWORD", "")

CH_NODE_HOST = os.getenv("ASTRID_CH_HOST", "127.0.0.1")
CH_NODE_PORT = os.getenv("ASTRID_CH_PORT", "8124")
CH_NODE_USER = os.getenv("ASTRID_CH_USER", "astrid")
CH_NODE_PW = os.getenv("ASTRID_CH_PASS", "astrid")

TEST_DIR = Path("/tmp/astrid_e2e_test")
SOURCE_TABLE = "market_timeseries"


def src_auth():
    return base64.b64encode(f"{CH_SRC_USER}:{CH_SRC_PW}".encode()).decode()


def node_auth():
    return base64.b64encode(f"{CH_NODE_USER}:{CH_NODE_PW}".encode()).decode()


def ch_src_url():
    return f"http://{CH_SRC_HOST}:{CH_SRC_PORT}"


def ch_node_url():
    return f"http://{CH_NODE_HOST}:{CH_NODE_PORT}"


def ch_query(url, auth, query):
    req = urllib.request.Request(
        f"{url}/",
        data=query.encode("utf-8"),
        headers={"Authorization": f"Basic {auth}"},
        method="POST",
    )
    return urllib.request.urlopen(req).read().decode().strip()


def source_available():
    """Check if source ClickHouse has market_timeseries data."""
    try:
        count = ch_query(ch_src_url(), src_auth(), f"SELECT count() FROM {SOURCE_TABLE} FORMAT TabSeparated")
        return int(count) > 0
    except Exception:
        return False


def node_available():
    """Check if node ClickHouse is reachable."""
    try:
        ch_query(ch_node_url(), node_auth(), "SELECT 1 FORMAT TabSeparated")
        return True
    except Exception:
        return False


requires_source = pytest.mark.skipif(
    not source_available(),
    reason="Source ClickHouse with market_timeseries not available",
)

requires_node = pytest.mark.skipif(
    not node_available(),
    reason="Node ClickHouse (port 8124) not available",
)

try:
    import duckdb
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False


@pytest.fixture(autouse=True)
def clean_test_dir():
    """Ensure clean test directory for each test."""
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    yield
    # Cleanup after test
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)


# ── Tests ──


@requires_source
def test_snapshot_export():
    """Publisher can export per-market Parquet snapshots."""
    os.environ["CLICKHOUSE_HOST"] = CH_SRC_HOST
    os.environ["CLICKHOUSE_PORT"] = CH_SRC_PORT
    os.environ["CLICKHOUSE_USER"] = CH_SRC_USER
    os.environ["CLICKHOUSE_PASSWORD"] = CH_SRC_PW

    from analytics.streams.snapshot import export_snapshot

    t0 = time.time()
    manifest = export_snapshot(SOURCE_TABLE, str(TEST_DIR), workers=8)
    elapsed = time.time() - t0

    # Assertions
    assert manifest["version"] == 2
    assert len(manifest["markets"]) > 0, "No markets exported"
    assert manifest["stats"]["total_rows"] > 0, "No rows exported"

    # Manifest file exists
    manifest_path = TEST_DIR / "manifest.json"
    assert manifest_path.exists()

    # Per-market files exist
    markets_dir = TEST_DIR / "markets"
    parquet_files = list(markets_dir.glob("*.parquet"))
    assert len(parquet_files) == len(manifest["markets"])

    # Full snapshot exists
    assert (TEST_DIR / "all_markets.parquet").exists()

    # SHA-256 checksums are valid
    for m in manifest["markets"][:5]:  # spot-check first 5
        path = markets_dir / m["filename"]
        actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual_sha == m["sha256"], f"Checksum mismatch for {m['filename']}"

    print(f"\n  EXPORT: {len(manifest['markets'])} markets, "
          f"{manifest['stats']['total_rows']:,} rows in {elapsed:.1f}s")


@requires_source
@requires_node
def test_full_pipeline_under_120s():
    """Export → verify → import → query must complete in <120s (1307 markets)."""
    os.environ["CLICKHOUSE_HOST"] = CH_SRC_HOST
    os.environ["CLICKHOUSE_PORT"] = CH_SRC_PORT
    os.environ["CLICKHOUSE_USER"] = CH_SRC_USER
    os.environ["CLICKHOUSE_PASSWORD"] = CH_SRC_PW

    from analytics.streams.snapshot import export_snapshot
    from astrid_node.native_import import sync_from_manifest

    t_total = time.time()

    # Step 1: Export
    manifest = export_snapshot(SOURCE_TABLE, str(TEST_DIR), workers=8)

    # Step 2: Import into node ClickHouse
    data_dir = TEST_DIR / "markets"
    result = sync_from_manifest(
        ch_node_url(), manifest, data_dir,
        target_db="astrid_e2e_test",
        auth=node_auth(),
        workers=16,
    )

    elapsed = time.time() - t_total

    # Step 3: Query to verify
    count = int(ch_query(
        ch_node_url(), node_auth(),
        "SELECT count() FROM astrid_e2e_test.market_timeseries FORMAT TabSeparated"
    ))
    manifest_rows = manifest["stats"]["total_rows"]

    # Cleanup
    ch_query(ch_node_url(), node_auth(), "DROP DATABASE IF EXISTS astrid_e2e_test")

    # Assertions — compare against manifest, not live source (which may gain rows during test)
    assert count == manifest_rows, f"Row count mismatch: imported={count} vs manifest={manifest_rows}"
    assert elapsed < 120.0, f"Pipeline took {elapsed:.1f}s (limit: 120s)"
    assert result["rows_imported"] > 0

    print(f"\n  PIPELINE: {count:,} rows in {elapsed:.1f}s")


@requires_source
@pytest.mark.skipif(not HAS_DUCKDB, reason="DuckDB not installed")
def test_duckdb_zero_import():
    """DuckDB must query 7M-row Parquet in <200ms with zero import step."""
    os.environ["CLICKHOUSE_HOST"] = CH_SRC_HOST
    os.environ["CLICKHOUSE_PORT"] = CH_SRC_PORT
    os.environ["CLICKHOUSE_USER"] = CH_SRC_USER
    os.environ["CLICKHOUSE_PASSWORD"] = CH_SRC_PW

    from analytics.streams.snapshot import export_full_snapshot

    out = TEST_DIR / "duck_test"
    out.mkdir(parents=True)
    full_info = export_full_snapshot(SOURCE_TABLE, out)
    parquet_path = out / full_info["filename"]

    import duckdb

    # Warm up
    con = duckdb.connect()
    con.execute(f"SELECT count(*) FROM '{parquet_path}'").fetchone()

    # Benchmark: aggregation query
    t0 = time.time()
    result = con.execute(f"""
        SELECT date_trunc('day', timestamp) as d,
               avg(borrow_apy)*100 as borrow,
               count(*) as n
        FROM '{parquet_path}'
        WHERE timestamp >= '2026-05-01'
        GROUP BY d ORDER BY d
    """).fetchall()
    t_query = time.time() - t0
    con.close()

    assert t_query < 0.2, f"DuckDB query took {t_query*1000:.0f}ms (limit: 200ms)"
    assert len(result) > 0, "No results from DuckDB query"

    print(f"\n  DUCKDB: {len(result)} rows in {t_query*1000:.0f}ms")


@requires_source
@pytest.mark.skipif(not HAS_DUCKDB, reason="DuckDB not installed")
def test_duckdb_per_market_query():
    """Per-market Parquet query must complete in <50ms."""
    os.environ["CLICKHOUSE_HOST"] = CH_SRC_HOST
    os.environ["CLICKHOUSE_PORT"] = CH_SRC_PORT
    os.environ["CLICKHOUSE_USER"] = CH_SRC_USER
    os.environ["CLICKHOUSE_PASSWORD"] = CH_SRC_PW

    from analytics.streams.snapshot import discover_markets, export_market_parquet

    markets = discover_markets(SOURCE_TABLE)
    # Find a WETH market
    weth_markets = [m for m in markets if m["symbol"] == "WETH"]
    assert weth_markets, "No WETH market found"

    out = TEST_DIR / "per_market"
    out.mkdir(parents=True)
    info = export_market_parquet(SOURCE_TABLE, weth_markets[0], out)

    import duckdb
    path = out / info["filename"]

    con = duckdb.connect()
    # Warm up
    con.execute(f"SELECT count(*) FROM '{path}'").fetchone()

    # Benchmark
    t0 = time.time()
    result = con.execute(f"""
        SELECT date_trunc('day', timestamp) as d,
               avg(borrow_apy)*100 as borrow,
               avg(supply_apy)*100 as supply,
               count(*) as n
        FROM '{path}'
        WHERE timestamp >= '2026-05-01'
        GROUP BY d ORDER BY d
    """).fetchall()
    t_query = time.time() - t0
    con.close()

    assert t_query < 0.05, f"Per-market query took {t_query*1000:.0f}ms (limit: 50ms)"
    assert len(result) > 0

    print(f"\n  PER-MARKET: {info['symbol']} ({info['rows']} rows) "
          f"query={t_query*1000:.0f}ms file={info['bytes']/1024:.0f}KB")


@requires_source
@pytest.mark.skipif(not HAS_DUCKDB, reason="DuckDB not installed")
def test_duckdb_mode_api():
    """Test the duckdb_mode.py public API."""
    os.environ["CLICKHOUSE_HOST"] = CH_SRC_HOST
    os.environ["CLICKHOUSE_PORT"] = CH_SRC_PORT
    os.environ["CLICKHOUSE_USER"] = CH_SRC_USER
    os.environ["CLICKHOUSE_PASSWORD"] = CH_SRC_PW

    from analytics.streams.snapshot import export_full_snapshot
    from astrid_node.duckdb_mode import query_parquet, describe_parquet, export_csv

    out = TEST_DIR / "api_test"
    out.mkdir(parents=True)
    full_info = export_full_snapshot(SOURCE_TABLE, out)
    parquet_path = str(out / full_info["filename"])

    # describe
    info = describe_parquet(parquet_path)
    assert info["rows"] > 0
    assert len(info["columns"]) > 5

    # query
    rows = query_parquet(parquet_path, "SELECT count(*) as cnt FROM data")
    assert rows[0]["cnt"] == info["rows"]

    # filtered query
    rows = query_parquet(
        parquet_path,
        "SELECT avg(borrow_apy)*100 as avg_borrow FROM data WHERE symbol='WETH'"
    )
    assert rows[0]["avg_borrow"] is not None

    # CSV export
    csv_path = str(out / "test.csv")
    result = export_csv(
        parquet_path,
        "SELECT timestamp, borrow_apy FROM data WHERE symbol='WETH' ORDER BY timestamp LIMIT 100",
        csv_path,
    )
    assert result["bytes"] > 0
    assert Path(csv_path).exists()

    print(f"\n  API: {info['rows']:,} rows, {len(info['columns'])} cols, CSV={result['bytes']} bytes")


@requires_source
@requires_node
def test_query_results_match_source():
    """avg(borrow_apy) for WETH must match source ClickHouse within epsilon."""
    os.environ["CLICKHOUSE_HOST"] = CH_SRC_HOST
    os.environ["CLICKHOUSE_PORT"] = CH_SRC_PORT
    os.environ["CLICKHOUSE_USER"] = CH_SRC_USER
    os.environ["CLICKHOUSE_PASSWORD"] = CH_SRC_PW

    from analytics.streams.snapshot import export_snapshot
    from astrid_node.native_import import sync_from_manifest

    manifest = export_snapshot(SOURCE_TABLE, str(TEST_DIR), workers=8)
    data_dir = TEST_DIR / "markets"
    sync_from_manifest(
        ch_node_url(), manifest, data_dir,
        target_db="astrid_e2e_verify",
        auth=node_auth(),
        workers=16,
    )

    # Source value
    src_avg = float(ch_query(
        ch_src_url(), src_auth(),
        f"SELECT avg(borrow_apy) FROM {SOURCE_TABLE} WHERE symbol='WETH' FORMAT TabSeparated"
    ))

    # Imported value
    node_avg = float(ch_query(
        ch_node_url(), node_auth(),
        "SELECT avg(borrow_apy) FROM astrid_e2e_verify.market_timeseries WHERE symbol='WETH' FORMAT TabSeparated"
    ))

    # Cleanup
    ch_query(ch_node_url(), node_auth(), "DROP DATABASE IF EXISTS astrid_e2e_verify")

    assert abs(src_avg - node_avg) < 0.001, f"avg(borrow_apy) mismatch: src={src_avg} vs node={node_avg}"
    print(f"\n  VERIFY: WETH avg(borrow_apy) src={src_avg:.6f} node={node_avg:.6f}")
