"""Cloudflare R2 upload helpers for Astrid chunk distribution.

R2 is S3-compatible with zero egress fees, making it ideal for
public data distribution.

Environment variables:
    R2_ENDPOINT         Cloudflare R2 S3-compatible endpoint
    R2_ACCESS_KEY_ID    Access key
    R2_SECRET_ACCESS_KEY Secret key
    R2_BUCKET           Bucket name (default: astrid-public)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("astrid-r2")


def _s3_client():
    """Create a boto3 S3 client configured for Cloudflare R2."""
    import boto3

    endpoint = os.getenv("R2_ENDPOINT")
    access_key = os.getenv("R2_ACCESS_KEY_ID")
    secret_key = os.getenv("R2_SECRET_ACCESS_KEY")

    if not endpoint or not access_key or not secret_key:
        raise RuntimeError(
            "R2 upload requires R2_ENDPOINT, R2_ACCESS_KEY_ID, and R2_SECRET_ACCESS_KEY environment variables"
        )

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )


def upload_chunk(
    local_path: str | Path,
    key: str,
    *,
    bucket: str | None = None,
    content_type: str | None = None,
) -> str:
    """Upload a file to R2 and return the public URL.

    Args:
        local_path: Path to the local file to upload.
        key: Object key in the bucket (e.g., "aave/timeseries/2026-04.parquet").
        bucket: R2 bucket name. Defaults to R2_BUCKET env var.
        content_type: MIME type override.

    Returns:
        The full R2 URL of the uploaded object.
    """
    bucket = bucket or os.getenv("R2_BUCKET", "astrid-public")
    local_path = Path(local_path)

    extra_args = {}
    if content_type:
        extra_args["ContentType"] = content_type
    elif local_path.suffix == ".parquet":
        extra_args["ContentType"] = "application/octet-stream"
    elif local_path.suffix == ".jsonl":
        extra_args["ContentType"] = "application/x-ndjson"
    elif local_path.suffix == ".json":
        extra_args["ContentType"] = "application/json"

    client = _s3_client()
    client.upload_file(
        str(local_path),
        bucket,
        key,
        ExtraArgs=extra_args or None,
    )

    endpoint = os.getenv("R2_ENDPOINT", "")
    # Construct public URL
    public_domain = os.getenv("R2_PUBLIC_DOMAIN")
    if public_domain:
        url = f"https://{public_domain}/{key}"
    else:
        url = f"{endpoint}/{bucket}/{key}"

    log.info(f"Uploaded {local_path.name} → {url}")
    return url


def upload_chunk_with_sidecar(
    data_path: str | Path,
    sidecar_path: str | Path,
    key_prefix: str,
    *,
    bucket: str | None = None,
) -> dict[str, str]:
    """Upload a chunk data file and its .chunk.json sidecar to R2.

    Returns dict with 'data_url' and 'sidecar_url'.
    """
    data_path = Path(data_path)
    sidecar_path = Path(sidecar_path)

    data_key = f"{key_prefix.rstrip('/')}/{data_path.name}"
    sidecar_key = f"{key_prefix.rstrip('/')}/{sidecar_path.name}"

    data_url = upload_chunk(data_path, data_key, bucket=bucket)
    sidecar_url = upload_chunk(sidecar_path, sidecar_key, bucket=bucket)

    return {"data_url": data_url, "sidecar_url": sidecar_url}
