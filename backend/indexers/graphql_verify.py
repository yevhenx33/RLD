#!/usr/bin/env python3
"""
graphql_verify.py — End-to-end verification of all GraphQL resolvers.

Seeds the DB with a known market + runs queries via HTTP against a live
Uvicorn server. Asserts exact return values. Exits 0 if all pass.
"""
import asyncio
import os
import subprocess
import sys
import time
import asyncpg
import httpx

DB = "postgresql://rld:rld_dev_password@localhost:5432/rld_indexer_test"
URL = "http://localhost:8099/graphql"
HEALTH = "http://localhost:8099/healthz"

MARKET_ID = "0xverify_test_market_000000000000000000000000000000000000000000000000"

# ── Seed ─────────────────────────────────────────────────────────────────

async def seed(conn: asyncpg.Connection) -> None:
    """Insert deterministic test data into every table."""

    # Market
    await conn.execute("""
        INSERT INTO markets (
            market_id, deploy_block, deploy_timestamp,
            broker_factory, mock_oracle, twamm_hook,
            swap_router, bond_factory, basis_trade_factory, broker_executor,
            wausdc, wausdc_symbol, wrlp, wrlp_symbol,
            pool_id, pool_fee, tick_spacing,
            min_col_ratio, maintenance_margin, liq_close_factor,
            funding_period_sec, debt_cap, created_at
        ) VALUES (
            $1, 1000, 1700000000,
            '0xbroker_factory', '0xmock_oracle', '0xtwamm_hook',
            '0xswap_router', '0xbond_factory', '0xbasis_trade_factory', '0xbroker_executor',
            '0xwausdc', 'waUSDC', '0xwrlp', 'wRLP',
            '0xpool_id', 3000, 60,
            '1500000000000000000', '1200000000000000000', '500000000000000000',
            2592000, '10000000000000000000000000000', NOW()
        )
        ON CONFLICT (market_id) DO NOTHING
    """, MARKET_ID)

    # Broker
    await conn.execute("""
        INSERT INTO brokers (address, market_id, owner, created_block, created_tx, active_token_id)
        VALUES ('0xbroker_addr', $1, '0xowner_addr', 1001, '0xbrokertx', 42)
        ON CONFLICT (address) DO NOTHING
    """, MARKET_ID)

    # block_states (for pool_snapshot)
    await conn.execute("""
        INSERT INTO block_states (market_id, block_number, block_timestamp,
            sqrt_price_x96, tick, mark_price, index_price, normalization_factor, liquidity)
        VALUES ($1, 2000, 1700001000,
            79228162514264337593543950336, 0, 1.05, 1.04, '1020000000000000000', '500000000000')
        ON CONFLICT (market_id, block_number) DO NOTHING
    """, MARKET_ID)

    # indexer_state
    await conn.execute("""
        INSERT INTO indexer_state (market_id, last_indexed_block, total_events)
        VALUES ($1, 2000, 77)
        ON CONFLICT DO NOTHING
    """, MARKET_ID)

    # Candles: 3 consecutive 1m buckets, 1 1h bucket
    base = 1_700_100_000  # aligned to 60s and 3600s
    for i in range(3):
        ts = base + i * 60
        price_open = 1.0 + i * 0.01
        price_close = price_open + 0.005
        await conn.execute("""
            INSERT INTO candles (
                market_id, resolution, bucket,
                mark_open, mark_high, mark_low, mark_close,
                index_open, index_high, index_low, index_close,
                volume_usd, swap_count
            ) VALUES ($1, '1m', $2,
                $3, $4, $3, $4,
                $3, $4, $3, $4,
                1000.0, 5)
            ON CONFLICT (market_id, resolution, bucket) DO NOTHING
        """, MARKET_ID, ts, price_open, price_close)

    # 1h candle covering all 3 above
    h_bucket = (base // 3600) * 3600
    await conn.execute("""
        INSERT INTO candles (
            market_id, resolution, bucket,
            mark_open, mark_high, mark_low, mark_close,
            index_open, index_high, index_low, index_close,
            volume_usd, swap_count
        ) VALUES ($1, '1h', $2,
            1.00, 1.025, 1.00, 1.025,
            1.00, 1.025, 1.00, 1.025,
            3000.0, 15)
        ON CONFLICT (market_id, resolution, bucket) DO NOTHING
    """, MARKET_ID, h_bucket)

    # LP position — schema: no burn_block, no mint_tx
    await conn.execute("""
        INSERT INTO lp_positions (
            token_id, market_id, broker_address,
            liquidity, tick_lower, tick_upper,
            entry_price, entry_tick, mint_block,
            is_active, is_burned
        ) VALUES (
            9999, $1, '0xbroker_addr',
            '50000000000', -200, 200,
            1.04, 0, 1002,
            TRUE, FALSE
        )
        ON CONFLICT (token_id) DO NOTHING
    """, MARKET_ID)

    # TWAMM order — schema uses block_number/tx_hash/is_cancelled, not submit_block/is_active
    await conn.execute("""
        INSERT INTO twamm_orders (
            order_id, market_id, owner, expiration, zero_for_one,
            amount_in, start_epoch, block_number, tx_hash, is_cancelled
        ) VALUES (
            '0xtwamm_order_id', $1, '0xtrader', 1800000000, TRUE,
            '500000000', 1700000000, 1005, '0xtwammtx', FALSE
        )
        ON CONFLICT (order_id) DO NOTHING
    """, MARKET_ID)


# ── GraphQL helpers ───────────────────────────────────────────────────────

async def gql(client: httpx.AsyncClient, query: str) -> dict:
    resp = await client.post(URL, json={"query": query}, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    if "errors" in body:
        raise AssertionError(f"GraphQL errors: {body['errors']}")
    return body["data"]


# ── Tests ─────────────────────────────────────────────────────────────────

async def run_all_checks(client: httpx.AsyncClient) -> None:
    passed = 0
    failed = 0

    async def check(name: str, query: str, validator):
        nonlocal passed, failed
        try:
            data = await gql(client, query)
            validator(data)
            print(f"  ✓ {name}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {name}: {e}")
            failed += 1

    # 1. markets
    await check("markets — returns our seeded market",
        '{ markets { marketId brokerFactory mockOracle poolFee tickSpacing } }',
        lambda d: (
            assert_in(MARKET_ID, [m["marketId"] for m in d["markets"]]),
            assert_eq(3000, next(m for m in d["markets"] if m["marketId"] == MARKET_ID)["poolFee"]),
        )
    )

    # 2. market (single)
    await check("market — single lookup by id",
        f'{{ market(marketId: "{MARKET_ID}") {{ marketId debtCap twammHook }} }}',
        lambda d: (
            assert_eq(MARKET_ID, d["market"]["marketId"]),
            assert_eq("0xtwamm_hook", d["market"]["twammHook"]),
        )
    )

    # 3. brokers
    await check("brokers — correct address and activeTokenId",
        f'{{ brokers(marketId: "{MARKET_ID}") {{ address owner activeTokenId createdBlock }} }}',
        lambda d: (
            assert_eq(1, len(d["brokers"])),
            assert_eq("0xbroker_addr", d["brokers"][0]["address"]),
            assert_eq(42, d["brokers"][0]["activeTokenId"]),
        )
    )

    # 4. poolSnapshot
    await check("poolSnapshot — mark/index price and block",
        f'{{ poolSnapshot(marketId: "{MARKET_ID}") {{ blockNumber markPrice indexPrice tick liquidity }} }}',
        lambda d: (
            assert_eq(2000, d["poolSnapshot"]["blockNumber"]),
            assert_close(1.05, d["poolSnapshot"]["markPrice"], tol=0.001),
            assert_close(1.04, d["poolSnapshot"]["indexPrice"], tol=0.001),
        )
    )

    # 5. candles (1m)
    base = 1_700_100_000
    await check("candles — 3 x 1m candles correct OHLCV",
        f'{{ candles(marketId: "{MARKET_ID}", resolution: "1m", fromBucket: {base}, toBucket: {base + 200}, limit: 10) {{ bucket markOpen markClose volumeUsd swapCount }} }}',
        lambda d: (
            assert_eq(3, len(d["candles"])),
            assert_eq(base, d["candles"][0]["bucket"]),
            assert_close(1000.0, d["candles"][0]["volumeUsd"], tol=0.01),
            assert_eq(5, d["candles"][0]["swapCount"]),
        )
    )

    # 6. candles (1h) — aggregated
    h_bucket = (base // 3600) * 3600
    await check("candles — 1h candle covers all swaps",
        f'{{ candles(marketId: "{MARKET_ID}", resolution: "1h", fromBucket: {h_bucket}, toBucket: {h_bucket + 3600}, limit: 1) {{ bucket volumeUsd swapCount }} }}',
        lambda d: (
            assert_eq(1, len(d["candles"])),
            assert_close(3000.0, d["candles"][0]["volumeUsd"], tol=0.01),
            assert_eq(15, d["candles"][0]["swapCount"]),
        )
    )

    # 7. lpPositions
    await check("lpPositions — correct token, ticks, is_active",
        f'{{ lpPositions(marketId: "{MARKET_ID}", activeOnly: true) {{ tokenId tickLower tickUpper isActive isBurned entryPrice }} }}',
        lambda d: (
            assert_eq(1, len(d["lpPositions"])),
            assert_eq(9999, d["lpPositions"][0]["tokenId"]),
            assert_eq(-200, d["lpPositions"][0]["tickLower"]),
            assert_eq(200, d["lpPositions"][0]["tickUpper"]),
            assert_eq(True, d["lpPositions"][0]["isActive"]),
            assert_eq(False, d["lpPositions"][0]["isBurned"]),
            assert_close(1.04, d["lpPositions"][0]["entryPrice"], tol=0.001),
        )
    )

    # 8. twammOrders
    await check("twammOrders — correct orderId and amountIn",
        f'{{ twammOrders(marketId: "{MARKET_ID}", activeOnly: true) {{ orderId owner expiration zeroForOne amountIn isCancelled blockNumber }} }}',
        lambda d: (
            assert_eq(1, len(d["twammOrders"])),
            assert_eq("0xtwamm_order_id", d["twammOrders"][0]["orderId"]),
            assert_eq("500000000", d["twammOrders"][0]["amountIn"]),
            assert_eq(True, d["twammOrders"][0]["zeroForOne"]),
            assert_eq(False, d["twammOrders"][0]["isCancelled"]),
        )
    )

    # 9. indexerStatus
    await check("indexerStatus — events count and block",
        '{ indexerStatus { marketId lastIndexedBlock totalEvents } }',
        lambda d: (
            assert_in(MARKET_ID, [s["marketId"] for s in d["indexerStatus"]]),
            assert_eq(2000, next(s for s in d["indexerStatus"] if s["marketId"] == MARKET_ID)["lastIndexedBlock"]),
            assert_eq(77, next(s for s in d["indexerStatus"] if s["marketId"] == MARKET_ID)["totalEvents"]),
        )
    )

    print(f"\n{'═'*50}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'═'*50}")
    return failed


# ── Assertion helpers ─────────────────────────────────────────────────────

def assert_eq(expected, actual):
    assert expected == actual, f"expected {expected!r}, got {actual!r}"

def assert_in(item, collection):
    assert item in collection, f"{item!r} not in {collection!r}"

def assert_close(expected, actual, tol=0.01):
    assert abs(expected - actual) <= tol, f"expected ~{expected}, got {actual}"


# ── Main ──────────────────────────────────────────────────────────────────

async def main():
    os.environ["DATABASE_URL"] = DB

    # Apply schema and seed
    conn = await asyncpg.connect(DB)
    import pathlib
    schema_sql = pathlib.Path(__file__).parent / "schema.sql"
    await conn.execute(schema_sql.read_text())
    await seed(conn)
    await conn.close()
    print("Seed complete.")

    # Start uvicorn in background
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.graphql:app",
         "--host", "0.0.0.0", "--port", "8099", "--log-level", "error"],
        env={**os.environ, "PYTHONPATH": str(pathlib.Path(__file__).parent)},
        cwd=str(pathlib.Path(__file__).parent),
    )
    try:
        # Wait for server to be ready
        for _ in range(30):
            try:
                r = httpx.get(HEALTH, timeout=1)
                if r.status_code == 200:
                    print(f"Server ready: {r.json()}")
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("Server did not become healthy in 15s")

        async with httpx.AsyncClient() as client:
            failed = await run_all_checks(client)

        sys.exit(failed)
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    asyncio.run(main())
