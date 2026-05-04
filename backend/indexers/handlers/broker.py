"""
handlers/broker.py — Handles BrokerFactory + PrimeBroker events.

BrokerFactory events (watched at factory address):
  - BrokerCreated(address indexed broker, address indexed owner, uint256 marketId)

PrimeBroker events (watched at each broker address):
  - PositionModified(bytes32 indexed marketId, address indexed user, int256 deltaCollateral, int256 deltaDebt)
  - ActivePositionChanged(uint256 oldTokenId, uint256 newTokenId)
  - ActiveTwammOrderChanged(bytes32 oldOrderId, bytes32 newOrderId)
  - BrokerFrozen(address indexed owner)
  - BrokerUnfrozen(address indexed owner)
  - OperatorUpdated(address indexed operator, bool active)

All broker state is maintained as a single upserted row in `brokers`.
All values stored as raw uint256 strings — no decimal conversion.
"""
import asyncpg
import logging

log = logging.getLogger(__name__)


async def refresh_broker_account_index(
    conn: asyncpg.Connection,
    market_id: str,
    owner: str,
    block_number: int = 0,
) -> None:
    """Rebuild the materialized broker list for one market+owner key."""
    if not market_id or not owner:
        return
    await conn.execute("""
        INSERT INTO broker_account_index (
          market_id, owner, brokers, newest_broker_address, broker_count, updated_block, updated_at
        )
        SELECT
          market_id,
          owner,
          jsonb_agg(
            jsonb_build_object(
              'address', address,
              'marketId', market_id,
              'owner', owner,
              'createdBlock', created_block,
              'activeTokenId', active_lp_token_id,
              'wausdcBalance', wausdc_balance,
              'wrlpBalance', wrlp_balance,
              'debtPrincipal', debt_principal,
              'updatedBlock', GREATEST(COALESCE(updated_block, 0), created_block),
              'isFrozen', is_frozen,
              'isLiquidated', is_liquidated
            ) ORDER BY created_block DESC, address DESC
          ) AS brokers,
          (array_agg(address ORDER BY created_block DESC, address DESC))[1] AS newest_broker_address,
          COUNT(*)::INT AS broker_count,
          GREATEST(COALESCE(MAX(updated_block), 0), COALESCE(MAX(created_block), 0), $3::BIGINT) AS updated_block,
          NOW() AS updated_at
        FROM brokers
        WHERE market_id=$1 AND owner=$2
        GROUP BY market_id, owner
        ON CONFLICT (market_id, owner) DO UPDATE SET
          brokers = EXCLUDED.brokers,
          newest_broker_address = EXCLUDED.newest_broker_address,
          broker_count = EXCLUDED.broker_count,
          updated_block = GREATEST(broker_account_index.updated_block, EXCLUDED.updated_block),
          updated_at = EXCLUDED.updated_at
    """, market_id, owner.lower(), block_number or 0)


async def refresh_broker_account_index_for_broker(
    conn: asyncpg.Connection,
    broker_address: str,
    block_number: int = 0,
) -> None:
    """Resolve a broker address and rebuild its market+owner projection."""
    if not broker_address:
        return
    row = await conn.fetchrow(
        "SELECT market_id, owner FROM brokers WHERE address=$1",
        broker_address.lower(),
    )
    if not row:
        return
    await refresh_broker_account_index(
        conn,
        row["market_id"],
        row["owner"],
        block_number,
    )


async def handle_broker_created(
    conn: asyncpg.Connection,
    market_id: str,
    broker_address: str,
    owner: str,
    block_number: int,
    tx_hash: str,
) -> None:
    await conn.execute("""
        INSERT INTO brokers (address, market_id, owner, created_block, created_tx, updated_block)
        VALUES ($1, $2, $3, $4, $5, $4)
        ON CONFLICT (address) DO NOTHING
    """, broker_address.lower(), market_id, owner.lower(), block_number, tx_hash)
    await refresh_broker_account_index(conn, market_id, owner, block_number)
    log.info("[broker] BrokerCreated market=%s broker=%s owner=%s block=%d",
             market_id, broker_address, owner, block_number)


async def handle_position_modified(
    conn: asyncpg.Connection,
    market_id: str,
    broker_address: str,
    delta_collateral: int,
    delta_debt: int,
    block_number: int,
) -> None:
    """
    PositionModified carries (deltaCollateral int256, deltaDebt int256).
    deltaCollateral is always 0 (known contract behavior — collateral managed by broker).
    Only deltaDebt matters: raw int256, stored as running sum in debt_principal.
    """
    if delta_debt == 0:
        return  # Nothing to update

    # debt_principal stored as raw uint256 string. We do arithmetic in Python.
    row = await conn.fetchrow(
        "SELECT debt_principal FROM brokers WHERE address = $1",
        broker_address.lower()
    )
    if not row:
        return

    current = int(row["debt_principal"] or "0")
    new_principal = current + delta_debt
    if new_principal < 0:
        new_principal = 0  # safety clamp

    await conn.execute(
        "UPDATE brokers SET debt_principal = $1, updated_block = GREATEST(updated_block, $3::BIGINT) WHERE address = $2",
        str(new_principal), broker_address.lower(), block_number
    )
    await refresh_broker_account_index_for_broker(conn, broker_address, block_number)
    log.debug("[broker] PositionModified broker=%s deltaDebt=%d newDebt=%s block=%d",
              broker_address, delta_debt, new_principal, block_number)


async def handle_active_position_changed(
    conn: asyncpg.Connection,
    broker_address: str,
    old_token_id: int,
    new_token_id: int,
    block_number: int = 0,
) -> None:
    """ActivePositionChanged(uint256 oldTokenId, uint256 newTokenId)"""
    await conn.execute(
        "UPDATE brokers SET active_lp_token_id = $1, updated_block = GREATEST(updated_block, $3::BIGINT) WHERE address = $2",
        str(new_token_id), broker_address.lower(), block_number or 0
    )
    await refresh_broker_account_index_for_broker(conn, broker_address, block_number)
    # Update lp_positions: new tokenId is active, old is not
    if old_token_id != 0:
        await conn.execute(
            "UPDATE lp_positions SET is_active = FALSE WHERE token_id = $1",
            str(old_token_id)
        )
    if new_token_id != 0:
        await conn.execute(
            "UPDATE lp_positions SET is_active = TRUE WHERE token_id = $1",
            str(new_token_id)
        )
    log.debug("[broker] ActivePositionChanged broker=%s old=%d new=%d",
              broker_address, old_token_id, new_token_id)


async def handle_active_twamm_order_changed(
    conn: asyncpg.Connection,
    broker_address: str,
    old_order_id: str,
    new_order_id: str,
    block_number: int = 0,
) -> None:
    """ActiveTwammOrderChanged(bytes32 oldOrderId, bytes32 newOrderId)"""
    await conn.execute(
        "UPDATE brokers SET active_twamm_order_id = $1, updated_block = GREATEST(updated_block, $3::BIGINT) WHERE address = $2",
        new_order_id, broker_address.lower(), block_number or 0
    )
    await refresh_broker_account_index_for_broker(conn, broker_address, block_number)
    # Update twamm_orders: new orderId is registered, old is not
    if old_order_id and old_order_id != "0x" + "00" * 32:
        await conn.execute(
            "UPDATE twamm_orders SET is_registered = FALSE WHERE order_id = $1",
            old_order_id
        )
    if new_order_id and new_order_id != "0x" + "00" * 32:
        await conn.execute(
            "UPDATE twamm_orders SET is_registered = TRUE WHERE order_id = $1",
            new_order_id
        )
    log.debug("[broker] ActiveTwammOrderChanged broker=%s old=%s new=%s",
              broker_address, old_order_id[:18], new_order_id[:18])


async def handle_broker_frozen(
    conn: asyncpg.Connection,
    broker_address: str,
    block_number: int = 0,
) -> None:
    """BrokerFrozen(address indexed owner) — emitted at broker address."""
    await conn.execute(
        "UPDATE brokers SET is_frozen = TRUE, updated_block = GREATEST(updated_block, $2::BIGINT) WHERE address = $1",
        broker_address.lower(), block_number or 0
    )
    await refresh_broker_account_index_for_broker(conn, broker_address, block_number)
    log.info("[broker] BrokerFrozen broker=%s", broker_address)


async def handle_broker_unfrozen(
    conn: asyncpg.Connection,
    broker_address: str,
    block_number: int = 0,
) -> None:
    """BrokerUnfrozen(address indexed owner) — emitted at broker address."""
    await conn.execute(
        "UPDATE brokers SET is_frozen = FALSE, updated_block = GREATEST(updated_block, $2::BIGINT) WHERE address = $1",
        broker_address.lower(), block_number or 0
    )
    await refresh_broker_account_index_for_broker(conn, broker_address, block_number)
    log.info("[broker] BrokerUnfrozen broker=%s", broker_address)


async def handle_operator_updated(
    conn: asyncpg.Connection,
    broker_address: str,
    operator: str,
    active: bool,
) -> None:
    """OperatorUpdated(address indexed operator, bool active)"""
    if active:
        await conn.execute("""
            INSERT INTO broker_operators (broker_address, operator)
            VALUES ($1, $2)
            ON CONFLICT (broker_address, operator) DO NOTHING
        """, broker_address.lower(), operator.lower())
    else:
        await conn.execute(
            "DELETE FROM broker_operators WHERE broker_address = $1 AND operator = $2",
            broker_address.lower(), operator.lower()
        )
    log.debug("[broker] OperatorUpdated broker=%s operator=%s active=%s",
              broker_address, operator, active)
