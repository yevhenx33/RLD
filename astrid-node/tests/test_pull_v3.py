import hashlib
import json
from pathlib import Path

import pytest

from astrid_node.pull import cache_status, local_parquet_files, pull_streams


def _write_parquet(path: Path, rows: list[dict]):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size


def test_pull_v3_downloads_base_and_delta_and_queries(tmp_path):
    pytest.importorskip("duckdb")
    from astrid_node.duckdb_mode import query_parquet_files

    remote = tmp_path / "remote" / "v2"
    cache = tmp_path / "cache"
    stream_id = "aave.processed.market_timeseries.v1"
    base_path = remote / "base" / stream_id / "20260510T180000Z" / "data.parquet"
    delta_path = remote / "deltas" / stream_id / "2026" / "05" / "10" / "18" / "59" / "15" / "data.parquet"
    base_sha, base_size = _write_parquet(base_path, [{"timestamp": "2026-05-10 18:00:00", "protocol": "AAVE_MARKET", "entity_id": "weth", "symbol": "WETH", "borrow_apy": 0.03}])
    delta_sha, delta_size = _write_parquet(delta_path, [{"timestamp": "2026-05-10 18:59:15", "protocol": "AAVE_MARKET", "entity_id": "weth", "symbol": "WETH", "borrow_apy": 0.04}])
    manifest = {
        "product": "astrid",
        "version": 3,
        "generated_at": "2026-05-10T18:59:15Z",
        "prefix": "v2",
        "base_url": remote.as_uri(),
        "stats": {"max_timestamp": "2026-05-10 18:59:15", "stream_count": 1, "total_rows": 2},
        "streams": [
            {
                "id": stream_id,
                "identity_columns": ["protocol", "entity_id", "timestamp"],
                "cursor_column": "timestamp",
                "timestamp_column": "timestamp",
                "base": {
                    "kind": "base",
                    "base_id": "20260510T180000Z",
                    "object_key": "v2/base/aave.processed.market_timeseries.v1/20260510T180000Z/data.parquet",
                    "url": base_path.as_uri(),
                    "sha256": base_sha,
                    "rows": 1,
                    "bytes": base_size,
                    "max_timestamp": "2026-05-10 18:00:00",
                },
                "deltas": [
                    {
                        "kind": "delta",
                        "base_id": "20260510T180000Z",
                        "delta_id": "20260510T185915Z",
                        "object_key": "v2/deltas/aave.processed.market_timeseries.v1/2026/05/10/18/59/15/data.parquet",
                        "url": delta_path.as_uri(),
                        "sha256": delta_sha,
                        "rows": 1,
                        "bytes": delta_size,
                        "max_timestamp": "2026-05-10 18:59:15",
                    }
                ],
            }
        ],
    }
    (remote / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = pull_streams([stream_id], symbols=["WETH"], base_url=remote.as_uri(), data_dir=cache)
    assert result["downloaded"] == 2
    assert result["errors"] == []
    files, cached_manifest, stream_manifest = local_parquet_files(data_dir=cache, stream_ids=[stream_id])
    assert len(files) == 2
    assert cached_manifest["version"] == 3
    rows = query_parquet_files(files, "SELECT count(*) AS n, avg(borrow_apy) AS avg_borrow FROM data", stream_manifest=stream_manifest)
    assert rows == [{"n": 2, "avg_borrow": 0.035}]
    status = cache_status(cache)
    assert status["files"] == 2
    assert status["missing"] == 0


def _manifest(remote: Path, stream_id: str, file_path: Path, sha: str, size: int, *, generated_at: str = "2026-05-10T18:59:15Z") -> dict:
    return {
        "product": "astrid",
        "version": 3,
        "generated_at": generated_at,
        "prefix": "v2",
        "base_url": remote.as_uri(),
        "stats": {"max_timestamp": "2026-05-10 18:59:15", "stream_count": 1, "total_rows": 1},
        "streams": [
            {
                "id": stream_id,
                "identity_columns": ["protocol", "entity_id"],
                "cursor_column": "timestamp",
                "timestamp_column": "timestamp",
                "base": {
                    "kind": "base",
                    "base_id": "20260510T180000Z",
                    "object_key": f"v2/base/{stream_id}/20260510T180000Z/data.parquet",
                    "url": file_path.as_uri(),
                    "sha256": sha,
                    "rows": 1,
                    "bytes": size,
                    "max_timestamp": "2026-05-10 18:59:15",
                },
                "deltas": [],
            }
        ],
    }


def test_pull_v3_checksum_failure_does_not_publish_partial_file(tmp_path):
    remote = tmp_path / "remote" / "v2"
    cache = tmp_path / "cache"
    stream_id = "aave.processed.market_latest.v1"
    file_path = remote / "base" / stream_id / "20260510T180000Z" / "data.parquet"
    _sha, size = _write_parquet(file_path, [{"timestamp": "2026-05-10 18:59:15", "protocol": "AAVE_MARKET", "entity_id": "weth"}])
    manifest = _manifest(remote, stream_id, file_path, "0" * 64, size)
    (remote / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = pull_streams([stream_id], base_url=remote.as_uri(), data_dir=cache)
    assert result["downloaded"] == 0
    assert result["errors"]
    files, _, _ = local_parquet_files(data_dir=cache, stream_ids=[stream_id])
    assert files == []


def test_pull_v3_missing_object_and_stale_manifest_status(tmp_path):
    remote = tmp_path / "remote" / "v2"
    cache = tmp_path / "cache"
    stream_id = "aave.processed.market_latest.v1"
    missing = remote / "base" / stream_id / "20260510T180000Z" / "missing.parquet"
    manifest = _manifest(remote, stream_id, missing, "0" * 64, 10, generated_at="2020-01-01T00:00:00Z")
    remote.mkdir(parents=True)
    (remote / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = pull_streams([stream_id], base_url=remote.as_uri(), data_dir=cache)
    assert result["downloaded"] == 0
    assert result["errors"]
    status = cache_status(cache, now=__import__("datetime").datetime(2026, 5, 10, tzinfo=__import__("datetime").timezone.utc))
    assert status["missing"] == 1
    assert status["manifest"]["stale"] is True

