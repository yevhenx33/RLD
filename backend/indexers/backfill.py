"""
backfill.py — Fast standalone re-indexer.

Fetches ALL logs from deploy_block → head in large batches (2000 blocks),
topic-filtered only (no address filter), so we never miss events even if
the watch set doesn't know about a contract yet.

Usage:
    # From inside the container (or locally with correct DB/RPC env vars):
    python backfill.py [--batch-size 2000] [--reset]

    --reset: Truncate all tables first and re-seed market from deployment.json
"""
import asyncio
import argparse
import logging
import os
import sys
import time

from web3 import Web3

# Reuse existing modules
import db
import bootstrap
from handlers import snapshot as snapshot_handler

# Import TOPICS and dispatch from indexer
from indexer import TOPICS, dispatch, build_address_market_map

log = logging.getLogger("backfill")

# All topic0 hashes we care about
ALL_TOPICS = list(TOPICS.keys())


async def backfill(rpc_url: str, dsn: str, batch_size: int, do_reset: bool):
    """Fast backfill: fetch ALL logs topic-filtered in large batches."""

    # ── Connect ──────────────────────────────────────────────────────────
    await db.init(dsn)
    pool = db.pool

    async with pool.acquire() as conn:
        await bootstrap.apply_schema(conn)

    if do_reset:
        log.info("Resetting all data...")
        await bootstrap.reset(pool)

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        log.error("Cannot connect to RPC at %s", rpc_url)
        sys.exit(1)

    log.info("Connected to chain %d at %s", w3.eth.chain_id, rpc_url)

    # ── Load config ──────────────────────────────────────────────────────
    async with pool.acquire() as conn:
        market_row = await conn.fetchrow("SELECT market_id, deploy_block FROM markets LIMIT 1")

    if not market_row:
        log.error("No market in DB. Run deployer first or use --reset.")
        sys.exit(1)

    market_id = market_row["market_id"]
    deploy_block = market_row["deploy_block"] or 0

    global_cfg = {"market_id": market_id, "session_start_block": deploy_block}
    try:
        full_cfg = bootstrap.load_deployment_json()
        global_cfg.update(full_cfg)
    except (FileNotFoundError, ValueError) as e:
        log.warning("Could not load deployment.json: %s", e)

    head_block = w3.eth.block_number
    total_blocks = head_block - deploy_block
    log.info("Backfill: block %d → %d (%d blocks)", deploy_block, head_block, total_blocks)

    # ── Batch process ────────────────────────────────────────────────────
    start_time = time.time()
    from_block = deploy_block
    total_logs = 0
    total_events_dispatched = 0
    skipped_events = 0
    batch_count = 0

    while from_block <= head_block:
        to_block = min(head_block, from_block + batch_size - 1)
        batch_count += 1

        try:
            # Fetch ALL logs matching our event topics — NO address filter
            # This catches events from contracts we don't know about yet
            raw_logs = w3.eth.get_logs({
                "fromBlock": from_block,
                "toBlock": to_block,
                "topics": [ALL_TOPICS],
            })

            total_logs += len(raw_logs)

            if raw_logs:
                # Enrich with timestamps
                block_timestamps: dict[int, int] = {}
                enriched = []
                for entry in raw_logs:
                    bn = entry["blockNumber"]
                    if bn not in block_timestamps:
                        block_timestamps[bn] = w3.eth.get_block(bn)["timestamp"]
                    d = dict(entry)
                    d["_block_timestamp"] = block_timestamps[bn]
                    enriched.append(d)

                # Process — each dispatch in its own savepoint
                async with pool.acquire() as conn:
                    # Rebuild address map each batch (picks up newly created brokers)
                    addr_market_map = await build_address_market_map(conn)

                    for entry in enriched:
                        try:
                            async with conn.transaction():
                                await dispatch(entry, conn, global_cfg, addr_market_map, w3)
                                total_events_dispatched += 1
                        except Exception as e:
                            # Non-fatal: skip this event and continue
                            topic0 = entry.get("topics", [b""])[0]
                            t0hex = topic0.hex() if isinstance(topic0, bytes) else topic0
                            ename = TOPICS.get(t0hex, "unknown")
                            skipped_events += 1
                            if skipped_events <= 20:
                                log.warning(
                                    "Skipped %s at block %d: %s",
                                    ename, entry["blockNumber"], e
                                )

            # Update indexer state (outside event processing)
            async with pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO indexer_state (market_id, last_indexed_block, last_indexed_at, total_events)
                    VALUES ($1, $2, NOW(), $3)
                    ON CONFLICT (market_id) DO UPDATE SET
                      last_indexed_block = EXCLUDED.last_indexed_block,
                      last_indexed_at = EXCLUDED.last_indexed_at,
                      total_events = EXCLUDED.total_events
                """, market_id, to_block, total_events_dispatched)

            # Progress bar
            progress = (to_block - deploy_block) / total_blocks * 100
            elapsed = time.time() - start_time
            bps = (to_block - deploy_block) / elapsed if elapsed > 0 else 0
            remaining = (head_block - to_block) / bps if bps > 0 else 0

            if batch_count % 10 == 0 or len(raw_logs) > 0:
                log.info(
                    "  [%5.1f%%] blocks %d→%d | %d logs this batch | "
                    "total: %d events | %.0f blk/s | ETA %.0fs",
                    progress, from_block, to_block, len(raw_logs),
                    total_events_dispatched, bps, remaining
                )

        except Exception as e:
            log.error("Batch error at blocks %d→%d: %s", from_block, to_block, e,
                      exc_info=True)
            # On Anvil RPC errors, retry with smaller batch
            if batch_size > 200:
                batch_size = batch_size // 2
                log.info("Reducing batch size to %d", batch_size)
                continue
            else:
                raise

        from_block = to_block + 1

    # ── Final snapshot materialization ────────────────────────────────────
    async with pool.acquire() as conn:
        await snapshot_handler.materialize_snapshot(conn, market_id)

    elapsed = time.time() - start_time
    log.info("═" * 60)
    log.info("Backfill complete!")
    log.info("  Blocks: %d → %d (%d total)", deploy_block, head_block, total_blocks)
    log.info("  Events dispatched: %d", total_events_dispatched)
    log.info("  Total logs matched: %d", total_logs)
    log.info("  Time: %.1fs (%.0f blocks/s)", elapsed, total_blocks / elapsed if elapsed > 0 else 0)
    log.info("═" * 60)

    # ── Quick data summary ───────────────────────────────────────────────
    async with pool.acquire() as conn:
        n_brokers = await conn.fetchval("SELECT COUNT(*) FROM brokers")
        n_lps = await conn.fetchval("SELECT COUNT(*) FROM lp_positions")
        n_orders = await conn.fetchval("SELECT COUNT(*) FROM twamm_orders")
        n_ops = await conn.fetchval("SELECT COUNT(*) FROM broker_operators")
        n_events = await conn.fetchval("SELECT COUNT(*) FROM events")

        log.info("DB Summary:")
        log.info("  Brokers:          %d", n_brokers)
        log.info("  LP Positions:     %d", n_lps)
        log.info("  TWAMM Orders:     %d", n_orders)
        log.info("  Broker Operators: %d", n_ops)
        log.info("  Total Events:     %d", n_events)

        # Sample a broker
        sample = await conn.fetchrow(
            "SELECT address, owner, wausdc_balance, wrlp_balance, debt_principal, is_frozen "
            "FROM brokers LIMIT 1"
        )
        if sample:
            log.info("  Sample Broker: %s (owner=%s)", sample["address"][:18], sample["owner"][:18])
            log.info("    waUSDC=%s wRLP=%s debt=%s frozen=%s",
                     sample["wausdc_balance"], sample["wrlp_balance"],
                     sample["debt_principal"], sample["is_frozen"])

    await pool.close()


def main():
    parser = argparse.ArgumentParser(description="Fast backfill indexer — parse all blocks from deployment")
    parser.add_argument("--rpc-url", default=os.getenv("RPC_URL", "http://localhost:8545"))
    parser.add_argument("--db-dsn", default=os.getenv("DATABASE_URL", "postgresql://rld:rld@localhost:5432/rld_indexer"))
    parser.add_argument("--batch-size", type=int, default=2000, help="Blocks per getLogs call")
    parser.add_argument("--reset", action="store_true", help="Truncate all data and reseed market first")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # Quiet down noisy loggers
    logging.getLogger("web3").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    asyncio.run(backfill(args.rpc_url, args.db_dsn, args.batch_size, args.reset))


if __name__ == "__main__":
    main()
