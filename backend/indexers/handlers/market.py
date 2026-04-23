"""
handlers/market.py — Handles RLDCore + oracle events.

Events:
  - NormalizationFactorUpdated(bytes32 marketId, uint128 newFactor, uint128 timestamp)
  - MarketStateUpdated(bytes32 marketId, uint128 totalDebt, uint128 debtCap)
  - RateUpdated(bytes32 marketId, int256 indexPrice)  (from MockOracle)

All writes go into block_states via UPSERT — block_states has one row per
(market_id, block_number), updated as each event arrives within that block.
"""
import asyncpg
import logging

from handlers.pool import fetch_latest_state

log = logging.getLogger(__name__)


async def handle_funding_applied(
    conn: asyncpg.Connection,
    market_id: str,
    block_number: int,
    block_timestamp: int,
    new_factor: int,
    funding_rate: int,
) -> None:
    prev = await fetch_latest_state(conn, market_id)
    await conn.execute("""
        INSERT INTO block_states
          (market_id, block_number, block_timestamp,
           normalization_factor,
           index_price, mark_price, tick, sqrt_price_x96, liquidity, total_debt,
           token0_balance, token1_balance, fee_growth_global0, fee_growth_global1)
        VALUES ($1, $2, $3, $4,
                $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
        ON CONFLICT (market_id, block_number) DO UPDATE SET
          normalization_factor = EXCLUDED.normalization_factor,
          index_price         = COALESCE(block_states.index_price,         EXCLUDED.index_price),
          mark_price          = COALESCE(block_states.mark_price,          EXCLUDED.mark_price),
          tick                = COALESCE(block_states.tick,                EXCLUDED.tick),
          sqrt_price_x96      = COALESCE(block_states.sqrt_price_x96,      EXCLUDED.sqrt_price_x96),
          liquidity           = COALESCE(block_states.liquidity,           EXCLUDED.liquidity),
          total_debt          = COALESCE(block_states.total_debt,          EXCLUDED.total_debt),
          token0_balance      = COALESCE(block_states.token0_balance,      EXCLUDED.token0_balance),
          token1_balance      = COALESCE(block_states.token1_balance,      EXCLUDED.token1_balance),
          fee_growth_global0  = COALESCE(block_states.fee_growth_global0,  EXCLUDED.fee_growth_global0),
          fee_growth_global1  = COALESCE(block_states.fee_growth_global1,  EXCLUDED.fee_growth_global1)
    """, market_id, block_number, block_timestamp, new_factor / 1e18,
         prev.get("index_price"), prev.get("mark_price"),
         prev.get("tick"), prev.get("sqrt_price_x96"), prev.get("liquidity"),
         prev.get("total_debt"), prev.get("token0_balance"), prev.get("token1_balance"),
         prev.get("fee_growth_global0"), prev.get("fee_growth_global1"))
    log.debug("[market] Funding applied market=%s block=%d nf=%s rate=%s", market_id, block_number, new_factor, funding_rate)


async def handle_market_state_updated(
    conn: asyncpg.Connection,
    market_id: str,
    block_number: int,
    block_timestamp: int,
    normalization_factor: int,
    total_debt: int,
) -> None:
    prev = await fetch_latest_state(conn, market_id)
    await conn.execute("""
        INSERT INTO block_states
          (market_id, block_number, block_timestamp,
           normalization_factor, total_debt,
           index_price, mark_price, tick, sqrt_price_x96, liquidity,
           token0_balance, token1_balance, fee_growth_global0, fee_growth_global1)
        VALUES ($1, $2, $3, $4, $5,
                $6, $7, $8, $9, $10, $11, $12, $13, $14)
        ON CONFLICT (market_id, block_number) DO UPDATE SET
          normalization_factor = EXCLUDED.normalization_factor,
          total_debt          = EXCLUDED.total_debt,
          index_price         = COALESCE(block_states.index_price,         EXCLUDED.index_price),
          mark_price          = COALESCE(block_states.mark_price,          EXCLUDED.mark_price),
          tick                = COALESCE(block_states.tick,                EXCLUDED.tick),
          sqrt_price_x96      = COALESCE(block_states.sqrt_price_x96,      EXCLUDED.sqrt_price_x96),
          liquidity           = COALESCE(block_states.liquidity,           EXCLUDED.liquidity),
          token0_balance      = COALESCE(block_states.token0_balance,      EXCLUDED.token0_balance),
          token1_balance      = COALESCE(block_states.token1_balance,      EXCLUDED.token1_balance),
          fee_growth_global0  = COALESCE(block_states.fee_growth_global0,  EXCLUDED.fee_growth_global0),
          fee_growth_global1  = COALESCE(block_states.fee_growth_global1,  EXCLUDED.fee_growth_global1)
    """, market_id, block_number, block_timestamp,
         normalization_factor / 1e18, total_debt / 1e6,
         prev.get("index_price"), prev.get("mark_price"),
         prev.get("tick"), prev.get("sqrt_price_x96"), prev.get("liquidity"),
         prev.get("token0_balance"), prev.get("token1_balance"),
         prev.get("fee_growth_global0"), prev.get("fee_growth_global1"))
    log.debug("[market] state updated market=%s block=%d nf=%s debt=%s", market_id, block_number, normalization_factor, total_debt)


async def handle_rate_updated(
    conn: asyncpg.Connection,
    market_id: str,
    block_number: int,
    block_timestamp: int,
    index_price: int,
) -> None:
    prev = await fetch_latest_state(conn, market_id)
    await conn.execute("""
        INSERT INTO block_states
          (market_id, block_number, block_timestamp,
           index_price,
           mark_price, tick, sqrt_price_x96, liquidity, normalization_factor, total_debt,
           token0_balance, token1_balance, fee_growth_global0, fee_growth_global1)
        VALUES ($1, $2, $3, $4,
                $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
        ON CONFLICT (market_id, block_number) DO UPDATE SET
          index_price         = EXCLUDED.index_price,
          mark_price          = COALESCE(block_states.mark_price,          EXCLUDED.mark_price),
          tick                = COALESCE(block_states.tick,                EXCLUDED.tick),
          sqrt_price_x96      = COALESCE(block_states.sqrt_price_x96,      EXCLUDED.sqrt_price_x96),
          liquidity           = COALESCE(block_states.liquidity,           EXCLUDED.liquidity),
          normalization_factor = COALESCE(block_states.normalization_factor, EXCLUDED.normalization_factor),
          total_debt          = COALESCE(block_states.total_debt,          EXCLUDED.total_debt),
          token0_balance      = COALESCE(block_states.token0_balance,      EXCLUDED.token0_balance),
          token1_balance      = COALESCE(block_states.token1_balance,      EXCLUDED.token1_balance),
          fee_growth_global0  = COALESCE(block_states.fee_growth_global0,  EXCLUDED.fee_growth_global0),
          fee_growth_global1  = COALESCE(block_states.fee_growth_global1,  EXCLUDED.fee_growth_global1)
    """, market_id, block_number, block_timestamp, index_price / 1e18,
         prev.get("mark_price"), prev.get("tick"),
         prev.get("sqrt_price_x96"), prev.get("liquidity"),
         prev.get("normalization_factor"), prev.get("total_debt"),
         prev.get("token0_balance"), prev.get("token1_balance"),
         prev.get("fee_growth_global0"), prev.get("fee_growth_global1"))
    log.debug("[market] index_price updated market=%s block=%d price=%s", market_id, block_number, index_price)

async def handle_bad_debt_registered(
    conn: asyncpg.Connection,
    market_id: str,
    total_bad_debt: int,
) -> None:
    await conn.execute("""
        UPDATE markets SET bad_debt = $1 WHERE market_id = $2
    """, total_bad_debt / 1e6, market_id)
    log.debug("[market] Bad debt registered market=%s total=%s", market_id, total_bad_debt)
