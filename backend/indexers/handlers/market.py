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


async def handle_funding_applied(
    conn: asyncpg.Connection,
    market_id: str,
    block_number: int,
    block_timestamp: int,
    new_factor: int,
    funding_rate: int,
) -> None:
    await conn.execute("""
        INSERT INTO block_states (market_id, block_number, block_timestamp, normalization_factor, index_price, mark_price)
        VALUES ($1, $2, $3, $4,
                (SELECT index_price FROM block_states WHERE market_id=$1 AND index_price IS NOT NULL ORDER BY block_number DESC LIMIT 1),
                (SELECT mark_price FROM block_states WHERE market_id=$1 AND mark_price IS NOT NULL ORDER BY block_number DESC LIMIT 1))
        ON CONFLICT (market_id, block_number) DO UPDATE SET
          normalization_factor = EXCLUDED.normalization_factor,
          index_price = COALESCE(block_states.index_price, EXCLUDED.index_price),
          mark_price  = COALESCE(block_states.mark_price,  EXCLUDED.mark_price)
    """, market_id, block_number, block_timestamp, new_factor / 1e18)
    log.debug("[market] Funding applied market=%s block=%d nf=%s rate=%s", market_id, block_number, new_factor, funding_rate)


async def handle_market_state_updated(
    conn: asyncpg.Connection,
    market_id: str,
    block_number: int,
    block_timestamp: int,
    normalization_factor: int,
    total_debt: int,
) -> None:
    await conn.execute("""
        INSERT INTO block_states (market_id, block_number, block_timestamp, normalization_factor, total_debt, index_price, mark_price)
        VALUES ($1, $2, $3, $4, $5,
                (SELECT index_price FROM block_states WHERE market_id=$1 AND index_price IS NOT NULL ORDER BY block_number DESC LIMIT 1),
                (SELECT mark_price FROM block_states WHERE market_id=$1 AND mark_price IS NOT NULL ORDER BY block_number DESC LIMIT 1))
        ON CONFLICT (market_id, block_number) DO UPDATE SET
          normalization_factor = EXCLUDED.normalization_factor,
          total_debt = EXCLUDED.total_debt,
          index_price = COALESCE(block_states.index_price, EXCLUDED.index_price),
          mark_price  = COALESCE(block_states.mark_price,  EXCLUDED.mark_price)
    """, market_id, block_number, block_timestamp, normalization_factor / 1e18, total_debt / 1e6)
    log.debug("[market] state updated market=%s block=%d nf=%s debt=%s", market_id, block_number, normalization_factor, total_debt)


async def handle_rate_updated(
    conn: asyncpg.Connection,
    market_id: str,
    block_number: int,
    block_timestamp: int,
    index_price: int,
) -> None:
    await conn.execute("""
        INSERT INTO block_states (market_id, block_number, block_timestamp, index_price, mark_price)
        VALUES ($1, $2, $3, $4,
                (SELECT mark_price FROM block_states WHERE market_id=$1 AND mark_price IS NOT NULL ORDER BY block_number DESC LIMIT 1))
        ON CONFLICT (market_id, block_number) DO UPDATE SET
          index_price = EXCLUDED.index_price,
          mark_price  = COALESCE(block_states.mark_price, EXCLUDED.mark_price)
    """, market_id, block_number, block_timestamp, index_price / 1e18)
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
