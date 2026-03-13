"""
api/graphql.py — Strawberry GraphQL API serving indexer data.

Single process — reads from asyncpg pool. All resolvers: pure SELECT.
Served at: /graphql  (Strawberry + FastAPI)
Health at: /healthz
"""
import os
from typing import Optional, List
import strawberry
from strawberry.fastapi import GraphQLRouter
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


@strawberry.type
class Broker:
    address: str
    market_id: str
    owner: str
    created_block: int
    active_token_id: Optional[int]


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


@strawberry.type
class IndexerStatus:
    market_id: str
    last_indexed_block: int
    last_indexed_at: Optional[str]
    total_events: int


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
            rows = await conn.fetch("SELECT * FROM markets ORDER BY deploy_block")
        return [_market(r) for r in rows]

    @strawberry.field
    async def market(self, market_id: str) -> Optional[Market]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            r = await conn.fetchrow("SELECT * FROM markets WHERE market_id=$1", market_id)
        return _market(r) if r else None

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
            active_token_id=r["active_token_id"],
        ) for r in rows]

    @strawberry.field
    async def pool_snapshot(self, market_id: str) -> Optional[PoolSnapshot]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                  b.market_id, b.block_number, b.block_timestamp,
                  b.mark_price, b.tick, b.sqrt_price_x96, b.liquidity,
                  b.normalization_factor,
                  (SELECT index_price FROM block_states
                   WHERE market_id=$1 AND index_price IS NOT NULL
                   ORDER BY block_number DESC LIMIT 1) AS index_price
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
            mark_price=float(row["mark_price"]) if row["mark_price"] else None,
            index_price=float(row["index_price"]) if row["index_price"] else None,
            tick=row["tick"],
            sqrt_price_x96=str(row["sqrt_price_x96"]) if row["sqrt_price_x96"] else None,
            liquidity=str(row["liquidity"]) if row["liquidity"] else None,
            normalization_factor=str(row["normalization_factor"]) if row["normalization_factor"] else None,
        )

    @strawberry.field
    async def candles(
        self,
        market_id: str,
        resolution: str,
        from_bucket: int,
        to_bucket: int,
        limit: Optional[int] = 500,
    ) -> List[Candle]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM candles
                WHERE market_id=$1 AND resolution=$2
                  AND bucket BETWEEN $3 AND $4
                ORDER BY bucket ASC
                LIMIT $5
            """, market_id, resolution, from_bucket, to_bucket, limit or 500)
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


# ── App factory ────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    schema = strawberry.Schema(query=Query)
    graphql_app = GraphQLRouter(schema)

    app = FastAPI(title="RLD Indexer GraphQL API", version="1.0.0")
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

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.graphql:app", host="0.0.0.0", port=8080, reload=False)
