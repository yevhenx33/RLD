"""
handlers/twamm.py — Handles JTM hook + PrimeBroker TWAMM order events.

JTM Hook events (watched at twamm_hook address):
  - SubmitOrder(bytes32 indexed poolId, bytes32 indexed orderId, address owner,
                uint256 amountIn, uint160 expiration, bool zeroForOne,
                uint256 sellRate, uint256 earningsFactorLast, uint256 startEpoch,
                uint256 nonce)
  - CancelOrder(bytes32 indexed poolId, bytes32 indexed orderId, address owner, uint256 sellTokensRefund)

PrimeBroker events (watched at broker address):
  - TwammOrderSubmitted(bytes32 indexed orderId, bool zeroForOne, uint256 amountIn, uint256 expiration)
  - TwammOrderCancelled(bytes32 indexed orderId, uint256 buyTokensOut, uint256 sellTokensRefund)
  - TwammOrderClaimed(bytes32 indexed orderId, uint256 claimed0, uint256 claimed1)

TwapEngine events (watched at twap_engine address):
  - StreamSubmitted(bytes32 indexed marketId, bytes32 indexed orderId, address indexed owner, ...)
  - OrderCancelled(bytes32 indexed marketId, bytes32 indexed orderId, address indexed owner, ...)
  - TokensClaimed(bytes32 indexed marketId, bytes32 indexed orderId, address indexed owner, ...)

All values stored as raw uint256 strings. Frontend handles decimal conversion.
"""
import asyncpg
import logging

log = logging.getLogger(__name__)


async def handle_submit_order(
    conn: asyncpg.Connection,
    pool_id: str | None,
    order_id: str,
    owner: str,
    amount_in: int,
    expiration: int,
    zero_for_one: bool,
    sell_rate: int | None,
    start_epoch: int | None,
    nonce: int | None,
    block_number: int,
    tx_hash: str,
) -> None:
    """
    JTM hook SubmitOrder — primary source for order creation.
    Has all fields including sell_rate, start_epoch, and nonce.
    """
    await conn.execute("""
        INSERT INTO twamm_orders
          (order_id, pool_id, owner, amount_in, expiration, start_epoch,
           sell_rate, nonce, zero_for_one, block_number, tx_hash)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        ON CONFLICT (order_id) DO UPDATE SET
          sell_rate = COALESCE(EXCLUDED.sell_rate, twamm_orders.sell_rate),
          start_epoch = COALESCE(EXCLUDED.start_epoch, twamm_orders.start_epoch),
          nonce = COALESCE(EXCLUDED.nonce, twamm_orders.nonce),
          pool_id = COALESCE(EXCLUDED.pool_id, twamm_orders.pool_id)
    """, order_id, pool_id, owner.lower(),
         str(amount_in), expiration, start_epoch,
         str(sell_rate) if sell_rate else None,
         nonce,
         zero_for_one, block_number, tx_hash)
    log.info("[twamm] SubmitOrder orderId=%s owner=%s amount=%d expiry=%d nonce=%s",
             order_id[:18], owner, amount_in, expiration, nonce)


async def handle_twamm_order_submitted(
    conn: asyncpg.Connection,
    broker_address: str,
    order_id: str,
    zero_for_one: bool,
    amount_in: int,
    expiration: int,
    block_number: int,
    tx_hash: str,
) -> None:
    """
    PrimeBroker TwammOrderSubmitted — emitted at broker address.
    May fire before or after the JTM hook SubmitOrder in the same tx.
    UPSERT to merge with hook-level data.
    """
    await conn.execute("""
        INSERT INTO twamm_orders
          (order_id, owner, amount_in, expiration, zero_for_one, block_number, tx_hash)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (order_id) DO UPDATE SET
          owner = COALESCE(EXCLUDED.owner, twamm_orders.owner)
    """, order_id, broker_address.lower(),
         str(amount_in), expiration, zero_for_one,
         block_number, tx_hash)
    log.info("[twamm] TwammOrderSubmitted broker=%s orderId=%s",
             broker_address, order_id[:18])


async def handle_twamm_order_cancelled(
    conn: asyncpg.Connection,
    order_id: str,
    buy_tokens_out: int,
    sell_tokens_refund: int,
) -> None:
    """TwammOrderCancelled(bytes32 indexed orderId, uint256 buyTokensOut, uint256 sellTokensRefund)"""
    await conn.execute("""
        UPDATE twamm_orders
        SET status = 'cancelled', buy_tokens_out = $1, sell_tokens_refund = $2
        WHERE order_id = $3
    """, str(buy_tokens_out), str(sell_tokens_refund), order_id)
    log.info("[twamm] TwammOrderCancelled orderId=%s buyOut=%d refund=%d",
             order_id[:18], buy_tokens_out, sell_tokens_refund)


async def handle_twamm_order_claimed(
    conn: asyncpg.Connection,
    order_id: str,
    claimed0: int,
    claimed1: int,
) -> None:
    """TwammOrderClaimed(bytes32 indexed orderId, uint256 claimed0, uint256 claimed1)"""
    await conn.execute("""
        UPDATE twamm_orders
        SET status = 'claimed', buy_tokens_out = $1
        WHERE order_id = $2
    """, str(max(claimed0, claimed1)), order_id)
    log.info("[twamm] TwammOrderClaimed orderId=%s claimed0=%d claimed1=%d",
             order_id[:18], claimed0, claimed1)


async def handle_cancel_order_hook(
    conn: asyncpg.Connection,
    order_id: str,
    sell_tokens_refund: int,
) -> None:
    """CancelOrder from JTM hook — fallback if PrimeBroker event wasn't caught."""
    await conn.execute("""
        UPDATE twamm_orders
        SET status = 'cancelled', sell_tokens_refund = $1
        WHERE order_id = $2 AND status = 'active'
    """, str(sell_tokens_refund), order_id)
    log.info("[twamm] CancelOrder(hook) orderId=%s refund=%d", order_id[:18], sell_tokens_refund)
