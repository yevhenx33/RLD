"""Chunk verification helpers for Astrid Node."""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.request
from pathlib import Path

from astrid_node.clickhouse import insert_json_payloads
from astrid_node.config import NodeConfig
from astrid_node.streams import Stream


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_chunk(path: str | Path, expected_sha256: str) -> bool:
    return sha256_file(path) == expected_sha256


def fetch_chunk(uri: str, cache_dir: str | Path) -> Path:
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    filename = uri.rsplit("/", 1)[-1] or "chunk"
    target = cache / filename
    if uri.startswith(("http://", "https://")):
        request = urllib.request.Request(uri, headers={"User-Agent": "Astrid-Node/0.1"})
        with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as out:
            shutil.copyfileobj(response, out)
    elif uri.startswith("file://"):
        shutil.copyfile(uri.removeprefix("file://"), target)
    else:
        source = Path(uri)
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
    return target


def load_jsonl_chunk(ch, stream: Stream, path: str | Path) -> int:
    def payloads():
        with Path(path).open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                json.loads(stripped)
                yield stripped

    return insert_json_payloads(ch, stream.local_table, payloads())


def load_parquet_chunk(ch, stream: Stream, path: str | Path) -> int:
    """Load a Parquet chunk by converting rows to JSON envelopes."""
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    rows = table.to_pylist()

    def _default(v):
        if hasattr(v, 'isoformat'):
            return v.isoformat()
        return str(v)

    def payloads():
        for row in rows:
            envelope = {
                "stream_id": stream.id,
                "rows": [row],
            }
            yield json.dumps(envelope, default=_default, sort_keys=True)

    return insert_json_payloads(ch, stream.local_table, payloads())


def sync_chunks(ch, stream: Stream, config: NodeConfig) -> dict:
    downloaded = 0
    loaded = 0
    skipped = 0
    for chunk in stream.chunks:
        uri = chunk["uri"]
        path = fetch_chunk(uri, config.cache_dir / stream.id)
        expected = chunk.get("sha256")
        if expected and not verify_chunk(path, expected):
            raise ValueError(f"checksum mismatch for {uri}")
        fmt = str(chunk.get("format", "")).lower()
        if fmt in {"jsonl", "ndjson"}:
            loaded += load_jsonl_chunk(ch, stream, path)
        elif fmt == "parquet":
            loaded += load_parquet_chunk(ch, stream, path)
        else:
            skipped += 1
        downloaded += 1
        ch.insert(
            "astrid_meta.chunk_manifest",
            [[stream.id, chunk.get("chunk_id", path.name), uri, expected or "", int(chunk.get("row_count", 0))]],
            column_names=["stream_id", "chunk_id", "uri", "sha256", "row_count"],
        )
        ch.insert(
            "astrid_meta.chunk_sync_state",
            [[stream.id, chunk.get("chunk_id", path.name), "LOADED" if fmt in {"jsonl", "ndjson"} else "DOWNLOADED"]],
            column_names=["stream_id", "chunk_id", "status"],
        )
    return {"downloaded": downloaded, "loadedRows": loaded, "downloadedOnly": skipped}
