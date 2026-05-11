#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from astrid_node.duckdb_mode import query_parquet_files
from astrid_node.pull import local_parquet_files, pull_streams


def main() -> int:
    parser = argparse.ArgumentParser(description="Query cached Astrid Parquet files with DuckDB")
    parser.add_argument("--stream", required=True)
    parser.add_argument("--sql", required=True)
    parser.add_argument("--symbol", action="append", default=None)
    parser.add_argument("--since", default=None)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--base-url", default="https://astrid.rld.fi/v2")
    parser.add_argument("--pull", action="store_true")
    parser.add_argument("--no-dedupe", action="store_true")
    args = parser.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else None
    if args.pull:
        kwargs = {"base_url": args.base_url}
        if data_dir:
            kwargs["data_dir"] = data_dir
        pulled = pull_streams([args.stream], symbols=args.symbol, since=args.since, **kwargs)
        if pulled.get("errors"):
            print(json.dumps(pulled, indent=2, sort_keys=True))
            return 1

    files, manifest, stream_manifest = local_parquet_files(
        data_dir=data_dir or Path.home() / ".astrid" / "data" / "v2",
        stream_ids=[args.stream],
        symbols=args.symbol,
        since=args.since,
    )
    rows = query_parquet_files(files, args.sql, stream_manifest=stream_manifest, dedupe=not args.no_dedupe)
    print(json.dumps({"manifest": {"generated_at": manifest.get("generated_at") if manifest else None}, "rows": rows}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
