#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from astrid_node.pull import pull_streams


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull Astrid R2 base+delta Parquet files")
    parser.add_argument("--stream", action="append", default=None, help="Stream id; repeat for multiple streams")
    parser.add_argument("--symbol", action="append", default=None, help="Symbol filter; repeat for multiple symbols")
    parser.add_argument("--since", default=None, help="ISO timestamp lower bound")
    parser.add_argument("--base-url", default="https://astrid.rld.fi/v2")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    kwargs = {"base_url": args.base_url, "force": args.force}
    if args.data_dir:
        kwargs["data_dir"] = Path(args.data_dir)
    result = pull_streams(args.stream, symbols=args.symbol, since=args.since, **kwargs)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
