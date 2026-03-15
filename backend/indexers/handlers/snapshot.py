"""
handlers/snapshot.py — Materializes the global snapshot JSONB blob.

Called once per block-batch at the end of the indexer's transaction.
Reads the latest block_state + all brokers, computes derived metrics,
and stores the pre-built JSON in markets.snapshot.

Design: Every GraphQL read of "snapshot" is a single-row SELECT — zero computation.
"""
import asyncpg
import json
import logging
import math

log = logging.getLogger(__name__)


async def materialize_snapshot(
    conn: asyncpg.Connection,
    market_id: str,
) -> None:
    """Build and store the global snapshot for the given market."""

    # ── Latest block state ──
    latest = await conn.fetchrow("""
        SELECT block_number, block_timestamp,
               normalization_factor, total_debt, index_price,
               sqrt_price_x96, tick, mark_price, liquidity,
               token0_balance, token1_balance,
               fee_growth_global0, fee_growth_global1
        FROM block_states
        WHERE market_id = $1
        ORDER BY block_number DESC LIMIT 1
    """, market_id)
    if not latest:
        return

    block_number = latest["block_number"]
    block_timestamp = latest["block_timestamp"]
    nf = float(latest["normalization_factor"] or 1)
    total_debt = float(latest["total_debt"] or 0)
    index_price = float(latest["index_price"] or 0)
    mark_price = float(latest["mark_price"] or 0)
    tick = int(latest["tick"] or 0)
    liquidity = str(latest["liquidity"] or "0")
    sqrt_price_x96 = str(latest["sqrt_price_x96"] or "0")
    t0_balance = float(latest["token0_balance"] or 0)
    t1_balance = float(latest["token1_balance"] or 0)
    fg0 = str(latest["fee_growth_global0"] or "0")
    fg1 = str(latest["fee_growth_global1"] or "0")

    # ── Volume 24H ──
    cutoff = block_timestamp - 86400
    vol_row = await conn.fetchrow("""
        SELECT COALESCE(SUM(swap_volume), 0) AS vol,
               COALESCE(SUM(swap_count), 0) AS cnt
        FROM block_states
        WHERE market_id = $1 AND block_timestamp > $2
    """, market_id, cutoff)
    volume_24h = float(vol_row["vol"]) if vol_row else 0
    swap_count_24h = int(vol_row["cnt"]) if vol_row else 0

    # ── 24H price changes ──
    old_row = await conn.fetchrow("""
        SELECT mark_price, index_price FROM block_states
        WHERE market_id = $1 AND block_timestamp <= $2
              AND mark_price IS NOT NULL
        ORDER BY block_number DESC LIMIT 1
    """, market_id, cutoff)
    old_mark = float(old_row["mark_price"]) if old_row and old_row["mark_price"] else mark_price
    old_index = float(old_row["index_price"]) if old_row and old_row["index_price"] else index_price
    mark_24h = ((mark_price - old_mark) / old_mark * 100) if old_mark > 0 else 0
    index_24h = ((index_price - old_index) / old_index * 100) if old_index > 0 else 0

    # ── Derived metrics ──
    normalized_mark = mark_price / nf if nf > 0 else mark_price
    peg_dev = (normalized_mark - index_price) / index_price * 100 if index_price > 0 else 0

    funding_rate = (normalized_mark - index_price) / index_price if index_price > 0 else 0
    # Read funding_period_sec from markets table
    market_row = await conn.fetchrow(
        "SELECT funding_period_sec FROM markets WHERE market_id = $1", market_id
    )
    funding_period = int(market_row["funding_period_sec"]) if market_row else 2_592_000
    year_sec = 365 * 86400
    ann_exp = -funding_rate * year_sec / funding_period
    ann_exp_clamped = max(-20, min(20, ann_exp))
    funding_ann_pct = (math.exp(ann_exp_clamped) - 1) * 100

    # Pool TVL (t0 is position token, t1 is collateral in 6-dec raw)
    # t0_balance and t1_balance are raw integers
    t0_human = t0_balance / 1e6
    t1_human = t1_balance / 1e6
    tvl = t0_human * mark_price + t1_human

    # ── Brokers ──
    brokers = await conn.fetch("""
        SELECT address, owner, wausdc_balance, debt_principal,
               wausdc_value, wrlp_value, health_factor
        FROM brokers WHERE market_id = $1
    """, market_id)

    total_col = sum(float(b["wausdc_value"] or 0) for b in brokers)
    total_debt_usd = sum(float(b["debt_principal"] or 0) * index_price for b in brokers)
    over_collat = (total_col / total_debt_usd * 100) if total_debt_usd > 0 else 0

    def _broker_status(hf):
        try:
            hf_val = float(hf)
        except (TypeError, ValueError):
            return "unknown"
        if hf_val > 2:
            return "safe"
        elif hf_val > 1.2:
            return "watch"
        return "danger"

    system_health = "SAFE" if over_collat > 200 else ("WATCH" if over_collat > 120 else "DANGER")

    # ── Build JSON blob ──
    snapshot = {
        "blockNumber": block_number,
        "blockTimestamp": block_timestamp,
        "market": {
            "marketId": market_id,
            "blockNumber": block_number,
            "blockTimestamp": block_timestamp,
            "normalizationFactor": str(int(nf * 1e18)),
            "totalDebt": str(int(total_debt * 1e6)),
            "indexPrice": str(int(index_price * 1e18)),
            "lastUpdateTimestamp": block_timestamp,
        },
        "pool": {
            "poolId": (market_row["funding_period_sec"] if False else  # placeholder
                       (await conn.fetchval("SELECT pool_id FROM markets WHERE market_id = $1", market_id)) or ""),
            "markPrice": mark_price,
            "tick": tick,
            "liquidity": liquidity,
            "sqrtPriceX96": sqrt_price_x96,
            "token0Balance": str(int(t0_balance)),
            "token1Balance": str(int(t1_balance)),
            "tvlUsd": round(tvl, 2),
            "feeGrowthGlobal0": fg0,
            "feeGrowthGlobal1": fg1,
        },
        "derived": {
            "pegDeviationPct": round(peg_dev, 4),
            "fundingRateAnnPct": round(funding_ann_pct, 4),
            "nfDriftAnnPct": 0,  # requires historical NF tracking — future
            "overCollatPct": round(over_collat, 2),
            "volume24hUsd": round(volume_24h, 2),
            "swapCount24h": swap_count_24h,
            "mark24hChangePct": round(mark_24h, 4),
            "index24hChangePct": round(index_24h, 4),
            "systemHealth": system_health,
            "poolTvlUsd": round(tvl, 2),
        },
        "brokers": [
            {
                "address": b["address"],
                "owner": b["owner"],
                "collateral": str(int(float(b["wausdc_balance"] or 0) * 1e6)),
                "debt": str(int(float(b["debt_principal"] or 0) * 1e6)),
                "collateralValue": str(int(float(b["wausdc_value"] or 0) * 1e6)),
                "debtValue": str(int(float(b["debt_principal"] or 0) * index_price * 1e6)),
                "healthFactor": str(b["health_factor"] or "0"),
            }
            for b in brokers
        ],
    }

    await conn.execute(
        "UPDATE markets SET snapshot = $1 WHERE market_id = $2",
        json.dumps(snapshot), market_id
    )
    log.debug("[snapshot] Materialized for market=%s block=%d (vol24h=%.0f, %d brokers)",
              market_id[:16], block_number, volume_24h, len(brokers))
