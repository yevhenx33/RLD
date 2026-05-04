#!/usr/bin/env python3
"""Smoke test for the ClickHouse analytics indexer."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

import clickhouse_connect


API_BASE = os.getenv("RATES_API_BASE_URL", "http://127.0.0.1:5000").rstrip("/")


def fetch_json(path: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(f"{API_BASE}{path}", timeout=5) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def assert_api() -> None:
    status, payload = fetch_json("/healthz")
    assert status == 200 and payload.get("clickhouse") == "ok", f"healthz failed: {status} {payload}"
    status, payload = fetch_json("/readyz")
    assert status == 200 and payload.get("status") == "ready", f"readyz failed: {status} {payload}"
    status, payload = fetch_json("/status")
    assert status == 200 and payload.get("sourceStatus"), f"status failed: {status} {payload}"


def assert_clickhouse() -> None:
    ch = clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "127.0.0.1"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
    )
    try:
        rows = ch.query(
            """
            SELECT source, kind
            FROM source_status FINAL
            WHERE source IN ('AAVE_MARKET', 'CHAINLINK_PRICES', 'SOFR_RATES')
            ORDER BY source, kind
            """
        ).result_rows
        sources = {row[0] for row in rows}
        missing = {"AAVE_MARKET", "CHAINLINK_PRICES", "SOFR_RATES"} - sources
        assert not missing, f"missing source_status rows: {sorted(missing)}"
        part_rows = ch.query(
            """
            SELECT table, count()
            FROM system.parts
            WHERE active AND database = currentDatabase()
            GROUP BY table
            HAVING count() > 3000
            """
        ).result_rows
        assert not part_rows, f"too many active parts: {part_rows}"
    finally:
        ch.close()


def main() -> int:
    assert_api()
    assert_clickhouse()
    print("ClickHouse indexer smoke test passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
