"""
Backfill historical MetaMorpho vault allocations from Morpho Blue events.

Reconstructs vault-level supply positions per market at hourly granularity by
replaying Supply/Withdraw events from `morpho_market_events` and joining with
`morpho_market_metrics` for USD valuation.

Architecture
------------
    morpho_market_events (Supply/Withdraw, on_behalf = vault)
        → cumulative vault_supply_shares per (vault, market, hour)
    morpho_market_events (ALL Supply/Withdraw for target markets)
        → cumulative total_supply_shares per (market, hour)
    morpho_market_metrics (hourly supply_usd per market)
        → vault_allocation_usd = (vault_shares / total_shares) * supply_usd

Output: rows inserted into `metamorpho_vault_allocations` table.

Usage
-----
    python -m analytics.scripts.backfill_vault_allocation_history [--config .env]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from collections import defaultdict
from typing import Optional

try:
    import clickhouse_connect
except ImportError:
    print("clickhouse_connect is required", file=sys.stderr)
    sys.exit(1)


def _ch_client():
    settings = {}
    if os.getenv("CLICKHOUSE_ASYNC_INSERT", "true").strip().lower() in {"1", "true", "yes"}:
        settings["async_insert"] = 1
        settings["wait_for_async_insert"] = (
            1
            if os.getenv("CLICKHOUSE_WAIT_FOR_ASYNC_INSERT", "true").strip().lower()
            in {"1", "true", "yes"}
            else 0
        )
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "127.0.0.1"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
        database=os.getenv("CLICKHOUSE_DATABASE", "default"),
        settings=settings,
    )


def _apply_env_from_config(config_path: Optional[str]) -> None:
    if not config_path:
        for candidate in (".env", "../.env"):
            if os.path.isfile(candidate):
                config_path = candidate
                break
    if not config_path or not os.path.isfile(config_path):
        return
    with open(config_path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


def _to_hour(ts) -> dt.datetime:
    """Truncate a datetime (or datetime-like) to the start of its hour."""
    if hasattr(ts, "replace"):
        return ts.replace(minute=0, second=0, microsecond=0)
    return ts


# ---------------------------------------------------------------------------
# Phase 1: Fetch vault addresses and determine target markets
# ---------------------------------------------------------------------------


def _get_vault_addresses(ch) -> set[str]:
    rows = ch.query(
        "SELECT DISTINCT vault_address FROM metamorpho_vault_registry FINAL"
    ).result_rows
    return {str(row[0]).lower() for row in rows if row[0]}


def _get_target_markets(ch, vault_addresses: set[str]) -> set[str]:
    """Markets that have at least one Supply/Withdraw by a MetaMorpho vault."""
    escaped = ", ".join(f"'{v}'" for v in sorted(vault_addresses))
    rows = ch.query(
        f"""
        SELECT DISTINCT market_id
        FROM morpho_market_events FINAL
        WHERE event_name IN ('Supply', 'Withdraw')
          AND on_behalf IN ({escaped})
        """
    ).result_rows
    return {str(row[0]).lower() for row in rows if row[0]}


# ---------------------------------------------------------------------------
# Phase 2: Fetch events and build hourly share snapshots
# ---------------------------------------------------------------------------


def _fetch_share_events(ch, market_id: str) -> list[tuple]:
    """
    Return all (timestamp, on_behalf, event_name, shares) tuples for a market,
    ordered by block_number + log_index.  Only Supply/Withdraw events are
    relevant for supply-share accounting.
    """
    rows = ch.query(
        """
        SELECT timestamp, on_behalf, event_name, shares
        FROM morpho_market_events FINAL
        WHERE market_id = %(mid)s
          AND event_name IN ('Supply', 'Withdraw')
        ORDER BY block_number, log_index
        """,
        parameters={"mid": market_id},
    ).result_rows
    return rows


def _build_hourly_share_snapshots(
    events: list[tuple],
    vault_addresses: set[str],
) -> dict[dt.datetime, dict]:
    """
    Replay events chronologically to build cumulative share positions.

    Returns {hour: {"total_shares": int, "vaults": {addr: shares}}}

    Between events, the state is constant (forward-filled).
    """
    total_shares = 0
    vault_shares: dict[str, int] = defaultdict(int)
    hourly: dict[dt.datetime, dict] = {}
    current_hour: Optional[dt.datetime] = None

    def _snapshot(hour: dt.datetime) -> None:
        # Only include vaults with positive shares.
        hourly[hour] = {
            "total_shares": total_shares,
            "vaults": {addr: shares for addr, shares in vault_shares.items() if shares > 0},
        }

    for ts, on_behalf, event_name, shares_raw in events:
        ts_dt = ts.replace(tzinfo=None) if hasattr(ts, "tzinfo") and ts.tzinfo else ts
        hour = _to_hour(ts_dt)
        shares = int(shares_raw or 0)
        user = str(on_behalf).lower()

        # When we cross an hour boundary, snapshot the PREVIOUS hour state.
        if current_hour is not None and hour != current_hour:
            _snapshot(current_hour)
        current_hour = hour

        if event_name == "Supply":
            total_shares += shares
            if user in vault_addresses:
                vault_shares[user] += shares
        elif event_name == "Withdraw":
            total_shares = max(0, total_shares - shares)
            if user in vault_addresses:
                vault_shares[user] = max(0, vault_shares[user] - shares)

    # Final snapshot for the last hour.
    if current_hour is not None:
        _snapshot(current_hour)

    return hourly


def _forward_fill_hours(
    hourly: dict[dt.datetime, dict],
    start: dt.datetime,
    end: dt.datetime,
) -> dict[dt.datetime, dict]:
    """
    Forward-fill share snapshots for every hour from `start` to `end`.
    Hours without events inherit the state from the most recent event hour.
    """
    if not hourly:
        return {}

    filled: dict[dt.datetime, dict] = {}
    last_state: Optional[dict] = None
    cursor = start

    while cursor <= end:
        if cursor in hourly:
            last_state = hourly[cursor]
        if last_state is not None:
            filled[cursor] = last_state
        cursor += dt.timedelta(hours=1)

    return filled


# ---------------------------------------------------------------------------
# Phase 3: Join with morpho_market_metrics for USD valuation
# ---------------------------------------------------------------------------


def _fetch_market_supply_usd(ch, market_id: str) -> dict[dt.datetime, float]:
    """Return {hour: supply_usd} from the hourly metrics table."""
    rows = ch.query(
        """
        SELECT toStartOfHour(timestamp) AS ts, argMax(supply_usd, timestamp) AS supply_usd
        FROM morpho_market_metrics
        WHERE market_id = %(mid)s
        GROUP BY ts
        ORDER BY ts
        """,
        parameters={"mid": market_id},
    ).result_rows
    result: dict[dt.datetime, float] = {}
    for ts, supply_usd in rows:
        ts_dt = ts.replace(tzinfo=None) if hasattr(ts, "tzinfo") and ts.tzinfo else ts
        result[ts_dt] = float(supply_usd or 0.0)
    return result


# ---------------------------------------------------------------------------
# Phase 4: Determine the backfill time range
# ---------------------------------------------------------------------------


def _existing_allocation_range(ch) -> Optional[dt.datetime]:
    """Return the earliest existing timestamp in metamorpho_vault_allocations."""
    rows = ch.query(
        "SELECT min(timestamp) FROM metamorpho_vault_allocations"
    ).result_rows
    if rows and rows[0][0]:
        ts = rows[0][0]
        return ts.replace(tzinfo=None) if hasattr(ts, "tzinfo") and ts.tzinfo else ts
    return None


def _event_time_range(ch) -> tuple[Optional[dt.datetime], Optional[dt.datetime]]:
    rows = ch.query(
        "SELECT min(timestamp), max(timestamp) FROM morpho_market_events WHERE event_name IN ('Supply', 'Withdraw')"
    ).result_rows
    if not rows or not rows[0][0]:
        return None, None
    mn = rows[0][0]
    mx = rows[0][1]
    mn = mn.replace(tzinfo=None) if hasattr(mn, "tzinfo") and mn.tzinfo else mn
    mx = mx.replace(tzinfo=None) if hasattr(mx, "tzinfo") and mx.tzinfo else mx
    return mn, mx


# ---------------------------------------------------------------------------
# Phase 5: Write allocation rows
# ---------------------------------------------------------------------------


def _write_allocations(
    ch,
    market_id: str,
    filled_shares: dict[dt.datetime, dict],
    supply_usd_by_hour: dict[dt.datetime, float],
    batch_size: int = 50_000,
) -> int:
    rows = []
    for hour in sorted(filled_shares):
        state = filled_shares[hour]
        total_shares = state["total_shares"]
        vaults = state["vaults"]
        if total_shares <= 0 or not vaults:
            continue
        supply_usd = supply_usd_by_hour.get(hour)
        if supply_usd is None:
            # Try nearest prior hour.
            candidates = [h for h in supply_usd_by_hour if h <= hour]
            if candidates:
                supply_usd = supply_usd_by_hour[max(candidates)]
            else:
                supply_usd = 0.0

        for vault_addr, vault_s in vaults.items():
            fraction = vault_s / total_shares if total_shares > 0 else 0.0
            supplied_usd = fraction * supply_usd if supply_usd > 0 else 0.0
            supplied_assets_approx = str(vault_s)
            rows.append([
                hour,               # timestamp
                0,                  # block_number (not available for reconstructed)
                vault_addr,         # vault_address
                market_id,          # market_id
                "0",                # cap (unknown historically)
                supplied_assets_approx,  # supplied_assets (share units)
                supplied_usd,       # supplied_usd
                fraction,           # allocation_share
                "RECONSTRUCTED",    # snapshot_status
                "",                 # error
            ])

    written = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        ch.insert(
            "metamorpho_vault_allocations",
            batch,
            column_names=[
                "timestamp",
                "block_number",
                "vault_address",
                "market_id",
                "cap",
                "supplied_assets",
                "supplied_usd",
                "allocation_share",
                "snapshot_status",
                "error",
            ],
        )
        written += len(batch)

    return written


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(args) -> int:
    _apply_env_from_config(args.config)
    ch = _ch_client()

    try:
        # Phase 1: Determine scope.
        print("[1/5] Loading vault registry...", flush=True)
        vault_addresses = _get_vault_addresses(ch)
        print(f"  Found {len(vault_addresses)} vault addresses.", flush=True)

        print("[2/5] Finding target markets (markets with vault supply activity)...", flush=True)
        target_markets = _get_target_markets(ch, vault_addresses)
        print(f"  Found {len(target_markets)} target markets.", flush=True)

        # Determine time range.
        event_start, event_end = _event_time_range(ch)
        if not event_start or not event_end:
            print("  No events found. Nothing to backfill.")
            return 0

        existing_earliest = _existing_allocation_range(ch)
        backfill_end = existing_earliest or event_end
        backfill_start = _to_hour(event_start)
        backfill_end = _to_hour(backfill_end)

        if args.full_rebuild:
            backfill_end = _to_hour(event_end)
            print(f"  FULL REBUILD: {backfill_start} → {backfill_end}", flush=True)
        else:
            print(f"  Backfill range: {backfill_start} → {backfill_end}", flush=True)

        if backfill_start >= backfill_end:
            print("  Nothing to backfill (start >= end).")
            return 0

        # Phase 2-5: Process each market.
        total_written = 0
        market_list = sorted(target_markets)
        t0 = time.monotonic()

        for idx, market_id in enumerate(market_list, start=1):
            # Phase 2: Fetch events.
            events = _fetch_share_events(ch, market_id)
            if not events:
                continue

            # Phase 3: Build hourly share snapshots.
            hourly = _build_hourly_share_snapshots(events, vault_addresses)
            if not hourly:
                continue

            filled = _forward_fill_hours(hourly, backfill_start, backfill_end)
            if not filled:
                continue

            # Phase 4: Fetch USD supply values.
            supply_usd_by_hour = _fetch_market_supply_usd(ch, market_id)

            # Phase 5: Write allocation rows.
            written = _write_allocations(ch, market_id, filled, supply_usd_by_hour)
            total_written += written

            if idx % 25 == 0 or idx == len(market_list):
                elapsed = time.monotonic() - t0
                rate = idx / elapsed if elapsed > 0 else 0
                print(
                    json.dumps({
                        "progress": "vault_allocation_backfill",
                        "market": idx,
                        "of": len(market_list),
                        "rows_written": total_written,
                        "elapsed_sec": round(elapsed, 1),
                        "markets_per_sec": round(rate, 2),
                    }),
                    flush=True,
                )

        elapsed = time.monotonic() - t0
        print(
            json.dumps({
                "result": "backfill_complete",
                "total_markets": len(market_list),
                "total_rows_written": total_written,
                "elapsed_sec": round(elapsed, 1),
                "backfill_start": backfill_start.isoformat(),
                "backfill_end": backfill_end.isoformat(),
            }),
            flush=True,
        )
    finally:
        ch.close()

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill historical MetaMorpho vault allocations from Morpho Blue events"
    )
    parser.add_argument("--config", default=None, help="Path to .env file")
    parser.add_argument(
        "--full-rebuild",
        action="store_true",
        help="Ignore existing allocation data and rebuild all hours",
    )
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
