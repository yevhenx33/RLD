import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analytics.streams.r2_delta import hourly_base_id, manifest_from_files
from analytics.streams.registry import load_registry


def test_hourly_base_id_rounds_to_top_of_hour():
    value = dt.datetime(2026, 5, 10, 18, 52, 13, tzinfo=dt.UTC)
    assert hourly_base_id(value) == "20260510T180000Z"


def test_manifest_v3_selects_latest_base_and_matching_deltas():
    stream = next(item for item in load_registry() if item.id == "aave.processed.market_timeseries.v1")
    files = [
        {
            "stream_id": stream.id,
            "kind": "base",
            "base_id": "20260510T170000Z",
            "delta_id": "",
            "object_key": "v2/base/a/old/data.parquet",
            "url": "https://example.test/v2/base/a/old/data.parquet",
            "schema_hash": stream.schema_hash,
            "rows": 10,
            "bytes": 100,
            "sha256": "a",
            "min_timestamp": "2026-05-10 17:00:00",
            "max_timestamp": "2026-05-10 17:59:00",
            "min_cursor": "1",
            "max_cursor": "10",
            "last_cursor": "old",
            "created_at": "2026-05-10 17:59:00",
        },
        {
            "stream_id": stream.id,
            "kind": "base",
            "base_id": "20260510T180000Z",
            "delta_id": "",
            "object_key": "v2/base/a/new/data.parquet",
            "url": "https://example.test/v2/base/a/new/data.parquet",
            "schema_hash": stream.schema_hash,
            "rows": 20,
            "bytes": 200,
            "sha256": "b",
            "min_timestamp": "2026-05-10 18:00:00",
            "max_timestamp": "2026-05-10 18:59:00",
            "min_cursor": "11",
            "max_cursor": "30",
            "last_cursor": "new",
            "created_at": "2026-05-10 18:59:00",
        },
        {
            "stream_id": stream.id,
            "kind": "delta",
            "base_id": "20260510T180000Z",
            "delta_id": "20260510T185915Z",
            "object_key": "v2/deltas/a/data.parquet",
            "url": "https://example.test/v2/deltas/a/data.parquet",
            "schema_hash": stream.schema_hash,
            "rows": 2,
            "bytes": 20,
            "sha256": "c",
            "min_timestamp": "2026-05-10 18:59:01",
            "max_timestamp": "2026-05-10 18:59:15",
            "min_cursor": "31",
            "max_cursor": "32",
            "last_cursor": "delta",
            "created_at": "2026-05-10 18:59:15",
        },
    ]
    manifest = manifest_from_files([stream], files, prefix="v2", public_base_url="https://example.test")
    entry = manifest["streams"][0]
    assert manifest["version"] == 3
    assert entry["base"]["base_id"] == "20260510T180000Z"
    assert len(entry["deltas"]) == 1
    assert entry["stats"]["rows"] == 22


def test_manifest_v3_filters_files_by_prefix():
    stream = next(item for item in load_registry() if item.id == "aave.processed.market_timeseries.v1")
    base = {
        "prefix": "v2",
        "stream_id": stream.id,
        "kind": "base",
        "base_id": "20260510T180000Z",
        "delta_id": "",
        "object_key": "v2/base/a/live/data.parquet",
        "url": "https://example.test/v2/base/a/live/data.parquet",
        "schema_hash": stream.schema_hash,
        "rows": 20,
        "bytes": 200,
        "sha256": "b",
        "min_timestamp": "2026-05-10 18:00:00",
        "max_timestamp": "2026-05-10 18:59:00",
        "min_cursor": "11",
        "max_cursor": "30",
        "last_cursor": "live",
        "created_at": "2026-05-10 18:59:00",
    }
    e2e = {**base, "prefix": "v2-e2e/run", "base_id": "20260510T190000Z", "object_key": "v2-e2e/run/base/a/data.parquet", "last_cursor": "e2e"}
    manifest = manifest_from_files([stream], [base, e2e], prefix="v2", public_base_url="https://example.test")
    assert manifest["streams"][0]["base"]["base_id"] == "20260510T180000Z"
    assert manifest["stats"]["total_rows"] == 20

