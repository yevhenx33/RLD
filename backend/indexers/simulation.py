#!/usr/bin/env python3
"""
simulation.py — Indexer bug-catching simulation.

Runs entirely in-memory against a real Postgres test DB.
No RPC required — injects synthetic log entries directly into the dispatch pipeline.

Bugs targeted:
  1. Missing NOT NULL fields (no defaults) — constraint violations caught early
  2. Candle consistency — open/high/low/close/volume coherence across all 6 resolutions
  3. Single-writer atomicity — simulates mid-block error causing partial write
  4. Watch set expansion — new markets and brokers dynamically added
  5. Duplicate event idempotency — same log processed twice must not double-count
  6. TWAMM order_id collision — same owner/expiry/direction deduped correctly
  7. Broker state update — ActiveTokenSet correctly flips is_active on lp_positions
  8. Cross-market isolation — events for market A must not bleed into market B

Run with:
    DATABASE_URL=postgresql://rld:rld@localhost:5432/rld_indexer_test python simulation.py

Exits 0 if all assertions pass, non-zero if any fail.
"""

import asyncio
import asyncpg
import json
import logging
import os
import math
import random
import sys
import time
from datetime import datetime, timezone

log = logging.getLogger("sim")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s: %(message)s")

DSN = os.getenv("DATABASE_URL", "postgresql://rld:rld@localhost:5432/rld_indexer_test")

# ── Schema ─────────────────────────────────────────────────────────────────

SCHEMA = (
    open(os.path.join(os.path.dirname(__file__), "schema.sql")).read()
)

# ── Helpers ────────────────────────────────────────────────────────────────

RESOLUTIONS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


def bucket(ts: int, secs: int) -> int:
    return (ts // secs) * secs


async def insert_market(conn, market_id="mkt_A", deploy_block=100) -> dict:
    now = int(time.time())
    m = dict(
        market_id=market_id,
        deploy_block=deploy_block,
        deploy_timestamp=now,
        broker_factory=f"0xfactory_{market_id}",
        mock_oracle=f"0xoracle_{market_id}",
        twamm_hook=f"0xhook_{market_id}",
        swap_router=None, bond_factory=None,
        basis_trade_factory=None, broker_executor=None,
        wausdc="0xwausdc", wausdc_symbol="waUSDC",
        wrlp="0xwrlp", wrlp_symbol="wRLP",
        pool_id=f"0xpool_{market_id}",
        pool_fee=500, tick_spacing=5,
        min_col_ratio="1500000000000000000",
        maintenance_margin="1200000000000000000",
        liq_close_factor="500000000000000000",
        funding_period_sec=2592000,
        debt_cap="10000000000",
        created_at=datetime.now(timezone.utc),
    )
    await conn.execute("""
        INSERT INTO markets VALUES (
            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,
            $11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23
        ) ON CONFLICT DO NOTHING
    """, *m.values())
    await conn.execute("""
        INSERT INTO indexer_state (market_id, last_indexed_block, total_events)
        VALUES ($1, $2, 0) ON CONFLICT DO NOTHING
    """, market_id, deploy_block)
    return m


async def insert_broker(conn, market_id, broker_addr="0xbroker1", owner="0xowner1",
                        created_block=101, tx="0xtx1"):
    await conn.execute("""
        INSERT INTO brokers (address, market_id, owner, created_block, created_tx)
        VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING
    """, broker_addr, market_id, owner, created_block, tx)


async def insert_swap(conn, market_id, block_number, block_timestamp,
                      mark_price=1.05, index_price=1.04,
                      volume_usd=500.0, tx_hash=None, log_index=0):
    """
    Simulates how the main loop processes a Swap event:
    1. Try to insert raw event (ON CONFLICT DO NOTHING)
    2. If event was new (rowcount=1), call handlers
    3. If duplicate (rowcount=0), skip handlers entirely

    This is the correct idempotency model — handlers are never called twice.
    """
    tx_hash = tx_hash or f"0x{'0'*62}{block_number:02x}"

    # First ensure block_state has index_price (simulating market handler running first)
    await conn.execute("""
        INSERT INTO block_states (market_id, block_number, block_timestamp, index_price)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (market_id, block_number) DO UPDATE SET index_price = EXCLUDED.index_price
    """, market_id, block_number, block_timestamp, index_price)

    import sys; sys.path.insert(0, os.path.dirname(__file__))
    from handlers.pool import handle_swap

    # Try to record the event — DO NOTHING on duplicate
    result = await conn.execute("""
        INSERT INTO events (market_id, block_number, block_timestamp, tx_hash, log_index,
                            event_name, contract_address, data)
        VALUES ($1,$2,$3,$4,$5,'Swap','0xpool',$6)
        ON CONFLICT (tx_hash, log_index) DO NOTHING
    """, market_id, block_number, block_timestamp, tx_hash, log_index,
         json.dumps({"mark_price": mark_price, "volume_usd": volume_usd}))

    # Only run handlers if this is a new event (rowcount=1 → INSERT 0 1)
    if result == "INSERT 0 1":
        await handle_swap(
            conn, market_id, block_number, block_timestamp,
            sqrt_price_x96=int(math.sqrt(mark_price) * 2**96),
            tick=round(math.log(mark_price) / math.log(1.0001)),
            amount0=int(-volume_usd * 0.9 * 1e6),
            amount1=int(volume_usd * 1e6),
            liquidity=10_000_000_000,
        )


# ── Test cases ─────────────────────────────────────────────────────────────

async def test_schema_no_defaults(conn):
    """Bug 1: Verify that omitting required fields raises ConstraintError (no silent defaults)."""
    log.info("TEST 1: Schema enforces NOT NULL without defaults")
    try:
        # Try inserting a broker without created_block (NOT NULL, no default)
        await conn.execute("""
            INSERT INTO brokers (address, market_id, owner, created_tx)
            VALUES ('0xbad', 'mkt_A', '0xowner', '0xtx')
        """)
        raise AssertionError("Should have raised — created_block is NOT NULL")
    except asyncpg.NotNullViolationError as e:
        log.info("  ✓ NOT NULL enforced: %s", e.column_name)
    try:
        # Try inserting candle without swap_count (NOT NULL, no default)
        await conn.execute("""
            INSERT INTO candles (market_id, resolution, bucket,
              index_open, index_high, index_low, index_close,
              mark_open, mark_high, mark_low, mark_close, volume_usd)
            VALUES ('mkt_A', '1h', 3600, 1,1,1,1, 1,1,1,1, 100)
        """)
        raise AssertionError("Should have raised — swap_count is NOT NULL")
    except asyncpg.NotNullViolationError as e:
        log.info("  ✓ NOT NULL enforced: %s", e.column_name)


async def test_candle_consistency(conn):
    """Bug 2: Candles across resolutions must be consistent for the same swaps."""
    log.info("TEST 2: Candle consistency across resolutions")
    market_id = "mkt_A"
    base_ts = 1_700_000_040  # aligned to 60s boundary (1_700_000_040 % 60 == 0)

    prices_volumes = [
        (1.00, 100.0),
        (1.05, 200.0),
        (0.98, 150.0),
        (1.10, 300.0),
        (1.02, 120.0),
    ]

    for i, (price, vol) in enumerate(prices_volumes):
        ts = base_ts + i * 10  # 10s apart — all within the same 60s 1m bucket
        async with conn.transaction():
            await insert_swap(conn, market_id, block_number=200 + i,
                              block_timestamp=ts, mark_price=price,
                              volume_usd=vol, log_index=i)

    total_volume = sum(v for _, v in prices_volumes)
    expected_high = max(p for p, _ in prices_volumes)
    expected_low = min(p for p, _ in prices_volumes)

    # All 5 swaps fit in the same 1m, 5m, 15m, 1h, 4h, 1d bucket
    for res, secs in RESOLUTIONS.items():
        b = bucket(base_ts, secs)
        row = await conn.fetchrow(
            "SELECT * FROM candles WHERE market_id=$1 AND resolution=$2 AND bucket=$3",
            market_id, res, b
        )
        assert row is not None, f"Missing candle for resolution={res}"
        assert row["swap_count"] == len(prices_volumes), \
            f"[{res}] swap_count={row['swap_count']} expected {len(prices_volumes)}"
        assert abs(float(row["volume_usd"]) - total_volume) < 0.01, \
            f"[{res}] volume_usd={row['volume_usd']} expected {total_volume:.2f}"
        assert abs(float(row["mark_high"]) - expected_high) < 1e-6, \
            f"[{res}] mark_high={row['mark_high']} expected {expected_high}"
        assert abs(float(row["mark_low"]) - expected_low) < 1e-6, \
            f"[{res}] mark_low={row['mark_low']} expected {expected_low}"
        # open = first swap price, close = last swap price
        assert abs(float(row["mark_open"]) - prices_volumes[0][0]) < 1e-6, \
            f"[{res}] mark_open={row['mark_open']} expected {prices_volumes[0][0]}"
        assert abs(float(row["mark_close"]) - prices_volumes[-1][0]) < 1e-6, \
            f"[{res}] mark_close={row['mark_close']} expected {prices_volumes[-1][0]}"
        log.info("  ✓ resolution=%s bucket=%d volume=%.2f high=%.4f low=%.4f count=%d",
                 res, b, float(row["volume_usd"]), float(row["mark_high"]),
                 float(row["mark_low"]), row["swap_count"])


async def test_atomicity_on_error(conn):
    """Bug 3: A mid-batch error must not partially commit."""
    log.info("TEST 3: Transaction atomicity — partial write rolls back")
    market_id = "mkt_A"
    block_number = 9999
    ts = 1_700_001_000

    initial_count = await conn.fetchval("SELECT COUNT(*) FROM events WHERE market_id=$1", market_id)

    try:
        async with conn.transaction():
            # Good insert
            await conn.execute("""
                INSERT INTO events (market_id, block_number, block_timestamp, tx_hash,
                    log_index, event_name, contract_address, data)
                VALUES ($1,$2,$3,'0xbad_tx',0,'Swap','0xpool',$4)
            """, market_id, block_number, ts, json.dumps({}))

            # Bad insert — violates NOT NULL
            await conn.execute("""
                INSERT INTO brokers (address, market_id, owner, created_tx)
                VALUES ('0xrollback_test', $1, '0xowner', '0xtx')
            """, market_id)  # missing created_block → should raise

        raise AssertionError("Should have raised inside transaction")
    except asyncpg.NotNullViolationError:
        pass  # expected

    final_count = await conn.fetchval("SELECT COUNT(*) FROM events WHERE market_id=$1", market_id)
    assert final_count == initial_count, \
        f"Atomicity failed: event count changed from {initial_count} to {final_count}"
    log.info("  ✓ Rolled back cleanly — event count unchanged at %d", final_count)


async def test_duplicate_idempotency(conn):
    """Bug 4: Processing the same log twice must not double-count volume."""
    log.info("TEST 4: Duplicate event idempotency")
    market_id = "mkt_A"
    block_number = 500
    ts = 1_700_002_000

    before = await conn.fetchrow(
        "SELECT volume_usd, swap_count FROM candles WHERE market_id=$1 AND resolution='1h' AND bucket=$2",
        market_id, bucket(ts, 3600)
    )
    before_vol = float(before["volume_usd"]) if before else 0.0
    before_count = before["swap_count"] if before else 0

    # Insert same swap twice — second must be a no-op
    for _ in range(2):
        async with conn.transaction():
            await insert_swap(conn, market_id, block_number, ts,
                              mark_price=1.03, volume_usd=777.0,
                              tx_hash="0xdupe_tx", log_index=0)

    after = await conn.fetchrow(
        "SELECT volume_usd, swap_count FROM candles WHERE market_id=$1 AND resolution='1h' AND bucket=$2",
        market_id, bucket(ts, 3600)
    )
    assert abs(float(after["volume_usd"]) - (before_vol + 777.0)) < 0.01, \
        f"Duplicate caused double-count: volume={after['volume_usd']} expected {before_vol + 777.0}"
    assert after["swap_count"] == before_count + 1, \
        f"Duplicate caused double swap_count: {after['swap_count']} expected {before_count + 1}"
    log.info("  ✓ Duplicate ignored: volume=%.2f count=%d", float(after["volume_usd"]), after["swap_count"])


async def test_cross_market_isolation(conn):
    """Bug 5: Events for market B must not appear in market A queries."""
    log.info("TEST 5: Cross-market isolation")
    await insert_market(conn, market_id="mkt_B", deploy_block=200)
    await insert_broker(conn, "mkt_B", broker_addr="0xbroker_B", owner="0xowner_B",
                        created_block=201, tx="0xtx_B")

    ts = 1_700_003_000
    async with conn.transaction():
        await insert_swap(conn, "mkt_B", block_number=600, block_timestamp=ts,
                          mark_price=2.00, volume_usd=9999.0)

    # mkt_A should have zero events at this timestamp
    row_a = await conn.fetchrow(
        "SELECT volume_usd FROM candles WHERE market_id='mkt_A' AND resolution='1h' AND bucket=$1",
        bucket(ts, 3600)
    )
    assert row_a is None, f"mkt_A contaminated by mkt_B swap: volume={row_a['volume_usd'] if row_a else None}"

    # mkt_B should have the swap
    row_b = await conn.fetchrow(
        "SELECT volume_usd FROM candles WHERE market_id='mkt_B' AND resolution='1h' AND bucket=$1",
        bucket(ts, 3600)
    )
    assert row_b is not None, "mkt_B candle missing"
    assert abs(float(row_b["volume_usd"]) - 9999.0) < 0.01, \
        f"mkt_B volume wrong: {row_b['volume_usd']}"
    log.info("  ✓ Cross-market isolation: mkt_A clean, mkt_B volume=%.2f", float(row_b["volume_usd"]))


async def test_active_token_flip(conn):
    """Bug 6: ActiveTokenSet must flip is_active correctly on lp_positions."""
    log.info("TEST 6: ActiveTokenSet flips is_active correctly")
    from handlers.broker import handle_active_token_set

    await insert_broker(conn, "mkt_A", broker_addr="0xbroker_lp", owner="0xowner_lp",
                        created_block=300, tx="0xtx_lp")

    # Insert two LP positions: token 1 and token 2
    for token_id, is_active in [(1, True), (2, False)]:
        await conn.execute("""
            INSERT INTO lp_positions
              (token_id, market_id, broker_address, liquidity, tick_lower, tick_upper,
               mint_block, is_active, is_burned)
            VALUES ($1,'mkt_A','0xbroker_lp','1000000',-100,100, 300, $2, FALSE)
            ON CONFLICT DO NOTHING
        """, token_id, is_active)

    # Set token 2 as active
    async with conn.transaction():
        await handle_active_token_set(conn, "0xbroker_lp", token_id=2)

    pos1 = await conn.fetchrow("SELECT is_active FROM lp_positions WHERE token_id=1")
    pos2 = await conn.fetchrow("SELECT is_active FROM lp_positions WHERE token_id=2")
    assert pos1["is_active"] is False, f"token_id=1 should be inactive, got {pos1['is_active']}"
    assert pos2["is_active"] is True,  f"token_id=2 should be active, got {pos2['is_active']}"
    log.info("  ✓ ActiveTokenSet: token1.is_active=%s token2.is_active=%s",
             pos1["is_active"], pos2["is_active"])


async def test_twamm_order_id_dedup(conn):
    """Bug 7: Same TWAMM order submitted twice must not create two rows."""
    log.info("TEST 7: TWAMM order_id deduplication")
    from handlers.twamm import handle_submit_order

    owner = "0x" + "1" * 40   # valid 40-hex-char Ethereum address
    expiration = 1_800_000_000
    zero_for_one = True
    amount_in = 1_000_000_000

    for i in range(2):
        async with conn.transaction():
            await handle_submit_order(
                conn, "mkt_A", owner, expiration=expiration,
                start_epoch=1_700_000_000, zero_for_one=zero_for_one,
                amount_in=amount_in, block_number=700 + i,
                tx_hash=f"0xtwamm_tx_{i}"
            )

    count = await conn.fetchval(
        "SELECT COUNT(*) FROM twamm_orders WHERE owner=$1 AND expiration=$2",
        owner.lower(), expiration
    )
    assert count == 1, f"TWAMM order duplicated: count={count}"
    log.info("  ✓ TWAMM order_id dedup: count=1")


async def test_watch_set_expansion(conn):
    """Bug 8: New market registered after indexer start must appear in watch set."""
    log.info("TEST 8: Watch set expansion on new market")
    import sys; sys.path.insert(0, os.path.dirname(__file__))
    from indexer import build_watch_set

    global_cfg = {
        "rld_core": "0xcore",
        "v4_pool_manager": "0xpool_manager",
        "v4_position_manager": "0xposm",
    }

    # Before mkt_B is in markets table (it's there from test 5), include it
    watched = await build_watch_set(conn, global_cfg)
    assert "0xfactory_mkt_b" in watched or "0xfactory_mkt_B" in {w.lower() for w in watched}, \
        f"mkt_B factory not in watch set: {watched}"
    assert "0xhook_mkt_a" in {w.lower() for w in watched}, \
        f"mkt_A hook not in watch set"
    log.info("  ✓ Watch set contains %d addresses", len(watched))


async def test_multi_resolution_different_buckets(conn):
    """Bug 9: Swaps in different 1m buckets but same 1h bucket must aggregate correctly."""
    log.info("TEST 9: Multi-resolution bucket aggregation")
    market_id = "mkt_A"
    hour_start = 1_700_100_000  # aligned to hour

    # 3 swaps: minute 0, minute 1, minute 2 — all in same hour bucket
    for i in range(3):
        ts = hour_start + i * 61  # 61s apart → different 1m buckets
        async with conn.transaction():
            await insert_swap(conn, market_id, block_number=800 + i,
                              block_timestamp=ts, mark_price=1.0 + i * 0.01,
                              volume_usd=100.0, log_index=i)

    # Should have 3 separate 1m candles
    count_1m = await conn.fetchval(
        "SELECT COUNT(*) FROM candles WHERE market_id=$1 AND resolution='1m' AND bucket >= $2 AND bucket < $3",
        market_id, hour_start, hour_start + 3600
    )
    assert count_1m == 3, f"Expected 3 1m candles, got {count_1m}"

    # But only 1 1h candle covering all three
    row_1h = await conn.fetchrow(
        "SELECT swap_count, volume_usd FROM candles WHERE market_id=$1 AND resolution='1h' AND bucket=$2",
        market_id, bucket(hour_start, 3600)
    )
    assert row_1h is not None, "1h candle missing"
    assert row_1h["swap_count"] == 3, f"1h swap_count={row_1h['swap_count']} expected 3"
    assert abs(float(row_1h["volume_usd"]) - 300.0) < 0.01, \
        f"1h volume_usd={row_1h['volume_usd']} expected 300.0"
    log.info("  ✓ 3 separate 1m candles, 1 aggregated 1h candle with count=3 vol=%.2f",
             float(row_1h["volume_usd"]))


# ── Main ───────────────────────────────────────────────────────────────────

async def main():
    log.info("Connecting to %s", DSN)
    conn = await asyncpg.connect(DSN)

    try:
        # Drop and recreate schema for clean state
        log.info("Dropping and recreating test schema...")
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        await conn.execute(SCHEMA)

        # Seed market A and a broker for most tests
        await insert_market(conn, market_id="mkt_A", deploy_block=100)
        await insert_broker(conn, "mkt_A", broker_addr="0xbroker1",
                            owner="0xowner1", created_block=101, tx="0xtx1")

        tests = [
            test_schema_no_defaults,
            test_candle_consistency,
            test_atomicity_on_error,
            test_duplicate_idempotency,
            test_cross_market_isolation,
            test_active_token_flip,
            test_twamm_order_id_dedup,
            test_watch_set_expansion,
            test_multi_resolution_different_buckets,
        ]

        passed = 0
        failed = 0
        for test_fn in tests:
            try:
                await test_fn(conn)
                passed += 1
            except Exception as e:
                log.error("FAILED %s: %s", test_fn.__name__, e, exc_info=True)
                failed += 1

        log.info("")
        log.info("═" * 50)
        log.info("Results: %d passed, %d failed", passed, failed)
        log.info("═" * 50)

        if failed:
            sys.exit(1)

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
