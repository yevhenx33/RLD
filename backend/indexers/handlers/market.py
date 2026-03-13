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

log = logging.getLogger(__name__)


async def handle_normalization_factor_updated(
    conn: asyncpg.Connection,
    market_id: str,
    block_number: int,
    block_timestamp: int,
    new_factor: int,
) -> None:
    await conn.execute("""
        INSERT INTO block_states (market_id, block_number, block_timestamp, normalization_factor)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (market_id, block_number) DO UPDATE
          SET normalization_factor = EXCLUDED.normalization_factor
    """, market_id, block_number, block_timestamp, new_factor / 1e18)
    log.debug("[market] NF updated market=%s block=%d nf=%s", market_id, block_number, new_factor)


async def handle_market_state_updated(
    conn: asyncpg.Connection,
    market_id: str,
    block_number: int,
    block_timestamp: int,
    total_debt: int,
) -> None:
    await conn.execute("""
        INSERT INTO block_states (market_id, block_number, block_timestamp, total_debt)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (market_id, block_number) DO UPDATE
          SET total_debt = EXCLUDED.total_debt
    """, market_id, block_number, block_timestamp, total_debt / 1e6)
    log.debug("[market] debt updated market=%s block=%d debt=%s", market_id, block_number, total_debt)


async def handle_rate_updated(
    conn: asyncpg.Connection,
    market_id: str,
    block_number: int,
    block_timestamp: int,
    index_price: int,
) -> None:
    await conn.execute("""
        INSERT INTO block_states (market_id, block_number, block_timestamp, index_price)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (market_id, block_number) DO UPDATE
          SET index_price = EXCLUDED.index_price
    """, market_id, block_number, block_timestamp, index_price / 1e18)
    log.debug("[market] index_price updated market=%s block=%d price=%s", market_id, block_number, index_price)
