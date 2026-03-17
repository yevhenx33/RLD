"""
handlers/bond.py — BondMinted / BondClosed event handlers.

A bond is a frozen PrimeBroker clone with a short position + TWAMM buy-back.
BondFactory emits BondMinted on creation, BondClosed on redemption.
"""
import logging
import asyncpg

log = logging.getLogger(__name__)


async def handle_bond_minted(
    conn: asyncpg.Connection,
    market_id: str,
    owner: str,
    broker: str,
    notional: int,
    hedge: int,
    duration: int,
    block_number: int,
    tx_hash: str,
    factory_address: str = "",
) -> None:
    """Insert a new bond row. notional/hedge are raw 6-decimal ints.
    entry_rate is looked up from block_states.mark_price at mint_block.
    """
    # Look up mark price at mint block for entry rate
    entry_rate = await conn.fetchval(
        "SELECT mark_price FROM block_states WHERE block_number = $1",
        block_number,
    )

    await conn.execute("""
        INSERT INTO bonds (broker_address, market_id, owner, notional, hedge,
                           duration, mint_block, mint_tx, status, factory_address,
                           entry_rate)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'active', $9, $10)
        ON CONFLICT (broker_address) DO NOTHING
    """, broker.lower(), market_id, owner.lower(),
       notional, hedge, duration, block_number, tx_hash, factory_address.lower(),
       entry_rate)
    log.info("[bond] BondMinted owner=%s broker=%s notional=%d hedge=%d rate=%.4f dur=%d block=%d",
             owner[:10], broker[:10], notional, hedge, entry_rate or 0, duration, block_number)


async def handle_bond_closed(
    conn: asyncpg.Connection,
    broker: str,
    block_number: int,
    tx_hash: str,
) -> None:
    """Mark a bond as closed."""
    await conn.execute("""
        UPDATE bonds SET status = 'closed', close_block = $1, close_tx = $2
        WHERE broker_address = $3
    """, block_number, tx_hash, broker.lower())
    log.info("[bond] BondClosed broker=%s block=%d", broker[:10], block_number)
