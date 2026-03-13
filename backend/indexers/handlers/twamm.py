"""
handlers/twamm.py — Handles TWAMM hook SubmitOrder / CancelOrder events.

Events (watched at twamm_hook address per market):
  JIT Taker Market (JTM) hook events:
  - SubmitOrder(address indexed owner, uint160 expiration, bool zeroForOne, uint256 amountIn)
  - CancelOrder(address indexed owner, uint160 expiration, bool zeroForOne)

order_id = keccak256(abi.encode(owner, expiration, zeroForOne)) — matches the hook's key scheme.
"""
import asyncpg
import logging
from eth_abi import encode
from eth_utils import keccak

log = logging.getLogger(__name__)


def make_order_id(owner: str, expiration: int, zero_for_one: bool) -> str:
    """Deterministic order ID matching the hook's internal key scheme."""
    packed = encode(
        ["address", "uint160", "bool"],
        [owner, expiration, zero_for_one]
    )
    return "0x" + keccak(packed).hex()


async def handle_submit_order(
    conn: asyncpg.Connection,
    market_id: str,
    owner: str,
    expiration: int,
    start_epoch: int,
    zero_for_one: bool,
    amount_in: int,
    block_number: int,
    tx_hash: str,
    broker_address: str | None = None,
) -> None:
    order_id = make_order_id(owner, expiration, zero_for_one)
    await conn.execute("""
        INSERT INTO twamm_orders
          (order_id, market_id, owner, broker_address, amount_in,
           expiration, start_epoch, zero_for_one, block_number, tx_hash, is_cancelled)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, FALSE)
        ON CONFLICT (order_id) DO NOTHING
    """, order_id, market_id, owner.lower(),
         broker_address.lower() if broker_address else None,
         str(amount_in), expiration, start_epoch, zero_for_one,
         block_number, tx_hash)
    log.info("[twamm] SubmitOrder market=%s owner=%s orderId=%s expiry=%d",
             market_id, owner, order_id, expiration)


async def handle_cancel_order(
    conn: asyncpg.Connection,
    owner: str,
    expiration: int,
    zero_for_one: bool,
) -> None:
    order_id = make_order_id(owner, expiration, zero_for_one)
    await conn.execute("""
        UPDATE twamm_orders SET is_cancelled = TRUE WHERE order_id = $1
    """, order_id)
    log.info("[twamm] CancelOrder orderId=%s", order_id)
