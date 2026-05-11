"""Upload Astrid snapshots to Cloudflare R2.

Uses the S3-compatible API via boto3.  R2 charges $0 for egress,
making it ideal for public Parquet distribution.

Required env vars:
    R2_ACCOUNT_ID       Cloudflare account ID
    R2_ACCESS_KEY_ID    R2 API token access key
    R2_SECRET_ACCESS_KEY R2 API token secret key
    R2_BUCKET           Bucket name (default: astrid-public)
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import boto3


def _r2_client():
    account_id = os.environ["R2_ACCOUNT_ID"]
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def _bucket() -> str:
    return os.getenv("R2_BUCKET", "astrid")


def upload_snapshot(
    snapshot_dir: str | Path,
    *,
    prefix: str = "v2",
    bucket: str | None = None,
    public_url: str | None = None,
) -> dict[str, Any]:
    """Upload a snapshot directory to R2.

    Directory layout expected:
        snapshot_dir/
            manifest.json
            all_markets.parquet
            markets/
                MORPHO_MARKET__WETH__5a3d8c91.parquet
                ...

    Uploads to:
        s3://{bucket}/{prefix}/manifest.json
        s3://{bucket}/{prefix}/all_markets.parquet
        s3://{bucket}/{prefix}/markets/MORPHO_MARKET__WETH__5a3d8c91.parquet
        ...

    Returns upload stats.
    """
    s3 = _r2_client()
    bucket = bucket or _bucket()
    snapshot_dir = Path(snapshot_dir)

    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest.json in {snapshot_dir}")

    manifest = json.loads(manifest_path.read_text())
    base_url = public_url or os.getenv("R2_PUBLIC_URL", "https://astrid.rld.fi")

    # Collect all files to upload
    files_to_upload: list[tuple[Path, str]] = []

    # manifest.json
    files_to_upload.append((manifest_path, f"{prefix}/manifest.json"))

    # all_markets.parquet
    full_path = snapshot_dir / "all_markets.parquet"
    if full_path.exists():
        files_to_upload.append((full_path, f"{prefix}/all_markets.parquet"))

    # per-market files
    markets_dir = snapshot_dir / "markets"
    if markets_dir.exists():
        for parquet_file in sorted(markets_dir.glob("*.parquet")):
            key = f"{prefix}/markets/{parquet_file.name}"
            files_to_upload.append((parquet_file, key))

    # Upload with progress
    t0 = time.time()
    uploaded = 0
    total_bytes = 0

    for local_path, s3_key in files_to_upload:
        file_size = local_path.stat().st_size
        content_type = "application/json" if s3_key.endswith(".json") else "application/octet-stream"

        s3.upload_file(
            str(local_path),
            bucket,
            s3_key,
            ExtraArgs={
                "ContentType": content_type,
                "CacheControl": "public, max-age=3600" if s3_key.endswith(".json") else "public, max-age=86400",
            },
        )
        uploaded += 1
        total_bytes += file_size

    elapsed = time.time() - t0

    # Update manifest with public URLs
    manifest_url = f"{base_url}/{prefix}/manifest.json"
    for market in manifest.get("markets", []):
        market["url"] = f"{base_url}/{prefix}/markets/{market['filename']}"
    if "full_snapshot" in manifest:
        manifest["full_snapshot"]["url"] = f"{base_url}/{prefix}/all_markets.parquet"
    manifest["base_url"] = f"{base_url}/{prefix}"

    # Re-upload updated manifest with URLs
    updated_manifest = json.dumps(manifest, indent=2) + "\n"
    s3.put_object(
        Bucket=bucket,
        Key=f"{prefix}/manifest.json",
        Body=updated_manifest.encode(),
        ContentType="application/json",
        CacheControl="public, max-age=3600",
    )

    return {
        "bucket": bucket,
        "prefix": prefix,
        "manifest_url": manifest_url,
        "files_uploaded": uploaded,
        "total_bytes": total_bytes,
        "elapsed_s": round(elapsed, 1),
        "throughput_mbps": round(total_bytes / 1024 / 1024 / elapsed, 1) if elapsed > 0 else 0,
    }
