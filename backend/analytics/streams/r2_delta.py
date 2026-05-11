"""R2 base+delta publisher for Astrid Parquet distribution."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Iterable

from analytics.streams.publisher import _cursor_payload, _order_columns, _where_clause
from analytics.streams.registry import StreamDefinition, load_registry

MANIFEST_VERSION = 3
DEFAULT_PREFIX = "v2"
DATA_CACHE_CONTROL = "public, max-age=31536000, immutable"
MANIFEST_CACHE_CONTROL = "public, max-age=5"


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def hourly_base_id(value: dt.datetime | None = None) -> str:
    value = value or utc_now()
    value = value.astimezone(dt.UTC).replace(minute=0, second=0, microsecond=0)
    return value.strftime("%Y%m%dT%H0000Z")


def delta_id(value: dt.datetime | None = None) -> str:
    value = value or utc_now()
    value = value.astimezone(dt.UTC).replace(microsecond=0)
    return value.strftime("%Y%m%dT%H%M%SZ")


def delta_path(value: dt.datetime | None = None) -> str:
    value = value or utc_now()
    value = value.astimezone(dt.UTC).replace(microsecond=0)
    return value.strftime("%Y/%m/%d/%H/%M/%S")


def _escape_sql(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''")


def _json_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None).isoformat(sep=" ")
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


def _stream_source_where(stream: StreamDefinition, *, last_cursor: str | None = None) -> str:
    return _where_clause(stream, last_cursor=last_cursor)


def _stream_select_query(stream: StreamDefinition, *, last_cursor: str | None = None) -> str:
    where = _stream_source_where(stream, last_cursor=last_cursor)
    order = ", ".join(_order_columns(stream))
    return f"SELECT * FROM {stream.source_table} {where} ORDER BY {order}"


def _stream_count_query(stream: StreamDefinition, *, last_cursor: str | None = None) -> str:
    where = _stream_source_where(stream, last_cursor=last_cursor)
    return f"SELECT count() FROM {stream.source_table} {where}"


def _stream_range_query(stream: StreamDefinition, *, last_cursor: str | None = None) -> str:
    where = _stream_source_where(stream, last_cursor=last_cursor)
    return (
        f"SELECT min({stream.timestamp_column}), max({stream.timestamp_column}), "
        f"min({stream.cursor_column}), max({stream.cursor_column}) "
        f"FROM {stream.source_table} {where}"
    )


def _stream_last_row_query(stream: StreamDefinition, *, last_cursor: str | None = None) -> str:
    where = _stream_source_where(stream, last_cursor=last_cursor)
    order = ", ".join(f"{column} DESC" for column in _order_columns(stream))
    return f"SELECT * FROM {stream.source_table} {where} ORDER BY {order} LIMIT 1"


def _query_first_row(ch, query: str) -> tuple[Any, ...] | None:
    result = ch.query(query)
    rows = getattr(result, "result_rows", [])
    return rows[0] if rows else None


def _query_last_row_dict(ch, stream: StreamDefinition, *, last_cursor: str | None = None) -> dict[str, Any] | None:
    result = ch.query(_stream_last_row_query(stream, last_cursor=last_cursor))
    rows = getattr(result, "result_rows", [])
    columns = getattr(result, "column_names", None) or getattr(result, "columns", None)
    if not rows:
        return None
    if not columns:
        raise RuntimeError("ClickHouse result must expose column_names")
    return dict(zip(columns, rows[0]))


def _public_base_url() -> str:
    public_url = os.getenv("R2_PUBLIC_URL")
    if public_url:
        return public_url.rstrip("/")
    public_domain = os.getenv("R2_PUBLIC_DOMAIN")
    if public_domain:
        return f"https://{public_domain.strip('/')}"
    return "https://astrid.rld.fi"


def _object_url(key: str, *, public_base_url: str | None = None) -> str:
    return f"{(public_base_url or _public_base_url()).rstrip('/')}/{key.lstrip('/')}"


class R2Uploader:
    """Small S3-compatible R2 uploader used by the publisher commands."""

    def __init__(self, *, bucket: str | None = None, public_base_url: str | None = None):
        self.bucket = bucket or os.getenv("R2_BUCKET", "astrid")
        self.public_base_url = (public_base_url or _public_base_url()).rstrip("/")
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import boto3

            endpoint = os.getenv("R2_ENDPOINT")
            if not endpoint:
                account_id = os.getenv("R2_ACCOUNT_ID")
                if account_id:
                    endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
            access_key = os.getenv("R2_ACCESS_KEY_ID")
            secret_key = os.getenv("R2_SECRET_ACCESS_KEY")
            if not endpoint or not access_key or not secret_key:
                raise RuntimeError(
                    "R2 upload requires R2_ENDPOINT or R2_ACCOUNT_ID plus R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY"
                )
            self._client = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name="auto",
            )
        return self._client

    def upload_file(self, path: str | Path, key: str, *, content_type: str, cache_control: str) -> str:
        self.client.upload_file(
            str(path),
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type, "CacheControl": cache_control},
        )
        return _object_url(key, public_base_url=self.public_base_url)

    def upload_json(self, payload: dict[str, Any], key: str, *, cache_control: str = MANIFEST_CACHE_CONTROL) -> str:
        body = json.dumps(payload, indent=2, sort_keys=True, default=str).encode("utf-8")
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            CacheControl=cache_control,
        )
        return _object_url(key, public_base_url=self.public_base_url)

    def delete_prefix(self, prefix: str) -> int:
        deleted = 0
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix.rstrip("/") + "/"}
            if token:
                kwargs["ContinuationToken"] = token
            response = self.client.list_objects_v2(**kwargs)
            objects = [{"Key": item["Key"]} for item in response.get("Contents", [])]
            if objects:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": objects})
                deleted += len(objects)
            if not response.get("IsTruncated"):
                return deleted
            token = response.get("NextContinuationToken")


class LocalUploader:
    """Uploader used by unit tests and local dry runs."""

    def __init__(self, root: str | Path, *, public_base_url: str | None = None):
        self.root = Path(root)
        self.public_base_url = (public_base_url or self.root.resolve().as_uri()).rstrip("/")

    def upload_file(self, path: str | Path, key: str, *, content_type: str, cache_control: str) -> str:
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(path).read_bytes())
        return f"{self.public_base_url}/{key}"

    def upload_json(self, payload: dict[str, Any], key: str, *, cache_control: str = MANIFEST_CACHE_CONTROL) -> str:
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        return f"{self.public_base_url}/{key}"


def ensure_r2_state_tables(ch) -> None:
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS r2_publisher_state (
            prefix String DEFAULT 'v2',
            stream_id String,
            base_id String,
            last_cursor String,
            last_timestamp String,
            last_delta_id String,
            manifest_version UInt64,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY (prefix, stream_id)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS r2_publisher_files (
            prefix String DEFAULT 'v2',
            stream_id String,
            kind LowCardinality(String),
            base_id String,
            delta_id String,
            object_key String,
            url String,
            schema_hash String,
            rows UInt64,
            bytes UInt64,
            sha256 String,
            min_timestamp String,
            max_timestamp String,
            min_cursor String,
            max_cursor String,
            last_cursor String,
            created_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(created_at)
        ORDER BY (prefix, stream_id, kind, base_id, delta_id, object_key)
        """
    )
    ch.command("ALTER TABLE r2_publisher_state ADD COLUMN IF NOT EXISTS prefix String DEFAULT 'v2'")
    ch.command("ALTER TABLE r2_publisher_files ADD COLUMN IF NOT EXISTS prefix String DEFAULT 'v2'")


def _read_state_rows(ch, *, prefix: str = DEFAULT_PREFIX) -> dict[str, dict[str, Any]]:
    ensure_r2_state_tables(ch)
    result = ch.query(
        f"""
        SELECT prefix, stream_id, base_id, last_cursor, last_timestamp, last_delta_id, manifest_version, updated_at
        FROM r2_publisher_state FINAL
        WHERE prefix = '{_escape_sql(prefix.rstrip('/'))}'
        """
    )
    rows = getattr(result, "result_rows", [])
    states: dict[str, dict[str, Any]] = {}
    for row in rows:
        states[str(row[1])] = {
            "prefix": row[0],
            "stream_id": row[1],
            "base_id": row[2],
            "last_cursor": row[3],
            "last_timestamp": row[4],
            "last_delta_id": row[5],
            "manifest_version": row[6],
            "updated_at": _json_value(row[7]),
        }
    return states


def _read_file_rows(ch, *, prefix: str = DEFAULT_PREFIX) -> list[dict[str, Any]]:
    ensure_r2_state_tables(ch)
    result = ch.query(
        f"""
        SELECT prefix, stream_id, kind, base_id, delta_id, object_key, url, schema_hash, rows, bytes, sha256,
               min_timestamp, max_timestamp, min_cursor, max_cursor, last_cursor, created_at
        FROM r2_publisher_files FINAL
        WHERE prefix = '{_escape_sql(prefix.rstrip('/'))}'
        ORDER BY prefix, stream_id, kind, base_id, delta_id, object_key
        """
    )
    items = []
    for row in getattr(result, "result_rows", []):
        items.append(
            {
                "prefix": str(row[0]),
                "stream_id": str(row[1]),
                "kind": str(row[2]),
                "base_id": str(row[3]),
                "delta_id": str(row[4]),
                "object_key": str(row[5]),
                "url": str(row[6]),
                "schema_hash": str(row[7]),
                "rows": int(row[8] or 0),
                "bytes": int(row[9] or 0),
                "sha256": str(row[10]),
                "min_timestamp": str(row[11]),
                "max_timestamp": str(row[12]),
                "min_cursor": str(row[13]),
                "max_cursor": str(row[14]),
                "last_cursor": str(row[15]),
                "created_at": _json_value(row[16]),
            }
        )
    return items


def _record_file(ch, file_info: dict[str, Any]) -> None:
    ch.insert(
        "r2_publisher_files",
        [[
            file_info["prefix"],
            file_info["stream_id"],
            file_info["kind"],
            file_info.get("base_id", ""),
            file_info.get("delta_id", ""),
            file_info["object_key"],
            file_info["url"],
            file_info["schema_hash"],
            int(file_info["rows"]),
            int(file_info["bytes"]),
            file_info["sha256"],
            file_info.get("min_timestamp", ""),
            file_info.get("max_timestamp", ""),
            file_info.get("min_cursor", ""),
            file_info.get("max_cursor", ""),
            file_info.get("last_cursor", ""),
            dt.datetime.now(dt.UTC).replace(tzinfo=None),
        ]],
        column_names=[
            "prefix", "stream_id", "kind", "base_id", "delta_id", "object_key", "url", "schema_hash",
            "rows", "bytes", "sha256", "min_timestamp", "max_timestamp", "min_cursor", "max_cursor",
            "last_cursor", "created_at",
        ],
    )


def _upsert_state(ch, stream_id: str, *, prefix: str, base_id: str, last_cursor: str, last_timestamp: str, last_delta_id: str = "") -> None:
    ch.insert(
        "r2_publisher_state",
        [[prefix, stream_id, base_id, last_cursor, last_timestamp, last_delta_id, MANIFEST_VERSION, dt.datetime.now(dt.UTC).replace(tzinfo=None)]],
        column_names=["prefix", "stream_id", "base_id", "last_cursor", "last_timestamp", "last_delta_id", "manifest_version", "updated_at"],
    )


def _stream_file_key(prefix: str, stream: StreamDefinition, *, kind: str, base_id: str, delta_id_value: str = "", when: dt.datetime | None = None) -> str:
    if kind == "base":
        return f"{prefix.rstrip('/')}/base/{stream.id}/{base_id}/data.parquet"
    return f"{prefix.rstrip('/')}/deltas/{stream.id}/{delta_path(when)}/data.parquet"


def _file_manifest(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": row["kind"],
        "base_id": row.get("base_id", ""),
        "delta_id": row.get("delta_id", ""),
        "object_key": row["object_key"],
        "url": row["url"],
        "schema_hash": row["schema_hash"],
        "rows": row["rows"],
        "bytes": row["bytes"],
        "sha256": row["sha256"],
        "min_timestamp": row.get("min_timestamp", ""),
        "max_timestamp": row.get("max_timestamp", ""),
        "min_cursor": row.get("min_cursor", ""),
        "max_cursor": row.get("max_cursor", ""),
        "last_cursor": row.get("last_cursor", ""),
        "created_at": row.get("created_at", ""),
    }


def manifest_from_files(
    streams: Iterable[StreamDefinition],
    files: list[dict[str, Any]],
    *,
    prefix: str = DEFAULT_PREFIX,
    public_base_url: str | None = None,
    generated_at: dt.datetime | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or utc_now()
    by_stream: dict[str, list[dict[str, Any]]] = {}
    normalized_prefix = prefix.rstrip("/")
    for item in files:
        item_prefix = str(item.get("prefix") or "").rstrip("/")
        object_key = str(item.get("object_key") or "")
        if item_prefix and item_prefix != normalized_prefix:
            continue
        if not item_prefix and object_key and not object_key.startswith(normalized_prefix + "/"):
            continue
        by_stream.setdefault(item["stream_id"], []).append(item)

    stream_entries = []
    total_rows = 0
    max_timestamp = ""
    for stream in streams:
        stream_files = by_stream.get(stream.id, [])
        base_files = sorted([item for item in stream_files if item["kind"] == "base"], key=lambda item: item.get("base_id", ""))
        base = base_files[-1] if base_files else None
        base_id = base.get("base_id", "") if base else ""
        deltas = sorted(
            [item for item in stream_files if item["kind"] == "delta" and item.get("base_id", "") == base_id],
            key=lambda item: item.get("delta_id", ""),
        )
        stream_rows = int(base.get("rows", 0)) if base else 0
        stream_rows += sum(int(item.get("rows", 0)) for item in deltas)
        total_rows += stream_rows
        for item in ([base] if base else []) + deltas:
            ts = str(item.get("max_timestamp", ""))
            if ts > max_timestamp:
                max_timestamp = ts
        entry = stream.to_manifest()
        entry["base"] = _file_manifest(base) if base else None
        entry["deltas"] = [_file_manifest(item) for item in deltas]
        entry["stats"] = {"rows": stream_rows, "delta_count": len(deltas)}
        stream_entries.append(entry)

    return {
        "product": "astrid",
        "version": MANIFEST_VERSION,
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "prefix": prefix,
        "base_url": (public_base_url or _public_base_url()).rstrip("/"),
        "streams": stream_entries,
        "stats": {
            "stream_count": len(stream_entries),
            "total_rows": total_rows,
            "max_timestamp": max_timestamp,
        },
    }


def _metadata_for_export(
    ch,
    stream: StreamDefinition,
    data_path: Path,
    *,
    prefix: str,
    kind: str,
    base_id: str,
    delta_id_value: str,
    object_key: str,
    url: str,
    last_cursor: str | None,
) -> dict[str, Any]:
    digest = hashlib.sha256(data_path.read_bytes()).hexdigest()
    range_row = _query_first_row(ch, _stream_range_query(stream, last_cursor=last_cursor)) or (None, None, None, None)
    last_row = _query_last_row_dict(ch, stream, last_cursor=last_cursor)
    last_cursor_payload = _cursor_payload(stream, last_row) if last_row else ""
    row_count = int((_query_first_row(ch, _stream_count_query(stream, last_cursor=last_cursor)) or (0,))[0] or 0)
    return {
        "prefix": prefix.rstrip("/"),
        "stream_id": stream.id,
        "kind": kind,
        "base_id": base_id,
        "delta_id": delta_id_value,
        "object_key": object_key,
        "url": url,
        "schema_hash": stream.schema_hash,
        "rows": row_count,
        "bytes": data_path.stat().st_size,
        "sha256": digest,
        "min_timestamp": _json_value(range_row[0]),
        "max_timestamp": _json_value(range_row[1]),
        "min_cursor": _json_value(range_row[2]),
        "max_cursor": _json_value(range_row[3]),
        "last_cursor": last_cursor_payload,
    }


def _export_stream_parquet(
    ch,
    stream: StreamDefinition,
    out_dir: Path,
    *,
    prefix: str,
    kind: str,
    base_id: str,
    delta_id_value: str = "",
    when: dt.datetime | None = None,
    last_cursor: str | None = None,
) -> tuple[Path, str] | None:
    count = int((_query_first_row(ch, _stream_count_query(stream, last_cursor=last_cursor)) or (0,))[0] or 0)
    if count <= 0:
        return None
    key = _stream_file_key(prefix, stream, kind=kind, base_id=base_id, delta_id_value=delta_id_value, when=when)
    data = ch.raw_query(f"{_stream_select_query(stream, last_cursor=last_cursor)} FORMAT Parquet")
    local_path = out_dir / key
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(data)
    return local_path, key


def _select_streams(all_streams: bool, stream_ids: list[str] | None) -> list[StreamDefinition]:
    streams = load_registry()
    if all_streams:
        return streams
    requested = set(stream_ids or [])
    if not requested:
        raise ValueError("pass --all-streams or at least one stream id")
    selected = [stream for stream in streams if stream.id in requested]
    missing = sorted(requested - {stream.id for stream in selected})
    if missing:
        raise ValueError(f"unknown stream ids: {', '.join(missing)}")
    return selected


def publish_base(
    ch,
    uploader: R2Uploader | LocalUploader,
    *,
    prefix: str = DEFAULT_PREFIX,
    streams: list[StreamDefinition] | None = None,
    out_dir: str | Path = "/tmp/astrid-r2-publish",
    base_time: dt.datetime | None = None,
) -> dict[str, Any]:
    ensure_r2_state_tables(ch)
    selected = streams or load_registry()
    base_time = (base_time or utc_now()).astimezone(dt.UTC).replace(minute=0, second=0, microsecond=0)
    base_id = hourly_base_id(base_time)
    out = Path(out_dir) / base_id
    exported: list[dict[str, Any]] = []

    for stream in selected:
        exported_path = _export_stream_parquet(ch, stream, out, prefix=prefix, kind="base", base_id=base_id, when=base_time)
        if not exported_path:
            continue
        local_path, key = exported_path
        url = uploader.upload_file(local_path, key, content_type="application/octet-stream", cache_control=DATA_CACHE_CONTROL)
        file_info = _metadata_for_export(
            ch,
            stream,
            local_path,
            prefix=prefix,
            kind="base",
            base_id=base_id,
            delta_id_value="",
            object_key=key,
            url=url,
            last_cursor=None,
        )
        _record_file(ch, file_info)
        _upsert_state(
            ch,
            stream.id,
            prefix=prefix.rstrip("/"),
            base_id=base_id,
            last_cursor=file_info["last_cursor"],
            last_timestamp=file_info.get("max_timestamp", ""),
        )
        exported.append(file_info)

    manifest = upload_current_manifest(ch, uploader, prefix=prefix, streams=selected)
    return {"status": "OK", "mode": "base", "base_id": base_id, "streams": len(selected), "files": len(exported), "manifest": manifest}


def publish_delta(
    ch,
    uploader: R2Uploader | LocalUploader,
    *,
    prefix: str = DEFAULT_PREFIX,
    streams: list[StreamDefinition] | None = None,
    out_dir: str | Path = "/tmp/astrid-r2-publish",
    delta_time: dt.datetime | None = None,
) -> dict[str, Any]:
    ensure_r2_state_tables(ch)
    selected = streams or load_registry()
    states = _read_state_rows(ch, prefix=prefix)
    now = (delta_time or utc_now()).astimezone(dt.UTC).replace(microsecond=0)
    current_delta_id = delta_id(now)
    out = Path(out_dir) / current_delta_id
    exported: list[dict[str, Any]] = []
    skipped_no_base: list[str] = []

    for stream in selected:
        state = states.get(stream.id)
        if not state or not state.get("base_id"):
            skipped_no_base.append(stream.id)
            continue
        exported_path = _export_stream_parquet(
            ch,
            stream,
            out,
            prefix=prefix,
            kind="delta",
            base_id=state["base_id"],
            delta_id_value=current_delta_id,
            when=now,
            last_cursor=state.get("last_cursor") or None,
        )
        if not exported_path:
            continue
        local_path, key = exported_path
        url = uploader.upload_file(local_path, key, content_type="application/octet-stream", cache_control=DATA_CACHE_CONTROL)
        file_info = _metadata_for_export(
            ch,
            stream,
            local_path,
            prefix=prefix,
            kind="delta",
            base_id=state["base_id"],
            delta_id_value=current_delta_id,
            object_key=key,
            url=url,
            last_cursor=state.get("last_cursor") or None,
        )
        _record_file(ch, file_info)
        _upsert_state(
            ch,
            stream.id,
            prefix=prefix.rstrip("/"),
            base_id=state["base_id"],
            last_cursor=file_info["last_cursor"],
            last_timestamp=file_info.get("max_timestamp", ""),
            last_delta_id=current_delta_id,
        )
        exported.append(file_info)

    manifest = upload_current_manifest(ch, uploader, prefix=prefix, streams=selected)
    return {
        "status": "OK",
        "mode": "delta",
        "delta_id": current_delta_id,
        "streams": len(selected),
        "files": len(exported),
        "skipped_no_base": skipped_no_base,
        "manifest": manifest,
    }


def upload_current_manifest(
    ch,
    uploader: R2Uploader | LocalUploader,
    *,
    prefix: str = DEFAULT_PREFIX,
    streams: list[StreamDefinition] | None = None,
) -> dict[str, Any]:
    selected = streams or load_registry()
    manifest = manifest_from_files(
        selected,
        _read_file_rows(ch, prefix=prefix),
        prefix=prefix,
        public_base_url=getattr(uploader, "public_base_url", None),
    )
    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    immutable_key = f"{prefix.rstrip('/')}/manifests/{stamp}.json"
    mutable_key = f"{prefix.rstrip('/')}/manifest.json"
    immutable_url = uploader.upload_json(manifest, immutable_key, cache_control=DATA_CACHE_CONTROL)
    mutable_url = uploader.upload_json(manifest, mutable_key, cache_control=MANIFEST_CACHE_CONTROL)
    return {"version": MANIFEST_VERSION, "immutable_url": immutable_url, "url": mutable_url, "streams": len(selected)}


def run_delta_loop(
    ch_factory,
    uploader: R2Uploader,
    *,
    prefix: str = DEFAULT_PREFIX,
    all_streams: bool = True,
    stream_ids: list[str] | None = None,
    interval_seconds: float = 15.0,
) -> None:
    streams = _select_streams(all_streams, stream_ids)
    while True:
        started = time.time()
        ch = ch_factory()
        try:
            publish_delta(ch, uploader, prefix=prefix, streams=streams)
        finally:
            ch.close()
        elapsed = time.time() - started
        time.sleep(max(0.0, interval_seconds - elapsed))
