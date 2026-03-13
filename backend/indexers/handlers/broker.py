"""
handlers/broker.py — Handles BrokerFactory + PrimeBroker events.

BrokerFactory events (watched at factory address):
  - BrokerCreated(address indexed broker, address indexed owner, bytes32 marketId)

PrimeBroker events (watched at each broker address, added to watch set after BrokerCreated):
  - CollateralDeposited(address indexed token, uint256 amount)
  - CollateralWithdrawn(address indexed token, uint256 amount)
  - PositionMinted(uint256 amount)
  - PositionBurned(uint256 amount)
  - ActiveTokenSet(uint256 tokenId)

All broker state is maintained as a single upserted row in `brokers`.
"""
import asyncpg
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


async def handle_broker_created(
    conn: asyncpg.Connection,
    market_id: str,
    broker_address: str,
    owner: str,
    block_number: int,
    tx_hash: str,
) -> None:
    """
    Insert new broker. Also inserts a row into indexer_state if not present
    (the indexer loop will use this to know this broker needs watching).
    """
    await conn.execute("""
        INSERT INTO brokers (address, market_id, owner, created_block, created_tx)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (address) DO NOTHING
    """, broker_address.lower(), market_id, owner.lower(), block_number, tx_hash)
    log.info("[broker] BrokerCreated market=%s broker=%s owner=%s block=%d",
             market_id, broker_address, owner, block_number)


async def handle_collateral_deposited(
    conn: asyncpg.Connection,
    broker_address: str,
    token: str,
    amount: int,
    block_number: int,
    tx_hash: str,
) -> None:
    # State is recalculated from getFullState() via the pool handler writing block_states.
    # Here we just record the raw event for audit.
    log.debug("[broker] CollateralDeposited broker=%s token=%s amount=%d block=%d",
              broker_address, token, amount, block_number)


async def handle_active_token_set(
    conn: asyncpg.Connection,
    broker_address: str,
    token_id: int,
) -> None:
    await conn.execute("""
        UPDATE brokers SET active_token_id = $1 WHERE address = $2
    """, token_id, broker_address.lower())
    # Also update lp_positions: the given token_id is now active, all others for this broker are not
    await conn.execute("""
        UPDATE lp_positions
        SET is_active = (token_id = $1)
        WHERE broker_address = $2
    """, token_id, broker_address.lower())
    log.debug("[broker] ActiveTokenSet broker=%s tokenId=%d", broker_address, token_id)


async def update_broker_state(
    conn: asyncpg.Connection,
    broker_address: str,
    collateral: int | None = None,
    debt: int | None = None,
    collateral_value: int | None = None,
    debt_value: int | None = None,
    health_factor: str | None = None,
) -> None:
    """
    Partial update of broker state. Called after getFullState() enrichment or
    from events that carry state diffs.
    Only non-None fields are written.
    """
    fields = {}
    if collateral is not None:
        fields["collateral"] = collateral / 1e6
    if debt is not None:
        fields["debt"] = debt / 1e6
    if collateral_value is not None:
        fields["collateral_value"] = collateral_value / 1e6
    if debt_value is not None:
        fields["debt_value"] = debt_value / 1e6
    if health_factor is not None:
        fields["health_factor"] = health_factor

    if not fields:
        return

    set_clause = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(fields))
    values = [broker_address.lower()] + list(fields.values())
    await conn.execute(
        f"UPDATE brokers SET {set_clause} WHERE address = $1",
        *values
    )
