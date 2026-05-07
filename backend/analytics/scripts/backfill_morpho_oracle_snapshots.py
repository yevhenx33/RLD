#!/usr/bin/env python3
"""Backfill Morpho IOracle.price() snapshots for non-event-priced markets.

The runner snapshots unique oracle contracts, not individual markets. It uses
MAINNET_RPC_URL, which should point at the configured archive-capable dRPC URL.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import uuid
import clickhouse_connect

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from analytics.config import apply_env_from_config
from analytics.morpho_oracle_snapshots import (
    batch_call_oracle_prices,
    default_rpc_url,
    ensure_morpho_oracle_snapshot_tables,
    insert_oracle_snapshot_results,
)
from analytics.schema import ensure_schema


def _ratio(value: float | int | None) -> float | None:
    if value is None:
        return None
    ratio = float(value)
    if ratio > 1_000_000:
        ratio /= 1e18
    return ratio


def _price_feed_requirements(symbol: str, available_feeds: set[str]) -> tuple[str, ...]:
    aliases = {
        "ETH": ("ETH / USD",),
        "WETH": ("ETH / USD",),
        "BTC": ("BTC / USD",),
        "WBTC": ("WBTC / BTC", "BTC / USD"),
        "cbBTC": ("cbBTC / USD",),
        "CBBTC": ("cbBTC / USD",),
        "LBTC": ("LBTC / BTC", "BTC / USD"),
        "tBTC": ("TBTC / USD",),
        "TBTC": ("TBTC / USD",),
        "stETH": ("STETH / USD",),
        "STETH": ("STETH / USD",),
        "wstETH": ("wstETH/stETH exchange rate", "STETH / USD"),
        "WSTETH": ("wstETH/stETH exchange rate", "STETH / USD"),
        "weETH": ("weETH / ETH", "ETH / USD"),
        "WEETH": ("weETH / ETH", "ETH / USD"),
        "rETH": ("RETH / ETH", "ETH / USD"),
        "RETH": ("RETH / ETH", "ETH / USD"),
        "cbETH": ("CBETH / ETH", "ETH / USD"),
        "CBETH": ("CBETH / ETH", "ETH / USD"),
        "lsETH": ("LsETH / ETH Exchange Rate", "ETH / USD"),
        "LSETH": ("LsETH / ETH Exchange Rate", "ETH / USD"),
        "XAUt": ("XAU / USD",),
        "USD0pp": ("USD0++ / USD",),
        "crvUSD": ("CRVUSD / USD",),
        "CRVUSD": ("CRVUSD / USD",),
        "frxUSD": ("frxUSD / USD",),
        "FRXUSD": ("frxUSD / USD",),
    }
    direct = f"{symbol} / USD"
    if direct in available_feeds:
        return (direct,)
    if symbol in aliases:
        return aliases[symbol]
    eth_pair = f"{symbol} / ETH"
    btc_pair = f"{symbol} / BTC"
    if eth_pair in available_feeds:
        return (eth_pair, "ETH / USD")
    if btc_pair in available_feeds:
        return (btc_pair, "BTC / USD")
    return (direct,)


def resolve_symbol_price(symbol: str, feed_prices: dict[str, float]) -> float | None:
    feeds = _price_feed_requirements(symbol, set(feed_prices))
    if not feeds or any(feed not in feed_prices for feed in feeds):
        return None
    if len(feeds) == 1:
        return float(feed_prices[feeds[0]])
    ratio = _ratio(feed_prices.get(feeds[0]))
    base = feed_prices.get(feeds[1])
    if ratio is None or base is None:
        return None
    return float(ratio) * float(base)


def _parse_ts(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return parsed.replace(minute=0, second=0, microsecond=0)


def _ch_client():
    settings = {}
    if os.getenv("CLICKHOUSE_ASYNC_INSERT", "true").strip().lower() in {"1", "true", "yes"}:
        settings["async_insert"] = 1
        settings["wait_for_async_insert"] = 1 if os.getenv("CLICKHOUSE_WAIT_FOR_ASYNC_INSERT", "true").strip().lower() in {"1", "true", "yes"} else 0
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "127.0.0.1"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
        database=os.getenv("CLICKHOUSE_DATABASE", "default"),
        settings=settings,
    )


def _latest_feed_prices(ch) -> dict[str, float]:
    rows = ch.query("SELECT feed, argMax(price, timestamp) FROM chainlink_prices GROUP BY feed").result_rows
    return {str(feed): float(price) for feed, price in rows if feed and price is not None}


def _eligible_oracles(ch, args) -> tuple[list[str], dict[str, object]]:
    feeds = _latest_feed_prices(ch)
    rows = ch.query(
        """
        SELECT p.market_id, p.oracle, s.loan_symbol, s.collateral_symbol, p.loan_decimals,
               st.total_supply_assets, st.total_borrow_assets, p.creation_timestamp
        FROM (SELECT * FROM morpho_market_oracle_support FINAL) AS s
        INNER JOIN morpho_market_params AS p USING market_id
        INNER JOIN (SELECT * FROM morpho_market_state FINAL) AS st USING market_id
        WHERE p.oracle != '0x0000000000000000000000000000000000000000'
          AND NOT empty(s.loan_price_feeds)
          AND empty(s.collateral_price_feeds)
        """
    ).result_rows
    by_oracle: dict[str, dict[str, object]] = {}
    min_creation: dt.datetime | None = None
    for market_id, oracle, loan_symbol, _collateral_symbol, loan_decimals, supply_raw, borrow_raw, creation_ts in rows:
        loan_price = resolve_symbol_price(str(loan_symbol), feeds)
        if loan_price is None or loan_price <= 0:
            continue
        supply_usd = int(supply_raw or 0) / (10 ** int(loan_decimals or 18)) * loan_price
        borrow_usd = int(borrow_raw or 0) / (10 ** int(loan_decimals or 18)) * loan_price
        if supply_usd < args.min_supply_usd and borrow_usd < args.min_borrow_usd:
            continue
        oracle = str(oracle).lower()
        slot = by_oracle.setdefault(oracle, {"markets": 0, "supply_usd": 0.0, "borrow_usd": 0.0})
        slot["markets"] = int(slot["markets"]) + 1
        slot["supply_usd"] = float(slot["supply_usd"]) + float(supply_usd)
        slot["borrow_usd"] = float(slot["borrow_usd"]) + float(borrow_usd)
        if creation_ts:
            cts = creation_ts.replace(tzinfo=None) if getattr(creation_ts, "tzinfo", None) else creation_ts
            cts = cts.replace(minute=0, second=0, microsecond=0)
            min_creation = cts if min_creation is None else min(min_creation, cts)
            existing_first = slot.get("first_creation")
            slot["first_creation"] = cts if existing_first is None else min(existing_first, cts)
    ordered = sorted(by_oracle.items(), key=lambda item: float(item[1]["supply_usd"]) + float(item[1]["borrow_usd"]), reverse=True)
    if args.max_oracles:
        ordered = ordered[: args.max_oracles]
    return [oracle for oracle, _meta in ordered], {
        "candidate_market_rows": len(rows),
        "selected_oracles": len(ordered),
        "selected_markets": sum(int(meta["markets"]) for _oracle, meta in ordered),
        "selected_supply_usd": sum(float(meta["supply_usd"]) for _oracle, meta in ordered),
        "selected_borrow_usd": sum(float(meta["borrow_usd"]) for _oracle, meta in ordered),
        "min_creation_timestamp": min_creation,
        "oracle_start_timestamps": {oracle: meta.get("first_creation") for oracle, meta in ordered},
    }


def _hour_blocks(ch, start: dt.datetime, end: dt.datetime, limit_hours: int | None) -> list[tuple[dt.datetime, int]]:
    rows = ch.query(
        """
        SELECT toStartOfHour(timestamp) AS ts, argMax(block_number, timestamp) AS block_number
        FROM chainlink_prices
        WHERE timestamp >= %(start)s AND timestamp <= %(end)s
        GROUP BY ts
        ORDER BY ts
        """,
        parameters={"start": start, "end": end},
    ).result_rows
    parsed = [(row[0].replace(tzinfo=None) if getattr(row[0], "tzinfo", None) else row[0], int(row[1])) for row in rows if row[1]]
    if limit_hours and limit_hours > 0:
        parsed = parsed[-limit_hours:]
    return parsed


def _existing_ok_oracles(ch, timestamp: dt.datetime, oracles: list[str]) -> set[str]:
    if not oracles:
        return set()
    escaped = ", ".join("'" + oracle.replace("'", "''") + "'" for oracle in oracles)
    ts = timestamp.strftime("%Y-%m-%d %H:%M:%S")
    rows = ch.query(
        f"""
        SELECT oracle
        FROM morpho_oracle_snapshots FINAL
        WHERE timestamp = '{ts}'
          AND status = 'OK'
          AND oracle IN ({escaped})
        """
    ).result_rows
    return {str(row[0]).lower() for row in rows}


def run(args) -> int:
    apply_env_from_config(args.config)
    rpc_url = args.rpc_url or default_rpc_url()
    if not rpc_url and not args.dry_run:
        raise SystemExit("MAINNET_RPC_URL is required unless --dry-run is used")
    ch = _ch_client()
    run_id = uuid.uuid4().hex
    started_at = dt.datetime.utcnow().replace(microsecond=0)
    ok_count = 0
    error_count = 0
    try:
        ensure_schema(ch)
        ensure_morpho_oracle_snapshot_tables(ch)
        oracles, meta = _eligible_oracles(ch, args)
        if not oracles:
            print(json.dumps({"run_id": run_id, "selected_oracles": 0, "reason": "no eligible active oracles"}, default=str, indent=2))
            return 0
        start = _parse_ts(args.start) or meta.get("min_creation_timestamp") or dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        end = _parse_ts(args.end) or dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        if start > end:
            raise SystemExit("--start must be <= --end")
        hours = _hour_blocks(ch, start, end, args.limit_hours)
        oracle_start_timestamps = meta.pop("oracle_start_timestamps", {})
        summary = {
            "run_id": run_id,
            "dry_run": args.dry_run,
            "start": start,
            "end": end,
            "hour_count": len(hours),
            **meta,
        }
        print(json.dumps(summary, default=str, indent=2))
        if args.dry_run:
            return 0
        for idx, (ts, block_number) in enumerate(hours, 1):
            active_oracles = [
                oracle for oracle in oracles
                if (oracle_start_timestamps.get(oracle) or start) <= ts
            ]
            todo = active_oracles
            if args.skip_existing:
                existing = _existing_ok_oracles(ch, ts, active_oracles)
                todo = [oracle for oracle in active_oracles if oracle not in existing]
            if not todo:
                continue
            results = batch_call_oracle_prices(
                rpc_url,
                todo,
                block_number,
                ts,
                batch_size=args.rpc_batch_size,
                timeout_sec=args.http_timeout_sec,
                retries=args.retries,
            )
            insert_oracle_snapshot_results(ch, results)
            ok_count += sum(1 for result in results if result.status == "OK")
            error_count += sum(1 for result in results if result.status != "OK")
            if idx % max(1, args.progress_every) == 0:
                print(f"hour {idx}/{len(hours)} ts={ts} block={block_number} calls={len(todo)} ok={ok_count} errors={error_count}", flush=True)
        finished_at = dt.datetime.utcnow().replace(microsecond=0)
        ch.insert(
            "morpho_oracle_backfill_runs",
            [[
                run_id,
                started_at,
                finished_at,
                start,
                end,
                len(oracles),
                len(hours),
                ok_count,
                error_count,
                0,
                json.dumps({k: str(v) for k, v in meta.items()}),
            ]],
            column_names=[
                "run_id", "started_at", "finished_at", "start_timestamp", "end_timestamp",
                "oracle_count", "hour_count", "ok_count", "error_count", "dry_run", "details",
            ],
        )
        print(json.dumps({"run_id": run_id, "ok_count": ok_count, "error_count": error_count}, indent=2))
        return 0
    finally:
        ch.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill Morpho oracle.price() snapshots via dRPC")
    parser.add_argument("--config", default=None)
    parser.add_argument("--rpc-url", default=None)
    parser.add_argument("--start", default=None, help="UTC ISO timestamp, defaults to earliest selected market creation")
    parser.add_argument("--end", default=None, help="UTC ISO timestamp, defaults to current hour")
    parser.add_argument("--min-supply-usd", type=float, default=100_000.0)
    parser.add_argument("--min-borrow-usd", type=float, default=1.0)
    parser.add_argument("--max-oracles", type=int, default=None)
    parser.add_argument("--limit-hours", type=int, default=None, help="Limit to latest N selected hours, useful for staged runs")
    parser.add_argument("--rpc-batch-size", type=int, default=100)
    parser.add_argument("--http-timeout-sec", type=int, default=120)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--skip-existing", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--dry-run", action="store_true")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
