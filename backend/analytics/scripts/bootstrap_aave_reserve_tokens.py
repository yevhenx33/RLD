#!/usr/bin/env python3
"""Seed Aave reserve -> aToken/vToken metadata for account reconstruction."""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from analytics.aave_accounts import (  # noqa: E402
    bootstrap_reserve_tokens_from_events,
    bootstrap_reserve_tokens_from_rpc,
    clickhouse_client_from_env,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap Aave account reserve token registry")
    parser.add_argument("--rpc-url", default=os.getenv("MAINNET_RPC_URL") or os.getenv("ETH_RPC_URL"))
    parser.add_argument("--block-tag", default="latest")
    parser.add_argument("--events-only", action="store_true")
    args = parser.parse_args()

    ch = clickhouse_client_from_env()
    event_rows = bootstrap_reserve_tokens_from_events(ch)
    rpc_rows = 0
    if event_rows == 0 and not args.events_only:
        if not args.rpc_url:
            raise RuntimeError("ReserveInitialized logs unavailable; pass --rpc-url or MAINNET_RPC_URL for fallback")
        rpc_rows = bootstrap_reserve_tokens_from_rpc(ch, args.rpc_url, args.block_tag)
    print(f"reserve_token_rows_from_events={event_rows} reserve_token_rows_from_rpc={rpc_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
