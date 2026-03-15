"""
handlers/pool.py — Handles Uniswap V4 PoolManager events.

Events from PoolManager (watched always, from session_start_block):
  - Swap(bytes32 indexed id, address indexed sender, int128 amount0, int128 amount1,
          uint160 sqrtPriceX96, uint128 liquidity, int24 tick, uint24 fee)
  - ModifyLiquidity(bytes32 indexed id, address indexed sender,
                    int24 tickLower, int24 tickUpper, int256 liquidityDelta, bytes32 salt)

Pool events are the source of:
  - block_states (tick, sqrtPriceX96, mark_price, liquidity, token balances, fee growth)
  - candles (all resolutions, inline upsert)
  - volume_usd (accumulated in candles + block_states.swap_volume)
  - tick_liquidity_net (per-tick liquidity deltas for distribution bins)
  - markets.liquidity_bins (materialized JSON, rebuilt on ModifyLiquidity)
"""
import asyncpg
import json
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

# Pool fee in hundredths of bip (500 = 0.05%)
POOL_FEE = 500

Q128 = 2**128
Q96 = 2**96

# ── Carry-forward subselect helpers ────────────────────────────────────────
# Each returns the last known non-null value for a column in block_states.
_CF = "(SELECT {col} FROM block_states WHERE market_id=$1 AND {col} IS NOT NULL ORDER BY block_number DESC LIMIT 1)"

def _cf(col: str) -> str:
    return _CF.format(col=col)


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


# ── Fee growth computation (integer-precise SqrtPriceMath) ─────────────────

def _ceildiv(a: int, b: int) -> int:
    """Ceiling integer division (handles positive a and b)."""
    return (a + b - 1) // b


def _get_amount0_delta(sqrt_lo: int, sqrt_hi: int, liq: int) -> int:
    """Token0 amountIn for price move from sqrt_hi → sqrt_lo (zeroForOne)."""
    n = liq << 96
    d = sqrt_hi - sqrt_lo
    return _ceildiv(_ceildiv(n * d, sqrt_hi), sqrt_lo)


def _get_amount1_delta(sqrt_lo: int, sqrt_hi: int, liq: int) -> int:
    """Token1 amountIn for price move from sqrt_lo → sqrt_hi (oneForZero)."""
    d = sqrt_hi - sqrt_lo
    return _ceildiv(liq * d, Q96)


def compute_fee_growth(
    prev_sqrt: int,
    after_sqrt: int,
    liquidity: int,
    prev_fg0: int,
    prev_fg1: int,
) -> tuple[int, int]:
    """
    Compute new feeGrowthGlobal0X128 and feeGrowthGlobal1X128 from a swap.

    Uses V4 SqrtPriceMath: from the price change and liquidity, derive the
    exact amountIn, then compute fee = ceil(amountIn * feePips / (1M - feePips)).
    """
    if prev_sqrt == 0 or after_sqrt == 0 or liquidity == 0 or prev_sqrt == after_sqrt:
        return prev_fg0, prev_fg1

    zero_for_one = after_sqrt < prev_sqrt

    if zero_for_one:
        amount_in = _get_amount0_delta(after_sqrt, prev_sqrt, liquidity)
        fee_amount = _ceildiv(amount_in * POOL_FEE, 1_000_000 - POOL_FEE)
        prev_fg0 += (fee_amount * Q128) // liquidity
    else:
        amount_in = _get_amount1_delta(prev_sqrt, after_sqrt, liquidity)
        fee_amount = _ceildiv(amount_in * POOL_FEE, 1_000_000 - POOL_FEE)
        prev_fg1 += (fee_amount * Q128) // liquidity

    return prev_fg0, prev_fg1


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

    # ── Read previous state for running accumulators ──
    prev = await conn.fetchrow("""
        SELECT sqrt_price_x96, token0_balance, token1_balance,
               fee_growth_global0, fee_growth_global1, liquidity
        FROM block_states
        WHERE market_id = $1 AND sqrt_price_x96 IS NOT NULL
        ORDER BY block_number DESC LIMIT 1
    """, market_id)

    prev_sqrt = int(prev["sqrt_price_x96"]) if prev and prev["sqrt_price_x96"] else 0
    prev_t0 = int(prev["token0_balance"]) if prev and prev["token0_balance"] is not None else 0
    prev_t1 = int(prev["token1_balance"]) if prev and prev["token1_balance"] is not None else 0
    prev_fg0 = int(prev["fee_growth_global0"]) if prev and prev["fee_growth_global0"] else 0
    prev_fg1 = int(prev["fee_growth_global1"]) if prev and prev["fee_growth_global1"] else 0
    active_liq = int(prev["liquidity"]) if prev and prev["liquidity"] else liquidity

    # Token balances: negate swap amounts (V4 amounts are swapper-centric)
    # Positive event amount = swapper receives = pool LOSES
    new_t0 = prev_t0 - amount0
    new_t1 = prev_t1 - amount1

    # Fee growth: compute from price diff + liquidity (integer-precise)
    # prev_sqrt=0 means first swap after reset — skip fee growth for this swap
    new_fg0, new_fg1 = compute_fee_growth(
        prev_sqrt, sqrt_price_x96, active_liq, prev_fg0, prev_fg1
    )

    # Upsert block_states — carry forward columns this handler doesn't natively set
    await conn.execute(f"""
        INSERT INTO block_states
          (market_id, block_number, block_timestamp,
           sqrt_price_x96, tick, mark_price, liquidity,
           token0_balance, token1_balance,
           fee_growth_global0, fee_growth_global1,
           index_price, normalization_factor, total_debt)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                {_cf('index_price')},
                {_cf('normalization_factor')},
                {_cf('total_debt')})
        ON CONFLICT (market_id, block_number) DO UPDATE SET
          sqrt_price_x96      = EXCLUDED.sqrt_price_x96,
          tick                = EXCLUDED.tick,
          mark_price          = EXCLUDED.mark_price,
          liquidity           = EXCLUDED.liquidity,
          token0_balance      = EXCLUDED.token0_balance,
          token1_balance      = EXCLUDED.token1_balance,
          fee_growth_global0  = EXCLUDED.fee_growth_global0,
          fee_growth_global1  = EXCLUDED.fee_growth_global1,
          index_price         = COALESCE(block_states.index_price,         EXCLUDED.index_price),
          normalization_factor = COALESCE(block_states.normalization_factor, EXCLUDED.normalization_factor),
          total_debt          = COALESCE(block_states.total_debt,          EXCLUDED.total_debt)
    """, market_id, block_number, block_timestamp,
         str(sqrt_price_x96), tick, mark_price, str(liquidity),
         new_t0, new_t1, str(new_fg0), str(new_fg1))

    # ── Per-block swap volume accumulation ──
    await conn.execute("""
        UPDATE block_states
        SET swap_volume = COALESCE(swap_volume, 0) + $1,
            swap_count  = COALESCE(swap_count, 0) + 1
        WHERE market_id = $2 AND block_number = $3
    """, volume_usd, market_id, block_number)

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

    log.debug("[pool] Swap market=%s block=%d tick=%d price=%.6f vol=%.2f t0=%d t1=%d",
              market_id, block_number, tick, mark_price, volume_usd, new_t0, new_t1)


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
    w3=None,
    pool_manager: str = "",
) -> None:
    # ── Seed token balances via RPC if not yet initialized ─────────────
    # On ModifyLiquidity (LP add/remove), token balances change by amounts
    # that can't be precisely computed from float math. Read from RPC instead.
    seed_t0 = None
    seed_t1 = None

    if w3 and pool_manager:
        # Always re-read balances on ModifyLiquidity — LP events change
        # reserves in ways that can't be precisely tracked from event data alone.
        # Read at the EXACT block being indexed to avoid future-swap contamination.
        try:
            # Sort tokens to identify token0/token1
            token0_addr = min(wausdc.lower(), wrlp.lower())
            token1_addr = max(wausdc.lower(), wrlp.lower())
            erc20_abi = [{"inputs":[{"name":"","type":"address"}],"name":"balanceOf",
                          "outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]
            t0_contract = w3.eth.contract(address=w3.to_checksum_address(token0_addr), abi=erc20_abi)
            t1_contract = w3.eth.contract(address=w3.to_checksum_address(token1_addr), abi=erc20_abi)
            pm_addr = w3.to_checksum_address(pool_manager)
            seed_t0 = t0_contract.functions.balanceOf(pm_addr).call(
                block_identifier=block_number
            )
            seed_t1 = t1_contract.functions.balanceOf(pm_addr).call(
                block_identifier=block_number
            )
            log.info("[pool] ModifyLiquidity: reset balances at block=%d t0=%d t1=%d",
                     block_number, seed_t0, seed_t1)
        except Exception as e:
            log.warning("[pool] Failed to read balances via RPC: %s", e)

    # Build token0/1 values: use RPC seed if available, else carry-forward
    t0_val = str(seed_t0) if seed_t0 is not None else _cf('token0_balance')
    t1_val = str(seed_t1) if seed_t1 is not None else _cf('token1_balance')

    # Update block_states liquidity snapshot — carry forward all columns
    if seed_t0 is not None:
        # We have actual values — use parameterized insert
        await conn.execute(f"""
            INSERT INTO block_states
              (market_id, block_number, block_timestamp,
               tick, mark_price,
               index_price, sqrt_price_x96, liquidity,
               normalization_factor, total_debt,
               token0_balance, token1_balance,
               fee_growth_global0, fee_growth_global1)
            VALUES ($1, $2, $3, $4, $5,
                    {_cf('index_price')},
                    {_cf('sqrt_price_x96')},
                    {_cf('liquidity')},
                    {_cf('normalization_factor')},
                    {_cf('total_debt')},
                    $6, $7,
                    '0', '0')
            ON CONFLICT (market_id, block_number) DO UPDATE SET
              tick                = COALESCE(block_states.tick,                EXCLUDED.tick),
              mark_price          = COALESCE(block_states.mark_price,          EXCLUDED.mark_price),
              index_price         = COALESCE(block_states.index_price,         EXCLUDED.index_price),
              sqrt_price_x96      = COALESCE(block_states.sqrt_price_x96,      EXCLUDED.sqrt_price_x96),
              liquidity           = COALESCE(block_states.liquidity,           EXCLUDED.liquidity),
              normalization_factor = COALESCE(block_states.normalization_factor, EXCLUDED.normalization_factor),
              total_debt          = COALESCE(block_states.total_debt,          EXCLUDED.total_debt),
              token0_balance      = EXCLUDED.token0_balance,
              token1_balance      = EXCLUDED.token1_balance,
              fee_growth_global0  = COALESCE(block_states.fee_growth_global0,  EXCLUDED.fee_growth_global0),
              fee_growth_global1  = COALESCE(block_states.fee_growth_global1,  EXCLUDED.fee_growth_global1)
        """, market_id, block_number, block_timestamp,
             None, sqrt_price_x96_to_price(sqrt_price_x96, wausdc, wrlp) if sqrt_price_x96 else None,
             seed_t0, seed_t1)
    else:
        await conn.execute(f"""
            INSERT INTO block_states
              (market_id, block_number, block_timestamp,
               tick, mark_price,
               index_price, sqrt_price_x96, liquidity,
               normalization_factor, total_debt,
               token0_balance, token1_balance,
               fee_growth_global0, fee_growth_global1)
            VALUES ($1, $2, $3, $4, $5,
                    {_cf('index_price')},
                    {_cf('sqrt_price_x96')},
                    {_cf('liquidity')},
                    {_cf('normalization_factor')},
                    {_cf('total_debt')},
                    {_cf('token0_balance')},
                    {_cf('token1_balance')},
                    {_cf('fee_growth_global0')},
                    {_cf('fee_growth_global1')})
            ON CONFLICT (market_id, block_number) DO UPDATE SET
              tick                = COALESCE(block_states.tick,                EXCLUDED.tick),
              mark_price          = COALESCE(block_states.mark_price,          EXCLUDED.mark_price),
              index_price         = COALESCE(block_states.index_price,         EXCLUDED.index_price),
              sqrt_price_x96      = COALESCE(block_states.sqrt_price_x96,      EXCLUDED.sqrt_price_x96),
              liquidity           = COALESCE(block_states.liquidity,           EXCLUDED.liquidity),
              normalization_factor = COALESCE(block_states.normalization_factor, EXCLUDED.normalization_factor),
              total_debt          = COALESCE(block_states.total_debt,          EXCLUDED.total_debt),
              token0_balance      = COALESCE(block_states.token0_balance,      EXCLUDED.token0_balance),
              token1_balance      = COALESCE(block_states.token1_balance,      EXCLUDED.token1_balance),
              fee_growth_global0  = COALESCE(block_states.fee_growth_global0,  EXCLUDED.fee_growth_global0),
              fee_growth_global1  = COALESCE(block_states.fee_growth_global1,  EXCLUDED.fee_growth_global1)
        """, market_id, block_number, block_timestamp,
             None, sqrt_price_x96_to_price(sqrt_price_x96, wausdc, wrlp) if sqrt_price_x96 else None)

    # ── Track tick_liquidity_net deltas + rebuild bins ──
    await _update_tick_liquidity(
        conn, market_id, tick_lower, tick_upper, liquidity_delta
    )

    log.debug("[pool] ModifyLiquidity market=%s block=%d delta=%d [%d, %d]",
              market_id, block_number, liquidity_delta, tick_lower, tick_upper)


# ── Tick liquidity tracking + bin materialization ──────────────────────────

async def _update_tick_liquidity(
    conn: asyncpg.Connection,
    market_id: str,
    tick_lower: int,
    tick_upper: int,
    liquidity_delta: int,
) -> None:
    """Update tick_liquidity_net and rebuild materialized liquidity_bins."""
    # Get pool_id for this market
    row = await conn.fetchrow(
        "SELECT pool_id, tick_spacing FROM markets WHERE market_id = $1", market_id
    )
    if not row:
        return
    pool_id = row["pool_id"]
    tick_spacing = row["tick_spacing"]

    # +delta at tickLower, -delta at tickUpper
    await conn.execute("""
        INSERT INTO tick_liquidity_net (pool_id, tick, net_delta)
        VALUES ($1, $2, $3)
        ON CONFLICT (pool_id, tick) DO UPDATE SET
            net_delta = tick_liquidity_net.net_delta + EXCLUDED.net_delta
    """, pool_id, tick_lower, liquidity_delta)

    await conn.execute("""
        INSERT INTO tick_liquidity_net (pool_id, tick, net_delta)
        VALUES ($1, $2, $3)
        ON CONFLICT (pool_id, tick) DO UPDATE SET
            net_delta = tick_liquidity_net.net_delta + EXCLUDED.net_delta
    """, pool_id, tick_upper, -liquidity_delta)

    # Rebuild bins from tick data
    bins = await _build_liquidity_bins(conn, pool_id, tick_spacing)
    await conn.execute(
        "UPDATE markets SET liquidity_bins = $1 WHERE market_id = $2",
        json.dumps(bins), market_id
    )
    log.debug("[pool] Rebuilt %d liquidity bins for %s", len(bins), pool_id[:16])


async def _build_liquidity_bins(
    conn: asyncpg.Connection,
    pool_id: str,
    tick_spacing: int,
    num_bins: int = 50,
) -> list[dict]:
    """Walk tick_liquidity_net to build binned distribution."""
    rows = await conn.fetch("""
        SELECT tick, net_delta FROM tick_liquidity_net
        WHERE pool_id = $1 ORDER BY tick
    """, pool_id)
    if not rows:
        return []

    ticks = [(int(r["tick"]), float(r["net_delta"])) for r in rows]
    min_tick = ticks[0][0]
    max_tick = ticks[-1][0]
    if min_tick == max_tick:
        return [{"tickLow": min_tick, "tickHigh": max_tick, "liquidity": ticks[0][1]}]

    # Walk ticks, accumulate liquidity
    tick_liq = []
    cumulative = 0.0
    for tick, delta in ticks:
        cumulative += delta
        tick_liq.append((tick, cumulative))

    # Bin into buckets
    tick_range = max_tick - min_tick
    bin_width = max(tick_range // num_bins, tick_spacing)

    bins = []
    for i in range(num_bins):
        bin_low = min_tick + i * bin_width
        bin_high = bin_low + bin_width

        # Find liquidity at this bin (last entry <= bin_low)
        liq_at_bin = 0.0
        for tick, liq in tick_liq:
            if tick <= bin_low:
                liq_at_bin = liq
            else:
                break

        bins.append({
            "tickLow": bin_low,
            "tickHigh": bin_high,
            "priceLow": round(1.0001 ** bin_low, 6),
            "priceHigh": round(1.0001 ** bin_high, 6),
            "liquidity": liq_at_bin,
        })
        if bin_high >= max_tick:
            break

    return bins
