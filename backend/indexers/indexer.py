"""
indexer.py — Main event indexing loop.

Architecture:
  - Single async process. One writer at a time. No workers.
  - Every block batch wrapped in one transaction.
  - Watch set rebuilt from DB every poll cycle (catches new markets + brokers).
  - eth_getLogs across all watched addresses — one RPC call per poll.
"""
import asyncio
import json
import logging
import os
import time
from typing import Any

import asyncpg
from web3 import Web3
from web3.types import LogReceipt

import db
import bootstrap
from handlers import market as market_handler
from handlers import broker as broker_handler
from handlers import pool as pool_handler
from handlers import twamm as twamm_handler
from handlers import lp as lp_handler

log = logging.getLogger(__name__)

# ── ABI signatures for event topic0 hashes ────────────────────────────────

TOPICS = {
    # RLDCore
    Web3.keccak(text="NormalizationFactorUpdated(bytes32,uint128,uint128)").hex():
        "NormalizationFactorUpdated",
    Web3.keccak(text="MarketStateUpdated(bytes32,uint128,uint128)").hex():
        "MarketStateUpdated",
    # MockOracle
    Web3.keccak(text="RateUpdated(bytes32,int256)").hex():
        "RateUpdated",
    # BrokerFactory
    Web3.keccak(text="BrokerCreated(address,address,bytes32)").hex():
        "BrokerCreated",
    # PrimeBroker
    Web3.keccak(text="CollateralDeposited(address,uint256)").hex():
        "CollateralDeposited",
    Web3.keccak(text="CollateralWithdrawn(address,uint256)").hex():
        "CollateralWithdrawn",
    Web3.keccak(text="PositionMinted(uint256)").hex():
        "PositionMinted",
    Web3.keccak(text="PositionBurned(uint256)").hex():
        "PositionBurned",
    Web3.keccak(text="ActiveTokenSet(uint256)").hex():
        "ActiveTokenSet",
    Web3.keccak(text="V4LiquidityAdded(uint256,int24,int24,uint128)").hex():
        "V4LiquidityAdded",
    Web3.keccak(text="V4LiquidityRemoved(uint256,uint128)").hex():
        "V4LiquidityRemoved",
    # V4 PoolManager
    Web3.keccak(text="Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)").hex():
        "Swap",
    Web3.keccak(text="ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)").hex():
        "ModifyLiquidity",
    # TWAMM Hook
    Web3.keccak(text="SubmitOrder(address,uint160,bool,uint256)").hex():
        "SubmitOrder",
    Web3.keccak(text="CancelOrder(address,uint160,bool)").hex():
        "CancelOrder",
}

POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "2"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))  # max blocks per getLogs call


# ── Watch set ──────────────────────────────────────────────────────────────

async def build_watch_set(conn: asyncpg.Connection, global_cfg: dict) -> set[str]:
    """Rebuild the address filter from DB. Called every poll cycle."""
    markets = await conn.fetch("SELECT * FROM markets")
    brokers = await conn.fetch("SELECT address FROM brokers")

    watched = {
        global_cfg["rld_core"].lower(),
        global_cfg["v4_pool_manager"].lower(),
        global_cfg["v4_position_manager"].lower(),
    }
    for m in markets:
        watched.add(m["broker_factory"].lower())
        watched.add(m["mock_oracle"].lower())
        watched.add(m["twamm_hook"].lower())
    for b in brokers:
        watched.add(b["address"].lower())

    return watched


# ── Market ID resolver — address → market_id ──────────────────────────────

async def build_address_market_map(conn: asyncpg.Connection) -> dict[str, str]:
    """Map contract address → market_id for routing logs."""
    rows = await conn.fetch("""
        SELECT market_id, broker_factory, mock_oracle, twamm_hook
        FROM markets
    """)
    mapping = {}
    for r in rows:
        mapping[r["broker_factory"].lower()] = r["market_id"]
        mapping[r["mock_oracle"].lower()] = r["market_id"]
        mapping[r["twamm_hook"].lower()] = r["market_id"]

    # Broker → market_id
    broker_rows = await conn.fetch("SELECT address, market_id FROM brokers")
    for b in broker_rows:
        mapping[b["address"].lower()] = b["market_id"]

    return mapping


# ── Log dispatch ───────────────────────────────────────────────────────────

async def dispatch(
    log_entry: dict,
    conn: asyncpg.Connection,
    global_cfg: dict,
    addr_market_map: dict[str, str],
    w3: Web3,
) -> None:
    """Route one decoded log entry to the appropriate handler."""
    topics = log_entry.get("topics", [])
    if not topics:
        return

    topic0 = topics[0].hex() if isinstance(topics[0], bytes) else topics[0]
    event_name = TOPICS.get(topic0)
    if not event_name:
        return  # Unknown event, skip

    contract = log_entry["address"].lower()
    block_number = log_entry["blockNumber"]
    block_timestamp = log_entry.get("_block_timestamp", 0)
    tx_hash = log_entry["transactionHash"].hex() \
        if isinstance(log_entry["transactionHash"], bytes) \
        else log_entry["transactionHash"]
    log_index = log_entry["logIndex"]

    market_id = addr_market_map.get(contract)
    data = log_entry.get("data", "0x")
    if isinstance(data, bytes):
        data = data.hex()

    # ── Record raw event ─────────────────────────────────────────────────
    await conn.execute("""
        INSERT INTO events
          (market_id, block_number, block_timestamp, tx_hash, log_index,
           event_name, contract_address, data)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (tx_hash, log_index) DO NOTHING
    """, market_id, block_number, block_timestamp, tx_hash, log_index,
         event_name, contract, json.dumps({"raw": data, "topics": [
             t.hex() if isinstance(t, bytes) else t for t in topics
         ]}))

    # ── Route to handler ─────────────────────────────────────────────────

    if event_name == "BrokerCreated":
        # topics: [topic0, broker, owner]  data: marketId (bytes32)
        broker_addr = "0x" + topics[1][-20:].hex() if isinstance(topics[1], bytes) \
            else "0x" + topics[1][-40:]
        owner_addr = "0x" + topics[2][-20:].hex() if isinstance(topics[2], bytes) \
            else "0x" + topics[2][-40:]

        # Decode marketId from data
        raw_data = data if data.startswith("0x") else "0x" + data
        market_id_bytes = raw_data[2:66] if len(raw_data) >= 66 else None
        resolved_market_id = market_id or (
            addr_market_map.get(contract) if market_id_bytes else None
        )
        await broker_handler.handle_broker_created(
            conn, resolved_market_id or market_id, broker_addr, owner_addr,
            block_number, tx_hash,
        )

    elif event_name == "NormalizationFactorUpdated" and market_id:
        # data: (newFactor uint128, timestamp uint128)
        decoded = w3.eth.codec.decode(["uint128", "uint128"], bytes.fromhex(data[2:]))
        await market_handler.handle_normalization_factor_updated(
            conn, market_id, block_number, block_timestamp, decoded[0]
        )

    elif event_name == "RateUpdated" and market_id:
        # data: (marketId bytes32, indexPrice int256)
        decoded = w3.eth.codec.decode(["bytes32", "int256"], bytes.fromhex(data[2:]))
        await market_handler.handle_rate_updated(
            conn, market_id, block_number, block_timestamp, decoded[1]
        )

    elif event_name == "Swap":
        # id is topics[1] — resolve market from pool_id
        if not market_id:
            # Try resolving via pool_id from topics
            pool_id_topic = topics[1].hex() if isinstance(topics[1], bytes) else topics[1]
            row = await conn.fetchrow(
                "SELECT market_id FROM markets WHERE pool_id = $1", pool_id_topic
            )
            if row:
                market_id = row["market_id"]
        if not market_id:
            return

        # Swap ABI: (bytes32 id, address sender, int128 amount0, int128 amount1,
        #            uint160 sqrtPriceX96, uint128 liquidity, int24 tick, uint24 fee)
        # Topics: [sig, id, sender]  Data: (amount0, amount1, sqrtPriceX96, liquidity, tick, fee)
        try:
            decoded = w3.eth.codec.decode(
                ["int128", "int128", "uint160", "uint128", "int24", "uint24"],
                bytes.fromhex(data[2:])
            )
            await pool_handler.handle_swap(
                conn, market_id, block_number, block_timestamp,
                sqrt_price_x96=decoded[2], tick=decoded[4],
                amount0=decoded[0], amount1=decoded[1], liquidity=decoded[3]
            )
        except Exception as e:
            log.warning("[dispatch] Swap decode failed block=%d: %s", block_number, e)

    elif event_name == "ModifyLiquidity" and market_id:
        try:
            decoded = w3.eth.codec.decode(
                ["int24", "int24", "int256", "bytes32"],
                bytes.fromhex(data[2:])
            )
            await pool_handler.handle_modify_liquidity(
                conn, market_id, block_number, block_timestamp,
                tick_lower=decoded[0], tick_upper=decoded[1],
                liquidity_delta=decoded[2], sqrt_price_x96=0
            )
        except Exception as e:
            log.warning("[dispatch] ModifyLiquidity decode failed block=%d: %s", block_number, e)

    elif event_name == "V4LiquidityAdded" and market_id:
        try:
            decoded = w3.eth.codec.decode(
                ["uint256", "int24", "int24", "uint128"],
                bytes.fromhex(data[2:])
            )
            await lp_handler.handle_v4_liquidity_added(
                conn, market_id, contract,
                token_id=decoded[0], tick_lower=decoded[1], tick_upper=decoded[2],
                liquidity=decoded[3], block_number=block_number
            )
        except Exception as e:
            log.warning("[dispatch] V4LiquidityAdded decode failed: %s", e)

    elif event_name == "V4LiquidityRemoved":
        try:
            decoded = w3.eth.codec.decode(["uint256", "uint128"], bytes.fromhex(data[2:]))
            await lp_handler.handle_v4_liquidity_removed(conn, decoded[0], decoded[1])
        except Exception as e:
            log.warning("[dispatch] V4LiquidityRemoved decode failed: %s", e)

    elif event_name == "ActiveTokenSet":
        try:
            decoded = w3.eth.codec.decode(["uint256"], bytes.fromhex(data[2:]))
            await broker_handler.handle_active_token_set(conn, contract, decoded[0])
        except Exception as e:
            log.warning("[dispatch] ActiveTokenSet decode failed: %s", e)

    elif event_name == "SubmitOrder" and market_id:
        # topics: [sig, owner(indexed)]  data: (expiration, zeroForOne, amountIn)  OR all in data
        try:
            owner = "0x" + (topics[1][-20:].hex() if isinstance(topics[1], bytes) else topics[1][-40:])
            decoded = w3.eth.codec.decode(["uint160", "bool", "uint256"], bytes.fromhex(data[2:]))
            await twamm_handler.handle_submit_order(
                conn, market_id, owner,
                expiration=decoded[0], start_epoch=block_timestamp,
                zero_for_one=decoded[1], amount_in=decoded[2],
                block_number=block_number, tx_hash=tx_hash
            )
        except Exception as e:
            log.warning("[dispatch] SubmitOrder decode failed: %s", e)

    elif event_name == "CancelOrder":
        try:
            owner = "0x" + (topics[1][-20:].hex() if isinstance(topics[1], bytes) else topics[1][-40:])
            decoded = w3.eth.codec.decode(["uint160", "bool"], bytes.fromhex(data[2:]))
            await twamm_handler.handle_cancel_order(conn, owner, decoded[0], decoded[1])
        except Exception as e:
            log.warning("[dispatch] CancelOrder decode failed: %s", e)


# ── Stats update ───────────────────────────────────────────────────────────

async def update_indexer_state(conn: asyncpg.Connection, market_id: str, block_number: int) -> None:
    await conn.execute("""
        UPDATE indexer_state
        SET last_indexed_block = $1,
            last_indexed_at = NOW(),
            total_events = (SELECT COUNT(*) FROM events WHERE market_id = $2)
        WHERE market_id = $2
    """, block_number, market_id)


# ── Main loop ─────────────────────────────────────────────────────────────

async def run(rpc_url: str, dsn: str) -> None:
    await db.init(dsn)
    global_cfg = await bootstrap.bootstrap(db.pool)

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    log.info("Connected to chain %d at %s", w3.eth.chain_id, rpc_url)

    last_block = global_cfg["session_start_block"] - 1

    while True:
        try:
            latest = w3.eth.block_number
            if latest <= last_block:
                await asyncio.sleep(POLL_INTERVAL)
                continue

            to_block = min(latest, last_block + BATCH_SIZE)

            async with db.pool.acquire() as conn:
                # Rebuild watch set and address→market map — zero RPC
                watched = await build_watch_set(conn, global_cfg)
                addr_market_map = await build_address_market_map(conn)

                if not watched:
                    log.debug("No addresses to watch yet, waiting for first market deployment")
                    last_block = to_block
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                # Fetch all logs in [last_block+1 .. to_block] for watched addresses
                logs = w3.eth.get_logs({
                    "fromBlock": last_block + 1,
                    "toBlock": to_block,
                    "address": list(watched),
                })

                if not logs:
                    last_block = to_block
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                # Enrich logs with block timestamps (batch blocks)
                block_timestamps: dict[int, int] = {}
                for log_entry in logs:
                    bn = log_entry["blockNumber"]
                    if bn not in block_timestamps:
                        block_timestamps[bn] = w3.eth.get_block(bn)["timestamp"]
                    log_entry["_block_timestamp"] = block_timestamps[bn]

                # ── Single transaction per batch ────────────────────────
                async with conn.transaction():
                    for log_entry in logs:
                        await dispatch(log_entry, conn, global_cfg, addr_market_map, w3)

                    # Update progress per market seen in this batch
                    markets_in_batch = {
                        addr_market_map.get(log_entry["address"].lower())
                        for log_entry in logs
                    } - {None}

                    for mid in markets_in_batch:
                        row = await conn.fetchrow(
                            "SELECT 1 FROM indexer_state WHERE market_id = $1", mid
                        )
                        if row:
                            await update_indexer_state(conn, mid, to_block)

            last_block = to_block
            log.info("Indexed blocks %d→%d (%d logs)", last_block - len(logs) + 1,
                     to_block, len(logs))

        except Exception as e:
            log.error("Poll cycle error (will retry): %s", e, exc_info=True)
            await asyncio.sleep(POLL_INTERVAL * 2)
            continue

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    rpc_url = os.getenv("RPC_URL", "http://localhost:8545")
    dsn = os.getenv("DATABASE_URL", "postgresql://rld:rld@localhost:5432/rld_indexer")
    asyncio.run(run(rpc_url, dsn))
