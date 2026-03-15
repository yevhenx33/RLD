"""
handlers/pool.py — Handles Uniswap V4 PoolManager events.

Events from PoolManager (watched always, from session_start_block):
  - Swap(bytes32 indexed id, address indexed sender, int128 amount0, int128 amount1,
          uint160 sqrtPriceX96, uint128 liquidity, int24 tick, uint24 fee)
  - ModifyLiquidity(bytes32 indexed id, address indexed sender,
                    int24 tickLower, int24 tickUpper, int256 liquidityDelta, bytes32 salt)

Pool events are the source of:
  - block_states (tick, sqrtPriceX96, mark_price, liquidity, token balances)
  - candles (all resolutions, inline upsert)
  - volume_usd (accumulated in candles)
"""
import asyncpg
import logging
import math

log = logging.getLogger(__name__)

# Resolution → interval seconds
RESOLUTIONS = {
    "1m":  60,
    "5m":  300,
    "15m": 900,
    "1h":  3600,
    "4h":  14400,
    "1d":  86400,
}


def sqrt_price_x96_to_price(sqrt_price_x96: int, wausdc: str, wrlp: str) -> float:
    """
    Convert Uniswap V4 sqrtPriceX96 to a standardized Mark Price (wRLP/waUSDC).
    Uniswap sorts token0 (lower address) and token1 (higher address).
    sqrtPriceX96 is always token1 / token0.
    """
    if sqrt_price_x96 == 0:
        return 0.0

    token0_is_wausdc = wausdc.lower() < wrlp.lower()
    raw_price = (sqrt_price_x96 / (2 ** 96)) ** 2

    if token0_is_wausdc:
        # P = wRLP / waUSDC. We want waUSDC per wRLP, so invert.
        return 1.0 / raw_price if raw_price > 0 else 0.0
    else:
        # P = waUSDC / wRLP. Already what we want.
        return raw_price


def tick_to_price(tick: int) -> float:
    return math.pow(1.0001, tick)


async def handle_swap(
    conn: asyncpg.Connection,
    market_id: str,
    block_number: int,
    block_timestamp: int,
    sqrt_price_x96: int,
    tick: int,
    amount0: int,
    amount1: int,
    liquidity: int,
    wausdc: str,
    wrlp: str,
) -> None:
    mark_price = sqrt_price_x96_to_price(sqrt_price_x96, wausdc, wrlp)

    # Volume: absolute value of the USDC side (amount1 for wRLP/waUSDC pools where token1=waUSDC)
    volume_usd = abs(amount1) / 1e6

    # Upsert block_states (pool fields only — other fields may have been set by market handler)
    await conn.execute("""
        INSERT INTO block_states
          (market_id, block_number, block_timestamp, sqrt_price_x96, tick, mark_price, liquidity, index_price)
        VALUES ($1, $2, $3, $4, $5, $6, $7,
                (SELECT index_price FROM block_states WHERE market_id=$1 AND index_price IS NOT NULL ORDER BY block_number DESC LIMIT 1))
        ON CONFLICT (market_id, block_number) DO UPDATE SET
          sqrt_price_x96 = EXCLUDED.sqrt_price_x96,
          tick           = EXCLUDED.tick,
          mark_price     = EXCLUDED.mark_price,
          liquidity      = EXCLUDED.liquidity,
          index_price    = COALESCE(block_states.index_price, EXCLUDED.index_price)
    """, market_id, block_number, block_timestamp,
         str(sqrt_price_x96), tick, mark_price, str(liquidity))

    # Get current index_price for candles (read from latest block_state)
    row = await conn.fetchrow("""
        SELECT index_price FROM block_states
        WHERE market_id = $1 AND index_price IS NOT NULL
        ORDER BY block_number DESC LIMIT 1
    """, market_id)
    index_price = float(row["index_price"]) if row and row["index_price"] else mark_price

    # Inline candle upsert for all resolutions — all in this transaction
    for res, secs in RESOLUTIONS.items():
        bucket = (block_timestamp // secs) * secs
        await conn.execute("""
            INSERT INTO candles
              (market_id, resolution, bucket,
               mark_open, mark_high, mark_low, mark_close,
               index_open, index_high, index_low, index_close,
               volume_usd, swap_count)
            VALUES ($1, $2, $3, $4, $4, $4, $4, $5, $5, $5, $5, $6, 1)
            ON CONFLICT (market_id, resolution, bucket) DO UPDATE SET
              mark_high   = GREATEST(candles.mark_high,  EXCLUDED.mark_high),
              mark_low    = LEAST(candles.mark_low,    EXCLUDED.mark_low),
              mark_close  = EXCLUDED.mark_close,
              index_high  = GREATEST(candles.index_high, EXCLUDED.index_high),
              index_low   = LEAST(candles.index_low,   EXCLUDED.index_low),
              index_close = EXCLUDED.index_close,
              volume_usd  = candles.volume_usd + EXCLUDED.volume_usd,
              swap_count  = candles.swap_count + 1
        """, market_id, res, bucket, mark_price, index_price, volume_usd)

    log.debug("[pool] Swap market=%s block=%d tick=%d price=%.6f vol=%.2f",
              market_id, block_number, tick, mark_price, volume_usd)


async def handle_modify_liquidity(
    conn: asyncpg.Connection,
    market_id: str,
    block_number: int,
    block_timestamp: int,
    tick_lower: int,
    tick_upper: int,
    liquidity_delta: int,
    sqrt_price_x96: int,
    wausdc: str,
    wrlp: str,
) -> None:
    # Update block_states liquidity snapshot
    await conn.execute("""
        INSERT INTO block_states
          (market_id, block_number, block_timestamp, tick, mark_price, index_price)
        VALUES ($1, $2, $3, $4, $5,
                (SELECT index_price FROM block_states WHERE market_id=$1 AND index_price IS NOT NULL ORDER BY block_number DESC LIMIT 1))
        ON CONFLICT (market_id, block_number) DO UPDATE SET
          tick        = COALESCE(block_states.tick,       EXCLUDED.tick),
          mark_price  = COALESCE(block_states.mark_price, EXCLUDED.mark_price),
          index_price = COALESCE(block_states.index_price, EXCLUDED.index_price)
    """, market_id, block_number, block_timestamp,
         None, sqrt_price_x96_to_price(sqrt_price_x96, wausdc, wrlp) if sqrt_price_x96 else None)

    log.debug("[pool] ModifyLiquidity market=%s block=%d delta=%d [%d, %d]",
              market_id, block_number, liquidity_delta, tick_lower, tick_upper)
