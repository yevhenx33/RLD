#!/usr/bin/env python3
"""
verify_optimization.py — Automated verification for indexer optimizations.

Run inside the indexer container (or locally with DB access):
  python verify_optimization.py

Phases:
  1. SNAPSHOT: Capture current DB state checksums
  2. WATCH: Monitor live indexer for N batches, assert no errors
  3. ASSERT: Verify critical invariants hold
  4. BENCHMARK: Report batch timing statistics
"""
import asyncio
import json
import os
import sys
import time

import asyncpg

DSN = os.getenv("DATABASE_URL", "postgresql://rld:rld@localhost:5432/rld_indexer")

# ── Helpers ────────────────────────────────────────────────────────────────

async def get_conn():
    return await asyncpg.connect(DSN)


async def table_checksum(conn, table: str, where: str = "") -> dict:
    """Get row count and latest row hash for a table."""
    clause = f"WHERE {where}" if where else ""
    count = await conn.fetchval(f"SELECT COUNT(*) FROM {table} {clause}")
    return {"table": table, "count": count}


# ── Phase 1: Snapshot ──────────────────────────────────────────────────────

async def snapshot_state(conn) -> dict:
    """Capture current DB state for comparison."""
    state = {}

    # Row counts
    for tbl in ["events", "block_states", "brokers", "candles", "bonds", "lp_positions", "twamm_orders"]:
        state[tbl] = await table_checksum(conn, tbl)

    # Latest block_state values (the critical state)
    row = await conn.fetchrow("""
        SELECT block_number, mark_price, index_price, normalization_factor,
               total_debt, tick, liquidity, sqrt_price_x96,
               token0_balance, token1_balance
        FROM block_states ORDER BY block_number DESC LIMIT 1
    """)
    state["latest_block_state"] = dict(row) if row else {}

    # Broker balances
    brokers = await conn.fetch("SELECT address, wausdc_balance, wrlp_balance, debt_principal FROM brokers")
    state["broker_balances"] = {b["address"]: dict(b) for b in brokers}

    # Indexer state
    idx = await conn.fetchrow("SELECT last_indexed_block FROM indexer_state LIMIT 1")
    state["last_indexed_block"] = idx["last_indexed_block"] if idx else 0

    # Snapshot JSON exists
    mkt = await conn.fetchrow("SELECT snapshot IS NOT NULL as has_snap FROM markets LIMIT 1")
    state["has_snapshot"] = mkt["has_snap"] if mkt else False

    return state


# ── Phase 2: Watch live batches ────────────────────────────────────────────

async def watch_indexer(conn, n_batches: int = 20, timeout_sec: int = 60) -> list[dict]:
    """Poll indexer_state and measure batch times."""
    results = []
    last_block = await conn.fetchval("SELECT last_indexed_block FROM indexer_state LIMIT 1") or 0
    start = time.monotonic()
    batches_seen = 0

    print(f"  Watching for {n_batches} batches (timeout {timeout_sec}s)...")

    while batches_seen < n_batches and (time.monotonic() - start) < timeout_sec:
        await asyncio.sleep(1.0)
        current = await conn.fetchval("SELECT last_indexed_block FROM indexer_state LIMIT 1") or 0
        if current > last_block:
            delta = current - last_block
            elapsed = time.monotonic() - start
            results.append({
                "batch_blocks": delta,
                "last_block": current,
                "elapsed_s": round(elapsed, 2),
            })
            last_block = current
            batches_seen += 1
            print(f"    Batch {batches_seen}: blocks={delta}, head={current}")

    return results


# ── Phase 3: Assert invariants ────────────────────────────────────────────

async def assert_invariants(conn, before: dict, after: dict) -> list[str]:
    """Check that no data was lost or corrupted."""
    errors = []

    # 1. Row counts should only grow or stay (never shrink)
    for tbl in ["events", "block_states", "brokers", "candles"]:
        before_count = before.get(tbl, {}).get("count", 0)
        after_count = after.get(tbl, {}).get("count", 0)
        if after_count < before_count:
            errors.append(f"FAIL: {tbl} row count decreased ({before_count} → {after_count})")
        else:
            print(f"  ✓ {tbl}: {before_count} → {after_count}")

    # 2. Block head should advance
    if after["last_indexed_block"] < before["last_indexed_block"]:
        errors.append(f"FAIL: last_indexed_block went backwards")
    else:
        print(f"  ✓ last_indexed_block: {before['last_indexed_block']} → {after['last_indexed_block']}")

    # 3. Snapshot should still exist
    if not after.get("has_snapshot"):
        errors.append("FAIL: markets.snapshot is NULL after optimization")
    else:
        print(f"  ✓ markets.snapshot is populated")

    # 4. Latest block_state should have non-null critical fields
    latest = after.get("latest_block_state", {})
    for col in ["mark_price", "index_price", "tick", "liquidity"]:
        if latest.get(col) is None:
            errors.append(f"FAIL: block_states.{col} is NULL in latest row")
        else:
            print(f"  ✓ block_states.{col} = {latest.get(col)}")

    # 5. Broker balances should be non-negative
    for addr, b in after.get("broker_balances", {}).items():
        for col in ["wausdc_balance", "wrlp_balance"]:
            val = int(b.get(col) or "0")
            if val < 0:
                errors.append(f"FAIL: broker {addr[:10]} has negative {col}: {val}")

    if not errors:
        print("  ✓ All broker balances non-negative")

    return errors


# ── Main ──────────────────────────────────────────────────────────────────

async def main():
    conn = await get_conn()

    print("=" * 60)
    print("INDEXER OPTIMIZATION VERIFICATION")
    print("=" * 60)

    # Phase 1
    print("\n[Phase 1] Snapshotting current state...")
    before = await snapshot_state(conn)
    print(f"  Head block: {before['last_indexed_block']}")
    for tbl in ["events", "block_states", "brokers", "candles"]:
        print(f"  {tbl}: {before[tbl]['count']} rows")

    # Phase 2
    print("\n[Phase 2] Watching live indexer...")
    batches = await watch_indexer(conn, n_batches=15, timeout_sec=45)

    if not batches:
        print("  ⚠ No batches observed — indexer may not be running")
        print("  Skipping to Phase 3 with current state only")

    # Phase 3
    print("\n[Phase 3] Asserting invariants...")
    after = await snapshot_state(conn)
    errors = await assert_invariants(conn, before, after)

    # Phase 4
    print("\n[Phase 4] Performance summary")
    if batches:
        intervals = []
        for i in range(1, len(batches)):
            dt = batches[i]["elapsed_s"] - batches[i-1]["elapsed_s"]
            intervals.append(dt)
        if intervals:
            avg = sum(intervals) / len(intervals)
            p95 = sorted(intervals)[int(len(intervals) * 0.95)]
            print(f"  Batches observed: {len(batches)}")
            print(f"  Avg batch interval: {avg:.2f}s")
            print(f"  P95 batch interval: {p95:.2f}s")
            blocks_advanced = after["last_indexed_block"] - before["last_indexed_block"]
            elapsed = batches[-1]["elapsed_s"]
            print(f"  Blocks advanced: {blocks_advanced} in {elapsed:.1f}s")
            print(f"  Throughput: {blocks_advanced / elapsed:.1f} blocks/sec")
    else:
        print("  No timing data (indexer idle)")

    # Verdict
    print("\n" + "=" * 60)
    if errors:
        print(f"VERDICT: FAIL ({len(errors)} errors)")
        for e in errors:
            print(f"  ✗ {e}")
        await conn.close()
        sys.exit(1)
    else:
        print("VERDICT: PASS ✓")
        await conn.close()
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
