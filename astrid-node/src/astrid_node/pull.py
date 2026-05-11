"""Download Astrid snapshots and base+delta manifests from R2.

V3 manifests use immutable hourly base Parquet files plus append-only delta
Parquet files. Downloads are checksum-verified and written through a temporary
file before becoming visible in the local cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

DEFAULT_BASE_URL = os.getenv("ASTRID_DATA_URL", "https://astrid.rld.fi/v2")
DEFAULT_DATA_DIR = Path(os.getenv("ASTRID_DATA_DIR", os.path.expanduser("~/.astrid/data/v2")))


def _download(url: str, dest: Path, *, expected_sha: str | None = None) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    req = urllib.request.Request(url, headers={"User-Agent": "astrid-node/3.0"})
    with urllib.request.urlopen(req) as response:
        data = response.read()
    if expected_sha:
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_sha:
            raise ValueError(f"SHA-256 mismatch for {url}: expected {expected_sha[:16]}... got {actual[:16]}...")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return len(data)


def fetch_manifest(base_url: str = DEFAULT_BASE_URL, data_dir: Path = DEFAULT_DATA_DIR) -> dict[str, Any]:
    manifest_url = f"{base_url.rstrip('/')}/manifest.json"
    manifest_path = Path(data_dir) / "manifest.json"
    _download(manifest_url, manifest_path)
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def load_cached_manifest(data_dir: Path = DEFAULT_DATA_DIR) -> dict[str, Any] | None:
    path = Path(data_dir) / "manifest.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    cleaned = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _entry_max_time(entry: dict[str, Any]) -> datetime | None:
    return _parse_time(str(entry.get("max_timestamp") or ""))


def _stream_matches(stream: dict[str, Any], stream_ids: set[str] | None) -> bool:
    return not stream_ids or str(stream.get("id")) in stream_ids


def _entry_matches_symbols(entry: dict[str, Any], symbols: set[str] | None) -> bool:
    if not symbols:
        return True
    entry_symbols = entry.get("symbols") or entry.get("symbol")
    if not entry_symbols:
        return True
    if isinstance(entry_symbols, str):
        entry_symbols = [entry_symbols]
    return bool({str(symbol).upper() for symbol in entry_symbols} & symbols)


def iter_v3_entries(
    manifest: dict[str, Any],
    *,
    stream_ids: Iterable[str] | None = None,
    symbols: Iterable[str] | None = None,
    since: str | None = None,
) -> list[dict[str, Any]]:
    requested_streams = {str(item) for item in stream_ids or []} or None
    requested_symbols = {str(item).upper() for item in symbols or []} or None
    since_dt = _parse_time(since)
    entries: list[dict[str, Any]] = []
    for stream in manifest.get("streams", []):
        if not _stream_matches(stream, requested_streams):
            continue
        base = stream.get("base")
        if base and _entry_matches_symbols(base, requested_symbols):
            base_max = _entry_max_time(base)
            if since_dt is None or base_max is None or base_max >= since_dt:
                entries.append({**base, "stream_id": stream.get("id"), "stream": stream})
        for delta in stream.get("deltas") or []:
            if not _entry_matches_symbols(delta, requested_symbols):
                continue
            delta_max = _entry_max_time(delta)
            if since_dt is None or delta_max is None or delta_max >= since_dt:
                entries.append({**delta, "stream_id": stream.get("id"), "stream": stream})
    return entries


def _entry_cache_path(entry: dict[str, Any], data_dir: Path) -> Path:
    object_key = str(entry.get("object_key") or "")
    if object_key:
        return Path(data_dir) / "objects" / object_key
    parsed = urlparse(str(entry["url"]))
    return Path(data_dir) / "objects" / parsed.path.lstrip("/")


def pull_streams(
    stream_ids: Iterable[str] | None = None,
    *,
    symbols: Iterable[str] | None = None,
    since: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    data_dir: Path = DEFAULT_DATA_DIR,
    force: bool = False,
) -> dict[str, Any]:
    t0 = time.time()
    data_dir = Path(data_dir)
    manifest = fetch_manifest(base_url, data_dir)
    if int(manifest.get("version", 0) or 0) != 3:
        return pull_markets(list(symbols or []), base_url=base_url, data_dir=data_dir, force=force)

    entries = iter_v3_entries(manifest, stream_ids=stream_ids, symbols=symbols, since=since)
    downloaded = 0
    skipped = 0
    total_bytes = 0
    errors: list[str] = []
    paths: list[str] = []

    for entry in entries:
        dest = _entry_cache_path(entry, data_dir)
        expected_sha = str(entry.get("sha256") or "")
        if not force and dest.exists() and expected_sha:
            if hashlib.sha256(dest.read_bytes()).hexdigest() == expected_sha:
                skipped += 1
                paths.append(str(dest))
                continue
        try:
            nbytes = _download(str(entry["url"]), dest, expected_sha=expected_sha or None)
            downloaded += 1
            total_bytes += nbytes
            paths.append(str(dest))
        except Exception as exc:  # noqa: BLE001 - expose per-object errors to the CLI
            errors.append(f"{entry.get('stream_id')} {entry.get('object_key')}: {exc}")

    return {
        "version": 3,
        "data_dir": str(data_dir),
        "downloaded": downloaded,
        "skipped": skipped,
        "errors": errors,
        "total_bytes": total_bytes,
        "elapsed_s": round(time.time() - t0, 1),
        "files": paths,
        "manifest_generated_at": manifest.get("generated_at"),
        "max_timestamp": (manifest.get("stats") or {}).get("max_timestamp"),
    }


def local_parquet_files(
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    stream_ids: Iterable[str] | None = None,
    symbols: Iterable[str] | None = None,
    since: str | None = None,
) -> tuple[list[str], dict[str, Any] | None, dict[str, Any] | None]:
    manifest = load_cached_manifest(data_dir)
    if not manifest:
        return [], None, None
    if int(manifest.get("version", 0) or 0) != 3:
        files = [str(path) for path in sorted((Path(data_dir) / "markets").glob("*.parquet"))]
        return files, manifest, None
    entries = iter_v3_entries(manifest, stream_ids=stream_ids, symbols=symbols, since=since)
    files = [str(_entry_cache_path(entry, Path(data_dir))) for entry in entries if _entry_cache_path(entry, Path(data_dir)).exists()]
    stream_manifest = None
    if stream_ids:
        wanted = next(iter(stream_ids), None)
        for stream in manifest.get("streams", []):
            if stream.get("id") == wanted:
                stream_manifest = stream
                break
    return files, manifest, stream_manifest


def _manifest_age_seconds(manifest: dict[str, Any], *, now: datetime | None = None) -> int | None:
    generated = _parse_time(str(manifest.get("generated_at") or ""))
    if not generated:
        return None
    current = now or datetime.now(timezone.utc)
    return max(0, int((current.astimezone(timezone.utc) - generated).total_seconds()))


def cache_status(
    data_dir: Path = DEFAULT_DATA_DIR,
    *,
    stale_after_seconds: int = 120,
    now: datetime | None = None,
) -> dict[str, Any]:
    data_dir = Path(data_dir)
    manifest = load_cached_manifest(data_dir)
    if not manifest:
        return {"data_dir": str(data_dir), "manifest": None, "files": 0, "missing": 0}
    if int(manifest.get("version", 0) or 0) == 3:
        entries = iter_v3_entries(manifest)
        missing = 0
        present = 0
        for entry in entries:
            if _entry_cache_path(entry, data_dir).exists():
                present += 1
            else:
                missing += 1
        age = _manifest_age_seconds(manifest, now=now)
        return {
            "data_dir": str(data_dir),
            "manifest": {
                "version": manifest.get("version"),
                "generated_at": manifest.get("generated_at"),
                "max_timestamp": (manifest.get("stats") or {}).get("max_timestamp"),
                "streams": len(manifest.get("streams", [])),
                "age_seconds": age,
                "stale": age is not None and age > stale_after_seconds,
            },
            "files": present,
            "missing": missing,
        }
    cached = list_cached(data_dir)
    return {"data_dir": str(data_dir), "manifest": {"version": manifest.get("version")}, "files": len(cached), "missing": 0}


# Backward-compatible v2 helpers.
def pull_markets(
    symbols: list[str] | None = None,
    *,
    base_url: str = DEFAULT_BASE_URL,
    data_dir: Path = DEFAULT_DATA_DIR,
    force: bool = False,
) -> dict[str, Any]:
    t0 = time.time()
    manifest = fetch_manifest(base_url, data_dir)
    markets = manifest.get("markets", [])
    if symbols:
        symbol_set = {s.upper() for s in symbols}
        markets = [m for m in markets if m["symbol"].upper() in symbol_set]

    markets_dir = Path(data_dir) / "markets"
    markets_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    skipped = 0
    total_bytes = 0
    errors: list[str] = []

    for market in markets:
        filename = market["filename"]
        dest = markets_dir / filename
        expected_sha = market.get("sha256")
        if not force and dest.exists() and expected_sha:
            if hashlib.sha256(dest.read_bytes()).hexdigest() == expected_sha:
                skipped += 1
                continue
        url = market.get("url") or f"{base_url.rstrip('/')}/markets/{filename}"
        try:
            total_bytes += _download(url, dest, expected_sha=expected_sha)
            downloaded += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{filename}: {exc}")

    return {
        "data_dir": str(data_dir),
        "markets_dir": str(markets_dir),
        "downloaded": downloaded,
        "skipped": skipped,
        "errors": errors,
        "total_bytes": total_bytes,
        "elapsed_s": round(time.time() - t0, 1),
        "manifest_markets": len(manifest.get("markets", [])),
    }


def pull_full(*, base_url: str = DEFAULT_BASE_URL, data_dir: Path = DEFAULT_DATA_DIR) -> dict[str, Any]:
    t0 = time.time()
    manifest = fetch_manifest(base_url, data_dir)
    full = manifest.get("full_snapshot", {})
    filename = full.get("filename", "all_markets.parquet")
    url = full.get("url") or f"{base_url.rstrip('/')}/{filename}"
    expected_sha = full.get("sha256")
    dest = Path(data_dir) / filename
    nbytes = _download(url, dest, expected_sha=expected_sha)
    return {"path": str(dest), "bytes": nbytes, "elapsed_s": round(time.time() - t0, 1)}


def list_cached(data_dir: Path = DEFAULT_DATA_DIR) -> list[dict[str, Any]]:
    data_dir = Path(data_dir)
    files = sorted((data_dir / "objects").glob("**/*.parquet"))
    files.extend(sorted((data_dir / "markets").glob("*.parquet")))
    result = []
    for path in files:
        result.append({"filename": path.name, "bytes": path.stat().st_size, "path": str(path)})
    return result
