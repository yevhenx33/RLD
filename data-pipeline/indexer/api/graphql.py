import os
import strawberry
from typing import List
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from strawberry.fastapi import GraphQLRouter
import clickhouse_connect

@strawberry.type
class HistoricalRate:
    timestamp: int
    symbol: str
    apy: float
    price: float

@strawberry.type
class MarketSnapshot:
    symbol: str
    protocol: str
    supplyUsd: float
    borrowUsd: float
    supplyApy: float
    borrowApy: float
    utilization: float

def get_clickhouse_client():
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "127.0.0.1"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123"))
    )

@strawberry.type
class Query:
    @strawberry.field
    def historicalRates(self, symbols: List[str], resolution: str, limit: int = 17520) -> List[HistoricalRate]:
        """
        Hyper-efficient single query fetching everything directly from ClickHouse views/tables.
        Natively groups by resolution to crush bandwidth needs before leaving localhost.
        """
        ch = get_clickhouse_client()
        
        # Build SQL dynamically resolving into standard format
        # Aave symbols
        aave_symbols = [s for s in symbols if s not in ('SOFR', 'ETH', 'WETH')]
        
        # Safely quote strings for SQL 
        in_aave = "'" + "','".join(aave_symbols) + "'" if aave_symbols else "''"
        
        time_func = "toStartOfDay(timestamp)" if resolution == "1D" else "toStartOfHour(timestamp)"
        
        queries = []
        if aave_symbols:
            queries.append(f"""
                SELECT 
                    toUnixTimestamp({time_func}) as ts,
                    symbol,
                    avg(borrow_apy) as apy,
                    avg(price_usd) as price
                FROM aave_timeseries
                WHERE protocol = 'AAVE_MARKET' AND symbol IN ({in_aave})
                GROUP BY ts, symbol
            """)
            
        if 'SOFR' in symbols:
            queries.append(f"""
                SELECT 
                    toUnixTimestamp({time_func}) as ts,
                    'SOFR' as symbol,
                    avg(apy) as apy,
                    0.0 as price
                FROM raw_sofr_rates
                GROUP BY ts, symbol
            """)
            
        if 'ETH' in symbols or 'WETH' in symbols:
            queries.append(f"""
                SELECT 
                    toUnixTimestamp({time_func}) as ts,
                    'WETH' as symbol,
                    0.0 as apy,
                    avg(price) as price
                FROM chainlink_prices
                WHERE feed = 'ETH / USD'
                GROUP BY ts, symbol
            """)
            
        if not queries:
            return []
            
        sql = " UNION ALL ".join(queries)
        sql = f"SELECT ts, symbol, apy, price FROM ({sql}) ORDER BY ts DESC LIMIT {limit}"
        
        try:
            res = ch.query(sql)
            return [
                HistoricalRate(
                    timestamp=int(row[0]),
                    symbol=row[1],
                    apy=float(row[2]),
                    price=float(row[3])
                )
                for row in res.result_rows
            ]
        finally:
            ch.close()

    @strawberry.field
    def marketSnapshots(self) -> List[MarketSnapshot]:
        """
        Fetches the single latest state row for all tracked assets in both AAVE and MORPHO 
        timeseries blocks. Used to populate the data arrays on the bottom half of the Explore map.
        """
        ch = get_clickhouse_client()
        
        sql = """
        SELECT
            symbol,
            protocol,
            argMax(supply_usd, timestamp) as supplyUsd,
            argMax(borrow_usd, timestamp) as borrowUsd,
            argMax(supply_apy, timestamp) as supplyApy,
            argMax(borrow_apy, timestamp) as borrowApy,
            if(argMax(supply_usd, timestamp) > 0, argMax(borrow_usd, timestamp) / argMax(supply_usd, timestamp), 0.0) as utilization
        FROM
        (
            SELECT symbol, protocol, timestamp, supply_usd, borrow_usd, supply_apy, borrow_apy, utilization FROM aave_timeseries WHERE protocol = 'AAVE_MARKET'
            UNION ALL
            SELECT symbol, protocol, timestamp, supply_usd, borrow_usd, supply_apy, borrow_apy, utilization FROM morpho_timeseries WHERE protocol = 'MORPHO_MARKET'
        )
        GROUP BY symbol, protocol
        """
        try:
            res = ch.query(sql)
            return [
                MarketSnapshot(
                    symbol=row[0],
                    protocol=row[1],
                    supplyUsd=float(row[2]),
                    borrowUsd=float(row[3]),
                    supplyApy=float(row[4]),
                    borrowApy=float(row[5]),
                    utilization=float(row[6])
                )
                for row in res.result_rows
            ]
        finally:
            ch.close()

schema = strawberry.Schema(query=Query)
graphql_app = GraphQLRouter(schema)
app = FastAPI(title="RLD ClickHouse GraphQL")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(graphql_app, prefix="/graphql")
app.include_router(graphql_app, prefix="/envio-graphql") # Fallback to absorb existing proxy mappings cleanly

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

def create_app():
    return app
