#!/usr/bin/env python3
"""Materialize historical Aave account profiles from decoded account events."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from analytics.aave_accounts import (  # noqa: E402
    clickhouse_client_from_env,
    rebuild_historical_account_profiles,
)
from analytics.config import apply_env_from_config  # noqa: E402

apply_env_from_config()


def _parse_ts(value: str | None):
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Aave account historical profile time series")
    parser.add_argument("--start", default=None, help="Inclusive ISO timestamp")
    parser.add_argument("--end", default=None, help="Inclusive ISO timestamp")
    parser.add_argument(
        "--full-snapshot-every-hours",
        type=int,
        default=0,
        help="Also emit all active users every N hours; 0 emits changed users only",
    )
    parser.add_argument("--insert-batch-size", type=int, default=10000)
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    ch = clickhouse_client_from_env()
    result = rebuild_historical_account_profiles(
        ch,
        start_ts=_parse_ts(args.start),
        end_ts=_parse_ts(args.end),
        full_snapshot_every_hours=args.full_snapshot_every_hours,
        insert_batch_size=args.insert_batch_size,
        run_id=args.run_id,
    )
    print(json.dumps(result, default=str, indent=2))
    return 0 if result.get("status") in {"DONE", "NO_ACCOUNT_EVENTS"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

