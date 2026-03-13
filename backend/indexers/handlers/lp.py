"""
handlers/lp.py — Handles V4 LP position lifecycle events.

Sources:
  - PrimeBroker emits: V4LiquidityAdded(uint256 tokenId, int24 tickLower, int24 tickUpper, uint128 liquidity)
  - PrimeBroker emits: V4LiquidityRemoved(uint256 tokenId, uint128 liquidityRemoved)
  - V4 PositionManager emits: Transfer(address from, address to, uint256 tokenId)
    Used to detect broker receiving an NFT (mint to broker = new LP position)
  - PrimeBroker emits: ActiveTokenSet(uint256 tokenId)  [handled in broker.py]
"""
import asyncpg
import logging

log = logging.getLogger(__name__)


async def handle_v4_liquidity_added(
    conn: asyncpg.Connection,
    market_id: str,
    broker_address: str,
    token_id: int,
    tick_lower: int,
    tick_upper: int,
    liquidity: int,
    block_number: int,
) -> None:
    """
    Called when a broker mints a new V4 LP position.
    entry_price is enriched later from block_states at mint_block.
    """
    # Resolve entry_price from block_states at this block (best effort)
    row = await conn.fetchrow("""
        SELECT mark_price FROM block_states
        WHERE market_id = $1 AND block_number <= $2 AND mark_price IS NOT NULL
        ORDER BY block_number DESC LIMIT 1
    """, market_id, block_number)
    entry_price = float(row["mark_price"]) if row else None

    # entry_tick from entry_price
    import math
    entry_tick = round(math.log(entry_price) / math.log(1.0001)) if entry_price else None

    await conn.execute("""
        INSERT INTO lp_positions
          (token_id, market_id, broker_address, liquidity, tick_lower, tick_upper,
           entry_price, entry_tick, mint_block, is_active, is_burned)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, FALSE, FALSE)
        ON CONFLICT (token_id) DO UPDATE SET
          liquidity = EXCLUDED.liquidity
    """, token_id, market_id, broker_address.lower(), str(liquidity),
         tick_lower, tick_upper, entry_price, entry_tick, block_number)

    log.info("[lp] LiquidityAdded market=%s broker=%s tokenId=%d ticks=[%d,%d] liq=%d",
             market_id, broker_address, token_id, tick_lower, tick_upper, liquidity)


async def handle_v4_liquidity_removed(
    conn: asyncpg.Connection,
    token_id: int,
    liquidity_remaining: int,
) -> None:
    """
    Update liquidity and mark as burned if fully removed (liquidity=0).
    """
    is_burned = liquidity_remaining == 0
    await conn.execute("""
        UPDATE lp_positions
        SET liquidity = $1, is_burned = $2
        WHERE token_id = $3
    """, str(liquidity_remaining), is_burned, token_id)

    log.info("[lp] LiquidityRemoved tokenId=%d remaining=%d burned=%s",
             token_id, liquidity_remaining, is_burned)
