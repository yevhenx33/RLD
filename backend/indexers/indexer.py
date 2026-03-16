"""
indexer.py — Main event indexing loop.

Architecture:
  - Single async process. One writer at a time. No workers.
  - Every block batch wrapped in one transaction.
  - Two-pass hybrid log filter per poll:
    Pass 1 (topic-only): Custom events from our contracts — no address filter.
    Pass 2 (address-filtered): ERC20 Transfer events — address filter on watched tokens.
  - Logs are merged, deduplicated, and sorted before dispatch.
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
from handlers import bond as bond_handler
from handlers import snapshot as snapshot_handler

log = logging.getLogger(__name__)

# ── ABI signatures for event topic0 hashes ────────────────────────────────
#
# Split into two groups for the two-pass hybrid filter:
#   CUSTOM_TOPICS — events emitted only by our deployed contracts.
#     → Fetched with topic-only filter (no address restriction).
#     → Guarantees we never miss events from new/unknown broker addresses.
#
#   EXTERNAL_TOPICS — events with generic signatures (e.g. ERC20 Transfer)
#     that fire on many unrelated contracts.
#     → Fetched with address filter (only from watched token contracts).
#
# The combined TOPICS dict is the union, used by dispatch().

CUSTOM_TOPICS = {
    # RLDCore
    Web3.keccak(text="FundingApplied(bytes32,uint256,uint256,int256,uint256)").hex():
        "FundingApplied",
    Web3.keccak(text="MarketStateUpdated(bytes32,uint128,uint128)").hex():
        "MarketStateUpdated",
    Web3.keccak(text="PositionModified(bytes32,address,int256,int256)").hex():
        "PositionModified",
    Web3.keccak(text="BadDebtRegistered(bytes32,uint128,uint128)").hex():
        "BadDebtRegistered",
    # MockOracle
    Web3.keccak(text="RateUpdated(uint256,uint256)").hex():
        "RateUpdated",
    # BrokerFactory
    Web3.keccak(text="BrokerCreated(address,address,uint256)").hex():
        "BrokerCreated",
    # PrimeBroker — LP position events
    Web3.keccak(text="LiquidityAdded(uint256,uint128)").hex():
        "LiquidityAdded",
    Web3.keccak(text="LiquidityRemoved(uint256,uint128,bool)").hex():
        "LiquidityRemoved",
    Web3.keccak(text="ActivePositionChanged(uint256,uint256)").hex():
        "ActivePositionChanged",
    # PrimeBroker — TWAMM order events
    Web3.keccak(text="TwammOrderSubmitted(bytes32,bool,uint256,uint256)").hex():
        "TwammOrderSubmitted",
    Web3.keccak(text="TwammOrderCancelled(bytes32,uint256,uint256)").hex():
        "TwammOrderCancelled",
    Web3.keccak(text="TwammOrderClaimed(bytes32,uint256,uint256)").hex():
        "TwammOrderClaimed",
    Web3.keccak(text="ActiveTwammOrderChanged(bytes32,bytes32)").hex():
        "ActiveTwammOrderChanged",
    # PrimeBroker — lifecycle events
    Web3.keccak(text="BrokerFrozen(address)").hex():
        "BrokerFrozen",
    Web3.keccak(text="BrokerUnfrozen(address)").hex():
        "BrokerUnfrozen",
    Web3.keccak(text="OperatorUpdated(address,bool)").hex():
        "OperatorUpdated",
    # V4 PoolManager
    Web3.keccak(text="Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)").hex():
        "Swap",
    Web3.keccak(text="ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)").hex():
        "ModifyLiquidity",
    # TWAMM Hook (JTM)
    Web3.keccak(text="SubmitOrder(bytes32,bytes32,address,uint256,uint160,bool,uint256,uint256,uint256)").hex():
        "SubmitOrder",
    Web3.keccak(text="CancelOrder(bytes32,bytes32,address,uint256)").hex():
        "CancelOrder",
    # BondFactory
    Web3.keccak(text="BondMinted(address,address,uint256,uint256,uint256)").hex():
        "BondMinted",
    Web3.keccak(text="BondClosed(address,address,uint256,uint256)").hex():
        "BondClosed",
    # BasisTradeFactory
    Web3.keccak(text="BasisTradeOpened(address,address,uint256,uint256,uint256)").hex():
        "BasisTradeOpened",
    Web3.keccak(text="BasisTradeClosed(address,address,uint256)").hex():
        "BasisTradeClosed",
    # BrokerRouter — trade execution events
    Web3.keccak(text="LongExecuted(address,uint256,uint256)").hex():
        "LongExecuted",
    Web3.keccak(text="LongClosed(address,uint256,uint256)").hex():
        "LongClosed",
    Web3.keccak(text="ShortExecuted(address,uint256,uint256)").hex():
        "ShortExecuted",
    Web3.keccak(text="ShortClosed(address,uint256,uint256)").hex():
        "ShortClosed",
    Web3.keccak(text="Deposited(address,uint256,uint256)").hex():
        "Deposited",
}

EXTERNAL_TOPICS = {
    # ERC20 Transfer — generic signature, needs address filter
    Web3.keccak(text="Transfer(address,address,uint256)").hex():
        "ERC20Transfer",
}

# Combined lookup for dispatch()
TOPICS = {**CUSTOM_TOPICS, **EXTERNAL_TOPICS}

POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "2"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))  # max blocks per getLogs call


# ── Watch set (Pass 2 — address filter for external events) ───────────────

async def build_external_watch_set(conn: asyncpg.Connection, global_cfg: dict) -> set[str]:
    """Build the address filter for Pass 2 (ERC20 Transfer events).
    Only includes token contracts and the V4 PositionManager (ERC721)."""
    markets = await conn.fetch("SELECT wausdc, wrlp FROM markets")

    watched = set()
    # V4 PositionManager — ERC721 Transfer for LP NFTs
    posm = global_cfg.get("v4_position_manager")
    if posm:
        watched.add(posm.lower())
    # Token contracts — waUSDC and wRLP
    for m in markets:
        for col in ("wausdc", "wrlp"):
            if m.get(col):
                watched.add(m[col].lower())

    return watched


# ── Market ID resolver — address → market_id ──────────────────────────────

async def build_address_market_map(conn: asyncpg.Connection) -> dict[str, str]:
    """Map contract address → market_id for routing logs."""
    rows = await conn.fetch("""
        SELECT market_id, broker_factory, mock_oracle, twamm_hook, bond_factory, basis_trade_factory
        FROM markets
    """)
    mapping = {}
    for r in rows:
        mapping[r["broker_factory"].lower()] = r["market_id"]
        mapping[r["mock_oracle"].lower()] = r["market_id"]
        mapping[r["twamm_hook"].lower()] = r["market_id"]
        if r.get("bond_factory"):
            mapping[r["bond_factory"].lower()] = r["market_id"]
        if r.get("basis_trade_factory"):
            mapping[r["basis_trade_factory"].lower()] = r["market_id"]

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
    raw_data = log_entry.get("data", "0x")
    
    # Normalize data to bytes
    if isinstance(raw_data, str):
        hex_str = raw_data[2:] if raw_data.startswith("0x") else raw_data
        data_bytes = bytes.fromhex(hex_str)
    else:
        data_bytes = bytes(raw_data)

    log.info("[dispatch] topic0=%s name=%s dataLen=%d", topic0, event_name, len(data_bytes))

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
    # If not in map (singleton contracts), resolve via topics[1] if it looks like a market_id
    if not market_id and len(topics) > 1:
        tid = topics[1].hex() if isinstance(topics[1], bytes) else topics[1]
        market_id = "0x" + tid if not tid.startswith("0x") else tid

    # ── Record raw event ─────────────────────────────────────────────────
    await conn.execute("""
        INSERT INTO events
          (market_id, block_number, block_timestamp, tx_hash, log_index,
           event_name, contract_address, data)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (tx_hash, log_index) DO NOTHING
    """, market_id, block_number, block_timestamp, tx_hash, log_index,
       event_name, contract, json.dumps({
           "raw": "0x" + data_bytes.hex(),
           "topics": [t.hex() if isinstance(t, bytes) else t for t in topics]
       }))

    # ── Route to handler ─────────────────────────────────────────────────

    if event_name == "BrokerCreated":
        # topics: [topic0, broker, owner]  data: marketId (bytes32)
        broker_addr = "0x" + topics[1][-20:].hex() if isinstance(topics[1], bytes) \
            else "0x" + topics[1][-40:]
        owner_addr = "0x" + topics[2][-20:].hex() if isinstance(topics[2], bytes) \
            else "0x" + topics[2][-40:]

        # Decode marketId from data
        market_id_hex = data_bytes.hex()
        market_id_bytes = "0x" + market_id_hex if len(market_id_hex) == 64 else None
        resolved_market_id = market_id or (
            addr_market_map.get(contract) if market_id_bytes else None
        )
        await broker_handler.handle_broker_created(
            conn, resolved_market_id or market_id, broker_addr, owner_addr,
            block_number, tx_hash,
        )

    elif event_name == "FundingApplied" and market_id:
        # data: (oldNormFactor uint256, newNormFactor uint256, fundingRate int256, timeDelta uint256)
        # marketId is indexed (topics[1])
        decoded = w3.eth.codec.decode(["uint256", "uint256", "int256", "uint256"], data_bytes)
        await market_handler.handle_funding_applied(
            conn, market_id, block_number, block_timestamp, decoded[1], decoded[2]
        )

    elif event_name == "MarketStateUpdated" and market_id:
        # data: (normalizationFactor uint128, totalDebt uint128)
        decoded = w3.eth.codec.decode(["uint128", "uint128"], data_bytes)
        await market_handler.handle_market_state_updated(
            conn, market_id, block_number, block_timestamp, decoded[0], decoded[1]
        )

    elif event_name == "PositionModified" and market_id:
        # topics: [topic0, marketId, user]  data: (deltaCollateral int256, deltaDebt int256)
        user = "0x" + topics[2][-20:].hex() if isinstance(topics[2], bytes) else "0x" + topics[2][-40:]
        decoded = w3.eth.codec.decode(["int256", "int256"], data_bytes)
        await broker_handler.handle_position_modified(
            conn, market_id, user, decoded[0], decoded[1], block_number
        )

    elif event_name == "BadDebtRegistered" and market_id:
        # data: (amount uint128, totalBadDebt uint128)
        decoded = w3.eth.codec.decode(["uint128", "uint128"], data_bytes)
        await market_handler.handle_bad_debt_registered(
            conn, market_id, decoded[0], decoded[1]
        )

    elif event_name == "RateUpdated" and market_id:
        # data: (newRateRay uint256, timestamp uint256)
        decoded = w3.eth.codec.decode(["uint256", "uint256"], data_bytes)
        # newRateRay → compute index price: (rate * K_SCALAR) / 1e9 = WAD price
        new_rate_ray = decoded[0]
        index_price_wad = (new_rate_ray * 100) // 10**9  # K=100, RAY→WAD
        await market_handler.handle_rate_updated(
            conn, market_id, block_number, block_timestamp, index_price_wad
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
            pool_id = market_id  # In Swap, topic1 is the pool_id
            market_info = await conn.fetchrow(
                "SELECT market_id, wausdc, wrlp FROM markets WHERE pool_id = $1", pool_id
            )
            if not market_info:
                return
                
            real_market_id = market_info["market_id"]
            wausdc = market_info["wausdc"]
            wrlp = market_info["wrlp"]

            decoded = w3.eth.codec.decode(
                ["int128", "int128", "uint160", "uint128", "int24", "uint24"],
                data_bytes
            )
            await pool_handler.handle_swap(
                conn, real_market_id, block_number, block_timestamp,
                sqrt_price_x96=decoded[2], tick=decoded[4],
                amount0=decoded[0], amount1=decoded[1], liquidity=decoded[3],
                wausdc=wausdc, wrlp=wrlp
            )
        except Exception as e:
            log.warning("[dispatch] Swap decode failed block=%d: %s", block_number, e)

    elif event_name == "ModifyLiquidity" and market_id:
        try:
            pool_id = market_id  # topics[1] = V4 pool_id

            # Always decode tick data and enrich LP position first
            decoded = w3.eth.codec.decode(
                ["int24", "int24", "int256", "bytes32"],
                data_bytes
            )
            tick_lower, tick_upper, liquidity_delta, salt = decoded

            # Enrich LP position tick range from salt = bytes32(tokenId)
            token_id = int.from_bytes(salt, 'big')
            if token_id > 0:
                await lp_handler.enrich_tick_range(
                    conn, token_id, tick_lower, tick_upper, pool_id=pool_id
                )
                log.info("[dispatch] ModifyLiquidity enriched tokenId=%d ticks=[%d,%d] pool=%s",
                         token_id, tick_lower, tick_upper, pool_id[:18])

            # Optionally update pool snapshot if we can resolve the market
            market_info = await conn.fetchrow(
                "SELECT market_id, wausdc, wrlp FROM markets WHERE pool_id = $1", pool_id
            )
            if market_info:
                await pool_handler.handle_modify_liquidity(
                    conn, market_info["market_id"], block_number, block_timestamp,
                    tick_lower=tick_lower, tick_upper=tick_upper,
                    liquidity_delta=liquidity_delta, sqrt_price_x96=0,
                    wausdc=market_info["wausdc"], wrlp=market_info["wrlp"],
                    w3=w3, pool_manager=global_cfg.get("v4_pool_manager", "")
                )
        except Exception as e:
            log.warning("[dispatch] ModifyLiquidity decode failed block=%d: %s", block_number, e)

    elif event_name == "LiquidityAdded":
        # LiquidityAdded(uint256 indexed tokenId, uint128 liquidity)
        # Emitted at broker address. tokenId is indexed (topics[1]).
        try:
            token_id_raw = topics[1] if isinstance(topics[1], bytes) else bytes.fromhex(topics[1].replace('0x', ''))
            token_id = int.from_bytes(token_id_raw, 'big')
            decoded = w3.eth.codec.decode(["uint128"], data_bytes)
            await lp_handler.handle_liquidity_added(
                conn, contract, token_id, decoded[0], block_number
            )
        except Exception as e:
            log.warning("[dispatch] LiquidityAdded decode failed: %s", e)

    elif event_name == "LiquidityRemoved":
        # LiquidityRemoved(uint256 indexed tokenId, uint128 liquidity, bool burned)
        try:
            token_id_raw = topics[1] if isinstance(topics[1], bytes) else bytes.fromhex(topics[1].replace('0x', ''))
            token_id = int.from_bytes(token_id_raw, 'big')
            decoded = w3.eth.codec.decode(["uint128", "bool"], data_bytes)
            await lp_handler.handle_liquidity_removed(conn, token_id, decoded[0], decoded[1])
        except Exception as e:
            log.warning("[dispatch] LiquidityRemoved decode failed: %s", e)

    elif event_name == "ActivePositionChanged":
        # ActivePositionChanged(uint256 oldTokenId, uint256 newTokenId) — non-indexed
        try:
            decoded = w3.eth.codec.decode(["uint256", "uint256"], data_bytes)
            await broker_handler.handle_active_position_changed(
                conn, contract, decoded[0], decoded[1]
            )
        except Exception as e:
            log.warning("[dispatch] ActivePositionChanged decode failed: %s", e)

    elif event_name == "TwammOrderSubmitted":
        # TwammOrderSubmitted(bytes32 indexed orderId, bool zeroForOne, uint256 amountIn, uint256 expiration)
        try:
            order_id_bytes = topics[1] if isinstance(topics[1], bytes) else bytes.fromhex(topics[1].replace('0x', ''))
            order_id = "0x" + order_id_bytes.hex()
            decoded = w3.eth.codec.decode(["bool", "uint256", "uint256"], data_bytes)
            await twamm_handler.handle_twamm_order_submitted(
                conn, contract, order_id,
                zero_for_one=decoded[0], amount_in=decoded[1],
                expiration=decoded[2], block_number=block_number, tx_hash=tx_hash
            )
        except Exception as e:
            log.warning("[dispatch] TwammOrderSubmitted decode failed: %s", e)

    elif event_name == "TwammOrderCancelled":
        # TwammOrderCancelled(bytes32 indexed orderId, uint256 buyTokensOut, uint256 sellTokensRefund)
        try:
            order_id_bytes = topics[1] if isinstance(topics[1], bytes) else bytes.fromhex(topics[1].replace('0x', ''))
            order_id = "0x" + order_id_bytes.hex()
            decoded = w3.eth.codec.decode(["uint256", "uint256"], data_bytes)
            await twamm_handler.handle_twamm_order_cancelled(
                conn, order_id, decoded[0], decoded[1]
            )
        except Exception as e:
            log.warning("[dispatch] TwammOrderCancelled decode failed: %s", e)

    elif event_name == "TwammOrderClaimed":
        # TwammOrderClaimed(bytes32 indexed orderId, uint256 claimed0, uint256 claimed1)
        try:
            order_id_bytes = topics[1] if isinstance(topics[1], bytes) else bytes.fromhex(topics[1].replace('0x', ''))
            order_id = "0x" + order_id_bytes.hex()
            decoded = w3.eth.codec.decode(["uint256", "uint256"], data_bytes)
            await twamm_handler.handle_twamm_order_claimed(
                conn, order_id, decoded[0], decoded[1]
            )
        except Exception as e:
            log.warning("[dispatch] TwammOrderClaimed decode failed: %s", e)

    elif event_name == "ActiveTwammOrderChanged":
        # ActiveTwammOrderChanged(bytes32 oldOrderId, bytes32 newOrderId) — non-indexed
        try:
            decoded = w3.eth.codec.decode(["bytes32", "bytes32"], data_bytes)
            old_id = "0x" + decoded[0].hex()
            new_id = "0x" + decoded[1].hex()
            await broker_handler.handle_active_twamm_order_changed(
                conn, contract, old_id, new_id
            )
        except Exception as e:
            log.warning("[dispatch] ActiveTwammOrderChanged decode failed: %s", e)

    elif event_name == "BrokerFrozen":
        await broker_handler.handle_broker_frozen(conn, contract)

    elif event_name == "BrokerUnfrozen":
        await broker_handler.handle_broker_unfrozen(conn, contract)

    elif event_name == "OperatorUpdated":
        # OperatorUpdated(address indexed operator, bool active)
        try:
            operator_raw = topics[1] if isinstance(topics[1], bytes) else bytes.fromhex(topics[1].replace('0x', ''))
            operator = "0x" + operator_raw[-20:].hex()
            decoded = w3.eth.codec.decode(["bool"], data_bytes)
            await broker_handler.handle_operator_updated(
                conn, contract, operator, decoded[0]
            )
        except Exception as e:
            log.warning("[dispatch] OperatorUpdated decode failed: %s", e)

    elif event_name == "SubmitOrder" and market_id:
        # JTM Hook: SubmitOrder(bytes32 indexed poolId, bytes32 indexed orderId, address owner,
        #             uint256 amountIn, uint160 expiration, bool zeroForOne,
        #             uint256 sellRate, uint256 earningsFactorLast, uint256 startEpoch)
        try:
            pool_id_bytes = topics[1] if isinstance(topics[1], bytes) else bytes.fromhex(topics[1].replace('0x', ''))
            order_id_bytes = topics[2] if isinstance(topics[2], bytes) else bytes.fromhex(topics[2].replace('0x', ''))
            pool_id = "0x" + pool_id_bytes.hex()
            order_id = "0x" + order_id_bytes.hex()
            decoded = w3.eth.codec.decode(
                ["address", "uint256", "uint160", "bool", "uint256", "uint256", "uint256"],
                data_bytes
            )
            await twamm_handler.handle_submit_order(
                conn, pool_id=pool_id, order_id=order_id, owner=decoded[0],
                amount_in=decoded[1], expiration=decoded[2],
                zero_for_one=decoded[3], sell_rate=decoded[4],
                start_epoch=decoded[6],
                block_number=block_number, tx_hash=tx_hash
            )
        except Exception as e:
            log.warning("[dispatch] SubmitOrder decode failed: %s", e)

    elif event_name == "CancelOrder":
        # JTM Hook: CancelOrder(bytes32 indexed poolId, bytes32 indexed orderId, address owner, uint256 sellTokensRefund)
        try:
            order_id_bytes = topics[2] if isinstance(topics[2], bytes) else bytes.fromhex(topics[2].replace('0x', ''))
            order_id = "0x" + order_id_bytes.hex()
            decoded = w3.eth.codec.decode(["address", "uint256"], data_bytes)
            await twamm_handler.handle_cancel_order_hook(
                conn, order_id, decoded[1]
            )
        except Exception as e:
            log.warning("[dispatch] CancelOrder decode failed: %s", e)

    elif event_name == "ERC20Transfer":
        # Transfer(address indexed from, address indexed to, uint256 amount)
        # Emitted by waUSDC and wRLP contracts.
        # Update wausdc_balance or wrlp_balance as raw uint256 strings.
        try:
            from_addr = ("0x" + topics[1][-20:].hex()) if isinstance(topics[1], bytes) else ("0x" + topics[1][-40:])
            to_addr   = ("0x" + topics[2][-20:].hex()) if isinstance(topics[2], bytes) else ("0x" + topics[2][-40:])
            decoded   = w3.eth.codec.decode(["uint256"], data_bytes)
            amount    = decoded[0]
            from_addr = from_addr.lower()
            to_addr   = to_addr.lower()
            contract_lower = contract.lower()

            # Determine which column to update based on which token emitted the event
            markets = await conn.fetch("SELECT market_id, wausdc, wrlp FROM markets LIMIT 1")
            if not markets:
                return
            m = markets[0]
            mkt_id = m["market_id"]
            wausdc = (m["wausdc"] or "").lower()
            wrlp   = (m["wrlp"]   or "").lower()

            # Also check if this is an ERC721 Transfer on V4 PositionManager
            v4_posm = (global_cfg.get("v4_position_manager") or "").lower()
            if contract_lower == v4_posm:
                # ERC721 Transfer — route to LP handler
                token_id = amount  # For ERC721, the uint256 IS the tokenId
                await lp_handler.handle_lp_nft_transfer(
                    conn, from_addr, to_addr, token_id, block_number
                )
                return

            # Broker-level column
            col = "wausdc_balance" if contract_lower == wausdc else (
                  "wrlp_balance"   if contract_lower == wrlp   else None)
            if col is None:
                return  # Not a token we care about

            # Market-level counter column
            mkt_col = "total_broker_wausdc" if contract_lower == wausdc else "total_broker_wrlp"

            # Raw uint256 arithmetic: read current, add/subtract, write back
            # Update sender balance (subtract)
            from_row = await conn.fetchrow(
                f"SELECT {col} FROM brokers WHERE address = $1", from_addr
            )
            if from_row:
                current = int(from_row[col] or "0")
                new_val = max(0, current - amount)
                await conn.execute(
                    f"UPDATE brokers SET {col} = $1 WHERE address = $2",
                    str(new_val), from_addr
                )

            # Update recipient balance (add)
            to_row = await conn.fetchrow(
                f"SELECT {col} FROM brokers WHERE address = $1", to_addr
            )
            if to_row:
                current = int(to_row[col] or "0")
                new_val = current + amount
                await conn.execute(
                    f"UPDATE brokers SET {col} = $1 WHERE address = $2",
                    str(new_val), to_addr
                )

            # Update market-level running counter
            divisor = 1e6  # for human-readable counter
            human_amount = amount / divisor
            delta = 0.0
            if to_row:
                delta += human_amount
            if from_row:
                delta -= human_amount
            if delta != 0:
                await conn.execute(
                    f"UPDATE markets SET {mkt_col} = COALESCE({mkt_col}, 0) + $1 WHERE market_id = $2",
                    delta, mkt_id
                )

            log.debug("[ERC20Transfer] %s from=%s to=%s amount=%d col=%s",
                      contract[:10], from_addr[:10], to_addr[:10], amount, col)
        except Exception as e:
            log.warning("[dispatch] ERC20Transfer decode failed: %s", e)

    elif event_name in ("BondMinted", "BasisTradeOpened") and market_id:
        # topics: [sig, user, broker]  data: (notional uint256, hedge uint256, duration uint256)
        try:
            user   = "0x" + (topics[1][-20:].hex() if isinstance(topics[1], bytes) else topics[1][-40:])
            broker = "0x" + (topics[2][-20:].hex() if isinstance(topics[2], bytes) else topics[2][-40:])
            decoded = w3.eth.codec.decode(["uint256", "uint256", "uint256"], data_bytes)
            await bond_handler.handle_bond_minted(
                conn, market_id, user, broker,
                notional=decoded[0], hedge=decoded[1], duration=decoded[2],
                block_number=block_number, tx_hash=tx_hash,
                factory_address=contract,
            )
        except Exception as e:
            log.warning("[dispatch] %s decode failed block=%d: %s", event_name, block_number, e)

    elif event_name in ("BondClosed", "BasisTradeClosed"):
        # topics: [sig, user, broker]  data: ...
        try:
            broker = "0x" + (topics[2][-20:].hex() if isinstance(topics[2], bytes) else topics[2][-40:])
            await bond_handler.handle_bond_closed(
                conn, broker, block_number, tx_hash,
            )
        except Exception as e:
            log.warning("[dispatch] BondClosed decode failed block=%d: %s", block_number, e)


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
    # Schema-only — no market config needed yet
    await bootstrap.bootstrap(db.pool)

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    log.info("Connected to chain %d at %s", w3.eth.chain_id, rpc_url)

    # Wait for deployer to seed market via POST /admin/reset
    log.info("Waiting for market config in DB (deployer must call POST /admin/reset)...")
    global_cfg = {}
    while True:
        async with db.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT market_id, deploy_block FROM markets LIMIT 1")
            state = await conn.fetchrow("SELECT last_indexed_block FROM indexer_state LIMIT 1")
        if row:
            global_cfg["market_id"] = row["market_id"]
            global_cfg["session_start_block"] = row["deploy_block"] or 0
            # Load full config from deployment.json for rld_core, v4_pool_manager etc.
            try:
                full_cfg = bootstrap.load_deployment_json()
                global_cfg.update(full_cfg)
            except (FileNotFoundError, ValueError):
                pass
            log.info("Market found: %s (deploy_block=%d)", row["market_id"], global_cfg["session_start_block"])
            break
        await asyncio.sleep(5)

    last_block = global_cfg["session_start_block"] - 1
    if state and state["last_indexed_block"] is not None:
        last_block = max(last_block, state["last_indexed_block"])
        log.info("Resuming from block %d (from DB)", last_block)
    else:
        log.info("Starting from block %d (no state in DB)", last_block)

    while True:
        try:
            latest = w3.eth.block_number
            if latest <= last_block:
                await asyncio.sleep(POLL_INTERVAL)
                continue

            to_block = min(latest, last_block + BATCH_SIZE)

            async with db.pool.acquire() as conn:
                # Rebuild address→market map — zero RPC
                addr_market_map = await build_address_market_map(conn)

                from_block = last_block + 1

                # ── Pass 1: Topic-only — custom contract events ──────
                custom_topic_list = [bytes.fromhex(t) for t in CUSTOM_TOPICS.keys()]
                logs_pass1 = w3.eth.get_logs({
                    "fromBlock": from_block,
                    "toBlock": to_block,
                    "topics": [custom_topic_list],
                })

                # ── Pass 2: Address-filtered — ERC20 Transfer events ─
                ext_watched = await build_external_watch_set(conn, global_cfg)
                logs_pass2 = []
                if ext_watched:
                    valid = [a for a in ext_watched if isinstance(a, str) and a.startswith("0x") and len(a) == 42]
                    watched_cs = [Web3.to_checksum_address(a) for a in valid]
                    ext_topic_list = [bytes.fromhex(t) for t in EXTERNAL_TOPICS.keys()]
                    logs_pass2 = w3.eth.get_logs({
                        "fromBlock": from_block,
                        "toBlock": to_block,
                        "address": watched_cs,
                        "topics": [ext_topic_list],
                    })

                # ── Merge & deduplicate ──────────────────────────────
                seen = set()
                merged = []
                for entry in list(logs_pass1) + list(logs_pass2):
                    key = (entry["transactionHash"], entry["logIndex"])
                    if key not in seen:
                        seen.add(key)
                        merged.append(entry)
                # Sort by (blockNumber, logIndex) for correct event ordering
                merged.sort(key=lambda e: (e["blockNumber"], e["logIndex"]))

                logs = merged
                if logs:
                    # Enrich logs with block timestamps (batch blocks)
                    enriched_logs = []
                    block_timestamps: dict[int, int] = {}
                    for log_entry in logs:
                        bn = log_entry["blockNumber"]
                        if bn not in block_timestamps:
                            block_timestamps[bn] = w3.eth.get_block(bn)["timestamp"]
                        
                        entry_dict = dict(log_entry)
                        entry_dict["_block_timestamp"] = block_timestamps[bn]
                        enriched_logs.append(entry_dict)
                    logs = enriched_logs

                # ── Single transaction per batch ────────────────────────
                async with conn.transaction():
                    if logs:
                        for log_entry in logs:
                            await dispatch(log_entry, conn, global_cfg, addr_market_map, w3)

                    # Advance progress for ALL tracked markets
                    all_market_ids = set(addr_market_map.values()) - {None}
                    for mid in all_market_ids:
                        await conn.execute("""
                            INSERT INTO indexer_state (market_id, last_indexed_block, last_indexed_at, total_events)
                            VALUES ($1, $2, NOW(), (SELECT COUNT(*) FROM events WHERE market_id = $1))
                            ON CONFLICT (market_id) DO UPDATE SET
                              last_indexed_block = EXCLUDED.last_indexed_block,
                              last_indexed_at = EXCLUDED.last_indexed_at,
                              total_events = EXCLUDED.total_events
                            WHERE indexer_state.last_indexed_block < EXCLUDED.last_indexed_block
                        """, mid, to_block)
                        # Materialize global snapshot (precomputed JSON)
                        await snapshot_handler.materialize_snapshot(conn, mid)

            last_block = to_block
            log.info("Indexed blocks %d→%d (%d logs)", last_block - BATCH_SIZE + 1 if last_block > BATCH_SIZE else 0,
                     to_block, len(logs) if logs else 0)

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
