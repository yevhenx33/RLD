"""
handlers/lp.py — Handles V4 LP position lifecycle events.

PrimeBroker events (emitted at broker address):
  - LiquidityAdded(uint256 indexed tokenId, uint128 liquidity)
  - LiquidityRemoved(uint256 indexed tokenId, uint128 liquidity, bool burned)

V4 PositionManager events:
  - Transfer(address from, address to, uint256 tokenId) — ERC721 ownership change

ModifyLiquidity from PoolManager provides tick range data (decoded in indexer.py).

LP positions are stored in a normalized `lp_positions` table keyed by token_id.
The broker just holds a foreign key via `active_lp_token_id`.
"""
import asyncpg
import logging

log = logging.getLogger(__name__)


async def handle_liquidity_added(
    conn: asyncpg.Connection,
    broker_address: str,
    token_id: int,
    liquidity: int,
    block_number: int,
) -> None:
    """
    LiquidityAdded(uint256 indexed tokenId, uint128 liquidity)
    Emitted by PrimeBroker when adding liquidity through the broker.
    UPSERT into lp_positions — tick range comes separately from ModifyLiquidity.
    """
    await conn.execute("""
        INSERT INTO lp_positions (token_id, owner, liquidity, mint_block)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (token_id) DO UPDATE SET
          liquidity = EXCLUDED.liquidity,
          owner = EXCLUDED.owner
    """, str(token_id), broker_address.lower(), str(liquidity), block_number)
    log.info("[lp] LiquidityAdded broker=%s tokenId=%d liq=%d block=%d",
             broker_address, token_id, liquidity, block_number)


async def handle_liquidity_removed(
    conn: asyncpg.Connection,
    token_id: int,
    liquidity: int,
    burned: bool,
) -> None:
    """
    LiquidityRemoved(uint256 indexed tokenId, uint128 liquidity, bool burned)
    Update liquidity; mark as burned if fully removed.
    """
    await conn.execute("""
        UPDATE lp_positions
        SET liquidity = $1, is_burned = $2
        WHERE token_id = $3
    """, str(liquidity), burned, str(token_id))
    log.info("[lp] LiquidityRemoved tokenId=%d liq=%d burned=%s",
             token_id, liquidity, burned)


async def handle_lp_nft_transfer(
    conn: asyncpg.Connection,
    from_addr: str,
    to_addr: str,
    token_id: int,
    block_number: int,
) -> None:
    """
    ERC721 Transfer on V4 PositionManager.
    - from=0x0 → mint (INSERT new position with owner=to)
    - Otherwise → transfer (UPDATE owner)
    """
    zero_addr = "0x" + "00" * 20
    if from_addr == zero_addr:
        # Mint — create position stub. Tick range enriched by ModifyLiquidity.
        await conn.execute("""
            INSERT INTO lp_positions (token_id, owner, liquidity, mint_block)
            VALUES ($1, $2, '0', $3)
            ON CONFLICT (token_id) DO UPDATE SET owner = EXCLUDED.owner
        """, str(token_id), to_addr.lower(), block_number)
        log.info("[lp] NFT Mint tokenId=%d to=%s", token_id, to_addr)
    else:
        # Transfer — update owner
        await conn.execute(
            "UPDATE lp_positions SET owner = $1 WHERE token_id = $2",
            to_addr.lower(), str(token_id)
        )
        log.info("[lp] NFT Transfer tokenId=%d from=%s to=%s",
                 token_id, from_addr, to_addr)


async def enrich_tick_range(
    conn: asyncpg.Connection,
    token_id: int,
    tick_lower: int,
    tick_upper: int,
    liquidity_delta: int | None = None,
    pool_id: str | None = None,
) -> None:
    """
    Called from ModifyLiquidity dispatch to fill in tick range and pool_id
    for an LP position that was just created.
    The salt field of ModifyLiquidity = bytes32(tokenId).
    
    Uses UPSERT because ModifyLiquidity often fires BEFORE LiquidityAdded
    in the same transaction (lower log_index), so the row may not exist yet.
    """
    liquidity_value = str(abs(liquidity_delta)) if liquidity_delta else "0"
    await conn.execute("""
        INSERT INTO lp_positions (token_id, tick_lower, tick_upper, pool_id, owner, liquidity, mint_block)
        VALUES ($1, $2, $3, $4, '', $5, 0)
        ON CONFLICT (token_id) DO UPDATE SET
          tick_lower = COALESCE($2, lp_positions.tick_lower),
          tick_upper = COALESCE($3, lp_positions.tick_upper),
          pool_id = COALESCE($4, lp_positions.pool_id),
          liquidity = CASE
            WHEN CAST(lp_positions.liquidity AS NUMERIC) = 0 AND CAST($5 AS NUMERIC) > 0
            THEN $5
            ELSE lp_positions.liquidity
          END
    """, str(token_id), tick_lower, tick_upper, pool_id, liquidity_value)
    log.debug("[lp] Enriched tick range tokenId=%d ticks=[%d,%d] pool=%s",
              token_id, tick_lower, tick_upper, pool_id)
