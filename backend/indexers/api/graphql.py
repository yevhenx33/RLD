"""
api/graphql.py — Strawberry GraphQL API serving indexer data.

Single process — reads from asyncpg pool. All resolvers: pure SELECT.
Served at: /graphql  (Strawberry + FastAPI)
Health at: /healthz
"""
import os
import json
import math
from typing import Optional, List
import strawberry
from strawberry.fastapi import GraphQLRouter
from strawberry.scalars import JSON
from fastapi import FastAPI
import asyncpg

# ── Pool singleton ─────────────────────────────────────────────────────────

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=os.environ["DATABASE_URL"],
            min_size=2, max_size=10,
            command_timeout=30,
        )
    return _pool


# ── Types ──────────────────────────────────────────────────────────────────

@strawberry.type
class Market:
    market_id: str
    broker_factory: str
    mock_oracle: str
    twamm_hook: str
    wausdc: str
    wrlp: str
    pool_id: str
    pool_fee: int
    tick_spacing: int
    min_col_ratio: str
    maintenance_margin: str
    debt_cap: str
    normalization_factor: Optional[str]
    total_debt_raw: Optional[str]
    bad_debt: Optional[str]


@strawberry.type
class Broker:
    address: str
    market_id: str
    owner: str
    created_block: int
    active_token_id: Optional[int]
    wausdc_balance: Optional[str]
    wrlp_balance: Optional[str]
    debt_principal: Optional[str]
    is_liquidated: Optional[bool]
    health_factor: Optional[str]


@strawberry.type
class PoolSnapshot:
    market_id: str
    block_number: int
    block_timestamp: int
    mark_price: Optional[float]
    index_price: Optional[float]
    tick: Optional[int]
    sqrt_price_x96: Optional[str]
    liquidity: Optional[str]
    normalization_factor: Optional[str]
    total_debt: Optional[float]
    token0_balance: Optional[str]
    token1_balance: Optional[str]
    fee_growth_global0: Optional[str]
    fee_growth_global1: Optional[str]


@strawberry.type
class Candle:
    market_id: str
    resolution: str
    bucket: int
    mark_open: float
    mark_high: float
    mark_low: float
    mark_close: float
    index_open: float
    index_high: float
    index_low: float
    index_close: float
    volume_usd: float
    swap_count: int


@strawberry.type
class LpPosition:
    token_id: int
    market_id: str
    broker_address: str
    liquidity: str
    tick_lower: int
    tick_upper: int
    entry_price: Optional[float]
    entry_tick: Optional[int]
    mint_block: int
    is_active: bool
    is_burned: bool


@strawberry.type
class TwammOrder:
    order_id: str
    market_id: str
    owner: str
    expiration: int
    zero_for_one: bool
    amount_in: str
    is_cancelled: bool
    block_number: int
    start_epoch: int
    tx_hash: str


@strawberry.type
class IndexerStatus:
    market_id: str
    last_indexed_block: int
    last_indexed_at: Optional[str]
    total_events: int


@strawberry.type
class Event:
    event_name: str
    block_timestamp: int
    block_number: int
    data: str


# ── Helpers ────────────────────────────────────────────────────────────────

def _market(r) -> Market:
    return Market(
        market_id=r["market_id"], broker_factory=r["broker_factory"],
        mock_oracle=r["mock_oracle"], twamm_hook=r["twamm_hook"],
        wausdc=r["wausdc"], wrlp=r["wrlp"], pool_id=r["pool_id"],
        pool_fee=r["pool_fee"], tick_spacing=r["tick_spacing"],
        min_col_ratio=str(r["min_col_ratio"]),
        maintenance_margin=str(r["maintenance_margin"]),
        debt_cap=str(r["debt_cap"]),
        normalization_factor=str(r["normalization_factor"]) if r["normalization_factor"] is not None else None,
        total_debt_raw=str(r["total_debt_raw"]) if r["total_debt_raw"] is not None else None,
        bad_debt=str(r["bad_debt"]) if r["bad_debt"] is not None else None,
    )


def _candle(r) -> Candle:
    return Candle(
        market_id=r["market_id"], resolution=r["resolution"],
        bucket=r["bucket"],
        mark_open=float(r["mark_open"]), mark_high=float(r["mark_high"]),
        mark_low=float(r["mark_low"]), mark_close=float(r["mark_close"]),
        index_open=float(r["index_open"]), index_high=float(r["index_high"]),
        index_low=float(r["index_low"]), index_close=float(r["index_close"]),
        volume_usd=float(r["volume_usd"]), swap_count=r["swap_count"],
    )


def _lp(r) -> LpPosition:
    return LpPosition(
        token_id=r["token_id"], market_id=r["market_id"],
        broker_address=r["broker_address"],
        liquidity=str(r["liquidity"]),
        tick_lower=r["tick_lower"], tick_upper=r["tick_upper"],
        entry_price=float(r["entry_price"]) if r["entry_price"] else None,
        entry_tick=r["entry_tick"], mint_block=r["mint_block"],
        is_active=r["is_active"], is_burned=r["is_burned"],
    )


# ── Query ──────────────────────────────────────────────────────────────────

@strawberry.type
class Query:

    @strawberry.field
    async def markets(self) -> List[Market]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM markets ORDER BY deploy_timestamp DESC")
        return [_market(r) for r in rows]

    @strawberry.field
    async def brokers(self, market_id: str) -> List[Broker]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM brokers WHERE market_id=$1 ORDER BY created_block", market_id
            )
        return [Broker(
            address=r["address"], market_id=r["market_id"],
            owner=r["owner"], created_block=r["created_block"],
            active_token_id=r["active_lp_token_id"],
            wausdc_balance=str(r["wausdc_balance"]) if r["wausdc_balance"] is not None else None,
            wrlp_balance=str(r["wrlp_balance"]) if r["wrlp_balance"] is not None else None,
            debt_principal=str(r["debt_principal"]) if r["debt_principal"] is not None else None,
            is_liquidated=r["is_liquidated"],
            health_factor=str(r["health_factor"]) if r["health_factor"] is not None else None,
        ) for r in rows]

    @strawberry.field
    async def pool_snapshot(self, market_id: str) -> Optional[PoolSnapshot]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                  b.market_id, b.block_number, b.block_timestamp,
                  b.sqrt_price_x96, b.liquidity,
                  COALESCE(b.mark_price, (SELECT mark_price FROM block_states
                   WHERE market_id=$1 AND mark_price IS NOT NULL
                   ORDER BY block_number DESC LIMIT 1)) AS mark_price,
                  COALESCE(b.tick, (SELECT tick FROM block_states
                   WHERE market_id=$1 AND tick IS NOT NULL
                   ORDER BY block_number DESC LIMIT 1)) AS tick,
                  COALESCE(b.normalization_factor, (SELECT normalization_factor FROM block_states
                   WHERE market_id=$1 AND normalization_factor IS NOT NULL
                   ORDER BY block_number DESC LIMIT 1)) AS normalization_factor,
                  COALESCE(b.total_debt, (SELECT total_debt FROM block_states
                   WHERE market_id=$1 AND total_debt IS NOT NULL
                   ORDER BY block_number DESC LIMIT 1)) AS total_debt,
                  COALESCE(b.index_price, (SELECT index_price FROM block_states
                   WHERE market_id=$1 AND index_price IS NOT NULL
                   ORDER BY block_number DESC LIMIT 1)) AS index_price,
                  COALESCE(b.token0_balance, (SELECT token0_balance FROM block_states
                   WHERE market_id=$1 AND token0_balance IS NOT NULL
                   ORDER BY block_number DESC LIMIT 1)) AS token0_balance,
                  COALESCE(b.token1_balance, (SELECT token1_balance FROM block_states
                   WHERE market_id=$1 AND token1_balance IS NOT NULL
                   ORDER BY block_number DESC LIMIT 1)) AS token1_balance,
                  COALESCE(b.fee_growth_global0, (SELECT fee_growth_global0 FROM block_states
                   WHERE market_id=$1 AND fee_growth_global0 IS NOT NULL
                   ORDER BY block_number DESC LIMIT 1)) AS fee_growth_global0,
                  COALESCE(b.fee_growth_global1, (SELECT fee_growth_global1 FROM block_states
                   WHERE market_id=$1 AND fee_growth_global1 IS NOT NULL
                   ORDER BY block_number DESC LIMIT 1)) AS fee_growth_global1
                FROM block_states b
                WHERE b.market_id=$1
                ORDER BY b.block_number DESC
                LIMIT 1
            """, market_id)
        if not row:
            return None
        return PoolSnapshot(
            market_id=row["market_id"],
            block_number=row["block_number"],
            block_timestamp=row["block_timestamp"],
            mark_price=float(row["mark_price"]) if row["mark_price"] is not None else None,
            index_price=float(row["index_price"]) if row["index_price"] is not None else None,
            tick=row["tick"],
            sqrt_price_x96=str(row["sqrt_price_x96"]) if row["sqrt_price_x96"] is not None else None,
            liquidity=str(row["liquidity"]) if row["liquidity"] is not None else None,
            normalization_factor=str(row["normalization_factor"]) if row["normalization_factor"] is not None else None,
            total_debt=float(row["total_debt"]) if row["total_debt"] is not None else None,
            token0_balance=str(row["token0_balance"]) if row["token0_balance"] is not None else None,
            token1_balance=str(row["token1_balance"]) if row["token1_balance"] is not None else None,
            fee_growth_global0=str(row["fee_growth_global0"]) if row["fee_growth_global0"] is not None else None,
            fee_growth_global1=str(row["fee_growth_global1"]) if row["fee_growth_global1"] is not None else None,
        )

    @strawberry.field
    async def candles(
        self,
        market_id: str,
        resolution: str,
        from_bucket: Optional[int] = None,
        to_bucket: Optional[int] = None,
        limit: Optional[int] = 500,
    ) -> List[Candle]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            if from_bucket is not None and to_bucket is not None:
                rows = await conn.fetch("""
                    SELECT * FROM candles
                    WHERE market_id=$1 AND resolution=$2
                      AND bucket BETWEEN $3 AND $4
                    ORDER BY bucket ASC
                    LIMIT $5
                """, market_id, resolution, from_bucket, to_bucket, limit or 500)
            else:
                rows = await conn.fetch("""
                    SELECT * FROM candles
                    WHERE market_id=$1 AND resolution=$2
                    ORDER BY bucket DESC
                    LIMIT $3
                """, market_id, resolution, limit or 500)
                # Sort back to ascending for the chart
                rows = sorted(rows, key=lambda x: x["bucket"])
        return [_candle(r) for r in rows]

    @strawberry.field
    async def lp_positions(
        self,
        market_id: str,
        broker_address: Optional[str] = None,
        active_only: bool = False,
    ) -> List[LpPosition]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            q = "SELECT * FROM lp_positions WHERE market_id=$1"
            args: list = [market_id]
            if broker_address:
                args.append(broker_address.lower())
                q += f" AND broker_address=${len(args)}"
            if active_only:
                q += " AND is_active=TRUE AND is_burned=FALSE"
            q += " ORDER BY mint_block DESC"
            rows = await conn.fetch(q, *args)
        return [_lp(r) for r in rows]

    @strawberry.field
    async def twamm_orders(
        self, market_id: str, active_only: bool = True
    ) -> List[TwammOrder]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            q = "SELECT * FROM twamm_orders WHERE market_id=$1"
            if active_only:
                q += " AND is_cancelled=FALSE"
            q += " ORDER BY block_number DESC"
            rows = await conn.fetch(q, market_id)
        return [TwammOrder(
            order_id=r["order_id"], market_id=r["market_id"],
            owner=r["owner"], expiration=r["expiration"],
            zero_for_one=r["zero_for_one"],
            amount_in=str(r["amount_in"]),
            is_cancelled=r["is_cancelled"], block_number=r["block_number"],
            start_epoch=r["start_epoch"], tx_hash=r["tx_hash"],
            sell_rate=str(r["sell_rate"]),
        ) for r in rows]

    @strawberry.field
    async def indexer_status(self) -> List[IndexerStatus]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM indexer_state ORDER BY market_id")
        return [IndexerStatus(
            market_id=r["market_id"],
            last_indexed_block=r["last_indexed_block"],
            last_indexed_at=r["last_indexed_at"].isoformat() if r["last_indexed_at"] else None,
            total_events=r["total_events"],
        ) for r in rows]

    @strawberry.field
    async def events(self, limit: int = 100) -> List[Event]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT event_name, block_timestamp, block_number, data 
                FROM events 
                ORDER BY block_number DESC, log_index DESC
                LIMIT $1
            """, limit)
        return [Event(
            event_name=r["event_name"],
            block_timestamp=r["block_timestamp"],
            block_number=r["block_number"],
            data=r["data"]
        ) for r in rows]

    # ── NEW: Precomputed data resolvers ─────────────────────────────

    @strawberry.field
    async def snapshot(self) -> Optional[JSON]:
        """Returns the precomputed global snapshot JSON. Zero computation."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            val = await conn.fetchval("SELECT snapshot FROM markets LIMIT 1")
        if not val:
            return None
        return json.loads(val) if isinstance(val, str) else val

    @strawberry.field
    async def liquidity_distribution(self) -> Optional[JSON]:
        """Returns pre-built liquidity bin distribution. Zero computation."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            val = await conn.fetchval("SELECT liquidity_bins FROM markets LIMIT 1")
        if not val:
            return None
        return json.loads(val) if isinstance(val, str) else val

    @strawberry.field
    async def bonds(self, owner: str) -> Optional[JSON]:
        """Returns all bonds owned by the given address."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT broker_address, market_id, owner, notional, hedge,
                       duration, mint_block, mint_tx, status, close_block, close_tx,
                       factory_address
                FROM bonds WHERE owner = $1
                ORDER BY mint_block DESC
            """, owner.lower())
        if not rows:
            return []
        result = []
        for r in rows:
            d = dict(r)
            # Convert Decimal → float for JSON serialization
            for k in ("notional", "hedge"):
                if d.get(k) is not None:
                    d[k] = float(d[k])
            result.append(d)
        return result

    @strawberry.field
    async def broker_profile(self, owner: str) -> Optional[JSON]:
        """On-demand broker profile with LP position values and fees.
        
        Raw data stored in DB. Values/fees computed at query time
        using 5N multiplications per LP position. <1ms even with 50 positions.
        """
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Find broker by owner
            broker = await conn.fetchrow(
                "SELECT * FROM brokers WHERE owner = $1",
                owner.lower()
            )
            if not broker:
                return None

            market_id = broker["market_id"]

            # Get latest block state for current prices
            latest = await conn.fetchrow("""
                SELECT mark_price, tick, fee_growth_global0, fee_growth_global1,
                       index_price
                FROM block_states
                WHERE market_id = $1
                ORDER BY block_number DESC LIMIT 1
            """, market_id)
            if not latest:
                return None

            mark_price = float(latest["mark_price"] or 0)
            current_tick = int(latest["tick"] or 0)
            fg0 = int(latest["fee_growth_global0"] or "0")
            fg1 = int(latest["fee_growth_global1"] or "0")
            index_price = float(latest["index_price"] or 0)

            # Get LP positions
            positions = await conn.fetch("""
                SELECT * FROM lp_positions
                WHERE broker_address = $1 AND is_burned = FALSE
                ORDER BY mint_block DESC
            """, broker["address"])

            lp_data = []
            Q128 = 2**128
            for pos in positions:
                tick_lower = pos["tick_lower"]
                tick_upper = pos["tick_upper"]
                liquidity = int(pos["liquidity"])

                # Compute token amounts from liquidity + current tick
                amt0, amt1 = _liquidity_to_amounts(
                    liquidity, tick_lower, tick_upper, current_tick
                )
                amt0_human = amt0 / 1e6
                amt1_human = amt1 / 1e6
                value_usd = amt0_human * mark_price + amt1_human

                # Fee earnings (simplified: using global fee growth as upper bound)
                # In production V4, feeGrowthInside requires per-tick tracking
                fg_inside0 = fg0  # upper bound — TODO: track per-tick
                fg_inside1 = fg1
                fees0 = liquidity * fg_inside0 / Q128 / 1e6
                fees1 = liquidity * fg_inside1 / Q128 / 1e6

                in_range = tick_lower <= current_tick < tick_upper

                lp_data.append({
                    "tokenId": int(pos["token_id"]),
                    "tickLower": tick_lower,
                    "tickUpper": tick_upper,
                    "liquidity": str(liquidity),
                    "isActive": pos["is_active"],
                    "amount0": round(amt0_human, 4),
                    "amount1": round(amt1_human, 4),
                    "valueUsd": round(value_usd, 2),
                    "feesEarned0": round(fees0, 4),
                    "feesEarned1": round(fees1, 4),
                    "feesUsd": round(fees0 * mark_price + fees1, 2),
                    "inRange": in_range,
                    "entryPrice": float(pos["entry_price"]) if pos["entry_price"] else None,
                })

            debt_principal = float(broker["debt_principal"] or 0)
            collateral_value = float(broker["wausdc_value"] or 0)
            debt_value = debt_principal * index_price

            return {
                "address": broker["address"],
                "owner": broker["owner"],
                "collateral": float(broker["wausdc_balance"] or 0),
                "debt": debt_principal,
                "collateralValue": collateral_value,
                "debtValue": round(debt_value, 2),
                "healthFactor": str(broker["health_factor"] or "0"),
                "netEquityUsd": round(collateral_value - debt_value, 2),
                "lpPositions": lp_data,
            }

    @strawberry.field
    async def market_info(self) -> Optional[JSON]:
        """Static market configuration. Fetched once, cached forever."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT market_id, broker_factory, mock_oracle, twamm_hook,
                       wausdc, wrlp, pool_id, pool_fee, tick_spacing,
                       min_col_ratio, maintenance_margin, debt_cap,
                       swap_router, bond_factory, basis_trade_factory, broker_executor,
                       funding_period_sec, v4_quoter, broker_router
                FROM markets LIMIT 1
            """)
        if not row:
            return None
        return {
            "marketId": row["market_id"],
            "brokerFactory": row["broker_factory"],
            "mockOracle": row["mock_oracle"],
            "twammHook": row["twamm_hook"],
            "wausdc": row["wausdc"],
            "wrlp": row["wrlp"],
            "poolId": row["pool_id"],
            "poolFee": row["pool_fee"],
            "tickSpacing": row["tick_spacing"],
            "minColRatio": str(row["min_col_ratio"]),
            "maintenanceMargin": str(row["maintenance_margin"]),
            "debtCap": str(row["debt_cap"]),
            "swapRouter": row["swap_router"],
            "bondFactory": row["bond_factory"],
            "basisTradeFactory": row["basis_trade_factory"],
            "brokerExecutor": row["broker_executor"],
            "fundingPeriodSec": row["funding_period_sec"],
            "v4Quoter": row["v4_quoter"] or "",
            "brokerRouter": row["broker_router"] or "",
        }


# ── Uniswap V4 math for on-demand position value resolution ────────────────

def _liquidity_to_amounts(
    liquidity: int, tick_lower: int, tick_upper: int, current_tick: int
) -> tuple:
    """Convert LP position's liquidity to token amounts at current tick."""
    sa = math.sqrt(1.0001 ** tick_lower)
    sb = math.sqrt(1.0001 ** tick_upper)
    sp = math.sqrt(1.0001 ** current_tick)

    if current_tick < tick_lower:
        amount0 = liquidity * (1/sa - 1/sb)
        amount1 = 0
    elif current_tick >= tick_upper:
        amount0 = 0
        amount1 = liquidity * (sb - sa)
    else:
        amount0 = liquidity * (1/sp - 1/sb)
        amount1 = liquidity * (sp - sa)

    return amount0, amount1


# ── App factory ────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    schema = strawberry.Schema(query=Query)
    graphql_app = GraphQLRouter(schema)

    app = FastAPI(title="RLD Indexer GraphQL API", version="1.0.0")

    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(graphql_app, prefix="/graphql")

    @app.get("/healthz")
    async def healthz():
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return {"status": "ok"}
        except Exception as e:
            from fastapi.responses import JSONResponse
            return JSONResponse({"status": "error", "detail": str(e)}, status_code=503)

    @app.post("/admin/reset")
    async def admin_reset():
        """Deployer calls this after writing deployment.json to wipe stale data."""
        import bootstrap
        import logging
        log = logging.getLogger("admin.reset")
        try:
            pool = await get_pool()
            log.info("POST /admin/reset — truncating all indexed data")
            await bootstrap.reset(pool)  # truncates + re-seeds from deployment.json
            cfg = bootstrap.load_deployment_json()
            market_id = cfg.get("market_id", "unknown")
            log.info("Reset complete — market_id=%s", market_id)
            return {"status": "ok", "market_id": market_id}
        except Exception as e:
            log.error("Reset failed: %s", e, exc_info=True)
            from fastapi.responses import JSONResponse
            return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)

    @app.get("/config")
    async def get_config():
        """Daemons poll this to get deployment config. 503 until deployer has run."""
        from fastapi.responses import JSONResponse
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT market_id, broker_factory, mock_oracle, twamm_hook,
                           wausdc, wrlp, pool_id, pool_fee, tick_spacing,
                           swap_router, bond_factory, basis_trade_factory, broker_executor
                    FROM markets LIMIT 1
                """)
            if not row:
                return JSONResponse(
                    {"status": "waiting", "detail": "No market deployed yet"},
                    status_code=503
                )
            cfg = dict(row)
            # Overlay fields from deployment.json that are missing or null in DB
            import bootstrap
            try:
                deploy_cfg = bootstrap.load_deployment_json()
                for key in ("rpc_url", "rld_core", "pool_manager", "position_token",
                            "swap_router", "bond_factory", "basis_trade_factory",
                            "broker_executor", "broker_router", "token0", "token1",
                            "zero_for_one_long", "v4_quoter", "v4_position_manager"):
                    if key in deploy_cfg and not cfg.get(key):
                        cfg[key] = deploy_cfg[key]
            except (FileNotFoundError, ValueError):
                pass
            return cfg
        except Exception as e:
            return JSONResponse({"status": "error", "detail": str(e)}, status_code=503)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.graphql:app", host="0.0.0.0", port=8080, reload=False)
