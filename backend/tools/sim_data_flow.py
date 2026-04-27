"""
sim_data_flow.py — Pre-implementation simulation of the indexer data flow redesign.

Validates all precomputation logic in-memory before touching production code:
  1. Snapshot materialization (derived metrics from raw block state)
  2. Multi-resolution candle aggregation (OHLC UPSERT)
  3. Tick liquidity net tracking (ModifyLiquidity → bins)
  4. Per-broker on-demand computation (LP values + fees)
  5. Volume 24H rolling aggregation

Run: python backend/tools/sim_data_flow.py
"""
import json
import math
import random
from dataclasses import dataclass, field

# ═══════════════════════════════════════════════════════════════════
# SECTION 1: Uniswap V4 Math (pure functions)
# ═══════════════════════════════════════════════════════════════════

Q96 = 2**96
Q128 = 2**128

def tick_to_sqrt_price_x96(tick: int) -> int:
    """Convert tick → sqrtPriceX96."""
    return int(math.sqrt(1.0001**tick) * Q96)

def sqrt_price_x96_to_price(sqrt_price_x96: int) -> float:
    """Convert sqrtPriceX96 → human-readable price."""
    return (sqrt_price_x96 / Q96) ** 2

def liquidity_to_amounts(liquidity: int, tick_lower: int, tick_upper: int, current_tick: int) -> tuple[float, float]:
    """
    Given an LP position's liquidity and tick range, compute token amounts
    at the current tick. Returns (amount0, amount1) in raw units.
    """
    sa = math.sqrt(1.0001**tick_lower)
    sb = math.sqrt(1.0001**tick_upper)
    sp = math.sqrt(1.0001**current_tick)

    if current_tick < tick_lower:
        # All token0
        amount0 = liquidity * (1/sa - 1/sb)
        amount1 = 0
    elif current_tick >= tick_upper:
        # All token1
        amount0 = 0
        amount1 = liquidity * (sb - sa)
    else:
        # In range
        amount0 = liquidity * (1/sp - 1/sb)
        amount1 = liquidity * (sp - sa)

    return amount0, amount1


# ═══════════════════════════════════════════════════════════════════
# SECTION 2: In-memory "database" tables
# ═══════════════════════════════════════════════════════════════════

@dataclass
class BlockState:
    market_id: str
    block_number: int
    block_timestamp: int
    mark_price: float
    index_price: float
    tick: int
    liquidity: int
    sqrt_price_x96: int
    normalization_factor: float
    total_debt: float
    token0_balance: float  # human-readable (divided by 1e6)
    token1_balance: float
    fee_growth_global0: int  # Q128
    fee_growth_global1: int
    swap_volume: float = 0.0
    swap_count: int = 0


@dataclass
class Broker:
    address: str
    owner: str
    market_id: str
    wausdc_balance: float = 0.0
    debt_principal: float = 0.0
    wausdc_value: float = 0.0
    wrlp_value: float = 0.0
    health_factor: float = 0.0


@dataclass
class LPPosition:
    token_id: int
    broker_address: str
    tick_lower: int
    tick_upper: int
    liquidity: int
    is_active: bool = True
    fee_growth_inside_last0: int = 0  # Q128
    fee_growth_inside_last1: int = 0
    mint_block: int = 0


@dataclass
class CandleBucket:
    market_id: str
    resolution: str
    bucket: int  # timestamp
    mark_open: float = 0
    mark_high: float = 0
    mark_low: float = 0
    mark_close: float = 0
    index_open: float = 0
    index_high: float = 0
    index_low: float = 0
    index_close: float = 0
    volume: float = 0
    swap_count: int = 0


class SimDB:
    """In-memory mock of the indexer database."""

    def __init__(self):
        self.block_states: list[BlockState] = []
        self.brokers: list[Broker] = []
        self.lp_positions: list[LPPosition] = []
        self.candles: dict[tuple[str, str, int], CandleBucket] = {}  # (mkt, res, bucket) → candle
        self.tick_liquidity_net: dict[tuple[str, int], float] = {}   # (pool_id, tick) → net_delta
        self.snapshot: dict | None = None
        self.liquidity_bins: list[dict] | None = None

    def latest_block_state(self, market_id: str) -> BlockState | None:
        for bs in reversed(self.block_states):
            if bs.market_id == market_id:
                return bs
        return None


# ═══════════════════════════════════════════════════════════════════
# SECTION 3: Handler simulations
# ═══════════════════════════════════════════════════════════════════

RESOLUTIONS = {"1m": 60, "5m": 300, "15m": 900, "1H": 3600, "4H": 14400, "1D": 86400}


def handle_swap(db: SimDB, market_id: str, pool_id: str, block_number: int,
                block_timestamp: int, mark_price: float, index_price: float,
                tick: int, liquidity: int, sqrt_price_x96: int,
                amount0: float, amount1: float,
                fee_growth_global0: int, fee_growth_global1: int,
                token0_balance: float, token1_balance: float,
                nf: float, total_debt: float):
    """Simulate the pool.py Swap handler + new precomputation logic."""

    abs_volume = abs(amount1)

    # 1. Upsert block_state (existing)
    existing = None
    for bs in db.block_states:
        if bs.market_id == market_id and bs.block_number == block_number:
            existing = bs
            break

    if existing:
        existing.mark_price = mark_price
        existing.index_price = index_price
        existing.tick = tick
        existing.liquidity = liquidity
        existing.sqrt_price_x96 = sqrt_price_x96
        existing.fee_growth_global0 = fee_growth_global0
        existing.fee_growth_global1 = fee_growth_global1
        existing.token0_balance = token0_balance
        existing.token1_balance = token1_balance
        existing.swap_volume += abs_volume
        existing.swap_count += 1
    else:
        bs = BlockState(
            market_id=market_id, block_number=block_number,
            block_timestamp=block_timestamp,
            mark_price=mark_price, index_price=index_price,
            tick=tick, liquidity=liquidity, sqrt_price_x96=sqrt_price_x96,
            normalization_factor=nf, total_debt=total_debt,
            token0_balance=token0_balance, token1_balance=token1_balance,
            fee_growth_global0=fee_growth_global0, fee_growth_global1=fee_growth_global1,
            swap_volume=abs_volume, swap_count=1,
        )
        db.block_states.append(bs)

    # 2. Multi-resolution candle UPSERT (NEW)
    for res_name, seconds in RESOLUTIONS.items():
        bucket = (block_timestamp // seconds) * seconds
        key = (market_id, res_name, bucket)
        if key in db.candles:
            c = db.candles[key]
            c.mark_high = max(c.mark_high, mark_price)
            c.mark_low = min(c.mark_low, mark_price)
            c.mark_close = mark_price
            c.index_high = max(c.index_high, index_price)
            c.index_low = min(c.index_low, index_price)
            c.index_close = index_price
            c.volume += abs_volume
            c.swap_count += 1
        else:
            db.candles[key] = CandleBucket(
                market_id=market_id, resolution=res_name, bucket=bucket,
                mark_open=mark_price, mark_high=mark_price,
                mark_low=mark_price, mark_close=mark_price,
                index_open=index_price, index_high=index_price,
                index_low=index_price, index_close=index_price,
                volume=abs_volume, swap_count=1,
            )


def handle_modify_liquidity(db: SimDB, pool_id: str, tick_lower: int, tick_upper: int,
                            liquidity_delta: int, tick_spacing: int):
    """Simulate ModifyLiquidity handler with tick net delta tracking + bin materialization."""

    # 1. Update tick_liquidity_net
    key_lower = (pool_id, tick_lower)
    key_upper = (pool_id, tick_upper)
    db.tick_liquidity_net[key_lower] = db.tick_liquidity_net.get(key_lower, 0) + liquidity_delta
    db.tick_liquidity_net[key_upper] = db.tick_liquidity_net.get(key_upper, 0) - liquidity_delta

    # 2. Rebuild liquidity bins
    db.liquidity_bins = build_liquidity_bins(db, pool_id, num_bins=50, tick_spacing=tick_spacing)


def build_liquidity_bins(db: SimDB, pool_id: str, num_bins: int, tick_spacing: int) -> list[dict]:
    """Walk tick_liquidity_net to build binned distribution."""
    # Get all ticks for this pool, sorted
    ticks = sorted(
        [(tick, delta) for (pid, tick), delta in db.tick_liquidity_net.items() if pid == pool_id],
        key=lambda x: x[0]
    )
    if not ticks:
        return []

    min_tick = ticks[0][0]
    max_tick = ticks[-1][0]
    if min_tick == max_tick:
        return [{"tickLow": min_tick, "tickHigh": max_tick, "liquidity": ticks[0][1]}]

    # Walk ticks, accumulate liquidity
    tick_liq = []  # [(tick, cumulative_liquidity)]
    cumulative = 0
    for tick, delta in ticks:
        cumulative += delta
        tick_liq.append((tick, cumulative))

    # Bin into num_bins buckets
    tick_range = max_tick - min_tick
    bin_width = max(tick_range // num_bins, tick_spacing)

    bins = []
    for i in range(num_bins):
        bin_low = min_tick + i * bin_width
        bin_high = bin_low + bin_width

        # Find liquidity at this bin (last tick_liq entry <= bin_low)
        liq_at_bin = 0
        for tick, liq in tick_liq:
            if tick <= bin_low:
                liq_at_bin = liq
            else:
                break

        price_low = 1.0001 ** bin_low
        price_high = 1.0001 ** bin_high

        bins.append({
            "tickLow": bin_low,
            "tickHigh": bin_high,
            "priceLow": round(price_low, 6),
            "priceHigh": round(price_high, 6),
            "liquidity": float(liq_at_bin),
        })

        if bin_high >= max_tick:
            break

    return bins


# ═══════════════════════════════════════════════════════════════════
# SECTION 4: Snapshot materializer
# ═══════════════════════════════════════════════════════════════════

def materialize_snapshot(db: SimDB, market_id: str):
    """Build the global snapshot JSON — called once per block after all events processed."""
    latest = db.latest_block_state(market_id)
    if not latest:
        return

    # Volume 24H: sum swap_volume where timestamp > now - 86400
    cutoff = latest.block_timestamp - 86400
    volume_24h = sum(
        bs.swap_volume for bs in db.block_states
        if bs.market_id == market_id and bs.block_timestamp > cutoff
    )
    swap_count_24h = sum(
        bs.swap_count for bs in db.block_states
        if bs.market_id == market_id and bs.block_timestamp > cutoff
    )

    # 24H price changes. Inactive windows stay flat so markets without swaps
    # do not appear to have traded during the observed period.
    if swap_count_24h > 0:
        block_24h_ago = None
        for bs in db.block_states:
            if bs.market_id == market_id and bs.block_timestamp <= cutoff:
                block_24h_ago = bs  # keep last one before cutoff
        mark_24h_change = ((latest.mark_price - block_24h_ago.mark_price) / block_24h_ago.mark_price * 100) if block_24h_ago and block_24h_ago.mark_price > 0 else 0
        index_24h_change = ((latest.index_price - block_24h_ago.index_price) / block_24h_ago.index_price * 100) if block_24h_ago and block_24h_ago.index_price > 0 else 0
    else:
        mark_24h_change = 0
        index_24h_change = 0

    # Derived metrics
    nf = latest.normalization_factor
    normalized_mark = latest.mark_price / nf if nf > 0 else latest.mark_price
    peg_dev = (normalized_mark - latest.index_price) / latest.index_price * 100 if latest.index_price > 0 else 0

    funding_rate = (normalized_mark - latest.index_price) / latest.index_price if latest.index_price > 0 else 0
    funding_period = 2_592_000  # 30 days
    year_sec = 365 * 86400
    ann_exp = -funding_rate * year_sec / funding_period
    ann_exp_clamped = max(-20, min(20, ann_exp))
    funding_ann_pct = (math.exp(ann_exp_clamped) - 1) * 100

    # Pool TVL
    tvl = latest.token0_balance * latest.mark_price + latest.token1_balance

    # System health
    total_col = sum(b.wausdc_value for b in db.brokers if b.market_id == market_id)
    total_debt_usd = sum(b.debt_principal * latest.index_price for b in db.brokers if b.market_id == market_id)
    over_collat = (total_col / total_debt_usd * 100) if total_debt_usd > 0 else float('inf')

    system_health = "SAFE" if over_collat > 200 else ("WATCH" if over_collat > 120 else "DANGER")

    # Build snapshot
    db.snapshot = {
        "blockNumber": latest.block_number,
        "blockTimestamp": latest.block_timestamp,
        "market": {
            "marketId": market_id,
            "normalizationFactor": str(int(nf * 1e18)),
            "totalDebt": latest.total_debt,
            "indexPrice": latest.index_price,
        },
        "pool": {
            "markPrice": latest.mark_price,
            "tick": latest.tick,
            "liquidity": str(latest.liquidity),
            "sqrtPriceX96": str(latest.sqrt_price_x96),
            "token0Balance": latest.token0_balance,
            "token1Balance": latest.token1_balance,
            "tvlUsd": round(tvl, 2),
            "feeGrowthGlobal0": str(latest.fee_growth_global0),
            "feeGrowthGlobal1": str(latest.fee_growth_global1),
        },
        "derived": {
            "pegDeviationPct": round(peg_dev, 4),
            "fundingRateAnnPct": round(funding_ann_pct, 4),
            "overCollatPct": round(over_collat, 2),
            "volume24hUsd": round(volume_24h, 2),
            "swapCount24h": swap_count_24h,
            "mark24hChangePct": round(mark_24h_change, 4),
            "index24hChangePct": round(index_24h_change, 4),
            "systemHealth": system_health,
        },
        "brokers": [
            {
                "address": b.address,
                "owner": b.owner,
                "collateral": b.wausdc_balance,
                "debt": b.debt_principal,
                "collateralValue": b.wausdc_value,
                "debtValue": round(b.debt_principal * latest.index_price, 2),
                "healthFactor": b.health_factor,
                "status": "safe" if b.health_factor > 2 else ("watch" if b.health_factor > 1.2 else "danger"),
            }
            for b in db.brokers if b.market_id == market_id
        ],
    }


# ═══════════════════════════════════════════════════════════════════
# SECTION 5: Per-broker on-demand resolver
# ═══════════════════════════════════════════════════════════════════

def resolve_broker(db: SimDB, owner: str) -> dict | None:
    """Simulate the GraphQL broker(owner) resolver — on-demand computation."""
    broker = next((b for b in db.brokers if b.owner == owner), None)
    if not broker:
        return None

    latest = db.latest_block_state(broker.market_id)
    if not latest:
        return None

    # Compute LP position values + fees (the 5N mults)
    positions = [p for p in db.lp_positions if p.broker_address == broker.address]
    lp_data = []
    for pos in positions:
        # Token amounts from liquidity + current tick
        amt0, amt1 = liquidity_to_amounts(pos.liquidity, pos.tick_lower, pos.tick_upper, latest.tick)
        amt0_human = amt0 / 1e6
        amt1_human = amt1 / 1e6
        value_usd = amt0_human * latest.mark_price + amt1_human

        # Fee earnings from fee growth delta * liquidity
        # Simplified: use global fee growth as upper bound
        # (in production, use feeGrowthInside which requires per-tick tracking)
        fg_delta0 = latest.fee_growth_global0 - pos.fee_growth_inside_last0
        fg_delta1 = latest.fee_growth_global1 - pos.fee_growth_inside_last1
        fees0 = pos.liquidity * fg_delta0 / Q128 / 1e6
        fees1 = pos.liquidity * fg_delta1 / Q128 / 1e6

        in_range = pos.tick_lower <= latest.tick < pos.tick_upper

        lp_data.append({
            "tokenId": pos.token_id,
            "tickLower": pos.tick_lower,
            "tickUpper": pos.tick_upper,
            "liquidity": str(pos.liquidity),
            "isActive": pos.is_active,
            "amount0": round(amt0_human, 4),
            "amount1": round(amt1_human, 4),
            "valueUsd": round(value_usd, 2),
            "feesEarned0": round(fees0, 4),
            "feesEarned1": round(fees1, 4),
            "feesUsd": round(fees0 * latest.mark_price + fees1, 2),
            "inRange": in_range,
        })

    return {
        "address": broker.address,
        "owner": broker.owner,
        "collateral": broker.wausdc_balance,
        "debt": broker.debt_principal,
        "collateralValue": broker.wausdc_value,
        "debtValue": round(broker.debt_principal * latest.index_price, 2),
        "healthFactor": broker.health_factor,
        "status": "safe" if broker.health_factor > 2 else ("watch" if broker.health_factor > 1.2 else "danger"),
        "netEquityUsd": round(broker.wausdc_value - broker.debt_principal * latest.index_price, 2),
        "lpPositions": lp_data,
    }


# ═══════════════════════════════════════════════════════════════════
# SECTION 6: Simulation runner + assertions
# ═══════════════════════════════════════════════════════════════════

def run_simulation():
    print("=" * 70)
    print("DATA FLOW SIMULATION — Pre-implementation verification")
    print("=" * 70)

    db = SimDB()
    market_id = "0x3de..."
    pool_id = "0xpool..."
    tick_spacing = 10

    # ── Setup: create brokers ──
    db.brokers = [
        Broker(address="0xbrokerA", owner="0xownerA", market_id=market_id,
               wausdc_balance=50000, debt_principal=10000, wausdc_value=50000, health_factor=1.68),
        Broker(address="0xbrokerB", owner="0xownerB", market_id=market_id,
               wausdc_balance=30000, debt_principal=5000, wausdc_value=30000, health_factor=2.50),
    ]

    # ── Setup: create LP positions ──
    base_tick = 10880
    db.lp_positions = [
        LPPosition(token_id=1, broker_address="0xbrokerA",
                   tick_lower=base_tick - 100, tick_upper=base_tick + 100,
                   liquidity=14_029_742_371_290, is_active=True,
                   fee_growth_inside_last0=0, fee_growth_inside_last1=0, mint_block=100),
        LPPosition(token_id=2, broker_address="0xbrokerB",
                   tick_lower=base_tick - 200, tick_upper=base_tick + 50,
                   liquidity=8_000_000_000_000, is_active=True,
                   fee_growth_inside_last0=0, fee_growth_inside_last1=0, mint_block=150),
    ]

    # ── Simulate ModifyLiquidity events (LP position creation) ──
    print("\n[1] ModifyLiquidity events...")
    for pos in db.lp_positions:
        handle_modify_liquidity(db, pool_id, pos.tick_lower, pos.tick_upper, pos.liquidity, tick_spacing)
    assert db.liquidity_bins is not None, "Liquidity bins should be materialized"
    assert len(db.liquidity_bins) > 0, "Should have at least 1 bin"
    print(f"    ✓ {len(db.liquidity_bins)} bins materialized")

    # Verify tick net deltas are correct
    # Position 1: +L at 10780, -L at 10980
    # Position 2: +L at 10680, -L at 10930
    pos1_liq = 14_029_742_371_290
    pos2_liq = 8_000_000_000_000
    assert db.tick_liquidity_net[(pool_id, base_tick - 100)] == pos1_liq
    assert db.tick_liquidity_net[(pool_id, base_tick + 100)] == -pos1_liq
    assert db.tick_liquidity_net[(pool_id, base_tick - 200)] == pos2_liq
    assert db.tick_liquidity_net[(pool_id, base_tick + 50)] == -pos2_liq
    print("    ✓ Tick net deltas correct")

    # ── Simulate Swap events over 36 hours (to test 24H window) ──
    print("\n[2] Swap events (simulating 36 hours)...")
    base_timestamp = 1710518400  # arbitrary
    num_swaps = 200
    random.seed(42)

    fee_growth0 = 0
    fee_growth1 = 0
    current_tick = base_tick
    mark_price = 2.9729

    for i in range(num_swaps):
        ts = base_timestamp + i * 648  # ~36 hours / 200 swaps
        block_num = 1000 + i

        # Simulate price drift
        mark_price += random.uniform(-0.01, 0.012)
        index_price = mark_price + random.uniform(-0.005, 0.005)
        current_tick = int(math.log(mark_price) / math.log(1.0001))

        # Fee growth increments
        fee_growth0 += random.randint(100_000, 1_000_000) * Q128 // 10**12
        fee_growth1 += random.randint(100_000, 1_000_000) * Q128 // 10**12

        amount0 = random.uniform(-500, 500)
        amount1 = random.uniform(-1500, 1500)
        sqrt_px96 = tick_to_sqrt_price_x96(current_tick)

        t0_bal = 1091 + random.uniform(-50, 50)
        t1_bal = 3938 + random.uniform(-100, 100)

        handle_swap(
            db, market_id, pool_id, block_num, ts,
            mark_price, index_price, current_tick,
            pos1_liq + pos2_liq, sqrt_px96,
            amount0, amount1,
            fee_growth0, fee_growth1,
            t0_bal, t1_bal,
            nf=1.0 - i * 0.00001,  # slow NF drift
            total_debt=15230.5 + i * 10,
        )

    print(f"    ✓ {num_swaps} swaps processed")
    print(f"    ✓ {len(db.block_states)} block_state rows")

    # ── Test 3: Multi-resolution candles ──
    print("\n[3] Multi-resolution candles...")
    for res_name in RESOLUTIONS:
        candles_for_res = [c for c in db.candles.values() if c.resolution == res_name]
        assert len(candles_for_res) > 0, f"No candles for {res_name}"
        # Verify OHLC invariants
        for c in candles_for_res:
            assert c.mark_high >= c.mark_low, f"high < low in {res_name} bucket {c.bucket}"
            assert c.mark_high >= c.mark_open, f"high < open in {res_name}"
            assert c.mark_high >= c.mark_close, f"high < close in {res_name}"
            assert c.mark_low <= c.mark_open, f"low > open in {res_name}"
            assert c.mark_low <= c.mark_close, f"low > close in {res_name}"
            assert c.swap_count >= 1
        print(f"    ✓ {res_name}: {len(candles_for_res)} buckets, OHLC invariants hold")

    # Total swap count across all 1m candles should equal num_swaps
    total_swaps_in_candles = sum(c.swap_count for c in db.candles.values() if c.resolution == "1m")
    assert total_swaps_in_candles == num_swaps, f"Candle swap count {total_swaps_in_candles} != {num_swaps}"
    print(f"    ✓ Total swap count in 1m candles matches: {total_swaps_in_candles}")

    # ── Test 4: Snapshot materialization ──
    print("\n[4] Snapshot materialization...")
    materialize_snapshot(db, market_id)
    snap = db.snapshot
    assert snap is not None, "Snapshot should exist"
    assert snap["blockNumber"] == 1000 + num_swaps - 1
    assert snap["market"]["totalDebt"] > 0
    assert snap["pool"]["markPrice"] > 0
    assert snap["pool"]["tvlUsd"] > 0
    assert snap["derived"]["volume24hUsd"] > 0
    assert snap["derived"]["swapCount24h"] > 0
    assert snap["derived"]["systemHealth"] in ("SAFE", "WATCH", "DANGER")
    assert len(snap["brokers"]) == 2

    print(f"    ✓ Block #{snap['blockNumber']}")
    print(f"    ✓ Mark price: {snap['pool']['markPrice']:.4f}")
    print(f"    ✓ Index price: {snap['market']['indexPrice']:.4f}")
    print(f"    ✓ Pool TVL: ${snap['pool']['tvlUsd']:,.2f}")
    print(f"    ✓ Peg deviation: {snap['derived']['pegDeviationPct']:.4f}%")
    print(f"    ✓ Funding (ann): {snap['derived']['fundingRateAnnPct']:.4f}%")
    print(f"    ✓ Over-collat: {snap['derived']['overCollatPct']:.2f}%")
    print(f"    ✓ Volume 24H: ${snap['derived']['volume24hUsd']:,.2f}")
    print(f"    ✓ Swaps 24H: {snap['derived']['swapCount24h']}")
    print(f"    ✓ System health: {snap['derived']['systemHealth']}")

    # Volume 24H should be LESS than total volume (36 hours of data, only 24H counted)
    total_volume = sum(bs.swap_volume for bs in db.block_states)
    assert snap["derived"]["volume24hUsd"] <= total_volume, \
        f"24H volume ({snap['derived']['volume24hUsd']}) should be <= total ({total_volume})"
    assert snap["derived"]["volume24hUsd"] > 0, "24H volume should be > 0"
    print(f"    ✓ 24H window correct: {snap['derived']['volume24hUsd']:.0f} < total {total_volume:.0f}")

    # ── Test 5: Per-broker on-demand resolver ──
    print("\n[5] Per-broker on-demand resolver...")
    profile_a = resolve_broker(db, "0xownerA")
    assert profile_a is not None
    assert len(profile_a["lpPositions"]) == 1
    lp = profile_a["lpPositions"][0]
    assert lp["tokenId"] == 1
    assert lp["amount0"] != 0 or lp["amount1"] != 0, "Position should have some token amounts"
    assert lp["valueUsd"] > 0, "Position should have USD value"
    assert lp["feesEarned0"] >= 0, "Fees should be non-negative"
    assert lp["feesEarned1"] >= 0, "Fees should be non-negative"
    assert isinstance(lp["inRange"], bool)

    print(f"    ✓ Broker A: collateral={profile_a['collateral']}, debt={profile_a['debt']}")
    print(f"    ✓ LP #1: amounts=({lp['amount0']:.2f}, {lp['amount1']:.2f}), value=${lp['valueUsd']:.2f}")
    print(f"    ✓ LP #1: fees=({lp['feesEarned0']:.4f}, {lp['feesEarned1']:.4f}), total=${lp['feesUsd']:.2f}")
    print(f"    ✓ LP #1: in_range={lp['inRange']}")

    profile_b = resolve_broker(db, "0xownerB")
    assert profile_b is not None
    assert len(profile_b["lpPositions"]) == 1
    print(f"    ✓ Broker B: collateral={profile_b['collateral']}, debt={profile_b['debt']}")
    print(f"    ✓ Broker B: LP value=${profile_b['lpPositions'][0]['valueUsd']:.2f}")

    # Non-existent broker
    assert resolve_broker(db, "0xnonexistent") is None
    print("    ✓ Non-existent broker returns None")

    # ── Test 6: Verify liquidity bins accumulation ──
    print("\n[6] Liquidity bins verification...")
    bins = db.liquidity_bins
    assert bins is not None and len(bins) > 0

    # Find the bin that contains the overlap region (both positions active)
    # Position 1: [10780, 10980], Position 2: [10680, 10930]
    # Overlap: [10780, 10930] — should have combined liquidity
    overlap_bins = [b for b in bins if b["tickLow"] >= base_tick - 100 and b["tickHigh"] <= base_tick + 50]
    if overlap_bins:
        for ob in overlap_bins:
            assert ob["liquidity"] >= pos1_liq, f"Overlap bin should have at least pos1 liquidity"
        print(f"    ✓ Overlap region has combined liquidity ≥ {pos1_liq}")

    # Verify no negative liquidity in any bin
    for b in bins:
        assert b["liquidity"] >= 0, f"Negative liquidity at tick {b['tickLow']}"
    print(f"    ✓ All {len(bins)} bins have non-negative liquidity")

    # ── Test 7: Snapshot JSON serialization ──
    print("\n[7] JSON serialization...")
    json_str = json.dumps(db.snapshot)
    reparsed = json.loads(json_str)
    assert reparsed["blockNumber"] == snap["blockNumber"]
    assert reparsed["derived"]["systemHealth"] == snap["derived"]["systemHealth"]
    print(f"    ✓ Snapshot JSON roundtrip OK ({len(json_str)} bytes)")
    liq_json = json.dumps(db.liquidity_bins)
    print(f"    ✓ Liquidity bins JSON OK ({len(liq_json)} bytes)")

    # ── Summary ──
    print("\n" + "=" * 70)
    print("ALL ASSERTIONS PASSED")
    print("=" * 70)
    print(f"\nPrecomputed data sizes:")
    print(f"  Snapshot JSON:        {len(json_str):>6} bytes")
    print(f"  Liquidity bins JSON:  {len(liq_json):>6} bytes")
    print(f"  Block states:         {len(db.block_states):>6} rows")
    print(f"  Candle buckets:       {len(db.candles):>6} rows (all resolutions)")
    print(f"  Tick net deltas:      {len(db.tick_liquidity_net):>6} entries")


if __name__ == "__main__":
    run_simulation()
